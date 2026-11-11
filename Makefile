.PHONY: up test down logs rebuild

up:
	./scripts/prepare-env.sh
	docker compose up --build

test:
	./scripts/prepare-env.sh
	docker compose up -d postgres redis
	@rev=$$(git rev-parse --short HEAD 2>/dev/null || true); \
	dirty=true; \
	if [ -n "$$rev" ]; then \
	  if [ -z "$$(git status --porcelain 2>/dev/null)" ]; then dirty=false; else dirty=true; fi; \
	else rev=unknown; dirty=true; fi; \
	docker compose run --rm --no-deps \
	  -e REQUIRE_WORKERS=false \
	  -e CODE_REVISION=$$rev \
	  -e CODE_TREE_DIRTY=$$dirty \
	  api pytest

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

rebuild:
	docker compose build --no-cache
	docker compose up -d
