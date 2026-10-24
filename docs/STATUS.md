# Build status (handoff file)

Read this first. It says what exists, what works, how to run it and what's next. Update it at the end of
every working block. Owner of the build: the main implementation agent; subagents only read it.

**Last updated:** 2026-10-24 (night) · **Branch:** `feature/initial-implementation`

## How to run (local)

```bash
cd backend && uv sync            # Python 3.12 venv in backend/.venv
make test                        # offline tests (fake LLM/embeddings/TTS)
make dev                         # API on :8300 (fake LLM) + Vite on :5173 with /api proxy
make dev-live                    # same with real Gemini (needs GEMINI_API_KEY in .env)
make chat                        # a full reflection in the terminal
make seed                        # build data/seed/demo-memory.db from data/seed/meera.json
```

Root `.env` (git-ignored) holds `GEMINI_API_KEY`, `GEMINI_TTS_API_KEY`, and later `ELEVENLABS_API_KEY`,
`DEV_OWNER_KEY`, `OWNER_KEY_HASH`, `SESSION_SIGNING_KEY`, `VAPID_*`.

## Day-0 spike results

| Spike | Result |
|---|---|
| Model ids | `gemini-3.5-flash-lite`, `gemini-3.8-flash`, `gemini-embedding-2` (768-d), `gemini-3.8-flash-lite-tts` all reachable with the key. |
| S5 (text) | Flash-Lite with `thinking_level=minimal` + JSON schema: p50 ≈ 1.25 s, max ≈ 2 s per call (from India, local). Turn ≈ parallel(Researcher, fast pass) + Chat ≈ 2.5 s. |
| TTS fallback | Gemini TTS `gemini-3.8-flash-lite-tts`: ≈ 3 s for one line, WAV 24 kHz mono. |
| S1 (local only) | sqlite-vec v0.1.9 loads and FTS5 works in uv's CPython 3.12 on macOS. Lambda still untested. |

## Component status

Legend: ✅ done and tested · 🟡 partial · ⬜ not started

| Area | Status | Notes |
|---|---|---|
| Docs (CLAUDE, STATUS, API, DECISIONS, REVIEW_QUEUE) | ✅ | |
| Backend skeleton, settings, LLM client, fakes | ⬜ | |
| Memory: schema, vault, repo, validate, retrieval | ⬜ | |
| Agents + prompts | ⬜ | |
| Engine: phases, agenda, guards, safety, conductor, finalize | ⬜ | |
| API routers | ⬜ | |
| Voice (TTS + normalize) | ⬜ | |
| Push + reminders | ⬜ | |
| Traces + Admin API + session judge | ⬜ | |
| Demo sandbox + seed + caps + replay | ⬜ | |
| Frontend PWA | ⬜ | subagent |
| Seed persona + eval data | ⬜ | subagent |
| Evals runner | ⬜ | |
| Infra (CloudFormation, deploy) | ⬜ | **blocked: no deploys until Ayan says so** |

## Next up

See the table above, top to bottom.
