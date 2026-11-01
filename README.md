# DART

**Download And Retrieve Tool**

A minimal web-based media and file downloader with a bat-inspired identity.

> One link. Within reach.

## How it works

1. Select **Video**, **Photo**, **Profile Picture**, or **File**.
2. Paste a link; DART automatically detects the platform.
3. Preview the available results and choose a quality or file.
4. Download with clear progress and error messages.

Default to the highest available source quality. Preserve original resolution and encoding where accessible; quality already lost through platform compression cannot be recovered.

## Visual identity

- Bat mascot with a simple, recognizable silhouette.
- Charcoal and violet palette.
- One central download panel; minimal controls.
- Responsive and accessible on mobile and desktop.
- No user account required for the initial version.

## Target platforms

Instagram, YouTube, Facebook, X, and direct file links.

These are implementation targets, not currently supported integrations. Video, photo, and profile-picture capabilities must be implemented and verified separately for each platform. Public links do not always expose downloadable originals. Show clear messages for unsupported content, login requirements, and source restrictions. Do not bypass private access or DRM. Users should download content they own or have permission to retrieve.

## Roadmap

- [ ] Minimal bat-themed interface and download-type selector.
- [ ] URL validation and automatic platform detection.
- [ ] Backend media inspection and available-quality selection.
- [ ] Bounded direct-file streaming.
- [ ] Platform-specific video and photo adapters.
- [ ] Profile-picture adapters where supported by the source.
- [ ] Download jobs, progress, cancellation, and temporary-file cleanup.
- [ ] Security, accessibility, and functional verification.
- [ ] Local setup and deployment documentation.

## Engineering requirements

Media extraction and large-file handling require a backend. Choose hosting that supports the extractor runtime, media merging, temporary storage, and download duration. Before public exposure, enforce SSRF protection across URL redirects and resolved destinations, private-network egress restrictions, file-size and concurrency limits, timeouts, and rate limiting. Avoid storing submitted links or media beyond an active job's needs.

## Current status

Project definition initialized. The downloader has not yet been implemented or deployed.
