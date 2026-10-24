"""Embedders: Gemini (`gemini-embedding-2`, 768-d) or a deterministic offline fake (plan 6.1).

Vectors are normalized to length 1 and stored as float32 blobs.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np

from daybook.llm.gemini import EmbedTask

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "my", "i", "it",
         "is", "was", "that", "this", "what", "did", "do", "at", "by", "from", "me", "about"}


class Embedder(Protocol):
    model_name: str

    async def embed(self, texts: list[str], task: EmbedTask) -> list[list[float]]: ...


def normalize(v) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(a))
    return a / n if n > 0 else a


def to_blob(v) -> bytes:
    return normalize(v).astype(np.float32).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def _stem(tok: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            return tok[: -len(suf)]
    return tok


def fake_embedding(text: str, dim: int = 768) -> list[float]:
    """Feature-hashed bag of stemmed words: deterministic, and texts that share words get a high
    cosine, so offline retrieval tests behave like keyword search."""
    v = np.zeros(dim, dtype=np.float32)
    for tok in _TOKEN.findall(text.lower()):
        if tok in _STOP:
            continue
        h = hashlib.md5(_stem(tok).encode()).digest()  # noqa: S324
        idx = int.from_bytes(h[:4], "little") % dim
        v[idx] += 1.0 if h[4] % 2 == 0 else -1.0
    if not v.any():
        v[0] = 1.0
    return normalize(v).tolist()


class FakeEmbedder:
    model_name = "fake-hash-768"

    def __init__(self, dim: int = 768):
        self.dim = dim

    async def embed(self, texts: list[str], task: EmbedTask) -> list[list[float]]:
        return [fake_embedding(t, self.dim) for t in texts]


class GeminiEmbedder:
    def __init__(self, llm, model_name: str):
        self.llm = llm  # GeminiLLM: owns the client and retries
        self.model_name = model_name

    async def embed(self, texts: list[str], task: EmbedTask) -> list[list[float]]:
        return [normalize(v).tolist() for v in await self.llm.embed(texts, task)]
