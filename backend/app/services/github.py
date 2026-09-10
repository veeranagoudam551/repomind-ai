import re
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import settings

GITHUB_API_BASE = "https://api.github.com"

# Matches "owner/repo", "github.com/owner/repo", "https://github.com/owner/repo",
# and any of those with a trailing ".git" or "/".
_GITHUB_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:github\.com/)?"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
    r"(?P<repo>[A-Za-z0-9._-]+?)"
    r"(?:\.git)?/?$"
)


class InvalidGitHubUrl(ValueError):
    pass


class GitHubRepoNotFound(Exception):
    pass


class GitHubAPIError(Exception):
    pass


@dataclass
class GitHubRepoInfo:
    full_name: str
    html_url: str
    description: Optional[str]
    default_branch: str
    size_kb: int
    private: bool


def parse_github_url(url: str) -> tuple[str, str]:
    match = _GITHUB_URL_RE.match(url.strip())
    if not match:
        raise InvalidGitHubUrl(f"'{url}' is not a valid GitHub repository URL")
    return match.group("owner"), match.group("repo")


async def fetch_repository(owner: str, repo: str) -> GitHubRepoInfo:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RepoMind-AI",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}", headers=headers)
    except httpx.RequestError as exc:
        raise GitHubAPIError(f"Could not reach GitHub: {exc}") from exc

    if response.status_code == 404:
        raise GitHubRepoNotFound(f"Repository '{owner}/{repo}' was not found on GitHub")
    if response.status_code != 200:
        raise GitHubAPIError(
            f"GitHub API returned {response.status_code} for '{owner}/{repo}': {response.text}"
        )

    data = response.json()
    return GitHubRepoInfo(
        full_name=data["full_name"],
        html_url=data["html_url"],
        description=data.get("description"),
        default_branch=data["default_branch"],
        size_kb=data["size"],
        private=data["private"],
    )
