# Daybook: rules for coding agents

Daybook is a voice-first daily work reflection app (hackathon: No Hands, the Wispr Challenge).
**`plan.md` is the source of truth.** Read these before writing code:

1. `docs/STATUS.md`: what exists, what works, what's next, how to run it. **Start here.**
2. `docs/API.md`: the binding HTTP contract between `backend/` and `frontend/`.
3. `docs/DECISIONS.md`: decisions taken during the build (append, never rewrite).
4. `docs/REVIEW_QUEUE.md`: open questions for Ayan (add to it instead of blocking).
5. `plan.md` sections 5–8 for the engine; 10–11 for the app; 24 for the test script.

## Hard rules

- **LLM stack is Gemini only** (`google-genai`). No Anthropic SDK, keys or env vars in the app.
- Check every Gemini / ElevenLabs / Web Push call against the official docs. Don't write calls from memory.
- Every model id comes from an env var (`backend/daybook/settings.py`).
- Never invent numbers. Arithmetic (deltas, percentages) is code only (`memory/validate.py`).
- User text goes only in `contents` (role user). Operator instructions go only in `system_instruction`.
- Tests run offline: `make test` (fake LLM, fake embeddings, fake TTS; no network, no keys).
- Never commit secrets. `.env` is git-ignored; production keys live in SSM only.
- **No infrastructure changes or deploys** until Ayan says so (no `aws` writes, no `make deploy`).
- Git: work on feature branches; author `Ayan <ayansadhukhan28@gmail.com>`; **no `Co-Authored-By` trailer**.
- Python 3.12, async FastAPI, Pydantic v2, ruff (line 100). Structured logging, no `print` in app code.
- Prompts live in `backend/daybook/prompts/*.md` with a `version:` front matter. Bump it on any change.
- Frontend: Vite + React + TypeScript + vite-plugin-pwa (injectManifest), React Router, lucide-react,
  plain CSS with design tokens. Exact pinned versions. No heavy data library.

## Layout

```
backend/daybook/   main.py worker.py settings.py api/ engine/ agents/ prompts/ memory/ voice/ push/ obs/ llm/ storage/
backend/tests/     pytest, offline
frontend/          the PWA
data/seed/         demo-persona.json (demo persona: Ayan, made-up data), built demo-memory.db (git-ignored except seed)
evals/             eval data + runner
docs/              STATUS, API, DECISIONS, REVIEW_QUEUE, VOICE_PROMPTS
```

## Subagents

If you are a subagent: stay inside the directories your task names, don't commit, don't push, and end
with a short report: files changed, commands run and their results, anything left undone.
