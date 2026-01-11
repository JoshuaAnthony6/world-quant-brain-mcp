SHELL := /bin/bash

.PHONY: build up up-detach logs down restart ps shell exec

build:
	docker compose build

up:
	docker compose up --build

up-detach:
	docker compose up --build -d

logs:
	docker compose logs -f

down:
	docker compose down --volumes

restart: down up-detach

ps:
	docker compose ps

shell:
	@echo "Opening shell in 'mcp' container..."
	docker compose exec mcp /bin/bash

exec:
	@echo "Run command in 'mcp' container: make exec CMD=\"python -c 'print(1)'\""
	docker compose exec mcp /bin/sh -c "$(CMD)"
