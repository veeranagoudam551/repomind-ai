"""Unit tests for app.services.github, mocking the GitHub HTTP call via
httpx.MockTransport - the same technique as the other AI-provider service
tests, though this one needs no config-error handling since it has no API
key requirement (see app/api/repositories.py's `_mock_fetch` for the
higher-level ownership/validation coverage this module's callers get
instead). See docs/architecture.md Day 42 for the connection-failure case.
"""

import httpx
import pytest

from app.services.github import GitHubAPIError, GitHubRepoNotFound, fetch_repository


def _install_mock_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.github.httpx.AsyncClient", FakeAsyncClient)


def _repo_response() -> dict:
    return {
        "full_name": "octocat/Hello-World",
        "html_url": "https://github.com/octocat/Hello-World",
        "description": "My first repository on GitHub!",
        "default_branch": "master",
        "size": 1,
        "private": False,
    }


async def test_fetch_repository_success(monkeypatch):
    def handler(request):
        assert request.url.path == "/repos/octocat/Hello-World"
        return httpx.Response(200, json=_repo_response())

    _install_mock_transport(monkeypatch, handler)

    info = await fetch_repository("octocat", "Hello-World")
    assert info.full_name == "octocat/Hello-World"
    assert info.private is False


async def test_fetch_repository_raises_not_found_on_404(monkeypatch):
    def handler(request):
        return httpx.Response(404, json={"message": "Not Found"})

    _install_mock_transport(monkeypatch, handler)

    with pytest.raises(GitHubRepoNotFound):
        await fetch_repository("owner", "does-not-exist")


async def test_fetch_repository_raises_on_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(403, text='{"message": "rate limited"}')

    _install_mock_transport(monkeypatch, handler)

    with pytest.raises(GitHubAPIError):
        await fetch_repository("owner", "repo")


async def test_fetch_repository_works_without_a_github_token(monkeypatch):
    # Day 52: GITHUB_TOKEN is explicitly optional (public repo access
    # works fine without one, just at a lower rate limit) - confirms both
    # halves of that: the request still succeeds, and no Authorization
    # header is sent at all when unset (not an empty/placeholder one).
    monkeypatch.setattr("app.services.github.settings.github_token", "")

    def handler(request):
        assert "Authorization" not in request.headers
        return httpx.Response(200, json=_repo_response())

    _install_mock_transport(monkeypatch, handler)

    info = await fetch_repository("octocat", "Hello-World")
    assert info.full_name == "octocat/Hello-World"


async def test_fetch_repository_sends_bearer_token_when_configured(monkeypatch):
    monkeypatch.setattr("app.services.github.settings.github_token", "ghp_fake_test_token")

    def handler(request):
        assert request.headers["Authorization"] == "Bearer ghp_fake_test_token"
        return httpx.Response(200, json=_repo_response())

    _install_mock_transport(monkeypatch, handler)

    await fetch_repository("octocat", "Hello-World")


async def test_fetch_repository_raises_on_connection_failure(monkeypatch):
    # Day 42: a fully unreachable GitHub (DNS failure, connection refused,
    # timeout) raises a raw httpx.RequestError with no .status_code to
    # check - this must become a GitHubAPIError, not propagate raw past
    # create_repository's `except GitHubAPIError` in app/api/repositories.py.
    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    _install_mock_transport(monkeypatch, handler)

    with pytest.raises(GitHubAPIError):
        await fetch_repository("owner", "repo")
