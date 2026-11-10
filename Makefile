.PHONY: up down health test test-all backup clean-graphs watch unwatch watch-status

up:
	docker compose up -d

down:
	docker compose down

health:
	uv run swarmz health

test:
	uv run pytest -q

test-all:  ## includes integration tests (live API keys / FalkorDB)
	uv run pytest -q -m ""

# Copies the DuckDB registry (the critical file) and a FalkorDB snapshot into backups/<date>/.
backup:
	@d=backups/$$(date +%Y%m%d-%H%M%S); mkdir -p $$d; \
	db=$${SWARMZ_DB_PATH:-data/swarmz.duckdb}; \
	uv run python -c "import duckdb,sys; duckdb.connect(sys.argv[1]).execute('CHECKPOINT')" $$db && cp $$db $$d/; \
	if docker compose ps -q falkordb >/dev/null 2>&1 && [ -n "$$(docker compose ps -q falkordb)" ]; then \
	  docker compose exec -T falkordb redis-cli SAVE >/dev/null && \
	  docker compose cp falkordb:/var/lib/falkordb/data/dump.rdb $$d/falkordb-dump.rdb; \
	elif [ -d data/falkordb_lite ]; then cp -R data/falkordb_lite $$d/; fi; \
	echo "backup written to $$d"

clean-graphs:
	uv run swarmz graphs cleanup --yes

# Auto-resolve: poll Kalshi every 10 min, score and learn as markets settle. Runs detached
# (survives closing the terminal, not a reboot). Log: logs/resolve-watch.log
watch:
	@mkdir -p logs data
	@if [ -f data/resolve-watch.pid ] && kill -0 $$(cat data/resolve-watch.pid) 2>/dev/null; then \
	  echo "already running (pid $$(cat data/resolve-watch.pid))"; else \
	  nohup .venv/bin/swarmz resolve --watch --forever --every 600 >> logs/resolve-watch.log 2>&1 & echo $$! > data/resolve-watch.pid; \
	  echo "resolve watcher started (pid $$(cat data/resolve-watch.pid)); log: logs/resolve-watch.log"; fi

unwatch:
	@if [ -f data/resolve-watch.pid ]; then kill $$(cat data/resolve-watch.pid) 2>/dev/null && echo stopped || echo "not running"; rm -f data/resolve-watch.pid; else echo "not running"; fi

watch-status:
	@if [ -f data/resolve-watch.pid ] && kill -0 $$(cat data/resolve-watch.pid) 2>/dev/null; then echo "running (pid $$(cat data/resolve-watch.pid))"; else echo "not running"; fi
	@tail -5 logs/resolve-watch.log 2>/dev/null || true
