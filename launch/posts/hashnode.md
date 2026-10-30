---
title: "Daybook: a voice journal where code owns the numbers and only you hold the key"
subtitle: "How a five-minute evening conversation becomes resume-ready XYZ bullets, with an encrypted per-user SQLite vault on Lambda and S3"
slug: daybook-voice-journal-encrypted-memory
tags: ai, python, serverless, privacy
cover: ""
---

## The bug in my own workflow

In October I opened my resume to update it and had to reconstruct nine months of work. I knew what I had built. I didn't know by how much it had helped. My own notes backed that up: 21 of 35 achievements had no measured result.

That is a data-capture problem, not a memory problem. The number exists on the day, and it evaporates within weeks. So the fix has to run on the day, and it has to cost almost no effort.

Daybook is my answer for No Hands: The Wispr Challenge by WeMakeDevs and Wispr Flow. It is a five-minute voice conversation each evening. It asks what a good manager would ask ("by how much?"), then files the answers as a searchable memory with XYZ bullets: "Accomplished X as measured by Y, by doing Z".

This post is the technical version.

## Design rules

1. **The orchestrator is code.** An LLM never decides which step runs next.
2. **The model never produces a number the user didn't say.** Arithmetic lives in one Python module.
3. **Everything external sits behind a small interface** (blob store, LLM, embedder, TTS, push), so the whole app runs offline in tests.
4. **The server stores ciphertext.** More on that below.

## How it works

### One turn

A turn is `POST /sessions/{id}/turns` with the text and a client turn id for idempotency. The flow:

```text
text in
  -> Researcher (Flash-Lite) || fast pass (Flash-Lite)    # intent, entities | draft items, raw metrics
  -> merge into the session "board"
  -> agenda.decide(state, label, now)                      # plain rules, first match wins
  -> Chat (Flash-Lite) writes one short spoken line
  -> guards (code): one question, length, no advice, every number must appear in the user's words
  -> reply text, then audio from /turns/{id}/audio
```

Two hops of Flash-Lite give a turn latency of roughly 3 seconds in my measurements from Bangalore, which is above my 2.5 s target. I wrote that down as an open question.

The agenda is a chain of rules. An open confirmation comes first ("So that's from 40 to 6 a week?"), then the safety path, then meta commands such as stop or repeat, then edit and recall requests, then the next pending question, then the most useful open gap. Numbers are asked for achievements and never for soft skills. Crisis phrases skip the LLM entirely and return a fixed helpline message.

### Short-term and long-term memory

The session file (every turn, the draft board, the pending queue) is a JSON blob rewritten each turn with `If-Match` on the ETag. The vault is the long-term memory: one SQLite file per user, with FTS5 and vector search inside it. During a session it is written only when you confirm something. Otherwise it is written once, at finalize.

Finalize runs on a second Lambda. A deeper pass (Gemini Flash, low thinking) drafts items, bullets, metrics, links and new pending questions. Then code validates all of it before a versioned write.

### Numbers

A metric is accepted only if its quote and its figures appear in your words. A delta is `(final - baseline) / baseline`, computed in `memory/validate.py` and nowhere else. When the user answers a pending question with a number, the parser proposes it, the app reads it back, and nothing is saved until you say yes.

Unknown numbers are not failures. A pending question is created, and it is asked at the start of a later guided session. You can answer, defer, mark it "not quantifiable", or let it expire after three asks.

### Retrieval and abstention

Ask uses hybrid search: embedding similarity (`gemini-embedding-2`, 768 dimensions) and FTS5 BM25, fused with Reciprocal Rank Fusion at k = 60, with a boost for dates like "last Tuesday". The result carries the best cosine and the keyword hit count. If the best cosine is under 0.35 and nothing matched by keyword, Ask abstains. Answers are sentences with citations to specific memories.

Fix by voice reuses the search. A verifier checks the candidate, the app reads the proposed change back, and a yes writes a new version. Old versions stay.

### Voice

