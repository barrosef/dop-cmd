from __future__ import annotations

import http.server
import threading

import pytest

from dop import probe


@pytest.fixture
def server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code = int(self.path.strip("/") or 200)
            self.send_response(code)
            if 300 <= code < 400:
                self.send_header("Location", "/200")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.mark.parametrize("path, ready", [("/200", True), ("/204", True), ("/302", True), ("/404", False), ("/503", False)])
def test_http_ready(server, path, ready):
    assert probe.http_ready(server + path) is ready


def test_http_ready_nobody_listening():
    assert probe.http_ready("http://127.0.0.1:1", timeout=0.5) is False


def test_wait_until_polls(monkeypatch):
    monkeypatch.setattr(probe.time, "sleep", lambda s: None)
    answers = iter([False, False, True])
    assert probe.wait_until(lambda: next(answers), timeout=60) is True


def test_wait_until_times_out(monkeypatch):
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(probe.time, "sleep", lambda s: None)
    assert probe.wait_until(lambda: False, timeout=10) is False


def test_wait_until_gives_up_when_not_alive(monkeypatch):
    monkeypatch.setattr(probe.time, "sleep", lambda s: None)
    assert probe.wait_until(lambda: pytest.fail("probed a dead process"), timeout=60, alive=lambda: False) is False
