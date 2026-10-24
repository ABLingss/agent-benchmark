import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from proxy import forward, load_router
from router import RoutingError


def make_server(router, port=8761, timeout=30):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_body(self, status, body, headers=None):
            self.send_response(status)
            for key, value in (headers or {"Content-Type": "application/json"}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def error(self, status, code):
            self.send_body(status, json.dumps({"error": code}).encode())

        def do_GET(self):
            if self.path == "/health":
                self.send_body(200, b'{"ok":true}')
            elif self.path == "/metrics":
                self.send_body(200, json.dumps(router.snapshot()).encode())
            else:
                self.error(404, "not_found")

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.error(404, "not_found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100000:
                    raise ValueError()
                payload = json.loads(self.rfile.read(length))
                result = forward(router, payload, self.headers.get("X-Routing-Policy", "round_robin"), timeout)
                self.send_body(*result)
            except (ValueError, UnicodeError):
                self.error(400, "invalid_json")
            except RoutingError as exc:
                self.error(exc.status, exc.code)

    service = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    service.daemon_threads = True
    return service


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=8761)
    args = parser.parse_args()
    with make_server(load_router(args.config), args.port) as service:
        print(f"Gateway: http://127.0.0.1:{service.server_port}/v1/chat/completions", flush=True)
        try:
            service.serve_forever()
        except KeyboardInterrupt:
            pass
