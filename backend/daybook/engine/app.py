"""The process-wide container: settings, storage, vaults, LLM, embedder, TTS, budgets, jobs.

Locally, async jobs (finalize, judge) run in-process as asyncio tasks (DB-09). In Lambda,
`dispatch()` invokes the worker function asynchronously.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from daybook.agents.base import Deps
from daybook.engine.budget import DemoCaps, SpendBreaker
from daybook.memory.embed import Embedder, FakeEmbedder, GeminiEmbedder
from daybook.memory.vault import Vault, VaultRegistry
from daybook.obs.log import log_event
from daybook.settings import Settings, get_settings
from daybook.storage.blob import BlobStore, get_blob_store
from daybook.voice.tts import TTS, make_tts

log = logging.getLogger(__name__)


@dataclass
class User:
    uid: str  # "owner" or "demo-<sandbox>"
    role: str  # owner | demo
    prefix: str  # blob prefix: "users/owner/" or "demo/<sandbox>/"
    sandbox_id: str | None = None

    @property
    def vault_key(self) -> str:
        return f"{self.prefix}memory.db"


OWNER = User("owner", "owner", "users/owner/")


def demo_user(sandbox_id: str) -> User:
    return User(f"demo-{sandbox_id}", "demo", f"demo/{sandbox_id}/", sandbox_id)


class App:
    def __init__(
        self,
        settings: Settings | None = None,
        store: BlobStore | None = None,
        llm: Any = None,
        embedder: Embedder | None = None,
        tts: TTS | None | str = "auto",
    ):
        self.settings = settings or get_settings()
        s = self.settings
        self.store = store or get_blob_store()
        self.vaults = VaultRegistry(self.store, s.daybook_data_dir / "cache", s.embed_dim)
        self.llm = llm if llm is not None else self._make_llm()
        if embedder is not None:
            self.embedder = embedder
        elif s.daybook_embed == "gemini":
            from daybook.llm.gemini import GeminiLLM

            gem = self.llm if hasattr(self.llm, "client") else GeminiLLM(s)
            self.embedder = GeminiEmbedder(gem, s.embed_model)
        else:
            self.embedder = FakeEmbedder(s.embed_dim)
        self.tts = make_tts(s) if tts == "auto" else tts
        self.deps = Deps(self.llm, s)
        self.spend = SpendBreaker(self.store, s.daily_budget_usd, s.demo_budget_usd)
        self.demo_caps = DemoCaps(self.store, s)
        self._tasks: set[asyncio.Task] = set()

    def _make_llm(self):
        s = self.settings
        if s.daybook_llm == "live":
            if not s.gemini_api_key:
                raise RuntimeError("DAYBOOK_LLM=live needs GEMINI_API_KEY")
            from daybook.llm.gemini import GeminiLLM

            return GeminiLLM(s)
        from daybook.llm.fakes import HeuristicFakeLLM

        return HeuristicFakeLLM()

    def vault(self, user: User) -> Vault:
        return self.vaults.get(user.vault_key)

    # ------------------------------------------------------------------ async jobs

    def dispatch(self, job: str, **payload: Any) -> None:
        """Run a worker job off the request path."""
        event = {"job": job, **payload}
        if self.settings.worker_function_name and not self.settings.is_local:
            import boto3

            boto3.client("lambda").invoke(
                FunctionName=self.settings.worker_function_name,
                InvocationType="Event",
                Payload=json.dumps(event).encode(),
            )
            return
        from daybook.worker import run_job

        async def runner():
            try:
                await run_job(self, event)
            except Exception:
                log.exception("job %s failed", job)
                log_event(event="job_failed", job=job)

        task = asyncio.get_running_loop().create_task(runner())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for in-process jobs (tests and the terminal chat)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


_app: App | None = None


def get_app() -> App:
    global _app
    if _app is None:
        _app = App()
    return _app


def set_app(app: App | None) -> None:
    global _app
    _app = app
