#!/usr/bin/env python3
"""Focused regressions for exact ID bytes, broad dates, and output isolation."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_fixtures import SCHEMAS, load_actual, rows
from reference import solve

ROOT = Path(__file__).resolve().parents[1]


def write_data(data, events, *, clicks=(), links=None, broad=False):
    data.mkdir()
    window = ("0001-01-01T00:00:00Z", "9999-12-31T00:00:00Z") if broad else ("2026-10-01T00:00:00Z", "2026-10-15T00:00:00Z")
    (data / "manifest.json").write_text(json.dumps(dict(zip(("window_start", "window_end"), window))))
    (data / "products.csv").write_text("product_id,category\np,猫\n")
    (data / "campaigns.csv").write_text("campaign_id,channel\nc,search\nd,social\n")
    for name, header, values in (
        ("events", "event_id,user_id,ts,ingest_ts,event_type,product_id,price_cents", events),
        ("clicks", "click_id,user_id,campaign_id,ts", clicks),
        ("links", "user_id_a,user_id_b", ["u\0,u", "é,猫"] if links is None else links),
    ):
        (data / name).mkdir()
        for shard in range(2):
            content = header + "\n" + "\n".join(values[shard::2])
            (data / name / f"{shard}.csv").write_bytes(content.encode())


def run(data, out):
    command = [os.environ["PIPELINE_RUNNER"], str(data), str(out)] if "PIPELINE_RUNNER" in os.environ else [sys.executable, str(ROOT / "pipeline/pipeline.py")]
    return subprocess.run(command,
                          env=dict(os.environ, DATA_DIR=str(data), OUTPUT_DIR=str(out), NCPU="2"),
                          capture_output=True, text=True)


def check(data, out):
    result = run(data, out)
    assert result.returncode == 0, result.stderr
    expected = solve(data)
    for table, schema in SCHEMAS.items():
        wanted = sorted(expected[table], key=lambda r: tuple("" if v is None else str(v) for v in r))
        assert rows(load_actual(out / table, schema)) == wanted, table


class EdgeTests(unittest.TestCase):
    def test_int64_timestamp_boundaries(self):
        # Exercise both numeric parser paths, including 20/25-byte zero padding.
        values = []
        for i, stamp in enumerate(("9223372036854775807", "09223372036854775807",
                                   "0000009223372036854775807", "9223372036854775808",
                                   "09223372036854775808", "0000009223372036854775808")):
            values += [f'e{i},u,2026-10-03T00:00:00Z,{stamp},view,p,',
                       f't{i},u,{stamp},2026-10-03T00:00:00Z,view,p,']
        with tempfile.TemporaryDirectory() as tmp:
            data, out = Path(tmp) / "data", Path(tmp) / "out"
            write_data(data, values)
            check(data, out)

    def test_inline_and_borrowed_ids(self):
        # 15/16 UTF-8 bytes straddle inline storage. NUL is a legal field byte;
        # these IDs must differ from empty, shorter, and zero-padded IDs.
        ids = ["x" * n for n in (7, 8, 9, 15, 16, 17, 128, 1024)]
        ids += ["\0", "\0\0", "x\0", "x", "é" * 7 + "a", "é" * 8, "猫" * 5]
        ids += ["same-prefix-12345-A", "same-prefix-12345-B"]
        values = []
        for i, event in enumerate(ids):
            # A valid winner and a later copy from a different user. Distinct
            # mmap addresses for long IDs must still compare by full value.
            values += [f'"{event}",u\0,2026-10-03T00:00:00Z,2026-10-03T00:00:00Z,purchase,p,{i+1}',
                       f'{event},wrong,2026-10-03T00:00:00Z,2026-10-03T00:00:01Z,purchase,p,999']
        values += ["invalid-winner-16,u,bad,2026-10-02T00:00:00Z,view,p,",
                   "invalid-winner-16,u,2026-10-03T00:00:00Z,2026-10-03T00:00:00Z,view,p,"]
        clicks = ["a\0,u,c,2026-10-03T00:00:00Z", "a,u,d,2026-10-03T00:00:00Z"]
        with tempfile.TemporaryDirectory() as tmp:
            data, out = Path(tmp) / "data", Path(tmp) / "out"
            write_data(data, values, clicks=clicks)
            check(data, out)

    def test_person_and_click_id_storage_boundaries(self):
        users = ["u" * 15, "u" * 16, "u" * 512, "é" * 7 + "a", "é" * 8,
                 "猫" * 5, "\0", "u\0", "\0" * 16]
        events, clicks, links = [], [], []
        for i, user in enumerate(users):
            # Canonical user remains the shorter prefix, including long ones.
            # Both linked aliases and the canonical name span separate files.
            alias, second = user + "~alias", user + "~second"
            links += [f'"{alias}","{second}"', f'"{user}","{alias}"']
            events += [f'e{i}-a,"{alias}",2026-10-03T00:00:00Z,2026-10-03T00:00:00Z,purchase,p,{i+1}',
                       f'e{i}-b,"{second}",2026-10-03T00:01:00Z,2026-10-03T00:01:00Z,view,p,']
            if i % 4 == 0:
                smaller = str(i) + "a" * 14       # 15 -> 16 bytes
            elif i % 4 == 1:
                smaller = str(i) + "é" * 7       # UTF-8 15 -> 16 bytes
            elif i % 4 == 2:
                smaller = str(i) + "a" * 512     # borrowed long values
            else:
                smaller = str(i) + "\0"         # exact NUL prefix order
            larger = smaller + "a"
            clicks += [f'"{larger}","{user}",c,2026-10-03T00:00:00Z',
                       f'"{smaller}","{alias}",d,2026-10-03T00:00:00Z',
                       f'"{smaller}","{alias}",d,2026-10-03T00:00:00Z']
        with tempfile.TemporaryDirectory() as tmp:
            data, out = Path(tmp) / "data", Path(tmp) / "out"
            write_data(data, events, clicks=clicks, links=links)
            check(data, out)

    def test_sparse_broad_manifest_and_pre_epoch(self):
        events = ["a,u,1969-12-31T23:50:00Z,1969-12-31T23:50:00Z,view,p,",
                  "b,u,1970-01-01T00:05:00Z,1970-01-01T00:05:00Z,purchase,p,25",
                  "c,u,2400-02-29T00:00:00Z,2400-02-29T00:00:00Z,purchase,p,50"]
        with tempfile.TemporaryDirectory() as tmp:
            data, out = Path(tmp) / "data", Path(tmp) / "out"
            write_data(data, events, broad=True)
            check(data, out)

    def test_empty_and_reused_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, out = Path(tmp) / "data", Path(tmp) / "out"
            write_data(data, [])
            check(data, out)
            previous = {p: p.read_bytes() for p in out.rglob("*.parquet")}
            result = run(data, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("output table directory must be empty", result.stderr)
            self.assertEqual(previous, {p: p.read_bytes() for p in previous})


if __name__ == "__main__":
    unittest.main()
