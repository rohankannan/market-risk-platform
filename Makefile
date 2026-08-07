PY ?= .venv/bin/python

.PHONY: venv test lint db-up db-down migrate demo

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

# milestone 2+: make demo = db-up + migrate + seed + backfill
demo: db-up migrate
	$(PY) -m risk.jobs.seed
