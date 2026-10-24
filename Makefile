# Daybook: local development. Deploy targets come later (no infra changes until Ayan says so).
SHELL := /bin/bash
BE := cd backend &&
PORT ?= 8300

.PHONY: help install test lint dev dev-live api api-live web chat chat-live seed owner-key vapid eval build-web

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

install: ## install backend (uv) and frontend (npm) deps
	$(BE) uv sync
	cd frontend && npm install

test: ## offline tests (fake LLM, fake embeddings, fake TTS)
	$(BE) uv run pytest -q

lint: ## ruff + typescript
	$(BE) uv run ruff check daybook tests scripts && uv run ruff format --check daybook tests scripts
	cd frontend && npx tsc --noEmit

api: ## API on :8300 with the fake LLM
	$(BE) DAYBOOK_LLM=fake DAYBOOK_EMBED=fake DAYBOOK_TTS=fake uv run uvicorn daybook.main:app --reload --port $(PORT)

api-live: ## API on :8300 with real Gemini (LLM + embeddings + Gemini TTS)
	$(BE) DAYBOOK_LLM=live DAYBOOK_EMBED=gemini DAYBOOK_TTS=$${DAYBOOK_TTS:-gemini} DAYBOOK_DATA_DIR=../data/local-live uv run uvicorn daybook.main:app --reload --port $(PORT)

web: ## Vite dev server on :5173 (proxies /api to :8300)
	cd frontend && npm run dev

dev: ## API (fake) + web together
	@trap 'kill 0' EXIT; $(MAKE) -s api & $(MAKE) -s web & wait

dev-live: ## API (live Gemini) + web together
	@trap 'kill 0' EXIT; $(MAKE) -s api-live & $(MAKE) -s web & wait

chat: ## a full reflection in the terminal (fake LLM)
	$(BE) DAYBOOK_LLM=fake DAYBOOK_EMBED=fake uv run python scripts/chat.py

chat-live: ## a full reflection in the terminal (real Gemini)
	$(BE) DAYBOOK_LLM=live DAYBOOK_EMBED=gemini DAYBOOK_DATA_DIR=../data/local-live uv run python scripts/chat.py

seed: ## build data/seed/demo-memory.db (DAYBOOK_EMBED=fake|gemini)
	$(BE) uv run python scripts/seed_demo.py

owner-key: ## generate an owner key; prints it once, stores only its hash in .env
	$(BE) uv run python scripts/owner_key.py

vapid: ## generate VAPID keys for Web Push into .env
	$(BE) uv run python scripts/gen_vapid.py

build-web: ## production build of the PWA
	cd frontend && npm run build

eval: ## eval suites in fake mode (plumbing only; free)
	uv run --project backend python -m evals run --suite all --mode fake

eval-live: ## live eval run: prints the cost estimate and needs --yes (get Ayan's sign-off first)
	uv run --project backend python -m evals run --suite $${SUITE:-all} --mode live
