#!/usr/bin/env python3
"""Post-deployment smoke check (Day 50).

Non-destructive, GET-only checks against a running deployment: the
frontend's own URL, and the backend's /health (liveness) and
/health/ready (readiness). Stdlib only (urllib) - same "no extra
dependency just for an HTTP probe" reasoning as the Docker healthchecks
themselves (docker/docker-compose.yml) - so it runs from a bare Python
install with no `pip install` and no Docker Desktop, on the full
Compose stack, a native/WSL deployment, or a future cloud one alike.

Usage:
    python scripts/smoke_check.py
    python scripts/smoke_check.py --frontend-url http://localhost:3000 --api-url http://localhost:8000
    python scripts/smoke_check.py --api-url https://api.example.com --frontend-url https://app.example.com --timeout 10

Exit code 0 if every check passes, 1 otherwise. Prints one line per
check either way, so a failure says which service and why.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _get(url: str, timeout: float) -> "tuple[int, bytes]":
    request = urllib.request.Request(url, headers={"User-Agent": "repomind-smoke-check"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed http(s) URLs only
        return response.status, response.read()


def _get_allow_error_status(url: str, timeout: float) -> "tuple[Optional[int], bytes, Optional[str]]":
    """Like `_get`, but a non-2xx response is returned rather than
    raised - /health/ready's documented 503 is a valid, well-formed
    response, not a connection failure, and callers need to inspect its
    body. Returns (status, body, connection_error)."""
    try:
        status, body = _get(url, timeout)
        return status, body, None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), None
    except urllib.error.URLError as exc:
        return None, b"", str(exc.reason)


def check_frontend(base_url: str, timeout: float) -> CheckResult:
    status, _body, error = _get_allow_error_status(base_url, timeout)
    if error is not None:
        return CheckResult("frontend", False, f"unreachable: {error}")
    if status is None or status >= 500:
        return CheckResult("frontend", False, f"HTTP {status}")
    return CheckResult("frontend", True, f"HTTP {status}")


def check_health(api_base_url: str, timeout: float) -> CheckResult:
    url = api_base_url.rstrip("/") + "/health"
    status, body, error = _get_allow_error_status(url, timeout)
    if error is not None:
        return CheckResult("api /health", False, f"unreachable: {error}")
    if status != 200:
        return CheckResult("api /health", False, f"HTTP {status}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return CheckResult("api /health", False, "response was not valid JSON")
    if payload.get("status") != "ok":
        return CheckResult("api /health", False, f"unexpected body: {payload}")
    return CheckResult(
        "api /health", True, f"status={payload.get('status')} environment={payload.get('environment')}"
    )


def check_readiness(api_base_url: str, timeout: float) -> CheckResult:
    url = api_base_url.rstrip("/") + "/health/ready"
    status, body, error = _get_allow_error_status(url, timeout)
    if error is not None:
        return CheckResult("api /health/ready", False, f"unreachable: {error}")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return CheckResult("api /health/ready", False, f"HTTP {status}, response was not valid JSON")

    if status == 200 and payload.get("status") == "ready":
        return CheckResult("api /health/ready", True, "status=ready")
    return CheckResult("api /health/ready", False, f"HTTP {status}: {payload.get('detail', payload)}")


def run_checks(frontend_url: str, api_url: str, timeout: float) -> "list[CheckResult]":
    return [
        check_frontend(frontend_url, timeout),
        check_health(api_url, timeout),
        check_readiness(api_url, timeout),
    ]


def main(argv: "Optional[list[str]]" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--frontend-url", default="http://localhost:3000", help="Frontend base URL (default: %(default)s)"
    )
    parser.add_argument("--api-url", default="http://localhost:8000", help="API base URL (default: %(default)s)")
    parser.add_argument(
        "--timeout", type=float, default=5.0, help="Per-request timeout in seconds (default: %(default)s)"
    )
    args = parser.parse_args(argv)

    results = run_checks(args.frontend_url, args.api_url, args.timeout)

    all_ok = True
    for result in results:
        marker = "OK  " if result.ok else "FAIL"
        print(f"[{marker}] {result.name}: {result.detail}")
        all_ok = all_ok and result.ok

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
