"""Copy released MAXGEN schema files from the schema repository into this site.

Layout produced:
  schemas/maxgen/v1/<name>.schema.json        -> latest v1.x (the canonical $id URLs)
  schemas/maxgen/v1.13/<name>.schema.json     -> frozen copy of each released version
  schemas/maxgen/index.json                   -> machine-readable list of versions

Run:  python tools/sync_schemas.py
Never edit the copied files by hand; change them in the schema repo and re-run.
"""
from __future__ import annotations
import json
import re
import shutil
import sys
from pathlib import Path

SCHEMA_REPO = Path(r"C:\Users\stock\dev\opengenealogyai\schemas")
SITE = Path(__file__).resolve().parents[1]
OUT = SITE / "schemas" / "maxgen"

NAMES = ["raw-record", "person", "task-queue", "dna", "source", "recognition", "name"]


def version_of(path: Path) -> str:
    d = json.loads(path.read_text(encoding="utf-8"))
    v = d.get("properties", {}).get("schema_version", {}).get("const")
    if not v:
        m = re.search(r"MAXGEN v(\d+\.\d+)", d.get("description", ""))
        v = m.group(1) if m else "unknown"
    return str(v)


def copy_set(src_dir: Path, dst_dir: Path) -> list[str]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for n in NAMES:
        f = src_dir / f"{n}.schema.json"
        if f.exists():
            shutil.copy2(f, dst_dir / f.name)
            copied.append(n)
    return copied


def main() -> int:
    if not SCHEMA_REPO.exists():
        print(f"schema repo not found: {SCHEMA_REPO}", file=sys.stderr)
        return 1
    versions: dict[str, list[str]] = {}

    current = version_of(SCHEMA_REPO / "person.schema.json")
    versions[current] = copy_set(SCHEMA_REPO, OUT / "v1")
    copy_set(SCHEMA_REPO, OUT / f"v{current}")
    print(f"current v{current}: {len(versions[current])} schemas -> v1/ and v{current}/")

    archive = SCHEMA_REPO / "archive"
    if archive.exists():
        for d in sorted(archive.iterdir()):
            if d.is_dir() and d.name.startswith("v"):
                ver = d.name[1:]
                if ver == current:
                    continue
                versions[ver] = copy_set(d, OUT / d.name)
                print(f"archive v{ver}: {len(versions[ver])} schemas")

    def key(v: str):
        return tuple(int(x) for x in v.split("."))

    index = {
        "standard": "MAXGEN",
        "current": current,
        "canonical_base": "https://opengenealogyai.org/schemas/maxgen/v1/",
        "versions": [
            {"version": v, "schemas": versions[v], "url": f"https://opengenealogyai.org/schemas/maxgen/v{v}/"}
            for v in sorted(versions, key=key, reverse=True)
        ],
    }
    (OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"wrote index.json with {len(versions)} versions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
