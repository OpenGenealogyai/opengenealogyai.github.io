"""Copy the current MAXGEN 2.x release and its agent kit into this site, byte for byte, from ONE COMMIT.

Run deliberately:  python tools/sync_v2.py --commit <hash>
The build never reads the source repository itself; it reads only what this script pinned.

Source: one commit of BuildGenealogy's schema repo (C:\\Users\\stock\\dev\\opengenealogyai), read with
`git show <commit>:<path>`, so uncommitted working files are never used. BuildGenealogy, 2026-11-21: re-sync only on
its "ready for the website" message, from its commit; the 2.0 release lives in schemas/archive/v2.0/.
(The first publication of 2.1, 2026-11-21, came from maxgen-workbench; BuildGenealogy confirmed its commit carries
the same bytes for the twelve schema files and the kit.)

Writes
  schemas/maxgen/v2/<12 files>          the current 2.x files at their $id addresses (2.1)
  schemas/maxgen/v<ver>/<12 files>      a frozen copy of each 2.x release (2.0 and 2.1)
  .../SHA256SUMS.txt                    "<sha256>  <file name>" per file, from the bytes served
  schemas/maxgen/index.json             current -> 2.1; 2.x entries with fingerprints; 1.x entries archived
  agents/*, onboarding/*                the kit (hidden beta), with SHA256SUMS.txt and manifest.json
  content/v2/                           pinned build inputs and SYNC.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"C:\Users\stock\dev\opengenealogyai")
SITE = Path(__file__).resolve().parents[1]
BASE = "https://opengenealogyai.org"
V2_BASE = f"{BASE}/schemas/maxgen/v2/"

CURRENT = "2.1"
CURRENT_RELEASED = "2026-11-21"   # first served on the site this day (Garlon: no comment period, no outside users yet)
CURRENT_DIR = "schemas"
# Earlier 2.x releases kept as frozen copies: version -> (folder in the repo, public release date, build date).
# 2.0 was built 2026-10-16 but went public with 2.1 on 2026-11-21 (BuildGenealogy's v2.1 changelog entry).
EARLIER = {"2.0": ("schemas/archive/v2.0", "2026-11-21", "2026-10-16")}

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
AGENT_PY = {  # served name -> path in the repo
    "maxgen_db.py": "docs/agents/maxgen_db.py",
    "probable_pedigree.py": "docs/agents/probable_pedigree.py",
    "sample_family.py": "docs/agents/sample_family.py",
    "check_maxgen.py": "docs/onboarding/check_maxgen.py",   # one source, served at two paths
}
ONBOARDING = {"STARTER_PROMPT.md": "docs/onboarding/STARTER_PROMPT.md", "check_maxgen.py": "docs/onboarding/check_maxgen.py"}
# one made-up example per record kind (tests/fixtures/v2/<kind>/valid/<file>)
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
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Commit:
    def __init__(self, ref: str):
        r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                           capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode != 0:
            raise SystemExit(f"not a commit in {REPO}: {ref}")
        self.hash = r.stdout.strip()

    def read(self, relpath: str) -> bytes:
        r = subprocess.run(["git", "-C", str(REPO), "show", f"{self.hash}:{relpath}"], capture_output=True,
                           creationflags=NO_WINDOW)
        if r.returncode != 0:
            raise SystemExit(f"{relpath} is not in commit {self.hash[:10]}")
        return r.stdout


def put(data: bytes, dst: Path, record: dict, changed: list) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    rel = str(dst.relative_to(SITE)).replace("\\", "/")
    if not dst.exists() or dst.read_bytes() != data:
        changed.append(rel)
    dst.write_bytes(data)                         # bytes only; no newline conversion
    record[rel] = sha256(data)


def sums_file(folder: Path, names: list[str]) -> None:
    lines = [f"{sha256((folder / n).read_bytes())}  {n}" for n in names]
    (folder / "SHA256SUMS.txt").write_bytes(("\n".join(lines) + "\n").encode("ascii"))


def write_json(p: Path, data) -> None:
    """JSON with LF line endings on every OS (these folders are stored byte for byte)."""
    p.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def py_version(b: bytes) -> str:
    m = re.search(rb'^__version__\s*=\s*["\']([^"\']+)["\']', b, flags=re.M)
    return m.group(1).decode() if m else ""


def publish_set(c: Commit, src_dir: str, folders: list[Path], record: dict, changed: list) -> list[dict]:
    files = []
    for name, title in SCHEMAS:
        data = c.read(f"{src_dir}/{name}")
        sid = json.loads(data).get("$id", "")
        if sid != V2_BASE + name:
            raise SystemExit(f"{src_dir}/{name}: $id is {sid!r}, expected {V2_BASE + name}")
        for folder in folders:
            put(data, folder / name, record, changed)
        files.append({"file": name, "title": title, "$id": sid, "sha256": sha256(data)})
    for folder in folders:
        sums_file(folder, [n for n, _ in SCHEMAS])
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", required=True, help="commit of the schema repo named in BuildGenealogy's 'ready for the website'")
    c = Commit(ap.parse_args().commit)
    record: dict[str, str] = {}
    changed: list[str] = []
    maxgen = SITE / "schemas" / "maxgen"

    # 1. schemas: the current release at /v2/ and /v<CURRENT>/, earlier 2.x releases frozen at /v<ver>/
    cur_files = publish_set(c, CURRENT_DIR, [maxgen / "v2", maxgen / f"v{CURRENT}"], record, changed)
    earlier_entries = []
    for ver, (folder, released, built) in EARLIER.items():
        files = publish_set(c, folder, [maxgen / f"v{ver}"], record, changed)
        earlier_entries.append({"version": ver, "released": released, "built": built, "status": "previous",
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
        put(c.read(f"docs/agents/{n}"), agents / n, record, changed)
    scripts = {}
    for n, rel in AGENT_PY.items():
        scripts[n] = c.read(rel)
        put(scripts[n], agents / n, record, changed)
    put(c.read("LICENSE"), agents / "LICENSE", record, changed)
    for n, rel in ONBOARDING.items():
        put(c.read(rel), onboard / n, record, changed)
    sums_file(agents, AGENT_DOCS + list(AGENT_PY) + ["LICENSE"])
    sums_file(onboard, list(ONBOARDING))
    manifest = {
        "note": "SHA-256 and __version__ computed from the files exactly as served here.",
        "scripts": [{"path": f"/agents/{n}", "version": py_version(b), "sha256": sha256(b), "bytes": len(b)}
                    for n, b in scripts.items()]
                   + [{"path": "/onboarding/check_maxgen.py", "version": py_version(scripts["check_maxgen.py"]),
                       "sha256": sha256(scripts["check_maxgen.py"]), "bytes": len(scripts["check_maxgen.py"])}],
        "documents": [{"path": f"/agents/{n}", "sha256": sha256((agents / n).read_bytes())} for n in AGENT_DOCS + ["LICENSE"]]
                     + [{"path": "/onboarding/STARTER_PROMPT.md", "sha256": sha256((onboard / "STARTER_PROMPT.md").read_bytes())}],
        "license": {"scripts": "MIT (see /agents/LICENSE)", "standard": "CC0"},
    }
    write_json(agents / "manifest.json", manifest)

    # 4. pinned build inputs
    pin = SITE / "content" / "v2"
    put(c.read("docs/SCHEMA_CHANGELOG.md"), pin / "SCHEMA_CHANGELOG.md", record, changed)
    put(c.read("docs/agents/llms.txt"), pin / "agents-llms.txt", record, changed)
    put(c.read("schemas/README-v2.md"), pin / "README-v2.md", record, changed)
    for kind, fname in EXAMPLES.items():
        data = c.read(f"tests/fixtures/v2/{kind}/valid/{fname}")
        if b'"fixture": "made up"' not in data:   # the marker sits inside the document (e.g. extensions)
            raise SystemExit(f"example {kind}/{fname} is not marked \"fixture\": \"made up\"")
        put(data, pin / "examples" / kind / fname, record, changed)
    write_json(pin / "SYNC.json", {
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "current": CURRENT, "source": f"{REPO} commit {c.hash}",
        "examples": EXAMPLES, "sha256": record})
    print(f"synced {len(record)} files from commit {c.hash[:10]}; current {CURRENT}; earlier: {', '.join(EARLIER)}; "
          f"kit scripts: " + ", ".join(f"{s['path'].rsplit('/', 1)[1]} {s['version']}" for s in manifest["scripts"]))
    print(f"bytes changed from what the site served before this sync: {len(changed)}")
    for rel in changed:
        print("  changed:", rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
