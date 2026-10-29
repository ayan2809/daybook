# Build status (handoff file)

Read this first. It says what exists, what works, how to run it and what's next. For how the code
works (low-level architecture), read the root `README.md`. Update it at the end of
every working block. Owner of the build: the main implementation agent; subagents only read it.

**Last updated:** 2026-10-29 · **Branch:** `feature/initial-implementation`
**State:** M1 (engine), M2 (PWA core) and M3 (memory features) work locally, in fake mode and against
live Gemini. Infra go-ahead given 2026-10-29: storage stack deployed, app stack waits for secrets.

## How to run (local)

```bash
make install                     # uv sync (Python 3.12) + npm install
make test                        # 25 offline tests (fake LLM/embeddings/TTS), ~3 s
make dev                         # API :8300 (fake LLM) + Vite :5173 with the /api proxy → open :5173
make dev-live                    # same with real Gemini (LLM + gemini-embedding-2) + ElevenLabs voice
                                 # (DAYBOOK_TTS=gemini make dev-live for the Gemini TTS fallback)
make chat / make chat-live       # a full reflection in the terminal
make eval                        # eval suites in fake mode (plumbing only; free)
make seed                        # rebuild data/seed/demo-memory.db from demo-persona.json (DAYBOOK_EMBED=gemini for prod)
make check-data                  # seed + eval inputs consistent (keys, quotes, numbers)
make owner-key / make vapid      # owner sign-in key (prints once) / Web Push keys, both into .env
make secrets                     # .env keys → SSM /daybook/* (prints names only)
make deploy                      # storage + Lambda zip + app stack + PWA + smoke (DB-27)
```

Root `.env` (git-ignored): `GEMINI_API_KEY`, `GEMINI_TTS_API_KEY` (from Lore), later `ELEVENLABS_API_KEY`,
`ELEVEN_VOICE_ID`, `OWNER_KEY_HASH`, `SESSION_SIGNING_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`.
Local data lives in `data/local/` (fake) and `data/local-live/` (live), both git-ignored.
Time travel: header `X-Debug-Now` (local and demo only), or `localStorage["daybook.debugNow"]` in the PWA.

## What was verified (2026-10-24/25)

