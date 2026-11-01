"""DART single-user download service. Run: python server.py"""
import collections
import concurrent.futures
import email.message
import html.parser
import html
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from proxy import start_proxy

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('DART_DATA', str(ROOT / '.dart-data')))
TTL = 3600
JOBS = {}
LOCK = threading.RLock()
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2)
SLOTS = threading.BoundedSemaphore(4)
PROXY_URL = None
REQUESTS = collections.defaultdict(collections.deque)


def validate_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 for c in value):
        raise ValueError('Paste a valid public HTTP or HTTPS link')
    u = urllib.parse.urlsplit(value)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
        raise ValueError('Use a public HTTP or HTTPS link without credentials')
    if (u.port or (443 if u.scheme == 'https' else 80)) not in (80, 443):
        raise ValueError('Only standard HTTP and HTTPS links are supported')
    return value


def platform(url):
    host = (urllib.parse.urlsplit(url).hostname or '').lower().removeprefix('www.')
    for name, domains in {'YouTube': ('youtube.com', 'youtu.be'), 'Instagram': ('instagram.com',),
                          'Facebook': ('facebook.com', 'fb.watch'), 'X': ('x.com', 'twitter.com')}.items():
        if any(host == d or host.endswith('.' + d) for d in domains):
            return name
    return 'Web'


class PublicOpener(urllib.request.ProxyHandler):
    # Always use the validating proxy, including on redirects; ignore NO_PROXY.
    def proxy_open(self, req, proxy, type):
        req.set_proxy(urllib.parse.urlsplit(proxy).netloc, type)
        if req.type == 'https':
            return None
        return self.parent.open(req, timeout=req.timeout) if type != req.type else None


def open_url(url, headers=None):
    validate_url(url)
    opener = urllib.request.build_opener(PublicOpener({'http': PROXY_URL, 'https': PROXY_URL}))
    return opener.open(urllib.request.Request(url, headers={'User-Agent': 'DART/1.0', **(headers or {})}), timeout=25)


def fetch_file(url, dest, job, image=False):
    with open_url(url) as response:
        kind = response.headers.get_content_type()
        if kind in ('text/html', 'application/xhtml+xml'):
            raise ValueError('This is a webpage. Choose Video or Photo, or provide a direct file link.')
        if image and (not kind.startswith('image/') or kind == 'image/svg+xml'):
            raise ValueError('The source did not return a supported raster image')
        total = int(response.headers.get('Content-Length') or 0)
        message = email.message.Message()
        message['Content-Disposition'] = response.headers.get('Content-Disposition', '')
        name = message.get_filename() or Path(urllib.parse.urlsplit(response.url).path).name or 'download'
        name = re.sub(r'[^\w.() -]', '_', name)[:160].strip('. ') or 'download'
        suffix = Path(name).suffix or mimetypes.guess_extension(kind) or '.bin'
        output = dest / ('asset' + suffix)
        size = 0
        with output.open('wb') as f:
            while True:
                if job.get('cancelled'):
                    raise ValueError('Download cancelled')
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                f.write(chunk)
                job['progress'] = min(99, round(size / total * 100)) if total else None
        if not size:
            raise ValueError('The source returned an empty file')
        if total and size != total:
            raise ValueError('The source connection ended before the complete file arrived. Try again.')
        if not Path(name).suffix:
            name += suffix
        return output, name, kind


