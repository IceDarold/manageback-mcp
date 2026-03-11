SHELL := /bin/bash

.PHONY: db-up db-down db-logs db-wait

db-up:
	docker compose up -d mysql

db-down:
	docker compose down

db-logs:
	docker compose logs -f mysql

db-wait:
	set -a; source .env; set +a; python3 scripts/wait_for_mysql.py
