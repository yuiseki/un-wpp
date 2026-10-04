# un-wpp

Reproducible collection of the CSV files of the United Nations World
Population Prospects (WPP): annual estimates from 1950 and projections to
2100 of population by age and sex, fertility, mortality, life tables and net
migration, for countries and areas and for regional and other aggregates.
This repository holds the code that fetches and checks them; the data
itself is written to `data/raw/`, which is not committed.

- Publisher: United Nations, Department of Economic and Social Affairs,
  Population Division
- Source: https://population.un.org/wpp/ (Downloads)
- Catalogue used for every URL: https://population.un.org/wpp/assets/downloads.json

## What is taken

By default, the current release:

- every file of the "CSV format" group of Standard Projections: gzipped CSVs,
  one table each, and two notes CSVs (location notes and indicator
  reference), into `data/raw/<version>/`;
- the CSV zip of the "Interim Update" group, which revises the figures of
  one country after the release, into `data/raw/<version>/update-<release date>/`.
  The main files are not changed by it; the zip holds `_Medium_Update.csv`
  replacements for the affected location only.

`<version>` is the release (`wpp2024`), read from the `WPP<year>` prefix the
file names share; the update directory is named by the release date the
catalogue states. The Excel files, the probabilistic projections (Excel
only), the special aggregates and the Data Portal API (which needs a token)
are not taken.

Past releases are in the catalogue's Archive folder, one zip per revision.
They are taken only on request, into `data/raw/wpp<year>/`:

```sh
uv run python scripts/01_download.py --archive "CSV files" --release "2022 Revision"
uv run python scripts/01_download.py --archive "CSV files"   # every revision in the group
```

The run sums the sizes first and stops if they exceed `--max-bytes`
(6,000,000,000 by default).

## Licence

The data is © United Nations, made available under Creative Commons
Attribution 3.0 IGO (CC BY 3.0 IGO,
http://creativecommons.org/licenses/by/3.0/igo/). The suggested citation is:
United Nations, Department of Economic and Social Affairs, Population
Division (2024). World Population Prospects 2024, Online Edition. Each
`MANIFEST.json` records the licence and citation as the catalogue states
them for that release.

## Running it

```sh
uv run python scripts/01_download.py   # fetch into data/raw/
uv run python scripts/02_verify.py     # re-hash, test the zip and gzips, count CSV rows
uv run python -m pytest                # unit tests of the catalogue parsing
```

`01_download.py` uses only the Python standard library, makes its requests
one at a time at least a second apart, writes each file to a `.part` file,
checks it against the stated Content-Length, and renames it. A file whose
manifest entry matches its sha256 is not fetched again.

## Layout

```
scripts/01_download.py        catalogue parsing and the download
scripts/02_verify.py          checks every file against its MANIFEST.json
tests/                        unit tests; fixtures/ holds a trimmed downloads.json
data/raw/<version>/           generated: the files as served, and MANIFEST.json
data/raw/<version>/update-<date>/   generated: an interim update and its MANIFEST.json
```

`MANIFEST.json` gives, for each file, its URL, size, sha256, Last-Modified,
fetch time (UTC), and the catalogue's subgroup, title and description; for
the interim update also the member files the catalogue lists and its notice.
