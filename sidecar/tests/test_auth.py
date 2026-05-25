"""Auth module — storage, perms, resolution cascade."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from reviewer import auth
from reviewer.auth import (
    AuthMissingError,
    StoredAuth,
    auth_file_path,
    clear_auth,
    load_auth,
    resolve_token,
    save_auth,
)


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect auth storage into a temp dir for the duration of the test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    # Disable gh CLI fallback by neutralizing the helper
    monkeypatch.setattr(auth, "_try_gh_cli_token", lambda: None)
    return tmp_path


def test_save_and_load_roundtrip(isolated_config: Path) -> None:
    a = StoredAuth(access_token="ghp_x", user_login="alice", user_id=1)
    path = save_auth(a)
    assert path.exists()
    loaded = load_auth()
    assert loaded is not None
    assert loaded.access_token == "ghp_x"
    assert loaded.user_login == "alice"


def test_save_writes_owner_only_perms(isolated_config: Path) -> None:
    save_auth(StoredAuth(access_token="x", user_login="alice"))
    mode = stat.S_IMODE(os.stat(auth_file_path()).st_mode)
    assert mode == 0o600


def test_clear_is_idempotent(isolated_config: Path) -> None:
    save_auth(StoredAuth(access_token="x", user_login="alice"))
    assert clear_auth() is True
    assert clear_auth() is False
    assert load_auth() is None


def test_load_returns_none_for_corrupt_file(isolated_config: Path) -> None:
    auth_file_path().parent.mkdir(parents=True, exist_ok=True)
    auth_file_path().write_text("{this is not json")
    assert load_auth() is None


def test_resolve_token_prefers_env(isolated_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_auth(StoredAuth(access_token="from_file", user_login="alice"))
    monkeypatch.setenv("GITHUB_TOKEN", "from_env")
    assert resolve_token() == "from_env"


def test_resolve_token_falls_back_to_stored(isolated_config: Path) -> None:
    save_auth(StoredAuth(access_token="from_file", user_login="alice"))
    assert resolve_token() == "from_file"


def test_resolve_token_raises_when_nothing_available(isolated_config: Path) -> None:
    with pytest.raises(AuthMissingError, match="No GitHub credentials"):
        resolve_token()


def test_missing_auth_message_prompts_install_when_gh_not_installed(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If gh is missing, the error should tell the user how to install it."""
    monkeypatch.setattr("reviewer.auth.shutil.which", lambda _: None)
    with pytest.raises(AuthMissingError) as exc:
        resolve_token()
    message = str(exc.value)
    assert "brew install gh" in message
    assert "gh auth login" in message


def test_missing_auth_message_prompts_login_when_gh_installed(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If gh is installed but returned no token, point straight at `gh auth login`."""
    monkeypatch.setattr("reviewer.auth.shutil.which", lambda _: "/usr/local/bin/gh")
    # `_try_gh_cli_token` is already neutralized by the fixture → returns None
    with pytest.raises(AuthMissingError) as exc:
        resolve_token()
    message = str(exc.value)
    assert "you're not logged in" in message
    assert "gh auth login" in message
    # Should NOT include the install instructions — gh is already there
    assert "brew install gh" not in message
