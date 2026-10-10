#!/usr/bin/env python3
"""Turn the files of the current release under data/raw/ into Parquet.

    data/parquet/wpp2024/<table>.parquet                  one per CSV of "Standard Projections / CSV format"
    data/parquet/wpp2024-update-<date>/<table>.parquet    one per CSV of the interim update zip
    data/parquet/wpp2024/locations_notes.parquet, demographic_indicators_notes.parquet
    data/parquet/files.parquet                            every Parquet file with its source file,
                                                          rows and columns

Only the current release (the newest wppNNNN whose manifest is a "Standard Projections"
group) is turned into Parquet; the archive revisions stay as the zips the UN serves
(data/raw/wppNNNN/).

Columns are the CSV's, in its order, rows in file order. Types come from the column
names, never from inference: the identifiers and counts named in INTEGER_COLUMNS are
BIGINT, the labels and codes named in TEXT_COLUMNS are VARCHAR (ISO2 "NA" is Namibia,
not a missing value), every other column is DOUBLE. Each value of a typed column is
checked with a regular expression before the cast, and one that does not fit stops the
run with the file, the column and the value. An empty cell is NULL. The two notes
files are all text.

The interim update's files are written as they are: they hold the replacement rows
for the one affected location, and are not merged into the tables of the release.

    uv run python scripts/03_export_parquet.py
"""

import csv
import gzip
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "parquet"

INTEGER_COLUMNS = {
    "SortOrder", "LocID", "LocTypeID", "ParentID", "VarID", "Time",
    "AgeGrpStart", "AgeGrpSpan", "SexID",
}  # fmt: skip
TEXT_COLUMNS = {
    "Notes", "ISO3_code", "ISO2_code", "SDMX_code", "LocTypeName", "Location",
    "Variant", "AgeGrp", "Sex",
}  # fmt: skip
INTEGER_RE = "-?[0-9]+"
NUMBER_RE = r"-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?|-?Inf"  # Inf: a sex ratio with no males
COPY = "format parquet, compression zstd, row_group_size 100000, write_bloom_filter false"


# DuckDB compares column names case-insensitively, and the life tables have both lx (survivors)
# and Lx (person-years lived): the second is renamed so that a table can hold both.
RENAMED = {"Lx": "Lx_person_years"}


def output_name(name: str) -> str:
    return RENAMED.get(name, name)


def column_type(name: str) -> str:
    if name in INTEGER_COLUMNS:
        return "BIGINT"
    return "VARCHAR" if name in TEXT_COLUMNS else "DOUBLE"


def slug(stem: str) -> str:
    s = re.sub(r"^WPP\d{4}_", "", stem)
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def header_of(src: Path) -> list[str]:
    op = gzip.open if src.suffix == ".gz" else open
    with op(src, "rt", encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f))


def export_csv(con, src: Path, out: Path, label: str, text_only: bool = False) -> int:
    cols = header_of(src)
    types = {c: "VARCHAR" if text_only else column_type(c) for c in cols}
    if len({output_name(c).lower() for c in cols}) != len(cols):
        raise SystemExit(f"{label}: column names that differ only in case: {cols}")
    select = []
    for i, c in enumerate(cols):
        q, alias = f"c{i}", f'"{output_name(c)}"'
        if types[c] == "VARCHAR":
            select.append(f"{q} as {alias}")
            continue
        pattern, what = (
            (INTEGER_RE, "an integer") if types[c] == "BIGINT" else (NUMBER_RE, "a number")
        )
        select.append(
            f"case when {q} is null then null when regexp_full_match({q}, '{pattern}') "
            f"then cast({q} as {types[c]}) "
            f"else error('{label}: {c} is not {what}: ' || {q}) end as {alias}"
        )  # fmt: skip
    spec = ", ".join(f"'c{i}': 'VARCHAR'" for i in range(len(cols)))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.part")
    con.execute(
        f"copy (select {', '.join(select)} from read_csv('{src}', header=true, skip=0, columns={{{spec}}}, "
        f"encoding='utf-8', quote='\"', escape='\"', nullstr='', all_varchar=true, "
        f"strict_mode=true)) to '{tmp}' ({COPY})"
    )
    n = con.execute(f"select count(*) from read_parquet('{tmp}')").fetchone()[0]
    tmp.replace(out)
    return n


def csv_rows(src: Path) -> int:
    op = gzip.open if src.suffix == ".gz" else open
    with op(src, "rt", encoding="utf-8-sig", newline="") as f:
        return sum(1 for _ in csv.reader(f)) - 1


def current_release() -> Path:
    for p in sorted(RAW.glob("wpp*/MANIFEST.json"), reverse=True):
        if json.loads(p.read_text())["group"].startswith("Standard Projections"):
            return p.parent
    raise SystemExit("no Standard Projections release under data/raw/")


def main() -> int:
    rel = current_release()
    con = duckdb.connect()
    con.execute("set memory_limit='4GB'; set threads=4")
    rows = []
    jobs = []
    for src in sorted(rel.glob("WPP*.csv.gz")):
        jobs.append((src, rel.name, slug(src.name.removesuffix(".csv.gz")), False))
    for src in sorted(rel.glob("WPP*_notes.csv")):
        jobs.append((src, rel.name, slug(src.stem), True))
    upd = sorted(rel.glob("update-*/MANIFEST.json"))
    scratch = Path(tempfile.mkdtemp(prefix="wpp-update-"))
    for mf in upd:
        d = mf.parent
        for zp in sorted(d.glob("*.zip")):
            with zipfile.ZipFile(zp) as z:
                for name in sorted(z.namelist()):
                    if name.endswith(".csv"):
                        z.extract(name, scratch)
                        jobs.append(
                            (scratch / name, f"{rel.name}-{d.name}", slug(Path(name).stem), False)
                        )
    for src, group, table, text_only in jobs:
        out = OUT / group / f"{table}.parquet"
        label = f"{group}/{src.name}"
        n = export_csv(con, src, out, label, text_only)
        want = csv_rows(src)
        if n != want:
            raise SystemExit(f"{label}: {n} rows written, {want} read")
        rows.append((f"{group}/{table}", group, src.name, n, len(header_of(src))))
        print(f"{group}/{table} {n:,}", flush=True)
    con.execute(
        "create table f (parquet VARCHAR, release VARCHAR, source VARCHAR, rows BIGINT, columns BIGINT)"
    )
    con.executemany("insert into f values (?, ?, ?, ?, ?)", rows)
    con.execute(f"copy f to '{OUT / 'files.parquet'}' ({COPY})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
