from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import unittest
from urllib.request import Request, urlopen
from proxy import forward
from router import Backend, Router, RoutingError
from server import make_server


@contextmanager
def serving(service):
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield service.server_port
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=2)


def checksum_receiver(status=200):
    """A real HTTP checksum service for transport tests, not a model emulator."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, raw, self.headers.get("Authorization")))
            body = hashlib.sha256(raw).hexdigest().encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler), received


def payload(text="hello world"):
    return {"model": "configured-by-user", "messages": [{"role": "user", "content": text}], "stream": False}


class Baseline(unittest.TestCase):
    def test_round_robin(self):
        router = Router([Backend("a", "http://localhost/v1"), Backend("b", "http://localhost/v1")])
        ids = []
        for _ in range(3):
            backend, _ = router.reserve(("user", "hello"))
            ids.append(backend.id)
            router.release(backend, (), False)
        self.assertEqual(ids, ["a", "b", "a"])

    def test_unavailable(self):
        router = Router([Backend("a", "http://localhost/v1", healthy=False)])
        with self.assertRaises(RoutingError) as caught:
            router.reserve(("user", "hello"))
        self.assertEqual(caught.exception.status, 503)

    def test_real_http_forward(self):
        service, received = checksum_receiver()
        with serving(service) as port:
            router = Router([Backend("a", f"http://127.0.0.1:{port}/v1")])
            status, body, headers = forward(router, payload())
        self.assertEqual(status, 200)
        self.assertEqual(received[0][0], "/v1/chat/completions")
        self.assertEqual(json.loads(received[0][1]), payload())
        self.assertEqual(body, hashlib.sha256(received[0][1]).hexdigest().encode())
        self.assertEqual(headers["Content-Type"], "text/plain")
        self.assertEqual(router.backends[0].in_flight, 0)
        self.assertEqual(len(router.backends[0].prefixes), 1)

    def test_upstream_error_preserved(self):
        service, _ = checksum_receiver(429)
        with serving(service) as port:
            router = Router([Backend("a", f"http://127.0.0.1:{port}/v1")])
            status, body, _ = forward(router, payload())
        self.assertEqual(status, 429)
        self.assertEqual(len(body), 64)
        self.assertEqual(router.backends[0].prefixes, [])
        self.assertEqual(router.backends[0].in_flight, 0)

    def test_auth_header_uses_configured_secret(self):
        service, received = checksum_receiver()
        name = "BENCHMARK_TRANSPORT_TEST_SECRET"
        previous = os.environ.get(name)
        os.environ[name] = "local-transport-test-value"
        try:
            with serving(service) as port:
                router = Router([Backend("a", f"http://127.0.0.1:{port}/v1", key_env=name)])
                forward(router, payload())
            self.assertEqual(received[0][2], "Bearer local-transport-test-value")
            self.assertNotIn("local-transport-test-value", str(router.snapshot()))
        finally:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

    def test_full_proxy_path(self):
        upstream, received = checksum_receiver()
        with serving(upstream) as up_port:
            router = Router([Backend("a", f"http://127.0.0.1:{up_port}/v1")])
            with serving(make_server(router, 0)) as proxy_port:
                request = Request(f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                                  json.dumps(payload()).encode(), {"Content-Type": "application/json"})
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(response.headers["X-Backend-Id"], "a")
                    self.assertEqual(response.read(), hashlib.sha256(received[0][1]).hexdigest().encode())


if __name__ == "__main__":
    unittest.main()
