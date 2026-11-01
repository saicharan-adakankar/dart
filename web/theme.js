// Apply the saved theme before first paint. Dark is DART's default.
try { document.documentElement.dataset.theme = localStorage.getItem('dart-theme') === 'light' ? 'light' : 'dark'; } catch { /* Storage can be unavailable in private browsing. */ }
