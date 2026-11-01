"""Optional local, domain-scoped Instagram session for direct account metadata."""
import http.cookiejar
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request


def instagram_host(host):
    host = (host or '').lower()
    return host == 'instagram.com' or host.endswith('.instagram.com')


class InstagramRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != 'https' or not instagram_host(target.hostname) or target.port not in (None,443):
            raise ValueError('Instagram session requests cannot redirect to another service')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class InstagramSession:
    def __init__(self, cookie_file, proxy_handler):
        path = Path(cookie_file).expanduser()
        if os.name == 'posix' and path.stat().st_mode & 0o077:
            raise ValueError('Instagram cookie file must be private. Run chmod 600 on that local file.')
        jar = http.cookiejar.MozillaCookieJar()
        jar.load(str(path),ignore_discard=True,ignore_expires=False)
        for cookie in list(jar):
            if not instagram_host(cookie.domain.lstrip('.')):
                jar.clear(cookie.domain,cookie.path,cookie.name)
        self.csrf = next((c.value for c in jar if c.name == 'csrftoken'),'')
        self.opener = urllib.request.build_opener(proxy_handler,InstagramRedirect(),urllib.request.HTTPCookieProcessor(jar))

    def get_json(self, url):
        target = urllib.parse.urlsplit(url)
        if target.scheme != 'https' or not instagram_host(target.hostname) or target.port not in (None,443) or target.username or target.password:
            raise ValueError('Instagram session can only be used with Instagram HTTPS endpoints')
        headers = {'User-Agent':'DART/1.0','Accept':'application/json','X-IG-App-ID':'936619743392459'}
        if self.csrf:
            headers['X-CSRFToken'] = self.csrf
        with self.opener.open(urllib.request.Request(url,headers=headers),timeout=25) as response:
            return json.loads(response.read(2*1024*1024))