class Metadata(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = {}
        self.scripts = []
        self._script = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta':
            self.tags[a.get('property') or a.get('name')] = a.get('content', '')
        elif tag == 'script':
            self._script = []

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self._script is not None:
            self.scripts.append(''.join(self._script))
            self._script = None


def instagram_username(url):
    if platform(url) != 'Instagram':
        return None
    parts = urllib.parse.urlsplit(url).path.strip('/').split('/')
    reserved = {'p', 'reel', 'reels', 'stories', 'explore', 'accounts', 'direct', 'about', 'developer', 'api', 'challenge'}
    if (not parts or len(parts) > 2 or (len(parts) == 2 and parts[1] != 'avatar')
            or parts[0].lower() in reserved or not re.fullmatch(r'[A-Za-z0-9_.]{1,30}', parts[0])):
        raise ValueError('Use an Instagram profile link such as https://www.instagram.com/username/, rather than a post or Reel.')
    return parts[0].lower()


def json_nodes(scripts):
    """Decode structured and JSON-encoded hydration data, with bounded traversal."""
    stack = []
    decoder = json.JSONDecoder()
    for script in scripts:
        candidate = html.unescape(script).strip().removeprefix('window._sharedData =').strip()
        try:
            stack.append(decoder.raw_decode(candidate)[0])
        except (ValueError, RecursionError):
            continue
    count = 0
    while stack and count < 20000:
        node = stack.pop()
        count += 1
        if isinstance(node, dict):
            yield node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(reversed(node))
        elif isinstance(node, str) and node.startswith(('{', '[')):
            try:
                stack.append(json.loads(node))
            except (ValueError, RecursionError):
                pass


def avatar_field(node):
    for key in ('profile_pic_url_hd', 'profile_pic_url', 'avatar_url', 'profile_image_url_https'):
        if isinstance(node.get(key), str) and node[key]:
            return node[key]
    for key in ('hd_profile_pic_url_info', 'profile_picture', 'profilePicture'):
        value = node.get(key)
        if isinstance(value, dict) and isinstance(value.get('url') or value.get('uri'), str):
            return value.get('url') or value['uri']
    return None


def instagram_api_avatar(username):
    # Public profile metadata only. No cookies, credentials, or access-control bypass.
    endpoint = 'https://www.instagram.com/api/v1/users/web_profile_info/?' + urllib.parse.urlencode({'username': username})
    try:
        with open_url(endpoint, {'Accept': 'application/json', 'X-IG-App-ID': '936619743392459'}) as response:
            data = json.loads(response.read(2 * 1024 * 1024))
        user = (data.get('data') or {}).get('user') or {}
        if str(user.get('username', '')).lower() == username:
            return avatar_field(user)
    except (OSError, ValueError, AttributeError, RecursionError):
        pass
    return None


def profile_image(content, parser, url, username=None):
    nodes = list(json_nodes(parser.scripts))
    for node in nodes:
        # A profile's private flag does not hide an avatar that is publicly returned.
        # Match identity so suggested accounts cannot supply the wrong person's avatar.
        if username is not None and str(node.get('username', '')).lower() != username:
            continue
        value = avatar_field(node)
        if value:
            return value
    if username is None:
        for node in nodes:
            if node.get('@type') == 'ProfilePage' and isinstance(node.get('mainEntity'), dict):
                person = node['mainEntity']
                image = person.get('image')
                if person.get('@type') in ('Person', 'Organization'):
                    if isinstance(image, str):
                        return image
                    if isinstance(image, dict):
                        return image.get('contentUrl') or image.get('url')
        # Legacy non-JSON script assignments still expose explicit avatar fields.
        match = re.search(r'"(?:profile_pic_url_hd|profile_pic_url|avatar_url)"\s*:\s*("(?:[^"\\]|\\.)*")', html.unescape(content))
        if match:
            return json.loads(match.group(1))
    # Only use OG images when the page explicitly describes the requested profile.
    if parser.tags.get('og:type') == 'profile':
        canonical = parser.tags.get('og:url')
        if canonical and urllib.parse.urlsplit(canonical).hostname == urllib.parse.urlsplit(url).hostname:
            canonical_path = urllib.parse.urlsplit(canonical).path.strip('/').lower()
            requested_path = username or urllib.parse.urlsplit(url).path.strip('/').lower()
            if canonical_path == requested_path:
                return parser.tags.get('og:image:secure_url') or parser.tags.get('og:image')
    return None


def image_link(url, profile=False):
    username = instagram_username(url) if profile else None
    try:
        response = open_url(url)
    except (OSError, ValueError):
        if username:
            avatar = instagram_api_avatar(username)
            if avatar:
                return validate_url(avatar), '@' + username + ' · profile picture'
        raise ValueError('The platform did not allow DART to read this profile. Its public avatar may require a logged-in session or be temporarily blocked.'
                         if profile else 'The image source could not be reached. Check the link and try again.')
    with response:
        if response.headers.get_content_type().startswith('image/'):
            return url, 'Source image'
        if response.headers.get_content_type() not in ('text/html', 'application/xhtml+xml'):
            raise ValueError('This link does not contain a readable image page')
        content = response.read(2 * 1024 * 1024).decode('utf-8', errors='replace')
        parser = Metadata()
        parser.feed(content)
        if profile:
            avatar = profile_image(content, parser, response.url, username)
            if not avatar and username:
                avatar = instagram_api_avatar(username)
            if not avatar:
                raise ValueError('The platform did not return a publicly accessible avatar for this profile. A private profile can work when its avatar is public; a login requirement or platform block cannot be bypassed. You can also use the direct avatar image link.')
            return validate_url(urllib.parse.urljoin(response.url, avatar)), ('@' + username + ' · profile picture') if username else 'Public profile image'
        image = parser.tags.get('og:image:secure_url') or parser.tags.get('og:image') or parser.tags.get('twitter:image')
        if not image:
            raise ValueError('No public image was found. Try a direct image URL.')
        return validate_url(urllib.parse.urljoin(response.url, image)), parser.tags.get('og:title') or 'Page image'


def run_process(job, command, timeout=None):
    # Kill the entire process group on cancellation, including local FFmpeg children.
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    job['process'] = proc
    deadline = time.monotonic() + timeout if timeout is not None else None
    try:
        while True:
            if job.get('cancelled'):
                raise ValueError('Download cancelled')
            if deadline is not None and time.monotonic() > deadline:
                raise ValueError('The source took too long. Try again or use a smaller file.')
            try:
                stdout, stderr = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                continue
        return proc.returncode, stdout, stderr
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
        job.pop('process', None)


def ytdlp(job, args, timeout):
    command = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--proxy', PROXY_URL,
               '--socket-timeout', '20', '--retries', '1', '--fragment-retries', '1', '--no-cache-dir',
               '--js-runtimes', 'node', '--no-check-formats'] + args + ['--', job['url']]
    code, stdout, stderr = run_process(job, command, timeout)
    if code:
        if 'Unsupported URL' in stderr:
            raise ValueError('This link is not supported by the video extractor. Try a direct file link.')
        raise ValueError('The source could not provide this video. It may require login, restrict automated access, or be unavailable.')
    return stdout


