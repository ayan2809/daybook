"""All configuration, from environment variables (plan section 16). Every model id is a setting.

Locally the root `.env` is read too. In Lambda the secrets come from SSM (see `load_ssm_secrets`).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    daybook_env: Literal["local", "prod", "test"] = "local"
    daybook_blob: Literal["local", "s3"] = "local"
    daybook_bucket: str = ""
    daybook_data_dir: Path = REPO_ROOT / "data" / "local"
    daybook_llm: Literal["fake", "live"] = "fake"
    daybook_tts: Literal["fake", "gemini", "elevenlabs", "off"] = "fake"
    daybook_embed: Literal["fake", "gemini"] = "fake"

    # models (plan 6.1)
    model_chat: str = "gemini-3.5-flash-lite"
    model_router: str = "gemini-3.5-flash-lite"
    model_fast: str = "gemini-3.5-flash-lite"
    model_context: str = "gemini-3.5-flash-lite"
    model_deep: str = "gemini-3.8-flash"
    model_ask: str = "gemini-3.8-flash"
    model_judge_session: str = "gemini-3.5-flash-lite"
    model_judge_offline: str = "gemini-3.8-flash"
    embed_model: str = "gemini-embedding-2"
    embed_dim: int = 768

    # voice
    eleven_model: str = "eleven_flash_v2_5"
    eleven_voice_id: str = "EXAVITQu4vr4xnSDxMaL"  # "Sarah" (DB-26)
    elevenlabs_api_key: str = ""
    gemini_tts_model: str = "gemini-3.8-flash-lite-tts"
    gemini_tts_voice: str = "Sulafat"

    # keys
    gemini_api_key: str = ""
    gemini_tts_api_key: str = ""  # falls back to gemini_api_key

    # sessions and limits
    session_limit_sec: int = 420
    demo_session_limit_sec: int = 180
    abstain_cosine: float = 0.35
    demo_daily_sessions: int = 15
    demo_daily_sandboxes: int = 100  # new demo sandboxes per IST day, all visitors (DB-31)
    demo_daily_asks: int = 30
    demo_sandbox_sessions: int = 2
    demo_sandbox_asks: int = 10
    demo_tts_chars: int = 1000
    session_tts_chars: int = 3000
    session_max_turns: int = 25
    session_max_tokens_in: int = 150_000
    daily_budget_usd: float = 2.00
    demo_budget_usd: float = 1.40
    users_budget_usd: float = 1.00  # all non-owner accounts together, per IST day (DB-29)
    user_daily_budget_usd: float = 0.30  # one non-owner account per day (about two sessions)

    # auth
    owner_key_hash: str = ""  # sha256 hex of the owner key
    session_signing_key: str = "dev-only-signing-key-change-me"
    origin_verify: str = ""  # when set, requests must carry X-Origin-Verify with this value
    # Google sign-in (DB-29). The client id isn't secret. Sign-in is sign-up.
    google_client_id: str = ""
    owner_emails: str = ""  # comma list: these Google accounts open users/owner/ (admin)
    allowed_emails: str = ""  # comma list; empty = anyone with a verified Google email
    max_accounts: int = 25

    # push
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    site_url: str = "http://localhost:5173"

    # misc
    ssm_prefix: str = "/daybook"
    worker_function_name: str = ""
    # EventBridge Scheduler (prod): per-account reminder schedules target the worker (DB-29)
    scheduler_role_arn: str = ""
    worker_function_arn: str = ""
    tz_default: str = "Asia/Kolkata"

    @property
    def is_local(self) -> bool:
        return self.daybook_env in ("local", "test")

    @property
    def tts_key(self) -> str:
        return self.gemini_tts_api_key or self.gemini_api_key


def load_ssm_secrets(prefix: str) -> int:
    """Copy SecureString parameters under `prefix` (e.g. /daybook/gemini_api_key) into the
    environment as GEMINI_API_KEY etc. Values already in the environment win. Prod only."""
    import boto3

    ssm = boto3.client("ssm")
    n = 0
    for page in ssm.get_paginator("get_parameters_by_path").paginate(
        Path=prefix, WithDecryption=True
    ):
        for p in page["Parameters"]:
            os.environ.setdefault(p["Name"].rsplit("/", 1)[1].upper(), p["Value"])
            n += 1
    return n


@lru_cache
def get_settings() -> Settings:
    if os.environ.get("DAYBOOK_ENV") == "prod":
        load_ssm_secrets(os.environ.get("SSM_PREFIX", "/daybook"))
    return Settings()
