import gzip
import importlib.util
from pathlib import Path

import duckdb
import pytest

_spec = importlib.util.spec_from_file_location(
    "export", Path(__file__).resolve().parents[1] / "scripts" / "03_export_parquet.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

CSV = (
    "﻿SortOrder,LocID,Notes,ISO3_code,ISO2_code,Location,Time,AgeGrp,AgeGrpStart,MidPeriod,TFR\r\n"
    "1,516,,NAM,NA,Namibia,2024,0-4,0,2024.5,3.1\r\n"
    '2,4,a,AFG,AF,"Afghanistan, Islamic Rep.",2025,100+,100,2025.5,\r\n'
)


@pytest.mark.parametrize(
    ("name", "sql"),
    [
        ("SortOrder", "BIGINT"), ("LocID", "BIGINT"), ("Time", "BIGINT"),
        ("AgeGrpStart", "BIGINT"), ("SexID", "BIGINT"),
        ("ISO2_code", "VARCHAR"), ("Location", "VARCHAR"), ("AgeGrp", "VARCHAR"),
        ("Sex", "VARCHAR"), ("Notes", "VARCHAR"),
        ("MidPeriod", "DOUBLE"), ("TFR", "DOUBLE"), ("PopTotal", "DOUBLE"), ("ex", "DOUBLE"),
    ],
)  # fmt: skip
def test_column_type_is_set_by_name(name, sql):
    assert m.column_type(name) == sql


def test_stem_is_a_short_slug():
    assert m.slug("WPP2024_PopulationBySingleAgeSex_Constant fertility_2024-2100") == (
        "populationbysingleagesex_constant_fertility_2024_2100"
    )
    assert m.slug("WPP2024_Locations_notes") == "locations_notes"


def test_export_keeps_na_iso_code_and_empty_as_null(tmp_path):
    src = tmp_path / "f.csv.gz"
    src.write_bytes(gzip.compress(CSV.encode()))
    out = tmp_path / "f.parquet"
    n = m.export_csv(duckdb.connect(), src, out, label="f")
    assert n == 2
    got = duckdb.sql(f"select ISO2_code, Notes, TFR, AgeGrp, Time from '{out}'").fetchall()
    assert got == [("NA", None, 3.1, "0-4", 2024), ("AF", "a", None, "100+", 2025)]
    cols = {r[0]: r[1] for r in duckdb.sql(f"describe select * from '{out}'").fetchall()}
    assert cols["Time"] == "BIGINT" and cols["TFR"] == "DOUBLE" and cols["Location"] == "VARCHAR"


def test_a_value_that_does_not_fit_its_type_stops_with_the_file_and_value(tmp_path):
    src = tmp_path / "f.csv"
    src.write_text(CSV.replace("3.1", "n/a"), encoding="utf-8")
    with pytest.raises(Exception, match=r"f: TFR is not a number: n/a"):
        m.export_csv(duckdb.connect(), src, tmp_path / "f.parquet", label="f")


def test_notes_files_keep_their_text_columns(tmp_path):
    src = tmp_path / "n.csv"
    src.write_text('Notes,Text\na,"Long, quoted"\n', encoding="utf-8")
    out = tmp_path / "n.parquet"
    m.export_csv(duckdb.connect(), src, out, label="n", text_only=True)
    assert duckdb.sql(f"select Notes, Text from '{out}'").fetchall() == [("a", "Long, quoted")]


def test_inf_in_a_numeric_column_is_kept_as_infinity(tmp_path):
    src = tmp_path / "f.csv"
    src.write_text(CSV.replace("3.1", "Inf"), encoding="utf-8")
    out = tmp_path / "f.parquet"
    m.export_csv(duckdb.connect(), src, out, label="f")
    assert duckdb.sql(f"select isinf(TFR) from '{out}' where Location = 'Namibia'").fetchone() == (
        True,
    )


def test_lx_and_Lx_are_both_kept_under_different_names(tmp_path):
    src = tmp_path / "lt.csv"
    src.write_text("LocID,lx,Lx\n4,100000,99000.5\n", encoding="utf-8")
    out = tmp_path / "lt.parquet"
    m.export_csv(duckdb.connect(), src, out, label="lt")
    got = duckdb.sql(f"select lx, Lx_person_years from '{out}'").fetchall()
    assert got == [(100000.0, 99000.5)]
