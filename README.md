# DART 🦇

**Download And Retrieve Tool** · One link. Within reach.

A minimal, bat-themed web downloader with a pitch-black/violet dark theme, a light theme, interactive platform tiles, format selection and a real Python download backend. No frontend build step, account, analytics, or API key required.

## Run with Docker (optional)

Install Docker Desktop (or Docker Engine with Compose), then:

```bash
git clone https://github.com/saicharan-adakankar/dart.git
cd dart
docker compose up --build
```

Open **http://localhost:8000**. Stop with `docker compose down`.

The container includes Python, FFmpeg, Node.js 22, and yt-dlp. It runs as a non-root user with a read-only filesystem and download storage cleared at startup. The published port is bound to your computer, not all network interfaces.

## Run without Docker

Requires Python 3.10+, FFmpeg on PATH, and Node.js 22 on PATH for modern YouTube extraction.

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python server.py
```

Open http://localhost:8000. Video extraction uses a POSIX process group for cancellation; Docker is the supported Windows path.

## Use it

1. Choose **Video**, **Photo**, **Profile picture**, or **File**.
2. Paste a public link and select **Find**.
3. Review the detected source and file title, then choose a quality and output format. Platform tiles select the source; Auto detects it from your link.
4. Select **Prepare download**, then **Save file** when ready.

Use the header theme button to switch between pitch-black dark mode and light mode. DART remembers your choice in your browser. Phone portrait uses a three-column platform grid and full-width controls; phone landscape uses a compact side-by-side layout, with larger touch inputs and safe-area padding.

Downloads run on the server first. Files expire after one hour; restarting clears abandoned download directories. Cancel or select **New link** to discard a job. Job IDs are unguessable access links: treat them as private.

## Capabilities and limits

| Mode | Implemented behavior | Limits |
| --- | --- | --- |
| Video | Real yt-dlp inspection and downloads; best available or resolution cap; FFmpeg merges source streams without re-encoding | Single, non-live videos. Source can require login, block bots, or change extraction. No DRM bypass, cookies, playlists, or universal compatibility. |
| Photo | Direct raster images, or the main `og:image` / `twitter:image` exposed by a webpage | The page can provide a compressed preview. No carousel/gallery extraction yet. |
| Profile picture | Direct avatars; account-matched Instagram hydration data and public profile-metadata fallback; explicit avatar fields, verified profile OG metadata, and ProfilePage schema | Private accounts are accepted when their avatar is publicly exposed. Generic page logos and avatars from suggested accounts are rejected. Platforms can block requests or require login; those failures cannot be bypassed. |
| File | Streams direct HTTP/HTTPS file URLs to temporary storage, preserving bytes | A webpage is not a direct file. Expiring, signed, or authenticated links may fail. |

YouTube, Instagram, Facebook, and X are recognized automatically. Their video support comes from yt-dlp; recognition is **not** a guarantee that every platform/mode combination works. No live social-platform downloads or avatar lookups have been verified in the development environment. A generated video fixture is verified end-to-end with the actual extractor.

### Output formats

| Content | Choices | Behavior |
| --- | --- | --- |
| Video | Original, MP4, MKV, WebM | Original preserves downloaded source streams. MKV remuxes without re-encoding. MP4 converts to H.264/AAC; WebM converts to VP9/Opus. |
| Audio from video | MP3, M4A, WAV | Downloads an audio-only source when available, then converts. MP3 uses high-quality VBR, M4A uses 256 kbps AAC, WAV uses uncompressed 16-bit PCM. Video resolution selection is hidden for audio outputs. |
| Photo / profile picture | Original, PNG, JPEG, WebP | Original preserves bytes. Conversion supports PNG/JPEG/WebP inputs, uses the first frame, and retains source dimensions. JPEG drops transparency; WebP uses lossless encoding. Other image types require Original. |
| General files | Original | No arbitrary document/archive/file conversion. |

Conversion choices require FFmpeg. If it is missing, only Original is offered. Known silent video sources do not offer audio formats; sources with unknown codecs may still offer audio, with an error if no audio stream exists. Some source containers/codecs may not be convertible. DART explains conversion failures rather than changing a filename extension without converting the contents.

DART does not upscale media or recover quality removed by a source. Re-encoding can alter quality and take significant time; Original and MKV avoid re-encoding. MP4 may pad odd dimensions by one pixel for codec compatibility. Conversion is cancellable and has no overall duration or file-size cap. Download only content you own or have permission to save.

DART imposes no file-size cap or overall download-duration limit. Available disk space and source restrictions still apply; video merging needs additional temporary space. Network inactivity timeouts remain to detect stalled connections. Other limits: two active workers, four admitted operations, 64 retained jobs, 30 mutation requests/minute/client, and an 80-second video-inspection timeout. Completed and inactive jobs expire after one hour; active downloads are not expired.

## Architecture

- `web/`: responsive vanilla HTML/CSS/JavaScript; interactive platform buttons, persistent themes, portrait/landscape layouts, quality/format selection, validation, status, cancellation, and explicit save link.
- `server.py`: standard-library threaded HTTP service, bounded worker pool, in-memory job state, safe file serving, and expiry cleanup.
- `proxy.py`: loopback-only egress proxy used by direct fetches and yt-dlp. It resolves each destination, rejects any private/reserved DNS answers, and connects to the validated IP. Redirect requests are checked again. Only ports 80/443 are allowed. Native HTTP media download protocols are selected; FFmpeg is used for local merging.
- `.github/workflows/checks.yml`: API/security tests, real generated-video extraction, JavaScript syntax validation, and Docker build.

No uploaded cookies, platform credentials, custom extractor commands, external image requests from the frontend, or private-network fetching. Instagram avatar lookup uses public page data first, then one public metadata request; it does not access private posts or automatically retry blocks. Responses include a restrictive content security policy and no-store headers. Requests do not log submitted URLs or job identifiers.

## Deployment

This version is designed for **single-user local use**. A static host or GitHub Pages cannot run its backend. The Python/FFmpeg runtime also does not fit Cloudflare Workers / Sites hosting.

For a private server, run the Docker container behind an authenticated HTTPS reverse proxy and configure `DART_ALLOWED_HOSTS` with the exact public hostname (comma-separated, without scheme or port). Preserve the external Host header. The app checks same-origin JSON requests and allowed Host names. Do not expose this unauthenticated single-user service directly to the public internet: jobs are not separated into user accounts, and in-memory state/rate limits do not span replicas. Add identity, per-user quotas, persistent job coordination, and deployment-level egress restrictions before offering a public service.

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DART_HOST` | `127.0.0.1` | Listener address (`0.0.0.0` inside Docker) |
| `PORT` | `8000` | Listener port |
| `DART_DATA` | `.dart-data` next to the server | Dedicated temporary directory; child directories are deleted at startup |
| `DART_ALLOWED_HOSTS` | empty | Additional allowed request hostnames; localhost and loopback are always allowed |

