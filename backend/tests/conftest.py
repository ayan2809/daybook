import os

os.environ.setdefault("DAYBOOK_ENV", "test")
os.environ["DAYBOOK_LLM"] = "fake"
os.environ["DAYBOOK_EMBED"] = "fake"
os.environ["DAYBOOK_TTS"] = "fake"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GEMINI_TTS_API_KEY"] = ""

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """The rate limiters are per process; each test starts with full buckets."""
    from daybook.api import deps

    deps._general.state.clear()
    deps._login.state.clear()
    real_demo = deps._demo
    deps._demo = deps.TokenBucket(rate_per_min=600, burst=600)  # tests start many demos
    yield
    deps._demo = real_demo
