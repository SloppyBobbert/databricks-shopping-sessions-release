#!/usr/bin/env python3
"""Adversarial randomized comparisons against an independent datetime-based oracle."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_fixtures import SCHEMAS, load_actual, rows as sorted_rows
from reference import solve

ROOT = Path(__file__).resolve().parents[1]


def make_case(path, seed):
    rng = random.Random(seed)
    begin = datetime(2028, 2, 26, tzinfo=timezone.utc)
    epoch = int(begin.timestamp()) * 1000
    day = 86400000
    for directory in ("events", "clicks", "links"):
        (path / directory).mkdir()
    (path / "manifest.json").write_text(json.dumps({"window_start": begin.isoformat().replace("+00:00", "Z"), "window_end": (begin + timedelta(days=14)).isoformat().replace("+00:00", "Z")}))
    (path / "products.csv").write_text("product_id,category\np1,books\np2,shoes\np3,é\np4,猫\n")
    (path / "campaigns.csv").write_text("campaign_id,channel\nc1,search\nc2,social\nc3,search\nc4,email\n")
    users = ["u1", "u2", "u3", "u4", "U1", " u1", "u1 ", "é", "猫", "u\t1"]

    def encode(fields):
        return ",".join('"' + f + '"' if rng.random() < .5 else f for f in fields)

    def stamp(value):
        form = rng.randrange(5)
        if form < 2 or value % 1000:
            return "0" * rng.randrange(12) + str(value)
        dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        if form == 2:
            dt = dt.astimezone(timezone(timedelta(minutes=rng.choice([-1439, -240, 0, 345, 1439]))))
            return dt.isoformat(timespec="seconds")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    links = [encode([users[i], users[(i + 1) % 4]]) for i in range(3)]
    # Long, shuffled chains with a name unused by any event; union root order
    # differs from both lexical order and the order of discovery.
    chain = [f"chain-{i:03d}" for i in range(400)] + ["!unused", "u4"]
    links += [encode([a, b]) for a, b in zip(chain, chain[1:])]
    links += ["u2,", ",u3", 'u1,u5"', "u1,u2,u3", '"u1"junk,u8', 'u1,"u9"\r', "", "u1,u1"]
    rng.shuffle(links)
    events = []
    bad_ts = ["2028-02-30T00:00:00Z", "2027-02-29T00:00:00Z", "2028-02-28T24:00:00Z", "2028-02-28T00:00:60Z", "2028-02-28T10:00:00+24:00", "2028-02-28T10:00:00+05:60", "١٧٩١٠٧١٨٩٠٠٠٠", "9" * 50, "0" * 50 + str(epoch), "2028-02-28T00:00:00Z\r", "2028-02-28T00:00:00z", "2028-02-28t00:00:00Z", "", "1.79e12", "-5"]
    for i in range(2500):
        when = epoch + rng.randrange(15 * day) - day
        when = when // 1800000 * 1800000 + rng.choice([0, 1, 999, 1800000, 1800001])
        ingest = when // 1000 * 1000 + rng.choice([-1000, 0, day, day + 1000, rng.randrange(300000)])
        fields = [f"e{i}", rng.choice(users + [""]), stamp(when), stamp(ingest), rng.choice(["view", "view", "cart", "purchase", "purchase", "Purchase", "view "]), rng.choice(["p1", "p2", "p3", "p4", "p9", ""]), rng.choice(["1", "000250", "1000", "10000000", "0", "10000001", "+2", "2.00", "２５０", "٢٥٠", "10\r", ""])]
        fields[3] = datetime.fromtimestamp(ingest / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if rng.random() < .12:
            fields[2] = rng.choice(bad_ts)
        events.append(encode(fields))
        if rng.random() < .2:
            duplicate = fields.copy()
            duplicate[3] = datetime.fromtimestamp(ingest / 1000 + 10, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            duplicate[1] = rng.choice(users)
            duplicate[2] = stamp(when)
            events.append(encode(duplicate))
    events += ["", "bad,row", 'e,u1,1,2,view,p1,x"', 'e,u1,1,2,view,p1,"x"\r']
    clicks = []
    for i in range(1000):
        when = epoch + rng.randrange(22 * day) - 7 * day
        fields = [f"k{i}", rng.choice(users + [""]), rng.choice(["c1", "c2", "c3", "c4", "c9", " c1"]), rng.choice(bad_ts) if rng.random() < .1 else stamp(when)]
        clicks.append(encode(fields))
        if rng.random() < .15:
            clicks.append(encode(fields))
    # Exact seven-day and 1 ms boundaries, same-time credit ties, and CR fields.
    boundary = epoch + 3 * day
    for i, delta in enumerate([-7 * day - 1, -7 * day, 0, 1]):
        clicks.append(encode([f"boundary-{i}", "solo", f"c{i+1}", str(boundary + delta)]))
    for click, campaign in [("a-tie", "c2"), ("z-tie", "c1")]:
        clicks.append(encode([click, "solo", campaign, str(boundary)]))
    events.append(encode(["boundary-event", "solo", str(boundary), "2028-02-29T00:00:00Z", "purchase", "p1", "1000"]))
    for directory, values, header in [("links", links, "user_id_a,user_id_b"), ("events", events, "event_id,user_id,ts,ingest_ts,event_type,product_id,price_cents"), ("clicks", clicks, "click_id,user_id,campaign_id,ts")]:
        rng.shuffle(values)
        parts = [values[i::7] for i in range(7)] + [[]]
        for i, part in enumerate(parts):
            raw = header + "\n" + "\n".join(part)
            if i % 2:
                raw += "\n"
            (path / directory / f"part-{i:02d}.csv").write_bytes(raw.encode())


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    for seed in range(count):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            data, out = path / "data", path / "out"
            data.mkdir()
            make_case(data, seed)
            expected = solve(data)
            env = dict(os.environ, DATA_DIR=str(data), OUTPUT_DIR=str(out), NCPU=str([1, 2, 8][seed % 3]))
            subprocess.run([sys.executable, str(ROOT / "pipeline/pipeline.py")], env=env, check=True, capture_output=True)
            for table, schema in SCHEMAS.items():
                actual = sorted_rows(load_actual(out / table, schema))
                wanted = sorted(expected[table], key=lambda r: tuple("" if v is None else str(v) for v in r))
                assert actual == wanted, f"seed {seed}: {table} differs\nactual {actual[:4]}\nwanted {wanted[:4]}"
        print(f"ok seed {seed}", flush=True)
    print(f"{count} randomized differential cases passed")


if __name__ == "__main__":
    main()
