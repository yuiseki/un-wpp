#!/usr/bin/env python3
"""Check data/parquet against data/raw with a second reader.

Every row of every Parquet file is compared, in order, with the row the csv module reads
from its source file (the gzip, the notes CSV, or the member of the interim update zip):
text equal (empty is NULL), integers equal, doubles equal as Python floats. files.parquet
must list every Parquet file with the row count it has.

    uv run python scripts/04_verify_parquet.py
"""

import csv
import gzip
import importlib.util
import io
import sys
import zipfile
from pathlib import Path

import duckdb

_spec = importlib.util.spec_from_file_location(
    "export", Path(__file__).resolve().parent / "03_export_parquet.py"
)
ex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex)


def convert(col: str, value: str, text_only: bool):
    if value == "":
        return None
    kind = "VARCHAR" if text_only else ex.column_type(col)
    if kind == "VARCHAR":
        return value
    return int(value) if kind == "BIGINT" else float(value)


def open_source(rel: Path, group: str, source: str):
    if "-update-" in group:
        d = rel / group.split(rel.name + "-", 1)[1]
        z = zipfile.ZipFile(next(d.glob("*.zip")))
        return io.TextIOWrapper(z.open(source), encoding="utf-8-sig", newline="")
    p = rel / source
    if p.suffix == ".gz":
        return gzip.open(p, "rt", encoding="utf-8-sig", newline="")
    return open(p, encoding="utf-8-sig", newline="")


def check(rel: Path, parquet: str, group: str, source: str, rows: int) -> list[str]:
    text_only = source.endswith("_notes.csv")
    cur = duckdb.connect().execute(f"select * from read_parquet('{ex.OUT / parquet}.parquet')")
    names = [d[0] for d in cur.description]
    n, errors, batch = 0, [], []
    with open_source(rel, group, source) as f:
        r = csv.reader(f)
        header = next(r)
        if [ex.output_name(c) for c in header] != names:
            return [f"{parquet}: columns {names} != {header}"]
        for row in r:
            if not batch:
                batch = cur.fetchmany(50000)
                batch.reverse()
            if not batch:
                return [f"{parquet}: Parquet ends at row {n}"]
            got = batch.pop()
            n += 1
            want = tuple(convert(c, v, text_only) for c, v in zip(header, row, strict=True))
            if got != want:
                errors.append(f"{parquet}: row {n}: {got} != {want}")
                if len(errors) > 3:
                    return errors
    if batch or cur.fetchmany(1):
        errors.append(f"{parquet}: more rows in the Parquet than the {n} of the source")
    if n != rows:
        errors.append(f"{parquet}: {n} rows read, files.parquet says {rows}")
    return errors


def main() -> int:
    rel = ex.current_release()
    listed = duckdb.sql(
        f"select parquet, release, source, rows from '{ex.OUT / 'files.parquet'}' order by 1"
    ).fetchall()
    on_disk = {p.relative_to(ex.OUT).with_suffix("").as_posix() for p in ex.OUT.glob("*/*.parquet")}
    bad, total = 0, 0
    if on_disk != {x[0] for x in listed}:
        print("ERROR files.parquet and the files on disk differ:", on_disk ^ {x[0] for x in listed})
        bad += 1
    for parquet, group, source, rows in listed:
        errs = check(rel, parquet, group, source, rows)
        total += rows
        for e in errs:
            print("ERROR", e)
        bad += bool(errs)
        print(f"{parquet} {rows:,} {'FAIL' if errs else 'ok'}", flush=True)
    print(f"{total:,} rows checked, {bad} files with errors")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
