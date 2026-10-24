import os

os.environ.setdefault("DAYBOOK_ENV", "test")
os.environ["DAYBOOK_LLM"] = "fake"
os.environ["DAYBOOK_EMBED"] = "fake"
os.environ["DAYBOOK_TTS"] = "fake"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GEMINI_TTS_API_KEY"] = ""
