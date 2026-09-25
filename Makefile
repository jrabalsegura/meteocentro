# Atajos locales. No conectan con `remote` ni publican nada.
VENV := backend/.venv/bin
TEST_DB_PORT ?= 55432
TEST_DATABASE_URL ?= postgresql+psycopg://meteocentro:test@127.0.0.1:$(TEST_DB_PORT)/meteocentro_test
PY_TESTS := tests/test_phase1.py tests/test_phase2.py tests/test_phase3.py tests/test_phase4.py \
	tests/test_phase5.py tests/test_phase6.py tests/test_phase7.py
ADMIN ?= propietario
export TEST_DATABASE_URL

.PHONY: up up-worker down ps logs admin test-db test-db-stop test lint web-build e2e check

up: ## Base, API y web con recarga (http://localhost:5173)
	docker compose up -d --build

up-worker: ## Igual que up, más el worker de ingestión (usa AEMET_API_KEY de .env)
	docker compose --profile ingestion up -d --build

down: ## Detiene contenedores y conserva el volumen de datos
	docker compose --profile ingestion down

ps:
	docker compose --profile ingestion ps

logs:
	docker compose --profile ingestion logs -f --tail=100

admin: ## make admin ADMIN=nombre
	docker compose exec api python -m meteocentro.admin_cli create $(ADMIN)

test-db: ## PostgreSQL desechable para pruebas (sin volumen)
	@docker inspect meteocentro-testdb >/dev/null 2>&1 || docker run -d --rm --name meteocentro-testdb \
		-e POSTGRES_USER=meteocentro -e POSTGRES_PASSWORD=test -e POSTGRES_DB=meteocentro_test \
		-e POSTGRES_INITDB_ARGS=--encoding=UTF8 -p 127.0.0.1:$(TEST_DB_PORT):5432 postgres:17.11
	@until docker exec meteocentro-testdb pg_isready -U meteocentro -d meteocentro_test >/dev/null 2>&1; do sleep 1; done

test-db-stop:
	docker stop meteocentro-testdb

test: test-db ## Pruebas de backend y despliegue, como en CI
	$(VENV)/pytest $(PY_TESTS) -q
	python3 -m unittest discover -s tests -p test_phase0.py
	python3 -m unittest discover -s tests -p test_deploy.py

lint:
	$(VENV)/ruff check backend/src tests/conftest.py $(PY_TESTS)
	$(VENV)/ruff format --check backend/src tests/conftest.py $(PY_TESTS)
	$(VENV)/ruff check --config backend/pyproject.toml deploy/scripts tests/test_deploy.py tests/seed_phase7.py

web-build:
	cd frontend && npm run build

e2e: ## Recorridos de navegador con fixtures (sin red externa)
	cd frontend && npm run test:e2e

check: lint test web-build ## Lo mismo que exige la CI antes de un PR
