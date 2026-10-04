import importlib.util
import io
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "verify", Path(__file__).resolve().parents[1] / "scripts" / "02_verify.py"
)
v = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v)


def test_count_rows_strips_the_bom_and_counts_quoted_newlines_once():
    data = '﻿SortOrder,Location,TFR\r\n1,"Bonaire, Sint Eustatius\nand Saba",1.6\r\n2,Japan,1.2\r\n'
    rows, ragged, header = v.count_rows(io.BytesIO(data.encode("utf-8")))
    assert header == ["SortOrder", "Location", "TFR"]
    assert (rows, ragged) == (2, 0)


def test_count_rows_counts_ragged_rows():
    rows, ragged, _ = v.count_rows(io.BytesIO(b"a,b\n1,2\n3\n"))
    assert (rows, ragged) == (2, 1)