def output_formats(kind):
    formats = ['original']
    if shutil.which('ffmpeg'):
        if kind == 'video':
            formats += ['mp4', 'mkv', 'webm', 'mp3', 'm4a', 'wav']
        elif kind in ('photo', 'profile'):
            formats += ['png', 'jpg', 'webp']
    return formats


def convert_file(job, path, name):
    fmt = job.get('output_format', 'original')
    if fmt == 'original':
        return path, name
    job.update(stage='converting', progress=None)
    conversion_dir = job['dir'] / 'output'
    conversion_dir.mkdir(exist_ok=True)
    output = conversion_dir / ('converted.' + fmt)
    # Decode only local media; disable network protocols and playlist demuxers.
    inputs = ['-protocol_whitelist', 'file,pipe']
    if job['kind'] in ('photo', 'profile'):
        with path.open('rb') as f:
            magic = f.read(12)
        decoder = ('png' if magic.startswith(b'\x89PNG\r\n\x1a\n') else 'mjpeg' if magic.startswith(b'\xff\xd8\xff')
                   else 'webp' if magic.startswith(b'RIFF') and magic[8:12] == b'WEBP' else None)
        if not decoder:
            raise ValueError('Image conversion supports PNG, JPEG and WebP sources. Use Original for other image types.')
        inputs += ['-f', 'image2pipe', '-c:v', decoder]
        options = ['-frames:v', '1']
        if fmt == 'jpg':
            options += ['-q:v', '2', '-pix_fmt', 'yuvj444p']
        elif fmt == 'webp':
            options += ['-c:v', 'libwebp', '-lossless', '1']
    else:
        inputs += ['-format_whitelist', 'mov,matroska,webm,avi,mpegts,flv,ogg,mp3,aac,wav']
        if fmt in ('mp3', 'm4a', 'wav'):
            codec = {'mp3':['libmp3lame','-q:a','0'], 'm4a':['aac','-b:a','256k'], 'wav':['pcm_s16le']}[fmt]
            options = ['-map', '0:a:0', '-vn', '-c:a'] + codec
        elif fmt == 'mkv':
            options = ['-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy']
        elif fmt == 'mp4':
            options = ['-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
                       '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '256k', '-movflags', '+faststart']
        else:
            options = ['-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libvpx-vp9', '-crf', '24', '-b:v', '0',
                       '-deadline', 'good', '-cpu-used', '4', '-c:a', 'libopus', '-b:a', '192k']
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y'] + inputs + ['-i', str(path)] + options + ['-threads', '2', str(output)]
    code, _, _ = run_process(job, command)
    if code or not output.is_file() or not output.stat().st_size:
        raise ValueError('This source could not be converted to the selected format. Try Original; audio formats require a source with audio.')
    path.unlink()
    return output, Path(name).stem + '.' + fmt


def work(job, action):
    try:
        job.update(state='working', stage='inspecting' if action == 'inspect' else 'downloading')
        if job['kind'] == 'video':
            if action == 'inspect':
                info = json.loads(ytdlp(job, ['--dump-single-json', '--skip-download', '--playlist-items', '1'], 80))
                if info.get('is_live') or info.get('_type') in ('playlist', 'multi_video'):
                    raise ValueError('Use a single, non-live video link')
                heights = sorted({f['height'] for f in info.get('formats', []) if isinstance(f.get('height'), int) and f.get('vcodec') != 'none'}, reverse=True)
                has_audio = any(f.get('acodec') != 'none' for f in info.get('formats', []))
                formats = [f for f in output_formats('video') if has_audio or f not in ('mp3', 'm4a', 'wav')]
                job.update(title=str(info.get('title') or 'Video')[:200], qualities=[str(h) for h in heights], formats=formats, state='ready', progress=0)
                return
            quality = job.get('quality', 'best')
            safe = '[protocol~="^(https?|m3u8_native|http_dash_segments)$"]'
            cap = '' if quality == 'best' else f'[height<={quality}]'
            fmt = f'bv*{cap}{safe}+ba{safe}/b{cap}{safe}'
            if job.get('output_format') in ('mp3', 'm4a', 'wav'):
                fmt = f'ba{safe}/b{safe}'
            ytdlp(job, ['--format', fmt, '--downloader', 'native',
                       '--hls-prefer-native', '--no-progress', '--restrict-filenames', '--playlist-items', '1',
                       '--output', str(job['dir'] / '%(title).100s.%(ext)s')], None)
            files = [p for p in job['dir'].iterdir() if p.is_file() and p.suffix not in ('.part', '.ytdl', '.temp')]
            if len(files) != 1:
                raise ValueError('No complete file was produced. Try another source or quality.')
            path = files[0]
            name = path.name
        else:
            if action == 'inspect':
                if job['kind'] in ('photo', 'profile'):
                    job['asset_url'], job['title'] = image_link(job['url'], job['kind'] == 'profile')
                else:
                    job['asset_url'] = job['url']
                    job['title'] = Path(urllib.parse.urlsplit(job['url']).path).name or 'Linked file'
                job.update(state='ready', qualities=['original'], formats=output_formats(job['kind']), progress=0)
                return
            path, name, kind = fetch_file(job['asset_url'], job['dir'], job, job['kind'] in ('photo', 'profile'))
        path, name = convert_file(job, path, name)
        job.update(path=path, filename=name, content_type=mimetypes.guess_type(name)[0] or 'application/octet-stream', state='done', stage='done', progress=100)
    except Exception as e:
        job.update(state='cancelled' if job.get('cancelled') else 'error', error=str(e) if isinstance(e, ValueError) else 'The source could not be reached. Check the link and try again.')
        shutil.rmtree(job['dir'], ignore_errors=True)
    finally:
        job['updated'] = time.time()
        SLOTS.release()


def schedule(job, action):
    if not SLOTS.acquire(blocking=False):
        raise ValueError('DART is busy. Please try again shortly.')
    job['state'] = 'queued'
    POOL.submit(work, job, action)


def clean():
    while True:
        time.sleep(30)
        with LOCK:
            for key, job in list(JOBS.items()):
                if time.time() - job['updated'] > TTL and job['state'] not in ('queued', 'working'):
                    shutil.rmtree(job['dir'], ignore_errors=True)
                    del JOBS[key]


class Handler(BaseHTTPRequestHandler):
    timeout = 30

    def log_message(self, fmt, *args):
        pass  # URLs and job IDs are not logged.

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        super().end_headers()

    def reply(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def valid_host(self):
        allowed = {'localhost', '127.0.0.1', '[::1]'} | set(filter(None, os.getenv('DART_ALLOWED_HOSTS', '').split(',')))
        try:
            host = urllib.parse.urlsplit('//' + self.headers.get('Host', '')).hostname
            if host in allowed or ('[' + str(host) + ']') in allowed:
                return True
        except ValueError:
            pass
        self.reply({'error': 'Host is not allowed. Configure DART_ALLOWED_HOSTS for your deployment.'}, 403)
        return False

    def do_GET(self):
        if not self.valid_host():
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/health':
            self.reply({'status': 'ok', 'extractor': bool(shutil.which('yt-dlp')), 'ffmpeg': bool(shutil.which('ffmpeg'))})
            return
        if path.startswith('/api/jobs/'):
            parts = path.strip('/').split('/')
            with LOCK:
                job = JOBS.get(parts[2]) if len(parts) >= 3 else None
            if not job:
                self.reply({'error': 'This download expired or does not exist'}, 404)
                return
            if len(parts) == 4 and parts[3] == 'file':
                if job['state'] != 'done' or not job['path'].is_file():
                    self.reply({'error': 'File is not ready'}, 409)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(job['filename']))
                self.send_header('Content-Length', str(job['path'].stat().st_size))
                self.end_headers()
                try:
                    with job['path'].open('rb') as f:
                        shutil.copyfileobj(f, self.wfile)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            self.reply({k: job[k] for k in ('id', 'state', 'stage', 'title', 'qualities', 'formats', 'progress', 'error', 'platform', 'filename') if k in job})
            return
        asset = {'/': 'index.html', '/style.css': 'style.css', '/app.js': 'app.js', '/theme.js': 'theme.js', '/bat.svg': 'bat.svg'}.get(path)
        if not asset:
            self.send_error(404)
            return
        data = (ROOT / 'web' / asset).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(asset)[0] or 'text/plain')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self.valid_host():
            return
        try:
            # JSON + same-origin checks prevent cross-site form submissions to localhost.
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Send JSON requests')
            origin = self.headers.get('Origin')
            if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get('Host'):
                self.reply({'error': 'Cross-origin requests are blocked'}, 403)
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError('Invalid request size')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Invalid request')
            path = urllib.parse.urlsplit(self.path).path
            with LOCK:
                now = time.time()
                rates = REQUESTS[self.client_address[0]]
                while rates and now - rates[0] > 60:
                    rates.popleft()
                if len(rates) >= 30:
                    self.reply({'error': 'Too many requests. Try again in a minute.'}, 429)
                    return
                rates.append(now)
                if path == '/api/inspect':
                    if len(JOBS) >= 64:
                        raise ValueError('Session capacity reached. Old downloads expire after one hour.')
                    kind = body.get('kind')
                    if kind not in ('video', 'photo', 'profile', 'file'):
                        raise ValueError('Select a download type')
                    url = validate_url(body.get('url'))
                    key = secrets.token_urlsafe(24)
                    directory = DATA / key
                    directory.mkdir(parents=True)
                    job = dict(id=key, url=url, kind=kind, dir=directory, state='new', created=now, updated=now, platform=platform(url))
                    try:
                        schedule(job, 'inspect')
                    except Exception:
                        directory.rmdir()
                        raise
                    JOBS[key] = job
                    self.reply({'id': key}, 202)
                    return
                parts = path.strip('/').split('/')
                if len(parts) != 4 or parts[:2] != ['api', 'jobs'] or parts[3] not in ('download', 'cancel'):
                    self.reply({'error': 'Not found'}, 404)
                    return
                job = JOBS.get(parts[2])
                if not job:
                    self.reply({'error': 'Download expired'}, 404)
                    return
                if parts[3] == 'cancel':
                    job['cancelled'] = True
                    if job['state'] in ('ready', 'done', 'error'):
                        job['state'] = 'cancelled'
                        shutil.rmtree(job['dir'], ignore_errors=True)
                    self.reply({'status': 'cancelled'})
                    return
                if job['state'] != 'ready':
                    raise ValueError('Inspect the link before downloading')
                quality = str(body.get('quality', 'best'))
                if quality not in ['best', 'original'] + job.get('qualities', []):
                    raise ValueError('Select an available quality')
                if job['kind'] == 'video' and quality == 'original':
                    raise ValueError('Select a video quality')
                job['quality'] = quality
                output_format = body.get('format', 'original')
                if not isinstance(output_format, str) or output_format not in job.get('formats', ['original']):
                    raise ValueError('Select an available output format')
                job['output_format'] = output_format
                job['created'] = now
                schedule(job, 'download')
                self.reply({'id': job['id']}, 202)
        except (ValueError, TypeError, KeyError) as e:
            self.reply({'error': str(e) if type(e) is ValueError and not isinstance(e, json.JSONDecodeError) else 'Invalid request. Check your link and selection.'}, 400)


def main():
    global PROXY_URL
    DATA.mkdir(parents=True, exist_ok=True)
    # In-memory job IDs do not survive restarts; remove abandoned files.
    for child in DATA.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
    proxy, PROXY_URL = start_proxy()
    threading.Thread(target=clean, daemon=True).start()
    server = ThreadingHTTPServer((os.getenv('DART_HOST', '127.0.0.1'), int(os.getenv('PORT', '8000'))), Handler)
    server.daemon_threads = True
    print(f'DART ready at http://{server.server_address[0]}:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        proxy.shutdown()


if __name__ == '__main__':
    main()
