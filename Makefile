.PHONY: health test test-all backup watch unwatch watch-status cloud-status cloud-pull

health:
	uv run swarmz health

test:
	uv run pytest -q

test-all:  ## includes integration tests (live API keys)
	uv run pytest -q -m ""

# Copies the DuckDB registry (the critical file) into backups/<date>/.
backup:
	@d=backups/$$(date +%Y%m%d-%H%M%S); mkdir -p $$d; \
	db=$${SWARMZ_DB_PATH:-data/swarmz.duckdb}; \
	uv run python -c "import duckdb,sys; duckdb.connect(sys.argv[1]).execute('CHECKPOINT')" $$db && cp $$db $$d/; \
	echo "backup written to $$d"

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

# The scheduled runs happen in the cloud (see swarmz/cloud.py). These work on the stored copy of the registry.
cloud-status:
	uv run swarmz cloud status

cloud-pull:
	uv run swarmz cloud pull
