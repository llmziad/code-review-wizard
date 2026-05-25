"""Authentication — persistent GitHub credentials for the CLI.

Storage model:
    ~/.config/code-review-wizard/auth.json, 0600 perms.
    One file, one identity. Multi-account is a future concern.

Token sources, in cascade order (see `resolve_token`):
    1. Explicit `GITHUB_TOKEN` env var — for CI and power users.
    2. `gh auth token` subprocess — zero-friction for anyone with the gh CLI.
    3. Stored auth file — the persistent path the GUI/CLI shares.
    4. None — caller raises with a friendly "run `reviewer login`" message.

Login surfaces:
    - PAT paste (`reviewer login --token <PAT>`) — works today, no infra.
    - OAuth device flow — scaffolded, gated on a `CRW_OAUTH_CLIENT_ID` env var
      pointing at a registered GitHub OAuth App. Documented in README.

The Electron app will eventually move token storage to the OS keychain (via
Electron's safeStorage API). Until then, file with strict perms is fine; the
`TokenStore` abstraction keeps the migration local.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class StoredAuth(BaseModel):
    """Persistent record of a logged-in identity."""

    access_token: str
    token_type: str = Field(default="PAT", description="`PAT` or `OAuth`")
    scope: str | None = None
    user_login: str
    user_id: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def auth_file_path() -> Path:
    """Cross-platform config dir. XDG-respecting on Linux, ~/.config elsewhere."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "code-review-wizard" / "auth.json"


def save_auth(auth: StoredAuth) -> Path:
    path = auth_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(auth.model_dump_json(indent=2))
    # 0600 — owner-readable only. Defends against shared-machine snooping.
    os.chmod(path, 0o600)
    return path


def load_auth() -> StoredAuth | None:
    path = auth_file_path()
    if not path.exists():
        return None
    try:
        return StoredAuth.model_validate_json(path.read_text())
    except Exception:
        # Corrupt file — return None so the caller can re-prompt login.
        return None


def clear_auth() -> bool:
    """Delete the stored auth file. Returns True if anything was deleted."""
    path = auth_file_path()
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Token resolution cascade
# ---------------------------------------------------------------------------


class AuthMissingError(RuntimeError):
    """Raised when no token source is available. CLI catches and instructs."""


def resolve_token() -> str:
    """Walk env → gh → stored → raise. Returns the first usable token.

    Invoked by GitHubClient construction when no explicit token is passed.
    The raised error is tailored to what's actually missing — the install
    instruction matters more than a generic "credentials not found".
    """
    env = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env:
        return env

    gh_token = _try_gh_cli_token()
    if gh_token:
        return gh_token

    stored = load_auth()
    if stored:
        return stored.access_token

    raise AuthMissingError(_missing_auth_message())


def _missing_auth_message() -> str:
    """Headline + body. Branches on whether `gh` is installed.

    Designed for rich rendering by `cli._fail`: first line is the headline,
    the rest is detail. Plain text — markup happens in the renderer.
    """
    gh_installed = shutil.which("gh") is not None

    if not gh_installed:
        return (
            "No GitHub credentials found.\n"
            "\n"
            "The easiest setup is the GitHub CLI:\n"
            "  brew install gh                  (macOS)\n"
            "  winget install GitHub.cli        (Windows)\n"
            "  # other platforms: https://cli.github.com\n"
            "  gh auth login\n"
            "\n"
            "Then re-run this command — no other setup needed.\n"
            "\n"
            "Alternatives if you'd rather not install gh:\n"
            "  reviewer login --token <PAT>     (paste a personal access token)\n"
            "  export GITHUB_TOKEN=<token>      (env var, useful in CI)"
        )

    # gh is installed but didn't return a token → user hasn't logged in
    return (
        "No GitHub credentials found.\n"
        "\n"
        "You have the GitHub CLI installed but you're not logged in. Run:\n"
        "  gh auth login\n"
        "\n"
        "Then re-run this command — we'll pick up the token automatically.\n"
        "\n"
        "Alternatives:\n"
        "  reviewer login --token <PAT>\n"
        "  export GITHUB_TOKEN=<token>"
    )


def _try_gh_cli_token() -> str | None:
    """Best-effort `gh auth token`. Returns None if gh isn't installed or isn't logged in."""
    if not shutil.which("gh"):
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


# ---------------------------------------------------------------------------
# Login surfaces
# ---------------------------------------------------------------------------


