"""Run every test file in small chunks, one after another, and print one summary.

The database tests share one scratch database, so chunks must run in series, never side by side.
A chunk keeps memory use low and shows where a failure is. New test files are picked up
automatically.

    python scripts/test_all.py            # all chunks
    python scripts/test_all.py --size 2   # smaller chunks
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def clean_scratch_database() -> None:
    """Remove the scratch database and role a killed earlier run may have left behind."""
    sys.path.insert(0, str(ROOT))
    from tests.conftest import API_DB, API_ROLE
    from tests.dbutil import ADMIN_URL, drop_database

    if ADMIN_URL:
        drop_database(API_DB, API_ROLE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=4, help="test files per chunk")
    args = parser.parse_args()
    files = sorted(p.name for p in (ROOT / "tests").glob("test_*.py"))
    chunks = [files[i:i + args.size] for i in range(0, len(files), args.size)]
    clean_scratch_database()
    totals = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    failed_chunks: list[int] = []
    for number, chunk in enumerate(chunks, start=1):
        started = time.time()
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             *[f"tests/{name}" for name in chunk]],
            cwd=ROOT, capture_output=True, text=True)
        tail = [ln for ln in run.stdout.splitlines() if re.search(r"\d+ (passed|failed|error)", ln)]
        line = tail[-1] if tail else "no result line"
        for key in totals:
            found = re.search(rf"(\d+) {key}", line)
            totals[key] += int(found.group(1)) if found else 0
        mark = "ok  " if run.returncode == 0 else "FAIL"
        print(f"[{mark}] chunk {number}/{len(chunks)} ({time.time() - started:4.0f}s) "
              f"{', '.join(n.removeprefix('test_').removesuffix('.py') for n in chunk)}: {line}",
              flush=True)
        if run.returncode != 0:
            failed_chunks.append(number)
            print("\n".join(ln for ln in run.stdout.splitlines()
                            if ln.startswith(("FAILED", "ERROR"))), flush=True)
    print(f"\nTOTAL: {totals['passed']} passed, {totals['failed']} failed, "
          f"{totals['error']} errors, {totals['skipped']} skipped in {len(chunks)} chunks")
    print("ALL GREEN" if not failed_chunks else f"FAILED chunks: {failed_chunks}")
    return 1 if failed_chunks else 0


if __name__ == "__main__":
    raise SystemExit(main())
