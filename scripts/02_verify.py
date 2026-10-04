#!/usr/bin/env python3
"""Check every file under data/raw/ against the MANIFEST.json beside it.

For each MANIFEST.json found under data/raw/:
  - every file it lists is there, with its size and sha256
  - no other file is there (a leftover .part, say)
  - a .zip passes zipfile.testzip, and holds the members the catalogue listed
    for it, if it listed any
  - a .csv.gz is read to the end (so gzip checks its CRC and length)
  - every CSV, plain, gzipped or inside a zip, is parsed with the csv module,
    every row has as many fields as the header, and its rows are counted

Prints one ok/FAIL line per check and exits non-zero if any failed.

    uv run python scripts/02_verify.py
"""

import csv
import gzip
import hashlib
import io
import json
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
WORKERS = 4

csv.field_size_limit(1 << 24)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def count_rows(binary) -> tuple[int, int, list[str]]:
    """(data rows, rows whose field count differs from the header, header)."""
    reader = csv.reader(io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""))
    header = next(reader)
    rows = ragged = 0
    for row in reader:
        rows += 1
        if len(row) != len(header):
            ragged += 1
    return rows, ragged, header


def check_file(path: Path, entry: dict) -> list[tuple[bool, str]]:
    name = f"{path.relative_to(RAW)}"
    out = []
    if not path.exists():
        return [(False, f"{name}: missing")]
    size = path.stat().st_size
    out.append((size == entry["bytes"], f"{name}: {size:,} bytes"))
    out.append((sha256(path) == entry["sha256"], f"{name}: sha256"))
    try:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
                out.append((bad is None, f"{name}: zip test" + (f" (bad: {bad})" if bad else "")))
                members = sorted(n for n in z.namelist() if not n.endswith("/"))
                listed = entry.get("members_listed")
                if listed is not None:
                    base = sorted(Path(n).name for n in members)
                    out.append(
                        (base == sorted(listed), f"{name}: {len(members)} members as listed")
                    )
                for n in members:
                    if n.lower().endswith(".csv"):
                        with z.open(n) as f:
                            rows, ragged, header = count_rows(f)
                        out.append(
                            (
                                ragged == 0,
                                f"{name}:{n}: {rows:,} rows, {len(header)} columns, {ragged} ragged",
                            )
                        )
        elif path.name.endswith(".csv.gz"):
            with gzip.open(path, "rb") as f:
                rows, ragged, header = count_rows(f)
            out.append(
                (ragged == 0, f"{name}: gzip read to end, {rows:,} rows, {len(header)} columns")
            )
        elif path.suffix == ".csv":
            with open(path, "rb") as f:
                rows, ragged, header = count_rows(f)
            out.append((ragged == 0, f"{name}: {rows:,} rows, {len(header)} columns"))
    except (OSError, EOFError, zipfile.BadZipFile, UnicodeDecodeError, csv.Error) as e:
        out.append((False, f"{name}: {type(e).__name__}: {e}"))
    return out


def main() -> int:
    manifests = sorted(RAW.rglob("MANIFEST.json"))
    if not manifests:
        print("FAIL  no MANIFEST.json under data/raw/")
        return 1
    failures = []

    def report(ok: bool, what: str) -> None:
        print(("ok    " if ok else "FAIL  ") + what, flush=True)
        if not ok:
            failures.append(what)

    jobs = []
    for mpath in manifests:
        d = mpath.parent
        files = json.loads(mpath.read_text())["files"]
        report(bool(files), f"{d.relative_to(RAW)}: {len(files)} files in MANIFEST.json")
        extra = sorted(
            p.name
            for p in d.iterdir()
            if p.is_file() and p.name != "MANIFEST.json" and p.name not in files
        )
        report(not extra, f"{d.relative_to(RAW)}: no files outside the manifest {extra or ''}")
        jobs += [(d / n, e) for n, e in files.items()]

    # largest first, so the long ones start early
    jobs.sort(key=lambda j: -j[1]["bytes"])
    with ProcessPoolExecutor(WORKERS) as ex:
        for results in ex.map(check_file, *zip(*jobs, strict=True)):
            for ok, what in results:
                report(ok, what)

    if failures:
        print(f"\n{len(failures)} checks failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
