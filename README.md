# DART 🦇

**Download And Retrieve Tool** · One link. Within reach.

A minimal, bat-themed web downloader with a charcoal/violet interface and a real Python download backend. No frontend build step, account, analytics, or API key required.

## Run with Docker

Install Docker Desktop (or Docker Engine with Compose), then:

```bash
git clone https://github.com/saicharan-adakankar/dart.git
cd dart
docker compose up --build
```

Open **http://localhost:8000**. Stop with `docker compose down`.

The container includes Python, FFmpeg, Node.js 22, and yt-dlp. It runs as a non-root user with a read-only filesystem and temporary download storage. The published port is bound to your computer, not all network interfaces.

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
3. Review the detected source, file title, and available video resolutions.
4. Select **Prepare download**, then **Save file** when ready.

Downloads run on the server first. Files expire after one hour; restarting clears abandoned download directories. Cancel or select **New link** to discard a job. Job IDs are unguessable access links: treat them as private.

## Capabilities and limits

| Mode | Implemented behavior | Limits |
| --- | --- | --- |
| Video | Real yt-dlp inspection and downloads; best available or resolution cap; FFmpeg merges source streams without re-encoding | Single, non-live videos. Source can require login, block bots, or change extraction. No DRM bypass, cookies, playlists, or universal compatibility. |
| Photo | Direct raster images, or the main `og:image` / `twitter:image` exposed by a webpage | The page can provide a compressed preview. No carousel/gallery extraction yet. |
| Profile picture | Direct avatar image URLs, or explicit `profile_pic_url_hd`, `profile_pic_url`, or `avatar_url` metadata in public HTML | Generic social-page preview images are deliberately not treated as avatars. Instagram, Facebook, and X often hide avatar data or require login; those links return an explanatory error. |
| File | Streams direct HTTP/HTTPS file URLs to temporary storage, preserving bytes | A webpage is not a direct file. Expiring, signed, or authenticated links may fail. |

YouTube, Instagram, Facebook, and X are recognized automatically. Their video support comes from yt-dlp; recognition is **not** a guarantee that every platform/mode combination works. No live social-platform downloads have been verified in the development environment. A generated video fixture is verified end-to-end with the actual extractor.

DART does not upscale media or recover quality removed by a source. Container and codec depend on available formats. Photo/profile bytes are not recompressed. Download only content you own or have permission to save.

Limits: 512 MB per job's working files (including intermediate video streams), two active workers, four admitted operations, 64 retained jobs, 30 mutation requests/minute/client, 10-minute download timeout, 80-second video-inspection timeout. Video merging can need temporary space larger than the final file. Docker temporary storage is capped at 1.2 GB; capacity errors require discarding earlier downloads or waiting for expiry.

## Architecture

- `web/`: responsive vanilla HTML/CSS/JavaScript; accessible radio selector, validation, status, cancellation, and explicit save link.
- `server.py`: standard-library threaded HTTP service, bounded worker pool, in-memory job state, safe file serving, and expiry cleanup.
- `proxy.py`: loopback-only egress proxy used by direct fetches and yt-dlp. It resolves each destination, rejects any private/reserved DNS answers, and connects to the validated IP. Redirect requests are checked again. Only ports 80/443 are allowed. Native HTTP media download protocols are selected; FFmpeg is used for local merging.
- `.github/workflows/checks.yml`: API/security tests, real generated-video extraction, JavaScript syntax validation, and Docker build.

No uploaded cookies, platform credentials, custom extractor commands, external image requests from the frontend, or private-network fetching. Responses include a restrictive content security policy and no-store headers. Requests do not log submitted URLs or job identifiers.

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

Tests use controlled local fixtures through the network guard; they do not need social accounts. They cover exact-byte file downloads, photo metadata, explicit avatars, generic-avatar rejection, private redirects, mixed public/private DNS answers, pinned-IP connections, Host validation, cancellation, working-size limits, traversal, and the real yt-dlp video pipeline.

Browser visual QA and a local Docker build were unavailable in the development environment. CI performs the Docker build; inspect its result before relying on that packaging.

## Maintenance

yt-dlp is pinned in `requirements.txt` for reproducibility. Platform changes may require a reviewed version update and a container rebuild. See the [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp#readme) for source compatibility and JavaScript-runtime requirements.
