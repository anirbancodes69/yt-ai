# Every target runs inside Docker. The host needs only Docker.
RUN := docker compose run --rm app

.PHONY: build shell test lint format check demo sample-data lock clean

build:
	docker compose build

shell: build
	$(RUN) bash

test: build
	$(RUN) pytest

lint: build
	$(RUN) sh -c "ruff check . && ruff format --check ."

format: build
	$(RUN) sh -c "ruff format . && ruff check --fix ."

check: build
	$(RUN) sh -c "ruff check . && ruff format --check . && pytest"

demo: build
	$(RUN) sh -c "channel validate && channel demo"

sample-data: build
	$(RUN) python tools/gen_sample_data.py

# Re-resolve uv.lock after editing dependencies in pyproject.toml.
lock:
	$(RUN) uv lock

clean:
	rm -rf out .pytest_cache .ruff_cache
