#!/usr/bin/env python3
"""Write data/README.md (the Hub card), data/LICENSE and data/provenance.yaml.

Counts come from data/parquet/files.parquet and the manifests under data/raw/, so run
it after 03_export_parquet.py and 04_verify_parquet.py.

    uv run python scripts/05_card.py
"""

import importlib.util
import json
import sys
from pathlib import Path

import duckdb

_spec = importlib.util.spec_from_file_location(
    "export", Path(__file__).resolve().parent / "03_export_parquet.py"
)
ex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex)

DATA = ex.ROOT / "data"
LICENCE_URL = "https://creativecommons.org/licenses/by/3.0/igo/"
SOURCE = "https://population.un.org/wpp/"


def revisions() -> list[tuple[str, int, str]]:
    out = []
    for p in sorted((DATA / "raw").glob("wpp*/MANIFEST.json")):
        m = json.loads(p.read_text())
        out.append((m["version"], sum(e["bytes"] for e in m["files"].values()), m["group"]))
    return out


def card() -> str:
    rel = ex.current_release()
    files = duckdb.sql(
        f"select parquet, source, rows, columns from '{ex.OUT / 'files.parquet'}' order by 1"
    ).fetchall()
    total = sum(f[2] for f in files)
    cfg = "\n".join(
        f"- config_name: '{f[0].replace('/', '__')}'\n  data_files: parquet/{f[0]}.parquet"
        for f in files
    )
    cfg = cfg.replace("- config_name: 'wpp2024__total", "- config_name: 'wpp2024__total", 1)
    table = "\n".join(f"| `{f[0]}` | {f[2]:,} | {f[3]} | `{f[1]}` |" for f in files)
    revs = "\n".join(f"| `raw/{v}/` | {b / 1e6:,.0f} MB | {g} |" for v, b, g in revisions())
    upd = sorted(rel.glob("update-*/MANIFEST.json"))
    upd_note = json.loads(upd[0].read_text()) if upd else None
    upd_text = (
        f"""
## The interim update of {upd_note["release_date"]}

The UN published an interim update after the release: more detailed results of Togo's 2022
census showed that the Population Division had adjusted the census counts a second time
for undercount. The update holds revised population and vital-event counts for Togo
(estimates 1950-2023 and medium projections 2024-2100) as `*_Medium_Update.csv` files
that replace the Togo rows of the release's tables; fertility rates and the other
variants are unchanged, as the UN's notice says. They are published unmerged: the subsets
`{rel.name}-{upd[0].parent.name}__*` are these files as received, and the release's tables
are as released. To have the current figures for Togo, replace the Togo rows (`LocID` 768)
of the release's tables with them. The UN's notice is in
`raw/{rel.name}/{upd[0].parent.name}/MANIFEST.json`.
"""
        if upd_note
        else ""
    )
    return f"""---
license: other
license_name: cc-by-3.0-igo
license_link: {LICENCE_URL}
language:
- en
task_categories:
- time-series-forecasting
- tabular-regression
tags:
- united-nations
- demography
- population
- population-projections
- fertility
- mortality
- life-tables
- migration
size_categories:
- 100M<n<1B
configs:
{cfg}
---

# un-wpp

The CSV files of the United Nations World Population Prospects (WPP), Population Division,
UN DESA: annual estimates from 1950 and projections to 2100 of population by age and sex,
fertility, mortality, life tables and net migration, for countries and areas and for
regional aggregates. Every revision of the CSV files from 1998 to 2024 is here as the UN
serves it (`raw/`); the current release, **{rel.name.upper()}**, is also in Parquet
({total:,} rows in {len(files)} tables).

Source: {SOURCE} (Downloads; the catalogue is
<https://population.un.org/wpp/assets/downloads.json>). Reuse is under CC BY 3.0 IGO
(see Licence).

## Raw files

Every revision, as served and checked against its `MANIFEST.json` (URL, size, sha256,
Last-Modified, fetch time):

| path | size | group |
|---|---:|---|
{revs}

The release files are gzipped CSVs with a UTF-8 byte order mark; the archive revisions are
zips of CSV or Excel-converted files whose layout changed from revision to revision.
Only the Parquet of the current release is harmonised here; read the archive zips with the
revision's own documentation.

## Parquet subsets (current release)

One subset per CSV file: the config name is the directory and the file name joined with
`__`. Columns are those of the CSV, in its order; rows are in file order.

| subset | rows | columns | source file |
|---|---:|---:|---|
{table}
| `files` | {len(files)} | 5 | one row per subset: source file, rows, columns |

Types come from the column names, not from inference: `SortOrder`, `LocID`, `LocTypeID`,
`ParentID`, `VarID`, `Time`, `AgeGrpStart`, `AgeGrpSpan` and `SexID` are BIGINT; `Notes`,
`ISO3_code`, `ISO2_code`, `SDMX_code`, `LocTypeName`, `Location`, `Variant`, `AgeGrp`
and `Sex` are VARCHAR (`ISO2_code` `NA` is Namibia, not a missing value); every other
column (`MidPeriod`, the measures) is DOUBLE. Each value of a typed column was checked against
a regular expression before the cast, and one that did not fit would have stopped the
build. An empty cell is NULL. One value is the text `Inf` in the source (a sex ratio
with no males, Andorra, `Lower 80 PI`, 2100); it is stored as infinity. The two notes files
are all text.

The life tables have both `lx` (survivors) and `Lx` (person-years lived); a column name that
differs only in case cannot be told apart in DuckDB, so `Lx` is named `Lx_person_years` here
(the raw files keep `Lx`).

`Notes` in a table is a letter that refers to `locations_notes`; `Variant` is the
projection variant (Medium, High, Low, constant-fertility and other scenarios, and the
80 and 95 per cent prediction intervals in the `OtherVariants` tables). Units are in
`demographic_indicators_notes`.

All {total:,} rows were compared with a second reader (the csv module on the raw file):
every cell equals the CSV.
{upd_text}
## Licence

The data are © United Nations, available under Creative Commons Attribution 3.0 IGO
(CC BY 3.0 IGO, {LICENCE_URL}). HF has no identifier for the IGO version, so the card says
`license: other`. Suggested citation: United Nations, Department of Economic and Social
Affairs, Population Division (2024). World Population Prospects 2024, Online Edition.
Earlier revisions are cited with their own year. The Parquet files are a reformatting of
the UN's CSV files; they are not endorsed by the United Nations.

Collection and conversion code: <https://github.com/yuiseki/un-wpp> (MIT).
"""


