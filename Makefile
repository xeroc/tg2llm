.PHONY: help setup install uninstall auth test lint fmt check clean

-include .env
export

UV ?= uv

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Install dependencies into .venv (uv sync)
	$(UV) sync

install: ## Install tg system-wide via uv tool
	$(UV) tool install --upgrade .

uninstall: ## Remove system-wide tg
	$(UV) tool uninstall tg2llm

auth: ## One-time interactive Telegram login
	$(UV) run python tg.py auth

test: ## Run test suite
	$(UV) run pytest -q

lint: ## Run ruff checks
	ruff check tg.py test_tg.py

fmt: ## Ruff autofix
	ruff check --fix tg.py test_tg.py

check: lint test ## Everything a change must pass

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache __pycache__
