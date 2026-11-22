#!/usr/bin/env python3
"""Check small, varied official-generator datasets against the Python oracle."""
from pathlib import Path
import os
import subprocess
import tempfile

from test_edges import check

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = Path(os.environ.get("GENERATOR", ROOT / "generator/target/release/generate"))


def main():
    seeds = [0, 1, 42, 20261121, 8675309, 4294967295, 123456789, 8888]
    for seed in seeds:
        with tempfile.TemporaryDirectory() as tmp:
            data, out = Path(tmp) / "data", Path(tmp) / "out"
            subprocess.run([str(GENERATOR), "--seed", str(seed), "--events", "15000", "--clicks", "4000", "--output", str(data)], check=True, capture_output=True)
            check(data, out)
        print(f"ok official-generator seed {seed}", flush=True)
    print(f"{len(seeds)} official-generator oracle cases passed")


if __name__ == "__main__":
    main()
