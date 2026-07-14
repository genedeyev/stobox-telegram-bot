.PHONY: install run ingest test eval lint fmt docker up

install:
	pip install -e ".[dev]"

run:
	python -m stobox_ai

ingest:
	stobox-ingest --rebuild

test:
	pytest -q

eval:
	python -m evals.run_evals --min-pass 0.0

lint:
	ruff check src evals tests

fmt:
	ruff check --fix src evals tests

docker:
	docker build -t stobox-ai .

up:
	docker compose up --build
