import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("download", ROOT / "scripts" / "01_download.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

# downloads.json as served on 2026-10-05, trimmed: the CSV format, Most used and
# Interim Update groups of Standard Projections, and two revisions of two
# Archive groups; long descriptions are cut.
CAT = json.loads((ROOT / "tests" / "fixtures" / "downloads_trimmed.json").read_text())


def test_current_csv_takes_only_the_csv_format_group():
    b = m.current_csv(CAT)
    names = [Path(f["path"]).name for f in b["files"]]
    assert names == [
        "WPP2024_Locations_notes.csv",
        "WPP2024_Demographic_Indicators_notes.csv",
        "WPP2024_Demographic_Indicators_Medium.csv.gz",
        "WPP2024_Demographic_Indicators_OtherVariants.csv.gz",
        "WPP2024_TotalPopulationBySex.csv.gz",
        "WPP2024_Population1JanuaryByAge5GroupSex_Medium.csv.gz",
        "WPP2024_Population1JanuaryByAge5GroupSex_OtherVariants.csv.gz",
    ]
    assert not any(n.endswith(".xlsx") for n in names)


def test_current_csv_version_and_subgroups():
    b = m.current_csv(CAT)
    assert b["version"] == "wpp2024"
    assert b["subdir"] == ""
    f = b["files"][2]
    assert f["subgroup"] == "Demographic Indicators"
    assert f["title"] == "1950-2100, medium"


def test_current_csv_licence_and_citation():
    b = m.current_csv(CAT)
    assert b["licence"] == "CC BY 3.0 IGO"
    assert b["licence_url"] == "http://creativecommons.org/licenses/by/3.0/igo/"
    assert b["citation"] == (
        "United Nations, Department of Economic and Social Affairs, Population Division "
        "(2024). World Population Prospects 2024, Online Edition."
    )


def test_interim_update_is_the_csv_zip_with_its_date():
    b = m.interim_update(CAT)
    assert b["version"] == "wpp2024"
    assert b["release_date"] == "2026-01-19"
    assert b["subdir"] == "update-2026-01-19"
    assert [Path(f["path"]).name for f in b["files"]] == ["WPP2024_CSV_files_update.zip"]
    members = b["files"][0]["members"]
    assert len(members) == 11
    assert members[0] == "WPP2024_Demographic_Indicators_Medium_Update.csv"
    assert members[-1] == "WPP2024_DeathsBySingleAgeSex_Medium_Update.csv"


def test_archive_selects_group_and_release():
    bs = m.archive(CAT, "CSV files", ["2019 Revision"])
    assert len(bs) == 1
    assert bs[0]["version"] == "wpp2019"
    assert bs[0]["files"][0]["path"] == "assets/Excel Files/5_Archive/WPP2019-CSV-data.zip"


def test_archive_without_release_takes_the_whole_group():
    bs = m.archive(CAT, "CSV files", None)
    assert [b["version"] for b in bs] == ["wpp2022", "wpp2019"]


def test_archive_unknown_names_fail():
    with pytest.raises(SystemExit):
        m.archive(CAT, "CSV files", ["1066 Revision"])
    with pytest.raises(SystemExit):
        m.archive(CAT, "No such group", None)


@pytest.mark.parametrize(
    ("paths", "version"),
    [
        (["a/WPP2024_x.csv.gz", "a/WPP2024_y.csv"], "wpp2024"),
        (["a/WPP2022-CSV-data.zip"], "wpp2022"),
    ],
)
def test_version_from_paths(paths, version):
    assert m.version_from_paths(paths) == version


@pytest.mark.parametrize("paths", [["a/WPP2024_x.csv.gz", "a/WPP2022_y.csv"], ["a/notes.csv"], []])
def test_version_from_paths_refuses_mixed_or_missing(paths):
    with pytest.raises(SystemExit):
        m.version_from_paths(paths)


def test_revision_version():
    assert m.revision_version("2022 Revision") == "wpp2022"
    with pytest.raises(SystemExit):
        m.revision_version("Latest")


def test_parse_release_date():
    assert m.parse_release_date("<h3 style='x'>Release date: 19 January 2026</h3>") == "2026-01-19"
    with pytest.raises(SystemExit):
        m.parse_release_date("<p>no date here</p>")


def test_file_url_quotes_spaces_and_keeps_parentheses():
    assert m.file_url(
        "assets/Excel Files/1_Indicator (Standard)/CSV_FILES/WPP2024_PopulationBySingleAgeSex_No change_2024-2100.csv.gz"
    ) == (
        "https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/"
        "CSV_FILES/WPP2024_PopulationBySingleAgeSex_No%20change_2024-2100.csv.gz"
    )


def test_html_text():
    assert m.html_text("a <i>b</i>\n  c") == "a b c"
