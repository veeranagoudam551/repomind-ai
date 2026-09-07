from app.services.github import GitHubRepoInfo


def make_repo_info(**overrides) -> GitHubRepoInfo:
    defaults = dict(
        full_name="octocat/Hello-World",
        html_url="https://github.com/octocat/Hello-World",
        description="Test repo",
        default_branch="main",
        size_kb=10,
        private=False,
    )
    defaults.update(overrides)
    return GitHubRepoInfo(**defaults)
