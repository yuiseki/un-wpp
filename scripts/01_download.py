#!/usr/bin/env python3
"""Fetch the CSV files of UN World Population Prospects (WPP) as published.

The UN Population Division lists every bulk download in one catalogue,
https://population.un.org/wpp/assets/downloads.json, which the Downloads page
of its site is built from. Every URL here comes from that catalogue.

By default this takes the current release:
  - the "CSV format" group of Standard Projections (the .csv.gz files and the
    two notes .csv files), into data/raw/<version>/
  - the CSV zip of its "Interim Update" group, into
    data/raw/<version>/update-<release date>/

<version> is a release, not a fetch date: it is read from the WPP<year> prefix
the file names share (wpp2024), and the interim update is named by the
"Release date" its catalogue entry states. The Excel files, the probabilistic
projections and the Data Portal API (which needs a token) are not taken.

Past releases are in the Archive folder of the catalogue, one zip per revision
and group. They are taken only when asked for:

    uv run python scripts/01_download.py --archive "CSV files" --release "2022 Revision"

which files the zip under data/raw/wpp2022/. Without --release the whole group
is taken.

Each file goes to a .part file, is checked against the Content-Length the
server states, and is then renamed. data/raw/<version>/MANIFEST.json records
for each file its URL, size, sha256, Last-Modified and fetch time, with the
catalogue's title and description, the licence and the suggested citation.
A file whose manifest entry has the sha256 of the file on disk is not fetched
again. Before anything is fetched, the sizes are summed and the run stops if
they exceed --max-bytes.

    uv run python scripts/01_download.py
"""

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
BASE = "https://population.un.org/wpp/"
CATALOGUE = BASE + "assets/downloads.json"
UA = "un-wpp/1 (+https://github.com/yuiseki/un-wpp)"
GAP = 1.1  # seconds between requests
DEFAULT_MAX_BYTES = 6_000_000_000

_last_request = 0.0


# ---------------------------------------------------------------- parsing


def html_text(s: str) -> str:
    """The text of an HTML fragment, tags dropped and whitespace collapsed."""
    s = re.sub(r"</?(?:i|b|a|em|strong|span)\b[^>]*>", "", s)  # inline: no word break
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def html_list_items(s: str) -> list[str]:
    return [html_text(li) for li in re.findall(r"<li>(.*?)</li>", s, flags=re.S)]


def file_url(path: str) -> str:
    return BASE + urllib.parse.quote(path, safe="/()")


def parse_release_date(s: str) -> str:
    m = re.search(r"Release date:\s*(\d{1,2} \w+ \d{4})", html_text(s))
    if not m:
        raise SystemExit("no 'Release date' in the catalogue entry")
    return dt.datetime.strptime(m.group(1), "%d %B %Y").date().isoformat()


def parse_footer(s: str) -> dict:
    """The licence and suggested citation a catalogue group's footer states."""
    text = html_text(s)
    out = {}
    m = re.search(r"Creative Commons license (CC BY [\d.]+(?: IGO)?)", text)
    if m:
        out["licence"] = m.group(1)
    m = re.search(r'href="([^"]*creativecommons\.org[^"]*)"', s)
    if m:
        out["licence_url"] = m.group(1)
    m = re.search(r"Suggested citation[^:]*:\s*(.*)", text)
    if m:
        out["citation"] = m.group(1).strip()
    return out


def version_from_paths(paths: list[str]) -> str:
    years = {m.group(1) for p in paths if (m := re.match(r"WPP(\d{4})[_-]", Path(p).name))}
    if len(years) != 1 or not all(re.match(r"WPP\d{4}[_-]", Path(p).name) for p in paths):
        raise SystemExit(f"the files do not share one WPP<year> prefix: {sorted(years)}")
    return f"wpp{years.pop()}"


def revision_version(name: str) -> str:
    m = re.fullmatch(r"(\d{4}) Revision", name)
    if not m:
        raise SystemExit(f"not a revision name: {name!r}")
    return f"wpp{m.group(1)}"


def _folder(cat: dict, word: str) -> dict:
    for f in cat["Folders"]:
        if html_text(f["display"]).startswith(word):
            return f
    raise SystemExit(f"no folder {word!r} in the catalogue")


def _group(folder: dict, name: str) -> dict:
    for g in folder["MajorGroup"]:
        if g["name"] == name:
            return g
    raise SystemExit(f"no group {name!r} in the catalogue")


def _files(group: dict, subgroups: list[dict] | None = None) -> list[dict]:
    out = []
    for s in subgroups if subgroups is not None else group["SubGroup"]:
        for item in s["Item"]:
            for f in item["File"]:
                out.append(
                    {
                        "path": f["Path"],
                        "type": f["type"],
                        "subgroup": s["name"],
                        "title": f["Title"],
                        "description": html_text(item.get("Description", "")),
                    }
                )
    return out


def current_csv(cat: dict) -> dict:
    g = _group(_folder(cat, "Standard Projections"), "CSV format")
    files = _files(g)
    bad = [f["path"] for f in files if not re.search(r"\.csv(\.gz)?$", f["path"])]
    if bad:
        raise SystemExit(f"the CSV format group lists files that are not CSV: {bad}")
    return {
        "version": version_from_paths([f["path"] for f in files]),
        "subdir": "",
        "group": "Standard Projections / CSV format",
        "files": files,
        **parse_footer(g.get("Footer", "")),
    }


