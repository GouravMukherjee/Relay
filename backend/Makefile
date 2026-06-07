.PHONY: install migrate run-gateway run-agent run-ingest seed-demo test

install:
	pip install -e .

migrate:
	alembic --config alembic.ini upgrade head

run-gateway:
	uvicorn relay.gateway.app:app --host 0.0.0.0 --port 8000 --reload

run-agent:
	python -m relay.agent.worker

run-ingest:
	arq relay.ingestion.worker.WorkerSettings

seed-demo:
	python -m relay.seed.northwind --fake-embeddings

test:
	pytest
