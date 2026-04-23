.PHONY: help up down logs shell psql redis-cli migrate makemigration test lint fmt clean

help:
	@echo "Targets:"
	@echo "  up            Start the full stack (postgres + redis + api) with logs."
	@echo "  down          Stop and remove containers (keeps volumes)."
	@echo "  logs          Tail logs from the api container."
	@echo "  shell         Open a shell in the api container."
	@echo "  psql          Open psql against the ledger database."
	@echo "  redis-cli     Open redis-cli against the redis container."
	@echo "  migrate       Run alembic upgrade head inside the api container."
	@echo "  makemigration M=<msg> Autogenerate a new alembic revision."
	@echo "  test          Run pytest (spins up testcontainers)."
	@echo "  clean         docker compose down -v (DROPS volumes)."

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f api

shell:
	docker compose exec api /bin/sh

psql:
	docker compose exec postgres psql -U ledger -d ledger

redis-cli:
	docker compose exec redis redis-cli

migrate:
	docker compose exec api alembic upgrade head

makemigration:
	@if [ -z "$(M)" ]; then echo "usage: make makemigration M='describe the change'"; exit 1; fi
	docker compose exec api alembic revision --autogenerate -m "$(M)"

test:
	python -m pip install -r requirements-dev.txt
	pytest -q

clean:
	docker compose down -v
