IMAGE ?= shopping-sessions:latest
CPUS ?= 8
MEM ?= 24g
PLATFORM ?= linux/amd64
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
FIXTURES ?= fixtures
DEV ?= dev
OUT ?= out
ANSWER ?= dev-answer
GENERATOR ?= generator/target/release/generate
export IMAGE CPUS MEM PLATFORM

.PHONY: build native fixtures fixtures-native test test-core test-edges test-edges-docker test-random test-generator generator dev run-dev run-native check-dev

## Build the scorer image. The Dockerfile requires amd64 / x86-64-v3.
build:
	docker buildx build --platform $(PLATFORM) --load -t $(IMAGE) .

native: build/sessions

build/sessions: src/main.cpp
	mkdir -p build
	$(CXX) -O3 -DNDEBUG -std=c++17 -pthread $< -o $@

fixtures: scripts/run_fixtures.py scripts/docker_run.sh build
	$(PYTHON) scripts/run_fixtures.py "$(FIXTURES)" -- scripts/docker_run.sh

fixtures-native: scripts/run_fixtures.py native
	$(PYTHON) scripts/run_fixtures.py "$(FIXTURES)" -- $(PYTHON) pipeline/pipeline.py

test: fixtures-native test-core test-generator

test-core: test-edges test-random

test-edges: native
	$(PYTHON) tests/test_edges.py

.PHONY: test-ubsan
test-ubsan:
	mkdir -p build
	$(CXX) -O1 -g -std=c++17 -pthread -fsanitize=undefined -fno-sanitize-recover=undefined src/main.cpp -o build/sessions-ubsan
	SESSIONS_BINARY=$(abspath build/sessions-ubsan) $(PYTHON) tests/test_edges.py

test-edges-docker: scripts/docker_run.sh build
	PIPELINE_RUNNER=$(abspath scripts/docker_run.sh) $(PYTHON) tests/test_edges.py

test-random: native
	$(PYTHON) tests/test_differential.py 20

generator: $(GENERATOR)

$(GENERATOR): generator/Cargo.toml $(wildcard generator/Cargo.lock generator/src/*.rs)
	cargo build --release --manifest-path generator/Cargo.toml

scripts/run_fixtures.py scripts/docker_run.sh scripts/compare.py generator/Cargo.toml:
	@printf 'Missing %s. See README.md for the upstream challenge downloads.\n' "$@" >&2; exit 1

test-generator: native generator
	GENERATOR=$(abspath $(GENERATOR)) $(PYTHON) tests/test_generator.py

dev: generator
	$(GENERATOR) --seed 1 --events 1000000 --clicks 250000 --output $(DEV)

## OUT must be fresh or contain empty table directories.
run-dev: scripts/docker_run.sh build
	scripts/docker_run.sh $(DEV) $(OUT)

run-native: native
	NCPU=$(CPUS) $(PYTHON) pipeline/pipeline.py $(DEV) $(OUT)

check-dev: scripts/compare.py
	$(PYTHON) scripts/compare.py "$(OUT)" "$(ANSWER)"
