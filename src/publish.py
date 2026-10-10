#!/usr/bin/env python3
"""Push the card, the licence, the provenance, the Parquet and every release's raw files to the Hub.

Each WPP revision is a different estimate of the same quantities, so every revision under
data/raw/ is kept (the zips as the UN serves them); only the current release is also
turned into Parquet. A raw file goes up unchanged after its sha256 is checked against its
MANIFEST.json. The card must declare every Parquet file as a subset. Parquet files of an
earlier push that are no longer produced are deleted on the Hub in the same commit.

    uv run python src/publish.py             # dry run: list the files and their total size
    uv run python src/publish.py --push
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPO = "yuiseki/un-wpp"
DELETE_PATTERNS = ["parquet/**"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def collect(data: Path) -> list[str]:
    """The files to upload, relative to data/; stops on anything the card or a manifest refutes."""
    card = (data / "README.md").read_text(encoding="utf-8")
    if "license_name: cc-by-3.0-igo" not in card:
        raise SystemExit("the card does not declare license_name: cc-by-3.0-igo")
    files = ["README.md", "LICENSE", "provenance.yaml"]
    parquet = sorted((data / "parquet").glob("*/*.parquet"))
    if not parquet:
        raise SystemExit("no Parquet under data/parquet/; run scripts/03_export_parquet.py")
    for p in parquet:
        name = p.relative_to(data / "parquet").with_suffix("").as_posix()
        if f"data_files: parquet/{name}.parquet" not in card:
            raise SystemExit(f"the card does not declare the subset '{name}'")
        files.append(p.relative_to(data).as_posix())
    files.append("parquet/files.parquet")
    manifests = sorted((data / "raw").glob("wpp*/MANIFEST.json")) + sorted(
        (data / "raw").glob("wpp*/update-*/MANIFEST.json")
    )
    if not manifests:
        raise SystemExit("no raw release under data/raw/")
    for mf in manifests:
        d = mf.parent
        if f"raw/{d.relative_to(data / 'raw').as_posix()}/" not in card:
            raise SystemExit(f"the card does not name raw/{d.relative_to(data / 'raw')}/")
        for name, e in json.loads(mf.read_text())["files"].items():
            p = d / name
            if sha256(p) != e["sha256"]:
                raise SystemExit(f"{p.relative_to(data)} is not the file in its manifest")
            files.append(p.relative_to(data).as_posix())
        files.append(mf.relative_to(data).as_posix())
    stray = {
        p.relative_to(data).as_posix() for p in (data / "raw").rglob("*") if p.is_file()
    } - set(files)
    if stray:
        raise SystemExit(f"files under data/raw/ that no manifest lists: {sorted(stray)[:5]}")
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    files = collect(DATA)
    total = sum((DATA / f).stat().st_size for f in files)
    shown = [f for f in files if not f.startswith(("raw/", "parquet/wpp"))]
    for f in shown:
        print(f"  {(DATA / f).stat().st_size:>14,}  {f}")
    print(f"  ({len(files) - len(shown)} more files not listed)")
    print(f"{a.repo}\n  {len(files)} files, {total:,} bytes ({total / 1e9:.2f} GB)")
    print(f"  remote files matching {DELETE_PATTERNS} and not uploaded are deleted")
    if not a.push:
        print("\ndry run. pass --push to upload")
        return 0

    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(a.repo, repo_type="dataset", exist_ok=True, private=False)
    api.upload_folder(
        folder_path=str(DATA), repo_id=a.repo, repo_type="dataset", allow_patterns=files,
        delete_patterns=DELETE_PATTERNS,
    )  # fmt: skip
    print(f"\npushed to https://huggingface.co/datasets/{a.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