Output is ElevenLabs (`eleven_flash_v2_5`), cached by a hash of voice, model and text, with Gemini TTS as the fallback. Input is Wispr Flow: hold Fn on the Mac, or use the Flow keyboard on iPhone. A burst of 20 or more characters followed by a pause auto-sends, which is what dictation looks like. Typed text sends on Enter.

## The privacy layer

A work journal is sensitive, and I didn't want to be the person who can read it.

Each account gets a recovery key that only the user holds. It is generated in the browser and shown once, to be saved in a password manager. The browser remembers it on that device. On a new device, you paste it once.

```text
recovery key = 160 random bits, made in the browser, shown once
vault key    = HKDF-SHA256(recovery key)            # never stored
blob key     = HKDF(vault key, "blob v1")           # AES-256-GCM for every file
folder       = "v" + HMAC(vault key, "folder v1")   # your S3 prefix
key check    = HMAC(vault key, "check v1")          # all the account record keeps
account id   = "a" + HMAC(server key, google_sub)   # no email, no name stored

S3 object    = "DBK1" | 12-byte nonce | ciphertext+tag   (the object's path is the associated data)
```

That's the real key hierarchy from `storage/sealed.py`. What matters:

- Everything in the user's folder is encrypted before it is written to S3: the SQLite file, every transcript, traces, usage counters and cached voice audio.
- The account id is a keyed hash of Google's opaque account id. No email or name is stored.
- The folder name derives from the user's key. Even the person running the AWS account can't match a folder to a sign-in.
- Lose the key and the data is gone. There is no reset, on purpose.

It is encryption rather than hashing because a hash can't be read back, not even by you.

The limits, stated plainly:

- The server must decrypt in memory during a request to run the conversation.
- Gemini processes each turn's text.
- The operator could in theory deploy changed code. That would show in the deploy history.
- Reminders fire while you're away, so one small record stays readable to the server: push endpoints, reminder times, the last day you reflected and how many questions are open. No words.

## Export your brain

One click produces a single Markdown file written for an LLM:

```text
# Ayan's work memory (Daybook export)
> For the AI reading this: first-hand notes, numbers verified, quotes verbatim, don't invent
## Profile
## Achievements            ### October 2026 …  (bullet, metrics, skills, open question, "You said")
## Progress · Things learned · Decisions · Praise received …
## Skills (times used)
## Projects
## Open questions (not answered yet)
## Recent days, in their own summary
```

Paste it into ChatGPT, Claude or Gemini to draft a resume, prep an appraisal or write a weekly update. It works in the demo too.

## Running it cheaply

Lambda for the API and the worker, S3 for the web app and the data, CloudFront in front. No servers, no VPC, no always-on timer, so nothing runs when nobody uses it. A $2/day spend breaker caps AI spend, with separate caps for the demo and for each account. Reminders are Web Push, scheduled per account, and they show up on a phone and a watch.

Quality is checked with a trace per turn, an owner-only Admin page and eval suites for guards, intent routing, extraction, retrieval and Ask. In CI the evals run in fake mode, which checks the plumbing. The tests run offline with a fake LLM, fake embeddings and fake TTS.

## How I built it

Wispr Flow is in both the product and the build. Every reflection answer is dictated. So were my specs, my prompts to Claude Code, my commit messages and the README.

The Scratchpad and Note Taker let me capture prompts away from my laptop. I talk an idea into Flow on my phone, and later paste it into Claude Code. The Note Taker keeps notes of things I talked through, which I turn into prompts. A scratchpad note from Oct 25 reads: "do not expose anything that is related to me, because at the end of the day, I want to maintain privacy with the demo." That became the fictional demo persona, "Ayan at Kestrel Travel".

The code was written by Claude Code, an AI coding agent, from prompts I dictated. Gemini is the model inside the app. The stack is FastAPI, a React PWA and SQLite per user in S3.

## Try it

- Live app, with a no-sign-in demo on fictional data: https://d2u2cnea4n3vkc.cloudfront.net
- [Demo video](https://youtu.be/VZzfZ93o-I4)
- Repo: https://github.com/ayan2809/daybook

Built for No Hands: The Wispr Challenge by WeMakeDevs and Wispr Flow.
