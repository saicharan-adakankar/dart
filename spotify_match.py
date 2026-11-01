"""Opt-in Spotify metadata matching. Never reads Spotify audio or credentials."""
import json
import os
import re
import urllib.parse


def enabled():
    return os.getenv('DART_EXPERIMENTAL_SPOTIFY', '0') == '1'


def track_id(url):
    parsed = urllib.parse.urlsplit(url)
    match = re.fullmatch(r'/(?:intl-[a-z]{2}/)?track/([A-Za-z0-9]{22})/?', parsed.path)
    if parsed.scheme != 'https' or parsed.hostname != 'open.spotify.com' or not match:
        raise ValueError('Use a Spotify track URL: https://open.spotify.com/track/… Albums, playlists and short links are not supported in this experiment.')
    return match[1]


def metadata(url, open_url):
    identity = track_id(url)
    with open_url('https://open.spotify.com/embed/track/' + identity) as response:
        page = response.read(2 * 1024 * 1024).decode('utf-8', errors='replace')
    match = re.search(r'<script\b[^>]*\bid=[\"\']__NEXT_DATA__[\"\'][^>]*>(.*?)</script>', page, re.S)
    if not match:
        raise ValueError('Spotify did not return readable track metadata. This experimental lookup may need updating.')
    try:
        entity = json.loads(match[1])['props']['pageProps']['state']['data']['entity']
        title = entity.get('name') or entity.get('title')
        artists = [a['name'] for a in entity.get('artists', []) if isinstance(a.get('name'), str) and a['name'].strip()]
        duration = entity.get('duration')
        if entity.get('uri') != 'spotify:track:' + identity or not isinstance(title, str) or not title.strip() or not artists:
            raise ValueError()
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not 0 < duration < 24 * 3600 * 1000:
            raise ValueError()
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError('Spotify returned incomplete or mismatched track metadata. No substitute audio was downloaded.') from None
    return {'title': title[:200], 'artists': artists[:10], 'duration': duration / 1000,
            'url': 'https://open.spotify.com/track/' + identity}


def tokens(value):
    return set(re.findall(r'[^\W_]+', value.casefold()))


def candidates(track, entries):
    """Conservative suggestions, never a guarantee of recording identity."""
    results = []
    seen = set()
    for entry in entries[:5]:
        identity = entry.get('id', '')
        title = entry.get('title')
        duration = entry.get('duration')
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', identity) or identity in seen:
            continue
        if not isinstance(title, str) or not isinstance(duration, (int, float)) or isinstance(duration, bool):
            continue
        title_tokens = tokens(title)
        expected = tokens(track['title'])
        artist_tokens = tokens(' '.join(track['artists']))
        artist_text = title_tokens | tokens(str(entry.get('channel') or entry.get('uploader') or ''))
        delta = abs(duration - track['duration'])
        unexpected = (tokens('cover remix live karaoke instrumental slowed sped nightcore acoustic pianoforte piano remaster remastered rework') & title_tokens) - tokens(track['title'])
        if not expected or len(expected & title_tokens) / len(expected) < .75 or not artist_tokens & artist_text:
            continue
        if delta > max(12, track['duration'] * .06) or unexpected:
            continue
        seen.add(identity)
        results.append({'id': identity, 'title': title[:200], 'duration': duration,
                        'channel': str(entry.get('channel') or entry.get('uploader') or '')[:120],
                        'url': 'https://www.youtube.com/watch?v=' + identity, 'difference': round(delta, 1)})
    return sorted(results, key=lambda item: item['difference'])


def inspect(url, open_url, extract):
    if not enabled():
        raise ValueError('Spotify matching is disabled. Set DART_EXPERIMENTAL_SPOTIFY=1 to test it locally.')
    track = metadata(url, open_url)
    query = ' '.join([track['title'], *track['artists'], 'official audio'])[:350]
    payload = json.loads(extract('ytsearch5:' + query, ['--flat-playlist', '--dump-single-json', '--skip-download'], 80))
    matches = candidates(track, payload.get('entries') or [])
    if not matches:
        raise ValueError('No sufficiently close audio match was found. DART will not download an unrelated recording. Try a direct source link instead.')
    return track, matches
