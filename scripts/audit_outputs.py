#!/usr/bin/env python3
"""Validate table schemas, unique keys, and challenge conservation invariants."""
import json
from pathlib import Path
import sys

import polars as pl
from run_fixtures import SCHEMAS, load_actual


def main():
    folder = Path(sys.argv[1])
    summary = {}
    for table, schema in SCHEMAS.items():
        frame = load_actual(folder / table, schema)
        keys = ["user_id", "day"] if table == "user_daily" else ["campaign_id", "day"]
        duplicates = frame.height - frame.n_unique(subset=keys)
        assert duplicates == 0, f"{table}: {duplicates} duplicate keys"
        for column in schema:
            if column != "top_category":
                assert frame[column].null_count() == 0, f"{table}: null {column}"
        assert frame["sessions"].min() is None or frame["sessions"].min() >= 0
        assert frame["revenue_cents"].min() is None or frame["revenue_cents"].min() >= 0
        values = {"rows": frame.height, "sessions": int(frame["sessions"].sum()),
                  "revenue_cents": int(frame["revenue_cents"].sum()), "duplicate_keys": duplicates}
        if table == "user_daily":
            assert frame.filter((pl.col("sessions") < 1) | (pl.col("events") < pl.col("sessions"))).is_empty()
            values["events"] = int(frame["events"].sum())
        else:
            assert frame.filter((pl.col("purchasing_sessions") < 0) |
                                (pl.col("purchasing_sessions") > pl.col("sessions")) |
                                (pl.col("assisted_sessions") < 0) |
                                ((pl.col("sessions") == 0) & (pl.col("assisted_sessions") == 0))).is_empty()
            organic = frame.filter(pl.col("campaign_id") == "organic")
            assert organic["assisted_sessions"].sum() == 0
            assert organic.filter(pl.col("channel") != "organic").is_empty()
            nonorganic = frame.filter(pl.col("campaign_id") != "organic")
            assert nonorganic.filter(pl.col("assisted_sessions") < pl.col("sessions")).is_empty()
            values["assisted_sessions"] = int(frame["assisted_sessions"].sum())
        summary[table] = values
        del frame
    for column in ("sessions", "revenue_cents"):
        assert summary["user_daily"][column] == summary["campaign_daily"][column], column
    print(json.dumps(summary, indent=2))
    print("PASS: schemas, keys, nonnegative counts, session/revenue conservation, and assist invariants")


if __name__ == "__main__":
    main()
