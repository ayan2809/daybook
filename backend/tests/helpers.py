from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from daybook.engine.app import App
from daybook.settings import Settings
from daybook.storage.blob import LocalBlobStore
from daybook.voice.tts import FakeTTS

IST = ZoneInfo("Asia/Kolkata")
T0 = datetime(2026, 10, 26, 19, 5, tzinfo=IST)


def make_app(tmp_path, **overrides) -> App:
    s = Settings(
        _env_file=None,
        daybook_env="test",
        daybook_data_dir=tmp_path,
        daybook_llm="fake",
        daybook_embed="fake",
        daybook_tts="fake",
        **overrides,
    )
    return App(settings=s, store=LocalBlobStore(tmp_path / "blobs"), tts=FakeTTS())


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


RK = "AAAQ-EAYE-AUDA-OCAJ-BIFQ-YDIO-B4IB-CEQT"  # test recovery keys (DB-32)
RK2 = "CQKR-MFYY-DENB-WHA5-DYPS-AIJC-EMSC-KJRH"
XRW = {"X-Requested-With": "daybook"}


def open_vault(client, key: str = RK):
    """Set up the signed-in account's recovery key, or unlock with it if it has one."""
    r = client.post("/api/vault/setup", headers=XRW, json={"recovery_key": key})
    if r.status_code == 409:
        r = client.post("/api/vault/unlock", headers=XRW, json={"recovery_key": key})
    assert r.status_code == 200, r.text
    return r