_GITHUB_USER_ENDPOINT = "https://api.github.com/user"


def fetch_github_user(token: str) -> dict:
    """GET /user. Validates the token and returns the GitHub user record."""
    response = httpx.get(
        _GITHUB_USER_ENDPOINT,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "code-review-wizard/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def login_with_pat(token: str) -> StoredAuth:
    """Validate the token, fetch the user, persist. Returns the saved record."""
    token = token.strip()
    if not token:
        raise ValueError("Empty token.")
    user = fetch_github_user(token)  # raises on invalid token

    # Token-type detection from prefix is cheap and informative.
    # gho_ = OAuth user-to-server, ghp_ = PAT, ghs_ = server-to-server.
    if token.startswith("gho_"):
        token_type = "OAuth"
    elif token.startswith("ghs_"):
        token_type = "InstallationToken"
    else:
        token_type = "PAT"

    auth = StoredAuth(
        access_token=token,
        token_type=token_type,
        user_login=user["login"],
        user_id=user.get("id"),
    )
    save_auth(auth)
    return auth


# ---------------------------------------------------------------------------
# OAuth Device Flow
# ---------------------------------------------------------------------------
#
# Bundled client_id for the official "Code Review Wizard" GitHub OAuth App.
# This is intentionally public — OAuth Apps' client_id is not a secret, and
# the Device Flow doesn't use a client_secret. Override with the
# CRW_OAUTH_CLIENT_ID env var if you want to point at your own OAuth App
# (useful for forks or for internal deployments).

_DEFAULT_OAUTH_CLIENT_ID = "Ov23lior5HyqZnLLHhQT"
_OAUTH_CLIENT_ID_ENV = "CRW_OAUTH_CLIENT_ID"
_DEVICE_CODE_URL = "https://github.com/login/device/code"
_DEVICE_TOKEN_URL = "https://github.com/login/oauth/access_token"
# `repo` covers private repos too; `read:user` is for whoami.
_DEFAULT_SCOPES = "repo read:user"


class DeviceFlowChallenge(BaseModel):
    """What we show the user during device-flow login."""

    user_code: str
    verification_uri: str
    device_code: str
    expires_in: int
    interval: int  # seconds between polls


class DeviceFlowUnavailable(RuntimeError):
    """Raised when the OAuth App's client_id cannot be resolved."""


def oauth_client_id() -> str:
    """Override-aware. Env var wins; bundled default is the fallback."""
    return (os.environ.get(_OAUTH_CLIENT_ID_ENV) or _DEFAULT_OAUTH_CLIENT_ID).strip()


def device_flow_start(scopes: str = _DEFAULT_SCOPES) -> DeviceFlowChallenge:
    """Step 1 of the device flow — request a user_code + device_code."""
    response = httpx.post(
        _DEVICE_CODE_URL,
        data={"client_id": oauth_client_id(), "scope": scopes},
        headers={"Accept": "application/json"},
        timeout=10.0,
    )
    response.raise_for_status()
    payload = response.json()
    return DeviceFlowChallenge(
        user_code=payload["user_code"],
        verification_uri=payload["verification_uri"],
        device_code=payload["device_code"],
        expires_in=payload["expires_in"],
        interval=payload.get("interval", 5),
    )


def device_flow_poll(
    challenge: DeviceFlowChallenge,
    on_poll: Callable[[int], None] | None = None,
) -> StoredAuth:
    """Step 2 — poll until the user completes the flow, then save.

    `on_poll(seconds_remaining)` is called before each sleep — useful for
    CLI spinners and sidecar progress events. The callback should not block.
    """
    deadline = time.time() + challenge.expires_in
    interval = challenge.interval

    while time.time() < deadline:
        response = httpx.post(
            _DEVICE_TOKEN_URL,
            data={
                "client_id": oauth_client_id(),
                "device_code": challenge.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()

        if "access_token" in payload:
            return login_with_pat(payload["access_token"])  # validates + persists

        error = payload.get("error")
        if error == "authorization_pending":
            if on_poll:
                on_poll(int(deadline - time.time()))
            time.sleep(interval)
            continue
        if error == "slow_down":
            interval += 5
            if on_poll:
                on_poll(int(deadline - time.time()))
            time.sleep(interval)
            continue
        raise RuntimeError(f"Device flow failed: {payload}")

    raise TimeoutError("Device flow expired before authorization completed.")
