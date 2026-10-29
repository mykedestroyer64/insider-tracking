"""web.py - stand-in sites: a local web server answering like the real site would."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StandInHandler(BaseHTTPRequestHandler):
    """A request handler that doesn't print a line per request, with a helper to answer."""

    def reply(self, code, body, content_type="text/html", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(handler_class):
    """Start a local web server with this handler, in the background; returns its address
    ("http://127.0.0.1:<port>")."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"
