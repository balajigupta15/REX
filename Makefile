.PHONY: setup test api web dev seed
setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r backend/requirements.txt
	@if [ -d web ]; then cd web && npm install; fi
test:
	cd backend && python3 -m pytest -q tests
seed:
	cd backend && python3 scripts/seed_demo.py --reset
api:
	cd backend && REX_DB_URL=$${REX_DB_URL-postgresql://yugabyte@127.0.0.1:5434/rexlite} REX_OFFLINE=$${REX_OFFLINE:-1} python3 -m uvicorn app.main:app --port 8000
web:
	cd web && NEXT_PUBLIC_API=http://localhost:8000 npm run dev
# one command: API (offline bundled data by default) + web UI
dev:
	$(MAKE) -j2 api web
