# Makefile for the zha-tools Home Assistant custom integration.
# All tooling is driven through uv.

VERSION       := 0.1.0
INTEGRATION   := zha_tools
SRC_DIR       := custom_components/$(INTEGRATION)
BUILD_DIR     := build
DIST_DIR      := dist
STAGE_DIR     := $(BUILD_DIR)/custom_components/$(INTEGRATION)
ZIP_NAME      := $(INTEGRATION)-$(VERSION).zip

.DEFAULT_GOAL := help

.PHONY: help install venv test validate run logo build dist clean distclean

help:
	@echo "Available targets:"
	@echo "  install / venv  Create the venv and install dev dependencies (uv sync)"
	@echo "  test            Run the test suite with pytest"
	@echo "  validate / run  Validate the integration"
	@echo "  logo            Rebuild the logo SVGs and brand PNGs from tools/"
	@echo "  build           Stage the integration under $(BUILD_DIR)/"
	@echo "  dist            Build $(DIST_DIR)/$(ZIP_NAME)"
	@echo "  clean           Remove $(BUILD_DIR)/ and $(DIST_DIR)/"
	@echo "  distclean       clean plus caches, venv and editor backups"

install venv:
	uv python install 3.13
	uv sync

test: install
	uv run python -m pytest

validate:
	uv run --script scripts/validate.py

run: validate

logo:
	uv run --script tools/make_logo.py

build: validate
	rm -rf $(BUILD_DIR)
	mkdir -p $(STAGE_DIR)
	cp -R $(SRC_DIR)/. $(STAGE_DIR)/
	find $(STAGE_DIR) -type d -name __pycache__ -prune -exec rm -rf {} +
	find $(STAGE_DIR) -type f -name '*.pyc' -delete

dist: build
	mkdir -p $(DIST_DIR)
	rm -f $(DIST_DIR)/$(ZIP_NAME)
	cd $(BUILD_DIR) && zip -r -q ../$(DIST_DIR)/$(ZIP_NAME) custom_components/$(INTEGRATION)
	@echo "Created $(DIST_DIR)/$(ZIP_NAME)"

clean:
	rm -rf $(BUILD_DIR) $(DIST_DIR)

distclean: clean
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type f \( -name '*~' -o -name '*.bak' \) -delete
	rm -rf .venv
