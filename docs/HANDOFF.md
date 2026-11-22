# Shopping Sessions reviewer handoff

## Resume after interruption (2026-11-21)

Both numeric timestamp paths now subtract the ASCII digit offset before addition,
preventing an intermediate signed overflow at INT64_MAX. The existing edge suite
covers INT64_MAX/MAX+1 with unpadded, 20-byte, and 25-byte forms; `make test-ubsan`
repeats this check with nonrecovering UBSan. See VALIDATION.md for resume evidence.

The recovered original task `b6c960442` **failed** its UBSan edge check;
its chained native suite never ran. The preserved log exposed invalid ingest
sentinel subtraction overflow. The current ingest guard rejects BAD before
an overflow-safe lateness comparison. See VALIDATION.md for recovered evidence
and replacement validation outcome; do not treat the original task as a pass.

The fixed `c8948de` source subsequently passed 36/36 official Docker
fixtures and five Docker edge cases. Its Docker and native runs agreed on
both logical tables for one generated full-size dataset. The candidate keeps
those runtime bytes unchanged. See VALIDATION.md for the distinction between
fixed-source results and older, inconclusive runs. Run heavy checks serially.

No competition submission has been made and no hidden dataset was accessed.

Start with [VALIDATION.md](VALIDATION.md) for commands, results, and benchmark
limits, and the [README](../README.md#upstream-challenge-downloads) for the local
setup of upstream materials.

## File map

- [Official rules](https://www.joinbuilders.org/buildathon/tracks/shopping-sessions/files/rules.md?inline=1): authoritative full challenge specification.
- `src/main.cpp`: complete C++17 data pipeline, no third-party native dependencies.
- `pipeline/pipeline.py`: manifest handling, native invocation, final Arrow/Parquet encoding.
- `Dockerfile`: amd64 x86-64-v3 two-stage build; offline, non-root runtime.
- `Makefile`, `README.md`: local native/container commands and test targets.
- `fixtures/`: 36 official fixture inputs and expected answers (obtain upstream).
- `scripts/run_fixtures.py`, `scripts/compare.py`, `scripts/docker_run.sh`: official validation/wrapper scripts (obtain upstream).
- `scripts/audit_outputs.py`: schemas, unique keys, count/revenue conservation, assist invariants.
- `tests/reference.py`: independent Python written-rules oracle, used only in tests.
- `tests/test_differential.py`: 20 adversarial randomized datasets, NCPU 1/2/8.
- `tests/test_edges.py`: focused inline/long/NUL/UTF-8 ID, broad-date, empty/reused-output tests.
- `tests/test_generator.py`: eight official-generator seeds compared against the oracle.
- `generator/`: official Rust generator source (obtain upstream).
- `docs/SPEC_AUDIT.md`: rules-to-code mapping and review caveats.
- `docs/logs/`: local validation and benchmark diagnostics, not included here.

## Architecture and why

1. Memory-map every CSV, skip exactly its first header line, and create LF-aligned
   chunks of approximately 2 MB. CSV parsing strips only balanced whole-field
   quotes, preserves CR/whitespace, and rejects malformed quotes/field counts.
2. Build a union-find from every valid link. Use union by size/path compression,
   but track the component's byte-smallest ID separately. Freeze canonical names
   into a separate vector only after all unions are finished.
3. Parse events in parallel. Retain every syntactically valid nonempty event ID,
   even if its row is invalid/late. Partition on exact ID hashes; keep the minimum
   ingest-time copy before dropping invalid winners. Validation may be computed
   early, but cannot rescue a later duplicate.
4. A 16-byte ID value stores up to 15 bytes inline and borrows an exact view for
   longer IDs. It is used for event IDs, canonical person names, and click IDs.
   Full equality resolves hash collisions. Long views remain valid because all
   input mappings outlive native computation. The length tag distinguishes NUL
   bytes, zero padding, and empty tombstones.
5. Repartition surviving events by canonical person. Each shard owns one copy of
   each user name in stable deque storage, interns it into a numeric index, and
   sorts compact events by `(person_index, timestamp)`. It never retains a view
   into a destroyed inline-ID vector. Person-shard hashing uses inline ID hashes;
   user interning hashes exact bytes independently.
6. Resolve/partition clicks by person. Identical click-ID copies remain physically
   present: they cannot change latest-click credit, and per-campaign presence
   makes them contribute one assist. Sort by person, timestamp ascending, and
   exact click ID descending. The last eligible click has the latest timestamp
   and byte-smallest ID.
7. A two-pointer seven-day click window tracks campaign multiplicities and a
   dense active campaign list. Each active campaign assists each session once.
   Events form a session while each adjacent gap is at most 30 minutes. Equal
   timestamps stay together. All events/revenue are assigned to the session's
   UTC start day; category revenue is accumulated across all that person's
   sessions starting on the day, with exact byte-order category ties.
8. Short manifest windows use flat per-shard campaign totals. When the window
   times campaign count exceeds one million, use sparse observed `(day,campaign)`
   pairs instead. There is no arbitrary manifest-day guard. Dense counters contain
   four int64 values instead of carrying unused row IDs per slot.
9. Write compact numeric aggregates and Arrow string dictionaries in a unique
   scratch subdirectory. C++ exits before PyArrow imports. Python writes DATE
   columns and masked-null categories, then removes only that owned scratch
   directory. Existing nonempty table directories are rejected before computation
   so local reruns cannot silently retain stale shards.

## Audit priorities and remaining limits

- Re-read the official rules against the code. Passing fixtures/oracles is evidence, not
  a proof. The independent oracle itself once accepted Python 3.15's `24:00:00`;
  it now explicitly rejects out-of-range clock fields. Native code already did.
- Audit the ID length tag and view lifetimes, especially short inline values
  copied between vectors and long borrowed IDs. Tests cover 15/16-byte, long,
  UTF-8, and NUL event/person/click IDs and linked aliases across files.
- Review the click-copy equivalence and same-timestamp descending-ID sort. Copies
  are guaranteed to have identical values by the input contract.
- The native process materializes raw and compact event arrays. Its peak RSS
  includes resident input mappings; cgroup memory also includes file cache and
  Python. Full-size local results and any incomplete attempt are explicitly
  separated in VALIDATION.md. Local amd64 emulation cannot establish official
  scorer timing or rank.
- The Dockerfile deliberately targets amd64 and x86-64-v3. Native arm64 development
  is supported by `make native`; an arm64 Docker build is not supported by the
  current compiler flag. Verify actual scorer CPU guarantees before changing it.
- Native dictionary offsets are Arrow string int32, and user/category indices
  are uint32. The implementation rejects oversized dictionaries rather than
  silently truncating them. These bounds exceed supplied generator requirements.
- Keep output directories fresh. The wrapper does not delete prior user files;
  Python fails clearly if an output table directory is nonempty.
- No full-size independent published expected answer exists locally. Full-size
  native/Docker agreement and invariants must be distinguished from the public
  1M exact-answer comparison.

## Reproduce before review handback

Run `make test-core` and `make test-ubsan` on a fresh clone. With the original
challenge files installed as documented in the README, use `make test` and
`make fixtures MEM=7g` once you have exclusive access to the heavy-work slot.
See [VALIDATION.md](VALIDATION.md) for the fixed-image verification plan and
benchmark commands.
