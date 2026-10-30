# Daybook: local development and AWS deploy (CloudFormation; infra/deploy.sh).
SHELL := /bin/bash
BE := cd backend &&
PORT ?= 8300

.PHONY: help install test lint dev dev-live api api-live web chat chat-live seed owner-key vapid eval build-web \
	secrets build-lambda deploy deploy-backend deploy-frontend status logs destroy

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

api-live: ## API on :8300 with real Gemini (LLM + embeddings) + ElevenLabs voice (DAYBOOK_TTS=gemini for Gemini TTS)
	$(BE) DAYBOOK_LLM=live DAYBOOK_EMBED=gemini DAYBOOK_TTS=$${DAYBOOK_TTS:-elevenlabs} DAYBOOK_DATA_DIR=../data/local-live uv run uvicorn daybook.main:app --reload --port $(PORT)

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

check-data: ## check the demo seed and eval inputs against each other
	python3 evals/check_data.py

eval: ## eval suites in fake mode (plumbing only; free)
	uv run --project backend python -m evals run --suite all --mode fake

eval-live: ## live eval run: prints the cost estimate and needs --yes (get Ayan's sign-off first)
	uv run --project backend python -m evals run --suite $${SUITE:-all} --mode live

# ---------------------------------------------------------------- AWS (ap-south-1)
secrets: ## copy keys from .env into SSM (/daybook/*); never prints values
	$(BE) uv run python scripts/push_secrets.py

build-lambda: ## build build/daybook-lambda.zip (arm64 wheels, no Docker)
	scripts/build_lambda_zip.sh

deploy: ## full deploy: storage, Lambda zip, app stack, PWA, smoke check
	infra/deploy.sh all

deploy-backend: ## Lambda + stack only (DAYBOOK_SCHEDULES=true for reminder schedules)
	infra/deploy.sh backend

deploy-frontend: ## PWA only (build, upload, invalidate)
	infra/deploy.sh frontend

status: ## stack outputs
	aws cloudformation describe-stacks --stack-name daybook --query 'Stacks[0].[StackStatus,Outputs]' --output table

logs: ## tail the API logs (FN=worker for the worker)
	aws logs tail /aws/lambda/daybook-$${FN:-api} --since 30m --follow

destroy: ## delete the app stack (the data bucket and its memory vault are kept)
	@read -p "Type daybook to delete the app stack: " x && [ "$$x" = daybook ] && aws cloudformation delete-stack --stack-name daybook && aws cloudformation wait stack-delete-complete --stack-name daybook && echo deleted
