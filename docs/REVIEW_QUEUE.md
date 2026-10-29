# Human review queue

Things that need Ayan's eyes, a decision, or a device test. The build doesn't wait on these: each item
says what the agent assumed in the meantime. Newest at the bottom of each section. Tick when resolved.

## Decisions needed

- [x] **RQ-01 · ElevenLabs.** Key arrives tomorrow. Until then voice uses Gemini TTS (DB-03). When the key
  is in `.env` as `ELEVENLABS_API_KEY`: pick a voice by ear (set `ELEVEN_VOICE_ID`) and compare
  `eleven_flash_v2_5` with v3 Conversational. *Assumed:* `DAYBOOK_TTS=gemini` locally.
  **Resolved 2026-10-29:** key set; Ayan picked **Sarah** (`EXAVITQu4vr4xnSDxMaL`, mature, warm, reassuring)
  on `eleven_flash_v2_5` (DB-26). Live smoke test passed (MP3 for every reply, 0.3–0.6 s). v3
  Conversational (same $0.04 / 1K chars) can still be compared by ear later.
- [x] **RQ-02 · Push to GitHub.** The remote `ayan2809/daybook` is set. The agent pushes the feature branch
  (code only, no secrets). Confirm the repo should stay public. *Assumed:* yes (plan §22).
  **Resolved 2026-10-29:** Ayan confirmed; the feature branch is on GitHub (CI green), not merged to main.

- [x] **RQ-03 · Gemini TTS price.** `obs/pricing.yaml` has a conservative placeholder for
  `gemini-3.8-flash-lite-tts` (not verified against the pricing page). The spend breaker uses it.
  *Assumed:* placeholder until checked; the whole live demo run (voice included) counted $0.039.
  **Resolved 2026-10-29** (pricing page, updated 2026-10-28): $0.50 / 1M text tokens in, **$6.00 / 1M audio
  tokens** out (25 tokens = 1 s, so ≈ $0.0015 per 10 s of audio), free tier available; both double on
  2027-01-01. `pricing.yaml` updated (output was a $10 placeholder).
- [ ] **RQ-04 · Turn latency ≈ 3.2 s p50** (target ≤ 2.5 s; DB-14). Options: (a) accept for now; text
  shows at once and voice follows; (b) merge Researcher + fast pass into one call (saves no hop, only
  tokens); (c) start the Chat call in parallel with the analysis using the previous turn's plan, then
  re-check (complex); (d) measure again from Lambda in ap-south-1 after deploy (likely lower).
  *Recommendation:* (a) now, then (d). *Assumed:* (a).
- [ ] **RQ-05 · First live eval run.** Estimate **$0.24** for all suites (`make eval-live` prints it,
  needs `--yes`). Plan 13.4 asks for your sign-off on the eval inputs (RQ-20), the grading method and
  the cost first. *Assumed:* not run yet.
- [ ] **RQ-06 · "Pending questions at the end" setting.** The setting is stored, but the engine always
  asks pending questions at the start (your choice in plan 22.2). Build the "end" variant, or remove the
  setting? *Assumed:* start only.
- [ ] **RQ-07 · Demo replay.** Replay mode works but has no recorded session yet
  (`data/seed/replay.json`). Record one real demo session with ElevenLabs audio once the key arrives.
- [ ] **RQ-08 · Your owner key.** Run `make owner-key` yourself: it prints the key once and stores only
  its hash in `.env`. The agent didn't generate one, so it never saw your key.
- [ ] **RQ-09 · Ownership wording in bullets.** Live deep pass turned "helping a friend" into "Mentored…".
  Prompt v2 forbids it (DB-18); confirm with the live extraction eval (RQ-05).

## Device / manual tests (can't be done by the agent)

- [ ] **RQ-10 · Spikes S2, S3, S4** (iPhone push + Watch, Wispr Flow keyboard in the installed PWA,
  audio playback after one tap). Need a deployed HTTPS URL, so they wait for the deploy go-ahead.
- [ ] **RQ-11 · Spikes S1, S6, S7** (sqlite-vec on Lambda, CloudFront → Function URL, deployer policy).
  Also wait for the deploy go-ahead.

## Content to review

- [ ] **RQ-20 · Demo persona + eval data** (rewritten 2026-10-25, DB-25; validated by
  `evals/check_data.py`, not yet read by a human): `data/seed/demo-persona.json` (Ayan at Kestrel
  Travel, fictional: 10 days, 30 items, 9 pending of which 2 open), `evals/data/{router,extraction,
  retrieval,ask}.jsonl` (61 / 15 / 30 / 30 lines). Plan 13.4 says Ayan reviews eval inputs before the
  first live eval run. Check: the story reads naturally and nothing in it is too close to your real
  work; "last week" in retrieval means Oct 19–23 (today = Sat 24th); the last day's plan says "next
  week" because 10-23 is a Friday.

## Cost sign-offs

- [ ] **RQ-30 · Spend so far (2026-10-24/25, dev):** spikes + two live terminal sessions + one live
  browser demo run + latency probes, roughly $0.10 in total per the app's own counters.
