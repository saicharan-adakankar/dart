"""Loopback forward proxy. Resolve once and connect only to validated public IPs."""
import ipaddress
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def public_addresses(host, port):
    if port not in (80, 443):
        raise ValueError('Only HTTP and HTTPS ports are supported')
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers or any(not ipaddress.ip_address(a[4][0]).is_global for a in answers):
        raise ValueError('Private and reserved network destinations are blocked')
    return answers


def connect_public(host, port):
    for family, kind, proto, _, address in public_addresses(host, port):
        sock = socket.socket(family, kind, proto)
        sock.settimeout(20)
        try:
            sock.connect(address)
            return sock
        except OSError:
            sock.close()
    raise OSError('Could not connect to source')


class Proxy(BaseHTTPRequestHandler):
    timeout = 25

    def log_message(self, *_):
        pass

    def relay(self, remote):
        peers = [self.connection, remote]
        while True:
            ready, _, _ = select.select(peers, [], [], 25)
            if not ready:
                break
            for source in ready:
                data = source.recv(65536)
                if not data:
                    return
                (remote if source is self.connection else self.connection).sendall(data)

    def do_CONNECT(self):
        try:
            target = urlsplit('//' + self.path)
            with connect_public(target.hostname, target.port or 443) as remote:
                self.send_response(200)
                self.end_headers()
                self.relay(remote)
        except (ValueError, OSError):
            self.close_connection = True
            try:
                self.send_error(403, 'Destination unavailable or blocked')
            except OSError:
                pass

    def do_GET(self):
        try:
            target = urlsplit(self.path)
            if target.scheme != 'http' or target.username or target.password:
                raise ValueError('Unsupported URL')
            with connect_public(target.hostname, target.port or 80) as remote:
                path = (target.path or '/') + ('?' + target.query if target.query else '')
                host = target.hostname
                headers = ''.join(f'{k}: {v}\r\n' for k, v in self.headers.items()
                                  if k.lower() not in ('host', 'connection', 'proxy-connection', 'proxy-authorization'))
                remote.sendall(f'GET {path} HTTP/1.1\r\nHost: {host}\r\n{headers}Connection: close\r\n\r\n'.encode('latin1'))
                self.relay(remote)
        except (ValueError, OSError):
            self.close_connection = True
            try:
                self.send_error(403, 'Destination unavailable or blocked')
            except OSError:
                pass


def start_proxy():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}'
