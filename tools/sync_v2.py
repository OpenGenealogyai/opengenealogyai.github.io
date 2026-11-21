"""Copy the current MAXGEN 2.x release and its agent kit into this site, byte for byte.

Run deliberately:  python tools/sync_v2.py
The build never reads the source repositories itself; it reads only what this script pinned.

Sources (both owned by BuildGenealogy; read only)
  CURRENT_SRC   C:\\Users\\stock\\dev\\maxgen-workbench   2.1 schemas and the newest kit (2026-11-08)
  DOCS_SRC      C:\\Users\\stock\\dev\\opengenealogyai    the changelog, the starter prompt and the 2.0 release files

Writes
  schemas/maxgen/v2/<12 files>          the current 2.x files at their $id addresses (now 2.1)
  schemas/maxgen/v<ver>/<12 files>      a frozen copy of each 2.x release (2.0 and 2.1)
  .../SHA256SUMS.txt                    "<sha256>  <file name>" per file, from the bytes served
  schemas/maxgen/index.json             current -> 2.1; 2.x entries with fingerprints; 1.x entries archived
  agents/*, onboarding/*                the kit (hidden beta), with SHA256SUMS.txt and manifest.json
  content/v2/                           pinned build inputs and SYNC.json
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CURRENT_SRC = Path(r"C:\Users\stock\dev\maxgen-workbench")
DOCS_SRC = Path(r"C:\Users\stock\dev\opengenealogyai")
SITE = Path(__file__).resolve().parents[1]
BASE = "https://opengenealogyai.org"
V2_BASE = f"{BASE}/schemas/maxgen/v2/"

CURRENT = "2.1"
CURRENT_RELEASED = "2026-11-21"   # published on the site this day (Garlon: no comment period, nobody else uses it yet)
# Earlier 2.x releases kept as frozen copies: version -> (schema folder, release date)
EARLIER = {"2.0": (DOCS_SRC / "schemas", "2026-10-16")}

# (file, title) in BuildGenealogy's handoff order
SCHEMAS = [
    ("maxgen-defs.schema.json", "MAXGEN shared building blocks"),
    ("raw-record.schema.json", "MaxRecord"),
    ("person.schema.json", "MaxPerson"),
    ("relationship.schema.json", "MaxRelationship"),
    ("task-queue.schema.json", "MaxTask"),
    ("source.schema.json", "MaxSource"),
    ("place.schema.json", "MaxPlace"),
    ("group.schema.json", "MaxGroup"),
    ("event.schema.json", "MaxEvent"),
    ("name.schema.json", "MaxName"),
    ("recognition.schema.json", "MaxRecognition"),
    ("dna.schema.json", "MaxDNA"),
]
AGENT_DOCS = ["START.md", "RULES.md", "ORCHESTRATOR.md", "GENEALOGIST.md", "QUALITY_CONTROL.md",
              "CYCLE.md", "DATABASE.md", "EXAMPLE.md", "AGENTS.md"]
AGENT_PY = {  # served name -> source
    "maxgen_db.py": CURRENT_SRC / "docs" / "agents" / "maxgen_db.py",
    "probable_pedigree.py": CURRENT_SRC / "docs" / "agents" / "probable_pedigree.py",
    "sample_family.py": CURRENT_SRC / "docs" / "agents" / "sample_family.py",
    "check_maxgen.py": CURRENT_SRC / "docs" / "onboarding" / "check_maxgen.py",
}
ONBOARDING = {
    "STARTER_PROMPT.md": DOCS_SRC / "docs" / "onboarding" / "STARTER_PROMPT.md",   # only in the schema repo
    "check_maxgen.py": CURRENT_SRC / "docs" / "onboarding" / "check_maxgen.py",
}
# one made-up example per record kind (tests/fixtures/v2/<kind>/valid/<file>; identical in both repos)
EXAMPLES = {
    "record": "marriage_register_1880_obadiah_patience.json",
    "person": "birth_calculated_from_census_age.json",
    "relationship": "parent_child_candidate_father_a.json",
    "task": "extract_record_with_steps.json",
    "source": "census_collection_with_routing.json",
    "place": "town_with_dated_kinds.json",
    "group": "emigrant_company_no_members.json",
    "event": "marriage_shared_by_three_documents.json",
    "name": "quillfeather_surname_variants_with_status.json",
    "recognition": "htr_reading_with_region.json",
    "dna": "autosomal_with_match_ids.json",
}
POLICY = "2.x changes are additive only: a file valid under 2.0 stays valid under every 2.x."


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def copy(src: Path, dst: Path, record: dict) -> Path:
    if not src.is_file():
        raise SystemExit(f"missing source file: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)                       # bytes only; no newline conversion
    if dst.read_bytes() != src.read_bytes():
        raise SystemExit(f"copy differs from source: {dst}")
    record[str(dst.relative_to(SITE)).replace("\\", "/")] = sha256(dst)
    return dst


def sums_file(folder: Path, names: list[str]) -> None:
    lines = [f"{sha256(folder / n)}  {n}" for n in names]
    (folder / "SHA256SUMS.txt").write_bytes(("\n".join(lines) + "\n").encode("ascii"))


def write_json(p: Path, data) -> None:
    """JSON with LF line endings on every OS (these folders are stored byte for byte)."""
    p.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def py_version(p: Path) -> str:
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', p.read_text(encoding="utf-8"), flags=re.M)
    return m.group(1) if m else ""


def git_head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()


def publish_set(src_dir: Path, folders: list[Path], record: dict) -> list[dict]:
    files = []
    for folder in folders:
        for name, title in SCHEMAS:
            dst = copy(src_dir / name, folder / name, record)
            sid = json.loads(dst.read_bytes()).get("$id", "")
            if sid != V2_BASE + name:
                raise SystemExit(f"{name}: $id is {sid!r}, expected {V2_BASE + name}")
        sums_file(folder, [n for n, _ in SCHEMAS])
    first = folders[0]
    for name, title in SCHEMAS:
        files.append({"file": name, "title": title, "$id": V2_BASE + name, "sha256": sha256(first / name)})
    return files


def main() -> int:
    record: dict[str, str] = {}
    maxgen = SITE / "schemas" / "maxgen"

    # 1. schemas: the current release at /v2/ and /v<CURRENT>/, earlier 2.x releases frozen at /v<ver>/
    cur_files = publish_set(CURRENT_SRC / "schemas", [maxgen / "v2", maxgen / f"v{CURRENT}"], record)
    earlier_entries = []
    for ver, (folder, released) in EARLIER.items():
        files = publish_set(folder, [maxgen / f"v{ver}"], record)
        earlier_entries.append({"version": ver, "released": released, "status": "previous",
                                "url": f"{BASE}/schemas/maxgen/v{ver}/", "frozen_url": f"{BASE}/schemas/maxgen/v{ver}/",
                                "schemas": [n.replace(".schema.json", "") for n, _ in SCHEMAS], "files": files})

    # 2. index.json (keep the structure; current 2.x first; 1.x entries archived)
    idx_path = maxgen / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    idx["current"] = CURRENT
    idx["canonical_base"] = V2_BASE
    idx["policy"] = POLICY
    old = [v for v in idx["versions"] if not str(v["version"]).startswith("2.")]
    for v in old:
        v["status"] = "archived"
    current_entry = {"version": CURRENT, "released": CURRENT_RELEASED, "status": "current", "url": V2_BASE,
                     "frozen_url": f"{BASE}/schemas/maxgen/v{CURRENT}/",
                     "schemas": [n.replace(".schema.json", "") for n, _ in SCHEMAS], "files": cur_files}
    idx["versions"] = [current_entry] + earlier_entries + old
    write_json(idx_path, idx)

    # 3. agent kit (hidden beta)
    agents, onboard = SITE / "agents", SITE / "onboarding"
    for n in AGENT_DOCS:
        copy(CURRENT_SRC / "docs" / "agents" / n, agents / n, record)
    for n, src in AGENT_PY.items():
        copy(src, agents / n, record)
    copy(CURRENT_SRC / "LICENSE", agents / "LICENSE", record)
    for n, src in ONBOARDING.items():
        copy(src, onboard / n, record)
    sums_file(agents, AGENT_DOCS + list(AGENT_PY) + ["LICENSE"])
    sums_file(onboard, list(ONBOARDING))
    manifest = {
        "note": "SHA-256 and __version__ computed from the files exactly as served here.",
        "scripts": [{"path": f"/agents/{n}", "version": py_version(agents / n), "sha256": sha256(agents / n),
                     "bytes": (agents / n).stat().st_size} for n in AGENT_PY]
                   + [{"path": "/onboarding/check_maxgen.py", "version": py_version(onboard / "check_maxgen.py"),
                       "sha256": sha256(onboard / "check_maxgen.py"), "bytes": (onboard / "check_maxgen.py").stat().st_size}],
        "documents": [{"path": f"/agents/{n}", "sha256": sha256(agents / n)} for n in AGENT_DOCS + ["LICENSE"]]
                     + [{"path": "/onboarding/STARTER_PROMPT.md", "sha256": sha256(onboard / "STARTER_PROMPT.md")}],
        "license": {"scripts": "MIT (see /agents/LICENSE)", "standard": "CC0"},
    }
    write_json(agents / "manifest.json", manifest)

    # 4. pinned build inputs
    pin = SITE / "content" / "v2"
    copy(DOCS_SRC / "docs" / "SCHEMA_CHANGELOG.md", pin / "SCHEMA_CHANGELOG.md", record)
    copy(CURRENT_SRC / "docs" / "agents" / "llms.txt", pin / "agents-llms.txt", record)
    copy(CURRENT_SRC / "schemas" / "README-v2.md", pin / "README-v2.md", record)
    for kind, fname in EXAMPLES.items():
        dst = copy(DOCS_SRC / "tests" / "fixtures" / "v2" / kind / "valid" / fname, pin / "examples" / kind / fname, record)
        if b'"fixture": "made up"' not in dst.read_bytes():   # the marker sits inside the document (e.g. extensions)
            raise SystemExit(f"example {kind}/{fname} is not marked \"fixture\": \"made up\"")
    write_json(pin / "SYNC.json", {
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "current": CURRENT,
        "sources": {"current_schemas_and_kit": f"{CURRENT_SRC} (HEAD {git_head(CURRENT_SRC)})",
                    "changelog_starter_prompt_examples_and_2.0": f"{DOCS_SRC} (uncommitted working copy; HEAD {git_head(DOCS_SRC)})"},
        "examples": EXAMPLES, "sha256": record})
    print(f"synced {len(record)} files; current {CURRENT} at /v2/ and /v{CURRENT}/; earlier: {', '.join(EARLIER)}; kit scripts: "
          + ", ".join(f"{s['path'].rsplit('/', 1)[1]} {s['version']}" for s in manifest["scripts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
