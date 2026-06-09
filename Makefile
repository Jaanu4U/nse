.PHONY: build up down logs test init-db sync-data shell

# Docker Compose Commands
build:
	docker-compose build

up:
	docker-compose up -d

down:
	docker-compose down

logs:
	docker-compose logs -f

# Run tests locally
test:
	./venv/bin/pytest backend/tests/

# Initialize database schemas inside the backend container
init-db:
	docker-compose exec backend python app/db_init.py

# Trigger a manual stock collection and analysis sync inside the backend container
sync-data:
	docker-compose exec backend python -c "from app.database import SessionLocal; from app.tasks.scheduler import run_daily_sync; run_daily_sync()"

# Enter backend container shell
shell:
	docker-compose exec backend /bin/bash
