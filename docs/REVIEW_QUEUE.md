# Human review queue

Things that need Ayan's eyes, a decision, or a device test. The build doesn't wait on these: each item
says what the agent assumed in the meantime. Newest at the bottom of each section. Tick when resolved.

## Decisions needed

- [ ] **RQ-01 · ElevenLabs.** Key arrives tomorrow. Until then voice uses Gemini TTS (DB-03). When the key
  is in `.env` as `ELEVENLABS_API_KEY`: pick a voice by ear (set `ELEVEN_VOICE_ID`) and compare
  `eleven_flash_v2_5` with v3 Conversational. *Assumed:* `DAYBOOK_TTS=gemini` locally.
- [ ] **RQ-02 · Push to GitHub.** The remote `ayan2809/daybook` is set. The agent pushes the feature branch
  (code only, no secrets). Confirm the repo should stay public. *Assumed:* yes (plan §22).

## Device / manual tests (can't be done by the agent)

- [ ] **RQ-10 · Spikes S2, S3, S4** (iPhone push + Watch, Wispr Flow keyboard in the installed PWA,
  audio playback after one tap). Need a deployed HTTPS URL, so they wait for the deploy go-ahead.
- [ ] **RQ-11 · Spikes S1, S6, S7** (sqlite-vec on Lambda, CloudFront → Function URL, deployer policy).
  Also wait for the deploy go-ahead.

## Content to review

- [ ] **RQ-20 · Demo persona + eval data** (written by a subagent, validated by script, not yet read by
  a human): `data/seed/meera.json` (10 days, 30 items, 9 pending of which 2 open),
  `evals/data/{router,extraction,retrieval,ask}.jsonl` (61 / 15 / 30 / 30 lines). Plan 13.4 says Ayan
  reviews eval inputs before the first live eval run. Choices to check: lag metrics use category
  `other` with unit `messages`; "about 2 million" is stored expanded (2000000, approx); the last day's
  plan says "next time" because 10-23 is a Friday.

## Cost sign-offs
