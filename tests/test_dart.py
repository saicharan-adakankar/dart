import io
import json
from pathlib import Path
import socket
import tempfile
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import proxy
import server


class Source(BaseHTTPRequestHandler):
    video = b''
    image = b'\x89PNG\r\n\x1a\nEXAMPLE'
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', 'http://127.0.0.1/private')
            self.end_headers()
            return
        if self.path == '/video.mp4':
            payload = self.video
            mime = 'video/mp4'
        elif self.path == '/page':
            payload = b'<meta property="og:title" content="Example photo"><meta property="og:image" content="/image.png">'
            mime = 'text/html'
        elif self.path == '/profile':
            payload = b'<script>{"profile_pic_url_hd":"http://source.test/image.png"}</script>'
            mime = 'text/html'
        elif self.path == '/image.png':
            payload = self.image
            mime = 'image/png'
        else:
            payload = b'DART integration fixture\n'
            mime = 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class DartTests(unittest.TestCase):
    def setUp(self):
        # Keep test cases independent of the real per-client mutation rate limit.
        server.REQUESTS.clear()

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.DATA = Path(cls.tmp.name)
        cls.source = ThreadingHTTPServer(('127.0.0.1', 0), Source)
        cls.guard, server.PROXY_URL = proxy.start_proxy()
        cls.app = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        for service in (cls.source, cls.app):
            threading.Thread(target=service.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.app.server_port}'
        original = proxy.connect_public

        def fixture_connect(host, port):
            if host == 'source.test':
                return socket.create_connection(cls.source.server_address, timeout=5)
            return original(host, port)

        cls.mock = patch('proxy.connect_public', side_effect=fixture_connect)
        cls.mock.start()

    @classmethod
    def tearDownClass(cls):
        cls.mock.stop()
        for service in (cls.source, cls.app, cls.guard):
            service.shutdown()
            service.server_close()
        cls.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers or ({'Content-Type':'application/json'} if body is not None else {}))
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.read(), response.headers

    def wait(self, key):
        for _ in range(100):
            data = json.loads(self.request('/api/jobs/' + key)[0])
            if data['state'] not in ('queued', 'working'):
                return data
            time.sleep(.03)
        self.fail('Job did not finish')

    def inspect(self, kind, url):
        key = json.loads(self.request('/api/inspect', {'kind':kind, 'url':url})[0])['id']
        return key, self.wait(key)

    def test_direct_file_roundtrip(self):
        key, job = self.inspect('file', 'http://source.test/file.bin')
        self.assertEqual(job['state'], 'ready')
        self.request(f'/api/jobs/{key}/download', {'quality':'original'})
        self.assertEqual(self.wait(key)['state'], 'done')
        payload, headers = self.request(f'/api/jobs/{key}/file')
        self.assertEqual(payload, b'DART integration fixture\n')
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')

    def test_page_image(self):
        key, job = self.inspect('photo', 'http://source.test/page')
        self.assertEqual(job['title'], 'Example photo')
        self.request(f'/api/jobs/{key}/download', {'quality':'original'})
        self.assertEqual(self.wait(key)['state'], 'done')
        self.assertTrue(self.request(f'/api/jobs/{key}/file')[0].startswith(b'\x89PNG'))

    def test_explicit_profile_avatar(self):
        key, job = self.inspect('profile', 'http://source.test/profile')
        self.assertEqual(job['state'], 'ready')
        self.request(f'/api/jobs/{key}/download', {'quality':'original'})
        self.assertEqual(self.wait(key)['state'], 'done')

    def test_profile_does_not_use_generic_og_image(self):
        _, job = self.inspect('profile', 'http://source.test/page')
        self.assertEqual(job['state'], 'error')
        self.assertIn('avatar', job['error'])

    def test_redirect_to_private_network_blocked(self):
        key, _ = self.inspect('file', 'http://source.test/redirect')
        self.request(f'/api/jobs/{key}/download', {'quality':'original'})
        self.assertEqual(self.wait(key)['state'], 'error')

    def test_private_destination_blocked(self):
        with self.assertRaises(ValueError):
            proxy.public_addresses('127.0.0.1', 80)
        with self.assertRaises(ValueError):
            proxy.public_addresses('::1', 443)

    def test_mixed_dns_blocked(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))]
        with patch('socket.getaddrinfo', return_value=answers), self.assertRaises(ValueError):
            proxy.public_addresses('example.test', 443)

    def test_connect_uses_validated_ip(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34',443))]
        # Call original implementation; integration fixture routes source.test only.
        with patch('proxy.public_addresses', return_value=answers), patch('socket.socket') as mock:
            proxy.connect_public('example.test',443)
            mock.return_value.connect.assert_called_once_with(('93.184.216.34',443))

    def test_bad_urls(self):
        for value in ('file:///etc/passwd','ftp://example.com','https://u:p@example.com','https://example.com:8080','https://example.com/\r\nx',None):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                server.validate_url(value)

    def test_source_detection_not_spoofed(self):
        self.assertEqual(server.platform('https://youtube.com.evil.test/watch'), 'Web')
        self.assertEqual(server.platform('https://m.youtube.com/watch'), 'YouTube')

    def test_cancel(self):
        key, _ = self.inspect('file','http://source.test/file.bin')
        self.request(f'/api/jobs/{key}/cancel', {})
        self.assertEqual(self.wait(key)['state'], 'cancelled')

    def test_cross_origin_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/inspect',{}, {'Content-Type':'application/json','Origin':'https://other.test'})
        self.assertEqual(caught.exception.code,403)

    def test_static_and_traversal(self):
        payload, headers = self.request('/')
        self.assertIn(b'Within reach.',payload)
        self.assertIn("script-src 'self'",headers['Content-Security-Policy'])
        with self.assertRaises(urllib.error.HTTPError):
            self.request('/../server.py')

    def test_video_pipeline(self):
        info = {'title':'Fixture video','formats':[{'height':720,'vcodec':'h264'},{'height':1080,'vcodec':'h264'}]}
        def fake_extract(job, args, timeout):
            if '--dump-single-json' in args:
                return json.dumps(info)
            (job['dir']/'fixture.mp4').write_bytes(b'VIDEO')
            return ''
        with patch('server.ytdlp', side_effect=fake_extract):
            key, job = self.inspect('video','https://youtube.com/watch?v=fixture')
            self.assertEqual(job['qualities'],['1080','720'])
            self.request(f'/api/jobs/{key}/download',{'quality':'720'})
            self.assertEqual(self.wait(key)['state'],'done')

    def test_real_video_extractor(self):
        import importlib.util
        import shutil
        if not shutil.which('ffmpeg') or not importlib.util.find_spec('yt_dlp'):
            self.skipTest('Install FFmpeg and requirements to verify actual extraction')
        target = Path(self.tmp.name) / 'fixture.mp4'
        subprocess.run(['ffmpeg','-y','-f','lavfi','-i','color=c=black:s=160x120:d=1',
                        '-c:v','mpeg4',str(target)],check=True,capture_output=True)
        Source.video = target.read_bytes()
        key, job = self.inspect('video','http://source.test/video.mp4')
        self.assertEqual(job['state'],'ready',job)
        self.request(f'/api/jobs/{key}/download',{'quality':'best'})
        # Actual subprocess extraction can take several seconds.
        for _ in range(200):
            job = json.loads(self.request('/api/jobs/' + key)[0])
            if job['state'] not in ('queued','working'):
                break
            time.sleep(.1)
        self.assertEqual(job['state'],'done',job)
        self.assertEqual(self.request(f'/api/jobs/{key}/file')[0],Source.video)

    def test_dns_rebinding_host_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/',headers={'Host':'attacker.test'})
        self.assertEqual(caught.exception.code,403)

    def media_fixture(self, image=False):
        import shutil
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and FFprobe are required for format verification')
        target = Path(self.tmp.name) / ('real.png' if image else 'real.mp4')
        if image:
            args = ['-f','lavfi','-i','color=c=red:s=32x32','-frames:v','1']
        else:
            args = ['-f','lavfi','-i','color=c=blue:s=32x32:r=2:d=1','-f','lavfi','-i',
                    'sine=frequency=440:duration=1','-c:v','libx264','-c:a','aac','-shortest']
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y']+args+['-threads','1',str(target)],check=True,capture_output=True)
        return target

    def probe(self, path):
        result = subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],check=True,capture_output=True,text=True)
        return json.loads(result.stdout)

    def test_video_and_audio_conversion_codecs(self):
        source = self.media_fixture()
        expected = {'mp4':('h264','aac'),'mkv':('h264','aac'),'webm':('vp9','opus'),
                    'mp3':(None,'mp3'),'m4a':(None,'aac'),'wav':(None,'pcm_s16le')}
        for fmt, codecs in expected.items():
            with self.subTest(format=fmt), tempfile.TemporaryDirectory(dir=self.tmp.name) as directory:
                path = Path(directory)/'source.mp4'
                path.write_bytes(source.read_bytes())
                job = {'dir':Path(directory),'kind':'video','output_format':fmt}
                output, name = server.convert_file(job,path,'example.mp4')
                streams = self.probe(output)['streams']
                video = next((s['codec_name'] for s in streams if s['codec_type']=='video'),None)
                audio = next((s['codec_name'] for s in streams if s['codec_type']=='audio'),None)
                self.assertEqual((video,audio),codecs)
                self.assertEqual(name,'example.'+fmt)
                self.assertFalse(path.exists())

    def test_image_conversion_signatures_and_dimensions(self):
        source = self.media_fixture(image=True)
        for fmt in ('png','jpg','webp'):
            with self.subTest(format=fmt), tempfile.TemporaryDirectory(dir=self.tmp.name) as directory:
                path = Path(directory)/'source.png'
                path.write_bytes(source.read_bytes())
                job = {'dir':Path(directory),'kind':'photo','output_format':fmt}
                output, name = server.convert_file(job,path,'example.png')
                stream = self.probe(output)['streams'][0]
                self.assertEqual((stream['width'],stream['height']),(32,32))
                self.assertEqual(name,'example.'+fmt)
                self.assertTrue(output.stat().st_size)

    def test_image_format_api_roundtrip(self):
        source = self.media_fixture(image=True)
        previous = Source.image
        Source.image = source.read_bytes()
        try:
            key, job = self.inspect('photo','http://source.test/page')
            self.assertIn('jpg',job['formats'])
            self.request(f'/api/jobs/{key}/download',{'quality':'original','format':'jpg'})
            job = self.wait(key)
            self.assertEqual(job['state'],'done',job)
            payload, headers = self.request(f'/api/jobs/{key}/file')
            self.assertTrue(payload.startswith(b'\xff\xd8\xff'))
            self.assertIn('.jpg',headers['Content-Disposition'])
        finally:
            Source.image = previous

    def test_video_to_audio_api_roundtrip(self):
        source = self.media_fixture()
        Source.video = source.read_bytes()
        key, job = self.inspect('video','http://source.test/video.mp4')
        self.assertIn('mp3',job['formats'])
        self.request(f'/api/jobs/{key}/download',{'quality':'best','format':'mp3'})
        job = self.wait(key)
        self.assertEqual(job['state'],'done',job)
        self.assertTrue(job['filename'].endswith('.mp3'))
        streams = self.probe(server.JOBS[key]['path'])['streams']
        self.assertEqual([s['codec_type'] for s in streams],['audio'])

    def test_format_rejected_for_general_file(self):
        key, job = self.inspect('file','http://source.test/file.bin')
        self.assertEqual(job['formats'],['original'])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(f'/api/jobs/{key}/download',{'quality':'original','format':'mp4'})
        self.assertEqual(caught.exception.code,400)
        self.assertEqual(self.wait(key)['state'],'ready')

    def test_missing_ffmpeg_only_offers_original(self):
        with patch('server.shutil.which',return_value=None):
            self.assertEqual(server.output_formats('video'),['original'])
            self.assertEqual(server.output_formats('photo'),['original'])

    def test_conversion_cancellation(self):
        job = {}
        timer = threading.Timer(.1,lambda:job.update(cancelled=True))
        timer.start()
        try:
            with self.assertRaisesRegex(ValueError,'cancelled'):
                server.run_process(job,[sys.executable,'-c','import time; time.sleep(20)'])
            self.assertNotIn('process',job)
        finally:
            timer.cancel()

    def test_invalid_image_is_not_decoded(self):
        with tempfile.TemporaryDirectory(dir=self.tmp.name) as directory:
            path = Path(directory)/'fake.png'
            path.write_text('not an image')
            with self.assertRaisesRegex(ValueError,'supports PNG'):
                server.convert_file({'dir':Path(directory),'kind':'photo','output_format':'png'},path,'fake.png')

    def test_theme_script_served_under_csp(self):
        data, headers = self.request('/theme.js')
        self.assertIn(b'dart-theme',data)
        self.assertIn('javascript',headers['Content-Type'])

    def profile_response(self, body, url, content_type='text/html'):
        response = io.BytesIO(body.encode())
        response.url = url
        response.headers = server.email.message.Message()
        response.headers['Content-Type'] = content_type
        return response

    def test_private_and_public_instagram_avatars(self):
        url = 'https://www.instagram.com/example/'
        for private in (True,False):
            with self.subTest(private=private):
                data = {'require':[{'user':{'username':'example','is_private':private,
                        'profile_pic_url':'https://images.test/small.jpg',
                        'profile_pic_url_hd':'https://images.test/large.jpg'}}],
                        'suggested':{'username':'someone_else','profile_pic_url_hd':'https://images.test/wrong.jpg'}}
                body = '<script type="application/json">'+json.dumps(data)+'</script>'
                with patch('server.open_url',return_value=self.profile_response(body,url)) as opened:
                    avatar, title = server.image_link(url,profile=True)
                self.assertEqual(avatar,'https://images.test/large.jpg')
                self.assertIn('@example',title)
                opened.assert_called_once_with(url)

    def test_encoded_profile_data(self):
        data = {'username':'example','profile_pic_url_hd':'https://images.test/large.jpg'}
        body = '<script type="application/json">'+json.dumps({'payload':json.dumps(data)})+'</script>'
        parser = server.Metadata()
        parser.feed(body)
        self.assertEqual(server.profile_image(body,parser,'https://www.instagram.com/example/','example'),data['profile_pic_url_hd'])

    def test_source_hd_avatar_preferred_over_thumbnail(self):
        node = {'profile_pic_url':'https://images.test/thumbnail.jpg',
                'hd_profile_pic_url_info':{'url':'https://images.test/source.jpg','width':1080,'height':1080}}
        self.assertEqual(server.avatar_field(node),'https://images.test/source.jpg')

    def test_largest_source_avatar_variant(self):
        node = {'profile_pic_url':'https://images.test/thumbnail.jpg','hd_profile_pic_versions':[
            {'url':'https://images.test/320.jpg','width':320,'height':320},
            {'url':'https://images.test/1080.jpg','width':1080,'height':1080},
            {'url':'https://images.test/640.jpg','width':640,'height':640}]}
        self.assertEqual(server.avatar_field(node),'https://images.test/1080.jpg')

    def test_instagram_api_fallback(self):
        url = 'https://www.instagram.com/example/'
        page = '<title>Sign in to Instagram</title>'
        api = json.dumps({'data':{'user':{'username':'example','is_private':True,'profile_pic_url_hd':'https://images.test/avatar.jpg'}}})
        with patch('server.open_url',side_effect=[self.profile_response(page,url),self.profile_response(api,url,'application/json')]) as opened:
            avatar, _ = server.image_link(url,profile=True)
        self.assertEqual(avatar,'https://images.test/avatar.jpg')
        self.assertIn('username=example',opened.call_args_list[1].args[0])

    def test_instagram_api_identity_mismatch(self):
        api = json.dumps({'data':{'user':{'username':'wrong','profile_pic_url':'https://images.test/wrong.jpg'}}})
        with patch('server.open_url',return_value=self.profile_response(api,'https://www.instagram.com/','application/json')):
            self.assertIsNone(server.instagram_api_avatar('example'))

    def test_instagram_rejects_post_as_profile(self):
        for path in ('p/123','reel/123','accounts/login','explore','example/tagged'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError,'profile link'):
                server.instagram_username('https://www.instagram.com/'+path)

    def test_verified_profile_og_image(self):
        url = 'https://www.instagram.com/example/'
        body = '<meta property="og:type" content="profile"><meta property="og:url" content="'+url+'"><meta property="og:image" content="https://images.test/avatar.jpg">'
        parser = server.Metadata()
        parser.feed(body)
        self.assertEqual(server.profile_image(body,parser,url,'example'),'https://images.test/avatar.jpg')
        self.assertIsNone(server.profile_image(body,parser,url,'different'))

    def test_profile_schema_main_entity(self):
        data = {'@type':'ProfilePage','mainEntity':{'@type':'Person','image':{'contentUrl':'https://images.test/person.png'}}}
        body = '<script type="application/ld+json">'+json.dumps(data)+'</script>'
        parser = server.Metadata()
        parser.feed(body)
        self.assertEqual(server.profile_image(body,parser,'https://source.test/person'),'https://images.test/person.png')


if __name__ == '__main__':
    unittest.main()
