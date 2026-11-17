#!/usr/bin/env python3
"""Local server for the leaderboard.

  python3 server.py              serve http://127.0.0.1:8787 and keep data fresh in the background
  python3 server.py --open       ...and open it in your browser
  python3 server.py --no-schedule  serve whatever is in data/latest.json without refreshing

Sources refresh on their own intervals (config.json -> refresh.intervals_minutes); edits to
config.json are picked up on the next cycle (~1 minute). Binds to localhost only.
"""

import argparse
import json
import logging
import mimetypes
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from leaderboard import DATA_DIR, LATEST, ROOT
from leaderboard import weights as weights_mod
from leaderboard.build import build_and_write
from leaderboard.scheduler import Scheduler
from leaderboard.store import load_config, load_status
from leaderboard.util import read_json

WEB = ROOT / "web"
MAX_BODY = 64 * 1024
mimetypes.add_type("application/javascript", ".js")

_rebuild_timer = None
_rebuild_lock = threading.Lock()


def rebuild_soon(delay=1.0):
    """Rebuild data/latest.json shortly after the last weights save (a burst of slider moves = one build)."""
    global _rebuild_timer

    def run():
        try:
            build_and_write(load_config())
        except Exception:  # noqa: BLE001 - a failed rebuild must never take the server down
            logging.getLogger("server").exception("rebuild after weights save failed")

    with _rebuild_lock:
        if _rebuild_timer:
            _rebuild_timer.cancel()
        _rebuild_timer = threading.Timer(delay, run)
        _rebuild_timer.daemon = True
        _rebuild_timer.start()


class Handler(SimpleHTTPRequestHandler):
    scheduler = None
    allowed_hosts = set()

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    # -- plumbing ------------------------------------------------------------------
    def log_message(self, fmt, *args):
        logging.getLogger("server").debug(fmt, *args)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        # blocks DNS-rebinding and cross-site POSTs: only our own localhost origin may talk to the API
        if (self.headers.get("Host") or "").lower() not in self.allowed_hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc.lower() in self.allowed_hosts

    # -- routes --------------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if not self._host_ok():
            return self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
        if path == "/data/latest.json":
            if not LATEST.exists():
                return self._json({"error": "no data yet"}, HTTPStatus.NOT_FOUND)
            body = LATEST.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/status":
            return self._json(self._status())
        if path == "/api/weights/version":  # tiny, polled every couple of seconds so the page can report any change
            return self._json({"version": weights_mod.version()})
        if path == "/api/weights":
            return self._json(weights_mod.snapshot(load_config()))
        return super().do_GET()

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Requested-With") != "leaderboard":
            return self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
        if urlparse(self.path).path == "/api/refresh":
            if self.scheduler is None:
                return self._json({"error": "scheduler disabled (--no-schedule)"}, HTTPStatus.CONFLICT)
            self.scheduler.trigger(force=True)
            return self._json({"queued": True}, HTTPStatus.ACCEPTED)
        if urlparse(self.path).path == "/api/weights":
            return self._save_weights()
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _save_weights(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            return self._json({"error": "body must be 1 byte to 64 KB"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length > MAX_BODY else HTTPStatus.BAD_REQUEST)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict) or "aliases" not in body:
                raise ValueError('expected {"aliases": {...}}')
            saved = weights_mod.save(load_config(), body["aliases"], body.get("caps"))  # caps optional: omitted keeps them
        except (ValueError, UnicodeDecodeError) as exc:  # includes json.JSONDecodeError
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        rebuild_soon()
        return self._json(saved)

    def _status(self):
        gen = None
        if LATEST.exists():
            gen = LATEST.stat().st_mtime
        st = self.scheduler.state if self.scheduler else {}
        return {
            "generated_ts": gen,
            "now": time.time(),
            "running": st.get("running", False),
            "step": st.get("step"),
            "last_error": st.get("last_error"),
            "scheduler": self.scheduler is not None,
            "sources": load_status(),
        }


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=cfg["server"]["host"])
    ap.add_argument("--port", type=int, default=cfg["server"]["port"])
    ap.add_argument("--open", action="store_true", help="open the page in a browser")
    ap.add_argument("--no-schedule", action="store_true", help="do not refresh data in the background")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    DATA_DIR.mkdir(exist_ok=True)

    local_ip = None
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    hosts = ["localhost", "127.0.0.1", "[::1]", args.host]
    if local_ip:
        hosts.append(local_ip)
    Handler.allowed_hosts = {f"{h}:{args.port}" for h in hosts}
    if not args.no_schedule:
        Handler.scheduler = Scheduler()
        Handler.scheduler.start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    logging.info("Leaderboard on %s  (data: %s)%s", url, LATEST, "" if Handler.scheduler else "  [refresh disabled]")
    if not LATEST.exists() and Handler.scheduler:
        logging.info("First run: collecting data (about 20 seconds); the page fills in when it is ready.")
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        logging.info("bye")


if __name__ == "__main__":
    main()
