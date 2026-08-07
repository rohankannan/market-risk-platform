PY ?= .venv/bin/python

# demo window: snapshot history ends 2026-08-06; backfill stops one day short
# so the final date runs through the full EOD path (DQ + scenarios included)
DEMO_BF_START ?= 2025-05-01
DEMO_BF_END   ?= 2026-08-05
DEMO_DATE     ?= 2026-08-06

.PHONY: venv test lint db-up db-down migrate seed backfill eod api demo

venv:
	python3.11 -m venv .venv
	.venv/bin/pip install -U pip
	.venv/bin/pip install -e ".[dev]"

test:
	$(PY) -m pytest

lint:
	.venv/bin/ruff check .

db-up:
	docker compose up -d db --wait

db-down:
	docker compose down

migrate:
	.venv/bin/alembic upgrade head

seed:
	$(PY) -m risk.jobs.seed

backfill:
	$(PY) -m risk.jobs.eod backfill --start $(DEMO_BF_START) --end $(DEMO_BF_END) --resume

eod:
	$(PY) -m risk.jobs.eod run --date $(DEMO_DATE) --no-fetch --force

api:
	.venv/bin/uvicorn api.main:app --reload

# offline end-to-end demo from the committed snapshot; re-runnable (resume/force)
demo: db-up migrate seed backfill eod
