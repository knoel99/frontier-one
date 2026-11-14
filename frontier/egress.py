"""Outbound filter for a mission run without a sandbox: an HTTP proxy that refuses the hosts of `[network] deny` and
passes everything else on, to the upstream proxy of the machine when there is one, else directly.

The harness gets it as HTTPS_PROXY / HTTP_PROXY. Every host a team reaches, and every refusal, is written to the
mission's events.jsonl (`egress` events), for the dashboard and the audit after the race.

Limits, stated in MISSION.md: it filters by host name (the CONNECT target or the URL's host), not by path inside an
encrypted connection, and a process that drops the proxy variables bypasses it. A real block needs a sandbox
(frontier/sandbox.py). Searches run by a model provider on its own servers never pass through any of them.
"""

from __future__ import annotations

import os
import pathlib
import select
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .budget import append_chained, now


def denied(host: str, deny: list[str]) -> bool:
    """`deny` holds exact host names and `*.suffix` patterns (the suffix itself included)."""
    host = host.lower().rstrip(".")
    for d in deny:
        d = d.lower().rstrip(".")
        if host == d or (d.startswith("*.") and (host == d[2:] or host.endswith(d[1:]))):
            return True
    return False


class _Handler(BaseHTTPRequestHandler):
    server: Egress

    def log_message(self, *a):
        pass

    def _log(self, host: str, allowed: bool) -> None:
        key = (host, allowed)
        with self.server.lock:
            first = key not in self.server.seen
            self.server.seen.add(key)
        if first or not allowed:  # each allowed host once, every refusal
            append_chained(self.server.state / "events.jsonl", {
                "t": now().isoformat(timespec="seconds"), "event": "egress", "host": host, "allowed": allowed})

    def _refuse(self, host: str) -> None:
        self._log(host, False)
        self.send_error(403, f"{host} is blocked for this mission")

    def _upstream(self, host: str, port: int) -> socket.socket:
        up = self.server.upstream
        if not up:
            return socket.create_connection((host, port), timeout=30)
        s = socket.create_connection((up.hostname, up.port or 80), timeout=30)
        s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
        status = resp.split(b"\r\n", 1)[0]
        if b" 200" not in status:
            s.close()
            raise OSError(f"upstream proxy refused {host}:{port}: {status.decode(errors='replace')}")
        return s

    def do_CONNECT(self):  # noqa: N802
        host, _, port = self.path.rpartition(":")
        if denied(host, self.server.deny):
            return self._refuse(host)
        try:
            remote = self._upstream(host, int(port or 443))
        except OSError as e:
            return self.send_error(502, str(e)[:200])
        self._log(host, True)
        self.send_response(200, "Connection established")
        self.end_headers()
        _relay(self.connection, remote)

    def _plain(self):  # an absolute-URI HTTP request: forwarded through a tunnel to its host
        u = urllib.parse.urlsplit(self.path)
        host = u.hostname or ""
        if denied(host, self.server.deny):
            return self._refuse(host)
        try:
            remote = self._upstream(host, u.port or 80)
        except OSError as e:
            return self.send_error(502, str(e)[:200])
        self._log(host, True)
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        head = f"{self.command} {path} {self.request_version}\r\n" + "".join(
            f"{k}: {v}\r\n" for k, v in self.headers.items() if k.lower() not in ("proxy-connection", "proxy-authorization"))
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""  # already buffered by the server: send it with the head
        remote.sendall(head.encode() + b"\r\n" + body)
        _relay(self.connection, remote)
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_PATCH = do_OPTIONS = _plain  # noqa: N815


def _relay(a: socket.socket, b: socket.socket) -> None:
    socks = [a, b]
    try:
        while True:
            ready, _, err = select.select(socks, [], socks, 300)
            if err or not ready:
                return
            for s in ready:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return
    finally:
        b.close()


class Egress(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state, deny: list[str], upstream: str | None = None, port: int = 0):
        super().__init__(("127.0.0.1", port), _Handler)
        self.state, self.deny = pathlib.Path(state), list(deny)
        self.upstream = urllib.parse.urlsplit(upstream) if upstream else None
        self.lock, self.seen = threading.Lock(), set()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


def start(state, deny: list[str]) -> Egress:
    """The filter for one mission, chained to the machine's own proxy when there is one."""
    up = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or None
    e = Egress(state, deny, up)
    threading.Thread(target=e.serve_forever, daemon=True).start()
    return e
