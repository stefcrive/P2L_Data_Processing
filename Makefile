.PHONY: web api worker up down test fmt lint precommit-install

web:
	cd apps/web && npm run dev

api:
	uvicorn apps.api.app.main:app --reload

worker:
	cd apps/api && celery -A app.queue.celery_app.celery_app worker -Q irms -l info

up:
	docker compose -f infra/docker/docker-compose.dev.yml up --build

down:
	docker compose -f infra/docker/docker-compose.dev.yml down -v

test:
	python -m pytest -q apps/api/tests

fmt:
	black apps/api && isort apps/api

lint:
	flake8 apps/api && mypy apps/api

precommit-install:
	pre-commit install