def interim_update(cat: dict) -> dict:
    folder = _folder(cat, "Standard Projections")
    g = _group(folder, "Interim Update")
    date = parse_release_date(g["Header"])
    subs = [s for s in g["SubGroup"] if s["name"].startswith("CSV")]
    files = _files(g, subs)
    if len(files) != 1:
        raise SystemExit(f"expected one CSV zip in the interim update, found {len(files)}")
    files[0]["members"] = [n for n in html_list_items(subs[0]["Item"][0]["Description"])]
    return {
        "version": version_from_paths([f["path"] for f in files]),
        "subdir": f"update-{date}",
        "group": "Standard Projections / Interim Update / CSV",
        "release_date": date,
        "notice": html_text(g["Header"]),
        "files": files,
        # the interim update's own footer is empty; the release's CSV footer applies
        **parse_footer(_group(folder, "CSV format").get("Footer", "")),
    }


def archive(cat: dict, group: str, releases: list[str] | None) -> list[dict]:
    g = _group(_folder(cat, "Archive"), group)
    subs = g["SubGroup"]
    if releases:
        unknown = set(releases) - {s["name"] for s in subs}
        if unknown:
            raise SystemExit(f"no release {sorted(unknown)} in Archive / {group}")
        subs = [s for s in subs if s["name"] in releases]
    return [
        {
            "version": revision_version(s["name"]),
            "subdir": "",
            "group": f"Archive / {group} / {s['name']}",
            "files": _files(g, [s]),
            **parse_footer(g.get("Footer", "")),
        }
        for s in subs
    ]


# ---------------------------------------------------------------- network


def _request(url: str, method: str = "GET"):
    global _last_request
    wait = _last_request + GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=300)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def head_size(url: str) -> int | None:
    with _request(url, "HEAD") as r:
        n = r.headers.get("Content-Length")
    return int(n) if n is not None else None


def fetch(url: str, dest: Path) -> dict:
    tmp = dest.with_name(dest.name + ".part")
    with _request(url) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
        length = r.headers.get("Content-Length")
        lm = r.headers.get("Last-Modified", "")
    got = tmp.stat().st_size
    if length is not None and got != int(length):
        tmp.unlink()
        raise SystemExit(f"{url}: got {got} bytes, the server stated {length}")
    tmp.replace(dest)
    return {
        "url": url,
        "bytes": got,
        "sha256": sha256(dest),
        "last_modified": lm,
        "fetched_at": now(),
        "content_length_stated": length is not None,
    }


# ---------------------------------------------------------------- main


def batch_dir(b: dict) -> Path:
    return RAW / b["version"] / b["subdir"] if b["subdir"] else RAW / b["version"]


def load_manifest(d: Path) -> dict:
    p = d / "MANIFEST.json"
    return json.loads(p.read_text()) if p.exists() else {}


def have(d: Path, entry: dict | None, name: str) -> bool:
    p = d / name
    return (
        entry is not None
        and p.exists()
        and p.stat().st_size == entry["bytes"]
        and sha256(p) == entry["sha256"]
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", metavar="GROUP", help='an Archive group, e.g. "CSV files"')
    ap.add_argument("--release", action="append", help='with --archive, e.g. "2022 Revision"')
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = ap.parse_args()
    if args.release and not args.archive:
        ap.error("--release needs --archive")

    with _request(CATALOGUE) as r:
        raw = r.read()
        cat_lm = r.headers.get("Last-Modified", "")
    cat = json.loads(raw)
    catalogue = {
        "url": CATALOGUE,
        "last_modified": cat_lm,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "fetched_at": now(),
    }

    batches = (
        archive(cat, args.archive, args.release)
        if args.archive
        else [current_csv(cat), interim_update(cat)]
    )

    # plan: what is already here, and the size of what is not
    plan, total, todo = [], 0, 0
    for b in batches:
        d = batch_dir(b)
        files = load_manifest(d).get("files", {})
        for f in b["files"]:
            name = Path(f["path"]).name
            url = file_url(f["path"])
            if have(d, files.get(name), name):
                total += files[name]["bytes"]
                continue
            size = head_size(url)
            if size is None:
                raise SystemExit(f"{url}: no Content-Length on HEAD; cannot check the size limit")
            total += size
            todo += size
            plan.append((b, f, name, url))
    print(f"{len(plan)} files to fetch, {todo:,} bytes; selection total {total:,} bytes")
    if total > args.max_bytes:
        raise SystemExit(f"the selection is {total:,} bytes, over --max-bytes {args.max_bytes:,}")

    for b in batches:
        d = batch_dir(b)
        if not any(bb is b for bb, *_ in plan):
            continue  # nothing new: the manifest stays as it is
        d.mkdir(parents=True, exist_ok=True)
        manifest = load_manifest(d)
        files = manifest.get("files", {})
        manifest = {
            "dataset": "UN World Population Prospects",
            "publisher": "United Nations, Department of Economic and Social Affairs, Population Division",
            "version": b["version"],
            "group": b["group"],
            **{k: b[k] for k in ("release_date", "notice") if k in b},
            "licence": b.get("licence", ""),
            "licence_url": b.get("licence_url", ""),
            "citation": b.get("citation", ""),
            "catalogue": catalogue,
            "files": files,
        }
        for bb, f, name, url in plan:
            if bb is not b:
                continue
            entry = fetch(url, d / name)
            entry.update({k: f[k] for k in ("subgroup", "title", "description")})
            if "members" in f:
                entry["members_listed"] = f["members"]
            files[name] = entry
            (d / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
            print(
                f"{d.relative_to(RAW)}/{name}: {entry['bytes']:,} bytes, {entry['last_modified']}"
            )
        (d / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
