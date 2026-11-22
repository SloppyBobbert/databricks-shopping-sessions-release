#!/usr/bin/env python3
"""Run the native exact pipeline, then encode its compact aggregates as Parquet."""
import concurrent.futures
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time


def main():
    started = time.monotonic()
    data = Path(os.environ.get("DATA_DIR", sys.argv[1] if len(sys.argv) > 1 else "/data"))
    output = Path(os.environ.get("OUTPUT_DIR", sys.argv[2] if len(sys.argv) > 2 else "/output"))
    # Fail before computing if an old run would contaminate this run's tables.
    # The scorer supplies empty output directories; local callers must do so too.
    for table in ("user_daily", "campaign_daily"):
        folder = output / table
        if folder.is_symlink() or (folder.exists() and any(folder.iterdir())):
            raise ValueError(f"output table directory must be empty: {folder}")
    scratch = Path(os.environ.get("SCRATCH_DIR", tempfile.gettempdir()))
    scratch.mkdir(parents=True, exist_ok=True)
    with (data / "manifest.json").open() as stream:
        manifest = json.load(stream)
    binary = os.environ.get("SESSIONS_BINARY", str(Path(__file__).resolve().parents[1] / "build" / "sessions"))
    with tempfile.TemporaryDirectory(prefix="sessions-", dir=scratch) as work:
        work = Path(work)
        subprocess.run([binary, str(data), str(work), manifest["window_start"], manifest["window_end"]], check=True)
        peak_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        peak_bytes = peak_rss if sys.platform == "darwin" else peak_rss * 1024
        print(f"native peak RSS: {peak_bytes / (1024 ** 2):.1f} MiB", file=sys.stderr)
        # Import after the native child exits, so Arrow's memory and thread pool do
        # not overlap the CSV parser's peak allocation.
        import numpy as np
        import pyarrow as pa
        import pyarrow.parquet as pq

        pa.set_cpu_count(int(os.environ.get("NCPU", "8")))

        def dictionary(path):
            raw = pa.memory_map(str(path)).read_buffer()
            count = int.from_bytes(raw.slice(0, 4), "little")
            offset_bytes = (count + 1) * 4
            return pa.Array.from_buffers(pa.string(), count, [None, raw.slice(4, offset_bytes), raw.slice(4 + offset_bytes)])

        categories = dictionary(work / "categories.dict")
        campaigns = dictionary(work / "campaigns.dict")
        channels = dictionary(work / "channels.dict")

        def indexed(indices, values, nullable=False):
            return pa.DictionaryArray.from_arrays(pa.array(indices.astype(np.int32), mask=(indices < 0) if nullable else None), values)

        user_dir, campaign_dir = output / "user_daily", output / "campaign_daily"
        user_dir.mkdir(parents=True, exist_ok=True)
        campaign_dir.mkdir(parents=True, exist_ok=True)

        def write_users(shard):
            rows = np.fromfile(work / f"users-{shard}.bin", dtype="<i8").reshape(-1, 6)
            if len(rows) == 0 and shard != 0:
                return
            names = dictionary(work / f"users-{shard}.dict")
            table = pa.table({
                "user_id": indexed(rows[:, 0], names),
                "day": pa.array(rows[:, 1].astype(np.int32), type=pa.date32()),
                "sessions": pa.array(rows[:, 2]),
                "events": pa.array(rows[:, 3]),
                "revenue_cents": pa.array(rows[:, 4]),
                "top_category": indexed(rows[:, 5], categories, nullable=True),
            })
            pq.write_table(table, user_dir / f"part-{shard:02d}.parquet", compression="snappy", use_dictionary=["user_id", "top_category"], write_statistics=False)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, int(os.environ.get("NCPU", "8")))) as pool:
            list(pool.map(write_users, range(32)))
        rows = np.fromfile(work / "campaigns.bin", dtype="<i8").reshape(-1, 6)
        table = pa.table({
            "campaign_id": indexed(rows[:, 0], campaigns),
            "channel": indexed(rows[:, 0], channels),
            "day": pa.array(rows[:, 1].astype(np.int32), type=pa.date32()),
            "sessions": pa.array(rows[:, 2]),
            "purchasing_sessions": pa.array(rows[:, 3]),
            "revenue_cents": pa.array(rows[:, 4]),
            "assisted_sessions": pa.array(rows[:, 5]),
        })
        pq.write_table(table, campaign_dir / "part-0.parquet", compression="snappy", write_statistics=False)
    if sys.platform.startswith("linux"):
        try:
            peak = int(Path("/sys/fs/cgroup/memory.peak").read_text())
            print(f"cgroup peak memory (includes file cache): {peak / (1024 ** 2):.1f} MiB", file=sys.stderr)
        except (OSError, ValueError):
            pass  # cgroup v1 or a host that does not expose this statistic
    print(f"total: {time.monotonic() - started:.3f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
