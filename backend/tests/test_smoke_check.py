"""Tests for scripts/smoke_check.py (Day 50).

Loaded by file path rather than as an installed package - it's a small,
standalone operator tool living at the project root (not part of the
`app` package), meant to run against any already-running deployment
with nothing but a stdlib Python interpreter. Exercised here against a
local `http.server` thread returning crafted responses, so these tests
need no real deployment, Docker, or network access.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "smoke_check.py"
_spec = importlib.util.spec_from_file_location("smoke_check", _SCRIPT_PATH)
smoke_check = importlib.util.module_from_spec(_spec)
# Registered in sys.modules before exec: dataclasses (Python 3.9, with
# `from __future__ import annotations` in smoke_check.py making every
# field annotation a string) resolves them by looking the module back up
# in sys.modules - a module never registered there fails that lookup.
sys.modules["smoke_check"] = smoke_check
_spec.loader.exec_module(smoke_check)


class _FakeHandler(BaseHTTPRequestHandler):
    responses: dict = {}

    def do_GET(self):
        status, body = self.responses.get(self.path, (404, b"not found"))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - matches BaseHTTPRequestHandler's signature
        pass  # keep test output quiet


@pytest.fixture
def fake_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join()


def _url(server: HTTPServer) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}"


def test_check_health_passes_on_ok_status(fake_server):
    _FakeHandler.responses = {
        "/health": (200, json.dumps({"status": "ok", "environment": "production"}).encode())
    }
    result = smoke_check.check_health(_url(fake_server), timeout=2)
    assert result.ok
    assert "environment=production" in result.detail


def test_check_health_fails_on_unexpected_body(fake_server):
    _FakeHandler.responses = {"/health": (200, json.dumps({"status": "degraded"}).encode())}
    result = smoke_check.check_health(_url(fake_server), timeout=2)
    assert not result.ok


def test_check_readiness_passes_when_ready(fake_server):
    _FakeHandler.responses = {"/health/ready": (200, json.dumps({"status": "ready"}).encode())}
    result = smoke_check.check_readiness(_url(fake_server), timeout=2)
    assert result.ok


def test_check_readiness_fails_with_reason_on_503(fake_server):
    _FakeHandler.responses = {
        "/health/ready": (503, json.dumps({"status": "not ready", "detail": "database unreachable"}).encode())
    }
    result = smoke_check.check_readiness(_url(fake_server), timeout=2)
    assert not result.ok
    assert "database unreachable" in result.detail


def test_check_frontend_passes_on_200(fake_server):
    _FakeHandler.responses = {"/": (200, b"<html></html>")}
    result = smoke_check.check_frontend(_url(fake_server), timeout=2)
    assert result.ok


def test_check_frontend_fails_on_server_error(fake_server):
    _FakeHandler.responses = {"/": (503, b"")}
    result = smoke_check.check_frontend(_url(fake_server), timeout=2)
    assert not result.ok


def test_checks_report_unreachable_when_nothing_is_listening():
    # An address nothing listens on, rather than a hostname that might
    # trigger a slow DNS lookup - keeps the test itself fast.
    result = smoke_check.check_health("http://127.0.0.1:1", timeout=1)
    assert not result.ok
    assert "unreachable" in result.detail


def test_run_checks_returns_three_passing_results(fake_server):
    _FakeHandler.responses = {
        "/": (200, b"ok"),
        "/health": (200, json.dumps({"status": "ok", "environment": "development"}).encode()),
        "/health/ready": (200, json.dumps({"status": "ready"}).encode()),
    }
    results = smoke_check.run_checks(_url(fake_server), _url(fake_server), timeout=2)
    assert len(results) == 3
    assert all(result.ok for result in results)


def test_main_returns_nonzero_exit_code_on_failure(fake_server, capsys):
    _FakeHandler.responses = {}  # everything 404s
    exit_code = smoke_check.main(
        ["--frontend-url", _url(fake_server), "--api-url", _url(fake_server), "--timeout", "2"]
    )
    assert exit_code == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_returns_zero_exit_code_on_success(fake_server, capsys):
    _FakeHandler.responses = {
        "/": (200, b"ok"),
        "/health": (200, json.dumps({"status": "ok", "environment": "development"}).encode()),
        "/health/ready": (200, json.dumps({"status": "ready"}).encode()),
    }
    exit_code = smoke_check.main(
        ["--frontend-url", _url(fake_server), "--api-url", _url(fake_server), "--timeout", "2"]
    )
    assert exit_code == 0
    assert "FAIL" not in capsys.readouterr().out
