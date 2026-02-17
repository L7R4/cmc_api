.PHONY: up down build logs restart ps reset seed 

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build --no-cache

restart:
	docker compose restart

ps:
	docker compose ps

seed:
	docker compose exec fastapi python app/scripts/seed_local.py
