---
title: "I lost the numbers behind nine months of work, so I built a voice journal that asks for them"
published: true
description: "Daybook is a five-minute evening voice conversation that turns your workday into a searchable, resume-ready memory, encrypted with a key only you hold."
tags: ai, webdev, privacy, productivity
cover_image: ""
---

In October I sat down to update my resume and found I couldn't. I remembered the projects. I had lost the numbers.

I had to rebuild nine months of work from memory. When I checked my own notes, 21 of 35 achievements had no measured result. The numbers existed on the day the work happened, and nobody wrote them down. That nobody was me.

So I built Daybook, for No Hands: The Wispr Challenge by WeMakeDevs and Wispr Flow.

## What Daybook does

Every evening I have a five-minute voice conversation with it. It asks one question at a time, and it asks the follow-ups a good manager would. "You said the import got faster. By how much?"

Then it turns my answers into a memory I can search, with resume-ready bullets in the XYZ shape: "Accomplished X as measured by Y, by doing Z".

The rule I care about most: **it never invents a number.** Every number has to appear in my own words. Deltas and percentages are computed in code, not by the model. If I don't know a number yet, that is fine. It becomes a question that comes back tomorrow.

## A tour

- **Guided session or brain dump.** Guided mode asks yesterday's open questions first, then explores today. Brain dump lets me talk for a few minutes in one go, and then asks at most three follow-ups.
- **Voice out, Wispr Flow in.** Daybook speaks with ElevenLabs. I answer by dictating with Wispr Flow.
- **"Captured so far" chips.** While I talk, a draft board shows what it has understood, so I can spot a wrong guess right away.
- **Pending questions.** Anything unmeasured is carried to the next day. It asks up to three times, then lets the question expire.
- **Fix by voice.** "It was 40, not 50." It finds the memory, reads the change back, and saves a new version. The old one is kept. You can also edit in the UI, with versions.
- **Ask, with citations.** "What did I ship in October?" Answers cite my own notes. If nothing supports an answer, it says so instead of guessing.
- **Evening reminder.** A Web Push notification arrives on my phone and my watch.
- **Traces, an Admin page and evals.** Every turn leaves a trace. There are eval suites for the guards, intent routing, extraction, retrieval and Ask.

## How I used Wispr Flow

Wispr Flow is not a feature I added at the end. It is how I use the product and how I built it.

Every answer in a reflection is dictated. I hold Fn on the Mac, or use the Flow keyboard on iPhone.

The build was dictated too: specs, prompts to Claude Code (my coding agent), commit messages, the README.

The part I didn't expect to use so much is the Wispr Flow Scratchpad and Note Taker, which let me capture prompts when I'm not at my laptop. I talk an idea or a feature request into Flow on my phone, and later I paste that prompt into Claude Code. The Note Taker keeps notes of things I talked through, and I turn those into prompts later.

One real example, from a scratchpad note on Oct 25: "I want you to check my resume as a code repository and take inspiration from what kind of work I do on a day-to-day basis... do not expose anything that is related to me, because at the end of the day, I want to maintain privacy with the demo." That note became the change that rebuilt the demo persona.

## Privacy, because this is a diary

A journal of your work is sensitive, so I added the part I'd want if I were a user.

Each account gets a **recovery key that only you hold.** It is generated in your browser and shown once. You save it in a password manager. Your browser remembers it on that device, and on a new device you paste it once.

Everything in your folder is encrypted before it reaches S3: the memory database, every conversation transcript, traces, usage counters and cached voice audio. The cipher is AES-256-GCM, under a key derived from your recovery key.

```text
recovery key = 160 random bits, made in the browser, shown once
vault key    = HKDF-SHA256(recovery key)            # never stored
blob key     = HKDF(vault key, "blob v1")           # AES-256-GCM for every file
folder       = "v" + HMAC(vault key, "folder v1")   # your S3 prefix
key check    = HMAC(vault key, "check v1")          # all the account record keeps
account id   = "a" + HMAC(server key, google_sub)   # no email, no name stored

S3 object    = "DBK1" | 12-byte nonce | ciphertext+tag   (the object's path is the associated data)
```

That's the real key hierarchy from `storage/sealed.py`. Three consequences:

- The account id is a keyed hash of Google's opaque account id. I store no email and no name.
- The folder name comes from your key, so even the person running the AWS account can't tell which folder belongs to which sign-in.
- Lose the key and the data is gone. There is no reset, by design.

Why encryption and not hashing? A hash can't be read back, not even by you.

The honest limits:

- The server has to decrypt in memory during a request to run the conversation.
- Gemini processes each turn's text.
- The operator could in theory deploy changed code. That would show in the deploy history.
- Reminders fire while you're away, so one small record stays readable to the server: push endpoints, reminder times, the last day you reflected and how many questions are open. No words.

## Export your brain

One click exports the whole memory as a single Markdown file written for an LLM. It opens with a short note to the model on how to use it, and then the memory itself.

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

Paste it into ChatGPT, Claude or Gemini and ask for a resume draft, appraisal prep or a weekly update. It works in the demo too.

## How it's built

Claude Code wrote the code, from prompts I dictated. I want to say that plainly. Gemini is the LLM inside the app. The backend is FastAPI on Lambda, the frontend is a React PWA, and each user's memory is one SQLite file in S3.

The orchestration is plain code, not an LLM. Code decides what to ask next, when to wrap up, and which numbers are allowed. The model does the language work: understanding a turn, drafting items and writing the bullets.

Hosting is Lambda, S3 and CloudFront, so nothing runs when nobody uses it. At one person's use the AWS bill is close to nothing. The real cost is the AI calls, and a $2/day spend breaker caps them. A full live demo run, with voice and Ask, cost about four cents.

Sign-in is Google. There is also a public demo with no sign-in, using fictional data. "Ayan at Kestrel Travel" is a made-up persona, so none of my real work is in it.

## Try it

- Live app: https://d2u2cnea4n3vkc.cloudfront.net (tap "Try the demo")
- [Demo video](https://youtu.be/VZzfZ93o-I4)
- Repo: https://github.com/ayan2809/daybook

Built for No Hands: The Wispr Challenge by WeMakeDevs and Wispr Flow.
