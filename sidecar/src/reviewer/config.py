"""Application settings — single source of truth for env-driven configuration.

Secrets (API keys, tokens) default to empty strings so the package imports
cleanly without env vars set. The clients that need them check at construction
time with a friendly error.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    anthropic_api_key: str = ""
    github_token: str = ""

    anthropic_model: str = "claude-sonnet-4-6"

    min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    max_blocking_comments: int = Field(default=3, ge=0)
    max_suggestion_comments: int = Field(default=5, ge=0)
    max_nit_comments: int = Field(default=3, ge=0)

    cache_dir: Path = Path("./cache")

    def ensure_cache_dirs(self) -> None:
        (self.cache_dir / "repos").mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "runs").mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
