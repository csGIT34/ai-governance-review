# Common tasks. Assumes an activated venv with requirements-dev.txt installed.
PG_TEST_URL ?= postgresql+psycopg://governance:governance@localhost:55432/governance

.PHONY: up seed-demo test test-pg e2e migrate revision

up:            ## run the app + Postgres + Azurite on http://localhost:8000
	docker compose up --build

seed-demo:     ## load example controls and scope into the running compose stack
	docker compose exec app python -m app.seed --demo

test:          ## full suite on SQLite (browser test skips if Chromium is missing)
	python -m pytest -q

test-pg:       ## full suite on a throwaway Postgres container
	docker run -d --rm --name govtest-pg -e POSTGRES_USER=governance -e POSTGRES_PASSWORD=governance \
	  -e POSTGRES_DB=governance -p 55432:5432 postgres:16-alpine >/dev/null
	until docker exec govtest-pg pg_isready -U governance >/dev/null 2>&1; do sleep 1; done
	TEST_DATABASE_URL=$(PG_TEST_URL) python -m pytest -q; status=$$?; docker stop govtest-pg >/dev/null; exit $$status

e2e:           ## browser test of autosave (run `playwright install chromium` once)
	python -m pytest -q tests/test_e2e_autosave.py

migrate:       ## apply migrations to DATABASE_URL
	alembic upgrade head

revision:      ## autogenerate a migration after changing app/models.py: make revision m="add x"
	alembic revision --autogenerate -m "$(m)"