def licence_file() -> str:
    return f"""The contents of data/ are the United Nations World Population Prospects, © United
Nations, Department of Economic and Social Affairs, Population Division, made available
under Creative Commons Attribution 3.0 IGO ({LICENCE_URL}).

Suggested citation: United Nations, Department of Economic and Social Affairs, Population
Division (2024). World Population Prospects 2024, Online Edition.

The raw files are the UN's, unchanged. The Parquet files under parquet/ are a reformatting
of the UN's CSV files (types set, rows kept); they are not endorsed by the United Nations.

The code of the repository this dataset was built from is MIT licensed.
"""


def provenance() -> str:
    rel = ex.current_release()
    m = json.loads((rel / "MANIFEST.json").read_text())
    revs = "\n".join(f"  - {v}  # {g}" for v, _, g in revisions())
    return f"""# Where this data comes from and what was done to it.

source:
  name: World Population Prospects, CSV files
  publisher: United Nations, Department of Economic and Social Affairs, Population Division
  portal: {SOURCE}
  catalogue: {m["catalogue"]["url"]}  # sha256 {m["catalogue"]["sha256"]}, Last-Modified {m["catalogue"]["last_modified"]}
  licence:
    applied: CC BY 3.0 IGO ({LICENCE_URL}), as the catalogue states it for each release
    card: "license: other, license_name: cc-by-3.0-igo"
    attribution_required: true
  citation: >-
    {m["citation"]}

revisions_in_raw:
{revs}

processing:
  - raw files kept as served (zips, gzipped CSVs), each checked against the sha256 in its MANIFEST.json
  - scripts/03_export_parquet.py writes the current release and its interim update to Parquet
  - scripts/04_verify_parquet.py compares every row with the source file
"""


def main() -> int:
    (DATA / "README.md").write_text(card(), encoding="utf-8")
    (DATA / "LICENSE").write_text(licence_file(), encoding="utf-8")
    (DATA / "provenance.yaml").write_text(provenance(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
