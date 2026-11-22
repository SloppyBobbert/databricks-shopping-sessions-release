# Validation record

## Interruption resume: timestamp boundary (2026-11-21)

The two numeric timestamp paths used `v*10+c-'0'`; even with the bound
check, the intermediate addition overflows for INT64_MAX. Both now use
`v*10+(c-'0')`. The existing edge tests exercise MAX and MAX+1, including
20/25-byte leading-zero forms, as event and ingest timestamps. `make test-ubsan`
provides a repeatable nonrecovering UBSan check without a new test harness.

The historical 40.792 s Docker run did not establish fixed-source image
identity. Use the completed fixed-image record below instead. With the
[upstream materials](../README.md#upstream-challenge-downloads) installed,
run these commands serially with fresh output directories:

```sh
set -eu
make build IMAGE=shopping-sessions:resume-fixed
IMAGE=$(docker image inspect --format '{{.Id}}' shopping-sessions:resume-fixed)
printf 'Fixed image ID: %s\n' "$IMAGE"
export IMAGE
stamp=$(date +%Y%m%d-%H%M%S)
MEM=7g CPUS=8 PIPELINE_RUNNER="$PWD/scripts/docker_run.sh" .venv/bin/python tests/test_edges.py
MEM=7g CPUS=8 .venv/bin/python scripts/run_fixtures.py fixtures -- scripts/docker_run.sh
MEM=7g CPUS=8 scripts/docker_run.sh benchmarks/final "out-resume60m-docker-$stamp"
NCPU=8 .venv/bin/python pipeline/pipeline.py benchmarks/final "out-resume60m-native-$stamp"
.venv/bin/python scripts/compare.py "out-resume60m-native-$stamp" "out-resume60m-docker-$stamp"
```

Use Docker Desktop's PATH prefix from the environment section if necessary.
Run the edge script directly: `make test-edges-docker` depends on `build` and
would attempt another build rather than just testing the captured image ID.
The completed fixed-image run used the official wrapper with disabled network,
read-only root and input, uid/gid 1000, dropped capabilities, no new privileges,
8 CPUs, and a 7 GiB memory limit with no swap.

No competition submission was made. Local measurements are not official scorer
results, leaderboard scores, or evidence of performance on hidden seeds.

## Recovered terminal evidence (2026-11-21)

Task `b6c960442` **failed, exit 1**. Its empty task output was not a successful
check. The local `docs/logs/resume-timestamp-20261121-123408.log` records UBSan signed overflow
at the ingest lateness subtraction (`INT64_MIN - 1790985600000`); the
chained `make test` was never reached. The ingest guard now rejects BAD
and uses an ordered unsigned difference, retaining acceptance of earlier
ingest times without signed overflow. The existing boundary regression
reproduces this failure. Replacement task `b6ebebc6c` completed in 25 seconds,
exit 0: `make test-ubsan` passed all five focused cases; `make test` passed
36/36 fixtures, five focused cases, 20 differential seeds, and eight official
generator/oracle seeds. Evidence is in the local log
`docs/logs/resume-native-20261121-124120.log`; raw logs and benchmark artifacts
are not included in this repository. Tested `src/main.cpp` SHA-256:
`c68a152f2f4689b7b94c9c5b04c14698583aae60b641bdee8f24765fdc978818`.

No optimized image build, Docker verification, 60M benchmark, or full native
comparison was launched in that replacement run.

## Environment and commands

Local date: 2026-11-21. Host: macOS, Apple Silicon (`Darwin arm64`), 16 GB RAM, 10 CPU cores. Docker
Desktop Linux VM exposes approximately 8.3 GB. Final container runs use
Linux/amd64 emulation, 8 CPUs, and a 7 GB memory/no-swap limit, below the 24 GB
scorer contract. Large runs are serialized with the other Buildathon tracks.

Local Python: 3.15.2; NumPy 2.5.5; PyArrow 26.0.1; Polars 2.1.0. Runtime image:
Python 3.12 on Debian Bookworm, pinned NumPy/PyArrow. Native binary: C++17,
`-O3 -DNDEBUG -pthread`; Docker additionally uses `-march=x86-64-v3`.

```sh
make test
PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make build
PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH MEM=7g \
  .venv/bin/python scripts/run_fixtures.py fixtures -- scripts/docker_run.sh
NCPU=8 .venv/bin/python pipeline/pipeline.py dev out-dev-final-native
.venv/bin/python scripts/compare.py out-dev-final-native dev-answer
PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH MEM=7g \
  scripts/docker_run.sh dev out-dev-final-docker
.venv/bin/python scripts/compare.py out-dev-final-docker dev-answer
```

The Docker Desktop PATH supplies the client and its credential helper.

`make test` covers 36 untouched official fixtures; five focused regression cases;
20 adversarial random datasets with NCPU cycling through 1, 2, and 8; and eight
official-generator datasets (15,000 events / 4,000 clicks each) compared against
the independent Python rules oracle. Generator seeds: 0, 1, 42, 20261121,
8675309, 4294967295, 123456789, and 8888.

Focused coverage includes 7/8/9/15/16-byte and 1,024-byte event IDs, UTF-8 and NUL
bytes, exact dedup across different mappings, earlier-invalid duplicate winners,
15/16-byte/long/UTF-8/NUL person and click IDs, linked aliases across files,
same-time click ties, click copies, empty outputs, safe rejection of output
reuse, pre-epoch/cross-midnight sessions, leap year 2400, and a manifest from year
0001 to 9999. Random cases add malformed/quoted CSV, whitespace and CR, 400-link
chains, millisecond boundaries, all timestamp forms, and varied input file counts.

## Completed fixed-image evidence (2026-11-21)

The Linux/amd64 image built from `c8948de` passed five Docker edge cases,
36/36 official fixtures, and one full-size run (seed `8675309`, 60M events and
15M clicks as generator arguments). A fresh optimized native arm64 build of
that source processed the same inputs. Docker took 28.404 s pipeline time /
28.90 s wrapper wall time; native took 19.105 s pipeline time / 19.18 s wall time.
Docker reached its local 7 GiB cgroup cap (including file cache) but completed
without an OOM. Child peak RSS was 7,126.7 MiB in Docker and 5,798.7 MiB on the host.

Input SHA-256 manifests before Docker and after native matched. Both output
audits passed, and the official comparator found no unmatched or duplicate rows:
6,614,000 `user_daily` rows and 28,014 `campaign_daily` rows per run, with
9,989,046 sessions and 19,864,155,305 revenue cents. Canonical table checksums
matched; Parquet file bytes did not. The raw logs, full input/output manifests,
and private repository are not redistributed here. The candidate keeps the
runtime/build inputs from `c8948de` byte-identical.

## Full-size dataset and incomplete baseline

```sh
generator/target/release/generate --seed 8675309 \
  --events 60000000 --clicks 15000000 --output benchmarks/final
PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH MEM=7g \
  scripts/docker_run.sh benchmarks/final out-final-docker
```

The official generator input occupies approximately 5.3 GiB locally. It is
ignored by Git. Event/click counts above are generator arguments; dirt may add
or drop physical CSV rows.

The first full-size Docker attempt was explicitly stopped during aggregation
after observing sustained file-page reclaim. It completed links at 3.66697 s,
parsing at 33.6421 s, and deduplication at 98.9085 s (cumulative native times).
Observed cgroup memory peak was exactly 7 GiB, with no OOM or OOM kill. It has no
complete runtime or correctness result. Its container termination was confirmed
before the replacement run. Details are in the local `full60m-docker-initial.log`
and `full60m-docker-initial-resources.log`.

The replacement extends inline IDs from event IDs to person/click IDs and owns
one copy of each output user name. This avoids repeated random CSV mapping
access during dedup repartitioning, sorting, and attribution.

## Prior-phase evidence

The preceding implementation phase recorded 36/36 native and Docker fixture
passes, exact public-dev agreement (117,038 user / 27,400 campaign rows), and 20
random-oracle cases. Its seed-20261121 20M-event / 5M-click run produced matching
native and Docker outputs (2,240,971 user / 28,014 campaign rows). Recorded total
times were native 18.763 s, emulated Docker 17.697 s, and native 13.293 s after the
first inline-event-ID optimization, with native peak RSS 3,494.3 MiB on that last
run. These historical measurements were passed in the implementation handoff;
the final source has additional changes and is validated separately above.

The first `/usr/bin/time -l` command returned 1 because the macOS sandbox could
not read a kernel clock statistic, although the pipeline completed. Production
now reports its child's peak RSS with Python `resource.getrusage`; this excludes
Python's later Parquet encoding and differs from cgroup memory (which includes
file cache and all container processes).

## Limits of this evidence

Fixtures, the independent oracle, and the public published answer validate
specific inputs. Full-size conservation checks and native/Docker agreement do
not provide an independent full-size expected answer. Only the competition's
scorer can establish hidden correctness, runtime on its hardware, or ranking.

## Focused memory-safety check

```sh
c++ -O1 -g -std=c++17 -pthread -fsanitize=address,undefined \
  -fno-omit-frame-pointer src/main.cpp -o build/sessions-asan
SESSIONS_BINARY="$PWD/build/sessions-asan" .venv/bin/python tests/test_edges.py
```

All four focused regressions passed under AddressSanitizer and
UndefinedBehaviorSanitizer; see the local `docs/logs/sanitizer-edges.log`.
This check specifically exercises the inline/borrowed string lifetime changes.
It does not sanitize the release Docker image or prove absence of all UB.