| Check | Result |
|---|---|
| Unit + flow tests (`make test`) | 25 pass: schema/validation/deltas (A1–A3), versions and restore, pending lifecycle (A7), vault If-Match (A8), hybrid retrieval + numpy fallback (A10), guided session end to end, idempotency + time_up (A9, A6), wrap timing + stop, safety path with no LLM call (A11), auth/origin rules (A14), demo seed + caps + replay (A15, L1, L6), fix-by-voice (F1–F2), Ask abstention (I2), typed pending answer (E7), Timeline edit/restore/hide (F7), settings, reminder tick (A16), soft-idle cleanup (DB-13). |
| Browser (headless Chromium, 390×844, Playwright from the scratchpad) | Landing → demo → Today (2 pending) → guided reflection (pending answer + confirm, defer, new work, suggestion chip, wrap) → summary → Timeline → item → Ask (citations + CFO abstention) → Remind me → Settings: **all pass, zero console/API errors**, in fake mode **and** live Gemini mode. |
| Live terminal session | Correct items, exact numbers (1,200 → under 100, delta by code), XYZ bullets, promised question created, no guard failures, $0.016. |
| Live HTTP smoke test (2026-10-29, Gemini + ElevenLabs Sarah, new persona) | 20/20: demo start → Today (Ayan, 2 open) → guided session (metric read-back + confirm, ownership answer, new work, wrap naming tomorrow's question) → MP3 audio on every reply → finalize (3 items, 1 new pending) → Ask with citations → CFO abstention → Timeline 31 items. Turns 2.4–4.7 s. |
| Live demo run in the browser | Pending loop, deferral, follow-ups, wrap naming the open question; finalize + judge; whole run incl. Gemini voice and Ask ≈ $0.04. |
| Lint | `ruff check` + `ruff format --check` clean; `tsc --noEmit` clean; `npm run build` OK. |

## Spike results

| Spike | Result |
|---|---|
| Model ids | `gemini-3.5-flash-lite`, `gemini-3.8-flash`, `gemini-embedding-2` (768-d), `gemini-3.8-flash-lite-tts` all reachable. |
| S5 (text) | One Flash-Lite call ≈ 1.2–1.9 s (3.1-flash-lite the same). Turn p50 ≈ 3.2 s locally, above the 2.5 s target (DB-14, RQ-04). |
| TTS fallback | Gemini TTS ≈ 3 s per line, WAV 24 kHz mono, plays in the browser. |
| S1 (local only) | sqlite-vec v0.1.9 + FTS5 load in uv's CPython 3.12. Lambda untested. |

## Component status

Legend: ✅ done and tested · 🟡 partial · ⬜ not started

| Area | Status | Notes |
|---|---|---|
| Docs (CLAUDE, STATUS, API, DECISIONS, REVIEW_QUEUE, VOICE_PROMPTS) | ✅ | |
| Settings, Gemini client (from Lore), fakes, pricing, tracer | ✅ | `llm/gemini.py`, `llm/fakes.py`, `obs/` |
| Memory: schema, vault (If-Match), repo + versions, validate, retrieval, export | ✅ | export to Resume-as-code: shape only; X3 (its jq gate) not run |
| Agents + versioned prompts | ✅ | researcher, fast pass, edit patch, pending answer, context, chat, deep pass (v2), ask, judge |
| Engine: phases, agenda, guards, safety, conductor, finalize, budget | ✅ | quick log + brain dump wired; `pending_at=end` not built (RQ-06) |
| API (all routes in docs/API.md) | ✅ | |
| Voice | ✅ | ElevenLabs (`eleven_flash_v2_5`, voice Sarah, DB-26) is the live default, smoke-tested 2026-10-29; Gemini TTS is the fallback; pre-recorded lines (P1) not built |
| Push + reminders | 🟡 | tick logic tested with a fake sender; real Web Push needs HTTPS + device (RQ-10) |
| Traces, Admin API, session judge | ✅ | Admin UI built by the frontend subagent; checked only by type-check |
| Demo sandbox, seed, caps, replay | 🟡 | persona: Ayan at Kestrel Travel, fictional, plain words (DB-25); replay has no recorded session yet (RQ-07) |
| Frontend PWA | ✅ | all screens; browser-tested flows above; not yet on a real phone |
| Eval runner + data | 🟡 | fake mode in CI; live run waits for sign-off (RQ-05); `sim` suite (P1) not built |
| CI (GitHub Actions) | ✅ | `.github/workflows/ci.yml`: ruff, pytest, fake evals, tsc, build |
| Infra (CloudFormation, deploy scripts, Lambda zip) | 🟡 | go-ahead 2026-10-29. Templates + scripts done (DB-27). `daybook-storage` stack **deployed**; app stack waits for `make secrets` (RQ-12) |

## Next up (in order)

1. Ayan: work through `docs/REVIEW_QUEUE.md` (owner key, persona/eval sign-off, latency choice).
2. Record the demo replay session with ElevenLabs audio (RQ-07).
3. Infra: Ayan runs `make secrets` (+ `make owner-key` first), then `make deploy`; then the deployer policy
   update (RQ-13) and `DAYBOOK_SCHEDULES=true make deploy-backend`; then spikes on the real URL. Was:
   `infra/daybook.yaml`, `infra/deploy.sh`, `scripts/build_lambda_zip.sh`,
   SSM secrets, then spikes S1/S2/S3/S4/S6/S7 on the real URL.
4. P1: Remind me read-aloud, pre-recorded lines, continuity recall, `sim` eval suite, weekly digest.
5. First live eval run (after sign-off), fix the worst failures.

## Map of the code (where to look)

- One turn: `backend/daybook/engine/conductor.py` (`handle_turn` → `_run_turn`), rules in `engine/agenda.py`.
- After a session: `engine/finalize.py` (deep pass, code validation, vault write), `worker.py` (jobs).
- Prompts: `backend/daybook/prompts/*.md` (bump `version:` on any change).
- HTTP: `backend/daybook/api/*.py`, app in `backend/daybook/main.py`.
- PWA: `frontend/src/screens/*`, Reflect screen in `frontend/src/screens/reflect/`, API client `frontend/src/api.ts`.
