import io
import json
import os
from pathlib import Path
import shutil
import unittest
import urllib.error
from unittest.mock import patch

import server
import spotify_match
import test_dart

URL = 'https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC'
TRACK = dict(title='Never Gonna Give You Up', artists=['Rick Astley'], duration=213.573, url=URL)
MATCH = dict(id='dQw4w9WgXcQ', title='Rick Astley - Never Gonna Give You Up', duration=214,
             channel='Rick Astley', url='https://www.youtube.com/watch?v=dQw4w9WgXcQ', difference=.4)


class MetadataTests(unittest.TestCase):
    def test_track_validation(self):
        self.assertEqual(spotify_match.track_id(URL + '?si=example'), URL.rsplit('/', 1)[1])
        for url in [URL.replace('/track/', '/playlist/'), URL.replace('open.spotify.com', 'evil.test'),
                    URL.replace('https:', 'http:'), 'https://spotify.link/example']:
            with self.assertRaises(ValueError):
                spotify_match.track_id(url)

    def test_metadata_only_no_audio_requests(self):
        entity = dict(uri='spotify:track:' + URL.rsplit('/', 1)[1], name=TRACK['title'],
                      artists=[{'name': 'Rick Astley'}], duration=213573,
                      audioPreview={'url': 'https://audio.test/never-request'})
        payload = {'props': {'pageProps': {'state': {'data': {'entity': entity}}}}}
        page = ('<script id="__NEXT_DATA__">' + json.dumps(payload) + '</script>').encode()
        with patch('server.open_url', return_value=io.BytesIO(page)) as request:
            result = spotify_match.metadata(URL, request)
        self.assertEqual(result, TRACK)
        request.assert_called_once_with('https://open.spotify.com/embed/track/' + URL.rsplit('/', 1)[1])

    def test_mismatched_identity_rejected(self):
        entity = dict(uri='spotify:track:wrong', name='Song', artists=[{'name':'Artist'}], duration=200000)
        data = {'props': {'pageProps': {'state': {'data': {'entity': entity}}}}}
        with self.assertRaisesRegex(ValueError, 'mismatched'):
            spotify_match.metadata(URL, lambda _: io.BytesIO(('<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>').encode()))

    def test_reject_wrong_recordings_and_sort(self):
        entries = [MATCH, dict(MATCH, id='aaaaaaaaaaa', title='Rick Astley - Never Gonna Give You Up live'),
                   dict(MATCH, id='bbbbbbbbbbb', duration=400),
                   dict(MATCH, id='ccccccccccc', title='Another Song', channel='Other'),
                   dict(MATCH, id='ddddddddddd', title='Rick Astley - Never Gonna Give You Up remix')]
        self.assertEqual([m['id'] for m in spotify_match.candidates(TRACK, entries)], [MATCH['id']])
        for version in ('pianoforte', 'acoustic', 'remaster'):
            self.assertEqual(spotify_match.candidates(TRACK, [dict(MATCH, title=MATCH['title'] + ' ' + version)]), [])

    def test_disabled_does_not_contact_sources(self):
        with patch.dict(os.environ, {'DART_EXPERIMENTAL_SPOTIFY': '0'}), patch('server.open_url') as request:
            with self.assertRaisesRegex(ValueError, 'disabled'):
                spotify_match.inspect(URL, request, None)
            request.assert_not_called()


class SpotifyAPI(unittest.TestCase):
    setUpClass = classmethod(test_dart.DartTests.setUpClass.__func__)
    tearDownClass = classmethod(test_dart.DartTests.tearDownClass.__func__)
    setUp = test_dart.DartTests.setUp
    request = test_dart.DartTests.request
    wait = test_dart.DartTests.wait
    inspect = test_dart.DartTests.inspect
    media_fixture = test_dart.DartTests.media_fixture
    probe = test_dart.DartTests.probe

    def test_disabled_api_and_discovery(self):
        with patch.dict(os.environ, {'DART_EXPERIMENTAL_SPOTIFY': '0'}):
            self.assertFalse(json.loads(self.request('/api/features')[0])['spotify_matching'])
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.inspect('music', URL)
            self.assertEqual(error.exception.code, 400)

    def test_confirmation_and_download_roundtrip(self):
        source = self.media_fixture()
        def download(job, args, timeout, url=None):
            self.assertEqual(url, MATCH['url'])
            self.assertIn('ba[protocol', args[1])
            shutil.copyfile(source, job['dir'] / 'test.mp4')
            return ''
        with patch.dict(os.environ, {'DART_EXPERIMENTAL_SPOTIFY': '1'}), \
             patch('spotify_match.inspect', return_value=(TRACK, [MATCH])), \
             patch('server.ytdlp', side_effect=download):
            key, job = self.inspect('music', URL)
            self.assertEqual(job['matches'][0]['url'], MATCH['url'])
            for match in [None, 'unknown-source']:
                with self.assertRaises(urllib.error.HTTPError):
                    self.request(f'/api/jobs/{key}/download', {'format': 'mp3', 'match': match})
            self.request(f'/api/jobs/{key}/download', {'format': 'mp3', 'match': MATCH['id']})
            result = self.wait(key)
            self.assertEqual(result['state'], 'done', result)
            self.assertEqual([s['codec_type'] for s in self.probe(server.JOBS[key]['path'])['streams']], ['audio'])
            self.assertTrue(self.request(f'/api/jobs/{key}/file')[0])

    def test_disable_between_inspect_and_download(self):
        with patch.dict(os.environ, {'DART_EXPERIMENTAL_SPOTIFY': '1'}), patch('spotify_match.inspect', return_value=(TRACK, [MATCH])):
            key, _ = self.inspect('music', URL)
        with patch.dict(os.environ, {'DART_EXPERIMENTAL_SPOTIFY': '0'}):
            with self.assertRaises(urllib.error.HTTPError):
                self.request(f'/api/jobs/{key}/download', {'format':'original', 'match':MATCH['id']})
