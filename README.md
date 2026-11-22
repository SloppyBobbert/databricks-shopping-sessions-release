# Shopping Sessions

An exact C++17 and PyArrow implementation of the Databricks Shopping Sessions
Buildathon challenge. It joins linked identities, deduplicates events before
validation, builds sessions, and writes user and campaign daily Parquet tables
with click attribution and assists.

Source-only release candidate prepared from reviewed source `c8948de`.
No competition submission has been made. See [LICENSE](LICENSE) and
[NOTICE](NOTICE.md) for the scope of MIT, prior work, and external materials.

## Run in Docker

Docker with Linux/amd64 support is required. The scorer CPU contract is
Cascade Lake or newer; the binary uses x86-64-v3. Apple Silicon runs this image
under emulation. `PLATFORM=linux/arm64` is not supported by this Dockerfile.
Get the official wrapper from the challenge downloads below before running it.
The timestamp-fixed source passed 36 official Docker fixtures and five focused
Docker cases. Docker and native also matched on one generated full-size input.
See the [validation record](docs/VALIDATION.md) for scope and limits.

```sh
make build
scripts/docker_run.sh /absolute/path/to/input /absolute/path/to/fresh-output
```

The supplied wrapper sets 8 CPUs and 24 GB memory, disables network and swap,
uses a read-only root and non-root user, and mounts `/data`, `/output`, and
`/scratch`. Every output table directory must be empty. Override resources for
local checks, for example `CPUS=8 MEM=7g scripts/docker_run.sh dev out-local`.
These local resource settings do not reproduce scorer hardware.

Input and output semantics are defined in the [official rules](https://www.joinbuilders.org/buildathon/tracks/shopping-sessions/files/rules.md?inline=1). Runtime Python
dependencies are pinned in [requirements.txt](requirements.txt); the container
needs no runtime downloads.

## Local development and verification

Install the upstream fixture runner as described below first: both test
targets import its Parquet helpers. With Python 3.12 or newer and a C++17
compiler, run:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make test-core       # focused regressions and 20 randomized oracle cases
make test-ubsan      # focused regressions with nonrecovering UBSan
```

`.venv/bin/python` is used automatically when present; set `PYTHON` to override it.

### Upstream challenge downloads

Get the original materials from the [Shopping Sessions track page](https://www.joinbuilders.org/buildathon/tracks/shopping-sessions). They are not redistributed here because no redistribution permission was confirmed. Extract the starter separately, then copy only its `fixtures/` and three scripts into this checkout (do not replace the project's Dockerfile, Makefile, or pipeline):

```sh
cp -R /path/to/starter-duckdb/fixtures .                 # fresh checkout
for script in run_fixtures.py compare.py docker_run.sh; do
  cp -p "/path/to/starter-duckdb/scripts/$script" scripts/
done
```

Extract `generator.zip` so `generator/Cargo.toml` is in this checkout; Rust
1.85 or newer is needed to build it. Extract `dev-answer.zip` to `dev-answer/`
for the dev comparison. Optional local copies of the rules can go in `rules.md`.

```sh
make test             # test-core, 36 official fixtures, and eight generator/oracle cases
make fixtures MEM=7g  # build the amd64 image and run official fixtures via the wrapper
```

```sh
make dev                         # public seed 1, 1M events / 250k clicks
make run-native OUT=out-native
make run-dev OUT=out-docker MEM=7g
make check-dev OUT=out-native ANSWER=/path/to/published/dev-answer
```

Downloaded challenge materials, generated datasets, benchmark outputs, logs,
binaries, virtual environments, and local agent configuration stay local.

## Implementation and audit

The native process memory-maps CSV files, resolves linked people with union-find,
and partitions exact event IDs into 32 shards. IDs up to 15 bytes are stored
inline; longer IDs retain full byte comparisons. After event deduplication it
sorts each person's events and clicks, then sweeps sessions and seven-day click
windows. Short manifest windows use dense campaign totals; broad windows use
sparse observed day/campaign pairs. Python starts PyArrow after the native
process exits and encodes Parquet DATE columns and nullable categories.

- [Validation evidence and commands](docs/VALIDATION.md)
- [Reviewer handoff and open limitations](docs/HANDOFF.md)
- [Rules-to-implementation audit](docs/SPEC_AUDIT.md)

This repository is not an official Buildathon artifact, submission, or score.
