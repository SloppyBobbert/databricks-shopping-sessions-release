# Rules-to-implementation audit

Read the [official rules](https://www.joinbuilders.org/buildathon/tracks/shopping-sessions/files/rules.md?inline=1) as the authoritative source. Function names below refer to `src/main.cpp` unless otherwise specified.

## Input, CSV, and strings

`load` enumerates all `.csv` files, skips the first LF-delimited line of each file, preserves final lines without LF, handles header-only files, and creates line-aligned chunks. `csv<N>` enforces exactly N fields. It strips only one balanced pair of whole-field quotes and rejects every remaining quote. It does not trim whitespace, normalize case, recognize CR as a line ending, or use locale digit predicates. `SV` equality and lexicographic comparison use exact bytes. Coverage: E13, E15, E18–E20, L3; random tests spread records across seven files plus a header-only eighth file, alternating files with/without final LF.

## Timestamps and windows

`timestamp` implements strict 20-byte F1 / 25-byte F2 syntax, ASCII positions, leap/calendar validation, hour/minute/second bounds, offset bounds and subtraction, and checked digit-only F3 parsing with leading zeros. `days_from_civil` computes the epoch date without locale/timezone state. Events use `[start,end)`; clicks use `[start-WEEK,end)`. `floor_day` handles pre-epoch negative dates. Coverage: E2, E7, E12, E17–E18, E20, A2, A4, A10; randomized leap dates, fractional-millisecond boundaries, Unicode digits, and overlong F3 values.

## Linked people

`Identities::join` interns exact strings, joins every remaining nonempty link side transitively, tolerates duplicates/self links, and tracks each component's smallest byte name. `finish` computes names without mutating the union tree while traversing it. `canonical` defaults to the original name for an ID absent from links. All links are consumed before parsing events/clicks. Coverage: L1–L4, E11, A7, A12; randomized 400-edge shuffled chains, unused smallest ID, multiple files.

## Event ordering and validation

The parser immediately removes malformed rows/empty event IDs. Every remaining ID gets a RawEvent even if validation fails. A hash partition and exact-key table select minimum ingest time across all copies/users/files. Only then is a BAD timestamp winner discarded. Valid rows require nonempty user/product, exact view/cart/purchase types, in-window timestamps, and ingest minus timestamp at most one day. `price` applies ASCII digits and the inclusive 1..10,000,000 range only to purchases; view/cart ignore the field. Missing product lookup becomes unknown. Coverage: E4–E6, E9–E13, E18–E20; random earlier-invalid/later-valid copies, cross-user copies, and all price forms.

## Sessions and user_daily

After canonical person partitioning, numeric person IDs and millisecond timestamps determine event ordering. Session scan uses `next.ts - previous.ts <= GAP`, so equal timestamps cannot split. Session day is computed before consuming that session. Every event and revenue/category contribution is added to that start day. The per-user day accumulator counts sessions/events, sums purchase revenue, and picks the maximum purchase category sum, byte-smallest on tie, or null without purchases. Coverage: E1–E3, E8–E9, E14, E16, L1; random exact gap boundaries and cross-midnight chains.

## Clicks, credit, assists, and campaign_daily

Valid click rows require nonempty click/user, known campaign, and in-window strict timestamp. Identical duplicates remain physically present but do not affect outputs: credit picks a value from identical copies, and assists use positive per-campaign presence, not multiplicity. Sort is `(person, time ascending, click ID descending)`. Before crediting a session, sweep inserts clicks with `time <= start` and removes clicks with `time < start-WEEK`. Thus latest eligible record resolves time ties by smallest click ID, or organic if none. The dense active campaign set increments each campaign once. All credited and assisted-only daily rows are retained; only rows with no sessions and no assists are dropped. Organic is inserted only as the no-click campaign and never appears in the active set. Coverage: A1–A12, L4; random campaign repeats, exact time/seven-day boundaries, same-time click IDs, and identical click copies.

## Parquet and runtime contract

`pipeline/pipeline.py` writes exactly the two named table schemas, DATE (Arrow date32) days, integer counts/revenue, string dictionary IDs/categories, and masked-null top categories. It always emits a user file even for zero events, and emits a schema-correct campaign file even for zero campaigns/sessions. Native diagnostics and Python timings go to stderr. Scratch uses a unique owned subdirectory and is cleaned. Docker starts uid 1000, has no run-time downloads, and the official wrapper applies network=none, read-only root, 64 MB /tmp, process/thread, CPU, memory/no-swap flags. Official fixture checker verifies schemas and full rows; public dev comparer additionally checks unique table keys.

## What the evidence does not establish

The public answer verifies one dataset, and randomized cases verify only their generated inputs. The full-size local run measures one seed under emulation and checks agreement across two builds, not independent ground truth. These checks do not establish official hidden performance, scorer ranking, or exhaustive correctness.

## Final implementation review notes

`EventId` now also stores canonical person names and click IDs, with inline
storage through 15 bytes and exact borrowed views beyond that. Its view of a
short ID points into the ID object itself. Such views must never outlive that
object. Per-shard `Strings users` enables `own_keys`, copying each distinct name
once into stable `std::deque<std::string>` storage before releasing named-event
vectors. Clicks retain ID values, rather than views into inline IDs in destroyed
vectors. All long borrowed values remain backed by live input mappings. The
focused suite checks short/long/UTF-8/NUL values for all three kinds of ID,
linked aliases from different input files, and same-time click ordering.

Person sharding uses `EventId::hash`, whereas user interning uses `hashstr` on
its exact view. These are different hashes, so the old five-bit shift is no
longer applied to per-person interning. Event dedup still discards the five
bits already consumed by its 32-way partition.

The arbitrary 100,000-day guard was removed. `CampaignTotals` uses four int64
counters per dense cell when `days * campaigns <= 1,000,000`; otherwise a map
contains only observed day/campaign pairs. A focused oracle case spans years
0001 through 9999, includes pre-epoch and cross-midnight sessions and leap day
2400, and forces the sparse branch.

Python now rejects nonempty output table directories before invoking native
code. This enforces the scorer's empty-output assumption for local reuse and
prevents a missing new shard from leaving old rows behind. The empty-output
regression verifies correct zero-row schemas, a clear error on reuse, and
unchanged prior output bytes.

See VALIDATION.md for current final-image and full-size evidence; earlier
statements about missing measurements are superseded only by the completed
runs recorded there. No local observation is an official score.
