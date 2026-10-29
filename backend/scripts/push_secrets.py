"""`make secrets`: copy keys from the root .env into SSM Parameter Store (plan 12.1).

SecureStrings under /daybook/, read by the Lambdas at cold start (settings.load_ssm_secrets).
Values are never printed. A parameter is rewritten only when its value changed. The two
server-only secrets (session signing key, CloudFront origin header) are generated in SSM when
missing and are never copied from the local .env, so local and prod keys stay different.
"""

from __future__ import annotations

import secrets
import sys

import boto3

from daybook.settings import REPO_ROOT

PREFIX = "/daybook"
FROM_ENV = [
    "GEMINI_API_KEY",
    "GEMINI_TTS_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVEN_VOICE_ID",
    "OWNER_KEY_HASH",
    "VAPID_PUBLIC_KEY",
    "VAPID_PRIVATE_KEY",
]
GENERATED = ["SESSION_SIGNING_KEY", "ORIGIN_VERIFY"]
REQUIRED = ["GEMINI_API_KEY", "ELEVENLABS_API_KEY"]


def read_env() -> dict[str, str]:
    env = REPO_ROOT / ".env"
    out = {}
    for line in env.read_text().splitlines() if env.exists() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    ssm = boto3.client("ssm", region_name="ap-south-1")
    env = read_env()
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        print(f"missing in .env: {', '.join(missing)}")
        return 1

    def current(name: str) -> str | None:
        try:
            return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
        except ssm.exceptions.ParameterNotFound:
            return None

    def put(name: str, value: str) -> None:
        ssm.put_parameter(Name=name, Value=value, Type="SecureString", Overwrite=True)

    for key in FROM_ENV:
        name = f"{PREFIX}/{key.lower()}"
        value = env.get(key, "")
        if not value:
            print(f"  skip       {name} (not in .env)")
        elif current(name) == value:
            print(f"  unchanged  {name}")
        else:
            put(name, value)
            print(f"  set        {name}")
    for key in GENERATED:
        name = f"{PREFIX}/{key.lower()}"
        if current(name) is None:
            put(name, secrets.token_urlsafe(48))
            print(f"  generated  {name}")
        else:
            print(f"  kept       {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
