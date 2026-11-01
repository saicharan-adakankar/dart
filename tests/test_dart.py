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
            payload = b'\x89PNG\r\n\x1a\nEXAMPLE'
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

    def test_size_limit(self):
        with patch('server.MAX_BYTES', 2):
            key, _ = self.inspect('file', 'http://source.test/file.bin')
            self.request(f'/api/jobs/{key}/download',{'quality':'original'})
            job = self.wait(key)
            self.assertEqual(job['state'], 'error')
            self.assertFalse(server.JOBS[key]['dir'].exists())

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


if __name__ == '__main__':
    unittest.main()
