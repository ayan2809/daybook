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