Use a dedicated empty `DART_DATA` directory. Never point it to unrelated user files. Only one server process should use a data directory.

## Verify

```bash
python -m unittest discover -s tests -v
node --check web/app.js
```

Tests use controlled local fixtures through the network guard; they do not need social accounts. They cover exact-byte file downloads, photo metadata, explicit avatars, generic-avatar rejection, private redirects, mixed public/private DNS answers, pinned-IP connections, Host validation, cancellation, traversal, and the real yt-dlp video pipeline.

Desktop dark/light themes, theme persistence, platform selection and link mismatch handling were checked in the browser. Static frontend snapshots were visually checked in 390 × 844 portrait and 844 × 390 landscape frames; actual device testing remains recommended. Backend API tests cover real image/video/audio conversions and codec verification. A local Docker build was unavailable; CI performs the build.

## Maintenance

yt-dlp is pinned in `requirements.txt` for reproducibility. Platform changes may require a reviewed version update and a container rebuild. See the [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp#readme) for source compatibility and JavaScript-runtime requirements.

### Profile picture lookup

Instagram profile links are parsed as account identities (post/Reel URLs are rejected). The resolver prefers high-resolution avatar fields belonging to that username, including nested and JSON-encoded page data. A private-account flag alone does not prevent retrieval of an avatar the platform returns publicly. If the page lacks the avatar, DART tries the public profile-metadata endpoint once. It does not use third-party lookup services, logins, or cookies. The endpoint is unofficial and may change, throttle requests, or require login. Controlled fixtures verify both private/public flags and identity matching; successful retrieval for every live profile is not guaranteed.
