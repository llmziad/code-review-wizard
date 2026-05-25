"""GitHub access — metadata, diff, file fetch, repo clone, review posting.

The split of responsibility here:
- PyGithub for structured metadata reads (cleanly parses the API JSON).
- httpx for raw diff fetch (the API serves it as text, no parsing needed).
- `git` subprocess for cloning (faster, more correct, and skips the API for
  bulk file access — symbol resolution would melt the rate limit otherwise).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import httpx
from github import Auth, Github
from github.PullRequest import PullRequest

from .auth import resolve_token
from .models import PRMetadata, PRSummary

_PR_URL_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)(?:/|/files|/commits)?/?(?:\?.*)?$"
)


class PRURLError(ValueError):
    """Raised when a string isn't a parseable GitHub PR URL."""


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse `https://github.com/owner/repo/pull/123` → (owner, repo, 123)."""
    match = _PR_URL_RE.match(url.strip())
    if not match:
        raise PRURLError(f"not a GitHub PR URL: {url!r}")
    owner, repo, number = match.groups()
    return owner, repo, int(number)


class GitHubClient:
    """Thin wrapper around PyGithub + httpx + git for everything GitHub-shaped."""

    def __init__(self, token: str | None, cache_dir: Path):
        """Construct a client. If `token` is None, resolves via the auth cascade
        (env → gh CLI → stored auth). Raises `AuthMissingError` if no source works.
        """
        if not token:
            token = resolve_token()
        self._token = token
        self._gh = Github(auth=Auth.Token(token))
        self._http = httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "smart-code-reviewer/0.1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        self.cache_dir = cache_dir

    # ---- metadata ----

    def fetch_pr_metadata(self, url: str) -> PRMetadata:
        owner, repo, number = parse_pr_url(url)
        pr = self._gh.get_repo(f"{owner}/{repo}").get_pull(number)
        return PRMetadata(
            owner=owner,
            repo=repo,
            number=number,
            title=pr.title or "",
            description=pr.body or "",
            author=pr.user.login if pr.user else "unknown",
            base_sha=pr.base.sha,
            head_sha=pr.head.sha,
            url=url,
        )

    def get_raw_pr(self, pr: PRMetadata) -> PullRequest:
        """Lower-level PyGithub PR handle. Used by github_poster to create reviews."""
        return self._gh.get_repo(f"{pr.owner}/{pr.repo}").get_pull(pr.number)

    # ---- diff ----

    def fetch_diff(self, pr: PRMetadata) -> str:
        """Fetch the unified diff text for the PR. Authoritative for diff parsing."""
        response = self._http.get(
            f"https://api.github.com/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        response.raise_for_status()
        return response.text

    # ---- repo on disk ----

    def clone_repo(self, pr: PRMetadata, *, refresh: bool = False) -> Path:
        """Shallow-clone the PR's head into the cache. Returns the local path.

        Handles fork PRs by cloning from the head repo's URL (which differs
        from the base repo for cross-fork PRs). Idempotent: if the directory
        exists and `refresh` is False, returns the cached path.
        """
        dest = self.cache_dir / "repos" / f"{pr.owner}__{pr.repo}__{pr.head_sha[:12]}"

        if dest.exists() and not refresh:
            return dest
        if dest.exists() and refresh:
            shutil.rmtree(dest)

        dest.parent.mkdir(parents=True, exist_ok=True)

        raw_pr = self.get_raw_pr(pr)
        head_clone_url = raw_pr.head.repo.clone_url if raw_pr.head.repo else None
        head_ref = raw_pr.head.ref

        if not head_clone_url:
            # Fork was deleted or made private; fall back to base repo and hope.
            head_clone_url = f"https://github.com/{pr.owner}/{pr.repo}.git"

        authed_url = head_clone_url.replace(
            "https://", f"https://x-access-token:{self._token}@"
        )

        # --depth=1 + --branch lands the working tree at HEAD of the PR's head ref
        # in one network round-trip. This is the fast path; if the PR head has
        # since moved, we'd get the new head — acceptable for a take-home.
        subprocess.run(
            [
                "git",
                "clone",
                "--depth=1",
                "--branch",
                head_ref,
                "--quiet",
                authed_url,
                str(dest),
            ],
            check=True,
            capture_output=True,
        )
        return dest

    # ---- listing (the Electron home screen, in CLI form) ----

    def search_prs(self, query_fragment: str, *, max_results: int = 50) -> list[PRSummary]:
        """Search PRs via the issues search API.

        `query_fragment` should be the user-supplied portion, e.g.
        "review-requested:@me". `is:pr is:open` is prepended.
        """
        q = f"is:pr is:open {query_fragment}"
        response = self._http.get(
            "https://api.github.com/search/issues",
            params={"q": q, "per_page": max_results, "sort": "updated", "order": "desc"},
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        return [_summary_from_search_item(item) for item in items]

    def list_review_requested(self, max_results: int = 50) -> list[PRSummary]:
        return self.search_prs("review-requested:@me", max_results=max_results)

    def list_authored(self, max_results: int = 50) -> list[PRSummary]:
        return self.search_prs("author:@me", max_results=max_results)

    def list_assigned(self, max_results: int = 50) -> list[PRSummary]:
        return self.search_prs("assignee:@me", max_results=max_results)

    # ---- single-file fetch ----

    def fetch_file_at_sha(self, pr: PRMetadata, path: str, sha: str | None = None) -> str:
        """Fetch a single file's content from the repo at a specific SHA."""
        sha = sha or pr.head_sha
        response = self._http.get(
            f"https://api.github.com/repos/{pr.owner}/{pr.repo}/contents/{path}",
            params={"ref": sha},
            headers={"Accept": "application/vnd.github.v3.raw"},
        )
        response.raise_for_status()
        return response.text

    def close(self) -> None:
        self._http.close()
        self._gh.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summary_from_search_item(item: dict) -> PRSummary:
    """Convert a search-issues result into a PRSummary.

    Search results have `repository_url` like
    https://api.github.com/repos/{owner}/{repo}, which we split.
    """
    repo_url = item["repository_url"]  # e.g. https://api.github.com/repos/foo/bar
    parts = repo_url.rstrip("/").split("/")
    owner, repo = parts[-2], parts[-1]
    return PRSummary(
        url=item["html_url"],
        owner=owner,
        repo=repo,
        number=item["number"],
        title=item["title"],
        author=(item.get("user") or {}).get("login") or "unknown",
        created_at=datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")),
        updated_at=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
        state=item.get("state", "open"),
        draft=item.get("draft", False),
    )
