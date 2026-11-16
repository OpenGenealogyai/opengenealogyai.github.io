#!/usr/bin/env python3
"""maxgen_db.py -- the one database a MAXGEN 2.0 project keeps, and the only way to write to it.

One SQLite file (a database in a single file, no server). Every MAXGEN document -- MaxRecord,
MaxPerson, MaxRelationship, MaxTask, MaxSource, MaxPlace, MaxGroup, MaxEvent, MaxName,
MaxRecognition, MaxDNA -- is checked against its schema on the way in and stored whole, so
nothing enters that is not valid MAXGEN 2.0. Family links are never written by hand: the `link`
command finds or makes the MaxRelationship records.

    pip install jsonschema referencing rfc3987 rfc3339-validator        (once)

    python maxgen_db.py init     genealogy.db
    python maxgen_db.py session  genealogy.db --name Genealogist --kind ai --role Genealogist \\
                                 --model-name <name> --run-id <run>     (prints MAXGEN_SESSION=...)
    python maxgen_db.py add      genealogy.db <file.json> --task <task_id> [--replace --expect-sha256 <sha>]
    python maxgen_db.py claim    genealogy.db <claims.json> --task <task_id>
    python maxgen_db.py link     genealogy.db <request.json> [--task <task_id>]
    python maxgen_db.py show     genealogy.db <id>
    python maxgen_db.py review   genealogy.db <id> pass|fail --by "Quality Control" --note "..."
    python maxgen_db.py task     genealogy.db <task_id> pending|processing|done|failed|escalated|on_hold|cancelled
                                 [--verdict X] [--note "..."] [--answer accepted|rejected|rework]
    python maxgen_db.py step     genealogy.db --task <id> --source "<site>" --query "<what>" --found "<what>"
                                 --verdict key_finding|usable|inconclusive|ruled_out|dead_end|inaccessible
    python maxgen_db.py dead-end genealogy.db --task <id> --person <id> --source "<site>" --query "<what>" --note "..."
    python maxgen_db.py retract  genealogy.db <id> --task <task_id> --reason "<why>"
    python maxgen_db.py apply-proof | withdraw-proof | prefer | merge | unmerge | distinct | split |
                        resolve | apply-run-review                       (section 8.5 of the spec)
    python maxgen_db.py status | list | export | occurrence | lineage | check | reindex | portal
    python maxgen_db.py upgrade  genealogy.db [--backup <copy.db>]   (a tree made by kit 1.1.0 or
                                 earlier: adds the 1.2.0 bookkeeping after a checked backup copy)

Every write runs under a session: pass --as <session_id> or set MAXGEN_SESSION. A refusal prints
"REFUSED: <plain sentence> (R-nn)", exits 1 and changes nothing.

KIT 1.2.0 (the tree file format MAXGEN 2.1; Probable Pedigree design 02 section 4)
  Every write transaction is stamped (write_tx: who wrote, as which kind of writer), every change
  to a data, review, redirect, meta, owner or action row is logged by triggers stored in the file
  itself (change_log, so raw SQL is seen too), and every transaction leaves one action_log row
  with an undo recipe. The owner tables (owner_setting, owner_branch, owner_rule, owner_mark,
  owner_tag, owner_list) hold the person's own choices; they only grow, and only the person's own
  session presenting the owner key may write them (R-33); a locked person is refused to AI,
  importer and sync sessions (R-34). Programs write through `Gate` (one transaction, prints
  nothing, owns its connection). While <tree>.pp-maintenance exists, every write is refused.
  Programs that show the whole tree read it with `TreeSnapshot` (one read transaction, compact facts
  only, the same answers as the per-document readers) and ask `checked_view` which of a person's claims
  Quality Control passed; merge redirects are followed backwards through the index ix_redirect_new.
  Every connection uses synchronous=FULL (and fullfsync on a Mac) and a 5-second busy timeout,
  and opens the tree by its real name however the path was written (tree_file); a tree file with
  a second name (a hard link) is never written.

THE GATES (numbered as in the MAXGEN 2.0 specification, section 6)
  The document passes its schema (R-01) and carries a 2.x stamp (R-02). Helper-owned fields are
  filled by the helper, never by the writer (R-03). Every id it names exists -- no invented
  sources (R-04). Every document that names a person says whether that person may be alive;
  living means tier2-private, and living is always worked out again when read (R-05, Living A).
  Every claim cites one piece of evidence (schema), labels on records are checked (R-10), and a
  claim resting only on the person's word, a tree, an import or a compiled work is capped at 0.4
  and never stands alone (R-11). Claims are append-only: retract, never delete (R-07, R-29).
  Records are approved before the persons and links that cite them (R-26). Adoption needs the
  person's recorded yes (R-31).

Schemas: --schemas <folder>, else a `schemas` folder next to this file, else ../../schemas.
"""
# Copyright (c) 2026 OpenGenealogyAI Contributors. MIT License; see LICENSE.
from __future__ import annotations

__version__ = "1.2.0"   # the MAXGEN 2.0 kit helper; bump on any behaviour change
MAXGEN_VERSION = "2.0"  # the stamp the kit writes on documents (2.1 schemas add only optional fields)
# The newest minor version of the tree file this kit knows. 1 = the 1.2.0 bookkeeping (change log,
# transaction stamps, action log, owner tables). A kit that knows less refuses to write such a file,
# so every writer of an upgraded tree keeps the stamps, the log and the owner rules.
KNOWN_MINOR = 1

import argparse
import copy
import gzip
import hashlib
import hmac
import html as _html
import json
import os
import re
import secrets
import sqlite3
import sys
import uuid
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
SCHEMA_BASE = "https://opengenealogyai.org/schemas/maxgen/v2/"
SCHEMA_FILES = ("maxgen-defs.schema.json", "raw-record.schema.json", "person.schema.json",
                "task-queue.schema.json", "source.schema.json", "name.schema.json",
                "recognition.schema.json", "dna.schema.json", "relationship.schema.json",
                "place.schema.json", "group.schema.json", "event.schema.json")

# Kind order (spec 2.2): the first of these keys present at the top level decides the kind.
# (key, kind, table, schema file, title)
KINDS = (
    ("dna_id", "dna", "max_dna", "dna.schema.json", "MaxDNA"),
    ("recognition_id", "recognition", "max_recognition", "recognition.schema.json", "MaxRecognition"),
    ("name_id", "name", "max_name", "name.schema.json", "MaxName"),
    ("relationship_id", "relationship", "max_relationship", "relationship.schema.json", "MaxRelationship"),
    ("occurrence_id", "event", "max_event", "event.schema.json", "MaxEvent"),
    ("place_id", "place", "max_place", "place.schema.json", "MaxPlace"),
    ("group_id", "group", "max_group", "group.schema.json", "MaxGroup"),
    ("record_id", "record", "max_record", "raw-record.schema.json", "MaxRecord"),
    ("task_id", "task", "max_task", "task-queue.schema.json", "MaxTask"),
    ("source_id", "source", "max_source", "source.schema.json", "MaxSource"),
    ("person_id", "person", "max_person", "person.schema.json", "MaxPerson"),
)
KEY = {k: key for key, k, _t, _f, _n in KINDS}
TABLE = {k: t for _key, k, t, _f, _n in KINDS}
FILE = {k: f for _key, k, _t, f, _n in KINDS}
TITLE = {k: n for _key, k, _t, _f, n in KINDS}
KIND_OF_TABLE = {t: k for k, t in TABLE.items()}
STAGED_KINDS = ("record", "person", "relationship", "place", "group", "dna")
LIFECYCLE_KINDS = tuple(k for k in KEY if k != "task")
MERGE_KINDS = ("person", "record", "relationship", "place", "group", "event", "source", "name", "dna")
STAGES = ("staged", "approved", "rejected")
TASK_STATUS = ("pending", "processing", "done", "failed", "escalated", "on_hold", "cancelled")
STEP_VERDICTS = ("key_finding", "usable", "inconclusive", "ruled_out", "dead_end", "inaccessible", "infra", "other")
LIVE = ("proposed", "proven", "disputed")          # live claim statuses (4.3)
LICENCE_ORDER = {"public-domain": 0, "CC0": 0, "CC-BY": 1, "CC-BY-SA": 2, "all_rights_reserved": 3,
                 "tier2-private": 4}
ANALYSIS_TASKS = ("import_file", "resolve_conflict", "build_tree")

# The claim lists of each kind (claims are objects built on claim_header; MaxName variants and
# cognates are claim-like and are indexed the same way).
CLAIM_LISTS = {
    "person": ("name_assertions", "gender_assertions", "birth_assertions", "death_assertions",
               "event_assertions", "attribute_assertions", "non_event_assertions",
               "membership_assertions", "photo_assertions", "external_id_assertions"),
    "relationship": ("fact_assertions", "non_event_assertions", "lineage_assertions", "association_assertions"),
    "place": ("name_assertions", "kind_assertions", "parent_place_assertions", "coordinate_assertions",
              "related_place_assertions"),
    "group": ("name_assertions", "fact_assertions", "parent_group_assertions"),
    "event": ("conclusion_assertions",),
    "dna": ("matches",),
    "name": ("variants", "cognates"),
}

COUPLE_FACTS = ("marriage", "marriage_banns", "marriage_license", "marriage_contract", "marriage_notice",
                "marriage_settlement", "engagement", "separation", "divorce", "divorce_filing", "annulment",
                "civil_union", "common_law_marriage", "domestic_partnership")
MARRIAGE_FACTS = ("marriage", "marriage_license", "marriage_banns", "marriage_contract", "civil_union",
                  "common_law_marriage", "domestic_partnership")
COUPLE_EVENT_TYPES = ("marriage", "marriage_license", "marriage_banns", "marriage_contract", "marriage_notice",
                      "marriage_settlement", "engagement", "divorce", "divorce_filing", "annulment", "separation")
COUPLE_ROLES = ("principal", "bride", "groom", "spouse", "partner")
ONE_PRINCIPAL_EVENTS = ("birth", "stillbirth", "baptism", "christening", "death", "burial", "cremation")
LDS_EVENTS = ("lds_baptism", "lds_confirmation", "lds_initiatory", "lds_endowment", "lds_sealing_to_parents",
              "lds_sealing_to_spouse")
SET_KINDS = ("birth", "adoptive", "step_household", "foster", "guardianship", "sealing", "alternative_hypothesis", "other")
SET_RANK = {k: i for i, k in enumerate(("birth", "adoptive", "step_household", "foster", "guardianship",
                                        "sealing", "other", "alternative_hypothesis"))}
LINEAGE_TO_SET = {"birth_parent": "birth", "adoptive_parent": "adoptive", "step_parent": "step_household",
                  "foster_parent": "foster", "guardian": "guardianship", "sealing": "sealing"}
SPECIFIC_ORDER = ("adoptive", "step_household", "foster", "guardianship", "sealing", "birth", "other")
DEFAULT_DIMENSION = {"adoptive_parent": "legal", "step_parent": "household", "foster_parent": "household",
                     "guardian": "legal", "social_parent": "social", "gestational_carrier": "gestational",
                     "sealing": "religious"}
ADOPTION_SET_KINDS = ("adoptive", "foster", "guardianship", "sealing", "alternative_hypothesis")
ADOPTION_FACTS = ("adoptive_parent", "foster_parent", "guardian", "sealing")
ADOPTION_EVENT_TYPES = ("adoption", "guardianship", "sealing", "lds_sealing_to_parents")   # never shareable (9.4)
CENSUS_OK_LINEAGE = ("unknown", "step_parent", "adoptive_parent", "foster_parent", "guardian", "other")
UNORDERED_ASSOCIATIONS = ("neighbour", "friend", "multiple_birth", "other")
SET_WORDS = {"birth": "Birth parents", "adoptive": "Adoptive parents", "step_household": "Step-family household",
             "foster": "Foster parents", "guardianship": "Guardians", "sealing": "Sealing (church ordinance)",
             "alternative_hypothesis": "Possible parents, not settled", "other": "Parents, kind not stated"}

V1_MESSAGE = "this is a MAXGEN 1.x file (or has no schema_version); MAXGEN 2.0 does not read 1.x files"
UPDATE_MESSAGE = ("written by MAXGEN 2.%s; this kit knows 2.%d -- update the kit's schemas folder and "
                  "maxgen_db.py")
V1_DB_MESSAGE = "this genealogy.db was made by a MAXGEN 1.x kit; 2.0 cannot open it -- start a new database"
ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
AGENT_NAME_BAD = (re.compile(r"v?[0-9]+\.[0-9]+"), re.compile(r"[0-9]{4}-[0-9]{2}"))


# ------------------------------------------------------------------ small things

_RULE_IN_TEXT = re.compile(r"\((R-[0-9]{2})\)")


class Refused(Exception):
    """A gate said no. main() prints 'REFUSED: ...', rolls back and exits 1. ``rule`` is the rule
    number (R-nn), or a code such as 'maintenance' for a refusal that is not a numbered rule, or
    None; ``words`` is the plain sentence without the number."""

    def __init__(self, msg="", rule=None, words=None):
        super().__init__(msg)
        if rule is None:
            m = _RULE_IN_TEXT.search(str(msg))
            rule = m.group(1) if m else None
        self.rule = rule
        self.words = words if words is not None else str(msg)


class AlreadyDone(Refused):
    """A Gate was given an action id that action_log already holds: that action was saved before
    (a journal replayed after a crash finds it here and marks it done)."""


def refuse(msg: str, rule: str | None = None, code: str | None = None):
    raise Refused(msg + ((" (%s)" % rule) if rule else ""), rule=rule or code, words=msg)


def die(msg: str, code: int = 1):
    """Kept for callers of the 1.x kit: print a refusal and exit."""
    print("REFUSED: " + msg)
    sys.exit(code)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> _date:
    """Today's date (UTC). MAXGEN_TODAY=YYYY-MM-DD fixes it for repeatable tests."""
    fixed = os.environ.get("MAXGEN_TODAY")
    if fixed:
        try:
            return _date.fromisoformat(fixed)
        except ValueError:
            pass
    return datetime.now(timezone.utc).date()


def new_id() -> str:
    return str(uuid.uuid4())


def low(v):
    return str(v).lower() if v is not None else None


def canon(doc) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(doc) -> str:
    return hashlib.sha256(canon(doc).encode("utf-8")).hexdigest()


def strictest(*licences) -> str:
    """The strictest licence (spec 3.1); an unknown value counts as tier2-private; the
    public-domain/CC0 tie gives CC0."""
    best, rank = None, -1
    for lic in licences:
        if lic is None:
            continue
        r = LICENCE_ORDER.get(lic, 4)
        lic = lic if lic in LICENCE_ORDER else "tier2-private"
        if r > rank or (r == rank == 0 and lic == "CC0"):
            best, rank = lic, r
    return best or "CC0"


def esc(v) -> str:
    """html.escape for every value that reaches a page, None included."""
    return _html.escape("" if v is None else str(v), quote=True)


def n_people(n: int) -> str:
    return "%d %s" % (n, "person" if n == 1 else "people")


def load_json_file(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        refuse("%s does not exist" % path)
    except Exception as e:
        refuse("%s is not valid JSON: %s" % (path, e))
    if not isinstance(data, dict):
        refuse("%s must hold one JSON object" % path)
    return data


# Paths inside a document, for logged annotation writes that can be reversed exactly. A path is
# a list of steps: a key, or {"k": key, "v": value} = the list item whose key equals v.
ABSENT = {"$absent": True}


def _step(node, s):
    if isinstance(s, dict):
        for item in node if isinstance(node, list) else []:
            if isinstance(item, dict) and low(item.get(s["k"])) == low(s["v"]):
                return item
        return None
    return node.get(s) if isinstance(node, dict) else None


def path_get(doc, path):
    node = doc
    for i, s in enumerate(path):
        if i == len(path) - 1 and isinstance(s, str):
            return copy.deepcopy(node[s]) if isinstance(node, dict) and s in node else ABSENT
        node = _step(node, s)
        if node is None:
            return ABSENT
    return copy.deepcopy(node)


def path_set(doc, path, value):
    node = doc
    for s in path[:-1]:
        nxt = _step(node, s)
        if nxt is None:
            if isinstance(s, str) and isinstance(node, dict):
                node[s] = {}
                nxt = node[s]
            else:
                raise KeyError("path %r not found" % (path,))
        node = nxt
    last = path[-1]
    if not isinstance(last, str):
        raise KeyError("cannot set a list item directly at %r" % (path,))
    if value == ABSENT:
        node.pop(last, None)
    else:
        node[last] = copy.deepcopy(value)


# ------------------------------------------------------------------ schemas (spec 2.4)

# The kit-internal link request (Appendix B.1). Not a MAXGEN record type and not published; it
# is registered in the same registry so its maxgen-defs references resolve.
_D = "maxgen-defs.schema.json#/$defs/"
LINK_REQUEST_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SCHEMA_BASE + "link-request.kit.json",
    "title": "MAXGEN kit link request (input to maxgen_db.py link; not a MAXGEN record type)",
    "type": "object",
    "required": ["link_request", "kind", "evidence", "confidence"],
    "properties": {
        "link_request": {"const": "2.0"},
        "kind": {"enum": ["parents", "couple", "association", "siblings"]},
        "task_id": {"$ref": _D + "uuid"},
        "evidence": {"$ref": _D + "evidence"},
        "confidence": {"$ref": _D + "confidence"},
        "date": {"$ref": _D + "date"},
        "end_date": {"$ref": _D + "date"},
        "place": {"$ref": _D + "place_ref"},
        "notes": {"type": "array", "items": {"$ref": _D + "note"}},
        "family_id": {"$ref": _D + "uuid"},
        "new_family": {"type": "boolean"},
        "child_id": {"$ref": _D + "uuid"},
        "child_mention_id": {"$ref": _D + "label"},
        "parents": {
            "type": "array", "minItems": 1, "maxItems": 2,
            "items": {
                "type": "object", "additionalProperties": False, "required": ["person_id", "lineage"],
                "properties": {
                    "person_id": {"$ref": _D + "uuid"},
                    "parent_role": {"enum": ["father", "mother", "parent", "unknown"], "default": "unknown"},
                    "parent_role_as_written": {"type": "string", "maxLength": 200},
                    "lineage": {"enum": ["birth_parent", "adoptive_parent", "step_parent", "foster_parent",
                                         "guardian", "social_parent", "donor_parent", "gestational_carrier",
                                         "sealing", "related", "unknown", "entering_heir", "exiting_heir",
                                         "other"]},
                    "lineage_as_written": {"type": "string", "maxLength": 200},
                    "dimension": {"enum": ["genetic", "gestational", "legal", "social", "religious", "household",
                                           "unknown"]},
                    "mention_id": {"$ref": _D + "label"},
                    "evidence": {"$ref": _D + "evidence"},
                },
            },
        },
        "set_kind": {"enum": list(SET_KINDS)},
        "set_kind_as_written": {"type": "string", "maxLength": 200},
        "child_order": {"type": "integer", "minimum": 1},
        "partners": {
            "type": "array", "minItems": 1, "maxItems": 2,
            "contains": {"required": ["person_id"]},
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "person_id": {"$ref": _D + "uuid"},
                    "absent": {"const": True},
                    "label_as_written": {"type": "string", "maxLength": 200},
                    "phrase_as_written": {"type": "string", "maxLength": 1000},
                    "mention_id": {"$ref": _D + "label"},
                    "age_as_written": {"type": "string", "maxLength": 100},
                },
                "allOf": [
                    {"oneOf": [{"required": ["person_id"]}, {"required": ["absent"]}]},
                    {"if": {"required": ["absent"]},
                     "then": {"properties": {"mention_id": False, "age_as_written": False}}},
                ],
            },
        },
        "fact": {
            "type": "object", "additionalProperties": False, "required": ["fact_type"],
            "properties": {
                "fact_type": {"enum": ["marriage", "marriage_banns", "marriage_license", "marriage_contract",
                                       "marriage_notice", "marriage_settlement", "engagement", "separation",
                                       "divorce", "divorce_filing", "annulment", "civil_union",
                                       "common_law_marriage", "domestic_partnership", "number_of_children",
                                       "census", "residence", "existence", "other"]},
                "fact_type_as_written": {"type": "string", "maxLength": 200},
                "ceremony": {"enum": ["civil", "religious", "sealing", "other"]},
                "ceremony_as_written": {"type": "string", "maxLength": 200},
                "value_as_written": {"type": "string", "maxLength": 200},
            },
        },
        "union_type": {"enum": ["marriage", "civil_union", "domestic_partnership", "common_law", "unmarried",
                                "unknown", "other"]},
        "new_union": {"type": "boolean"},
        "association_type": {"enum": ["godparent", "enslaved_by", "ancestor_descendant", "employer", "neighbour",
                                      "friend", "multiple_birth", "other"]},
        "association_type_as_written": {"type": "string", "maxLength": 200},
        "generations": {"type": "integer", "minimum": 1},
        "party1": {"$ref": "#/$defs/party"},
        "party2": {"$ref": "#/$defs/party"},
        "children": {"type": "array", "minItems": 2, "items": {"$ref": _D + "uuid"}},
    },
    "allOf": [
        {"if": {"properties": {"kind": {"const": "parents"}}},
         "then": {"required": ["child_id", "parents"],
                  "properties": {"partners": False, "fact": False, "union_type": False, "new_union": False,
                                 "association_type": False, "party1": False, "party2": False,
                                 "children": False, "generations": False}}},
        {"if": {"properties": {"kind": {"const": "couple"}}},
         "then": {"required": ["partners", "fact"],
                  "properties": {"child_id": False, "parents": False, "set_kind": False, "child_order": False,
                                 "association_type": False, "party1": False, "party2": False,
                                 "children": False, "generations": False}}},
        {"if": {"properties": {"kind": {"const": "association"}}},
         "then": {"required": ["association_type", "party1", "party2"],
                  "properties": {"child_id": False, "parents": False, "set_kind": False, "child_order": False,
                                 "partners": False, "fact": False, "union_type": False, "new_union": False,
                                 "children": False, "family_id": False, "new_family": False}}},
        {"if": {"properties": {"kind": {"const": "siblings"}}},
         "then": {"required": ["children"],
                  "properties": {"child_id": False, "parents": False, "partners": False, "fact": False,
                                 "union_type": False, "new_union": False, "association_type": False,
                                 "party1": False, "party2": False, "generations": False}}},
        {"not": {"required": ["family_id", "new_family"]}},
    ],
    "$defs": {
        "party": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "person_id": {"$ref": _D + "uuid"},
                "mention": {"type": "object", "additionalProperties": False, "required": ["record_id", "mention_id"],
                            "properties": {"record_id": {"$ref": _D + "uuid"},
                                           "mention_id": {"$ref": _D + "label"}}},
                "name_as_written": {"type": "string", "maxLength": 500},
                "role": {"$ref": _D + "role"},
                "role_as_written": {"type": "string", "maxLength": 200},
            },
            "oneOf": [{"required": ["person_id"]}, {"required": ["mention"]}],
        },
    },
    "unevaluatedProperties": False,
}


def _no_network(uri: str):
    # The kit never fetches anything. An unknown $ref is an error, not a download.
    raise NoSuchResource(ref=uri)


def schemas_dir(arg: str | None = None) -> Path:
    """--schemas, else a `schemas` folder next to this script, else the repository's schemas/."""
    for cand in ([Path(arg)] if arg else []) + [HERE / "schemas", HERE.parent.parent / "schemas"]:
        if (cand / "maxgen-defs.schema.json").exists():
            return cand
    refuse("cannot find the MAXGEN 2.0 schemas folder (it holds maxgen-defs.schema.json); "
           "pass --schemas <folder>")


_NOTED_FORMATS = []


class Schemas:
    """The twelve files in one registry (spec 2.4), plus the kit's link-request schema."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.docs = {}
        for name in SCHEMA_FILES:
            path = self.folder / name
            if not path.exists():
                refuse("the schemas folder %s has no %s; the kit needs all twelve MAXGEN 2.0 files"
                       % (self.folder, name))
            doc = json.loads(path.read_text(encoding="utf-8"))
            expected = SCHEMA_BASE + name
            if doc.get("$id") != expected:
                refuse("%s has $id %r, expected %r" % (name, doc.get("$id"), expected))
            self.docs[name] = doc
        resources = [(d["$id"], Resource.from_contents(d, default_specification=DRAFT202012))
                     for d in list(self.docs.values()) + [LINK_REQUEST_SCHEMA]]
        self.registry = Registry(retrieve=_no_network).with_resources(resources)
        self._validators = {}
        self.missing_formats = [f for f in ("uri", "date-time") if f not in FormatChecker.checkers]

    def note_missing_formats(self):
        """Print, once per process, which formats this Python cannot check (never from a Gate,
        which prints nothing)."""
        if self.missing_formats and not _NOTED_FORMATS:
            _NOTED_FORMATS.append(1)
            print("NOTE: this Python cannot check %s format(s) (install rfc3987 and rfc3339-validator); "
                  "those checks are skipped" % " or ".join(self.missing_formats))

    @property
    def defs(self):
        return self.docs["maxgen-defs.schema.json"]

    def validator(self, name: str):
        if name not in self._validators:
            schema = LINK_REQUEST_SCHEMA if name == "link" else (
                self.docs[name] if name in self.docs else
                {"$schema": "https://json-schema.org/draft/2020-12/schema",
                 "$ref": "maxgen-defs.schema.json#/$defs/" + name,
                 "$id": SCHEMA_BASE + "kit-fragment-" + name + ".json"})
            self._validators[name] = Draft202012Validator(schema, registry=self.registry,
                                                          format_checker=FormatChecker())
        return self._validators[name]

    def errors(self, kind_or_file: str, doc) -> list:
        """Plain-language errors, most specific first, sorted by path (spec 2.4 error wording)."""
        name = FILE.get(kind_or_file, kind_or_file)
        v = self.validator(name)
        root = LINK_REQUEST_SCHEMA if name == "link" else self.docs.get(name, {})
        raw = list(v.iter_errors(doc))
        return plain_errors(raw, self, root)

    # -- declared property names, for unknown-field wording
    def _resolve(self, ref: str, root: dict):
        if ref.startswith("maxgen-defs.schema.json#/$defs/"):
            return self.defs["$defs"].get(ref.rsplit("/", 1)[1], {})
        if ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[1]
            if name in root.get("$defs", {}):
                return root["$defs"][name]
            return self.defs["$defs"].get(name, {})
        return {}

    def declared(self, schema, root, seen=None) -> set:
        seen = seen if seen is not None else set()
        if not isinstance(schema, dict) or id(schema) in seen:
            return set()
        seen.add(id(schema))
        names = set((schema.get("properties") or {}).keys())
        if "$ref" in schema:
            names |= self.declared(self._resolve(schema["$ref"], root), root, seen)
        for key in ("allOf", "anyOf", "oneOf"):
            for sub in schema.get(key) or []:
                names |= self.declared(sub, root, seen)
        for key in ("if", "then", "else"):
            names |= self.declared(schema.get(key), root, seen)
        return names


_SCHEMA_CACHE = {}


def load_schemas(folder: Path) -> "Schemas":
    """The registry for a folder, reused while none of the twelve files has changed."""
    folder = Path(folder).resolve()
    stamp = tuple((n, (folder / n).stat().st_mtime_ns if (folder / n).exists() else None) for n in SCHEMA_FILES)
    hit = _SCHEMA_CACHE.get(str(folder))
    if hit and hit[0] == stamp:
        return hit[1]
    schemas = Schemas(folder)
    _SCHEMA_CACHE[str(folder)] = (stamp, schemas)
    return schemas


_QUOTED = re.compile(r"'((?:[^'\\]|\\.)*)'")


def plain_one(err, keys=None) -> str:
    where = ".".join(str(p) for p in err.absolute_path) or "top level"
    if err.validator in ("additionalProperties", "unevaluatedProperties"):
        if keys is None:
            keys = [k for k in err.instance if k not in (err.schema.get("properties") or {})]
        return "%s: unknown field(s) %s -- MAXGEN has no field with that name" % (where, sorted(keys))
    if err.validator == "required":
        missing = [k for k in err.validator_value if isinstance(err.instance, dict) and k not in err.instance]
        return "%s: missing required field(s) %s" % (where, missing)
    if err.validator == "enum":
        return "%s: %r is not allowed; allowed: %s" % (where, err.instance, err.validator_value)
    if err.validator == "const":
        return "%s: must be %r, not %r" % (where, err.validator_value, err.instance)
    if err.validator is None or err.validator == "false" or "False schema" in err.message:
        return "%s: this field is not allowed here" % where
    return "%s: %s" % (where, err.message)


def plain_errors(raw, schemas: "Schemas", root: dict) -> list:
    """Report the specific errors; drop an unevaluatedProperties error whose keys are all
    declared somewhere (they only look unevaluated because a part around them failed)."""
    out = []
    others = [e for e in raw if e.validator != "unevaluatedProperties"]
    for e in sorted(raw, key=lambda e: ([str(p) for p in e.absolute_path], e.message)):
        if e.validator == "unevaluatedProperties":
            listed = _QUOTED.findall(e.message)
            declared = schemas.declared(e.schema, root)
            unknown = [k for k in listed if k not in declared] if listed else None
            if unknown == [] and others:
                continue
            out.append(plain_one(e, unknown if unknown else listed or None))
        else:
            out.append(plain_one(e))
    seen, uniq = set(), []
    for line in out:
        if line not in seen:
            seen.add(line)
            uniq.append(line)
    return uniq


def validator(folder, name: str):
    """A validator for one schema file through the registry (kept for callers of the 1.x kit,
    which passed a folder and a file name)."""
    return load_schemas(Path(folder)).validator(name)


def plain(err) -> str:
    """One error in plain words (kept for callers of the 1.x kit)."""
    return plain_one(err)


# ------------------------------------------------------------------ the reference map (Appendix C)
#
# One entry per row of Appendix C. "path"/"target" name the row's first machine path (the form
# docs/onboarding/check_maxgen.py reads: "<kind>:<dotted path>", "name[]" steps into a list, "**"
# matches any depth); "paths" lists every path of the row with its target. Targets are kinds
# (record, person, ...), "same" (the holder's own kind) or a label kind (mention, event_entry,
# region, saved_copy, part, match, step, claim, membership, label, by_kind).

# The redirects column in plain words, and whether a merged-away document named there is followed to its
# survivor before it is checked (4.1). Labels never redirect; history and task ids never do.
DOC_REDIRECTS = {"yes": True, "holder: yes": True, "person: yes": True, "record: yes; label: never": True,
                 "documents: yes": True, "documents: yes; labels: never": True, "as media_refs": True}


def _ref(row, paths, scope, redirects, new_write):
    return {"row": row, "path": paths[0][0], "target": paths[0][1], "paths": paths,
            "scope": scope, "redirects": redirects, "new_write": new_write,
            "doc_redirects": DOC_REDIRECTS.get(redirects, False)}


REFERENCES = [
    _ref("created_by_task_id, claim/membership/note/region/variant task_id, capture_task_id",
         [("*:**.created_by_task_id", "task"), ("*:**.task_id", "task"), ("*:**.capture_task_id", "task")],
         "--", "no", "exists, not cancelled; R-14 for the writing task"),
    _ref("status_task_id, lifecycle_task_id, merge/distinct/split task ids, preferred_by_task_id, "
         "place_task_id, checked_by_task_id, MaxEvent origin/preferred/related task ids",
         [("*:**.status_task_id", "task"), ("*:**.lifecycle_task_id", "task"),
          ("*:merge_history[].revert_task_id", "task"), ("relationship:children[].preferred_by_task_id", "task"),
          ("*:**.place_task_id", "task"), ("*:**.checked_by_task_id", "task"),
          ("event:preferred.set_by_task_id", "task")],
         "--", "no", "exists (written by the helper or under R-24)"),
    _ref("duplicate_of, merge_history[].merged_id, distinct_from[].id, split_history[].other_id",
         [("*:duplicate_of", "same"), ("*:merge_history[].merged_id", "same"), ("*:distinct_from[].id", "same"),
          ("*:split_history[].other_id", "same")], "--", "no (history)", "any lifecycle"),
    _ref("claim supersedes_assertion_id", [("*:**.supersedes_assertion_id", "claim")],
         "the same holder (any holder only under split)", "holder: yes", "any status"),
    _ref("evidence source_record_id", [("*:**.evidence.source_record_id", "record")], "--",
         "only for whole-record questions", "active (a merged-away record is refused, naming the survivor)"),
    _ref("evidence mention_id, subject_mention_id",
         [("*:**.evidence.mention_id", "mention"), ("*:**.evidence.subject_mention_id", "mention")],
         "on source_record_id", "never", "active"),
    _ref("evidence record_event_ref", [("*:**.evidence.record_event_ref", "event_entry")],
         "on source_record_id", "never", "active; suits the claim"),
    _ref("evidence region_id", [("*:**.evidence.region_id", "region")],
         "on a copy held by source_record_id, or in its media_refs", "never", "active"),
    _ref("evidence import_record_id", [("*:**.evidence.import_record_id", "record")], "--", "yes",
         "active, record_type tree_import"),
    _ref("evidence tree_citations[].record_id", [("*:**.evidence.tree_citations[].record_id", "record")],
         "--", "yes", "active"),
    _ref("evidence tree_citations[].media_refs[]",
         [("*:**.evidence.tree_citations[].media_refs[].held_on_record_id", "record")], "as media_refs",
         "as media_refs", "as media_refs"),
    _ref("evidence dna_id", [("*:**.evidence.dna_id", "dna")], "--", "yes", "active"),
    _ref("evidence match_ids[]", [("*:**.evidence.match_ids[]", "match")],
         "on every test whose redirect chain ends at the resolved dna_id (the named test and its merged-away shells)",
         "never (the test: yes)", "live match"),
    _ref("evidence conclusion_task_id", [("*:**.evidence.conclusion_task_id", "task")], "--", "no",
         "exists with result.proof; reliability judged when read"),
    _ref("evidence task_id + step_no", [("*:**.evidence.task_id", "step")], "on that task", "no", "exists"),
    _ref("quote_check.part_id", [("*:**.quote_check.part_id", "part")], "on the cited record", "never", "exists"),
    _ref("claim occurrence_id",
         [("person:**.occurrence_id", "occurrence"), ("relationship:**.occurrence_id", "occurrence"),
          ("group:fact_assertions[].occurrence_id", "occurrence")], "--", "yes", "active; rules of 5.1"),
    _ref("place_ref.place_id", [("*:**.place.place_id", "place"), ("record:record_place.place_id", "place")],
         "--", "yes", "active"),
    _ref("claim and mention group_id",
         [("person:**.group_id", "group"), ("record:persons_mentioned[].group_id", "group")], "--", "yes", "active"),
    _ref("media_refs[].held_on_record_id", [("*:**.media_refs[].held_on_record_id", "record")], "--", "yes",
         "active"),
    _ref("media_refs[].sha256", [("*:**.media_refs[].sha256", "saved_copy")], "on held_on_record_id", "never",
         "described there"),
    _ref("media_refs[].region_ids[]", [("*:**.media_refs[].region_ids[]", "region")], "on that copy", "never",
         "active"),
    _ref("saved_copies[].derived_from.sha256", [("record:saved_copies[].derived_from.sha256", "saved_copy")],
         "any live record", "--", "described on a live record"),
    _ref("saved_copies[].derived_from.region_id", [("record:saved_copies[].derived_from.region_id", "region")],
         "on that copy", "never", "exists"),
    _ref("MaxRecord part_of_source_id", [("record:part_of_source_id", "source")], "--", "yes", "active"),
    _ref("MaxRecord events_documented[].occurrence_id", [("record:events_documented[].occurrence_id", "occurrence")],
         "--", "yes", "exists (helper-made)"),
    _ref("MaxRecord copied_from[].record_id", [("record:copied_from[].record_id", "record")], "--", "yes", "active"),
    _ref("MaxRecord copied_from[].source_id", [("record:copied_from[].source_id", "source")], "--", "yes", "active"),
    _ref("mention relative_to, in_event_refs[], lines[]; event entry informant_mention_id",
         [("record:persons_mentioned[].relative_to", "mention"),
          ("record:persons_mentioned[].in_event_refs[]", "event_entry"),
          ("record:persons_mentioned[].lines[]", "label"),
          ("record:events_documented[].informant_mention_id", "mention")], "on the same record", "never", "exist"),
    _ref("MaxPerson photo_assertions[].photo_record_id", [("person:photo_assertions[].photo_record_id", "record")],
         "--", "yes", "active"),
    _ref("MaxPerson photo_assertions[].sha256, region_ids[]",
         [("person:photo_assertions[].sha256", "saved_copy"), ("person:photo_assertions[].region_ids[]", "region")],
         "on photo_record_id", "never", "described there; active"),
    _ref("MaxPerson attached_sources[].record_id", [("person:attached_sources[].record_id", "record")], "--",
         "yes", "active"),
    _ref("MaxPerson same_as_candidates[].person_id, task_id",
         [("person:same_as_candidates[].person_id", "person"), ("person:same_as_candidates[].task_id", "task")],
         "--", "person: yes", "active; exists"),
    _ref("MaxRelationship person ids",
         [("relationship:partners[].person_id", "person"), ("relationship:parent_id", "person"),
          ("relationship:child_id", "person"), ("relationship:children[].child_id", "person"),
          ("relationship:party1.person_id", "person"), ("relationship:party2.person_id", "person"),
          ("relationship:fact_assertions[].partners_as_recorded[].person_id", "person")], "--", "yes", "active"),
    _ref("MaxRelationship children[].parent_link_ids[]",
         [("relationship:children[].parent_link_ids[]", "relationship")], "--", "yes",
         "active, kind parent_child"),
    _ref("MaxRelationship children[].lineage_as_imported[].parent_person_id",
         [("relationship:children[].lineage_as_imported[].parent_person_id", "person")], "--", "yes", "active"),
    _ref("MaxRelationship children[].merged_into_membership_id",
         [("relationship:children[].merged_into_membership_id", "membership")], "--", "no", "any"),
    _ref("lineage parent_mention_id, child_mention_id; partners_as_recorded[].mention_id",
         [("relationship:lineage_assertions[].parent_mention_id", "mention"),
          ("relationship:lineage_assertions[].child_mention_id", "mention"),
          ("relationship:fact_assertions[].partners_as_recorded[].mention_id", "mention")],
         "on the claim's evidence.source_record_id", "never", "active"),
    _ref("association party.mention.record_id + mention_id",
         [("relationship:party1.mention.record_id", "record"), ("relationship:party2.mention.record_id", "record")],
         "the mention on that record", "record: yes; label: never", "active"),
    _ref("MaxTask parent_task_id, depends_on[]",
         [("task:parent_task_id", "task"), ("task:depends_on[]", "task")], "--", "no", "exists"),
    _ref("MaxTask payload.person_id, payload.person_ids[]",
         [("task:payload.person_id", "person"), ("task:payload.person_ids[]", "person")], "--", "yes",
         "any lifecycle"),
    _ref("MaxTask payload.record_id", [("task:payload.record_id", "record")], "--", "yes", "any lifecycle"),
    _ref("MaxTask payload.target_task_id, result.review.target_task_id, result.proof.supersedes_task_id",
         [("task:payload.target_task_id", "task"), ("task:result.review.target_task_id", "task"),
          ("task:result.proof.supersedes_task_id", "task")], "--", "no", "exists"),
    _ref("MaxTask result.steps[].source_id, record_id, person_id",
         [("task:result.steps[].source_id", "source"), ("task:result.steps[].record_id", "record"),
          ("task:result.steps[].person_id", "person")], "--", "yes", "any lifecycle"),
    _ref("MaxTask result.proof.subject[].id, id2",
         [("task:result.proof.subject[].id", "by_kind"), ("task:result.proof.subject[].id2", "person")],
         "a mention on its record; a membership through merged_into_membership_id",
         "documents: yes; labels: never", "any lifecycle"),
    _ref("MaxTask result.proof.relied_on[]", [("task:result.proof.relied_on[]", "by_kind")],
         "labels on their record, test or task", "documents: yes; labels: never",
         "live claim, active record, existing step or proof"),
    _ref("MaxTask result.proof.conclusion.settles[].assertion_id",
         [("task:result.proof.conclusion.settles[].assertion_id", "claim")], "--", "holder: yes", "live"),
    _ref("MaxTask result.proof.conclusion.membership_updates[].membership_id, add_parent_link_ids[]",
         [("task:result.proof.conclusion.membership_updates[].membership_id", "membership"),
          ("task:result.proof.conclusion.membership_updates[].add_parent_link_ids[]", "relationship")], "--",
         "membership: through merged_into_membership_id (a non-reverted chain); links: yes",
         "active (the end of the chain); links: live parent_child of the same child"),
    _ref("MaxTask result.occurrence_verdicts[].occurrence_ids[], survivor_id",
         [("task:result.occurrence_verdicts[].occurrence_ids[]", "occurrence"),
          ("task:result.occurrence_verdicts[].survivor_id", "occurrence")], "--", "yes", "active"),
    _ref("MaxTask result.run_decisions[].id, replacement_id",
         [("task:result.run_decisions[].id", "by_kind"), ("task:result.run_decisions[].replacement_id", "by_kind")],
         "as the retract id forms say", "documents: yes", "any"),
    _ref("MaxTask result.associates[].person_id; associates[].mention.record_id + mention_id",
         [("task:result.associates[].person_id", "person"), ("task:result.associates[].mention.record_id", "record")],
         "the mention on its record", "documents: yes; labels: never", "active"),
    _ref("MaxSource parent_source_id, holdings[].repository_source_id",
         [("source:parent_source_id", "source"), ("source:holdings[].repository_source_id", "source")],
         "--", "yes", "active"),
    _ref("MaxSource coverage.places[].place.place_id", [("source:coverage.places[].place.place_id", "place")],
         "--", "yes", "active"),
    _ref("MaxPlace parent_place_id, related-place place_id",
         [("place:parent_place_assertions[].parent_place_id", "place"),
          ("place:related_place_assertions[].place_id", "place")], "--", "yes", "active"),
    _ref("MaxPlace cited_record_ids[]", [("place:cited_record_ids[]", "record")], "--", "yes", "active"),
    _ref("MaxGroup parent-group group_id", [("group:parent_group_assertions[].group_id", "group")], "--", "yes",
         "active"),
    _ref("MaxGroup observed_in[].record_id + record_event_ref",
         [("group:observed_in[].record_id", "record"), ("group:observed_in[].record_event_ref", "event_entry")],
         "the entry on that record", "record: yes; label: never", "active"),
    _ref("MaxEvent origin.record_id + record_event_ref",
         [("event:origin.record_id", "record"), ("event:origin.record_event_ref", "event_entry")],
         "the entry on that record", "no (the identity of the entry)", "exists"),
    _ref("MaxEvent preferred.date_assertion_id, place_assertion_id, order[]",
         [("event:preferred.date_assertion_id", "claim"), ("event:preferred.place_assertion_id", "claim"),
          ("event:preferred.order[]", "claim")], "--", "holder: yes", "live"),
    _ref("MaxEvent related[].occurrence_id", [("event:related[].occurrence_id", "occurrence")], "--", "yes", "active"),
    _ref("MaxName cognates[].related_name_id", [("name:cognates[].related_name_id", "name")], "--", "yes", "active"),
    _ref("MaxRecognition raw_record_id", [("recognition:raw_record_id", "record")], "--", "yes", "active"),
    _ref("MaxRecognition copy_sha256, region_id",
         [("recognition:copy_sha256", "saved_copy"), ("recognition:region_id", "region")],
         "on raw_record_id (or the live record holding the copy)", "never", "described; active"),
    _ref("MaxDNA person_id, matches[].match_person_id, common_ancestor_candidates[].person_id",
         [("dna:person_id", "person"), ("dna:matches[].match_person_id", "person"),
          ("dna:matches[].common_ancestor_candidates[].person_id", "person")], "--", "yes", "active"),
    _ref("MaxDNA source_record_ids[]", [("dna:source_record_ids[]", "record")], "--", "yes", "active"),
    _ref("MaxDNA matches[].match_dna_id", [("dna:matches[].match_dna_id", "dna")], "--", "yes", "active"),
    _ref("MaxDNA matches[].triangulated_with[]", [("dna:matches[].triangulated_with[]", "match")],
         "on the same test", "never", "live match"),
]
TARGET_KIND = {"occurrence": "event"}     # Appendix C calls a MaxEvent an occurrence


def ref_target(target, holder_kind):
    """The kind a reference-map target names (None for a label), or the holder's own kind."""
    if target == "same":
        return holder_kind
    target = TARGET_KIND.get(target, target)
    return target if target in KEY else None


NOT_REFERENCES = ("outside_ids[].id", "external_id_claim.external_id", "kit_aliases[].id_hash",
                  "MaxRecognition consensus_group_id", "provenance agent_id, run_id, party",
                  "MaxTask created_by, assigned_to", "extensions",
                  "an item's own id (assertion_id, membership_id, match_id, region_id, variant_id, cognate_id, "
                  "dispute_id)")
# The same list, machine-readable: property names that hold an item's own id (or an id that names nothing in
# this database), used by the reference-map test (A-72).
OWN_ID_FIELDS = ("assertion_id", "membership_id", "match_id", "region_id", "variant_id", "cognate_id", "dispute_id",
                 "consensus_group_id")


def values_at(node, segments):
    """Values at a dotted path ('a.b[].c'; '**' matches any depth), as check_maxgen.py reads them."""
    if not segments:
        yield node
        return
    seg, rest = segments[0], segments[1:]
    if seg == "**":
        yield from values_at(node, rest)
        if isinstance(node, dict):
            for key, value in node.items():
                if key != "extensions":
                    yield from values_at(value, segments)
        elif isinstance(node, list):
            for value in node:
                yield from values_at(value, segments)
        return
    if isinstance(node, list):
        for value in node:
            yield from values_at(value, segments)
        return
    if not isinstance(node, dict):
        return
    many = seg.endswith("[]")
    key = seg[:-2] if many else seg
    if key not in node:
        return
    value = node[key]
    if many and isinstance(value, list):
        for item in value:
            yield from values_at(item, rest)
    elif not many:
        yield from values_at(value, rest)


# ------------------------------------------------------------------ the database (spec 7.1)

DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS max_record (
  record_id TEXT PRIMARY KEY, record_type TEXT NOT NULL, redistribution_license TEXT NOT NULL,
  is_living_flag INTEGER NOT NULL, source_class TEXT NOT NULL,
  stage TEXT NOT NULL DEFAULT 'staged', lifecycle TEXT NOT NULL DEFAULT 'active', duplicate_of TEXT,
  original_key TEXT, original_key_partial INTEGER, url_key TEXT, transcription_sha256 TEXT,
  doc TEXT NOT NULL, doc_sha256 TEXT NOT NULL, added_by TEXT, added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_record_stage ON max_record(stage, record_type);
CREATE UNIQUE INDEX IF NOT EXISTS ux_record_key ON max_record(original_key)
  WHERE original_key IS NOT NULL AND original_key_partial = 0 AND lifecycle = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS ux_record_url ON max_record(url_key)
  WHERE url_key IS NOT NULL AND lifecycle = 'active';

CREATE TABLE IF NOT EXISTS max_person (
  person_id TEXT PRIMARY KEY, display_name TEXT, person_kind TEXT NOT NULL, is_living INTEGER NOT NULL,
  stage TEXT NOT NULL DEFAULT 'staged', lifecycle TEXT NOT NULL DEFAULT 'active', duplicate_of TEXT,
  doc TEXT NOT NULL, doc_sha256 TEXT NOT NULL, added_by TEXT, added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_person_stage ON max_person(stage, display_name);

CREATE TABLE IF NOT EXISTS max_relationship (
  relationship_id TEXT PRIMARY KEY, kind TEXT NOT NULL, parent_id TEXT, child_id TEXT,
  association_type TEXT, party1 TEXT, party2 TEXT, is_living_flag INTEGER NOT NULL,
  stage TEXT NOT NULL DEFAULT 'staged', lifecycle TEXT NOT NULL DEFAULT 'active', duplicate_of TEXT,
  doc TEXT NOT NULL, doc_sha256 TEXT NOT NULL, added_by TEXT, added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_parent_child ON max_relationship(parent_id, child_id)
  WHERE kind = 'parent_child' AND lifecycle = 'active';
CREATE INDEX IF NOT EXISTS ix_rel_stage ON max_relationship(stage, kind);

CREATE TABLE IF NOT EXISTS max_task (
  task_id TEXT PRIMARY KEY, task_type TEXT NOT NULL, status TEXT NOT NULL, priority INTEGER, verdict TEXT,
  payload_kind TEXT, target_task_id TEXT, is_living_flag INTEGER NOT NULL,
  doc TEXT NOT NULL, doc_sha256 TEXT NOT NULL, added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_task_status ON max_task(status, priority DESC);
CREATE INDEX IF NOT EXISTS ix_task_target ON max_task(target_task_id);

CREATE TABLE IF NOT EXISTS max_source (
  source_id TEXT PRIMARY KEY, name TEXT NOT NULL, source_type TEXT NOT NULL, redistribution_license TEXT NOT NULL,
  lifecycle TEXT NOT NULL DEFAULT 'active', doc TEXT NOT NULL, doc_sha256 TEXT NOT NULL,
  added_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS max_place (
  place_id TEXT PRIMARY KEY, display_name TEXT, stage TEXT NOT NULL DEFAULT 'staged',
  lifecycle TEXT NOT NULL DEFAULT 'active', duplicate_of TEXT, doc TEXT NOT NULL, doc_sha256 TEXT NOT NULL,
  added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS max_group (
  group_id TEXT PRIMARY KEY, group_kind TEXT NOT NULL, display_name TEXT, stage TEXT NOT NULL DEFAULT 'staged',
  lifecycle TEXT NOT NULL DEFAULT 'active', duplicate_of TEXT, doc TEXT NOT NULL, doc_sha256 TEXT NOT NULL,
  added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS max_event (
  occurrence_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, origin_record_id TEXT, origin_ref TEXT,
  owner_kind TEXT, owner_id TEXT,
  lifecycle TEXT NOT NULL DEFAULT 'active', duplicate_of TEXT,
  doc TEXT NOT NULL, doc_sha256 TEXT NOT NULL, added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_event_origin ON max_event(origin_record_id, origin_ref)
  WHERE origin_record_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS max_name (
  name_id TEXT PRIMARY KEY, canonical_form TEXT NOT NULL, name_type TEXT NOT NULL,
  lifecycle TEXT NOT NULL DEFAULT 'active', doc TEXT NOT NULL, doc_sha256 TEXT NOT NULL,
  added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS max_recognition (
  recognition_id TEXT PRIMARY KEY, raw_record_id TEXT, lifecycle TEXT NOT NULL DEFAULT 'active',
  doc TEXT NOT NULL, doc_sha256 TEXT NOT NULL, added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS max_dna (
  dna_id TEXT PRIMARY KEY, person_id TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'staged',
  lifecycle TEXT NOT NULL DEFAULT 'active', doc TEXT NOT NULL, doc_sha256 TEXT NOT NULL,
  added_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS claim_index (
  assertion_id TEXT PRIMARY KEY, holder_table TEXT NOT NULL, holder_id TEXT NOT NULL,
  effective_holder_id TEXT NOT NULL,
  list_name TEXT NOT NULL,
  fact_type TEXT, status TEXT NOT NULL, task_id TEXT, basis TEXT, source_record_id TEXT, mention_id TEXT,
  record_event_ref TEXT, occurrence_id TEXT, import_record_id TEXT, dna_id TEXT, conclusion_task_id TEXT,
  year_lo INTEGER, year_hi INTEGER, confidence REAL, capped INTEGER NOT NULL DEFAULT 0,
  is_private INTEGER NOT NULL DEFAULT 0, quote_status TEXT);
CREATE INDEX IF NOT EXISTS ix_claim_holder ON claim_index(holder_table, holder_id);
CREATE INDEX IF NOT EXISTS ix_claim_effective ON claim_index(holder_table, effective_holder_id);
CREATE INDEX IF NOT EXISTS ix_claim_record ON claim_index(source_record_id);
CREATE INDEX IF NOT EXISTS ix_claim_occ ON claim_index(occurrence_id);
CREATE INDEX IF NOT EXISTS ix_claim_status ON claim_index(status);
CREATE TABLE IF NOT EXISTS link_index (
  person_id TEXT NOT NULL,
  stored_person_id TEXT NOT NULL,
  relationship_id TEXT NOT NULL,
  role TEXT NOT NULL,
  PRIMARY KEY (person_id, relationship_id, role, stored_person_id));
CREATE TABLE IF NOT EXISTS membership_index (
  membership_id TEXT PRIMARY KEY,
  family_id TEXT NOT NULL,
  child_id TEXT NOT NULL,
  set_kind TEXT NOT NULL,
  is_preferred INTEGER NOT NULL,
  preferred_by_task_id TEXT,
  status TEXT NOT NULL,
  merged_into_membership_id TEXT,
  parent_link_ids TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_membership_child ON membership_index(child_id);
CREATE TABLE IF NOT EXISTS id_redirect (
  id INTEGER PRIMARY KEY AUTOINCREMENT, old_id TEXT NOT NULL, kind TEXT NOT NULL, new_id TEXT NOT NULL,
  task_id TEXT NOT NULL, merged_at TEXT NOT NULL, reverted_at TEXT, revert_task_id TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_redirect_live ON id_redirect(old_id) WHERE reverted_at IS NULL;
CREATE TABLE IF NOT EXISTS mention_index (
  record_id TEXT NOT NULL, mention_id TEXT NOT NULL, name_as_written TEXT, is_living INTEGER NOT NULL,
  status TEXT NOT NULL, PRIMARY KEY (record_id, mention_id));
CREATE TABLE IF NOT EXISTS media_index (
  sha256 TEXT NOT NULL, held_on_record_id TEXT NOT NULL, copy_kind TEXT, content_type TEXT, bytes INTEGER,
  PRIMARY KEY (sha256, held_on_record_id));
CREATE TABLE IF NOT EXISTS media_location (
  record_id TEXT NOT NULL, copy_index INTEGER NOT NULL, sha256 TEXT, uri TEXT NOT NULL, verified_at TEXT,
  is_public INTEGER NOT NULL DEFAULT 0, pending INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS region_index (
  region_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, record_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS search_step (
  task_id TEXT NOT NULL, step_no INTEGER NOT NULL,
  person_id TEXT,
  source TEXT NOT NULL, source_id TEXT,
  query TEXT NOT NULL, found TEXT NOT NULL, verdict TEXT NOT NULL, record_id TEXT, at TEXT,
  PRIMARY KEY (task_id, step_no));
CREATE VIEW IF NOT EXISTS dead_end AS
  SELECT person_id, source, query, found AS note, at, task_id, step_no, verdict
  FROM search_step WHERE verdict IN ('dead_end', 'inaccessible');

CREATE TABLE IF NOT EXISTS review (
  id INTEGER PRIMARY KEY AUTOINCREMENT, target_id TEXT NOT NULL, table_name TEXT NOT NULL,
  verdict TEXT NOT NULL, by TEXT NOT NULL, note TEXT, at TEXT NOT NULL, approved_ids TEXT);
CREATE TABLE IF NOT EXISTS run_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS agent_session (
  session_id TEXT PRIMARY KEY, provenance TEXT NOT NULL, started_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS status_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, assertion_id TEXT NOT NULL, old_status TEXT, new_status TEXT NOT NULL,
  task_id TEXT, reason TEXT, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS annotation_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL, target_id TEXT NOT NULL, field TEXT NOT NULL,
  old_value TEXT, new_value TEXT, task_id TEXT, batch_id TEXT, at TEXT NOT NULL);
"""
DATA_TABLES = tuple(TABLE[k] for k in KEY)
INDEX_TABLES = ("claim_index", "link_index", "membership_index", "mention_index", "media_index",
                "media_location", "region_index", "search_step")
LOG_TABLES = ("review", "run_log", "agent_session", "status_log", "annotation_log", "id_redirect")
ALL_TABLES = DATA_TABLES + INDEX_TABLES + LOG_TABLES + ("meta",)
META_DEFAULTS = {"maxgen_version": MAXGEN_VERSION, "max_minor_written": "0", "about_years_vital": "5",
                 "about_years_other": "2", "living_years_birth": "110", "living_years_family": "90"}

# ------------------------------------------------------------------ kit 1.2.0 bookkeeping (design 02 4.1 to 4.4)
#
# Kit-local tables: never in a MAXGEN file, a shareable export or a registry card. `init` makes them;
# `upgrade` adds them to an older tree after a checked backup copy. Nothing in them is deleted, except
# that the change log and the transaction stamps are trimmed (older than 30 days and beyond the newest
# 200,000 changes) and old undo recipes are dropped (365 days for the person's own actions, 30 for
# everyone else's; design 00 8.3 FF-11).

OWNER_TABLES = ("owner_setting", "owner_branch", "owner_rule", "owner_mark", "owner_tag", "owner_list")
BOOKKEEPING_TABLES = ("write_tx", "change_log", "action_log") + OWNER_TABLES
# The key of each owner table: the newest row per key is the current state ('set'), or nothing ('clear').
OWNER_KEYS = {"owner_setting": ("key",), "owner_branch": ("branch_id",), "owner_rule": ("rule_id",),
              "owner_mark": ("kind", "target_kind", "target_id", "mark_key"), "owner_tag": ("tag_id",),
              "owner_list": ("list_id",)}
OWNER_VIEWS = tuple(t + "_now" for t in OWNER_TABLES)
# Every table whose rows the triggers log, with the column each row is logged by.
LOGGED_TABLES = (tuple((TABLE[k], KEY[k]) for k in KEY)
                 + (("review", "id"), ("id_redirect", "id"), ("meta", "key"), ("action_log", "action_id"))
                 + tuple((t, "row_no") for t in OWNER_TABLES))
# The kit never deletes a row of these, so a 'd' row in change_log for one of them means something other
# than the kit changed the file (design 02 4.2: a delete is an alarm).
NEVER_DELETED = DATA_TABLES + ("review",) + OWNER_TABLES
TRIGGERS = tuple("mg_log_%s_%s" % (t, op) for t, _k in LOGGED_TABLES for op in "iud")

OWNER_MARK_KINDS = ("hold_back", "lock", "pick", "bookmark", "tag", "not_a_problem", "task_archived",
                    "outside_holdings", "private", "main_photo", "main_copy")
OWNER_TARGET_KINDS = ("person", "record", "relationship", "task", "source", "place", "group", "name", "event",
                      "dna_match", "claim", "branch")
# Per-tree owner settings (design 00 8.5.2); they travel with the tree, never in a MAXGEN file.
OWNER_SETTING_KEYS = ("outgoing_mode", "incoming_default", "threshold", "chart_includes", "chart_defaults",
                      "date_style", "name_order", "married_name_in_brackets", "calendars", "check_limits",
                      "show_alerts", "credit_name", "show_credit_default", "duplicate_strictness", "show_ordinances",
                      "colour_grandparent_lines", "origin_tree_labels", "prepared_for", "custom_fact_kinds",
                      "research_sites", "agent_batch_size", "spending_limit", "progress_preference",
                      "report_defaults", "map_view", "agent_view")
OWNER_SETTING_VALUES = {"outgoing_mode": ("all_died", "all_except_held_back", "picked"),
                        "incoming_default": ("everything", "my_rules", "review"),
                        "chart_includes": ("checked_and_unchecked", "checked_only"),
                        "agent_view": ("no_living_no_dna", "everything")}
OWNER_BRANCH_KINDS = ("ancestors_of", "descendants_of", "whole_tree")
OWNER_RULE_MODES = ("everything", "my_rules", "review")
OWNER_LIST_KINDS = ("search", "fixed", "view", "book")
OWNER_JSON_MAX = 65536          # the longest JSON value an owner row may hold
TREE_KINDS = ("own", "friend", "one_name", "sample", "staging")
# The meta allow-list (K-7): the only meta keys the Gate's set_tree_meta knows. tree_id is made once;
# owner_key_check is written only with the owner key itself (upgrade).
TREE_META_KEYS = ("tree_id", "tree_name", "home_person_id", "tree_kind", "owner_key_check")
TREE_NAME_MAX = 120
WRITER_CODES = ("you", "agent", "import", "quality_control", "genealogist", "orchestrator", "other")
TRIM_EVERY = 10000              # the log is trimmed in a commit where change_no passes a multiple of this,
KEEP_CHANGES = 200000           # always keeping at least the newest this many changes
KEEP_DAYS = 30                  # and every change newer than this
UNDO_DAYS_YOU, UNDO_DAYS_OTHERS = 365, 30
MAINTENANCE_SUFFIX = ".pp-maintenance"
MAINTENANCE_WORDS = ("Probable Pedigree is restoring or moving this tree, so nothing can be saved to it just now; "
                     "wait until it has finished, then try again")
HARD_LINK_WORDS = ("this tree's file has a second name on this computer (a hard link), so a save made through one "
                   "name could be missed through the other; nothing was saved. Remove the extra name, keep one "
                   "file, then try again")
OLD_FORMAT_WORDS = ("this tree was made by an older kit, so it has no change log, transaction stamps or owner "
                    "tables yet; run `python maxgen_db.py upgrade %s` once (it saves a checked backup copy "
                    "first), then try again")
PERSON_ROLE_WORDS = ("the role and the name \"person\" belong to the person's own session in Probable Pedigree, "
                     "so the command line cannot make a session with them; give your own role (Orchestrator, "
                     "Genealogist, Quality Control or another), and record the person's answer with "
                     "`task ... --answer`")
OWNER_WORDS = ("only the person, in their own Probable Pedigree, can change who is held back, what is shared, "
               "picks, locks, bookmarks, tags, saved lists and the tree's other owner settings")
LOCK_WORDS = ("person %s is locked by the person who owns this tree, so no AI, importer or sync session may "
              "change them; ask the person to unlock them first")

_OWNER_TAIL = ("op TEXT NOT NULL CHECK (op IN ('set','clear')), session_id TEXT NOT NULL, action_id TEXT, "
               "at TEXT NOT NULL")


def _now_view(table: str) -> str:
    match = " AND ".join("x.%s = t.%s" % (k, k) for k in OWNER_KEYS[table])
    return ("CREATE VIEW IF NOT EXISTS %s_now AS SELECT t.* FROM %s t WHERE t.op = 'set' AND t.row_no = "
            "(SELECT max(x.row_no) FROM %s x WHERE %s)" % (table, table, table, match))


# Tables, indexes and views, one statement each (never executescript inside a transaction: it commits).
BOOKKEEPING_DDL = (
    "CREATE TABLE IF NOT EXISTS write_tx (tx_no INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
    "writer_code TEXT NOT NULL, channel TEXT, action_id TEXT, started_at TEXT NOT NULL, "
    "closed INTEGER NOT NULL DEFAULT 0, first_change INTEGER, last_change INTEGER, ended_at TEXT)",
    "CREATE INDEX IF NOT EXISTS ix_write_tx_open ON write_tx(tx_no) WHERE closed = 0",
    "CREATE TABLE IF NOT EXISTS change_log (change_no INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT NOT NULL, "
    "row_key TEXT NOT NULL, op TEXT NOT NULL CHECK (op IN ('i','u','d')), tx_no INTEGER, at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS ix_change_tbl ON change_log(tbl, change_no)",
    "CREATE TABLE IF NOT EXISTS action_log (action_id TEXT PRIMARY KEY, tx_no INTEGER NOT NULL, "
    "action_name TEXT NOT NULL, session_id TEXT NOT NULL, writer_code TEXT NOT NULL, first_change INTEGER, "
    "last_change INTEGER, summary_key TEXT NOT NULL, summary_args TEXT, undo_json TEXT, undoes TEXT, "
    "undone_by TEXT, at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS ix_action_at ON action_log(at)",
    "CREATE TABLE IF NOT EXISTS owner_setting (row_no INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, "
    "value_json TEXT, " + _OWNER_TAIL + ")",
    "CREATE INDEX IF NOT EXISTS ix_owner_setting ON owner_setting(key, row_no)",
    "CREATE TABLE IF NOT EXISTS owner_branch (row_no INTEGER PRIMARY KEY AUTOINCREMENT, branch_id TEXT NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('ancestors_of','descendants_of','whole_tree')), person_id TEXT, "
    "name TEXT, " + _OWNER_TAIL + ")",
    "CREATE INDEX IF NOT EXISTS ix_owner_branch ON owner_branch(branch_id, row_no)",
    "CREATE TABLE IF NOT EXISTS owner_rule (row_no INTEGER PRIMARY KEY AUTOINCREMENT, rule_id TEXT NOT NULL, "
    "branch_id TEXT NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('everything','my_rules','review')), "
    "rules_json TEXT, " + _OWNER_TAIL + ")",
    "CREATE INDEX IF NOT EXISTS ix_owner_rule ON owner_rule(rule_id, row_no)",
    "CREATE TABLE IF NOT EXISTS owner_mark (row_no INTEGER PRIMARY KEY AUTOINCREMENT, mark_id TEXT NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN (%s)), target_kind TEXT NOT NULL, target_id TEXT NOT NULL, "
    "mark_key TEXT NOT NULL DEFAULT '', value_json TEXT, reason TEXT, " % ",".join("'%s'" % k for k in OWNER_MARK_KINDS)
    + _OWNER_TAIL + ")",
    "CREATE INDEX IF NOT EXISTS ix_owner_mark_key ON owner_mark(kind, target_kind, target_id, mark_key, row_no)",
    "CREATE INDEX IF NOT EXISTS ix_owner_mark_target ON owner_mark(target_id)",
    "CREATE TABLE IF NOT EXISTS owner_tag (row_no INTEGER PRIMARY KEY AUTOINCREMENT, tag_id TEXT NOT NULL, "
    "name TEXT, colour_token TEXT, " + _OWNER_TAIL + ")",
    "CREATE INDEX IF NOT EXISTS ix_owner_tag ON owner_tag(tag_id, row_no)",
    "CREATE TABLE IF NOT EXISTS owner_list (row_no INTEGER PRIMARY KEY AUTOINCREMENT, list_id TEXT NOT NULL, "
    "name TEXT, definition_json TEXT, " + _OWNER_TAIL + ")",
    "CREATE INDEX IF NOT EXISTS ix_owner_list ON owner_list(list_id, row_no)",
) + tuple(_now_view(t) for t in OWNER_TABLES)


def trigger_ddl() -> tuple:
    """The triggers that fill change_log: after every insert, update and delete on each logged table,
    one row with the table, the row's key, the operation, the open transaction stamp (NULL when the
    writer did not stamp one: raw SQL or an older kit) and the time."""
    out = []
    for tbl, key in LOGGED_TABLES:
        for op, event, row in (("i", "INSERT", "NEW"), ("u", "UPDATE", "NEW"), ("d", "DELETE", "OLD")):
            out.append("CREATE TRIGGER IF NOT EXISTS mg_log_%s_%s AFTER %s ON %s BEGIN "
                       "INSERT INTO change_log(tbl, row_key, op, tx_no, at) VALUES ('%s', %s.%s, '%s', "
                       "(SELECT max(tx_no) FROM write_tx WHERE closed = 0), "
                       "strftime('%%Y-%%m-%%dT%%H:%%M:%%SZ', 'now')); END" % (tbl, op, event, tbl, tbl, row, key, op))
    return tuple(out)


# Per connection, temporary (never in the file): what a write transaction is about to change, so the
# undo recipe is written from what was really done (design 02 4.3). A trigger's own conflict clause is
# overridden by the outer statement's (INSERT OR REPLACE INTO meta), so these use NOT EXISTS.
CAPTURE_DDL = (
    "CREATE TEMP TABLE IF NOT EXISTS mg_old_doc (tbl TEXT NOT NULL, id TEXT NOT NULL, doc TEXT, "
    "PRIMARY KEY (tbl, id))",
    "CREATE TEMP TABLE IF NOT EXISTS mg_meta_old (key TEXT PRIMARY KEY, value TEXT)",
) + tuple(
    "CREATE TEMP TRIGGER IF NOT EXISTS mg_cap_%s BEFORE UPDATE ON main.%s BEGIN INSERT INTO mg_old_doc(tbl, id, doc) "
    "SELECT '%s', OLD.%s, OLD.doc WHERE NOT EXISTS (SELECT 1 FROM mg_old_doc WHERE tbl = '%s' AND id = OLD.%s); END"
    % (TABLE[k], TABLE[k], TABLE[k], KEY[k], TABLE[k], KEY[k]) for k in KEY
) + (
    "CREATE TEMP TRIGGER IF NOT EXISTS mg_cap_meta_i BEFORE INSERT ON main.meta BEGIN INSERT INTO mg_meta_old(key, value) "
    "SELECT NEW.key, (SELECT value FROM main.meta WHERE key = NEW.key) "
    "WHERE NOT EXISTS (SELECT 1 FROM mg_meta_old WHERE key = NEW.key); END",
    "CREATE TEMP TRIGGER IF NOT EXISTS mg_cap_meta_u BEFORE UPDATE ON main.meta BEGIN INSERT INTO mg_meta_old(key, value) "
    "SELECT OLD.key, OLD.value WHERE NOT EXISTS (SELECT 1 FROM mg_meta_old WHERE key = OLD.key); END",
)


def has_table(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def missing_bookkeeping(con) -> list:
    """The 1.2.0 tables, views and triggers this file lacks (all of them for a tree made by kit 1.1.0)."""
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view','trigger')")}
    return [n for n in BOOKKEEPING_TABLES + OWNER_VIEWS + TRIGGERS if n not in have]


# Columns a later 2.0 kit added to a table that was already there: (table, column, type). A tree made
# before one existed is given it only inside a write's own turn, after that write's maintenance,
# older-tree and newer-kit checks (so they refuse it too and it is part of the stamped transaction),
# or by `upgrade` after its checked backup copy. Never on opening, and never on its own.
ADDED_COLUMNS = (("review", "approved_ids", "TEXT"),)


# Indexes a later 1.2.0 kit added for speed (design 02 4.6, K-10): (name, statement). Like ADDED_COLUMNS
# they are made inside a write's own turn when missing, or by `upgrade`; a tree without one still reads
# right, only more slowly. They change no data, so nothing in change_log or an undo recipe mentions them.
ADDED_INDEXES = (
    ("ix_redirect_new", "CREATE INDEX IF NOT EXISTS ix_redirect_new ON id_redirect(new_id) WHERE reverted_at IS NULL"),
)


def missing_indexes(con) -> list:
    """The ADDED_INDEXES this file lacks, by name (read only)."""
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    return [name for name, _stmt in ADDED_INDEXES if name not in have]


def add_missing_indexes(con) -> list:
    """Make the indexes missing_indexes names, inside a write's own transaction, and return them."""
    if not con.in_transaction:
        raise RuntimeError("a missing index is made only inside a write's own transaction")
    added = missing_indexes(con)
    for name, stmt in ADDED_INDEXES:
        if name in added:
            con.execute(stmt)
    return added


def missing_columns(con) -> list:
    """The ADDED_COLUMNS this file's tables lack, as 'table.column' (read only)."""
    out = []
    for table, column, _type in ADDED_COLUMNS:
        have = {r[1] for r in con.execute('PRAGMA table_info("%s")' % table).fetchall()}
        if have and column not in have:
            out.append("%s.%s" % (table, column))
    return out


def add_missing_columns(con) -> list:
    """Add the columns missing_columns names, and return them. Only inside a write transaction whose
    writer holds the turn and has made its checks: never in autocommit, where it would be saved at once,
    outside any refusal and with no stamp."""
    if not con.in_transaction:
        raise RuntimeError("a missing column is added only inside a write's own transaction")
    added = missing_columns(con)
    types = {"%s.%s" % (t, c): ty for t, c, ty in ADDED_COLUMNS}
    for name in added:
        table, column = name.split(".")
        con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, types[name]))   # the kit's own names
    return added


def tree_file(db) -> str:
    """The one name of a tree's file. Every kit connection opens it by this name, and every file the
    kit keeps beside a tree (the maintenance marker, an upgrade's backup copy) is named from it. It is
    the full path, with any other spelling of the same file turned into the file's real name: on
    Windows a relative path, trailing dots and spaces (the file system drops them), capitals, forward
    slashes, a short 8.3 name, a symbolic link or a junction. Two spellings must never reach SQLite,
    because it names a tree's write-ahead log after the name it was opened with: each spelling would
    get a log of its own and miss the other's saves, and the marker would be looked for in the wrong
    place (fix round 4)."""
    return os.path.realpath(os.path.abspath(os.fspath(db)))


def extra_names(db) -> int:
    """How many other names (hard links) the tree's file has. A hard link has no real name to turn
    into, so a write through it would get a log of its own; the kit refuses to write such a file."""
    try:
        return max(0, int(os.stat(tree_file(db)).st_nlink) - 1)
    except (OSError, ValueError):
        return 0


def refuse_unsafe_tree_file(db):
    """The checks every writer makes on the tree's file itself, while it holds the turn (or, for init
    and a checkpoint, before it opens the file): the maintenance marker, then a second name."""
    if maintenance_path(db).exists():
        refuse(MAINTENANCE_WORDS, code="maintenance")
    if extra_names(db):
        refuse(HARD_LINK_WORDS, code="tree_file")


def maintenance_path(db) -> Path:
    """While this file exists beside the tree (a restore or move is running), every write is refused.
    It is named from the tree's real name (tree_file), whatever spelling of the path was given."""
    return Path(tree_file(db) + MAINTENANCE_SUFFIX)


def change_seq(con) -> int:
    """The newest change number this connection's snapshot sees (0 before any change, or with no log)."""
    try:
        row = con.execute("SELECT max(change_no) FROM change_log").fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0)


def tree_format(con) -> dict:
    """What a program needs before it opens a tree, read only: {'version': '2.<minor>', 'minor': n,
    'bookkeeping': True when every 1.2.0 table, view and trigger is there, 'newer': True when a newer kit
    wrote it (this kit opens it read-only), 'seq': the newest change number}."""
    def meta(key):
        try:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row else None
    version = str(meta("maxgen_version") or "")
    m = re.match(r"^2\.([0-9]{1,4})$", version)
    try:
        written = int(meta("max_minor_written") or 0)
    except ValueError:
        written = 0
    minor = max(written, int(m.group(1)) if m else 0)
    return {"version": "2.%d" % minor if version.startswith("2.") else version, "minor": minor,
            "bookkeeping": not missing_bookkeeping(con), "newer": written > KNOWN_MINOR, "seq": change_seq(con)}


_OPEN: list = []   # every connection main() opened, closed in its finally (Windows holds file locks)


BUSY_TIMEOUT_MS = 5000


def connect(db: str, track: bool = True) -> sqlite3.Connection:
    """A read-write connection with the 1.2.0 durability settings (design 00 4.5.1 b): every
    committed save survives a power cut (synchronous=FULL in WAL mode; fullfsync on a Mac), and a
    writer waits up to 5 seconds for another writer's turn. ``track=False`` keeps it off the
    module's list of open connections (a Gate owns its connection and closes it itself). It opens the
    tree by its real name (tree_file), so every spelling of the path shares one write-ahead log."""
    con = sqlite3.connect(tree_file(db), isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
        con.execute("PRAGMA synchronous=FULL")
        if sys.platform == "darwin":
            con.execute("PRAGMA fullfsync=ON")
            con.execute("PRAGMA checkpoint_fullfsync=ON")
    except Exception:
        con.close()
        raise
    if track:
        _OPEN.append(con)
    return con


def close_all():
    while _OPEN:
        try:
            _OPEN.pop().close()
        except Exception:
            pass


# ------------------------------------------------------------------ dates (spec 3.2)
#
# The date block stores the date as stated, never its bounds. Bounds, checks and display are
# worked out here when read. Day numbers are Julian Day Numbers on the proleptic Gregorian
# calendar, so any two dates compare directly; a BC year Y is astronomical year 1 - Y.

VITAL_EVENTS = ("birth", "baptism", "christening", "stillbirth", "death")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
SEASONS = {"spring": (3, 5), "summer": (6, 8), "autumn": (9, 11), "winter": (12, 14)}


def _gleap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _jleap(y):
    return y % 4 == 0


def _mdays(y, m, julian=False):
    if m == 2:
        return 29 if (_jleap(y) if julian else _gleap(y)) else 28
    return 30 if m in (4, 6, 9, 11) else 31


def jdn_gregorian(y, m, d):
    shift = 0
    while y + 4800 <= 0:            # keep the formula in its range for very early years
        y += 400
        shift += 146097
    a = (14 - m) // 12
    y2, m2 = y + 4800 - a, m + 12 * a - 3
    return d + (153 * m2 + 2) // 5 + 365 * y2 + y2 // 4 - y2 // 100 + y2 // 400 - 32045 - shift


def jdn_julian(y, m, d):
    shift = 0
    while y + 4800 <= 0:
        y += 4
        shift += 1461
    a = (14 - m) // 12
    y2, m2 = y + 4800 - a, m + 12 * a - 3
    return d + (153 * m2 + 2) // 5 + 365 * y2 + y2 // 4 - 32083 - shift


def gregorian_of(jdn):
    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - 146097 * b // 4
    d = (4 * c + 3) // 1461
    e = c - 1461 * d // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = 100 * b + d - 4800 + m // 10
    return year, month, day


def _astro(point):
    y = int(point.get("year", 0))
    return 1 - y if point.get("epoch") == "bce" else y


def _point_range(point, calendar, precision):
    """(first day, last day) of one stated point, in Gregorian day numbers, plus a basis word."""
    y = _astro(point)
    month, day = point.get("month"), point.get("day")
    julian = calendar in ("julian", "swedish")
    basis = "exact"
    if calendar == "hebrew":
        if month and day:
            basis = "approximate_calendar"
        return jdn_gregorian(y - 3761, 9, 1), jdn_gregorian(y - 3760, 10, 15), "approximate_calendar"
    if calendar == "french_republican":
        start = jdn_gregorian(1791 + y, 9, 22)
        if month and day:
            d0 = start + 30 * (int(month) - 1) + int(day) - 1
            return d0, d0 + 1, "approximate_calendar"
        return start, jdn_gregorian(1792 + y, 9, 22), "approximate_calendar"
    if calendar == "islamic":
        g = int(622 + (y - 1) * 0.970224)
        return jdn_gregorian(g, 1, 1), jdn_gregorian(g + 1, 12, 31), "approximate_calendar"
    if calendar == "persian":
        return jdn_gregorian(y + 621, 3, 19), jdn_gregorian(y + 622, 3, 21), "approximate_calendar"
    to_jdn = jdn_julian if julian else jdn_gregorian
    if precision == "decade":
        lo, hi = to_jdn(y, 1, 1), to_jdn(y + 9, 12, 31)
    elif precision == "century":
        lo, hi = to_jdn(y, 1, 1), to_jdn(y + 99, 12, 31)
    elif month and day:
        m = min(int(month), 12)
        d = min(int(day), _mdays(y, m, julian))
        lo = hi = to_jdn(y, m, d)
    elif month:
        m = min(int(month), 12)
        lo, hi = to_jdn(y, m, 1), to_jdn(y, m, _mdays(y, m, julian))
    elif point.get("quarter"):
        q = int(point["quarter"])
        m1, m2 = 3 * q - 2, 3 * q
        lo, hi = to_jdn(y, m1, 1), to_jdn(y, m2, _mdays(y, m2, julian))
    elif point.get("season") in SEASONS:
        m1, m2 = SEASONS[point["season"]]
        lo = to_jdn(y, m1, 1)
        hi = to_jdn(y + 1, m2 - 12, _mdays(y + 1, m2 - 12, julian)) if m2 > 12 else to_jdn(y, m2, _mdays(y, m2, julian))
    else:
        lo, hi = to_jdn(y, 1, 1), to_jdn(y, 12, 31)
    if calendar == "swedish":
        lo, hi = lo - 1, hi + 1
    if calendar in (None, "unknown", "other"):
        lo, hi = lo - 13, hi + 13
        basis = "calendar_not_stated"
    return lo, hi, basis


def _shift_years(day, years):
    """The same calendar day some whole years earlier or later (29 Feb falls back to 28 Feb)."""
    y, mo, d = gregorian_of(day)
    y += years
    return jdn_gregorian(y, mo, min(d, _mdays(y, mo)))


def date_settings(con=None) -> dict:
    s = {"about_years_vital": 5, "about_years_other": 2, "living_years_birth": 110, "living_years_family": 90}
    if con is not None:
        try:
            for r in con.execute("SELECT key, value FROM meta"):
                if r["key"] in s:
                    s[r["key"]] = int(r["value"])
        except Exception:
            pass
    # the living thresholds are floors: a mistaken meta row can never make people dead, and so shareable
    s["living_years_birth"] = max(110, s["living_years_birth"])
    s["living_years_family"] = max(90, s["living_years_family"])
    return s


def date_bounds(date, event_class="other", settings=None):
    """(earliest day, latest day) as Gregorian day numbers; None for an open end; (None, None)
    for a date with no bounds (text_only, or no usable value)."""
    if not isinstance(date, dict):
        return None, None
    settings = settings or date_settings()
    modifier = date.get("modifier", "on")
    if modifier == "text_only" or not isinstance(date.get("value"), dict) or "year" not in date["value"]:
        return None, None
    cal, prec = date.get("calendar"), date.get("precision")
    lo, hi, _basis = _point_range(date["value"], cal, prec)
    if modifier in ("between", "from_to", "or") and isinstance(date.get("value2"), dict) and "year" in date["value2"]:
        _l2, hi, _b2 = _point_range(date["value2"], cal, prec)
    width = 0
    if modifier == "about" or date.get("approximate") or date.get("source_hedge") == "circa":
        width = settings["about_years_vital"] if event_class in VITAL_EVENTS else settings["about_years_other"]
    if width:
        lo, hi = _shift_years(lo, -width), _shift_years(hi, width)
    if modifier == "before":
        return None, lo - 1
    if modifier == "by":
        return None, hi
    if modifier == "after":
        return hi + 1, None
    if modifier == "from":
        return lo, None
    if modifier == "to":
        return None, hi
    if modifier not in ("on", "about", "between", "from_to", "or"):
        return None, None        # an unknown modifier: undated (kit-internal enum, fails closed)
    return lo, hi


def year_span(date, event_class="other", settings=None):
    """(earliest year, latest year), None for an open end."""
    lo, hi = date_bounds(date, event_class, settings)
    return (gregorian_of(lo)[0] if lo is not None else None,
            gregorian_of(hi)[0] if hi is not None else None)


def _fold(s, mode):
    s = re.sub(r"\s+", " ", s.strip()) if mode != "exact" else s
    return s.lower() if mode == "case_folded" else s


def text_contains(haystack: str, needle: str) -> bool:
    for mode in ("exact", "whitespace_folded", "case_folded"):
        if _fold(needle, mode) in _fold(haystack, mode):
            return True
    return False


QUAKER_ORDINAL = re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:mo\b|month)", re.I)


def date_check(date, today_day=None):
    """(problems, warnings) for one date block. Problems refuse the write (R-12)."""
    problems, warnings = [], []
    if not isinstance(date, dict):
        return problems, warnings
    cal = date.get("calendar")
    for key in ("value", "value2"):
        p = date.get(key)
        if not isinstance(p, dict) or not p.get("day") or not p.get("month"):
            continue
        y, m, d = _astro(p), int(p["month"]), int(p["day"])
        if m > 12 or cal not in (None, "gregorian", "julian", "unknown", "swedish"):
            continue
        julian_ok = d <= _mdays(y, m, True)
        greg_ok = d <= _mdays(y, m, False)
        ok = julian_ok if cal in ("julian", "swedish") else greg_ok if cal == "gregorian" else (julian_ok or greg_ok)
        if not ok:
            problems.append("%s: day %d does not exist in %s %s" % (key, d, MONTHS[m - 1], p["year"]))
    if isinstance(date.get("value"), dict) and isinstance(date.get("value2"), dict):
        lo1, _h1, _b = _point_range(date["value"], cal, date.get("precision"))
        lo2, _h2, _b = _point_range(date["value2"], cal, date.get("precision"))
        if lo2 < lo1:
            problems.append("value2 is earlier than value")
    if date.get("dual_dated") and isinstance(date.get("value"), dict):
        if date["value"].get("month") == 3 and int(date["value"].get("day") or 0) > 24:
            problems.append("a dual date in March must be on or before 24 March")
    if today_day is not None:
        lo, _hi = date_bounds(date)
        if lo is not None and lo > today_day:
            problems.append("the date is after today")
    if date.get("style") == "quaker_numbered" and isinstance(date.get("value"), dict):
        y, m = date["value"].get("year"), date["value"].get("month")
        hit = QUAKER_ORDINAL.search(date.get("as_written") or "")
        if hit and m and y and int(y) < 1752 and int(hit.group(1)) == int(m):
            warnings.append("Quaker date %r: value.month equals the written ordinal; before 1752 the "
                            "first month is March (the ten-month error, MC-3.3)" % date.get("as_written"))
    return problems, warnings


def date_display(date) -> str:
    """Plain words: '20 Feb 1924', 'about 1850', 'between 1849 and 1851', '44 BC' ..."""
    if not isinstance(date, dict):
        return ""
    modifier = date.get("modifier", "on")
    if modifier == "text_only" or not isinstance(date.get("value"), dict):
        if date.get("as_written"):
            return date["as_written"]
        if isinstance(date.get("as_entered"), dict):
            return "%s (as another program gave it)" % date["as_entered"].get("text", "")
        return ""

    def point(p):
        y = p.get("year")
        bc = " BC" if p.get("epoch") == "bce" else ""
        if date.get("dual_dated") and p is date.get("value") and y:
            ys = "%d/%02d" % (int(y) - 1, int(y) % 100)
        else:
            ys = "%s" % y
        m, d = p.get("month"), p.get("day")
        if m and 1 <= int(m) <= 12:
            return ("%d %s %s%s" % (int(d), MONTHS[int(m) - 1], ys, bc)) if d else "%s %s%s" % (MONTHS[int(m) - 1], ys, bc)
        if p.get("season"):
            return "%s %s%s" % (p["season"], ys, bc)
        if p.get("quarter"):
            return "Q%s %s%s" % (p["quarter"], ys, bc)
        return ys + bc

    v1 = point(date["value"])
    v2 = point(date["value2"]) if isinstance(date.get("value2"), dict) else ""
    words = {"on": "%s", "about": "about %s", "before": "before %s", "by": "by %s", "after": "after %s",
             "from": "from %s", "to": "until %s"}
    if modifier in ("between", "from_to"):
        out = ("between %s and %s" if modifier == "between" else "from %s to %s") % (v1, v2)
    elif modifier == "or":
        out = "%s or %s" % (v1, v2)
    else:
        out = words.get(modifier, "%s") % v1
    if date.get("approximate") and modifier in ("between", "from_to"):
        out = "about " + out
    if date.get("calendar") == "julian":
        out += " (Julian)"
    elif date.get("calendar") not in (None, "gregorian", "julian", "unknown"):
        out += " (%s calendar)" % str(date.get("calendar")).replace("_", " ")
    if date.get("derivation") == "calculated":
        out += " (worked out)"
    return out


def date_year(date, event_class="other", settings=None):
    """One year to print on a chart: the stated year, or the earliest of a range."""
    if not isinstance(date, dict) or not isinstance(date.get("value"), dict):
        return None
    y = date["value"].get("year")
    if not isinstance(y, int):
        return None
    return -y + 1 if date["value"].get("epoch") == "bce" else y


# ------------------------------------------------------------------ the command context

MERGED_INTO_SQL = ("WITH RECURSIVE m(id) AS (SELECT ? UNION SELECT r.old_id FROM id_redirect r JOIN m ON r.new_id = m.id "
                   "WHERE r.reverted_at IS NULL) SELECT id FROM m")


class Ctx:
    """One command's view of the database: the connection, the schemas, the session and the
    caches of things worked out when read (living, reliability). Writes happen inside one
    transaction; a refusal rolls everything back."""

    def __init__(self, db, schemas_arg=None, write=False, as_session=None, quiet=False, track=True,
                 upgrading=False):
        self.db = db
        self.schemas = load_schemas(schemas_dir(schemas_arg))
        if not quiet:
            self.schemas.note_missing_formats()
        if write and extra_names(db):
            # refused before the file is opened at all: opening it by a second name would already
            # give that name a write-ahead log of its own (fix round 4)
            refuse(HARD_LINK_WORDS, code="tree_file")
        self.con = connect(db, track=track)
        self.write = write
        self.as_session = as_session
        self.session = None
        self.batch = None
        self.quiet = quiet
        self.today = today()
        self.today_day = jdn_gregorian(self.today.year, self.today.month, self.today.day)
        self._living = {}
        self._warned = set()
        self.warnings = []
        self.tx_no = None          # this write transaction's write_tx row (kit 1.2.0)
        self.action = None         # what a Gate says this transaction is (id, name, words key, numbers)
        self.command = None        # the command-line command, for the action log
        self._locks = None
        try:
            self.check_database()
            self.settings = date_settings(self.con)
            if write:
                self.con.execute("BEGIN IMMEDIATE")
                # Every check of a write is made while this writer holds the turn (so a restore that
                # makes the marker, or a newer kit's write, never races a writer that already looked),
                # and nothing is written before all of them have passed. The marker is looked for
                # beside the tree's real name, whatever spelling of the path was given (fix round 4).
                refuse_unsafe_tree_file(db)
                written = int(self.meta("max_minor_written") or 0)
                if written > KNOWN_MINOR:
                    refuse(UPDATE_MESSAGE % (written, KNOWN_MINOR))
                if not upgrading:
                    if missing_bookkeeping(self.con):
                        refuse(OLD_FORMAT_WORDS % Path(tree_file(db)).name, code="old_format")
                    # a tree made before a column existed gets it here, in this transaction, which
                    # begin_bookkeeping stamps; `upgrade` adds it after its backup copy instead
                    add_missing_columns(self.con)
                    add_missing_indexes(self.con)
                    self.begin_bookkeeping()
        except BaseException:
            self.close()
            raise

    # -- opening
    def check_database(self):
        """Is this a MAXGEN 2 tree at all. It only reads: a write's own checks come after BEGIN IMMEDIATE."""
        names = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        if "meta" not in names:
            refuse(V1_DB_MESSAGE)
        version = self.meta("maxgen_version")
        if not version or not str(version).startswith("2."):
            refuse(V1_DB_MESSAGE)

    def meta(self, key):
        row = self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (key, str(value)))

    def commit(self):
        if self.write:
            if int(self.meta("max_minor_written") or 0) < KNOWN_MINOR:
                self.set_meta("max_minor_written", KNOWN_MINOR)
            if self.tx_no is not None:
                self.finish_bookkeeping()
            self.con.execute("COMMIT")
            self.write = False
            self.tx_no = None

    def rollback(self):
        if self.write:
            try:
                self.con.execute("ROLLBACK")
            except Exception:
                pass
            self.write = False
            self.tx_no = None

    # -- kit 1.2.0: the transaction stamp, the action log and its undo recipe (design 02 4.2, 4.3)
    def begin_bookkeeping(self):
        """Stamp this write transaction (one write_tx row, its first statement) and start noting what
        it changes. The triggers point every change it makes at this row."""
        for stmt in CAPTURE_DDL:
            self.con.execute(stmt)
        self.con.execute("DELETE FROM temp.mg_old_doc")
        self.con.execute("DELETE FROM temp.mg_meta_old")
        self._change_start = change_seq(self.con)
        self._annotation_start = int(self.con.execute("SELECT max(id) FROM annotation_log").fetchone()[0] or 0)
        sid = self.as_session or os.environ.get("MAXGEN_SESSION") or "-"
        cur = self.con.execute("INSERT INTO write_tx(session_id, writer_code, started_at) VALUES (?,?,?)",
                               (str(sid), "other", now()))
        self.tx_no = cur.lastrowid
        self._stamp_session()

    def _stamp_session(self):
        if self.tx_no is None or self.session is None:
            return
        prov = self.session["provenance"]
        self.con.execute("UPDATE write_tx SET session_id=?, writer_code=?, channel=? WHERE tx_no=?",
                         (self.session["session_id"], writer_code(self.session), prov.get("channel"), self.tx_no))

    def finish_bookkeeping(self):
        """Before COMMIT: the undo recipe from what this transaction really did, the trim, the
        action_log row (always for a Gate; for a command, when it changed something) and the closed
        stamp."""
        end = change_seq(self.con)
        first, last = (self._change_start + 1, end) if end > self._change_start else (None, None)
        act = self.action or {}
        steps = [] if (act.get("no_undo") or last is None) else self.undo_steps(first, last)
        if last is not None:
            self._trim(last)
        action_id = None
        if act or last is not None:
            sess = self.session or {"session_id": str(self.as_session or "-"), "provenance": {}}
            action_id = act.get("action_id") or new_id()
            name = act.get("action_name") or ("kit." + str(self.command or "write").replace("-", "_"))
            key = act.get("summary_key") or name
            args = act.get("summary_args")
            if args is None:
                args = {"changes": (last - first + 1) if last else 0,
                        "added": sum(1 for s in steps if s.get("op") == "withdraw")}
            self.con.execute(
                "INSERT INTO action_log(action_id, tx_no, action_name, session_id, writer_code, first_change, "
                "last_change, summary_key, summary_args, undo_json, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (action_id, self.tx_no, name, sess["session_id"], writer_code(sess), first, last, key,
                 canon(args), canon({"v": 1, "steps": steps}) if steps else None, now()))
        done = change_seq(self.con)
        self.con.execute("UPDATE write_tx SET closed=1, action_id=?, first_change=?, last_change=?, ended_at=? "
                         "WHERE tx_no=?", (action_id, self._change_start + 1 if done > self._change_start else None,
                                           done if done > self._change_start else None, now(), self.tx_no))

    def _trim(self, last):
        """In a commit where change_no has just passed a multiple of TRIM_EVERY: drop change_log and
        write_tx rows older than KEEP_DAYS, always keeping the newest KEEP_CHANGES changes, and drop
        undo recipes past their keeping time (FF-11). So every writer keeps the log small."""
        if last // TRIM_EVERY == self._change_start // TRIM_EVERY:
            return
        moment = datetime.now(timezone.utc)

        def ago(days):
            return (moment - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        keep_from = last - KEEP_CHANGES
        if keep_from > 0:
            self.con.execute("DELETE FROM change_log WHERE change_no <= ? AND at < ?", (keep_from, ago(KEEP_DAYS)))
        oldest = self.con.execute("SELECT min(change_no) FROM change_log").fetchone()[0]
        if oldest is not None:
            self.con.execute("DELETE FROM write_tx WHERE closed = 1 AND started_at < ? AND "
                             "(last_change IS NULL OR last_change < ?)", (ago(KEEP_DAYS), oldest))
        self.con.execute("UPDATE action_log SET undo_json = NULL WHERE undo_json IS NOT NULL AND "
                         "((writer_code = 'you' AND at < ?) OR (writer_code <> 'you' AND at < ?))",
                         (ago(UNDO_DAYS_YOU), ago(UNDO_DAYS_OTHERS)))

    def undo_steps(self, first, last) -> list:
        """The compensating steps for every change from change number first to last, in the order
        they happened (undo runs them backwards): withdraw what was added, reinstate what was
        withdrawn, unmerge a merge, restore an owner row, a meta value, a task's status, or the
        helper's annotations (design 02 9.6)."""
        rows = self.con.execute("SELECT change_no, tbl, row_key, op FROM change_log WHERE change_no BETWEEN ? AND ? "
                                "ORDER BY change_no", (first, last)).fetchall()
        steps, done = [], set()
        last_note = int(self.con.execute("SELECT max(id) FROM annotation_log").fetchone()[0] or 0)
        if last_note > self._annotation_start:
            steps.append({"op": "annotation_restore", "first_id": self._annotation_start + 1, "last_id": last_note})
        new_records = {r["row_key"] for r in rows if r["tbl"] == "max_record" and r["op"] == "i"}
        for r in rows:
            tbl, key, op = r["tbl"], r["row_key"], r["op"]
            if (tbl, key) in done or op == "d":
                continue
            if tbl in KIND_OF_TABLE:
                done.add((tbl, key))
                kind = KIND_OF_TABLE[tbl]
                if op == "i":
                    steps.extend(_new_doc_steps(self, kind, key, new_records))
                    continue
                old = self.con.execute("SELECT doc FROM temp.mg_old_doc WHERE tbl=? AND id=?", (tbl, key)).fetchone()
                new = self.con.execute("SELECT doc FROM %s WHERE %s=?" % (tbl, KEY[kind]), (key,)).fetchone()
                if old and new and old[0] != new[0]:
                    steps.extend(_doc_change_steps(kind, key, json.loads(old[0]), json.loads(new[0])))
            elif tbl == "id_redirect":
                done.add((tbl, key))
                rr = self.con.execute("SELECT * FROM id_redirect WHERE id=?", (int(key),)).fetchone()
                if rr is not None and op == "i":
                    steps.append({"op": "unmerge", "kind": rr["kind"], "absorbed": rr["old_id"], "task": rr["task_id"]})
                elif rr is not None and rr["reverted_at"]:
                    steps.append({"op": "merge_again", "kind": rr["kind"], "survivor": rr["new_id"],
                                  "absorbed": rr["old_id"]})
            elif tbl == "meta":
                done.add((tbl, key))
                if key not in ("max_minor_written", "kit_key"):
                    old = self.con.execute("SELECT value FROM temp.mg_meta_old WHERE key=?", (key,)).fetchone()
                    steps.append({"op": "meta_restore", "key": key, "old": old[0] if old else None})
            elif tbl in OWNER_TABLES and op == "i":
                done.add((tbl, key))
                row = self.con.execute("SELECT * FROM %s WHERE row_no=?" % tbl, (int(key),)).fetchone()
                cols = OWNER_KEYS[tbl]
                prev = self.con.execute("SELECT max(row_no) FROM %s WHERE %s AND row_no < ?"
                                        % (tbl, " AND ".join("%s=?" % c for c in cols)),
                                        [row[c] for c in cols] + [row["row_no"]]).fetchone()[0]
                steps.append({"op": "owner_restore", "table": tbl, "row_no": row["row_no"], "previous_row_no": prev})
        return steps

    def check_lock(self, kind, doc):
        """R-34: an AI, importer or sync session never changes a locked person, nor a family link of
        one. The person's own session and Quality Control's reviews (which change only the stage)
        still may; annotation writes (a raised living floor) are not changes the lock is about."""
        if self._locks is None:
            self._locks = has_table(self.con, "owner_mark") and self.con.execute(
                "SELECT 1 FROM owner_mark WHERE kind='lock' LIMIT 1").fetchone() is not None
        if not self._locks:
            return
        if self.require_session()["provenance"].get("agent_kind") not in ("ai", "importer", "sync_bridge"):
            return
        persons = [low(doc.get("person_id"))] if kind == "person" else relationship_persons(self, doc)
        for pid in persons:
            if pid and person_locked(self, pid):
                refuse(LOCK_WORDS % pid, "R-34")

    def close(self):
        """Close this context's connection (Windows holds file locks on open databases)."""
        self.rollback()
        try:
            self.con.close()
        finally:
            if self.con in _OPEN:
                _OPEN.remove(self.con)

    def log(self, kind, detail):
        self.con.execute("INSERT INTO run_log(at,kind,detail) VALUES (?,?,?)", (now(), kind, detail))

    def warn(self, msg, once_key=None):
        if once_key is not None:
            if once_key in self._warned:
                return
            self._warned.add(once_key)
        self.warnings.append(msg)
        if not self.quiet:
            print("WARNING: " + msg)

    def forget(self):
        """Drop what was worked out when read (after a write changes the data)."""
        self._living = {}

    # -- rows and documents
    def row(self, kind, ident):
        if ident is None:
            return None
        return self.con.execute("SELECT * FROM %s WHERE %s=?" % (TABLE[kind], KEY[kind]),
                                (low(ident),)).fetchone()

    def doc(self, kind, ident):
        r = self.row(kind, ident)
        return json.loads(r["doc"]) if r else None

    def kind_of_id(self, ident):
        for k in KEY:
            if self.row(k, ident) is not None:
                return k
        return None

    def resolve(self, ident):
        """Follow non-reverted redirects (4.1); refuse a cycle."""
        if ident is None:
            return None
        cur, seen = low(ident), set()
        while True:
            r = self.con.execute("SELECT new_id FROM id_redirect WHERE old_id=? AND reverted_at IS NULL",
                                 (cur,)).fetchone()
            if not r:
                return cur
            if cur in seen:
                refuse("the merge redirects for %s form a loop" % ident)
            seen.add(cur)
            cur = r["new_id"]

    def merged_into(self, ident):
        """Every id whose redirect chain ends at ident (ident included). One recursive query over the
        speed index ix_redirect_new (K-10; design 02 4.6), walking the live redirects backwards from
        ident. It gives the same answer as reading every redirect and following each one forwards: an
        id that is itself merged away is the end of no chain but its own (a kit test compares the two
        on random chains of merges)."""
        target = low(ident)
        if self.con.execute("SELECT 1 FROM id_redirect WHERE old_id=? AND reverted_at IS NULL", (target,)).fetchone():
            return {target}
        rows = self.con.execute(MERGED_INTO_SQL, (target,)).fetchall()
        return {r[0] for r in rows} | {target}

    # -- writing documents
    def store(self, kind, doc, new=False, stage=None, added_by=None, content=True):
        """Store one document (after the helper has filled it), update its index rows, and
        re-stage it on a content write (R-27)."""
        if content and kind in ("person", "relationship") and not (new and kind == "person"):
            self.check_lock(kind, doc)
        stamp = now()
        if new:
            doc.setdefault("created_at", stamp)
        doc["updated_at"] = stamp if (new or content) else doc.get("updated_at", stamp)
        if kind in LIFECYCLE_KINDS:
            doc.setdefault("lifecycle", "active")
        stored_check(self, kind, doc)
        cols = derived_columns(kind, doc)
        cols["doc"] = canon(doc)
        cols["doc_sha256"] = sha256_of(doc)
        cols["updated_at"] = doc["updated_at"]
        table = TABLE[kind]
        if new:
            cols["added_at"] = doc.get("created_at") or stamp
            if kind in STAGED_KINDS:
                cols["stage"] = stage or "staged"
            if table in ("max_record", "max_person", "max_relationship"):
                cols["added_by"] = added_by
            names = list(cols)
            self.con.execute("INSERT INTO %s (%s) VALUES (%s)" % (table, ",".join(names), ",".join("?" * len(names))),
                             [cols[n] for n in names])
        else:
            if kind in STAGED_KINDS and (stage or content):
                cols["stage"] = stage or "staged"
            ident = cols.pop(KEY[kind])
            self.con.execute("UPDATE %s SET %s WHERE %s=?" % (table, ",".join("%s=?" % n for n in cols), KEY[kind]),
                             list(cols.values()) + [ident])
        index_doc(self, kind, doc)
        self.forget()

    def annotate(self, kind, doc, path, value, task_id=None, batch=None):
        """One helper-owned field changed (an annotation write, 7.3), logged so it can be undone."""
        old = path_get(doc, path)
        if old == value or (old == ABSENT and value is None):
            return False
        path_set(doc, path, value if value is not None else ABSENT)
        self.con.execute("INSERT INTO annotation_log(table_name,target_id,field,old_value,new_value,task_id,"
                         "batch_id,at) VALUES (?,?,?,?,?,?,?,?)",
                         (TABLE[kind], low(doc[KEY[kind]]), json.dumps(path), json.dumps(old), json.dumps(value),
                          task_id, batch or self.batch, now()))
        return True

    def set_status(self, claim, status, task_id, reason):
        old = claim.get("status")
        claim["status"] = status
        claim["status_task_id"] = task_id
        if "variant_id" not in claim and "cognate_id" not in claim:      # MaxName items have no status_at
            claim["status_at"] = now()
        if reason:
            claim["status_reason"] = reason[:4000]
        self.con.execute("INSERT INTO status_log(assertion_id,old_status,new_status,task_id,reason,at) "
                         "VALUES (?,?,?,?,?,?)", (claim.get("assertion_id"), old, status, task_id, reason, now()))

    # -- sessions (8.1)
    def require_session(self):
        if self.session is not None:
            return self.session
        sid = self.as_session or os.environ.get("MAXGEN_SESSION")
        if not sid:
            refuse("every write runs under a session: run `maxgen_db.py session` once and pass --as <session_id> "
                   "or set MAXGEN_SESSION", "R-14")
        r = self.con.execute("SELECT * FROM agent_session WHERE session_id=?", (sid,)).fetchone()
        if not r:
            refuse("session %s is not known to this database; run `maxgen_db.py session` first" % sid, "R-14")
        self.session = {"session_id": sid, "provenance": json.loads(r["provenance"])}
        self._stamp_session()
        return self.session

    @property
    def agent_id(self):
        return self.require_session()["provenance"]["agent_id"]

    def database_id(self):
        return self.meta("database_id")


def writer_code(session) -> str:
    """The kind of writer a session is (write_tx.writer_code, action_log.writer_code): you (only the
    person's own session, person-page-<database id>, which is_person_session tells; a session that
    merely calls itself a human person is not it), import (an importer, whatever its role),
    quality_control, genealogist, orchestrator (the kit's three roles), agent (any other AI session,
    such as the person's own assistant) or other. ``session`` is {"session_id", "provenance"}."""
    if is_person_session(session):
        return "you"
    prov = (session or {}).get("provenance") or {}
    role = re.sub(r"[^a-z]", "", str(prov.get("role") or "").lower())
    kind = prov.get("agent_kind")
    if kind == "importer":
        return "import"
    if role == "qualitycontrol":
        return "quality_control"
    if role in ("genealogist", "orchestrator"):
        return role
    if kind == "ai":
        return "agent"
    return "other"


def person_locked(ctx, pid) -> bool:
    """A lock mark is on this person or on any id merged into them (fails closed, design 02 4.4)."""
    if not has_table(ctx.con, "owner_mark_now"):
        return False
    ids = sorted(ctx.merged_into(ctx.resolve(pid)) | {low(pid)})
    return ctx.con.execute("SELECT 1 FROM owner_mark_now WHERE kind='lock' AND target_kind='person' AND target_id IN "
                           "(%s) LIMIT 1" % ",".join("?" * len(ids)), ids).fetchone() is not None


def _item_key(c):
    for k in ("assertion_id", "match_id", "variant_id", "cognate_id"):
        if c.get(k):
            return k, low(c[k])
    return None, None


def _part_items(kind, ident, doc) -> dict:
    """{retract target: status} for the parts of a document that have ids of their own."""
    out = {}
    if kind == "relationship":
        for m in doc.get("children") or []:
            if m.get("membership_id"):
                out[low(m["membership_id"])] = m.get("status", "active")
        for p in doc.get("partners") or []:
            who = "absent" if p.get("absent") else low(p.get("person_id"))
            if who:
                out["%s#partner:%s" % (ident, who)] = p.get("status", "active")
    elif kind == "record":
        for m in doc.get("persons_mentioned") or []:
            out["%s#%s" % (ident, m.get("mention_id"))] = m.get("status", "active")
        for e in doc.get("events_documented") or []:
            out["%s#event:%s" % (ident, e.get("ref"))] = e.get("status", "active")
        for c in doc.get("saved_copies") or []:
            if c.get("sha256"):
                out["%s#copy:%s" % (ident, c["sha256"])] = "active"
            for r in c.get("regions") or []:
                if r.get("region_id"):
                    out[low(r["region_id"])] = r.get("status", "active")
    return out


def _new_doc_steps(ctx, kind, ident, new_records) -> list:
    if kind == "event":
        row = ctx.con.execute("SELECT origin_record_id FROM max_event WHERE occurrence_id=?", (ident,)).fetchone()
        if row and row["origin_record_id"] in new_records:
            return []                   # withdrawing its record withdraws it too
    if kind == "task":
        doc = ctx.doc("task", ident) or {}
        if (doc.get("payload") or {}).get("kind") == "person_page_session":
            return []                   # the person's session task holds later work too
    return [{"op": "withdraw", "kind": kind, "target": ident}]


def _doc_change_steps(kind, ident, old, new) -> list:
    """What undoes one document's change: compare it before and after."""
    steps = []
    if (old.get("lifecycle") or "active") == "active" and new.get("lifecycle") == "retracted":
        steps.append({"op": "reinstate", "kind": kind, "target": ident})
    if kind == "task" and old.get("status") != new.get("status"):
        steps.append({"op": "task_status", "task_id": ident, "old": old.get("status")})
    before = {}
    for c in iter_all_claims(old):
        k, v = _item_key(c)
        if v:
            before[v] = c.get("status", "proposed")
    for c in iter_all_claims(new):
        k, v = _item_key(c)
        if not v or k not in ("assertion_id", "match_id"):
            continue
        target = v if k == "assertion_id" else "%s#match:%s" % (ident, v)
        status = c.get("status", "proposed")
        if v not in before:
            steps.append({"op": "withdraw", "target": target})
        elif before[v] != status:
            if before[v] in LIVE and status == "retracted":
                steps.append({"op": "reinstate", "target": target, "status": before[v]})
            else:
                steps.append({"op": "status_restore", "target": target, "old": before[v], "new": status})
    was, now_ = _part_items(kind, ident, old), _part_items(kind, ident, new)
    for target, status in now_.items():
        if target not in was:
            steps.append({"op": "withdraw", "target": target})
        elif was[target] == "active" and status == "retracted":
            steps.append({"op": "reinstate", "target": target, "status": "active"})
    return steps


def derived_columns(kind, doc) -> dict:
    """The search columns beside the document (7.1). The document is the truth."""
    ident = low(doc[KEY[kind]])
    c = {KEY[kind]: ident}
    life = doc.get("lifecycle", "active")
    if kind in LIFECYCLE_KINDS:
        c["lifecycle"] = life
    if kind in ("record", "person", "relationship", "place", "group", "event"):
        c["duplicate_of"] = low(doc.get("duplicate_of"))
    if kind == "record":
        ok = doc.get("original_key") or {}
        c.update(record_type=doc["record_type"], redistribution_license=doc["redistribution_license"],
                 is_living_flag=1 if doc.get("is_living_flag") else 0,
                 source_class=(doc.get("source_classification") or {}).get("source", "undetermined"),
                 original_key=ok.get("key"), original_key_partial=(1 if ok.get("partial") else 0) if ok else None,
                 url_key=url_key(doc), transcription_sha256=doc.get("transcription_sha256"))
    elif kind == "person":
        c.update(display_name=best_name(doc), person_kind=doc.get("person_kind", "documented"),
                 is_living=1 if doc.get("is_living") else 0)
    elif kind == "relationship":
        p1 = (doc.get("party1") or {})
        p2 = (doc.get("party2") or {})
        c.update(kind=doc["kind"], parent_id=low(doc.get("parent_id")), child_id=low(doc.get("child_id")),
                 association_type=doc.get("association_type"),
                 party1=low(p1.get("person_id")) or (("%s#%s" % (low(p1["mention"]["record_id"]), p1["mention"]["mention_id"]))
                                                      if p1.get("mention") else None),
                 party2=low(p2.get("person_id")) or (("%s#%s" % (low(p2["mention"]["record_id"]), p2["mention"]["mention_id"]))
                                                      if p2.get("mention") else None),
                 is_living_flag=1 if doc.get("is_living_flag") else 0)
    elif kind == "task":
        res = doc.get("result") or {}
        target = (res.get("review") or {}).get("target_task_id") or (doc.get("payload") or {}).get("target_task_id")
        c.update(task_type=doc["task_type"], status=doc["status"], priority=doc.get("priority"),
                 verdict=res.get("verdict"), payload_kind=(doc.get("payload") or {}).get("kind"),
                 target_task_id=low(target), is_living_flag=1 if doc.get("is_living_flag") else 0)
    elif kind == "source":
        c.update(name=doc["name"], source_type=doc["source_type"], redistribution_license=doc["redistribution_license"])
    elif kind == "place":
        c.update(display_name=best_claim_value(doc.get("name_assertions"), "name_as_written"))
    elif kind == "group":
        c.update(group_kind=doc["group_kind"], display_name=best_claim_value(doc.get("name_assertions"), "name_as_written"))
    elif kind == "event":
        origin = doc.get("origin") or {}
        c.update(event_type=doc["event_type"],
                 origin_record_id=low(origin.get("record_id")) if origin.get("kind") == "document_entry" else None,
                 origin_ref=origin.get("record_event_ref") if origin.get("kind") == "document_entry" else None)
    elif kind == "name":
        c.update(canonical_form=doc["canonical_form"], name_type=doc["name_type"])
    elif kind == "recognition":
        c.update(raw_record_id=low(doc.get("raw_record_id")))
    elif kind == "dna":
        c.update(person_id=low(doc["person_id"]))
    return c


def best_claim_value(items, field):
    live = [i for i in (items or []) if isinstance(i, dict) and i.get("status", "proposed") in LIVE]
    if not live:
        return None
    pref = [i for i in live if i.get("is_preferred")]
    pick = max(pref or live, key=lambda i: float(i.get("confidence") or 0))
    return pick.get(field)


def best_name(doc) -> str:
    """The name to show: a live, real (name_status known) name; preferred first, then the
    highest confidence; a placeholder shows its label."""
    if doc.get("person_kind") == "placeholder" and not doc.get("name_assertions"):
        return doc.get("placeholder_label") or "(placeholder)"
    names = [n for n in doc.get("name_assertions") or []
             if n.get("status", "proposed") in LIVE and n.get("name_status", "known") == "known"
             and n.get("name_as_written")]
    if not names:
        return doc.get("placeholder_label") or "(no name)"
    pref = [n for n in names if n.get("is_preferred")]
    return max(pref or names, key=lambda n: float(n.get("confidence") or 0))["name_as_written"]


def _best(items):
    """The highest-confidence live item in a list (first one wins a tie), or None."""
    items = [i for i in (items or []) if isinstance(i, dict) and i.get("status", "proposed") in LIVE]
    return max(items, key=lambda i: float(i.get("confidence") or 0)) if items else None


def _gender_role(doc):
    best = _best(doc.get("gender_assertions"))
    s = str(best.get("gender_as_written") or "").strip().lower() if best else ""
    if s.startswith(("m", "son", "husband", "father", "boy")):
        return "father"
    if s.startswith(("f", "w", "daughter", "mother", "girl")):
        return "mother"
    return None


def _year(items, event_class="other"):
    """The stated year of the highest-confidence live dated claim in a list."""
    dated = [i for i in (items or []) if isinstance(i, dict) and i.get("status", "proposed") in LIVE
             and isinstance(i.get("date"), dict) and isinstance((i["date"].get("value") or {}).get("year"), int)]
    best = max(dated, key=lambda i: float(i.get("confidence") or 0)) if dated else None
    return date_year(best["date"]) if best else None


def cited_records(doc):
    """Every record id a document's claims cite through document-basis evidence (lower case)."""
    for item in iter_all_claims(doc):
        ev = item.get("evidence") or {}
        if ev.get("source_record_id"):
            yield low(ev["source_record_id"])


def iter_all_claims(doc):
    for key, value in doc.items():
        if key.endswith("_assertions") or key in ("matches", "variants", "cognates"):
            for item in value if isinstance(value, list) else []:
                if isinstance(item, dict):
                    yield item


def iter_claims(kind, doc):
    """(list name, claim) for every claim of a document of this kind."""
    for list_name in CLAIM_LISTS.get(kind, ()):
        for item in doc.get(list_name) or []:
            if isinstance(item, dict):
                yield list_name, item


# ------------------------------------------------------------------ stored-form completeness (7.4)

class StoredFormError(RuntimeError):
    """A document the helper was about to store is incomplete: a kit bug, not a refusal."""


def stored_check(ctx, kind, doc):
    missing = []

    def need(cond, what):
        if not cond:
            missing.append(what)
    need(doc.get("created_at"), "created_at")
    need(doc.get("updated_at"), "updated_at")
    if kind in LIFECYCLE_KINDS:
        need(doc.get("lifecycle"), "lifecycle")
        need(doc.get("created_by_task_id"), "created_by_task_id")
    for list_name, c in iter_claims(kind, doc):
        for f in ("assertion_id", "task_id", "status", "asserted_at"):
            need(c.get(f), "%s[].%s" % (list_name, f))
        ev = c.get("evidence") or {}
        if ev.get("basis") == "document":
            need(ev.get("information"), "%s[].evidence.information" % list_name)
            if ev.get("quote"):
                need(ev.get("quote_check"), "%s[].evidence.quote_check" % list_name)
        if ev.get("basis") == "imported":
            need(ev.get("quote_check"), "%s[].evidence.quote_check" % list_name)
        if kind == "dna":
            need(c.get("match_id"), "matches[].match_id")
    if kind == "record":
        if doc.get("transcription_parts"):
            need(doc.get("transcription_sha256"), "transcription_sha256")
        if (doc.get("source_classification") or {}).get("source") == "original":
            need(doc.get("original_key"), "original_key")
        for e in doc.get("events_documented") or []:
            need(e.get("occurrence_id") and e.get("status"), "events_documented[%s].occurrence_id/status" % e.get("ref"))
    if kind == "relationship":
        for m in doc.get("children") or []:
            for f in ("membership_id", "status", "created_at"):
                need(m.get(f), "children[].%s" % f)
            need("is_preferred" in m, "children[].is_preferred")
        for p in doc.get("partners") or []:
            need(p.get("status"), "partners[].status")
    if kind == "task":
        need(doc.get("created_by"), "created_by")
        if doc.get("status") != "pending":
            need(doc.get("provenance"), "provenance")
    if missing:
        raise StoredFormError("%s %s is missing helper-owned field(s) %s" % (TITLE[kind], doc.get(KEY[kind]), missing))
    errs = ctx.schemas.errors(kind, doc)
    if errs:
        raise StoredFormError("the stored %s %s fails its schema: %s" % (TITLE[kind], doc.get(KEY[kind]), errs[:5]))


# ------------------------------------------------------------------ tasks, ownership (R-14) and reviews

def task_agent(task) -> str | None:
    return (task.get("provenance") or {}).get("agent_id")


def created_done(task) -> bool:
    """A task created already done (a person-page session task, an import, an --answer review)."""
    return task.get("status") == "done" and not task.get("started_at")


def require_task(ctx, task_id, annotation=False, purpose="this write", types=None, rule="R-14"):
    """R-14: the task exists, is not cancelled, is processing (or done for an annotation, or a task
    this session created already done), and was claimed by the writing session's agent."""
    if not task_id:
        refuse("%s needs a task: pass --task <task_id> (the task the work is done under)" % purpose, "R-14")
    task = ctx.doc("task", task_id)
    if task is None:
        refuse("task %s does not exist -- add the MaxTask first" % task_id, "R-04")
    if task["status"] == "cancelled":
        refuse("task %s is cancelled; work cannot be filed under it" % task_id, "R-14")
    ok_status = task["status"] == "processing" or (task["status"] == "done" and (annotation or created_done(task)))
    if not ok_status:
        refuse("task %s is %s; move it to processing first (`task %s processing`)" % (task_id, task["status"], task_id),
               "R-14")
    if task_agent(task) != ctx.agent_id:
        refuse("task %s was claimed by %s, not by this session (%s); file work only under your own tasks"
               % (task_id, task_agent(task), ctx.agent_id), "R-14")
    if types and task["task_type"] not in types:
        refuse("%s needs a task of type %s; task %s is %s" % (purpose, " or ".join(types), task_id, task["task_type"]),
               rule)
    return task


def review_tasks(ctx, target_task_id):
    """The judge_review tasks targeting a task, oldest first (reviews are append-only)."""
    rows = ctx.con.execute("SELECT doc FROM max_task WHERE target_task_id=? AND task_type='judge_review' "
                           "AND status != 'cancelled' ORDER BY added_at, rowid", (low(target_task_id),)).fetchall()
    out = []
    for r in rows:
        d = json.loads(r["doc"])
        if ((d.get("result") or {}).get("review") or {}).get("target_task_id"):
            out.append(d)
    return out


def _verdict(review_task):
    return ((review_task.get("result") or {}).get("review") or {}).get("verdict")


def is_person_review(review_task, database_id=None) -> bool:
    """The person's own review: human, role person -- and, when the database id is given, recorded in
    this database (a review imported from another MAXGEN database is another person's, 4.4 rule 13)."""
    prov = review_task.get("provenance") or {}
    if database_id and prov.get("party") != "db:%s" % database_id:
        return False
    return prov.get("agent_kind") == "human" and prov.get("role") == "person"


def accepted_now(ctx, task_id) -> bool:
    """Accepted now: the latest review of the task says accepted (a later rework or rejection
    withdraws it at once)."""
    reviews = review_tasks(ctx, task_id)
    return bool(reviews) and _verdict(reviews[-1]) == "accepted"


def person_accepted(ctx, task_id) -> bool:
    """The person's recorded yes: the latest of the person's own reviews of this task is accepted
    (a relayed --answer counts)."""
    dbid = ctx.con.execute("SELECT value FROM meta WHERE key='database_id'").fetchone()
    reviews = [r for r in review_tasks(ctx, task_id) if is_person_review(r, dbid[0] if dbid else None)]
    return bool(reviews) and _verdict(reviews[-1]) == "accepted"


def independence(review_task, proof_task) -> str:
    r, p = review_task.get("provenance") or {}, proof_task.get("provenance") or {}
    if r.get("agent_kind") == "human":
        return "human"
    if r.get("agent_kind") == "ai" and p.get("agent_kind") == "ai":
        rm, pm = r.get("model") or {}, p.get("model") or {}
        if rm.get("name") != pm.get("name") and rm.get("attested_by") == "platform":
            return "different_model"
    if r.get("agent_id") != p.get("agent_id"):
        return "same_model_different_agent"
    if r.get("version") != p.get("version"):
        return "same_agent_different_version"
    return "same_agent_same_version"


def has_proof(task) -> bool:
    return bool((task.get("result") or {}).get("proof"))


# ------------------------------------------------------------------ records: labels, keys, quote checks

def record_lines(record):
    """{line label: (part_id, text of the line, start offset in the part)} for '[label] ' lines."""
    lines = {}
    for part in record.get("transcription_parts") or []:
        offset = 0
        for raw in part.get("text", "").split("\n"):
            m = re.match(r"^\[([A-Za-z0-9][A-Za-z0-9._:-]{0,63})\] ", raw)
            if m:
                lines[m.group(1)] = (part["part_id"], raw, offset)
            offset += len(raw) + 1
    return lines


def mention_of(record, mention_id):
    for m in record.get("persons_mentioned") or []:
        if m.get("mention_id") == mention_id:
            return m
    return None


def entry_of(record, ref):
    for e in record.get("events_documented") or []:
        if e.get("ref") == ref:
            return e
    return None


def regions_of(record):
    """{region_id: (sha256, region)} for every region on the record's own saved copies."""
    out = {}
    for c in record.get("saved_copies") or []:
        for r in c.get("regions") or []:
            out[low(r["region_id"])] = (c.get("sha256"), r)
    return out


def _find(text, quote):
    """(start, end, mode) of the quote in text, trying exact, whitespace-folded, case-folded."""
    i = text.find(quote)
    if i >= 0:
        return i, i + len(quote), "exact"
    tokens = quote.split()
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    m = re.search(pattern, text)
    if m:
        return m.start(), m.end(), "whitespace_folded"
    m = re.search(pattern, text, re.I)
    if m:
        return m.start(), m.end(), "case_folded"
    return None


def quote_check_document(record, ev, task_id):
    """The quote check for a document-basis claim (spec 3.6)."""
    quote = ev.get("quote")
    out = {"checked_at": now(), "against": "transcription"}
    if task_id:
        out["checked_by_task_id"] = task_id
    if record.get("transcription_sha256"):
        out["transcription_sha256"] = record["transcription_sha256"]
    parts = record.get("transcription_parts") or []
    if not parts:
        out["status"] = "not_checkable"
        return out
    if len(quote.strip()) < 4 or not re.search(r"[^\W_]", quote):
        out["status"] = "too_short"
        return out
    lines = record_lines(record)
    scope = []
    mid = ev.get("mention_id")
    if mid and ev.get("about", "own_mention") != "absence":
        m = mention_of(record, mid)
        labels = list((m or {}).get("lines") or [])
        if not labels and mid in lines:
            labels = [mid]
        scope = [lines[x] for x in labels if x in lines]
    if scope:
        for part_id, text, start in scope:
            hit = _find(text, quote)
            if hit:
                out.update(status="matched", part_id=part_id, char_start=start + hit[0], char_end=start + hit[1],
                           mode=hit[2])
                return out
    for part in parts:
        hit = _find(part.get("text", ""), quote)
        if hit:
            out.update(status="outside_line" if scope else "matched_whole_text", part_id=part["part_id"],
                       char_start=hit[0], char_end=hit[1], mode=hit[2])
            return out
    out["status"] = "not_found"
    return out


def _file_path_of(uri: str):
    """A local path from a file: address (never fetches anything)."""
    if not uri.lower().startswith("file:"):
        return None
    rest = uri[5:]
    if rest.startswith("//"):
        rest = rest[2:]
        if not rest.startswith("/"):
            rest = rest.split("/", 1)[1] if "/" in rest else rest
    rest = re.sub(r"%([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), rest)
    if re.match(r"^/[A-Za-z]:", rest):
        rest = rest[1:]
    return Path(rest)


def canonical_import_text(copy_):
    """(text, against) of an import file's saved copy, or None when it cannot be read here."""
    for loc in copy_.get("locations") or []:
        path = _file_path_of(loc.get("uri", ""))
        if path is None or not path.exists():
            continue
        data = path.read_bytes()
        if copy_.get("sha256") and hashlib.sha256(data).hexdigest() != copy_["sha256"]:
            return None
        if copy_.get("copy_kind") == "import_rendering":
            return data.decode("utf-8", errors="replace"), "import_rendering"
        if data[:2] == b"\x1f\x8b":
            try:
                return gzip.decompress(data).decode("utf-8"), "saved_copy_file"
            except Exception:
                return None
        if data[:3] == b"\xef\xbb\xbf":
            return data[3:].decode("utf-8", errors="replace"), "saved_copy_file"
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return data.decode("utf-16"), "saved_copy_file"
        head = data[:4096].decode("latin-1")
        m = re.search(r"^1 CHAR (\S+)", head, re.M)
        charset = (m.group(1).upper() if m else "UTF-8")
        if charset in ("UTF-8", "UNICODE", "UTF8"):
            return data.decode("utf-8", errors="replace"), "saved_copy_file"
        if charset == "ASCII":
            return data.decode("ascii", errors="replace"), "saved_copy_file"
        return None     # ANSEL and other sets: the importer saves an import_rendering copy
    return None


def quote_check_imported(record, ev, task_id):
    out = {"checked_at": now()}
    if task_id:
        out["checked_by_task_id"] = task_id
    copies = [c for c in record.get("saved_copies") or [] if c.get("copy_kind") == "import_rendering"] + \
             [c for c in record.get("saved_copies") or [] if c.get("copy_kind") == "import_file"]
    for c in copies:
        got = canonical_import_text(c)
        if got is None:
            continue
        text, against = got
        out["against"] = against
        if c.get("sha256"):
            out["copy_sha256"] = c["sha256"]
        quote = ev.get("quote", "")
        if len(quote.strip()) < 4:
            out["status"] = "too_short"
            return out
        hit = _find(text, quote)
        if hit:
            out.update(status="matched", char_start=hit[0], char_end=hit[1], mode=hit[2])
        else:
            out["status"] = "not_found"
        return out
    out["status"] = "not_checkable"
    return out


ORIGINAL_KEY_PARTS = ("repo", "series", "roll", "volume", "book", "register", "district", "enumeration_district",
                      "page", "folio", "sheet", "certificate", "entry", "item", "dwelling", "family", "lines", "type")


def _norm_part(v) -> str:
    s = re.sub(r"[\W_]+", "", str(v).lower(), flags=re.U)
    return re.sub(r"(?<!\d)0+(?=\d)", "", s)


def custodian(ctx, source_id, seen=None):
    """The repository holding the original (5.2.1): walk part-of sources up to the root."""
    seen = seen or set()
    sid = ctx.resolve(source_id)
    if not sid or sid in seen:
        return None
    seen.add(sid)
    src = ctx.doc("source", sid)
    if not src:
        return None
    for h in src.get("holdings") or []:
        if h.get("holds_original"):
            return h.get("repository_name")
    return custodian(ctx, src.get("parent_source_id"), seen) if src.get("parent_source_id") else None


def original_key_of(ctx, source_id, locator, record_type):
    parts = {}
    repo = custodian(ctx, source_id) if source_id else None
    if repo:
        parts["repo"] = _norm_part(repo)
    loc = dict(locator or {})
    if loc.get("film") and not loc.get("roll"):
        loc["roll"] = loc["film"]
    for p in ORIGINAL_KEY_PARTS[1:-2]:
        if loc.get(p) not in (None, ""):
            parts[p] = _norm_part(loc[p])
    if loc.get("line_first") is not None:
        parts["lines"] = _norm_part(loc["line_first"])
    parts["type"] = _norm_part(record_type)
    parts = {k: v for k, v in parts.items() if v != ""}
    if record_type == "census_household":
        middle = "series" in parts and ("district" in parts or "enumeration_district" in parts)
    else:
        middle = any(p in parts for p in ("series", "roll", "volume", "book", "register"))
    low_tier = any(p in parts for p in ("page", "folio", "sheet", "certificate", "entry", "item", "lines"))
    partial = not ("repo" in parts and middle and low_tier)
    key = "v2|" + "|".join("%s=%s" % (p, parts[p]) for p in ORIGINAL_KEY_PARTS if p in parts)
    return {"key": key, "partial": partial, "parts": parts}


def url_key(record):
    url = record.get("source_url") or ""
    m = re.match(r"^(https?)://([^/?#]+)([^?#]*)", url, re.I)
    loc = record.get("locator") or {}
    line = None
    for f in ("line_first", "entry", "dwelling", "family", "certificate"):
        if loc.get(f) not in (None, ""):
            line = "%s=%s" % (f, loc[f])
            break
    if not m or not line:
        return None
    return "%s://%s%s#%s|%s" % (m.group(1).lower(), m.group(2).lower(), m.group(3).rstrip("/"), line,
                                record.get("record_type"))


def page_url(record):
    url = record.get("source_url") or ""
    m = re.match(r"^(https?)://([^/?#]+)([^?#]*)", url, re.I)
    return ("%s://%s%s" % (m.group(1).lower(), m.group(2).lower(), m.group(3).rstrip("/"))) if m else None


# ------------------------------------------------------------------ which entries a claim may cite (5.2)

BIRTH_SAME, BIRTH_OTHER = ("birth", "stillbirth"), ("baptism", "christening")
DEATH_SAME, DEATH_OTHER = ("death",), ("burial", "cremation", "funeral", "probate", "will")


def entry_fit(kind, list_name, claim, entry_type):
    """'same' (a same-occasion entry: the claim copies its occurrence id), 'other' (may be cited,
    no occurrence id) or None (refused for a direct claim)."""
    if kind == "person":
        if list_name == "birth_assertions":
            return "same" if entry_type in BIRTH_SAME else "indirect_only" if entry_type in BIRTH_OTHER else None
        if list_name == "death_assertions":
            return "same" if entry_type in DEATH_SAME else "indirect_only" if entry_type in DEATH_OTHER else None
        if list_name == "event_assertions":
            et = claim.get("event_type")
            if et == entry_type or (et in BIRTH_OTHER and entry_type in BIRTH_OTHER):
                return "same"
            return None
        if list_name in ("attribute_assertions", "membership_assertions"):
            return "same"
        if list_name in ("name_assertions", "gender_assertions", "non_event_assertions"):
            return "other"
        if list_name == "photo_assertions":
            return "other" if entry_type == "photograph" else None
        return None
    if kind == "relationship":
        if list_name == "fact_assertions":
            ft = claim.get("fact_type")
            if ft == "existence":
                return "other"
            if ft == entry_type or (ft in ("census", "residence") and entry_type in ("census", "residence")):
                return "same"
            if claim.get("ceremony") == "sealing" and entry_type == "lds_sealing_to_spouse":
                return "same"
            return None
        if list_name == "lineage_assertions":
            return "same"
        # associations are standing ties, not occasions: they may cite any entry and get no occurrence id
        return "other"
    if kind == "group":
        return "same" if list_name == "fact_assertions" else "other"
    return "other"


def lds_entry_ok(list_name, claim, entry_type) -> bool:
    """A religious-only (lds_*) entry never supports a birth, death or family claim (5.2): the one
    exception is a sealing -- lds_sealing_to_parents for a lineage fact of fact_type sealing, and
    lds_sealing_to_spouse for a family fact with ceremony sealing."""
    if entry_type not in LDS_EVENTS:
        return True
    if list_name == "lineage_assertions":
        return entry_type == "lds_sealing_to_parents" and claim.get("fact_type") == "sealing"
    if list_name == "fact_assertions":
        return entry_type == "lds_sealing_to_spouse" and claim.get("ceremony") == "sealing"
    return list_name not in ("birth_assertions", "death_assertions")


def claim_event_class(list_name, claim):
    if list_name == "birth_assertions":
        return "birth"
    if list_name == "death_assertions":
        return "death"
    return claim.get("event_type") or claim.get("fact_type") or "other"


# ------------------------------------------------------------------ reliable evidence and the cap (4.5)

def reliable(ctx, ev, list_name=None) -> bool:
    """One function used by R-11, the living test, proof rule P4 and independence."""
    basis = (ev or {}).get("basis")
    if basis == "document":
        rid = ctx.resolve(ev.get("source_record_id"))
        r = ctx.row("record", rid)
        return bool(r) and r["source_class"] in ("original", "derivative")
    if basis == "conclusion":
        task = ctx.doc("task", ev.get("conclusion_task_id"))
        if not task or not has_proof(task):
            return False
        if (task["result"]["proof"].get("origin") or "maxgen") != "maxgen":
            return False
        return accepted_now(ctx, task["task_id"])
    if basis == "dna":
        return list_name == "lineage_assertions"
    return False


def capped(ctx, ev, list_name=None) -> bool:
    return (ev or {}).get("basis") != "search" and not reliable(ctx, ev, list_name)


def live_claim(c) -> bool:
    return c.get("status", "proposed") in LIVE


# ------------------------------------------------------------------ claims of a holder (through merges)

def effective_claims(ctx, kind, ident, live_only=True):
    """[(list name, claim, holder doc)] of every claim whose effective holder is ident (4.1 step 5):
    a merged-away document is a forwarding shell, so its claims count for the survivor."""
    ident = ctx.resolve(ident)
    sql = ("SELECT holder_id, assertion_id, list_name FROM claim_index WHERE holder_table=? AND "
           "effective_holder_id=?" + (" AND status IN ('proposed','proven','disputed')" if live_only else "")
           + " ORDER BY rowid")
    rows = ctx.con.execute(sql, (TABLE[kind], ident)).fetchall()
    docs, out = {}, []
    for r in rows:
        if r["holder_id"] not in docs:
            docs[r["holder_id"]] = ctx.doc(kind, r["holder_id"])
        holder = docs[r["holder_id"]]
        if holder is None:
            continue
        for c in holder.get(r["list_name"]) or []:
            cid = c.get("assertion_id") or c.get("match_id")
            if low(cid) == r["assertion_id"]:
                out.append((r["list_name"], c, holder))
    return out


def links_of(ctx, person_id, role=None):
    """Active relationship ids (resolved) in which the person (resolved) plays a role."""
    pid = ctx.resolve(person_id)
    sql = "SELECT DISTINCT relationship_id, role FROM link_index WHERE person_id=?"
    args = [pid]
    if role:
        sql += " AND role=?"
        args.append(role)
    out = []
    for r in ctx.con.execute(sql, args).fetchall():
        row = ctx.row("relationship", r["relationship_id"])
        if row and row["lifecycle"] == "active":
            out.append((r["relationship_id"], r["role"]))
    return out


def active_partners(doc):
    return [p for p in doc.get("partners") or [] if p.get("status", "active") == "active"]


def partner_ids(ctx, doc):
    return [ctx.resolve(p["person_id"]) for p in active_partners(doc) if p.get("person_id")]


def relationship_persons(ctx, doc):
    """Every person a relationship names (resolved)."""
    k = doc.get("kind")
    if k == "parent_child":
        return [ctx.resolve(doc["parent_id"]), ctx.resolve(doc["child_id"])]
    if k == "association":
        return [ctx.resolve(p["person_id"]) for p in (doc.get("party1") or {}, doc.get("party2") or {})
                if p.get("person_id")]
    ids = partner_ids(ctx, doc)
    for m in doc.get("children") or []:
        ids.append(ctx.resolve(m["child_id"]))
    return ids


# ------------------------------------------------------------------ Living A, computed when read (4.2)

def _birth_like(list_name, c):
    if list_name == "birth_assertions":
        return "birth"
    if list_name == "event_assertions" and c.get("event_type") in ("birth", "baptism", "christening") \
            and c.get("role") in (None, "principal"):
        return c["event_type"]
    return None


def inferred_only(c) -> bool:
    """A claim worked out from what a record does not say (negative evidence, or someone's absence
    from a complete list): it brackets dates but never establishes a death or an age (4.2)."""
    ev = c.get("evidence") or {}
    return ev.get("kind") == "negative" or ev.get("about") == "absence"


def establishes_death(ctx, ln, c) -> bool:
    """Rule 1 of 4.2: a reliable live death-type claim that says so outright -- direct evidence, the
    other person's line (a widow), an accepted proof, or proven -- and is not dated text_only."""
    ev = c.get("evidence") or {}
    if not reliable(ctx, ev, ln) or (c.get("date") or {}).get("modifier") == "text_only":
        return False
    if c.get("status") == "proven":
        return True
    if inferred_only(c):
        return False
    return ev.get("basis") != "document" or ev.get("kind") == "direct" or ev.get("about") == "other_mention"


def latest_birth_year(ctx, pid):
    """The latest possible birth year from live, reliable, dated birth-type claims, or None."""
    best = None
    for ln, c, _h in effective_claims(ctx, "person", pid):
        cls = _birth_like(ln, c)
        if not cls or not reliable(ctx, c.get("evidence"), ln) or inferred_only(c):
            continue
        lo, hi = year_span(c.get("date"), cls, ctx.settings)
        if lo is None or hi is None:
            continue        # an open start or no bounds counts as undated (3.2 rule 7)
        best = hi if best is None else max(best, hi)
    return best


def person_living_now(ctx, person_id) -> bool:
    """True unless reliable live evidence says this person died or was born too long ago (4.2).
    The stored is_living true is a floor and always wins; a stored false is never permission."""
    pid = ctx.resolve(person_id)
    if pid in ctx._living:
        return ctx._living[pid]
    doc = ctx.doc("person", pid)
    if doc is None:
        return True
    ctx._living[pid] = True        # guards against loops while working it out
    dead = dead_by_rules(ctx, pid)
    living = (not dead) or bool(doc.get("is_living"))
    if doc.get("is_living") is False and not dead:
        ctx.warn("%s (%s): is_living false but no reliable death or age evidence; treated as living"
                 % (best_name(doc), pid), once_key=("living", pid))
    ctx._living[pid] = living
    return living


def dead_by_rules(ctx, pid) -> bool:
    """The four rules of 4.2 on live claims with reliable evidence only."""
    ty = ctx.today.year
    s = ctx.settings
    dead = False
    for ln, c, _h in effective_claims(ctx, "person", pid):
        if ln == "death_assertions" or (ln == "event_assertions" and c.get("event_type") in ("death", "burial", "cremation")
                                        and c.get("role") in (None, "principal", "deceased")):
            if establishes_death(ctx, ln, c):
                dead = True
                break
    if not dead:
        b = latest_birth_year(ctx, pid)
        if b is not None and b <= ty - s["living_years_birth"]:
            dead = True
    if not dead:
        for rid, _role in links_of(ctx, pid, "partner"):
            fam = ctx.doc("relationship", rid)
            for c in (fam or {}).get("fact_assertions") or []:
                if live_claim(c) and c.get("fact_type") in MARRIAGE_FACTS and reliable(ctx, c.get("evidence")) \
                        and not inferred_only(c):
                    lo, hi = year_span(c.get("date"), "marriage", s)
                    if lo is not None and hi is not None and hi <= ty - s["living_years_family"]:
                        dead = True
            if dead:
                break
    if not dead:
        for rid, _role in links_of(ctx, pid, "parent"):
            row = ctx.row("relationship", rid)
            if not row or row["kind"] != "parent_child" or row["stage"] not in ("approved", "staged"):
                continue
            pc = json.loads(row["doc"])
            ok = any(live_claim(f) and (f.get("fact_type") in ("birth_parent", "gestational_carrier") or
                                        f.get("dimension") in ("genetic", "gestational"))
                     and reliable(ctx, f.get("evidence"), "lineage_assertions") and not inferred_only(f)
                     for f in pc.get("lineage_assertions") or [])
            if not ok:
                continue
            b = latest_birth_year(ctx, pc["child_id"])
            if b is not None and b <= ty - s["living_years_family"]:
                dead = True
                break
    return dead


def record_persons(ctx, record_id):
    """Persons (resolved) that hold live claims citing this record, directly or through a link."""
    ids = ctx.merged_into(ctx.resolve(record_id)) | {low(record_id)}
    out = set()
    qs = ",".join("?" * len(ids))
    for r in ctx.con.execute("SELECT holder_table, effective_holder_id FROM claim_index WHERE source_record_id IN "
                             "(%s) AND status IN ('proposed','proven','disputed')" % qs, list(ids)).fetchall():
        if r["holder_table"] == "max_person":
            out.add(r["effective_holder_id"])
        elif r["holder_table"] == "max_relationship":
            rel = ctx.doc("relationship", r["effective_holder_id"])
            if rel:
                out.update(p for p in relationship_persons(ctx, rel) if p)
    return out


def living_now(ctx, kind, doc) -> bool:
    """The live answer for any document: its stored floor OR any person it names is living now."""
    if kind == "person":
        return person_living_now(ctx, doc["person_id"])
    if kind == "record":
        if doc.get("is_living_flag") or any(m.get("is_living") and m.get("status", "active") == "active"
                                            for m in doc.get("persons_mentioned") or []):
            return True
        return any(person_living_now(ctx, p) for p in record_persons(ctx, doc["record_id"]))
    if kind == "relationship":
        if doc.get("is_living_flag"):
            return True
        if doc["kind"] == "family":
            return any(person_living_now(ctx, p) for p in partner_ids(ctx, doc))
        if doc["kind"] == "association":
            for party in (doc.get("party1") or {}, doc.get("party2") or {}):
                if party.get("person_id") and person_living_now(ctx, party["person_id"]):
                    return True
                if party.get("mention"):
                    rec = ctx.doc("record", party["mention"]["record_id"])
                    m = mention_of(rec or {}, party["mention"]["mention_id"])
                    if m is None or m.get("is_living"):
                        return True
            return False
        return any(person_living_now(ctx, p) for p in relationship_persons(ctx, doc))
    if kind == "task":
        p = doc.get("payload") or {}
        ids = ([p["person_id"]] if p.get("person_id") else []) + list(p.get("person_ids") or [])
        return bool(doc.get("is_living_flag")) or any(person_living_now(ctx, x) for x in ids)
    if kind == "event":
        if doc.get("is_living_flag"):
            return True
        ids = ctx.merged_into(doc["occurrence_id"])
        qs = ",".join("?" * len(ids))
        for r in ctx.con.execute("SELECT holder_table, effective_holder_id FROM claim_index WHERE occurrence_id IN "
                                 "(%s) AND status IN ('proposed','proven','disputed')" % qs, list(ids)).fetchall():
            if r["holder_table"] == "max_person" and person_living_now(ctx, r["effective_holder_id"]):
                return True
            if r["holder_table"] == "max_relationship":
                rel = ctx.doc("relationship", r["effective_holder_id"])
                if rel and any(person_living_now(ctx, p) for p in relationship_persons(ctx, rel)):
                    return True
        return False
    if kind == "dna":
        return True if doc.get("is_living_flag") else (
            person_living_now(ctx, doc["person_id"]) or any(m.get("is_living") for m in doc.get("matches") or []))
    if kind in ("group", "source", "recognition"):
        return bool(doc.get("is_living_flag"))
    return False


def mention_living(ctx, record_id, mention_id) -> bool:
    rec = ctx.doc("record", record_id)
    m = mention_of(rec or {}, mention_id)
    return True if m is None else bool(m.get("is_living"))


def record_private(ctx, record_id) -> bool:
    rid = ctx.resolve(record_id)
    rec = ctx.doc("record", rid)
    if rec is None:
        return True
    return private_now(ctx, "record", rec)


def _person_private(ctx, pid) -> bool:
    """Withheld from everything shareable: private now, or no name of theirs may be shown (every name claim
    is private), exactly as the shareable page withholds them (9.4)."""
    pid = ctx.resolve(pid)
    key = ("withheld", pid)
    if key in ctx._living:          # worked out already, or being worked out (a loop fails closed)
        return ctx._living[key]
    ctx._living[key] = True
    doc = ctx.doc("person", pid)
    out = doc is None or private_now(ctx, "person", doc) or person_display(ctx, pid, shareable=True)[0] is None
    ctx._living[key] = out
    return out


def _conclusion_private(ctx, task_id, depth=0) -> bool:
    """9.4: a claim resting on a proof is private on shareable pages when the proof is missing, private
    (its flag), relies on DNA, or relies on a private record or claim."""
    task = ctx.doc("task", task_id)
    if not task or task.get("is_living_flag") or not has_proof(task) or depth > 3:
        return True
    for item in task["result"]["proof"].get("relied_on") or []:
        k = item.get("kind")
        if k == "dna_match":
            return True
        if k in ("record", "authored_record", "mention") and record_private(ctx, item["record_id"]):
            return True
        if k == "claim":
            hk, h, ln, c = find_claim(ctx, item["assertion_id"])
            if c is None or claim_private(ctx, hk, h, ln, c, True, depth + 1):
                return True
        if k == "proof" and _conclusion_private(ctx, item["task_id"], depth + 1):
            return True
    return False


def claim_private(ctx, holder_kind, holder, list_name, claim, shareable=True, depth=0) -> bool:
    """Private at read time (4.2): its own flag, a tier2-private record, a living mention, a living
    holder; on shareable pages and exports every DNA-basis claim, every claim resting on a private or
    DNA-based proof, and every adoption-type event too (9.4)."""
    if claim.get("is_private"):
        return True
    ev = claim.get("evidence") or {}
    if shareable and ev.get("basis") == "dna":
        return True
    if shareable and ev.get("basis") == "conclusion" and _conclusion_private(ctx, ev.get("conclusion_task_id"), depth):
        return True
    if shareable and list_name == "event_assertions" and claim.get("event_type") in ADOPTION_EVENT_TYPES:
        return True
    if ev.get("basis") == "document":
        rec = ctx.doc("record", ctx.resolve(ev.get("source_record_id")))
        if (rec is None or rec.get("redistribution_license") == "tier2-private"
                or rec.get("record_type") == "dna_match_list"):
            return True
        stated = ctx.doc("record", ev.get("source_record_id")) or rec
        for mid in (ev.get("mention_id"), ev.get("subject_mention_id")):
            if mid and (mention_of(stated, mid) or {}).get("is_living", True):
                return True
        for p in claim.get("partners_as_recorded") or []:
            if p.get("mention_id") and (mention_of(stated, p["mention_id"]) or {}).get("is_living", True):
                return True
    if ev.get("basis") == "imported":
        return True     # an imported tree file is tier2-private (4.4 rule 2)
    if holder_kind == "person":
        return person_living_now(ctx, holder["person_id"])
    if holder_kind == "relationship":
        if holder["kind"] == "family":
            return any(person_living_now(ctx, p) for p in partner_ids(ctx, holder))
        return any(person_living_now(ctx, p) for p in relationship_persons(ctx, holder))
    if holder_kind in ("place", "group"):
        return False
    return True


def private_now(ctx, kind, doc) -> bool:
    """For the kinds that have no single claim to judge (4.2 table); every export and shareable
    page uses it."""
    if kind in ("task", "dna"):
        return True
    if doc.get("redistribution_license") == "tier2-private" or doc.get("is_private"):
        return True                     # a tier2-private licence or a private flag is private on every kind
    if kind == "person":
        return person_living_now(ctx, doc["person_id"]) or bool(doc.get("is_living"))
    if kind == "record":
        if doc.get("record_type") == "dna_match_list" or living_now(ctx, "record", doc):
            return True
        return any(_person_private(ctx, p) for p in record_persons(ctx, doc["record_id"]))
    if kind == "relationship":
        if doc.get("kind") == "family":
            return bool(doc.get("is_living_flag")) or any(person_living_now(ctx, p) or _person_private(ctx, p)
                                                          for p in partner_ids(ctx, doc))
        return living_now(ctx, "relationship", doc) or any(_person_private(ctx, p) for p in
                                                           relationship_persons(ctx, doc) if p)
    if kind == "place":
        if doc.get("is_private"):
            return True
        small = ("street", "address", "building", "farm", "estate")
        kinds = [c.get("kind") for c in doc.get("kind_assertions") or [] if live_claim(c)]
        is_small = (not kinds) or any(k in small for k in kinds)
        if not is_small:
            return False
        ids = ctx.merged_into(doc["place_id"])
        for r in ctx.con.execute("SELECT holder_table, holder_id FROM claim_index WHERE holder_table "
                                 "IN ('max_person','max_relationship') AND status IN ('proposed','proven','disputed')"
                                 ).fetchall():
            kind_h = KIND_OF_TABLE[r["holder_table"]]
            h = ctx.doc(kind_h, r["holder_id"])
            for ln, c in iter_claims(kind_h, h or {}):
                pid = low(((c.get("place") or {}).get("place_id")))
                if pid and pid in ids and live_claim(c):
                    if kind_h == "person" and person_living_now(ctx, h["person_id"]):
                        return True
                    if kind_h == "relationship" and any(person_living_now(ctx, p) for p in relationship_persons(ctx, h)):
                        return True
        return False
    if kind == "event":
        if living_now(ctx, "event", doc) or doc.get("event_type") in ADOPTION_EVENT_TYPES:
            return True
        rid = (doc.get("origin") or {}).get("record_id")
        if rid and record_private(ctx, rid):
            return True
        ids = ctx.merged_into(doc["occurrence_id"])
        qs = ",".join("?" * len(ids))
        for r in ctx.con.execute("SELECT holder_table, holder_id, assertion_id FROM claim_index WHERE occurrence_id IN "
                                 "(%s) AND status IN ('proposed','proven','disputed')" % qs, list(ids)).fetchall():
            hk = KIND_OF_TABLE[r["holder_table"]]
            h = ctx.doc(hk, r["holder_id"])
            for ln, c in iter_claims(hk, h or {}):
                if low(c.get("assertion_id")) == r["assertion_id"] and claim_private(ctx, hk, h, ln, c):
                    return True
        return False
    if kind in ("group", "source"):
        return bool(doc.get("is_living_flag")) or doc.get("redistribution_license") == "tier2-private"
    if kind == "recognition":
        if doc.get("is_living_flag") or doc.get("redistribution_license") == "tier2-private":
            return True
        if doc.get("raw_record_id"):
            return record_private(ctx, doc["raw_record_id"])
        if doc.get("copy_sha256"):
            for r in ctx.con.execute("SELECT held_on_record_id FROM media_index WHERE sha256=?",
                                     (doc["copy_sha256"],)).fetchall():
                if record_private(ctx, r["held_on_record_id"]):
                    return True
        return False
    if kind == "name":
        return doc.get("redistribution_license") == "tier2-private"
    return True


# ------------------------------------------------------------------ independence (4.5)

def copied_keys(ctx, record, seen=None):
    """Original keys reachable through a record's copied_from chain (followed through record ids)."""
    seen = seen or set()
    keys = set()
    for step in record.get("copied_from") or []:
        if step.get("record_id"):
            rid = ctx.resolve(step["record_id"])
            if rid in seen:
                continue
            seen.add(rid)
            r = ctx.doc("record", rid)
            if r:
                ok = r.get("original_key") or {}
                if ok.get("key"):
                    keys.add(ok["key"])
                keys |= copied_keys(ctx, r, seen)
        elif step.get("source_id") and step.get("locator"):
            keys.add(original_key_of(ctx, step["source_id"], step["locator"], record.get("record_type"))["key"])
    return keys


def independent(ctx, ev1, ev2) -> bool:
    """Two pieces of evidence are independent only when everything checks out; anything unknown
    means not independent (fails closed)."""
    try:
        if ev1.get("basis") != "document" or ev2.get("basis") != "document":
            return False
        r1 = ctx.doc("record", ctx.resolve(ev1["source_record_id"]))
        r2 = ctx.doc("record", ctx.resolve(ev2["source_record_id"]))
        if not r1 or not r2:
            return False
        for r in (r1, r2):
            if (r.get("source_classification") or {}).get("source") in ("authored", "undetermined"):
                return False
        k1, k2 = r1.get("original_key") or {}, r2.get("original_key") or {}
        if not k1 or not k2 or k1.get("partial") or k2.get("partial") or k1["key"] == k2["key"]:
            return False
        if k2["key"] in copied_keys(ctx, r1) or k1["key"] in copied_keys(ctx, r2):
            return False
        names = []
        for ev, r in ((ev1, r1), (ev2, r2)):
            entry = entry_of(r, ev.get("record_event_ref")) if ev.get("record_event_ref") else None
            if not entry or not entry.get("informant_mention_id"):
                return False
            m = mention_of(r, entry["informant_mention_id"])
            if not m or not m.get("name_as_written"):
                return False
            names.append(re.sub(r"\s+", " ", m["name_as_written"]).strip().lower())
        return names[0] != names[1]
    except Exception:
        return False


# ------------------------------------------------------------------ index tables (7.2): never data

def claim_index_rows(ctx, kind, doc):
    rows = []
    holder = low(doc[KEY[kind]])
    eff = ctx.resolve(holder)
    for list_name, c in iter_claims(kind, doc):
        cid = low(c.get("assertion_id") or (c.get("match_id") if kind == "dna" else None))
        if not cid:
            continue
        ev = c.get("evidence") or {}
        fact = (c.get("fact_type") or c.get("event_type") or c.get("attribute_type") or c.get("kind")
                or c.get("relation") or c.get("variant_type") or c.get("relationship_type")
                or list_name.replace("_assertions", ""))
        cls = claim_event_class(list_name, c)
        lo, hi = year_span(c.get("date"), cls, ctx.settings) if isinstance(c.get("date"), dict) else (None, None)
        qc = ev.get("quote_check") or {}
        rows.append((cid, TABLE[kind], holder, eff, list_name, fact, c.get("status", "proposed"), low(c.get("task_id")),
                     ev.get("basis"), low(ev.get("source_record_id")), ev.get("mention_id"), ev.get("record_event_ref"),
                     low(c.get("occurrence_id")), low(ev.get("import_record_id")), low(ev.get("dna_id")),
                     low(ev.get("conclusion_task_id")), lo, hi,
                     float(c["confidence"]) if isinstance(c.get("confidence"), (int, float)) else None,
                     1 if capped(ctx, ev, list_name) else 0, 1 if c.get("is_private") else 0, qc.get("status")))
    return rows


def index_rows(ctx, kind, doc) -> dict:
    """Every index row one document gives (table -> list of tuples)."""
    out = {t: [] for t in INDEX_TABLES}
    ident = low(doc[KEY[kind]])
    out["claim_index"] = claim_index_rows(ctx, kind, doc)
    if kind == "record":
        for m in doc.get("persons_mentioned") or []:
            out["mention_index"].append((ident, m["mention_id"], m.get("name_as_written"),
                                         1 if m.get("is_living") else 0, m.get("status", "active")))
        for i, c in enumerate(doc.get("saved_copies") or []):
            if c.get("sha256"):
                out["media_index"].append((c["sha256"], ident, c.get("copy_kind"), c.get("content_type"), c.get("bytes")))
            for loc in c.get("locations") or []:
                out["media_location"].append((ident, i, c.get("sha256"), loc["uri"], loc.get("verified_at"),
                                              1 if loc.get("is_public") else 0, 1 if c.get("pending") else 0))
            for r in c.get("regions") or []:
                out["region_index"].append((low(r["region_id"]), c.get("sha256") or "", ident))
    if kind == "relationship":
        k = doc["kind"]
        rid = ctx.resolve(ident)
        links = set()
        if k == "parent_child":
            links.add((ctx.resolve(doc["parent_id"]), low(doc["parent_id"]), rid, "parent"))
            links.add((ctx.resolve(doc["child_id"]), low(doc["child_id"]), rid, "child"))
        elif k == "association":
            for role in ("party1", "party2"):
                p = doc.get(role) or {}
                if p.get("person_id"):
                    links.add((ctx.resolve(p["person_id"]), low(p["person_id"]), rid, role))
        else:
            for p in active_partners(doc):
                if p.get("person_id"):
                    links.add((ctx.resolve(p["person_id"]), low(p["person_id"]), rid, "partner"))
            for m in doc.get("children") or []:
                if m.get("status", "active") == "active":
                    links.add((ctx.resolve(m["child_id"]), low(m["child_id"]), rid, "member_child"))
            for m in doc.get("children") or []:
                plinks = list(m.get("parent_link_ids") or [])
                resolved = []
                for x in plinks:
                    x = ctx.resolve(x)
                    if x not in resolved:
                        resolved.append(x)
                out["membership_index"].append((low(m["membership_id"]), rid, ctx.resolve(m["child_id"]),
                                                m["set_kind"], 1 if m.get("is_preferred") else 0,
                                                low(m.get("preferred_by_task_id")), m.get("status", "active"),
                                                low(m.get("merged_into_membership_id")), json.dumps(resolved),
                                                m.get("created_at") or ""))
        out["link_index"] = sorted(links)
    if kind == "task":
        for s in (doc.get("result") or {}).get("steps") or []:
            out["search_step"].append((ident, s["step_no"], low(s.get("person_id")), s["source"], low(s.get("source_id")),
                                       s["query"], s["found"], s["verdict"], low(s.get("record_id")), s.get("at")))
    return out


_INDEX_DELETE = {
    "claim_index": "DELETE FROM claim_index WHERE holder_table=? AND holder_id=?",
    "mention_index": "DELETE FROM mention_index WHERE record_id=?",
    "media_index": "DELETE FROM media_index WHERE held_on_record_id=?",
    "media_location": "DELETE FROM media_location WHERE record_id=?",
    "region_index": "DELETE FROM region_index WHERE record_id=?",
    "search_step": "DELETE FROM search_step WHERE task_id=?",
}
_INDEX_COLS = {
    "claim_index": 22, "link_index": 4, "membership_index": 10, "mention_index": 5, "media_index": 5,
    "media_location": 7, "region_index": 3, "search_step": 10,
}


def index_doc(ctx, kind, doc):
    """Replace the index rows of one document (7.2)."""
    ident = low(doc[KEY[kind]])
    rows = index_rows(ctx, kind, doc)
    ctx.con.execute(_INDEX_DELETE["claim_index"], (TABLE[kind], ident))
    if kind == "record":
        for t in ("mention_index", "media_index", "media_location", "region_index"):
            ctx.con.execute(_INDEX_DELETE[t], (ident,))
    if kind == "task":
        ctx.con.execute(_INDEX_DELETE["search_step"], (ident,))
    if kind == "relationship":
        for m in doc.get("children") or []:
            ctx.con.execute("DELETE FROM membership_index WHERE membership_id=?", (low(m.get("membership_id")),))
    for t, rows_t in rows.items():
        if t == "link_index":
            continue
        for row in rows_t:
            ctx.con.execute("INSERT OR REPLACE INTO %s VALUES (%s)" % (t, ",".join("?" * _INDEX_COLS[t])), row)
    if kind == "relationship":
        reindex_links(ctx, ctx.resolve(ident))


def reindex_links(ctx, survivor_id):
    """link_index rows of one effective relationship, from every document forwarding to it."""
    ids = ctx.merged_into(survivor_id)
    ctx.con.execute("DELETE FROM link_index WHERE relationship_id=?", (survivor_id,))
    for rid in ids:
        d = ctx.doc("relationship", rid)
        if not d:
            continue
        for row in index_rows(ctx, "relationship", d)["link_index"]:
            ctx.con.execute("INSERT OR REPLACE INTO link_index VALUES (?,?,?,?)", row)


def all_index_rows(ctx) -> dict:
    out = {t: set() for t in INDEX_TABLES}
    for kind in KEY:
        for r in ctx.con.execute("SELECT doc FROM %s" % TABLE[kind]).fetchall():
            for t, rows in index_rows(ctx, kind, json.loads(r["doc"])).items():
                out[t].update(rows)
    return out


def rebuild_indexes(ctx):
    rows = all_index_rows(ctx)
    for t in INDEX_TABLES:
        ctx.con.execute("DELETE FROM %s" % t)
        for row in rows[t]:
            ctx.con.execute("INSERT OR REPLACE INTO %s VALUES (%s)" % (t, ",".join("?" * _INDEX_COLS[t])), row)
    refresh_event_owners(ctx)
    ctx.forget()
    return rows


def current_index_rows(ctx) -> dict:
    out = {}
    for t in INDEX_TABLES:
        out[t] = {tuple(r) for r in ctx.con.execute("SELECT * FROM %s" % t).fetchall()}
    return out


def _normal(row):
    return tuple(round(v, 9) if isinstance(v, float) else v for v in row)


def index_drift(ctx) -> list:
    want = all_index_rows(ctx)
    have = current_index_rows(ctx)
    problems = []
    for t in INDEX_TABLES:
        w = {_normal(r) for r in want[t]}
        h = {_normal(r) for r in have[t]}
        if w != h:
            problems.append("%s: %d row(s) missing, %d row(s) extra" % (t, len(w - h), len(h - w)))
    owners = {r["occurrence_id"]: (r["owner_kind"], r["owner_id"]) for r in
              ctx.con.execute("SELECT occurrence_id, owner_kind, owner_id FROM max_event").fetchall()}
    for occ, have_owner in owners.items():
        if event_owner(ctx, occ) != have_owner:
            problems.append("max_event %s: owner index is stale" % occ)
    return problems


# ------------------------------------------------------------------ occurrences (5.8)

def event_owner(ctx, occurrence_id):
    """(owner kind, owner id) worked out when read: the principal of a vital event, the family of
    a couple event, the group of a group fact; otherwise (None, None)."""
    row = ctx.row("event", occurrence_id)
    if not row:
        return None, None
    et = row["event_type"]
    ids = ctx.merged_into(ctx.resolve(occurrence_id))
    qs = ",".join("?" * len(ids))
    rows = ctx.con.execute("SELECT holder_table, holder_id, effective_holder_id, assertion_id, list_name FROM claim_index "
                           "WHERE occurrence_id IN (%s) AND status IN ('proposed','proven','disputed') "
                           "ORDER BY assertion_id" % qs, list(ids)).fetchall()
    if et in ONE_PRINCIPAL_EVENTS:
        for r in rows:
            if r["holder_table"] != "max_person":
                continue
            if r["list_name"] in ("birth_assertions", "death_assertions"):
                return "person", r["effective_holder_id"]
            if r["list_name"] == "event_assertions":
                h = ctx.doc("person", r["holder_id"])
                for c in (h or {}).get("event_assertions") or []:
                    if low(c.get("assertion_id")) == r["assertion_id"] and c.get("role") in (None, "principal"):
                        return "person", r["effective_holder_id"]
        return None, None
    if et in COUPLE_EVENT_TYPES:
        for r in rows:
            if r["holder_table"] == "max_relationship" and r["list_name"] == "fact_assertions":
                return "relationship", r["effective_holder_id"]
        return None, None
    for r in rows:
        if r["holder_table"] == "max_group" and r["list_name"] == "fact_assertions":
            return "group", r["effective_holder_id"]
    return None, None


def refresh_event_owners(ctx, occurrence_ids=None):
    if occurrence_ids is None:
        occurrence_ids = [r["occurrence_id"] for r in ctx.con.execute("SELECT occurrence_id FROM max_event").fetchall()]
    for occ in occurrence_ids:
        if not occ:
            continue
        occ = ctx.resolve(occ)
        for o in ctx.merged_into(occ):
            kind, owner = event_owner(ctx, o)
            ctx.con.execute("UPDATE max_event SET owner_kind=?, owner_id=? WHERE occurrence_id=?", (kind, owner, o))


def make_occurrence(ctx, record, entry, task_id):
    """One MaxEvent for one document event entry (origin document_entry), made by the helper."""
    occ = new_id()
    living = bool(record.get("is_living_flag")) or any(
        m.get("is_living") for m in record.get("persons_mentioned") or [] if entry["ref"] in (m.get("in_event_refs") or []))
    doc = {"occurrence_id": occ, "schema_version": MAXGEN_VERSION, "event_type": entry["event_type"],
           "origin": {"kind": "document_entry", "record_id": low(record["record_id"]), "record_event_ref": entry["ref"]},
           "is_living_flag": living,
           "redistribution_license": "tier2-private" if living else record["redistribution_license"],
           "created_by_task_id": task_id}
    if entry.get("event_type_as_written"):
        doc["event_type_as_written"] = entry["event_type_as_written"]
    if entry.get("type_uri"):
        doc["type_uri"] = entry["type_uri"]
    ctx.store("event", doc, new=True)
    return occ


# ------------------------------------------------------------------ floors only rise (4.2)

def raise_doc_floor(ctx, kind, doc, task_id, flag_field="is_living_flag"):
    """Raise a stored living floor (and the licence with it). Returns True when it changed."""
    changed = False
    if kind == "task":
        if not doc.get("is_living_flag"):
            changed |= ctx.annotate(kind, doc, ["is_living_flag"], True, task_id)
        if doc.get("contributor_eligibility") == "any_contributor":
            changed |= ctx.annotate(kind, doc, ["contributor_eligibility"], "first_party_only", task_id)
        return changed
    if not doc.get(flag_field):
        changed |= ctx.annotate(kind, doc, [flag_field], True, task_id)
    if "redistribution_license" in doc and doc["redistribution_license"] != "tier2-private" and kind != "dna":
        changed |= ctx.annotate(kind, doc, ["redistribution_license"], "tier2-private", task_id)
    return changed


def raise_floors_for_person(ctx, person_id, task_id):
    """When a person may be alive, every record, mention, relationship, membership, task,
    occurrence and recognition naming them is raised to living and tier2-private (annotation
    writes; a family's top level follows its partners only)."""
    pid = ctx.resolve(person_id)
    if not person_living_now(ctx, pid):
        return
    ids = ctx.merged_into(pid)
    touched_records = {}
    for ln, c, holder in effective_claims(ctx, "person", pid):
        ev = c.get("evidence") or {}
        rid = ev.get("source_record_id") or ev.get("import_record_id")
        if rid:
            touched_records.setdefault(low(rid), set()).update(
                m for m in (ev.get("mention_id"), ev.get("subject_mention_id")) if m and ev.get("source_record_id"))
        if c.get("occurrence_id"):
            occ = ctx.doc("event", ctx.resolve(c["occurrence_id"]))
            if occ and raise_doc_floor(ctx, "event", occ, task_id):
                ctx.store("event", occ, content=False)
    for rid, role in links_of(ctx, pid):
        rel = ctx.doc("relationship", rid)
        if not rel:
            continue
        changed = False
        if rel["kind"] == "family":
            if role == "partner":
                changed |= raise_doc_floor(ctx, "relationship", rel, task_id)
            for m in rel.get("children") or []:
                if m.get("status", "active") != "active":
                    continue
                if low(m["child_id"]) in ids or role == "partner":
                    in_set = low(m["child_id"]) in ids
                    if not in_set:
                        parents = set()
                        for lid in m.get("parent_link_ids") or []:
                            pc = ctx.doc("relationship", ctx.resolve(lid))
                            if pc:
                                parents.add(ctx.resolve(pc["parent_id"]))
                        in_set = pid in parents
                    if in_set and not m.get("is_living_flag"):
                        changed |= ctx.annotate("relationship", rel, ["children", {"k": "membership_id", "v": m["membership_id"]},
                                                                      "is_living_flag"], True, task_id)
        else:
            changed |= raise_doc_floor(ctx, "relationship", rel, task_id)
        for ln, c in iter_claims("relationship", rel):
            ev = c.get("evidence") or {}
            if not ev.get("source_record_id"):
                continue
            if rel["kind"] == "parent_child":
                mid = c.get("child_mention_id") if low(rel["child_id"]) in ids else c.get("parent_mention_id")
                touched_records.setdefault(low(ev["source_record_id"]), set()).update([mid] if mid else [])
            elif rel["kind"] == "family" and role == "partner":
                for p in c.get("partners_as_recorded") or []:
                    if low(p["person_id"]) in ids and p.get("mention_id"):
                        touched_records.setdefault(low(ev["source_record_id"]), set()).add(p["mention_id"])
        if changed:
            ctx.store("relationship", rel, content=False)
    for rid, mids in touched_records.items():
        rec = ctx.doc("record", rid)
        if not rec:
            continue
        changed = raise_doc_floor(ctx, "record", rec, task_id)
        for mid in mids:
            m = mention_of(rec, mid)
            if m is not None and not m.get("is_living"):
                changed |= ctx.annotate("record", rec, ["persons_mentioned", {"k": "mention_id", "v": mid}, "is_living"],
                                        True, task_id)
        if changed:
            ctx.store("record", rec, content=False)
        for r in ctx.con.execute("SELECT recognition_id FROM max_recognition WHERE raw_record_id=?", (rid,)).fetchall():
            rec_doc = ctx.doc("recognition", r["recognition_id"])
            if rec_doc and raise_doc_floor(ctx, "recognition", rec_doc, task_id):
                ctx.store("recognition", rec_doc, content=False)
    for r in ctx.con.execute("SELECT task_id, doc FROM max_task").fetchall():
        t = json.loads(r["doc"])
        p = t.get("payload") or {}
        named = {low(p.get("person_id"))} | {low(x) for x in p.get("person_ids") or []}
        if named & ids and (not t.get("is_living_flag") or t.get("contributor_eligibility") == "any_contributor"):
            if raise_doc_floor(ctx, "task", t, task_id):
                ctx.store("task", t, content=False)
    ctx.forget()


# ------------------------------------------------------------------ the write pipeline (8.2)

IDENTITY_KEYS = ("assertion_id", "membership_id", "match_id", "occurrence_id")
IMPORT_KEEP = ("place_id", "place_task_id", "group_id")


def kind_of(doc) -> str:
    for key, kind, _t, _f, _n in KINDS:
        if key in doc:
            return kind
    refuse("not a MAXGEN 2.0 document: it has none of the id fields %s" % ", ".join(k for k, *_ in KINDS), "R-03")


def version_problem(v):
    """None for a 2.x stamp; otherwise the plain reason (2.1): missing or 1.x -> the 1.x message; a
    later major -> 'written by MAXGEN <major>.x'; anything else -> not a MAXGEN stamp."""
    if v is None or (isinstance(v, str) and re.match(r"^1(\.[0-9]+)*$", v)):
        return V1_MESSAGE
    m = re.match(r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$", v) if isinstance(v, str) else None
    if not m:
        return "schema_version %r is not a MAXGEN stamp (it is written 2.<n>, such as \"2.0\")" % (v,)
    major = int(m.group(1))
    if major < 2:
        return V1_MESSAGE
    if major > 2:
        return "written by MAXGEN %d.x; this kit reads 2.x" % major
    return None


def check_version(ctx, kind, doc):
    """R-02: a 2.x stamp; 1.x and unstamped files are refused; a newer minor must pass the 2.0
    schemas or the kit asks to be updated."""
    v = doc.get("schema_version")
    problem = version_problem(v)
    if problem:
        refuse(problem, "R-02")
    minor = int(v.split(".")[1])
    if minor > KNOWN_MINOR and ctx.schemas.errors(kind, doc):
        refuse(UPDATE_MESSAGE % (minor, KNOWN_MINOR), "R-02")


def _expand(ctx, subs, root, seen=None):
    seen = seen if seen is not None else set()
    for s in subs:
        if not isinstance(s, dict) or id(s) in seen:
            continue
        seen.add(id(s))
        yield s
        if "$ref" in s:
            yield from _expand(ctx, [ctx.schemas._resolve(s["$ref"], root)], root, seen)
        for key in ("allOf", "anyOf", "oneOf"):
            yield from _expand(ctx, s.get(key) or [], root, seen)
        for key in ("if", "then", "else"):
            if isinstance(s.get(key), dict):
                yield from _expand(ctx, [s[key]], root, seen)


def _readonly(ctx, sub, root) -> bool:
    return any(x.get("readOnly") for x in _expand(ctx, [sub], root) if isinstance(x, dict))


def strip_readonly(ctx, kind, doc, keep=()):
    """Step 2: remove every helper-owned value a writer supplied, except identity keys (and the
    place/group annotations an importer already knows)."""
    root = ctx.schemas.docs[FILE[kind]]
    removed = []

    def walk(node, subs, where):
        if isinstance(node, dict):
            props = {}
            for s in _expand(ctx, subs, root):
                for k, v in (s.get("properties") or {}).items():
                    props.setdefault(k, []).append(v)
            for k in list(node):
                ks = props.get(k, [])
                if ks and any(_readonly(ctx, x, root) for x in ks if isinstance(x, dict)):
                    if k not in IDENTITY_KEYS and k not in keep:
                        removed.append(where + k)
                        del node[k]
                        continue
                walk(node[k], ks, where + k + ".")
        elif isinstance(node, list):
            items = [s["items"] for s in _expand(ctx, subs, root) if isinstance(s.get("items"), dict)]
            for i, item in enumerate(node):
                walk(item, items, where.rstrip(".") + "[%d]." % i)
    walk(doc, [root], "")
    return removed


HELPER_CLAIM_FIELDS = ("assertion_id", "status", "status_reason", "status_task_id", "status_at", "asserted_at")
HELPER_EVIDENCE_FIELDS = ("information", "quote_check")


def _writer_view(item, helper=(), kind=None):
    """An item without its helper-owned fields, for comparing a re-sent item with the stored one."""
    out = copy.deepcopy(item)
    for f in tuple(helper) + ("amended_record_flag",):
        out.pop(f, None)
    if isinstance(out.get("evidence"), dict):
        for f in HELPER_EVIDENCE_FIELDS:
            out["evidence"].pop(f, None)
    if isinstance(out.get("place"), dict):
        for f in ("place_id", "place_task_id"):
            out["place"].pop(f, None)
    if kind == "person":
        out.pop("group_id", None)
    return out


HELPER_FILLED = ("occurrence_id", "dimension", "match_id")


def same_claim(stored, new, kind):
    """A re-sent claim equals the stored one, apart from what the helper filled in (an occurrence
    id, a default dimension, a record_event_ref it worked out) and a rise of is_private."""
    a, b = _writer_view(stored, HELPER_CLAIM_FIELDS, kind), _writer_view(new, HELPER_CLAIM_FIELDS, kind)
    for f in HELPER_FILLED:
        if f not in b:
            a.pop(f, None)
    if isinstance(a.get("evidence"), dict) and "record_event_ref" not in (b.get("evidence") or {}):
        a["evidence"].pop("record_event_ref", None)
    if b.get("is_private") is True and not a.get("is_private"):
        a["is_private"] = True
    a.setdefault("is_private", False), b.setdefault("is_private", False)
    return a == b


def _flag_lowered(old, new) -> bool:
    return bool(old) and new is False


def match_replace(ctx, kind, stored, new, task_id):
    """Step 3 and 4 on a replace (R-07): items are matched by identity key; a stored item may not be
    dropped or altered; new items are added; stored helper values are copied back. Returns the
    final document and the list of new claims."""
    lowered = []
    out = copy.deepcopy(new)
    # top level helper values
    for f in ("created_at", "lifecycle", "lifecycle_reason", "lifecycle_task_id", "duplicate_of", "merge_history",
              "distinct_from", "split_history", "provenance", "started_at", "completed_at", "created_by"):
        if f in stored:
            out[f] = copy.deepcopy(stored[f])
    if kind != "task":
        out["created_by_task_id"] = stored.get("created_by_task_id")
    for flag in ("is_living", "is_living_flag"):
        if flag in stored and _flag_lowered(stored.get(flag), new.get(flag)):
            lowered.append(flag)
    if stored.get("redistribution_license") == "tier2-private" and new.get("redistribution_license") != "tier2-private" \
            and "redistribution_license" in new:
        lowered.append("redistribution_license")
    new_claims = []

    def keyed(items, key):
        return {low(i.get(key)) if key != "step_no" else i.get(key): i for i in items or [] if isinstance(i, dict) and i.get(key) is not None}

    # claims
    for list_name in CLAIM_LISTS.get(kind, ()):
        stored_items = stored.get(list_name) or []
        idkey = "assertion_id"
        smap = keyed(stored_items, idkey)
        seen = set()
        merged = []
        for item in new.get(list_name) or []:
            cid = low(item.get(idkey))
            if kind == "dna" and not cid and item.get("match_id"):
                hit = [s for s in stored_items if low(s.get("match_id")) == low(item["match_id"])]
                cid = low(hit[0]["assertion_id"]) if hit else None
                if not hit:
                    new_claims.append((list_name, item))
                    merged.append(item)
                    continue
            if cid:
                if cid not in smap:
                    refuse("%s: claim %s is not stored on this document; a replace repeats stored claims "
                           "unchanged and adds new ones without an assertion_id" % (list_name, cid), "R-07")
                s = smap[cid]
                if not same_claim(s, item, kind):
                    refuse("%s: claim %s would be changed; claims are append-only -- retract it and add a new "
                           "claim with supersedes_assertion_id" % (list_name, cid), "R-07")
                keep = copy.deepcopy(s)
                if item.get("is_private"):
                    keep["is_private"] = True
                if kind == "dna" and _flag_lowered(s.get("is_living"), item.get("is_living")):
                    lowered.append("matches[].is_living")
                merged.append(keep)
                seen.add(cid)
            else:
                new_claims.append((list_name, item))
                merged.append(item)
        missing = [x for x in smap if x not in seen]
        if missing:
            refuse("%s: stored claim(s) %s are missing from the replacement; claims are never dropped -- use "
                   "retract" % (list_name, missing), "R-07")
        if merged or list_name in stored or list_name in new:
            out[list_name] = merged
        if not merged:
            out.pop(list_name, None)

    def frozen_list(field, key, helper, editable=(), label=None):
        s_items = stored.get(field) or []
        n_items = new.get(field) or []
        smap = keyed(s_items, key)
        result, seen = [], set()
        for item in n_items:
            k = low(item.get(key)) if key != "step_no" else item.get(key)
            if k in smap:
                s = smap[k]
                a = {x: y for x, y in _writer_view(s, helper).items() if x not in editable}
                b = {x: y for x, y in _writer_view(item, helper).items() if x not in editable}
                if field == "persons_mentioned" and _flag_lowered(s.get("is_living"), item.get("is_living")):
                    lowered.append("mention %s is_living" % k)
                if field in ("persons_mentioned", "children"):
                    a.pop("is_living", None), b.pop("is_living", None)
                    a.pop("is_living_flag", None), b.pop("is_living_flag", None)
                    a.pop("is_private", None), b.pop("is_private", None)
                if a != b:
                    refuse("%s %s would be changed or dropped; it is permanent -- add new items, retract old ones"
                           % (label or field, k), "R-07")
                merged_item = copy.deepcopy(s)
                for x in editable:
                    if x in item:
                        merged_item[x] = copy.deepcopy(item[x])
                    else:
                        merged_item.pop(x, None)
                for x in ("is_living", "is_living_flag", "is_private"):
                    if item.get(x) is True:
                        merged_item[x] = True
                result.append(merged_item)
                seen.add(k)
            elif item.get(key) is not None and field in ("children",):
                refuse("%s %s is not stored on this document" % (label or field, item.get(key)), "R-07")
            else:
                result.append(item)
        missing = [x for x in smap if x not in seen]
        if missing:
            refuse("%s %s missing from the replacement; withdraw with retract, never by leaving it out"
                   % (label or field, missing), "R-07")
        if result:
            out[field] = result

    if kind == "record":
        frozen_list("persons_mentioned", "mention_id", ("status", "status_reason", "group_id"), label="mention")
        frozen_list("events_documented", "ref", ("occurrence_id", "status", "status_reason", "status_task_id"),
                    label="event entry")
        frozen_list("transcription_parts", "part_id", (), editable=("text", "lang", "page", "kind", "kind_as_written"),
                    label="transcription part")
        # saved copies: matched by sha256 (a pending copy by its first location)
        def copy_key(c):
            return c.get("sha256") or ("pending:" + ((c.get("locations") or [{}])[0].get("uri", "")))
        smap = {copy_key(c): c for c in stored.get("saved_copies") or []}
        result, seen = [], set()
        for c in new.get("saved_copies") or []:
            k = copy_key(c)
            fingerprinted = False
            if k not in smap and c.get("sha256") and not c.get("pending"):
                # R-16: a pending copy gets its fingerprint (the file reached this computer); matched by location
                for loc in c.get("locations") or []:
                    pk = "pending:" + (loc.get("uri") or "")
                    if pk in smap and not smap[pk].get("sha256") and pk not in seen:
                        k, fingerprinted = pk, True
                        break
            if k in smap:
                s = smap[k]
                for f in ("sha256", "copy_kind", "derived_from"):
                    if f == "sha256" and fingerprinted:
                        continue
                    if s.get(f) != c.get(f):
                        refuse("saved copy %s: %s is frozen" % (k, f), "R-07")
                sreg = {low(r["region_id"]): r for r in s.get("regions") or []}
                nreg = {low(r["region_id"]): r for r in c.get("regions") or []}
                for rid, r in sreg.items():
                    if rid not in nreg:
                        refuse("region %s is missing from the replacement; a region is retracted, never removed"
                               % rid, "R-07")
                    if {x: y for x, y in nreg[rid].items() if x != "status"} != {x: y for x, y in r.items() if x != "status"}:
                        refuse("region %s would be changed; regions are frozen" % rid, "R-07")
                merged_c = copy.deepcopy(c)
                merged_c["regions"] = [sreg[low(r["region_id"])] if low(r["region_id"]) in sreg else r
                                       for r in c.get("regions") or []] or merged_c.pop("regions", None) or []
                if not merged_c["regions"]:
                    merged_c.pop("regions")
                result.append(merged_c)
                seen.add(k)
            else:
                result.append(c)
        missing = [x for x in smap if x not in seen]
        if missing:
            refuse("saved copy %s missing from the replacement; saved copies are frozen" % missing, "R-07")
        if result:
            out["saved_copies"] = result
    if kind == "relationship":
        frozen_list("children", "membership_id", ("membership_id", "is_preferred", "preferred_by_task_id", "status",
                                                  "status_reason", "merged_into_membership_id", "created_at"),
                    label="membership")
        for m in out.get("children") or []:
            if _flag_lowered(next((s.get("is_living_flag") for s in stored.get("children") or []
                                   if low(s.get("membership_id")) == low(m.get("membership_id"))), False),
                             m.get("is_living_flag")):
                lowered.append("membership is_living_flag")

        def pkey(p):
            return low(p.get("person_id")) if p.get("person_id") else "absent:" + canon(
                {k: v for k, v in p.items() if k not in ("status", "status_reason", "status_task_id")})
        smap = {pkey(p): p for p in stored.get("partners") or []}
        result, seen = [], set()
        for p in new.get("partners") or []:
            k = pkey(p)
            if k in smap:
                s = smap[k]
                if {x: y for x, y in p.items() if x not in ("status", "status_reason", "status_task_id")} != \
                        {x: y for x, y in s.items() if x not in ("status", "status_reason", "status_task_id")}:
                    refuse("partner %s would be changed; retract the partner item instead" % k, "R-07")
                result.append(copy.deepcopy(s))
                seen.add(k)
            else:
                result.append(p)
        missing = [x for x in smap if x not in seen]
        if missing:
            refuse("partner item(s) %s missing from the replacement; retract instead" % missing, "R-07")
        if result:
            out["partners"] = result
    if kind == "task":
        s_steps = ((stored.get("result") or {}).get("steps")) or []
        n_steps = ((new.get("result") or {}).get("steps")) or []
        smap = {s["step_no"]: s for s in s_steps}
        nmap = {s.get("step_no"): s for s in n_steps}
        for no, s in smap.items():
            if no not in nmap:
                refuse("step %s missing from the replacement; the search log is append-only" % no, "R-07")
            if nmap[no] != s:
                refuse("step %s would be changed; the search log is append-only" % no, "R-07")
        if new.get("status") != stored.get("status"):
            refuse("a task's status changes only through the task command (`task %s <status>`)" % stored["task_id"])
    return out, new_claims, lowered


def status_from_import(ctx, task, claim):
    """4.4 rule 4: an import may write disputed or disproven, never proven."""
    if not task or task.get("task_type") != "import_file":
        return "proposed"
    st = claim.get("imported_status") or {}
    system, value = st.get("system"), str(st.get("value_as_written") or "").strip().upper()
    if system == "rootsmagic" and value in ("DISPROVEN", "DISPUTED"):
        return value.lower()
    if system == "gedcom7" and value in ("DISPROVEN", "CHALLENGED"):
        return "disproven" if value == "DISPROVEN" else "disputed"
    return "proposed"


def fill_new_claim(ctx, kind, list_name, claim, default_task, import_task=None):
    if not (import_task and claim.get("assertion_id")):
        claim["assertion_id"] = new_id()
    claim["assertion_id"] = low(claim["assertion_id"])
    claim["task_id"] = low(claim.get("task_id") or default_task)
    for f in ("status", "status_reason", "status_task_id", "status_at"):
        claim.pop(f, None)
    claim["status"] = status_from_import(ctx, import_task, claim)
    if claim["status"] != "proposed":
        claim["status_reason"] = "status taken from the imported file"
        claim["status_task_id"] = claim["task_id"]
        claim["status_at"] = now()
    claim["asserted_at"] = now()
    if kind == "relationship" and list_name == "lineage_assertions" and "dimension" not in claim:
        claim["dimension"] = DEFAULT_DIMENSION.get(claim.get("fact_type"), "unknown")
    if kind == "dna":
        claim["match_id"] = low(claim.get("match_id") or new_id())


# ------------------------------------------------------------------ claim gates (R-04, R-10 to R-13, R-17 to R-22)

def _record_for(ctx, rid, why):
    row = ctx.row("record", rid)
    if not row:
        refuse("%s cites record %s, which is not in this database -- a claim with no document behind it is an "
               "invented source; add the MaxRecord first" % (why, rid), "R-04")
    if row["lifecycle"] == "merged_away":
        refuse("%s cites record %s, which was merged into %s -- cite that record" % (why, rid, ctx.resolve(rid)), "R-10")
    if row["lifecycle"] == "retracted":
        refuse("%s cites record %s, which was retracted" % (why, rid), "R-10")
    return json.loads(row["doc"])


def check_mention(ctx, record, mid, why):
    m = mention_of(record, mid)
    if m is None or m.get("status", "active") != "active":
        refuse("%s names mention %r, which is not an active mention on record %s" % (why, mid, record["record_id"]),
               "R-10")
    return m


def check_region_on(record, region_id, why):
    rid = low(region_id)
    if rid in regions_of(record):
        if regions_of(record)[rid][1].get("status", "active") != "active":
            refuse("%s names region %s, which was retracted" % (why, region_id), "R-10")
        return
    for mr in record.get("media_refs") or []:
        if rid in [low(x) for x in mr.get("region_ids") or []]:
            return
    refuse("%s names region %s, which is not on a copy held by record %s (nor in its media_refs)"
           % (why, region_id, record["record_id"]), "R-10")


def check_evidence(ctx, kind, list_name, claim, task, why):
    """Evidence gates for one new claim. Fills record_event_ref (single same-occasion entry),
    occurrence_id, information and quote_check. Returns (record, entry, fit)."""
    ev = claim.get("evidence")
    if not isinstance(ev, dict):
        refuse("%s has no evidence; every claim cites one piece of evidence" % why, "R-04")
    basis = ev.get("basis")
    record, entry, fit = None, None, None
    task_type = (task or {}).get("task_type")
    if basis == "document":
        record = _record_for(ctx, ev["source_record_id"], why)
        about = ev.get("about", "own_mention")
        for key in ("mention_id", "subject_mention_id"):
            if ev.get(key):
                check_mention(ctx, record, ev[key], why + " evidence." + key)
        if about == "absence" and record.get("transcription_scope") not in ("complete_entry", "complete_list"):
            refuse("%s: absence evidence needs a record transcribed as a complete entry or a complete list; "
                   "record %s is %s" % (why, record["record_id"], record.get("transcription_scope", "not transcribed")),
                   "R-10")
        if ev.get("region_id"):
            check_region_on(record, ev["region_id"], why)
        indirect = ev.get("kind") in ("indirect", "negative")
        active_entries = [e for e in record.get("events_documented") or [] if e.get("status", "active") == "active"]
        ref = ev.get("record_event_ref")
        if ref:
            entry = entry_of(record, ref)
            if entry is None or entry.get("status", "active") != "active":
                refuse("%s names event entry %r, which is not an active entry on record %s"
                       % (why, ref, record["record_id"]), "R-10")
            if not lds_entry_ok(list_name, claim, entry["event_type"]):
                refuse("%s cites a religious-only %s entry; those never support a birth, death or family claim "
                       "(only a sealing fact may cite a sealing entry)" % (why, entry["event_type"]), "R-10")
            fit = "other" if indirect else entry_fit(kind, list_name, claim, entry["event_type"])
            if fit is None or fit == "indirect_only":
                refuse("%s: a direct claim of this kind cannot cite the %s entry %r; cite a suitable entry, or "
                       "say kind indirect" % (why, entry["event_type"], ref), "R-10")
        elif len(active_entries) == 1 and not indirect:
            if not lds_entry_ok(list_name, claim, active_entries[0]["event_type"]):
                refuse("%s: record %s documents only a religious-only %s entry, which never supports a birth, death "
                       "or family claim (only a sealing fact may cite a sealing entry)"
                       % (why, record["record_id"], active_entries[0]["event_type"]), "R-10")
            if entry_fit(kind, list_name, claim, active_entries[0]["event_type"]) == "same":
                entry, fit = active_entries[0], "same"
                ev["record_event_ref"] = entry["ref"]
        if fit == "same":
            occ = entry.get("occurrence_id")
            if claim.get("occurrence_id") and ctx.resolve(claim["occurrence_id"]) != ctx.resolve(occ):
                refuse("%s: occurrence_id must be the cited entry's occurrence %s" % (why, occ), "R-10")
            claim["occurrence_id"] = low(claim.get("occurrence_id") or occ)
        elif claim.get("occurrence_id"):
            refuse("%s: occurrence_id must be left out when no same-occasion entry is cited (the entry is used "
                   "indirectly)" % why, "R-10")
        ev["information"] = (entry or {}).get("information") or \
            (record.get("source_classification") or {}).get("information") or "undetermined"
        if ev.get("quote"):
            ev["quote_check"] = quote_check_document(record, ev, claim.get("task_id"))
        return record, entry, fit
    if basis == "imported":
        rid = ctx.resolve(ev["import_record_id"])
        row = ctx.row("record", rid)
        if not row or row["lifecycle"] != "active":
            refuse("%s cites import record %s, which is not an active record here" % (why, ev["import_record_id"]), "R-04")
        record = json.loads(row["doc"])
        if record["record_type"] != "tree_import":
            refuse("%s: import_record_id must name a tree_import record; %s is %s" % (why, rid, record["record_type"]),
                   "R-04")
        for tc in ev.get("tree_citations") or []:
            if tc.get("record_id") and not ctx.row("record", ctx.resolve(tc["record_id"])):
                refuse("%s: tree citation names record %s, which is not here" % (why, tc["record_id"]), "R-04")
            check_media_refs(ctx, tc.get("media_refs"))
        ev["quote_check"] = quote_check_imported(record, ev, claim.get("task_id"))
    elif basis == "dna":
        did = ctx.resolve(ev["dna_id"])
        row = ctx.row("dna", did)
        if not row or row["lifecycle"] != "active":
            refuse("%s cites DNA test %s, which is not an active test here" % (why, ev["dna_id"]), "R-04")
        stored = dna_match_ids(ctx, did)
        for mid in ev.get("match_ids") or []:
            if low(mid) not in stored:
                refuse("%s cites DNA match %s, which is not a live match on test %s" % (why, mid, did), "R-04")
    elif basis == "conclusion":
        t = ctx.doc("task", ev["conclusion_task_id"])
        if not t or not has_proof(t):
            refuse("%s cites proof task %s, which is not a task with a proof here" % (why, ev["conclusion_task_id"]),
                   "R-04")
    elif basis == "search":
        t = ctx.doc("task", ev["task_id"])
        steps = {s.get("step_no") for s in ((t or {}).get("result") or {}).get("steps") or []}
        if not t or ev.get("step_no") not in steps:
            refuse("%s cites search step %s of task %s, which is not recorded -- record it with `step` first"
                   % (why, ev.get("step_no"), ev.get("task_id")), "R-04")
    if claim.get("occurrence_id"):
        occ = ctx.doc("event", ctx.resolve(claim["occurrence_id"]))
        if not occ or occ.get("lifecycle", "active") != "active":
            refuse("%s names occurrence %s, which is not an active occurrence" % (why, claim["occurrence_id"]), "R-04")
        if (occ.get("origin") or {}).get("kind") not in ("import", "analysis") or task_type not in ANALYSIS_TASKS:
            refuse("%s: with this evidence an occurrence_id may name only an import or analysis occurrence, under an "
                   "import_file, resolve_conflict or build_tree task" % why, "R-10")
    return record, entry, fit


def dna_match_ids(ctx, did, live_only=True) -> set:
    """Match ids that a (resolved) test id names: matches on the test and on every test merged into
    it (a merged-away test is a forwarding shell whose live matches read as the survivor's, 4.1), plus
    matches stored on the other side that point at it (match_dna_id, R-25)."""
    out = set()
    for r in ctx.con.execute("SELECT dna_id, doc FROM max_dna").fetchall():
        on_this = ctx.resolve(r["dna_id"]) == did
        for m in json.loads(r["doc"]).get("matches") or []:
            if live_only and m.get("status", "proposed") not in LIVE:
                continue
            if on_this or ctx.resolve(m.get("match_dna_id")) == did:
                out.add(low(m.get("match_id")))
    return out


def check_claim(ctx, kind, holder, list_name, claim, task_docs, why=None):
    """Every gate for one new claim."""
    why = why or "%s %s" % (list_name, claim.get("assertion_id"))
    tid = claim.get("task_id")
    if tid not in task_docs:
        task_docs[tid] = require_task(ctx, tid, purpose="a claim (%s)" % why)
    task = task_docs[tid]
    if "evidence" not in claim:
        refuse("%s has no evidence; every claim cites one piece of evidence" % why, "R-04")
    record, entry, fit = check_evidence(ctx, kind, list_name, claim, task, why)
    ev = claim["evidence"]
    # R-11: the 0.4 cap
    conf = claim.get("confidence")
    if isinstance(conf, (int, float)) and conf > 0.4 and capped(ctx, ev, list_name):
        refuse("%s rests on evidence that is capped at confidence 0.4 (the person's word, a family tree, an import, "
               "a compiled work, an unexamined citation or a proof not accepted now -- it never stands alone); "
               "confidence %s is too high" % (why, conf), "R-11")
    # R-12: dates
    for key in ("date", "end_date"):
        d = claim.get(key)
        if not isinstance(d, dict):
            continue
        problems, warnings = date_check(d, ctx.today_day)
        if problems:
            refuse("%s %s: %s" % (why, key, "; ".join(problems)), "R-12")
        for w in warnings:
            ctx.warn("%s %s: %s" % (why, key, w))
        if d.get("as_written") and ev.get("quote") and not text_contains(ev["quote"], d["as_written"]):
            refuse("%s %s: its as_written words %r are not in the evidence quote" % (why, key, d["as_written"]), "R-12")
        if record is not None and entry is not None and fit == "other" and d.get("derivation", "as_recorded") == "as_recorded":
            refuse("%s %s: the cited entry is used indirectly, so the date must say how it was worked out "
                   "(derivation calculated, estimated or interpreted), not as_recorded" % (why, key), "R-12")
    # person rules
    if kind == "person" and list_name == "event_assertions":
        if claim.get("event_type") in COUPLE_EVENT_TYPES and claim.get("role") in COUPLE_ROLES:
            refuse("%s: a couple's own %s is a family fact -- send it with `link` (kind couple); witnesses and "
                   "officiants are fine here" % (why, claim["event_type"]), "R-22")
    if kind == "person" and claim.get("occurrence_id"):
        principal = list_name in ("birth_assertions", "death_assertions") or (
            list_name == "event_assertions" and claim.get("role") in (None, "principal"))
        occ_row = ctx.row("event", ctx.resolve(claim["occurrence_id"]))
        if principal and occ_row and occ_row["event_type"] in ONE_PRINCIPAL_EVENTS:
            mine = ctx.resolve(holder["person_id"])
            ids = ctx.merged_into(ctx.resolve(claim["occurrence_id"]))
            qs = ",".join("?" * len(ids))
            for r in ctx.con.execute("SELECT holder_id, effective_holder_id, assertion_id, list_name FROM claim_index "
                                     "WHERE holder_table='max_person' AND occurrence_id IN (%s) AND status IN "
                                     "('proposed','proven','disputed')" % qs, list(ids)).fetchall():
                if r["effective_holder_id"] == mine:
                    continue
                if r["list_name"] in ("birth_assertions", "death_assertions"):
                    refuse("%s: occurrence %s already has a principal (%s); one principal per %s -- twins get one "
                           "event entry each" % (why, claim["occurrence_id"], r["effective_holder_id"],
                                                 occ_row["event_type"]), "R-21")
                if r["list_name"] == "event_assertions":
                    other = ctx.doc("person", r["holder_id"])
                    for c in (other or {}).get("event_assertions") or []:
                        if low(c.get("assertion_id")) == r["assertion_id"] and c.get("role") in (None, "principal"):
                            refuse("%s: occurrence %s already has a principal (%s)" % (why, claim["occurrence_id"],
                                                                                       r["effective_holder_id"]), "R-21")
    if kind == "person" and list_name == "photo_assertions":
        rec = _record_for(ctx, claim["photo_record_id"], why + " photo_record_id")
        photo = copy_on(rec, claim.get("sha256"), claim.get("location_uri"))
        if photo is None:
            refuse("%s: photograph %s is not a saved copy on record %s"
                   % (why, claim.get("sha256") or claim.get("location_uri"), rec["record_id"]), "R-04")
        regions = {low(r["region_id"]) for r in photo.get("regions") or []}
        for rid in claim.get("region_ids") or []:
            if low(rid) not in regions:
                refuse("%s: region %s is not on that photograph's copy" % (why, rid), "R-04")
    if kind == "relationship" and list_name == "lineage_assertions":
        check_lineage_fact(ctx, holder, claim, record, entry, why)
    if kind == "relationship" and list_name == "fact_assertions":
        for p in claim.get("partners_as_recorded") or []:
            if p.get("mention_id"):
                if record is None:
                    refuse("%s: partners_as_recorded mention ids need document evidence" % why, "R-10")
                check_mention(ctx, record, p["mention_id"], why + " partners_as_recorded")
    if claim.get("supersedes_assertion_id"):
        sup = ctx.con.execute("SELECT holder_table, effective_holder_id FROM claim_index WHERE assertion_id=?",
                              (low(claim["supersedes_assertion_id"]),)).fetchone()
        if not sup:
            refuse("%s supersedes claim %s, which is not stored" % (why, claim["supersedes_assertion_id"]), "R-04")
        if sup["holder_table"] != TABLE[kind] or sup["effective_holder_id"] != ctx.resolve(holder[KEY[kind]]):
            if not getattr(ctx, "splitting", False):
                refuse("%s supersedes a claim of another document; only split may do that" % why, "R-04")
    return record, entry, fit


def check_lineage_fact(ctx, rel, fact, record, entry, why):
    """R-17, R-18, R-19 and the mention labels of a lineage fact."""
    ev = fact.get("evidence") or {}
    for key in ("parent_mention_id", "child_mention_id"):
        if fact.get(key):
            if record is None:
                refuse("%s: %s needs document evidence (it is a line on the cited record)" % (why, key), "R-10")
            check_mention(ctx, record, fact[key], why + " " + key)
    census = record is not None and (record.get("record_type") == "census_household" or
                                     (entry or {}).get("event_type") == "census")
    if census and fact.get("fact_type") not in CENSUS_OK_LINEAGE:
        refuse("%s: a census line never shows a %s; use lineage unknown (or step_parent, adoptive_parent, "
               "foster_parent or guardian when the page says so)" % (why, fact.get("fact_type")), "R-17")
    if fact.get("dimension") == "genetic":
        ok = ev.get("basis") == "dna" or (ev.get("basis") == "conclusion" and reliable(ctx, ev))
        if not ok:
            refuse("%s: dimension genetic needs DNA evidence or an accepted proof; a document (even a birth "
                   "certificate) shows a legal presumption, not a genetic finding" % why, "R-18")
    if record is not None and record.get("record_status") in ("amended", "reissued", "delayed"):
        fact["amended_record_flag"] = True
    if record is not None:
        pair = (ctx.resolve(rel.get("parent_id")), ctx.resolve(rel.get("child_id")))
        for r in ctx.con.execute("SELECT holder_id, assertion_id FROM claim_index WHERE holder_table='max_relationship' "
                                 "AND list_name='lineage_assertions' AND status='disproven' AND fact_type=? AND "
                                 "source_record_id=?", (fact.get("fact_type"), low(record["record_id"]))).fetchall():
            other = ctx.doc("relationship", r["holder_id"])
            if other and (ctx.resolve(other["parent_id"]), ctx.resolve(other["child_id"])) == pair:
                refuse("%s: this %s fact from record %s was disproven; new evidence (another record) is needed"
                       % (why, fact.get("fact_type"), record["record_id"]), "R-19")


def check_document_references(ctx, kind, doc, stored=None):
    """R-04 for whole-document references (Appendix C), new values only on a replace."""
    def refs(d):
        out = set()
        if not d:
            return out
        for row in REFERENCES:
            for path, target in row["paths"]:
                holder, _, dotted = path.partition(":")
                if holder not in ("*", kind):
                    continue
                tkind = ref_target(target, kind)
                if tkind is None:
                    continue
                for v in values_at(d, [s for s in dotted.split(".") if s]):
                    for x in (v if isinstance(v, list) else [v]):
                        if isinstance(x, str) and ID_RE.match(x) and low(x) != low(d[KEY[kind]]):
                            out.add((path, tkind, low(x), row["doc_redirects"], row["new_write"]))
        return out
    new_refs = refs(doc) - refs(stored)
    for path, target, value, redirects, new_write in sorted(new_refs):
        if path.endswith("evidence.source_record_id") or path.endswith("evidence.import_record_id") or \
                path.endswith("evidence.dna_id") or path.endswith("evidence.conclusion_task_id") or \
                path.endswith("evidence.task_id"):
            continue    # checked with the claim's evidence
        if path == "record:events_documented[].occurrence_id":
            continue    # made by the helper in the same write (or brought by the same import); `check` verifies
        ident = ctx.resolve(value) if redirects else value
        row = ctx.row(target, ident)
        if row is None:
            refuse("%s names %s %s, which is not in this database -- add it first (no invented references)"
                   % (path.split(":", 1)[1], TITLE.get(target, target), value), "R-04")
        if target == "task" and row["status"] == "cancelled" and "not cancelled" in new_write:
            refuse("%s names task %s, which is cancelled" % (path.split(":", 1)[1], value), "R-04")
        if new_write.startswith("active") and target != "task" and target in LIFECYCLE_KINDS:
            if row["lifecycle"] != "active":
                refuse("%s names %s %s, which is %s" % (path.split(":", 1)[1], TITLE[target], value, row["lifecycle"]),
                       "R-04")


# ------------------------------------------------------------------ record gates (5.2, R-09, R-16)

MULTI_PERSON_TYPES = ("census_household", "parish_register", "passenger_list", "tax_record", "voter_list",
                      "city_directory")


def check_record(ctx, rec, stored=None):
    rid = low(rec["record_id"])
    labels = {}
    for field, key in (("persons_mentioned", "mention_id"), ("events_documented", "ref"),
                       ("transcription_parts", "part_id")):
        seen = set()
        for item in rec.get(field) or []:
            if item.get(key) in seen:
                refuse("%s %r appears twice; labels on one record are unique and permanent" % (key, item.get(key)), "R-07")
            seen.add(item.get(key))
        labels[field] = seen
    lines = record_lines(rec)
    line_list = []
    for part in rec.get("transcription_parts") or []:
        line_list += re.findall(r"^\[([A-Za-z0-9][A-Za-z0-9._:-]{0,63})\] ", part.get("text", ""), re.M)
    dup = sorted({x for x in line_list if line_list.count(x) > 1})
    if dup:
        refuse("line label(s) %s appear more than once in the transcription; line labels are unique" % dup, "R-07")
    for m in rec.get("persons_mentioned") or []:
        if m.get("relative_to") and m["relative_to"] not in labels["persons_mentioned"]:
            refuse("mention %s: relative_to %r is not a mention on this record" % (m["mention_id"], m["relative_to"]), "R-04")
        for ref in m.get("in_event_refs") or []:
            if ref not in labels["events_documented"]:
                refuse("mention %s: in_event_refs %r is not an event entry on this record" % (m["mention_id"], ref), "R-04")
        for line in m.get("lines") or []:
            if line not in lines:
                refuse("mention %s: line %r is not a line label in the transcription (a line starts with [%s] )"
                       % (m["mention_id"], line, line), "R-04")
    primaries = [e for e in rec.get("events_documented") or [] if e.get("is_primary")]
    if len(primaries) > 1:
        refuse("more than one event entry is marked is_primary", "R-04")
    for e in rec.get("events_documented") or []:
        if e.get("informant_mention_id") and e["informant_mention_id"] not in labels["persons_mentioned"]:
            refuse("event entry %s: informant_mention_id %r is not a mention on this record"
                   % (e["ref"], e["informant_mention_id"]), "R-04")
        if isinstance(e.get("date"), dict):
            problems, warnings = date_check(e["date"], ctx.today_day)
            if problems:
                refuse("event entry %s date: %s" % (e["ref"], "; ".join(problems)), "R-12")
            for w in warnings:
                ctx.warn("event entry %s date: %s" % (e["ref"], w))
    if isinstance(rec.get("record_date"), dict):
        problems, warnings = date_check(rec["record_date"], ctx.today_day)
        if problems:
            refuse("record_date: %s" % "; ".join(problems), "R-12")
    if rec.get("record_type") in MULTI_PERSON_TYPES and len(rec.get("persons_mentioned") or []) > 1 and \
            any(not (m.get("lines") or m["mention_id"] in lines) for m in rec.get("persons_mentioned") or []):
        ctx.warn("record %s names several people but not every mention has its line labelled; Quality Control "
                 "will fail it until each mention's lines are given" % rid)
    # saved copies and regions (3.7, R-09, R-16)
    stored_shas = {c.get("sha256") for c in (stored or {}).get("saved_copies") or [] if c.get("sha256")}
    stored_regions = set(regions_of(stored or {}))
    region_ids = set()
    for c in rec.get("saved_copies") or []:
        if c.get("redistribution_license") and LICENCE_ORDER.get(c["redistribution_license"], 4) < \
                LICENCE_ORDER.get(rec["redistribution_license"], 4):
            refuse("saved copy %s: its licence %s is looser than the record's %s; a copy may be stricter, never looser"
                   % (c.get("sha256") or "(pending)", c["redistribution_license"], rec["redistribution_license"]), "R-16")
        if c.get("sha256") and c["sha256"] not in stored_shas:
            for r in ctx.con.execute("SELECT held_on_record_id FROM media_index WHERE sha256=?", (c["sha256"],)).fetchall():
                other = ctx.row("record", r["held_on_record_id"])
                if other and other["lifecycle"] == "active" and other["record_id"] != rid:
                    refuse("the file %s is already described on record %s -- point at it with media_refs instead, or "
                           "cite that record (one original, one record)" % (c["sha256"], other["record_id"]), "R-09")
        for r in c.get("regions") or []:
            r_id = low(r["region_id"])
            if r_id in region_ids:
                refuse("region %s appears twice on this record" % r_id, "R-16")
            region_ids.add(r_id)
            if r_id not in stored_regions:
                hit = ctx.con.execute("SELECT record_id FROM region_index WHERE region_id=?", (r_id,)).fetchone()
                if hit and hit["record_id"] != rid:
                    refuse("region id %s is already used on record %s; region ids are unique" % (r_id, hit["record_id"]),
                           "R-16")
        df = c.get("derived_from")
        if df:
            own = {x.get("sha256"): x for x in rec.get("saved_copies") or [] if x.get("sha256")}
            src = own.get(df["sha256"])
            if src is None:
                hit = ctx.con.execute("SELECT held_on_record_id FROM media_index WHERE sha256=?", (df["sha256"],)).fetchone()
                if not hit:
                    refuse("saved copy derived_from %s, which is not described on any record here" % df["sha256"], "R-04")
                src_rec = ctx.doc("record", hit["held_on_record_id"])
                src = next((x for x in (src_rec or {}).get("saved_copies") or [] if x.get("sha256") == df["sha256"]), {})
            if df.get("region_id") and low(df["region_id"]) not in {low(r["region_id"]) for r in src.get("regions") or []}:
                refuse("saved copy derived_from region %s, which is not on copy %s" % (df["region_id"], df["sha256"]), "R-04")
    check_media_refs(ctx, rec.get("media_refs"))
    # one original, one record (R-09)
    if (rec.get("source_classification") or {}).get("source") == "original":
        rec["original_key"] = original_key_of(ctx, rec.get("part_of_source_id"), rec.get("locator"), rec["record_type"])
    else:
        rec.pop("original_key", None)
    ok = rec.get("original_key") or {}
    if ok and not ok.get("partial"):
        hit = ctx.con.execute("SELECT record_id FROM max_record WHERE original_key=? AND original_key_partial=0 AND "
                              "lifecycle='active' AND record_id != ?", (ok["key"], rid)).fetchone()
        if hit:
            refuse("record %s already holds this original (key %s); one original, one record -- cite that record"
                   % (hit["record_id"], ok["key"]), "R-09")
    uk = url_key(rec)
    if uk:
        hit = ctx.con.execute("SELECT record_id FROM max_record WHERE url_key=? AND lifecycle='active' AND record_id != ?",
                              (uk, rid)).fetchone()
        if hit:
            refuse("record %s already holds this line of this page (%s); cite that record" % (hit["record_id"], uk), "R-09")
    elif page_url(rec) and stored is None:
        for r in ctx.con.execute("SELECT record_id, doc FROM max_record WHERE lifecycle='active' AND record_type=?",
                                 (rec["record_type"],)).fetchall():
            if page_url(json.loads(r["doc"])) == page_url(rec):
                ctx.warn("record %s has the same page address and type; if it is the same entry, cite it instead"
                         % r["record_id"])
                break
    if rec.get("transcription_parts"):
        rec["transcription_sha256"] = hashlib.sha256(canon(rec["transcription_parts"]).encode("utf-8")).hexdigest()
    else:
        rec.pop("transcription_sha256", None)


def copy_on(rec, sha256=None, location_uri=None):
    """The saved copy on a record that a pointer names: by fingerprint, or -- while the copy is pending
    (an imported file not yet on this computer) -- by one of its locations."""
    for c in rec.get("saved_copies") or []:
        if sha256 and c.get("sha256") == sha256:
            return c
        if not sha256 and location_uri and any(loc.get("uri") == location_uri for loc in c.get("locations") or []):
            return c
    return None


def check_media_refs(ctx, media_refs):
    for mr in media_refs or []:
        row = ctx.row("record", ctx.resolve(mr["held_on_record_id"]))
        if not row or row["lifecycle"] != "active":
            refuse("media_refs names record %s, which is not an active record here" % mr["held_on_record_id"], "R-04")
        rec = json.loads(row["doc"])
        copy_ = copy_on(rec, mr.get("sha256"), mr.get("location_uri"))
        if copy_ is None:
            refuse("media_refs names file %s, which is not described on record %s"
                   % (mr.get("sha256") or mr.get("location_uri"), row["record_id"]), "R-04")
        regions = {low(r["region_id"]) for r in copy_.get("regions") or [] if r.get("status", "active") == "active"}
        for r in mr.get("region_ids") or []:
            if low(r) not in regions:
                refuse("media_refs names region %s, which is not an active region on that copy" % r, "R-04")


# ------------------------------------------------------------------ add, claim, show

def write_task(ctx, doc, stored=None, provenance=None, created_by=None):
    """A MaxTask: created_by comes from the session (R-14); provenance is written once."""
    sess = ctx.require_session()
    agent = created_by or sess["provenance"]["agent_id"]
    if doc.get("created_by") and doc["created_by"] != agent and stored is None:
        refuse("created_by %r differs from this session's agent %r; the helper fills it" % (doc["created_by"], agent),
               "R-14")
    if stored is None:
        doc["created_by"] = agent
        if doc["status"] != "pending":
            prov = copy.deepcopy(provenance or sess["provenance"])
            prov["claimed_at"] = now()
            doc["provenance"] = prov
            if doc["status"] == "processing":
                doc["started_at"] = now()
            if doc["status"] in ("done", "failed", "escalated", "cancelled"):
                doc["completed_at"] = now()
    for s in (doc.get("result") or {}).get("steps") or []:
        s.setdefault("at", now())
    steps = [s.get("step_no") for s in (doc.get("result") or {}).get("steps") or []]
    if len(steps) != len(set(steps)):
        refuse("two search steps share a step_no; step numbers are permanent and unique", "R-07")
    if doc.get("is_living_flag") is not True and living_now(ctx, "task", doc):
        doc["is_living_flag"] = True
        doc["contributor_eligibility"] = "first_party_only"
    return doc


def check_task_references(ctx, doc, stored=None):
    """R-04 for the by-kind references inside a task: proof subjects, what a proof relies on, the
    claims it settles, the memberships it updates, and occurrence verdicts."""
    if stored is not None and (stored.get("result") or {}).get("proof") == (doc.get("result") or {}).get("proof"):
        return
    res = doc.get("result") or {}
    proof = res.get("proof") or {}

    def claim_exists(aid):
        return ctx.con.execute("SELECT 1 FROM claim_index WHERE assertion_id=?", (low(aid),)).fetchone()
    for item in proof.get("relied_on") or []:
        k = item["kind"]
        ok = True
        if k == "claim":
            ok = bool(claim_exists(item["assertion_id"]))
        elif k in ("record", "authored_record"):
            ok = bool(ctx.row("record", ctx.resolve(item["record_id"])))
        elif k == "mention":
            r = ctx.doc("record", item["record_id"])
            ok = bool(r) and mention_of(r, item["mention_id"]) is not None
        elif k == "dna_match":
            ok = low(item["match_id"]) in dna_match_ids(ctx, ctx.resolve(item["dna_id"]), live_only=False)
        elif k == "search_step":
            t = ctx.doc("task", item["task_id"])
            ok = bool(t) and item["step_no"] in {s.get("step_no") for s in (t.get("result") or {}).get("steps") or []}
        elif k == "proof":
            t = ctx.doc("task", item["task_id"])
            ok = bool(t) and has_proof(t)
        if not ok:
            refuse("the proof relies on a %s that is not in this database (%s)" % (k, ", ".join(
                "%s=%s" % (x, y) for x, y in item.items() if x != "kind")), "R-04")
    for s_item in (proof.get("conclusion") or {}).get("settles") or []:
        if not claim_exists(s_item["assertion_id"]):
            refuse("the proof settles claim %s, which is not stored" % s_item["assertion_id"], "R-04")
    for mu in (proof.get("conclusion") or {}).get("membership_updates") or []:
        if not resolve_membership(ctx, mu["membership_id"]):
            refuse("the proof updates membership %s, which is not stored" % mu["membership_id"], "R-04")
    for ov in res.get("occurrence_verdicts") or []:
        for o in list(ov["occurrence_ids"]) + ([ov["survivor_id"]] if ov.get("survivor_id") else []):
            if not ctx.row("event", ctx.resolve(o)):
                refuse("occurrence %s is not in this database" % o, "R-04")


def add_document(ctx, doc, task_arg=None, replace=False, expect_sha=None, quiet=False):
    """The whole pipeline for one document (8.2). Returns (kind, stored doc, 'staged'|'replaced'|'stored')."""
    problem = version_problem(doc.get("schema_version"))        # the stamp is read first (2.1)
    if problem:
        refuse(problem, "R-02")
    try:
        kind = kind_of(doc)
    except Refused:
        minor = int(doc["schema_version"].split(".")[1])
        if minor > KNOWN_MINOR:      # a record kind a later 2.x added
            refuse(UPDATE_MESSAGE % (minor, KNOWN_MINOR), "R-02")
        raise
    check_version(ctx, kind, doc)
    key = KEY[kind]
    if not isinstance(doc.get(key), str) or not ID_RE.match(doc[key]):
        errs = ctx.schemas.errors(kind, doc)
        refuse("%s %r is not a uuid%s" % (key, doc.get(key), ("; " + "; ".join(errs[:3])) if errs else ""), "R-01")
    doc[key] = low(doc[key])
    ident = doc[key]
    ctx.require_session()
    stored_row = ctx.row(kind, ident)
    stored = json.loads(stored_row["doc"]) if stored_row else None
    if replace:
        if stored is None:
            refuse("--replace: there is no %s %s to replace" % (TITLE[kind], ident), "R-07")
        if not expect_sha or expect_sha != stored_row["doc_sha256"]:
            refuse("--replace needs --expect-sha256 <the stored doc_sha256> (shown by `show`); it is %s -- someone "
                   "else may have changed it" % stored_row["doc_sha256"], "R-07")
    else:
        other = ctx.kind_of_id(ident)
        if other:
            refuse("%s %s already exists; pass --replace --expect-sha256 <sha> to correct it" % (TITLE[other], ident), "R-03")
    # the task
    task = None
    if kind != "task":
        tid = low(task_arg or doc.get("created_by_task_id") or (stored or {}).get("created_by_task_id"))
        if task_arg and doc.get("created_by_task_id") and low(doc["created_by_task_id"]) != low(task_arg) and not replace:
            refuse("created_by_task_id %s differs from --task %s" % (doc["created_by_task_id"], task_arg), "R-14")
        task = require_task(ctx, tid, purpose="adding a %s" % TITLE[kind])
        task_arg = tid
    import_task = task if task and task["task_type"] == "import_file" else None
    strip_readonly(ctx, kind, doc, keep=IMPORT_KEEP if import_task else ())
    if kind != "task" and not replace:
        doc["created_by_task_id"] = low(task_arg)
    # match on replace, fill helper values
    lowered = []
    if replace:
        doc, new_claims, lowered = match_replace(ctx, kind, stored, doc, task_arg)
    else:
        new_claims = list(iter_claims(kind, doc))
        if not import_task:
            for m in doc.get("children") or []:
                m.pop("membership_id", None)
            for e in doc.get("events_documented") or []:
                e.pop("occurrence_id", None)
    for list_name, c in new_claims:
        fill_new_claim(ctx, kind, list_name, c, task_arg if kind != "task" else None, import_task)
    if lowered:
        if not (task and person_accepted(ctx, task["task_id"])):
            refuse("this replace lowers %s; lowering a living floor or a licence needs the person's recorded yes "
                   "(a judge_review of task %s, accepted, by the person)" % (", ".join(lowered), task_arg), "R-05")
    # kind-specific helper values
    if kind == "record" and doc.get("record_type") == "dna_match_list" and (
            doc.get("redistribution_license") != "tier2-private" or doc.get("is_living_flag") is not True
            or any(m.get("is_living") is not True for m in doc.get("persons_mentioned") or [])):
        refuse("a DNA match list is always private: redistribution_license tier2-private, is_living_flag true, and "
               "is_living true on every match line", "R-05")
    if kind == "record":
        for m in doc.get("persons_mentioned") or []:
            m.setdefault("status", "active")
        for e in doc.get("events_documented") or []:
            e.setdefault("status", "active")
            if not e.get("occurrence_id"):
                e["occurrence_id"] = new_id()
                e["_new_occurrence"] = True
    if kind == "relationship":
        for m in doc.get("children") or []:
            if not m.get("membership_id"):
                m["membership_id"] = new_id()
            m["membership_id"] = low(m["membership_id"])
            m.setdefault("status", "active")
            m.setdefault("created_at", now())
            m.setdefault("is_preferred", False)
            m.setdefault("task_id", low(task_arg))
        for p in doc.get("partners") or []:
            p.setdefault("status", "active")
    if kind == "task":
        doc = write_task(ctx, doc, stored)
    # R-01: the schema
    probe = copy.deepcopy(doc)
    for e in probe.get("events_documented") or []:
        e.pop("_new_occurrence", None)
    errs = ctx.schemas.errors(kind, probe)
    if errs:
        minor = int(str(doc.get("schema_version", "2.0")).split(".")[1] or 0)
        if minor > KNOWN_MINOR:
            refuse(UPDATE_MESSAGE % (minor, KNOWN_MINOR), "R-02")
        raise Refused("the document does not match %s (R-01)\n   - %s" % (FILE[kind], "\n   - ".join(errs)))
    # gates
    check_document_references(ctx, kind, doc, stored)
    if kind == "task":
        check_task_references(ctx, doc, stored)
    check_media_refs(ctx, doc.get("media_refs") if kind != "record" else None)
    if kind in ("relationship", "place", "group", "event") and not replace:
        if task["task_type"] not in ANALYSIS_TASKS:
            refuse("a %s is written only by the helper, an importer or an analysis task: add it under an import_file, "
                   "resolve_conflict or build_tree task (task %s is %s)%s"
                   % (TITLE[kind], task_arg, task["task_type"],
                      "; family links go through `link`" if kind == "relationship" else ""), "R-24")
    if kind == "event":
        origin = doc["origin"]
        if origin["kind"] == "document_entry" and not import_task:
            refuse("an occurrence of a document entry is made by the helper when the record is added", "R-24")
        if origin["kind"] in ("import", "analysis") and low(origin["task_id"]) != low(task_arg):
            refuse("origin.task_id must be the task this occurrence is added under (%s)" % task_arg, "R-24")
        changed_analysis = any(doc.get(f) != (stored or {}).get(f) for f in ("preferred", "conclusion_assertions", "related"))
        if changed_analysis and task["task_type"] not in ("resolve_conflict", "build_tree"):
            refuse("preferred, conclusion_assertions and related change only under a resolve_conflict or build_tree task",
                   "R-24")
    if kind == "person":
        if doc.get("person_kind") == "placeholder" and import_task and not replace:
            refuse("no placeholder person is created under an import_file task", "R-23")
        for nc in doc.get("name_assertions") or []:
            check_name_parts(ctx, nc)
    if kind == "record":
        check_record(ctx, doc, stored)
    if kind == "recognition" and (doc.get("copy_sha256") or doc.get("region_id")):
        holders = [ctx.doc("record", ctx.resolve(doc["raw_record_id"]))] if doc.get("raw_record_id") else             [ctx.doc("record", r["held_on_record_id"]) for r in
             ctx.con.execute("SELECT held_on_record_id FROM media_index WHERE sha256=?", (doc.get("copy_sha256"),)).fetchall()]
        copies = [c for h in holders if h for c in h.get("saved_copies") or []
                  if not doc.get("copy_sha256") or c.get("sha256") == doc["copy_sha256"]]
        if doc.get("copy_sha256") and not copies:
            refuse("copy_sha256 %s is not a saved copy on the record read" % doc["copy_sha256"], "R-04")
        if doc.get("region_id") and low(doc["region_id"]) not in {low(r["region_id"]) for c in copies
                                                                   for r in c.get("regions") or []}:
            refuse("region_id %s is not a region on that copy" % doc["region_id"], "R-04")
    if kind == "dna":
        for mt in doc.get("matches") or []:
            other = mt.get("match_dna_id")
            if other and ctx.row("dna", other) and low(other) < low(doc["dna_id"]):
                refuse("a match between two tests in this database is stored once, on the test with the smaller "
                       "dna_id (%s)" % low(other), "R-25")
    task_docs = {}
    if task is not None:
        task_docs[low(task_arg)] = task
    for list_name, c in new_claims:
        if kind == "task":
            continue
        check_claim(ctx, kind, doc, list_name, c, task_docs)
    if kind == "relationship":
        check_relationship_doc(ctx, doc, task, stored)
    # store
    new_occ = []
    for e in doc.get("events_documented") or []:
        if e.pop("_new_occurrence", None):
            new_occ.append(e)
    added_by = ctx.agent_id
    if stored is None:
        ctx.store(kind, doc, new=True, added_by=added_by)
    else:
        ctx.store(kind, doc, content=True)
    for e in new_occ:
        make_occurrence_with_id(ctx, doc, e, task_arg)
    if kind == "record" and replace:
        swap_pending_refs(ctx, doc, task_arg)
        for h in recheck_claims_citing(ctx, doc, task_arg):
            note = "%s cites this record and its quote no longer matches; it is staged again (R-15)" % h
            if ctx.quiet:
                ctx.warnings.append(note)
            else:
                print("NOTE: " + note)
    after_write(ctx, kind, doc, task_arg)
    state = "replaced" if replace else ("staged" if kind in STAGED_KINDS else "stored")
    return kind, doc, state


def swap_pending_refs(ctx, record, task_id):
    """R-16: once a pending copy is fingerprinted, every pointer that named it by location (media_refs,
    photo claims) gets the fingerprint instead, as logged annotation writes."""
    found = {}
    for c in record.get("saved_copies") or []:
        if c.get("sha256"):
            for loc in c.get("locations") or []:
                found[loc.get("uri")] = c["sha256"]
    if not found:
        return
    rid = low(record["record_id"])
    for kind in ("record", "relationship", "event", "person"):
        for r in ctx.con.execute("SELECT doc FROM %s WHERE doc LIKE '%%location_uri%%'" % TABLE[kind]).fetchall():
            d = json.loads(r["doc"])
            changed = False
            for mr in list(d.get("media_refs") or []):
                uri = mr.get("location_uri")
                if uri in found and not mr.get("sha256") and ctx.resolve(mr["held_on_record_id"]) == rid:
                    path = ["media_refs", {"k": "location_uri", "v": uri}]
                    changed |= ctx.annotate(kind, d, path + ["sha256"], found[uri], task_id)
                    changed |= ctx.annotate(kind, d, path + ["location_uri"], None, task_id)
            for c in list(d.get("photo_assertions") or []) if kind == "person" else []:
                uri = c.get("location_uri")
                if uri in found and not c.get("sha256") and ctx.resolve(c["photo_record_id"]) == rid:
                    path = ["photo_assertions", {"k": "assertion_id", "v": c["assertion_id"]}]
                    changed |= ctx.annotate(kind, d, path + ["sha256"], found[uri], task_id)
                    changed |= ctx.annotate(kind, d, path + ["location_uri"], None, task_id)
            if changed:
                ctx.store(kind, d, content=False)


def recheck_claims_citing(ctx, record, task_id):
    """R-15: after a record is corrected, every claim citing it gets a fresh quote check and fresh
    information / amended flags (annotation writes); every holder whose quote no longer matches
    goes back to staged. Returns the holders re-staged."""
    rid = low(record["record_id"])
    rows = ctx.con.execute("SELECT DISTINCT holder_table, holder_id FROM claim_index WHERE source_record_id=?",
                           (rid,)).fetchall()
    restaged = []
    good = ("matched", "matched_whole_text")
    for h in rows:
        kind = KIND_OF_TABLE[h["holder_table"]]
        doc = ctx.doc(kind, h["holder_id"])
        if not doc:
            continue
        fell = False
        for ln, c in iter_claims(kind, doc):
            ev = c.get("evidence") or {}
            if ev.get("basis") != "document" or low(ev.get("source_record_id")) != rid:
                continue
            path = [ln, {"k": "assertion_id", "v": c["assertion_id"]}]
            entry = entry_of(record, ev.get("record_event_ref")) if ev.get("record_event_ref") else None
            info = (entry or {}).get("information") or (record.get("source_classification") or {}).get("information")
            ctx.annotate(kind, doc, path + ["evidence", "information"], info, task_id)
            if ev.get("quote"):
                old = (ev.get("quote_check") or {}).get("status")
                qc = quote_check_document(record, ev, task_id)
                ctx.annotate(kind, doc, path + ["evidence", "quote_check"], qc, task_id)
                if old in good and qc["status"] not in good and live_claim(c):
                    fell = True
            if kind == "relationship" and ln == "lineage_assertions":
                flag = True if record.get("record_status") in ("amended", "reissued", "delayed") else None
                ctx.annotate(kind, doc, path + ["amended_record_flag"], flag, task_id)
        ctx.store(kind, doc, content=False)
        if fell and kind in STAGED_KINDS:
            ctx.con.execute("UPDATE %s SET stage='staged' WHERE %s=?" % (TABLE[kind], KEY[kind]), (low(doc[KEY[kind]]),))
            restaged.append("%s %s" % (TITLE[kind], doc[KEY[kind]]))
    return restaged


def make_occurrence_with_id(ctx, record, entry, task_id):
    living = bool(record.get("is_living_flag")) or any(
        m.get("is_living") for m in record.get("persons_mentioned") or [] if entry["ref"] in (m.get("in_event_refs") or []))
    doc = {"occurrence_id": low(entry["occurrence_id"]), "schema_version": MAXGEN_VERSION, "event_type": entry["event_type"],
           "origin": {"kind": "document_entry", "record_id": low(record["record_id"]), "record_event_ref": entry["ref"]},
           "is_living_flag": living,
           "redistribution_license": "tier2-private" if living else record["redistribution_license"],
           "created_by_task_id": low(task_id)}
    if entry.get("event_type") == "other" and entry.get("event_type_as_written"):
        doc["event_type_as_written"] = entry["event_type_as_written"]
    if entry.get("type_uri"):
        doc["type_uri"] = entry["type_uri"]
    if ctx.row("event", doc["occurrence_id"]):
        refuse("occurrence %s already exists" % doc["occurrence_id"], "R-03")
    ctx.store("event", doc, new=True)


def check_name_parts(ctx, nc):
    parts = nc.get("name_parts") or []
    if not parts:
        return
    # the shortcuts must agree with name_parts (5.1): given_name with the given parts only (a call name is
    # one of the given names, never added to them), surname with the surname parts (particles included)
    given = " ".join(p["value_as_written"] for p in parts if p["part_type"] == "given")
    surname = " ".join(p["value_as_written"] for p in parts if p["part_type"] in ("surname_prefix", "surname"))
    fold = lambda s: re.sub(r"\s+", " ", s or "").strip().lower()
    if nc.get("given_name") and given and fold(nc["given_name"]) != fold(given):
        refuse("name %r: given_name %r does not agree with its name_parts (%r); write the shortcut as the given "
               "parts, or leave it out" % (nc.get("name_as_written"), nc["given_name"], given), "R-01")
    if nc.get("surname") and surname and fold(nc["surname"]) != fold(surname) and \
            fold(nc["surname"]) not in fold(" ".join(p["value_as_written"] for p in parts)):
        refuse("name %r: surname %r does not agree with its name_parts (%r); write the shortcut as the surname "
               "parts, or leave it out" % (nc.get("name_as_written"), nc["surname"], surname), "R-01")


def after_write(ctx, kind, doc, task_id):
    """What every write does afterwards: floors raised for anyone who may be alive, preferences
    recomputed, occurrence owners refreshed, proofs' claims re-indexed."""
    occs = {c.get("occurrence_id") for _l, c in iter_claims(kind, doc) if c.get("occurrence_id")}
    if kind == "record":
        occs |= {e.get("occurrence_id") for e in doc.get("events_documented") or []}
    refresh_event_owners(ctx, [o for o in occs if o])
    persons = set()
    if kind == "person":
        persons.add(doc["person_id"])
    elif kind == "relationship":
        persons.update(relationship_persons(ctx, doc))
        for m in doc.get("children") or []:
            recompute_preferred(ctx, ctx.resolve(m["child_id"]), task_id, reason="link")
        refresh_relationship_floor(ctx, ctx.doc("relationship", doc["relationship_id"]), task_id)
        # R-32 also runs on relationships an import or analysis adds directly (an imported tree whose
        # program wrote "Birth" on both of an adoptee's families)
        children = {ctx.resolve(m["child_id"]) for m in doc.get("children") or [] if m.get("status", "active") == "active"}
        if doc["kind"] == "parent_child":
            children.add(ctx.resolve(doc["child_id"]))
        for child in sorted(children):
            if ctx.row("person", child) and competing_birth_parents(ctx, child) and not conflict_task_of(ctx, child):
                cid = open_birth_conflict(ctx, child, task_id, [doc["relationship_id"]])
                ctx.warn("%s now has two birth parent sets (or two birth fathers/mothers); a resolve_conflict task "
                         "%s was created (R-32)" % (child, cid))
    elif kind == "task":
        target = (doc.get("result") or {}).get("review", {}).get("target_task_id")
        if target:
            refresh_claims_citing_proof(ctx, target)
    elif kind == "dna":
        persons.add(doc["person_id"])
    for p in persons:
        if p and ctx.row("person", p):
            raise_floors_for_person(ctx, p, task_id)


def refresh_claims_citing_proof(ctx, proof_task_id):
    """Reliability of conclusion-basis claims changes when a proof's reviews change."""
    holders = ctx.con.execute("SELECT DISTINCT holder_table, holder_id FROM claim_index WHERE conclusion_task_id=?",
                              (low(proof_task_id),)).fetchall()
    for h in holders:
        k = KIND_OF_TABLE[h["holder_table"]]
        d = ctx.doc(k, h["holder_id"])
        if d:
            index_doc(ctx, k, d)
    ctx.forget()


def cmd_add(ctx, a):
    doc = load_json_file(a.file)
    kind, stored, state = add_document(ctx, doc, a.task, a.replace, a.expect_sha256)
    ctx.log("add", "%s %s" % (kind, stored[KEY[kind]]))
    ctx.commit()
    extra = ""
    if kind == "record" and stored.get("events_documented"):
        extra = " (%d occurrence%s)" % (len(stored["events_documented"]), "" if len(stored["events_documented"]) == 1 else "s")
    print("OK  %s -> %s %s (%s)%s" % (a.file, TABLE[kind], stored[KEY[kind]], state, extra))


def cmd_claim(ctx, a):
    """Append claims to an existing person without resending the whole file (8.2)."""
    data = load_json_file(a.file)
    pid = data.get("person_id")
    added = claim_core(ctx, data, a.task)
    ctx.log("claim", "%s +%d" % (pid, len(added)))
    ctx.commit()
    print("OK  %d claim(s) added to person %s (staged for Quality Control): %s"
          % (len(added), low(pid), ", ".join(c["assertion_id"] for _l, c in added)))


def claim_core(ctx, data, task_id):
    """The claim command's work, without committing or printing (the Gate's `claim` calls it too).
    ``data`` is a claims file: {"person_id": ..., "<list>_assertions": [...]}. Returns [(list, claim)]."""
    a = argparse.Namespace(task=task_id)
    pid = data.get("person_id")
    if not pid:
        refuse("a claims file names the person: {\"person_id\": ..., \"birth_assertions\": [...]}")
    unknown = [k for k in data if k != "person_id" and k not in CLAIM_LISTS["person"]]
    if unknown:
        refuse("unknown field(s) %s -- a claims file holds person_id and MaxPerson _assertions lists" % unknown, "R-01")
    row = ctx.row("person", pid)
    if not row:
        refuse("person %s is not in this database -- add the MaxPerson first" % pid, "R-04")
    if row["lifecycle"] != "active":
        refuse("person %s is %s; add claims to %s instead" % (pid, row["lifecycle"], ctx.resolve(pid)), "R-04")
    doc = json.loads(row["doc"])
    task = require_task(ctx, a.task, purpose="adding claims")
    import_task = task if task["task_type"] == "import_file" else None
    added = []
    for list_name in CLAIM_LISTS["person"]:
        for c in data.get(list_name) or []:
            if not isinstance(c, dict):
                refuse("%s must hold claim objects" % list_name, "R-01")
            strip_readonly(ctx, "person", {list_name: [c]}, keep=IMPORT_KEEP if import_task else ())
            fill_new_claim(ctx, "person", list_name, c, a.task, import_task)
            doc.setdefault(list_name, []).append(c)
            added.append((list_name, c))
    if not added:
        refuse("the claims file holds no claims")
    errs = ctx.schemas.errors("person", doc)
    if errs:
        raise Refused("the person with these claims does not match person.schema.json (R-01)\n   - %s" % "\n   - ".join(errs))
    task_docs = {low(a.task): task}
    for list_name, c in added:
        check_claim(ctx, "person", doc, list_name, c, task_docs)
    ctx.store("person", doc, content=True)
    after_write(ctx, "person", doc, a.task)
    return added


def cmd_show(ctx, a):
    kind = ctx.kind_of_id(a.id)
    if not kind:
        refuse("%s is not an id in this database" % a.id)
    row = ctx.row(kind, a.id)
    print(json.dumps(json.loads(row["doc"]), indent=2, ensure_ascii=False))
    extra = ""
    if kind in STAGED_KINDS:
        extra = "  stage=%s" % row["stage"]
    if kind in LIFECYCLE_KINDS and row["lifecycle"] != "active":
        extra += "  lifecycle=%s -> %s" % (row["lifecycle"], ctx.resolve(a.id))
    print("doc_sha256 %s  (%s)%s" % (row["doc_sha256"], TITLE[kind], extra))


# ------------------------------------------------------------------ relationships: rules shared by add and link

def live_person(ctx, person_id, what="person"):
    pid = ctx.resolve(person_id)
    row = ctx.row("person", pid)
    if not row:
        refuse("%s %s is not in this database -- add that person first, then the link" % (what, person_id), "R-04")
    if row["lifecycle"] != "active":
        refuse("%s %s is %s" % (what, person_id, row["lifecycle"]), "R-06")
    return pid


def live_facts(doc, list_name="lineage_assertions"):
    return [f for f in doc.get(list_name) or [] if live_claim(f)]


def parent_child_of(ctx, parent, child):
    """The live parent_child record for a (resolved) pair, or None."""
    for rid, _role in links_of(ctx, parent, "parent"):
        d = ctx.doc("relationship", rid)
        if d and d["kind"] == "parent_child" and ctx.resolve(d["child_id"]) == child:
            return d
    return None


def families_of(ctx, person_id, role="partner"):
    out = []
    for rid, _r in links_of(ctx, person_id, role):
        d = ctx.doc("relationship", rid)
        if d and d["kind"] == "family":
            out.append(d)
    return out


def memberships_of(ctx, child_id, family_id=None, live=True):
    sql = "SELECT * FROM membership_index WHERE child_id=?"
    args = [ctx.resolve(child_id)]
    if family_id:
        sql += " AND family_id=?"
        args.append(ctx.resolve(family_id))
    if live:
        sql += " AND status='active'"
    sql += " ORDER BY rowid"
    out = []
    for r in ctx.con.execute(sql, args).fetchall():
        fam = ctx.row("relationship", r["family_id"])
        if fam and fam["lifecycle"] == "active":
            out.append(dict(r))
    return out


def membership_links(ctx, m):
    """A membership's parent links: its own plus those of memberships combined into it (4.1)."""
    links = list(json.loads(m["parent_link_ids"]))
    for r in ctx.con.execute("SELECT parent_link_ids FROM membership_index WHERE merged_into_membership_id=? "
                             "ORDER BY rowid", (m["membership_id"],)).fetchall():
        for x in json.loads(r["parent_link_ids"]):
            if x not in links:
                links.append(x)
    return [ctx.resolve(x) for x in links]


def find_membership(doc, membership_id):
    for m in doc.get("children") or []:
        if low(m.get("membership_id")) == low(membership_id):
            return m
    return None


def check_relationship_doc(ctx, doc, task, stored=None, adoption_checked=False):
    """R-06, R-20 and R-31 for a relationship document (R-31 only for what is new: an adoptive
    membership or an adoptive_parent fact that the stored document did not have)."""
    stored = stored or {}
    old_adoptive = {low(m.get("membership_id")) for m in stored.get("children") or [] if m.get("set_kind") == "adoptive"}
    old_facts = {low(f.get("assertion_id")) for f in stored.get("lineage_assertions") or []}
    k = doc["kind"]
    if k == "parent_child":
        p, c = live_person(ctx, doc["parent_id"], "parent"), live_person(ctx, doc["child_id"], "child")
        if p == c:
            refuse("a person cannot be their own parent", "R-06")
        other = parent_child_of(ctx, p, c)
        if other and low(other["relationship_id"]) != low(doc["relationship_id"]):
            refuse("person %s already has a parent_child record for child %s (%s); one record per pair -- add the "
                   "fact there" % (p, c, other["relationship_id"]), "R-20")
        rev = parent_child_of(ctx, c, p)
        if rev and live_facts(rev) and live_facts(doc):
            refuse("%s is already recorded as a parent of %s; a direct cycle is impossible" % (c, p), "R-06")
        if not adoption_checked and any(f.get("fact_type") == "adoptive_parent" and low(f.get("assertion_id")) not in old_facts
                                        for f in live_facts(doc)) and not person_accepted(ctx, task["task_id"]):
            refuse("recording an adoption needs the person's yes first: a judge_review of task %s, accepted, by the "
                   "person" % task["task_id"], "R-31")
    elif k == "family":
        ids = [ctx.resolve(p["person_id"]) for p in active_partners(doc) if p.get("person_id")]
        for pid in ids:
            live_person(ctx, pid, "partner")
        if len(ids) != len(set(ids)):
            refuse("the two partners of a family must be different people", "R-06")
        kinds = {}
        for m in doc.get("children") or []:
            if m.get("status", "active") != "active":
                continue
            child = live_person(ctx, m["child_id"], "child")
            if child in ids:
                refuse("a child cannot be a partner of its own family", "R-06")
            key = (child, m["set_kind"])
            if key in kinds:
                refuse("child %s has two live %s memberships in this family; one per child and set kind"
                       % (child, m["set_kind"]), "R-20")
            kinds[key] = True
            for lid in m.get("parent_link_ids") or []:
                pc = ctx.doc("relationship", ctx.resolve(lid))
                if not pc or pc["kind"] != "parent_child" or ctx.resolve(pc["child_id"]) != child:
                    refuse("membership of %s names parent link %s, which is not a parent_child record for that child"
                           % (child, lid), "R-04")
            if not adoption_checked and m["set_kind"] == "adoptive" and low(m.get("membership_id")) not in old_adoptive                     and not person_accepted(ctx, task["task_id"]):
                refuse("recording an adoption needs the person's yes first (task %s)" % task["task_id"], "R-31")
    else:
        for role in ("party1", "party2"):
            party = doc.get(role) or {}
            if party.get("person_id"):
                live_person(ctx, party["person_id"], role)
            elif party.get("mention"):
                rec = _record_for(ctx, party["mention"]["record_id"], role)
                check_mention(ctx, rec, party["mention"]["mention_id"], role)
        p1, p2 = (doc.get("party1") or {}).get("person_id"), (doc.get("party2") or {}).get("person_id")
        if p1 and p2 and ctx.resolve(p1) == ctx.resolve(p2):
            refuse("an association needs two different people", "R-06")


def relationship_licence(ctx, doc):
    lics = [doc.get("redistribution_license")]
    for pid in relationship_persons(ctx, doc) if doc["kind"] != "family" else partner_ids(ctx, doc):
        p = ctx.doc("person", pid)
        if p:
            lics.append(p.get("redistribution_license"))
    lists = ("fact_assertions",) if doc["kind"] == "family" else CLAIM_LISTS["relationship"]
    for ln in lists:
        for c in doc.get(ln) or []:
            if live_claim(c):
                ev = c.get("evidence") or {}
                if ev.get("source_record_id"):
                    r = ctx.row("record", ctx.resolve(ev["source_record_id"]))
                    if r:
                        lics.append(r["redistribution_license"])
                if ev.get("import_record_id"):
                    lics.append("tier2-private")
    return strictest(*lics)


def refresh_relationship_floor(ctx, doc, task_id):
    """Floors (flags and licences) of one relationship, raised never lowered (4.2)."""
    if not doc:
        return
    changed = False
    if doc["kind"] == "family":
        living = any(person_living_now(ctx, p) for p in partner_ids(ctx, doc))
        for m in doc.get("children") or []:
            if m.get("status", "active") != "active" or m.get("is_living_flag"):
                continue
            ids = {ctx.resolve(m["child_id"])}
            for lid in m.get("parent_link_ids") or []:
                pc = ctx.doc("relationship", ctx.resolve(lid))
                if pc:
                    ids.add(ctx.resolve(pc["parent_id"]))
            if any(person_living_now(ctx, x) for x in ids):
                changed |= ctx.annotate("relationship", doc, ["children", {"k": "membership_id", "v": m["membership_id"]},
                                                              "is_living_flag"], True, task_id)
    else:
        living = living_now(ctx, "relationship", dict(doc, is_living_flag=False))
    lic = relationship_licence(ctx, doc)
    if living:
        changed |= ctx.annotate("relationship", doc, ["is_living_flag"], True, task_id)
        lic = "tier2-private"
    if LICENCE_ORDER.get(lic, 4) > LICENCE_ORDER.get(doc.get("redistribution_license"), 4):
        changed |= ctx.annotate("relationship", doc, ["redistribution_license"], lic, task_id)
    if changed:
        ctx.store("relationship", doc, content=False)


# ------------------------------------------------------------------ the preferred parent set (9.1)

def membership_complete(ctx, m):
    for lid in membership_links(ctx, m):
        pc = ctx.doc("relationship", lid)
        if not pc or not live_facts(pc):
            return False
    return True


def recompute_preferred(ctx, child_id, task_id, reason="link"):
    """Exactly one live membership per child is preferred: a recorded preference while it holds,
    else the computed default (complete first, then kind rank, then age, then id). When the
    preference moves for any reason but a new link, a pending review is opened."""
    child = ctx.resolve(child_id)
    ms = memberships_of(ctx, child)
    if not ms:
        return None
    recorded = [m for m in ms if m["preferred_by_task_id"]]
    chosen, lost, lost_ids = None, False, set()
    for m in sorted(recorded, key=lambda m: m["created_at"]):
        if membership_complete(ctx, m):
            chosen = m
        else:
            lost = True
            lost_ids.add(m["membership_id"])
    if chosen is None:
        ranked = sorted(ms, key=lambda m: (0 if membership_complete(ctx, m) else 1, SET_RANK.get(m["set_kind"], 6),
                                           m["created_at"], m["membership_id"]))
        chosen = ranked[0]
    every = [dict(r) for r in ctx.con.execute("SELECT * FROM membership_index WHERE child_id=?", (child,)).fetchall()]
    before = [m["membership_id"] for m in every if m["is_preferred"]]
    live_ids = {m["membership_id"] for m in ms}
    ms = ms + [m for m in every if m["membership_id"] not in live_ids and m["is_preferred"]]
    changed_families = {}
    for m in ms:
        want = m["membership_id"] == chosen["membership_id"]
        fam = changed_families.get(m["family_id"]) or ctx.doc("relationship", m["family_id"])
        # the membership may live on a merged-away family document that forwards to this one
        holder = fam if find_membership(fam, m["membership_id"]) else None
        if holder is None:
            for fid in ctx.merged_into(m["family_id"]):
                d = ctx.doc("relationship", fid)
                if d and find_membership(d, m["membership_id"]):
                    holder = d
                    break
        if holder is None:
            continue
        path = ["children", {"k": "membership_id", "v": m["membership_id"]}]
        ch = ctx.annotate("relationship", holder, path + ["is_preferred"], want, task_id)
        if (not want or m["membership_id"] in lost_ids) and m["preferred_by_task_id"]:
            ch |= ctx.annotate("relationship", holder, path + ["preferred_by_task_id"], None, task_id)
        if ch:
            changed_families[low(holder["relationship_id"])] = holder
            ctx.store("relationship", holder, content=False)
    after = chosen["membership_id"]
    if (before and sorted(before) != [after] and reason != "link") or lost:
        open_review(ctx, child, task_id, "the main parents of %s changed; please confirm" % child)
    return after


def open_review(ctx, person_id, task_id, words):
    doc = {"task_id": new_id(), "schema_version": MAXGEN_VERSION, "task_type": "judge_review", "status": "pending",
           "priority": 5, "is_living_flag": person_living_now(ctx, person_id),
           "payload": {"kind": "main_parents_changed", "person_id": low(person_id)},
           "result": {"what_we_did_and_result": words}}
    if task_id and ctx.row("task", task_id):
        doc["parent_task_id"] = low(task_id)
    write_task(ctx, doc)
    ctx.store("task", doc, new=True)
    return doc["task_id"]


# ------------------------------------------------------------------ the link command (8.3)

def new_relationship(ctx, kind, task_id, **fields):
    doc = {"relationship_id": new_id(), "schema_version": MAXGEN_VERSION, "kind": kind, "is_living_flag": False,
           "redistribution_license": "CC0", "created_by_task_id": low(task_id)}
    doc.update(fields)
    return doc


def _lineage_fact(p, req, task_id):
    fact = {"fact_type": p["lineage"], "evidence": copy.deepcopy(p.get("evidence") or req["evidence"]),
            "confidence": req["confidence"], "task_id": low(task_id)}
    if p.get("lineage_as_written"):
        fact["fact_type_as_written"] = p["lineage_as_written"]
    if p.get("dimension"):
        fact["dimension"] = p["dimension"]
    for key in ("date", "end_date", "place", "notes"):
        if req.get(key):
            fact[key] = copy.deepcopy(req[key])
    if p.get("mention_id"):
        fact["parent_mention_id"] = p["mention_id"]
    if req.get("child_mention_id"):
        fact["child_mention_id"] = req["child_mention_id"]
    return fact


def _save_rel(ctx, doc, new):
    errs = ctx.schemas.errors("relationship", doc)
    if errs:
        raise Refused("the relationship would not match relationship.schema.json (R-01)\n   - %s" % "\n   - ".join(errs))
    if new:
        ctx.store("relationship", doc, new=True, added_by=ctx.agent_id)
    else:
        ctx.store("relationship", doc, content=True)


def _add_partner(ctx, fam, pid, label, task_id):
    """Fill an empty slot or an absent item with a person (a content write)."""
    act = active_partners(fam)
    if any(p.get("person_id") and ctx.resolve(p["person_id"]) == pid for p in act):
        return False
    absent = [p for p in act if p.get("absent")]
    if len(act) >= 2 and not absent:
        refuse("family %s already has two partners; %s cannot join it" % (fam["relationship_id"], pid), "R-20")
    if len(act) >= 2 and absent:
        absent[0]["status"] = "retracted"
        absent[0]["status_reason"] = "the partner is now named"
        absent[0]["status_task_id"] = low(task_id)
    item = {"person_id": pid, "status": "active"}
    if label:
        item["label_as_written"] = label
    fam.setdefault("partners", []).append(item)
    return True


def link_parents(ctx, req, task):
    task_id = task["task_id"]
    child = live_person(ctx, req["child_id"], "child")
    parents = []
    for p in req["parents"]:
        pid = live_person(ctx, p["person_id"], "parent")
        if pid == child:
            refuse("a person cannot be their own parent", "R-06")
        parents.append((pid, p))
    if len(parents) == 2 and parents[0][0] == parents[1][0]:
        refuse("the two parents named are the same person", "R-06")
    for pid, _p in parents:
        rev = parent_child_of(ctx, child, pid)
        if rev and live_facts(rev):
            refuse("%s is already recorded as a parent of %s; a person cannot be their own ancestor this way"
                   % (child, pid), "R-06")
    explicit = "set_kind" in req
    if explicit:
        K = req["set_kind"]
    else:
        kinds = [LINEAGE_TO_SET.get(p["lineage"], "other") for _pid, p in parents]
        K = next(k for k in SPECIFIC_ORDER if k in kinds)
    adoption = K == "adoptive" or any(p["lineage"] == "adoptive_parent" for _pid, p in parents)
    if adoption and not person_accepted(ctx, task_id):
        refuse("recording an adoption needs the person's yes first: ask them, then record their answer with "
               "`task %s <status> --answer accepted --note \"<their words>\"`" % task_id, "R-31")
    before_birth_sets = [m for m in memberships_of(ctx, child) if m["set_kind"] == "birth"]
    before_birth_roles = birth_parent_roles(ctx, child)
    # steps 2-3: one parent_child record per pair, one lineage fact per parent
    links, notes = [], []
    task_docs = {low(task_id): task}
    for pid, p in parents:
        pc = parent_child_of(ctx, pid, child)
        role = p.get("parent_role", "unknown")
        new = pc is None
        if new:
            pc = new_relationship(ctx, "parent_child", task_id, parent_id=pid, child_id=child, parent_role=role,
                                  lineage_assertions=[])
            if p.get("parent_role_as_written"):
                pc["parent_role_as_written"] = p["parent_role_as_written"]
        else:
            stored_role = pc.get("parent_role", "unknown")
            if stored_role in ("father", "mother") and role in ("father", "mother") and stored_role != role:
                refuse("conflicting parent roles: %s is recorded as the %s of %s, and this document says %s; an "
                       "analysis task must settle this" % (pid, stored_role, child, role), "R-06")
            if stored_role in ("unknown", "other") and role in ("father", "mother", "parent"):
                pc["parent_role"] = role
        fact = _lineage_fact(p, req, task_id)
        fill_new_claim(ctx, "relationship", "lineage_assertions", fact, task_id)
        check_claim(ctx, "relationship", pc, "lineage_assertions", fact, task_docs,
                    why="the %s link of %s to %s" % (p["lineage"], pid, child))
        pc["lineage_assertions"].append(fact)
        if new:
            pc["redistribution_license"] = relationship_licence(ctx, pc)
            if person_living_now(ctx, pid) or person_living_now(ctx, child):
                pc["is_living_flag"] = True
                pc["redistribution_license"] = "tier2-private"
        _save_rel(ctx, pc, new)
        links.append((pid, p, pc["relationship_id"], new))
    link_ids = [low(x[2]) for x in links]
    req_parents = [pid for pid, _p in parents]
    # step 5: choose the family
    fam, fam_new = None, False
    if req.get("family_id"):
        fam = ctx.doc("relationship", ctx.resolve(req["family_id"]))
        if not fam or fam["kind"] != "family" or fam.get("lifecycle", "active") != "active":
            refuse("family_id %s is not a live family record" % req["family_id"], "R-04")
        for pid, p in parents:
            _add_partner(ctx, fam, pid, p.get("parent_role_as_written"), task_id)
    elif req.get("new_family"):
        fam_new = True
    else:
        cands = []
        for m in memberships_of(ctx, child):
            f = ctx.doc("relationship", m["family_id"])
            if not f or any(low(f["relationship_id"]) == low(x["relationship_id"]) for x in cands):
                continue
            pids = set(partner_ids(ctx, f))
            # 5c: a family with a partner the document does not name is used only when the child already
            # has a membership of kind K there (a mother-only birth record never joins her husband's household)
            if set(req_parents) <= pids and (pids <= set(req_parents) or any(
                    x["set_kind"] == K for x in memberships_of(ctx, child, f["relationship_id"]))):
                cands.append(f)
        if len(cands) == 1:
            fam = cands[0]
        elif len(cands) > 1:
            if explicit:
                with_k = [f for f in cands if any(m["set_kind"] == K for m in memberships_of(ctx, child, f["relationship_id"]))]
                if len(with_k) == 1:
                    fam = with_k[0]
                else:
                    refuse("the child is in more than one family with these parents; pass family_id", "R-20")
            else:
                refuse("the child is in more than one family with these parents; pass family_id (or set_kind)", "R-20")
        if fam is None and len(req_parents) == 2:
            exact = [f for f in families_of(ctx, req_parents[0])
                     if sorted(partner_ids(ctx, f)) == sorted(req_parents)]
            if len(exact) == 1:
                fam = exact[0]
            elif len(exact) > 1:
                refuse("this couple has more than one family record (for example a remarriage); pass family_id", "R-20")
        if fam is None and len(req_parents) == 2 and explicit:
            for i, pid in enumerate(req_parents):
                other = req_parents[1 - i]
                for f in families_of(ctx, pid):
                    if partner_ids(ctx, f) != [pid] or len(active_partners(f)) >= 2:
                        continue
                    ms = [m for m in memberships_of(ctx, child, f["relationship_id"]) if m["set_kind"] == K]
                    if not ms:
                        continue
                    same_doc = False
                    for m in ms:
                        for lid in membership_links(ctx, m):
                            pc = ctx.doc("relationship", lid)
                            for fct in live_facts(pc or {}):
                                if low((fct.get("evidence") or {}).get("source_record_id")) == \
                                        low(req["evidence"].get("source_record_id")) and req["evidence"].get("source_record_id"):
                                    same_doc = True
                    if same_doc:
                        fam = f
                        _add_partner(ctx, fam, other, dict(parents)[other].get("parent_role_as_written"), task_id)
                        break
                if fam is not None:
                    break
        if fam is None and len(req_parents) == 1:
            for f in families_of(ctx, req_parents[0]):
                if partner_ids(ctx, f) == [req_parents[0]] and \
                        any(m["set_kind"] == K for m in memberships_of(ctx, child, f["relationship_id"])):
                    fam = f
                    break
        if fam is None:
            fam_new = True
    if fam_new:
        partners = []
        for pid, p in parents:
            item = {"person_id": pid, "status": "active"}
            if p.get("parent_role_as_written"):
                item["label_as_written"] = p["parent_role_as_written"]
            partners.append(item)
        fam = new_relationship(ctx, "family", task_id, partners=partners, union_type="unknown", children=[])
    if child in partner_ids(ctx, fam):
        refuse("a child cannot be a partner of its own family", "R-06")
    # step 6: the membership
    fam.setdefault("children", [])
    live_here = [m for m in fam["children"] if ctx.resolve(m["child_id"]) == child and m.get("status", "active") == "active"]
    target = next((m for m in live_here if m["set_kind"] == K), None)

    def _links_named(mem):
        # 6b: every parent link in the membership belongs to a parent this request names
        for lid in mem.get("parent_link_ids") or []:
            pc = ctx.doc("relationship", ctx.resolve(lid))
            if not pc or ctx.resolve(pc["parent_id"]) not in req_parents:
                return False
        return True
    if target is None and K != "other" and len(live_here) == 1 and _links_named(live_here[0]) and (
            live_here[0]["set_kind"] == "other" or
            (live_here[0]["set_kind"] == "alternative_hypothesis" and explicit and task["task_type"] == "resolve_conflict")):
        target = live_here[0]
        ctx.annotate("relationship", fam, ["children", {"k": "membership_id", "v": target["membership_id"]}, "set_kind"],
                     K, task_id)
        if req.get("set_kind_as_written"):
            target["set_kind_as_written"] = req["set_kind_as_written"]
        else:
            target.pop("set_kind_as_written", None)
    if target is None and not explicit and K == "other":
        if len(live_here) == 1:
            target = live_here[0]
        elif len(live_here) > 1:
            refuse("the child has several parent sets in this family; pass set_kind", "R-20")
    if target is None:
        target = {"membership_id": new_id(), "child_id": child, "parent_link_ids": [], "set_kind": K,
                  "status": "active", "created_at": now(), "is_preferred": False, "task_id": low(task_id),
                  "is_living_flag": any(person_living_now(ctx, x) for x in [child] + req_parents)}
        if req.get("set_kind_as_written"):
            target["set_kind_as_written"] = req["set_kind_as_written"]
        elif not explicit and K == "other":
            target["set_kind_as_written"] = "kind of parent set not stated"
        if req.get("child_order"):
            target["child_order"] = req["child_order"]
        fam["children"].append(target)
    for lid in link_ids:
        if lid not in [low(x) for x in target.setdefault("parent_link_ids", [])]:
            target["parent_link_ids"].append(lid)
    if fam_new:
        fam["redistribution_license"] = relationship_licence(ctx, fam)
        if any(person_living_now(ctx, p) for p in partner_ids(ctx, fam)):
            fam["is_living_flag"] = True
            fam["redistribution_license"] = "tier2-private"
    check_relationship_doc(ctx, fam, task, adoption_checked=True)
    _save_rel(ctx, fam, fam_new)
    # step 7: floors, preference, indexes
    for _pid, _p, rid, _n in links:
        refresh_relationship_floor(ctx, ctx.doc("relationship", rid), task_id)
    refresh_relationship_floor(ctx, ctx.doc("relationship", fam["relationship_id"]), task_id)
    preferred = recompute_preferred(ctx, child, task_id, reason="link")
    refresh_event_owners(ctx, [f.get("occurrence_id") for _x in links for f in
                               (ctx.doc("relationship", _x[2]) or {}).get("lineage_assertions") or []])
    # R-32: competing birth parents are flagged, not refused
    after_birth_sets = [m for m in memberships_of(ctx, child) if m["set_kind"] == "birth"]
    after_roles = birth_parent_roles(ctx, child)
    second = (len(after_birth_sets) >= 2 and len(before_birth_sets) < 2) or any(
        len(after_roles.get(r, ())) >= 2 and len(before_birth_roles.get(r, ())) < 2 for r in ("father", "mother"))
    if second:
        cid = open_birth_conflict(ctx, child, task_id,
                                  sorted({low(x[2]) for x in links} | {low(fam["relationship_id"])}))
        notes.append("WARNING: %s now has two birth parent sets (or two birth fathers/mothers); a resolve_conflict "
                     "task %s was created -- if the new document only names the missing parent of an existing set, "
                     "retract this link and send it again with that set's family_id" % (child, cid))
    pcs = ", ".join("parent_child %s (%s)" % (rid, "new" if n else "updated") for _pid, _p, rid, n in links)
    return ("OK link: %s, family %s (%s), membership %s %s (%s) -- staged for Quality Control"
            % (pcs, fam["relationship_id"], "new" if fam_new else "updated", target["membership_id"],
               target["set_kind"], "preferred" if preferred == target["membership_id"] else "not preferred"), notes)


def competing_birth_parents(ctx, child) -> bool:
    """R-32: two live birth sets, or two birth fathers or two birth mothers."""
    roles = birth_parent_roles(ctx, child)
    return (sum(1 for m in memberships_of(ctx, child) if m["set_kind"] == "birth") >= 2
            or any(len(roles.get(r, ())) >= 2 for r in ("father", "mother")))


def conflict_task_of(ctx, child):
    """An open or settled (not cancelled) resolve_conflict task about this child, or None."""
    for r in ctx.con.execute("SELECT doc FROM max_task WHERE task_type='resolve_conflict' AND status != 'cancelled'"):
        d = json.loads(r["doc"])
        pid = (d.get("payload") or {}).get("person_id")
        if pid and ctx.resolve(pid) == ctx.resolve(child):
            return d
    return None


def open_birth_conflict(ctx, child, task_id, rel_ids):
    """R-32: competing birth parents are flagged with a pending resolve_conflict task, never refused."""
    conflict = {"task_id": new_id(), "schema_version": MAXGEN_VERSION, "task_type": "resolve_conflict",
                "status": "pending", "priority": 3, "is_living_flag": person_living_now(ctx, child),
                "payload": {"kind": "analysis", "person_id": child, "relationship_ids": sorted(set(rel_ids))},
                "result": {"what_we_did_and_result": "Two birth parent sets (or two birth fathers/mothers) are "
                                                     "now recorded for this person; settle which is right."}}
    if task_id and ctx.row("task", task_id):
        conflict["parent_task_id"] = low(task_id)
    write_task(ctx, conflict)
    ctx.store("task", conflict, new=True)
    return conflict["task_id"]


def birth_parent_roles(ctx, child):
    """{role: set of parent_child ids} of live birth_parent facts for a child."""
    out = {}
    for rid, _r in links_of(ctx, child, "child"):
        d = ctx.doc("relationship", rid)
        if d and d["kind"] == "parent_child" and any(f.get("fact_type") == "birth_parent" for f in live_facts(d)):
            out.setdefault(d.get("parent_role", "unknown"), set()).add(rid)
    return out


def link_couple(ctx, req, task):
    task_id = task["task_id"]
    persons = []
    for p in req["partners"]:
        if p.get("person_id"):
            persons.append((live_person(ctx, p["person_id"], "partner"), p))
    absent = [p for p in req["partners"] if p.get("absent")]
    if len(persons) == 2 and persons[0][0] == persons[1][0]:
        refuse("the two partners named are the same person", "R-06")
    ids = [pid for pid, _p in persons]
    fam, fam_new, notes = None, False, []
    if req.get("family_id"):
        fam = ctx.doc("relationship", ctx.resolve(req["family_id"]))
        if not fam or fam["kind"] != "family" or fam.get("lifecycle", "active") != "active":
            refuse("family_id %s is not a live family record" % req["family_id"], "R-04")
        for pid, p in persons:
            _add_partner(ctx, fam, pid, p.get("label_as_written"), task_id)
    elif len(ids) == 2:
        exact = [f for f in families_of(ctx, ids[0]) if sorted(partner_ids(ctx, f)) == sorted(ids)]
        if len(exact) > 1:
            refuse("this couple has more than one family record (for example a remarriage); pass family_id", "R-20")
        if exact and not req.get("new_union"):
            fam = exact[0]
        else:
            fam_new = True
            maybe = []
            for pid in ids:
                for f in families_of(ctx, pid):
                    act = active_partners(f)
                    if partner_ids(ctx, f) == [pid] and (len(act) < 2 or any(x.get("absent") for x in act)):
                        maybe.append(f["relationship_id"])
            if maybe:
                notes.append("NOTE: family %s may be the same union; pass family_id to use it, or an analysis task "
                             "can merge the families" % ", ".join(sorted(set(maybe))))
    else:
        pid = ids[0]
        cands = [f for f in families_of(ctx, pid) if partner_ids(ctx, f) == [pid] and
                 any(live_claim(c) and c.get("fact_type") in COUPLE_FACTS for c in f.get("fact_assertions") or [])]
        if len(cands) > 1:
            refuse("this person has more than one union with an unnamed partner; pass family_id", "R-20")
        if cands:
            fam = cands[0]
        else:
            fam_new = True
    if fam_new:
        partners = []
        for pid, p in persons:
            item = {"person_id": pid, "status": "active"}
            if p.get("label_as_written"):
                item["label_as_written"] = p["label_as_written"]
            partners.append(item)
        for p in absent[:1]:
            item = {"absent": True, "status": "active"}
            if p.get("label_as_written"):
                item["label_as_written"] = p["label_as_written"]
            if p.get("phrase_as_written"):
                item["phrase_as_imported"] = p["phrase_as_written"]
            partners.append(item)
        fam = new_relationship(ctx, "family", task_id, partners=partners, union_type=req.get("union_type", "unknown"))
    fact = {"fact_type": req["fact"]["fact_type"], "evidence": copy.deepcopy(req["evidence"]),
            "confidence": req["confidence"], "task_id": low(task_id)}
    for k in ("fact_type_as_written", "ceremony", "ceremony_as_written", "value_as_written"):
        if req["fact"].get(k):
            fact[k] = req["fact"][k]
    for k in ("date", "place", "notes"):
        if req.get(k):
            fact[k] = copy.deepcopy(req[k])
    par = []
    for pid, p in persons:
        item = {"person_id": pid}
        if p.get("mention_id"):
            item["mention_id"] = p["mention_id"]
        if p.get("age_as_written"):
            item["age_as_written"] = p["age_as_written"]
        par.append(item)
    if par:
        fact["partners_as_recorded"] = par
    fill_new_claim(ctx, "relationship", "fact_assertions", fact, task_id)
    check_claim(ctx, "relationship", fam, "fact_assertions", fact, {low(task_id): task},
                why="the %s fact of %s" % (fact["fact_type"], " and ".join(ids)))
    fam.setdefault("fact_assertions", []).append(fact)
    if fam_new:
        fam["redistribution_license"] = relationship_licence(ctx, fam)
        if any(person_living_now(ctx, p) for p in ids):
            fam["is_living_flag"] = True
            fam["redistribution_license"] = "tier2-private"
    check_relationship_doc(ctx, fam, task, adoption_checked=True)
    _save_rel(ctx, fam, fam_new)
    refresh_relationship_floor(ctx, ctx.doc("relationship", fam["relationship_id"]), task_id)
    refresh_event_owners(ctx, [fact.get("occurrence_id")])
    return ("OK link: family %s (%s), %s fact %s -- staged for Quality Control"
            % (fam["relationship_id"], "new" if fam_new else "updated", fact["fact_type"], fact["assertion_id"]), notes)


def _party(ctx, party, role):
    out = {}
    if party.get("person_id"):
        out["person_id"] = live_person(ctx, party["person_id"], role)
    else:
        rec = _record_for(ctx, party["mention"]["record_id"], role)
        check_mention(ctx, rec, party["mention"]["mention_id"], role)
        out["mention"] = {"record_id": low(party["mention"]["record_id"]), "mention_id": party["mention"]["mention_id"]}
    for k in ("name_as_written", "role", "role_as_written"):
        if party.get(k):
            out[k] = party[k]
    return out


def _party_key(p):
    return p.get("person_id") or "%s#%s" % (p["mention"]["record_id"], p["mention"]["mention_id"])


def link_association(ctx, req, task):
    task_id = task["task_id"]
    p1, p2 = _party(ctx, req["party1"], "party1"), _party(ctx, req["party2"], "party2")
    if _party_key(p1) == _party_key(p2):
        refuse("an association needs two different parties", "R-06")
    at = req["association_type"]
    want = (_party_key(p1), _party_key(p2))
    found = None
    for r in ctx.con.execute("SELECT relationship_id, doc FROM max_relationship WHERE kind='association' AND "
                             "lifecycle='active' AND association_type=?", (at,)).fetchall():
        d = json.loads(r["doc"])
        a1 = _party_key({k: (ctx.resolve(v) if k == "person_id" else v) for k, v in (d.get("party1") or {}).items()})
        a2 = _party_key({k: (ctx.resolve(v) if k == "person_id" else v) for k, v in (d.get("party2") or {}).items()})
        if (a1, a2) == want or (at in UNORDERED_ASSOCIATIONS and (a2, a1) == want):
            found = d
            break
    new = found is None
    doc = found or new_relationship(ctx, "association", task_id, association_type=at, party1=p1, party2=p2,
                                    association_assertions=[])
    if new:
        for k in ("association_type_as_written", "generations"):
            if req.get(k):
                doc[k] = req[k]
    fact = {"fact_type": "existence", "evidence": copy.deepcopy(req["evidence"]), "confidence": req["confidence"],
            "task_id": low(task_id)}
    for k in ("date", "place", "notes"):
        if req.get(k):
            fact[k] = copy.deepcopy(req[k])
    fill_new_claim(ctx, "relationship", "association_assertions", fact, task_id)
    check_claim(ctx, "relationship", doc, "association_assertions", fact, {low(task_id): task},
                why="the %s association" % at)
    doc["association_assertions"].append(fact)
    if new:
        doc["redistribution_license"] = relationship_licence(ctx, doc)
        if living_now(ctx, "relationship", doc):
            doc["is_living_flag"] = True
            doc["redistribution_license"] = "tier2-private"
    check_relationship_doc(ctx, doc, task, adoption_checked=True)
    _save_rel(ctx, doc, new)
    refresh_relationship_floor(ctx, ctx.doc("relationship", doc["relationship_id"]), task_id)
    return ("OK link: association %s %s (%s) -- staged for Quality Control"
            % (at, doc["relationship_id"], "new" if new else "updated"), [])


def link_siblings(ctx, req, task):
    task_id = task["task_id"]
    if task["task_type"] not in ANALYSIS_TASKS:
        refuse("a siblings link (a parent set with no known parents) is made only under an import_file, "
               "resolve_conflict or build_tree task", "R-24")
    kids = []
    for c in req["children"]:
        pid = live_person(ctx, c, "child")
        if pid in kids:
            refuse("child %s is named twice" % pid, "R-06")
        kids.append(pid)
    sk = req.get("set_kind", "birth")
    if sk == "adoptive" and not person_accepted(ctx, task_id):
        refuse("recording an adoption needs the person's yes first", "R-31")
    fam = new_relationship(ctx, "family", task_id, partners=[], union_type="unknown", children=[])
    for i, pid in enumerate(kids):
        m = {"membership_id": new_id(), "child_id": pid, "parent_link_ids": [], "set_kind": sk, "status": "active",
             "created_at": now(), "is_preferred": False, "task_id": low(task_id),
             "is_living_flag": person_living_now(ctx, pid)}
        if req.get("set_kind_as_written"):
            m["set_kind_as_written"] = req["set_kind_as_written"]
        fam["children"].append(m)
    fact = {"fact_type": "existence", "evidence": copy.deepcopy(req["evidence"]), "confidence": req["confidence"],
            "task_id": low(task_id)}
    fill_new_claim(ctx, "relationship", "fact_assertions", fact, task_id)
    check_claim(ctx, "relationship", fam, "fact_assertions", fact, {low(task_id): task}, why="the siblings' family")
    fam["fact_assertions"] = [fact]
    fam["redistribution_license"] = relationship_licence(ctx, fam)
    check_relationship_doc(ctx, fam, task, adoption_checked=True)
    _save_rel(ctx, fam, True)
    refresh_relationship_floor(ctx, ctx.doc("relationship", fam["relationship_id"]), task_id)
    for pid in kids:
        recompute_preferred(ctx, pid, task_id, reason="link")
    return "OK link: family %s (new) with %d children -- staged for Quality Control" % (fam["relationship_id"], len(kids)), []


def link_request(ctx, req, task_arg=None):
    errs = ctx.schemas.errors("link", req)
    if errs:
        raise Refused("the link request does not match the kit's link-request format (R-01)\n   - %s"
                      % "\n   - ".join(errs))
    tid = task_arg or req.get("task_id")
    if task_arg and req.get("task_id") and low(task_arg) != low(req["task_id"]):
        refuse("the request's task_id %s differs from --task %s" % (req["task_id"], task_arg), "R-14")
    task = require_task(ctx, tid, purpose="a link")
    fn = {"parents": link_parents, "couple": link_couple, "association": link_association,
          "siblings": link_siblings}[req["kind"]]
    return fn(ctx, req, task)


def cmd_link(ctx, a):
    req = load_json_file(a.file)
    line, notes = link_request(ctx, req, a.task)
    ctx.log("link", line)
    ctx.commit()
    for n in notes:
        print(n)
    print(line)


# ------------------------------------------------------------------ the lineage resolver (9.2)

DASHED_FACTS = ("adoptive_parent", "step_parent", "foster_parent", "guardian", "sealing")
DASHED_SETS = ("adoptive", "step_household", "foster", "guardianship", "sealing")


def _person_ok(ctx, pid, approved_only):
    row = ctx.row("person", pid)
    return bool(row) and row["lifecycle"] == "active" and (row["stage"] == "approved" or not approved_only)


def link_style(facts, set_kind):
    types = {f.get("fact_type") for f in facts}
    if "birth_parent" in types:
        return "b"
    if types & set(DASHED_FACTS):
        return "l"
    if types:
        return "u"
    return "l" if set_kind in DASHED_SETS else "u"


def parent_sets(con_or_ctx, person_id, *, approved_only=True, include_waiting=False):
    """The person's parent sets (one per live membership). Each: {membership_id, family_id, set_kind,
    set_kind_as_written, is_preferred, created_at, eligible, parents: [{person_id, role, link_id,
    line_style, link_confidence, facts}]}. Only eligible sets are returned unless include_waiting."""
    ctx = con_or_ctx if isinstance(con_or_ctx, (Ctx, _ReadCtx)) else _ReadCtx(con_or_ctx)
    pid = ctx.resolve(person_id)
    if approved_only and not _person_ok(ctx, pid, True) and not include_waiting:
        return []
    out = []
    for m in memberships_of(ctx, pid):
        holder = None
        for fid in sorted(ctx.merged_into(m["family_id"]), key=lambda x: (x == low(m["family_id"]), x)):
            d = ctx.doc("relationship", fid)
            if d and find_membership(d, m["membership_id"]):
                holder = find_membership(d, m["membership_id"])
        parents, eligible = [], False
        for lid in membership_links(ctx, m):
            row = ctx.row("relationship", lid)
            if not row or row["lifecycle"] != "active":
                continue
            pc = json.loads(row["doc"])
            facts = live_facts(pc)
            parent = ctx.resolve(pc["parent_id"])
            # a link re-staged by a newer fact keeps the facts Quality Control already passed (9.2)
            if not approved_only or row["stage"] == "approved":
                checked = facts
            elif row["stage"] == "staged":
                passed = approved_claim_ids(ctx, lid)
                checked = [f for f in facts if low(f.get("assertion_id")) in passed]
            else:
                checked = []
            ok = bool(checked) and _person_ok(ctx, parent, approved_only)
            if not ok and not include_waiting:
                continue
            use = checked if ok else facts
            parents.append({"person_id": parent, "role": pc.get("parent_role", "unknown"), "link_id": lid,
                            "line_style": link_style(use, m["set_kind"]),
                            "link_confidence": max([float(f.get("confidence") or 0) for f in use] or [0.0]),
                            "facts": facts, "checked_facts": checked,
                            "waiting_fact_ids": [low(f.get("assertion_id")) for f in facts
                                                 if not any(f is x for x in checked)],
                            "eligible": ok, "link_stage": row["stage"]})
            eligible = eligible or ok
        if not eligible and not include_waiting:
            continue
        out.append({"membership_id": m["membership_id"], "family_id": m["family_id"], "set_kind": m["set_kind"],
                    "set_kind_as_written": (holder or {}).get("set_kind_as_written"),
                    "is_preferred": bool(m["is_preferred"]), "preferred_by_task_id": m["preferred_by_task_id"],
                    "created_at": m["created_at"], "eligible": eligible,
                    "parents": parents, "is_private": bool((holder or {}).get("is_private")),
                    "is_living_flag": bool((holder or {}).get("is_living_flag"))})
    return out


def _oldest_or_preferred(sets):
    pref = [s for s in sets if s["is_preferred"]]
    if pref:
        return pref[0]
    return sorted(sets, key=lambda s: (s["created_at"], s["membership_id"]))[0]


POSSIBLE_NOTE = "Possible parents, not settled"


def _dotted(s):
    s = copy.deepcopy(s)
    for p in s["parents"]:
        p["line_style"] = "u"
    return s


def choose_set(sets, mode="preferred", pick=None):
    """(set or None, reason): a pick, then the mode's rule, then the preferred set (9.2). Candidate
    parents (alternative_hypothesis) are never drawn as the main parents unless picked or chosen
    (a recorded preference); when they are the only parents recorded they are drawn all dotted with
    the note "Possible parents, not settled"."""
    sets = [s for s in sets if s.get("eligible", True)]
    if pick:
        for s in sets:
            if low(s["membership_id"]) == low(pick):
                return s, "picked"
    if mode == "birth":
        birth = [s for s in sets if s["set_kind"] == "birth"]
        if birth:
            return _oldest_or_preferred(birth), "birth"
        unstated = [s for s in sets if s["set_kind"] == "other"]
        if unstated:
            return _dotted(_oldest_or_preferred(unstated)), "Kind of parents not stated"
        possible = [s for s in sets if s["set_kind"] == "alternative_hypothesis"]
        if possible:
            return _dotted(_oldest_or_preferred(possible)), POSSIBLE_NOTE
        if any(s["set_kind"] in ("adoptive", "step_household", "foster", "guardianship", "sealing") for s in sets):
            return None, "Birth parents not recorded"
    if mode == "adoptive":
        adoptive = [s for s in sets if s["set_kind"] == "adoptive"]
        if adoptive:
            return _oldest_or_preferred(adoptive), "adoptive"
    settled = [s for s in sets if s["set_kind"] != "alternative_hypothesis" or s.get("preferred_by_task_id")]
    pref = [s for s in settled if s["is_preferred"]]
    if pref:
        return pref[0], "preferred"
    if settled:
        return sorted(settled, key=lambda s: (SET_RANK.get(s["set_kind"], 6), s["created_at"]))[0], "default"
    if sets:
        return _dotted(_oldest_or_preferred(sets)), POSSIBLE_NOTE
    return None, "none"


class _ReadCtx:
    """A small read-only view over a connection, for callers that hold only a connection
    (probable_pedigree.py)."""

    def __init__(self, con):
        self.con = con
        self.today = today()
        self.today_day = jdn_gregorian(self.today.year, self.today.month, self.today.day)
        self.settings = date_settings(con)
        self._living = {}
        self._warned = set()
        self.quiet = True

    row = Ctx.row
    doc = Ctx.doc
    resolve = Ctx.resolve
    merged_into = Ctx.merged_into
    kind_of_id = Ctx.kind_of_id
    forget = Ctx.forget

    def warn(self, msg, once_key=None):
        pass


def read_ctx(con):
    return _ReadCtx(con)


def person_dates(ctx, pid, shareable=False):
    """(birth year, death year, has death) from live claims (the effective holder's); on a shareable
    page only claims that are not private (4.2)."""
    b = d = None
    has_death = False
    bconf = dconf = -1
    for ln, c, _h in effective_claims(ctx, "person", pid):
        if shareable and claim_private(ctx, "person", _h, ln, c):
            continue
        y = date_year(c.get("date")) if isinstance(c.get("date"), dict) else None
        conf = float(c.get("confidence") or 0)
        if ln == "birth_assertions" and y is not None and conf > bconf:
            b, bconf = y, conf
        if ln == "death_assertions":
            has_death = True
            if y is not None and conf > dconf:
                d, dconf = y, conf
    return b, d, has_death


def person_display(ctx, pid, shareable=False):
    """(name, confidence) from the best live name claim; on a shareable page only names that are not
    private, and (None, None) when none is left (the person is then withheld, 9.4)."""
    doc = ctx.doc("person", pid) or {}
    names = []
    for ln, c, _h in effective_claims(ctx, "person", pid):
        if ln == "name_assertions" and c.get("name_status", "known") == "known" and c.get("name_as_written"):
            if shareable and claim_private(ctx, "person", _h, ln, c):
                continue
            names.append(c)
    if names:
        pref = [n for n in names if n.get("is_preferred")]
        best = max(pref or names, key=lambda n: float(n.get("confidence") or 0))
        return best["name_as_written"], float(best.get("confidence") or 0)
    if shareable:
        return None, None
    return best_name(doc), None


def shareable_parents(ctx, s, shown):
    """9.4: on the shareable page a parent is drawn only from live, checked, non-private facts of types
    other than adoptive, foster, guardian and sealing; solid for birth_parent, dotted otherwise. A
    candidate (alternative_hypothesis) set is never drawn."""
    if s.get("is_private") or s.get("is_living_flag") or s.get("set_kind") == "alternative_hypothesis":
        return []
    out = []
    for p in s["parents"]:
        if p["person_id"] not in shown:
            continue
        pc = ctx.doc("relationship", p["link_id"])
        if pc is None:
            continue
        ok = [f for f in p.get("checked_facts", p["facts"]) if f.get("fact_type") not in ADOPTION_FACTS
              and not claim_private(ctx, "relationship", pc, "lineage_assertions", f)]
        if ok:
            q = dict(p)
            q["line_style"] = "b" if any(f.get("fact_type") == "birth_parent" for f in ok) else "u"
            q["link_confidence"] = max(float(f.get("confidence") or 0) for f in ok)
            out.append(q)
    return out


def chart_persons(con_or_ctx, mode="preferred", picks=None, *, approved_only=True, shareable=False):
    """{person_id: view} for the chart (the 1.x load_tree shape, extended): n/name, b, d, f, m,
    fk, mk (line styles), fc, mc (link confidences), c (best name confidence), sets, note, living,
    extra (third parents), waiting, u (slot taken by gender guess or as the first empty slot)."""
    ctx = con_or_ctx if isinstance(con_or_ctx, (Ctx, _ReadCtx)) else _ReadCtx(con_or_ctx)
    picks = picks or {}
    rows = ctx.con.execute("SELECT person_id, stage, doc FROM max_person WHERE lifecycle='active'").fetchall()
    persons = {}
    for r in rows:
        if approved_only and r["stage"] != "approved":
            continue
        doc = json.loads(r["doc"])
        if shareable and private_now(ctx, "person", doc):
            continue
        name, conf = person_display(ctx, r["person_id"], shareable=shareable)
        if name is None:
            continue                # every name claim is private: the person is withheld (9.4)
        b, d, has_death = person_dates(ctx, r["person_id"], shareable=shareable)
        persons[r["person_id"]] = {"id": r["person_id"], "name": name, "c": conf, "b": b, "d": d,
                                   "has_death": has_death, "living": person_living_now(ctx, r["person_id"]),
                                   "license": doc.get("redistribution_license"), "doc": doc,
                                   "f": None, "m": None, "fk": None, "mk": None, "fc": None, "mc": None,
                                   "fr": None, "mr": None, "extra": [], "waiting": False, "u": False,
                                   "sets": 0, "note": None}
    shown = set(persons)
    for pid, p in persons.items():
        sets = parent_sets(ctx, pid, approved_only=approved_only)
        p["sets"] = len(sets)
        if not shareable and approved_only:
            waiting = [s for s in parent_sets(ctx, pid, approved_only=False, include_waiting=True)
                       if not any(x["membership_id"] == s["membership_id"] for x in sets)]
            p["waiting"] = bool(waiting)
        if shareable:
            sets = [s for s in sets if s["set_kind"] != "alternative_hypothesis"]
            chosen, reason = choose_set(sets, "preferred")
            parents = shareable_parents(ctx, chosen, shown) if chosen else []
        else:
            chosen, reason = choose_set(sets, mode, picks.get(pid))
            parents = [x for x in (chosen or {}).get("parents", []) if x["person_id"] in shown]
            if reason in ("Kind of parents not stated", "Birth parents not recorded", POSSIBLE_NOTE):
                p["note"] = reason
        place_parents(ctx, p, parents, persons)
    return persons


def place_parents(ctx, p, parents, persons):
    """Slots: father first (2n), mother second (2n+1); parent/unknown by gender, else the first
    empty slot; a third parent is listed beside the chart."""
    unknown = []
    for x in parents:
        role = x["role"]
        if role == "father" and p["f"] is None:
            p["f"], p["fk"], p["fc"], p["fr"] = x["person_id"], x["line_style"], x["link_confidence"], role
        elif role == "mother" and p["m"] is None:
            p["m"], p["mk"], p["mc"], p["mr"] = x["person_id"], x["line_style"], x["link_confidence"], role
        else:
            unknown.append(x)
    for x in unknown:
        g = _gender_role(persons[x["person_id"]]["doc"]) if x["role"] not in ("father", "mother") else None
        slot = "f" if (g == "father" and p["f"] is None) else "m" if (g == "mother" and p["m"] is None) else \
            ("f" if p["f"] is None else "m" if p["m"] is None else None)
        if slot is None:
            p["extra"].append((x["person_id"], x["role"]))
            continue
        p[slot], p[slot + "k"], p[slot + "c"], p[slot + "r"] = x["person_id"], x["line_style"], x["link_confidence"], x["role"]
        if g is None and x["role"] not in ("father", "mother"):
            persons[x["person_id"]]["u"] = True


def load_tree(con):
    """The 1.x name for the chart view: approved persons, preferred parent sets."""
    return chart_persons(con, "preferred")


def all_root_sets(con_or_ctx, root):
    """For the `all` view: one (set, title) per eligible set of the root."""
    ctx = con_or_ctx if isinstance(con_or_ctx, (Ctx, _ReadCtx)) else _ReadCtx(con_or_ctx)
    name, _c = person_display(ctx, root)
    out = []
    for s in parent_sets(ctx, root):
        word = {"birth": "birth parents", "adoptive": "adoptive parents", "step_household": "step-family household",
                "foster": "foster parents", "guardianship": "guardians", "sealing": "sealing parents",
                "alternative_hypothesis": "possible parents", "other": "parents (kind not stated)"}[s["set_kind"]]
        out.append((s, "Through %s's %s" % (name, word)))
    return out


# ------------------------------------------------------------------ reading in bulk (kit 1.2.0; design 02 4.8)
#
# K-9 `checked_view` and K-8 `TreeSnapshot`. A program that shows the whole tree at once (Probable
# Pedigree's speed cache, design 02 7) reads it with TreeSnapshot: one read transaction that streams each
# table once and keeps compact facts only (names, dates, the living rule's inputs, parent sets), never
# whole documents. Every answer it gives equals the per-document readers' answer on the same snapshot:
# living == person_living_now, parent_sets == parent_sets (as compact_parent_sets shows them), display ==
# person_display and person_dates, checked_claims == checked_view, chart == chart_persons' main parents.
# The kit's tests compare them on random trees; the per-document readers stay the truth.

def checked_view(con_or_ctx, person_id):
    """K-9 (design 02 4.8, A-11): the claim ids of a person that Quality Control has passed, as a frozenset,
    or None when the person is not checked. An approved person: every live claim. A person whose latest
    review passed and who was sent back to "Waiting for a check" by new claims (R-27): the live claims that
    review passed, so the person keeps them (the rule the kit already applies to parent links, 9.2). A person
    never checked, whose latest review failed, turned down, withdrawn, or with none of the passed claims
    still live: None."""
    ctx = con_or_ctx if isinstance(con_or_ctx, (Ctx, _ReadCtx)) else _ReadCtx(con_or_ctx)
    pid = ctx.resolve(person_id)
    row = ctx.row("person", pid)
    if not row or row["lifecycle"] != "active" or row["stage"] not in ("approved", "staged"):
        return None
    live = [low(c["assertion_id"]) for _ln, c, _h in effective_claims(ctx, "person", pid) if c.get("assertion_id")]
    if row["stage"] == "approved":
        return frozenset(live)
    passed = approved_claim_ids(ctx, pid)
    kept = frozenset(x for x in live if x in passed)
    return kept or None


def compact_parent_sets(sets) -> list:
    """parent_sets' answer with each fact named by its id (the shape TreeSnapshot.parent_sets gives)."""
    out = []
    for s in sets:
        c = {k: v for k, v in s.items() if k != "parents"}
        c["parents"] = []
        for p in s["parents"]:
            q = {k: v for k, v in p.items() if k not in ("facts", "checked_facts")}
            q["fact_ids"] = [low(f.get("assertion_id")) for f in p["facts"]]
            q["checked_fact_ids"] = [low(f.get("assertion_id")) for f in p["checked_facts"]]
            c["parents"].append(q)
        out.append(c)
    return out


def main_parent_slots(parents, gender_role_of) -> tuple:
    """(father slot, mother slot) for a chosen set's parents on the chart (each {person_id, role}), filled as
    place_parents fills them: a father and a mother by their role, then anyone else by their gender
    (gender_role_of(person_id): "father", "mother" or None), else the first empty slot."""
    f = m = None
    unknown = []
    for x in parents:
        if x["role"] == "father" and f is None:
            f = x["person_id"]
        elif x["role"] == "mother" and m is None:
            m = x["person_id"]
        else:
            unknown.append(x)
    for x in unknown:
        g = gender_role_of(x["person_id"]) if x["role"] not in ("father", "mother") else None
        if g == "father" and f is None:
            f = x["person_id"]
        elif g == "mother" and m is None:
            m = x["person_id"]
        elif f is None:
            f = x["person_id"]
        elif m is None:
            m = x["person_id"]
    return f, m


_DEATH_EVENTS = ("death", "burial", "cremation")


def _snap_date(c):
    d = c.get("date")
    return d if isinstance(d, dict) else None


class TreeSnapshot:
    """K-8 (design 02 4.8, A-10): the bulk reader. ``TreeSnapshot.build(con)`` reads the tree inside one
    read transaction of its own (``con`` is a read-only connection with no transaction open, or one whose
    transaction the caller holds) and keeps only compact facts: per person the best name, the dates, the
    living rule's inputs, the gender and the stage; per family link its kind, persons, stage and live
    lineage facts (id, kind, confidence); per record its class. It never holds a whole document, and it
    is released after use.

    ``today`` fixes the day the living answers are worked out for (default: today(), which MAXGEN_TODAY
    fixes for tests). ``progress(fraction, code)`` hears how far the build has got.

    ``people`` (a list of person ids) reads only what the answers about those people need: their claims,
    their family links and memberships, the records and proofs those cite, their children's births and
    their parents' stage and gender. Its answers about those people equal a whole snapshot's (a program
    keeps its cache current with it after each change); about anyone else it knows nothing."""

    LIVE_SQL = "('proposed','proven','disputed')"

    def __init__(self, today_=None):
        self.today = today_ or today()
        self.year = self.today.year
        self.settings = date_settings()
        self.seq = 0
        self._to: dict = {}            # live redirects: old id -> new id
        self._from: dict = {}          # new id -> [old ids]
        self._good_records: set = set()   # record ids whose class is original or derivative
        self._conclusion_ok: dict = {}
        self._passed: dict = {}        # target id -> frozenset of the claim ids its latest review passed
        self._person: dict = {}        # person id -> [stage, lifecycle, floor, gender role, fallback name, extra]
        self._facts: dict = {}         # effective person id -> its effective claims, worked out
        self._rel: dict = {}           # relationship id -> its compact facts
        self._links: dict = {}         # person id -> [(relationship id, role)] for 'partner' and 'parent'
        self._members: dict = {}       # child id -> active membership rows, in index order
        self._merged_links: dict = {}  # membership id -> [parent_link_ids of memberships merged into it]
        self._living: dict = {}

    # -- building
    @classmethod
    def build(cls, con, *, progress=None, today=None, people=None) -> "TreeSnapshot":
        snap = cls(today)
        own = not con.in_transaction
        if own:
            con.execute("BEGIN")
        try:
            snap._read(con, progress or (lambda fraction, code: None), people)
        finally:
            if own:
                con.execute("COMMIT")
        return snap

    MEMBER_COLS = ("membership_id, family_id, child_id, set_kind, is_preferred, preferred_by_task_id, status, "
                   "merged_into_membership_id, parent_link_ids, created_at")

    @staticmethod
    def _chunks(ids, size=400):
        ids = list(ids)
        for i in range(0, len(ids), size):
            part = ids[i:i + size]
            yield part, ",".join("?" * len(part))

    def _rows(self, con, sql, ids, *before):
        """SELECT ... WHERE x IN (ids), in chunks; ``sql`` has one %s for the placeholders."""
        out = []
        for part, marks in self._chunks(ids):
            out += con.execute(sql % marks, list(before) + part).fetchall()
        return out

    def _read(self, con, progress, people=None):
        q = con.execute
        self.settings = date_settings(con)
        self.seq = change_seq(con)
        progress(0.0, "start")
        for old, new in q("SELECT old_id, new_id FROM id_redirect WHERE reverted_at IS NULL"):
            self._to[old] = new
            self._from.setdefault(new, []).append(old)
        scope = None if people is None else self._scope(con, people)
        if scope is None:
            total = int(q("SELECT count(*) FROM max_person").fetchone()[0] or 0)
            self._good_records = {r[0] for r in q("SELECT record_id, source_class FROM max_record")
                                  if r[1] in ("original", "derivative")}
            tasks = [r[0] for r in q("SELECT DISTINCT conclusion_task_id FROM claim_index "
                                     "WHERE conclusion_task_id IS NOT NULL")]
        else:
            total = len(scope["people"])
            cited = self._rows(con, "SELECT DISTINCT source_record_id, conclusion_task_id FROM claim_index "
                                    "WHERE holder_id IN (%s)", scope["holders"] | scope["relationships"])
            records = {self.resolve(r[0]) for r in cited if r[0]}
            self._good_records = {r[0] for r in self._rows(con, "SELECT record_id, source_class FROM max_record "
                                                                "WHERE record_id IN (%s)", records)
                                  if r[1] in ("original", "derivative")}
            tasks = sorted({r[1] for r in cited if r[1]})
        rc = _ReadCtx(con)
        for tid in tasks:
            task = rc.doc("task", tid)
            self._conclusion_ok[tid] = bool(task and has_proof(task)
                                            and (task["result"]["proof"].get("origin") or "maxgen") == "maxgen"
                                            and accepted_now(rc, task["task_id"]))
        latest = ("SELECT r.target_id, r.verdict, r.approved_ids FROM review r JOIN (SELECT target_id, max(id) AS id "
                  "FROM review %s GROUP BY target_id) x ON x.id = r.id")
        try:
            rows = q(latest % "").fetchall() if scope is None else self._rows(
                con, latest % "WHERE target_id IN (%s)", scope["people"] | scope["relationships"])
        except sqlite3.OperationalError:            # a tree made before approved_ids existed
            rows = []
        for target, verdict, approved in rows:
            if verdict == "pass" and approved:
                self._passed[target] = frozenset(json.loads(approved))
        progress(0.05, "relationships")
        rel_sql = "SELECT relationship_id, kind, stage, lifecycle, is_living_flag, doc FROM max_relationship"
        self._read_relationships(q(rel_sql) if scope is None else
                                 self._rows(con, rel_sql + " WHERE relationship_id IN (%s)", scope["relationships"]))
        link_sql = "SELECT DISTINCT person_id, relationship_id, role FROM link_index WHERE role IN ('partner','parent')"
        for pid, rid, role in (q(link_sql) if scope is None else
                               self._rows(con, link_sql + " AND person_id IN (%s)", scope["people"])):
            self._links.setdefault(pid, []).append((rid, role))
        for r in (q("SELECT %s FROM membership_index ORDER BY rowid" % self.MEMBER_COLS) if scope is None
                  else scope["memberships"]):
            if r[7]:
                self._merged_links.setdefault(r[7], []).append(json.loads(r[8]))
            if r[6] == "active":
                self._members.setdefault(r[2], []).append(r)
        progress(0.2, "people")
        self._read_people(con, total, progress, None if scope is None else scope["holders"] | scope["people"])
        progress(1.0, "done")

    def _scope(self, con, people) -> dict:
        """What the answers about ``people`` need (see build): the people to read, the holders of their
        claims, the family links and the membership rows."""
        mine = {self.resolve(x) for x in people if x}
        links = self._rows(con, "SELECT DISTINCT person_id, relationship_id, role FROM link_index "
                                "WHERE person_id IN (%s)", mine)
        members = self._rows(con, "SELECT rowid, %s FROM membership_index WHERE child_id IN (%%s)" % self.MEMBER_COLS,
                             mine)
        merged = self._rows(con, "SELECT rowid, %s FROM membership_index WHERE merged_into_membership_id IN (%%s)"
                            % self.MEMBER_COLS, {r[1] for r in members})
        rows = sorted({tuple(r) for r in members + merged}, key=lambda r: r[0])     # the index's own order
        rels = {r[1] for r in links}
        for r in rows:
            rels |= self.merged_into(r[2]) | {self.resolve(r[2])}
            rels |= {self.resolve(x) for x in json.loads(r[9])}
        everyone = set(mine)
        for rid, kind, parent, child in self._rows(con, "SELECT relationship_id, kind, parent_id, child_id "
                                                        "FROM max_relationship WHERE relationship_id IN (%s)", rels):
            if kind == "parent_child":
                everyone |= {self.resolve(parent), self.resolve(child)}
        holders = {r[0] for r in self._rows(con, "SELECT DISTINCT holder_id FROM claim_index WHERE holder_table="
                                                 "'max_person' AND effective_holder_id IN (%s)", everyone)}
        return {"people": everyone, "holders": holders | everyone, "relationships": rels,
                "memberships": [r[1:] for r in rows]}

    def _read_relationships(self, rows):
        for rid, kind, stage, life, flag, text in rows:
            doc = json.loads(text)
            if kind == "parent_child":
                facts = live_facts(doc)
                bears = any((f.get("fact_type") in ("birth_parent", "gestational_carrier")
                             or f.get("dimension") in ("genetic", "gestational"))
                            and self.reliable(f.get("evidence"), "lineage_assertions") and not inferred_only(f)
                            for f in facts)
                self._rel[rid] = {"kind": kind, "stage": stage, "life": life, "parent": doc.get("parent_id"),
                                  "child": doc.get("child_id"), "role": doc.get("parent_role", "unknown"),
                                  "facts": tuple((low(f.get("assertion_id")), f.get("fact_type"),
                                                  float(f.get("confidence") or 0)) for f in facts), "bears": bears}
            elif kind == "family":
                lowest = None
                for c in doc.get("fact_assertions") or []:
                    if live_claim(c) and c.get("fact_type") in MARRIAGE_FACTS and self.reliable(c.get("evidence")) \
                            and not inferred_only(c):
                        lo, hi = year_span(c.get("date"), "marriage", self.settings)
                        if lo is not None and hi is not None:
                            lowest = hi if lowest is None else min(lowest, hi)
                members: dict = {}
                for m in doc.get("children") or []:          # the first copy of a membership, as find_membership
                    members.setdefault(low(m.get("membership_id")), (m.get("set_kind_as_written"),
                                                                     bool(m.get("is_private")),
                                                                     bool(m.get("is_living_flag"))))
                self._rel[rid] = {"kind": kind, "stage": stage, "life": life, "marriage_hi": lowest,
                                  "members": members, "partners": tuple(p.get("person_id") for p in active_partners(doc)),
                                  "flag": bool(flag), "n_children": sum(1 for m in doc.get("children") or []
                                                                         if m.get("status", "active") == "active")}
            else:
                self._rel[rid] = {"kind": kind, "stage": stage, "life": life}

    def _read_people(self, con, total, progress, only=None):
        """The people and their claims, as two streams in step (both in person-id order): each person's
        index rows say which of its claims are live and in which order the readers meet them."""
        q = con.execute
        shared_sql = ("SELECT DISTINCT effective_holder_id FROM claim_index WHERE holder_table='max_person' "
                      "AND holder_id <> effective_holder_id")
        index_sql = ("SELECT holder_id, rowid, assertion_id, list_name, effective_holder_id FROM claim_index "
                     "WHERE holder_table='max_person' AND status IN %s" % self.LIVE_SQL)
        person_sql = "SELECT person_id, stage, lifecycle, is_living, person_kind, updated_at, doc_sha256, doc FROM max_person"
        if only is None:
            shared = {r[0] for r in q(shared_sql)}
            index = iter(q(index_sql + " ORDER BY holder_id, rowid"))
            persons = q(person_sql + " ORDER BY person_id")
        else:
            shared = {r[0] for r in self._rows(con, shared_sql + " AND effective_holder_id IN (%s)", only)}
            index = iter(sorted(self._rows(con, index_sql + " AND holder_id IN (%s)", only), key=lambda r: (r[0], r[1])))
            persons = sorted(self._rows(con, person_sql + " WHERE person_id IN (%s)", only), key=lambda r: r[0])
        waiting: dict = {}
        nxt = next(index, None)
        done = 0
        for pid, stage, life, floor, kind, updated, sha, text in persons:
            while nxt is not None and nxt[0] < pid:          # index rows of no stored document: never read
                nxt = next(index, None)
            mine = []
            while nxt is not None and nxt[0] == pid:
                mine.append(nxt)
                nxt = next(index, None)
            doc = json.loads(text)
            self._person[pid] = [stage, life, bool(doc.get("is_living")), _gender_role(doc), best_name(doc),
                                 {"kind": kind, "updated_at": updated, "sha256": sha,
                                  "sex": (_best(doc.get("gender_assertions")) or {}).get("gender_as_written")}]
            groups: dict = {}
            for _h, rowid, cid, list_name, eff in mine:
                for c in doc.get(list_name) or []:
                    if isinstance(c, dict) and low(c.get("assertion_id") or c.get("match_id")) == cid:
                        groups.setdefault(eff, []).append((rowid, list_name, self._compact(list_name, c)))
            for eff, items in groups.items():
                if eff in shared:
                    waiting.setdefault(eff, []).extend(items)
                else:
                    self._facts[eff] = self._work_out(items)
            done += 1
            if done % 2000 == 0 and total:
                progress(0.2 + 0.75 * done / total, "people")
        for eff, items in waiting.items():
            items.sort(key=lambda x: x[0])
            self._facts[eff] = self._work_out(items)

    def _compact(self, list_name, c):
        """What the readers use of one live claim (the claim itself is not kept)."""
        date = _snap_date(c)
        out = {"id": low(c.get("assertion_id")) if c.get("assertion_id") else None,
               "conf": float(c.get("confidence") or 0)}
        if list_name == "name_assertions":
            if c.get("name_status", "known") == "known" and c.get("name_as_written"):
                out["name"] = (c["name_as_written"], bool(c.get("is_preferred")), c.get("given_name"), c.get("surname"),
                               c.get("nickname"), c.get("name_type"))
            return out
        if list_name == "birth_assertions" and date is not None:
            out["year"] = date_year(date)
            out["date"] = date
            out["place"] = (c.get("place") or {}).get("place_as_written") if isinstance(c.get("place"), dict) else None
        if list_name == "death_assertions":
            out["death"] = True
            if date is not None:
                out["year"] = date_year(date)
                out["date"] = date
                out["place"] = (c.get("place") or {}).get("place_as_written") if isinstance(c.get("place"), dict) else None
        if list_name == "death_assertions" or (list_name == "event_assertions"
                                               and c.get("event_type") in _DEATH_EVENTS
                                               and c.get("role") in (None, "principal", "deceased")):
            out["establishes"] = self._establishes_death(list_name, c)
        cls = _birth_like(list_name, c)
        if cls and self.reliable(c.get("evidence"), list_name) and not inferred_only(c):
            lo, hi = year_span(c.get("date"), cls, self.settings)
            if lo is not None and hi is not None:
                out["birth_hi"] = hi
        return out

    def _establishes_death(self, list_name, c) -> bool:      # == establishes_death(ctx, ln, c)
        ev = c.get("evidence") or {}
        if not self.reliable(ev, list_name) or (c.get("date") or {}).get("modifier") == "text_only":
            return False
        if c.get("status") == "proven":
            return True
        if inferred_only(c):
            return False
        return ev.get("basis") != "document" or ev.get("kind") == "direct" or ev.get("about") == "other_mention"

    @staticmethod
    def _work_out(items):
        """One person's effective claims, in the readers' order, to the answers the readers give."""
        f = {"names": [], "b": None, "bconf": -1.0, "d": None, "dconf": -1.0, "has_death": False, "dead": False,
             "latest_birth": None, "ids": [], "birth": None, "death": None}
        for _rowid, list_name, c in items:
            if c["id"]:
                f["ids"].append(c["id"])
            if "name" in c:
                f["names"].append((c["name"], c["conf"]))
            if list_name == "birth_assertions" and c.get("year") is not None and c["conf"] > f["bconf"]:
                f["b"], f["bconf"], f["birth"] = c["year"], c["conf"], c
            if list_name == "death_assertions":
                f["has_death"] = True
                if c.get("year") is not None and c["conf"] > f["dconf"]:
                    f["d"], f["dconf"], f["death"] = c["year"], c["conf"], c
            if c.get("establishes"):
                f["dead"] = True
            if c.get("birth_hi") is not None:
                f["latest_birth"] = c["birth_hi"] if f["latest_birth"] is None else max(f["latest_birth"], c["birth_hi"])
        f["ids"] = tuple(f["ids"])
        return f

    # -- the same small calls the per-document readers make
    def resolve(self, ident):
        if ident is None:
            return None
        cur, seen = low(ident), set()
        while cur in self._to:
            if cur in seen:
                refuse("the merge redirects for %s form a loop" % ident)
            seen.add(cur)
            cur = self._to[cur]
        return cur

    def merged_into(self, ident):
        target = low(ident)
        if target in self._to:
            return {target}
        out, todo = {target}, [target]
        while todo:
            for old in self._from.get(todo.pop(), ()):
                if old not in out:
                    out.add(old)
                    todo.append(old)
        return out

    def reliable(self, ev, list_name=None) -> bool:
        """== reliable(ctx, ev, list_name) on the same tree."""
        basis = (ev or {}).get("basis")
        if basis == "document":
            return self.resolve(ev.get("source_record_id")) in self._good_records
        if basis == "conclusion":
            return self._conclusion_ok.get(low(ev.get("conclusion_task_id")), False)
        if basis == "dna":
            return list_name == "lineage_assertions"
        return False

    def _ok(self, pid, approved_only) -> bool:      # == _person_ok
        p = self._person.get(pid)
        return bool(p) and p[1] == "active" and (p[0] == "approved" or not approved_only)

    # -- the answers
    def living(self, person_id) -> bool:
        """== person_living_now(ctx, person_id) for the snapshot's day."""
        pid = self.resolve(person_id)
        if pid in self._living:
            return self._living[pid]
        p = self._person.get(pid)
        if p is None:
            return True
        out = (not self._dead(pid)) or p[2]
        self._living[pid] = out
        return out

    def _dead(self, pid) -> bool:                    # == dead_by_rules
        f = self._facts.get(pid)
        if f and f["dead"]:
            return True
        lb = f["latest_birth"] if f else None
        if lb is not None and lb <= self.year - self.settings["living_years_birth"]:
            return True
        family_years = self.year - self.settings["living_years_family"]
        links = self._links.get(pid, ())
        for rid, role in links:
            rel = self._rel.get(rid)
            if role == "partner" and rel and rel["life"] == "active" and rel.get("marriage_hi") is not None \
                    and rel["marriage_hi"] <= family_years:
                return True
        for rid, role in links:
            rel = self._rel.get(rid)
            if role != "parent" or not rel or rel["life"] != "active" or rel["kind"] != "parent_child" \
                    or rel["stage"] not in ("approved", "staged") or not rel["bears"]:
                continue
            cf = self._facts.get(self.resolve(rel["child"]))
            b = cf["latest_birth"] if cf else None
            if b is not None and b <= family_years:
                return True
        return False

    def display(self, person_id) -> tuple:
        """(name, confidence, birth year, death year, has death) == person_display and person_dates."""
        pid = low(person_id)
        f = self._facts.get(self.resolve(pid))
        own = self._person.get(pid)
        names = f["names"] if f else []
        if names:
            pref = [n for n in names if n[0][1]]
            best = max(pref or names, key=lambda n: n[1])
            name, conf = best[0][0], best[1]
        else:
            name, conf = (own[4] if own else "(no name)"), None
        if not f:
            return name, conf, None, None, False
        return name, conf, f["b"], f["d"], f["has_death"]

    def checked_claims(self, person_id):
        """== checked_view(ctx, person_id)."""
        pid = self.resolve(person_id)
        p = self._person.get(pid)
        if not p or p[1] != "active" or p[0] not in ("approved", "staged"):
            return None
        f = self._facts.get(pid)
        live = f["ids"] if f else ()
        if p[0] == "approved":
            return frozenset(live)
        passed = self._passed.get(pid, frozenset())
        kept = frozenset(x for x in live if x in passed)
        return kept or None

    def parent_sets(self, person_id, *, approved_only=True, include_waiting=False) -> list:
        """== compact_parent_sets(parent_sets(ctx, person_id, ...))."""
        pid = self.resolve(person_id)
        if approved_only and not self._ok(pid, True) and not include_waiting:
            return []
        out = []
        for m in self._members.get(pid, ()):
            mid, fam_id, _child, set_kind, preferred, by_task, _st, _into, link_text, created = m
            fam = self._rel.get(fam_id)
            if not fam or fam["life"] != "active":
                continue
            holder = None
            for fid in sorted(self.merged_into(fam_id), key=lambda x: (x == low(fam_id), x)):
                d = self._rel.get(fid)
                if d and d.get("members") and low(mid) in d["members"]:
                    holder = d["members"][low(mid)]
            links = list(json.loads(link_text))
            for more in self._merged_links.get(mid, ()):
                for x in more:
                    if x not in links:
                        links.append(x)
            parents, eligible = [], False
            for lid in [self.resolve(x) for x in links]:
                rel = self._rel.get(lid)
                if not rel or rel["life"] != "active":
                    continue
                facts = rel.get("facts", ())
                parent = self.resolve(rel.get("parent"))
                if not approved_only or rel["stage"] == "approved":
                    checked = list(range(len(facts)))
                elif rel["stage"] == "staged":
                    passed = self._passed.get(lid, frozenset())
                    checked = [i for i, f in enumerate(facts) if f[0] in passed]
                else:
                    checked = []
                ok = bool(checked) and self._ok(parent, approved_only)
                if not ok and not include_waiting:
                    continue
                use = [facts[i] for i in checked] if ok else list(facts)
                parents.append({"person_id": parent, "role": rel.get("role", "unknown"), "link_id": lid,
                                "line_style": link_style([{"fact_type": f[1]} for f in use], set_kind),
                                "link_confidence": max([f[2] for f in use] or [0.0]),
                                "waiting_fact_ids": [facts[i][0] for i in range(len(facts)) if i not in checked],
                                "eligible": ok, "link_stage": rel["stage"],
                                "fact_ids": [f[0] for f in facts], "checked_fact_ids": [facts[i][0] for i in checked]})
                eligible = eligible or ok
            if not eligible and not include_waiting:
                continue
            out.append({"membership_id": mid, "family_id": fam_id, "set_kind": set_kind,
                        "set_kind_as_written": (holder or (None, False, False))[0],
                        "is_preferred": bool(preferred), "preferred_by_task_id": by_task, "created_at": created,
                        "eligible": eligible, "parents": parents,
                        "is_private": bool((holder or (None, False, False))[1]),
                        "is_living_flag": bool((holder or (None, False, False))[2])})
        return out

    def choose_set(self, person_id, mode="preferred", pick=None):
        """== choose_set(parent_sets(ctx, person_id), mode, pick)."""
        return choose_set(self.parent_sets(person_id), mode, pick)

    def chart(self, mode="preferred", picks=None) -> dict:
        """{person_id: (father slot, mother slot)} for every person on the chart: the same people and the
        same two main-parent slots as chart_persons(con, mode, picks) (checked people, not to share)."""
        picks = picks or {}
        return {pid: self.chart_parents(pid, mode, picks.get(pid)) for pid, p in self._person.items()
                if p[1] == "active" and p[0] == "approved"}

    def chart_parents(self, person_id, mode="preferred", pick=None):
        """One person's (father slot, mother slot) on the chart, as chart(); None when the person is not on
        it (not checked, or not active)."""
        pid = low(person_id)
        if not self._ok(pid, True):
            return None
        chosen, _reason = choose_set(self.parent_sets(pid, approved_only=True), mode, pick)
        shown = [x for x in (chosen or {}).get("parents", []) if self._ok(x["person_id"], True)]
        return main_parent_slots(shown, lambda q: self._person[q][3])

    # -- what else a cache may show about a person (not part of the readers' answers)
    def people(self):
        """Every stored person id, as the snapshot read them."""
        return list(self._person)

    def person_info(self, person_id) -> dict | None:
        """The compact facts of one stored person: stage, lifecycle, the stored living floor, the gender as
        written, the person's kind, when it last changed, its fingerprint, its live names (each (name,
        preferred, given, surname, nickname, name type), confidence), its best birth and death claims
        (date block, year, place as written, confidence) and how many live claims it has."""
        p = self._person.get(low(person_id))
        if p is None:
            return None
        f = self._facts.get(self.resolve(person_id)) or {}
        return {"stage": p[0], "lifecycle": p[1], "floor": p[2], "gender_role": p[3], **p[5],
                "names": list(f.get("names", [])), "birth": f.get("birth"), "death": f.get("death"),
                "has_death": bool(f.get("has_death")), "claims": len(f.get("ids", ()))}

    def cited_tasks(self) -> set:
        """The research tasks the snapshot's claims rest on as proofs (whether or not they count now)."""
        return set(self._conclusion_ok)

    def families(self) -> list:
        """Every family link the snapshot read: (family id, stage, lifecycle, partner ids, children)."""
        return [(rid, r["stage"], r["life"], tuple(self.resolve(x) for x in r["partners"] if x), r["n_children"])
                for rid, r in self._rel.items() if r["kind"] == "family"]

    def partner_families(self, person_id) -> list:
        """The active families the person is a partner in, each (family id, stage, partner ids)."""
        out = []
        for rid, role in self._links.get(self.resolve(person_id), ()):
            rel = self._rel.get(rid)
            if role == "partner" and rel and rel["life"] == "active" and rel["kind"] == "family":
                out.append((rid, rel["stage"], tuple(self.resolve(x) for x in rel["partners"] if x)))
        return out


def lineage_text(ctx, person_id, mode, gens, as_json):
    pid = ctx.resolve(person_id)
    if not ctx.row("person", pid):
        refuse("%s is not a person in this database" % person_id)
    sets = parent_sets(ctx, pid, approved_only=False, include_waiting=True)
    chosen, reason = choose_set([s for s in parent_sets(ctx, pid, approved_only=False)], mode)
    data = {"person_id": pid, "mode": mode, "reason": reason,
            "chosen": None if chosen is None else {"membership_id": chosen["membership_id"],
                                                   "set_kind": chosen["set_kind"],
                                                   "parents": [x["person_id"] for x in chosen["parents"]],
                                                   "styles": [x["line_style"] for x in chosen["parents"]]},
            "sets": [{"membership_id": s["membership_id"], "family_id": s["family_id"], "set_kind": s["set_kind"],
                      "is_preferred": s["is_preferred"], "eligible": s["eligible"],
                      "parents": [{"person_id": x["person_id"], "role": x["role"], "line_style": x["line_style"],
                                   "link_id": x["link_id"]} for x in s["parents"]]} for s in sets]}
    if as_json:
        return json.dumps(data, indent=2)
    lines = ["LINEAGE  %s (%s)  view=%s" % (person_display(ctx, pid)[0], pid, mode)]
    for s in sets:
        lines.append("  %s %-24s %s  %s" % ("*" if s["is_preferred"] else " ", SET_WORDS.get(s["set_kind"], s["set_kind"]),
                                           s["membership_id"], ", ".join("%s (%s, %s)" % (person_display(ctx, x["person_id"])[0],
                                                                                           x["role"], x["line_style"])
                                                                          for x in s["parents"]) or "(no parents)"))
    if chosen:
        lines.append("  chosen: %s -> %s" % (chosen["membership_id"], ", ".join(x["person_id"] for x in chosen["parents"])))
    else:
        lines.append("  chosen: none (%s)" % reason)
    return "\n".join(lines)


def cmd_lineage(ctx, a):
    mode = a.mode if a.mode in ("preferred", "birth", "adoptive", "all") else "preferred"
    if mode == "all":
        pid = ctx.resolve(a.person)
        outs = []
        for s, title in all_root_sets(ctx, pid):
            outs.append({"title": title, "membership_id": s["membership_id"], "set_kind": s["set_kind"],
                         "parents": [x["person_id"] for x in s["parents"]]})
        print(json.dumps({"person_id": pid, "mode": "all", "charts": outs}, indent=2) if a.json else
              "\n".join("%s: %s" % (o["title"], ", ".join(o["parents"])) for o in outs) or "(no parent sets)")
        return
    print(lineage_text(ctx, a.person, mode, a.gens, a.json))


# ------------------------------------------------------------------ preference, review, tasks, steps

def cmd_prefer(ctx, a):
    m = ctx.con.execute("SELECT * FROM membership_index WHERE membership_id=?", (low(a.membership_id),)).fetchone()
    if not m or m["status"] != "active":
        refuse("%s is not a live membership" % a.membership_id, "R-04")
    task = require_task(ctx, a.task, annotation=True, purpose="recording the main parents")
    if m["set_kind"] == "adoptive" and not person_accepted(ctx, task["task_id"]):
        refuse("choosing an adoptive family as the main parents needs the person's yes on task %s" % a.task, "R-31")
    prefer_membership(ctx, m["membership_id"], task["task_id"])
    closed = close_main_parents_reviews(ctx, m["child_id"], task["task_id"])
    ctx.log("prefer", m["membership_id"])
    ctx.commit()
    if closed:
        print("OK  closed %d pending 'main parents changed' review(s): %s" % (len(closed), ", ".join(closed)))
    print("OK  membership %s is now the main parent set of %s" % (m["membership_id"], m["child_id"]))


def close_main_parents_reviews(ctx, child_id, task_id):
    """A main-parents choice answers the helper's pending 'main parents changed' reviews of that child (9.1)."""
    child = ctx.resolve(child_id)
    closed = []
    for r in ctx.con.execute("SELECT doc FROM max_task WHERE task_type='judge_review' AND status='pending'").fetchall():
        t = json.loads(r["doc"])
        p = t.get("payload") or {}
        if p.get("kind") != "main_parents_changed" or ctx.resolve(p.get("person_id")) != child:
            continue
        ts = now()
        t["status"] = "done"
        t["completed_at"] = ts
        t.setdefault("provenance", dict(ctx.require_session()["provenance"], claimed_at=ts))
        t.setdefault("result", {})["verdict"] = "infra"
        t["result"]["verdict_reason"] = "The main parents were chosen under task %s." % low(task_id)
        ctx.store("task", t, content=True)
        closed.append(t["task_id"])
    return closed


def prefer_membership(ctx, membership_id, task_id):
    m = dict(ctx.con.execute("SELECT * FROM membership_index WHERE membership_id=?", (low(membership_id),)).fetchone())
    for other in memberships_of(ctx, m["child_id"]):
        for fid in ctx.merged_into(other["family_id"]):
            fam = ctx.doc("relationship", fid)
            if not fam or not find_membership(fam, other["membership_id"]):
                continue
            path = ["children", {"k": "membership_id", "v": other["membership_id"]}]
            mine = other["membership_id"] == m["membership_id"]
            ch = ctx.annotate("relationship", fam, path + ["preferred_by_task_id"], low(task_id) if mine else None, task_id)
            ch |= ctx.annotate("relationship", fam, path + ["is_preferred"], mine, task_id)
            if ch:
                ctx.store("relationship", fam, content=False)
    recompute_preferred(ctx, m["child_id"], task_id, reason="link")


def cmd_review(ctx, a):
    ctx.require_session()
    kind = ctx.kind_of_id(a.id)
    if kind not in STAGED_KINDS:
        refuse("%s is not a record, person, relationship, place, group or DNA test in this database" % a.id)
    row = ctx.row(kind, a.id)
    doc = json.loads(row["doc"])
    if a.verdict == "pass":
        problems = approval_problems(ctx, kind, doc)
        if problems:
            refuse("cannot approve this %s yet: %s" % (TITLE[kind], "; ".join(problems)), "R-26")
    stage = "approved" if a.verdict == "pass" else "rejected"
    ctx.con.execute("UPDATE %s SET stage=? WHERE %s=?" % (TABLE[kind], KEY[kind]), (stage, low(a.id)))
    # the claims this pass approved, so a later fact that re-stages the document never hides them (9.2)
    passed = sorted(low(c["assertion_id"]) for _ln, c in iter_claims(kind, doc)
                    if live_claim(c) and c.get("assertion_id")) if a.verdict == "pass" else None
    ctx.con.execute("INSERT INTO review(target_id,table_name,verdict,by,note,at,approved_ids) VALUES (?,?,?,?,?,?,?)",
                    (low(a.id), TABLE[kind], a.verdict, a.by, a.note, now(),
                     json.dumps(passed) if passed is not None else None))
    ctx.commit()
    print("OK  %s %s -> %s (by %s)" % (TABLE[kind], low(a.id), stage, a.by))


def approved_claim_ids(ctx, ident) -> set:
    """The claim ids Quality Control's latest review of a document passed; empty when the latest review
    is not a pass (a rejection after a pass withdraws the trust) or there is none."""
    try:
        r = ctx.con.execute("SELECT verdict, approved_ids FROM review WHERE target_id=? ORDER BY id DESC LIMIT 1",
                            (low(ident),)).fetchone()
    except sqlite3.OperationalError:
        return set()
    if not r or r["verdict"] != "pass" or not r["approved_ids"]:
        return set()
    return set(json.loads(r["approved_ids"]))


def _records_of_claims(ctx, kind, ident):
    out = set()
    for ln, c, _h in effective_claims(ctx, kind, ident):
        ev = c.get("evidence") or {}
        for key in ("source_record_id", "import_record_id"):
            if ev.get(key):
                out.add(ctx.resolve(ev[key]))
    return out


def approval_problems(ctx, kind, doc):
    """R-26: what must be approved first."""
    problems = []
    ident = doc[KEY[kind]]
    recs = _records_of_claims(ctx, kind, ident) if kind in ("person", "relationship", "place", "group", "dna") else set()
    if kind == "place":
        recs |= {ctx.resolve(x) for x in doc.get("cited_record_ids") or []}
    if kind == "group":
        recs |= {ctx.resolve(o["record_id"]) for o in doc.get("observed_in") or []}
    bad = sorted(r for r in recs if (ctx.row("record", r) or {"stage": "missing"})["stage"] != "approved")
    if bad:
        problems.append("cited record(s) not yet approved: %s (review the records first)" % bad)
    if kind == "relationship":
        persons = relationship_persons(ctx, doc)
        staged = sorted({p for p in persons if (ctx.row("person", p) or {"stage": "missing"})["stage"] != "approved"})
        if staged:
            problems.append("person(s) it names not yet approved: %s" % staged)
    return problems


def cmd_task(ctx, a):
    task = ctx.doc("task", a.id)
    if not task:
        refuse("no task %s" % a.id, "R-04")
    sess = ctx.require_session()
    ts = now()
    lines = []
    if a.answer:
        if not a.note:
            refuse("--answer needs --note with the person's own words")
        review_id = record_person_review(ctx, task["task_id"], a.answer, a.note, channel="relayed",
                                         operator=sess["provenance"]["agent_id"])
        lines.append("OK  answer recorded as judge_review task %s (%s, relayed by %s)"
                     % (review_id, a.answer, sess["provenance"]["agent_id"]))
        task = ctx.doc("task", a.id)
    if task["status"] != a.status:
        old = task["status"]
        task["status"] = a.status
        if a.status != "pending" and not task.get("provenance"):
            prov = copy.deepcopy(sess["provenance"])
            prov["claimed_at"] = ts
            task["provenance"] = prov
        if a.status == "processing" and not task.get("started_at"):
            task["started_at"] = ts
        if a.status in ("done", "failed", "escalated", "cancelled"):
            task["completed_at"] = ts
        lines.append("OK  task %s %s -> %s" % (task["task_id"], old, a.status))
    if a.verdict:
        task.setdefault("result", {})["verdict"] = a.verdict
    if a.note and not a.answer:
        if a.status == "escalated":
            task["escalation_reason"] = a.note
        elif a.status == "failed":
            task["error_message"] = a.note
        elif a.status == "done":
            task.setdefault("result", {})["verdict_reason"] = a.note
        else:
            refuse("--note goes with escalated, failed or done (or with --answer); %s has nowhere to keep one" % a.status)
    if not task.get("provenance") and task["status"] != "pending":
        task["provenance"] = dict(sess["provenance"], claimed_at=ts)
    ctx.store("task", task, content=True)
    if a.verdict or a.note:
        lines.append("OK  task %s%s%s" % (task["task_id"], (" verdict " + a.verdict) if a.verdict else "",
                                         " (note kept)" if a.note and not a.answer else ""))
    ctx.commit()
    print("\n".join(lines) if lines else "OK  task %s unchanged (%s)" % (task["task_id"], task["status"]))


def person_session_provenance(ctx, channel, operator=None):
    dbid = ctx.database_id()
    prov = {"agent_id": "db:%s/person" % dbid, "agent_kind": "human", "role": "person", "party": "db:%s" % dbid,
            "channel": channel, "display_name": "the person", "claimed_at": now()}
    if operator:
        prov["operator"] = operator
    return prov


def record_person_review(ctx, target_task_id, verdict, words, channel="relayed", operator=None):
    """The person's decision, as a new judge_review task under the person's own provenance. The
    command line can only write channel 'relayed'; 'person_page' is written only by the person
    page through record_person_page_review()."""
    target = ctx.doc("task", target_task_id)
    if not target:
        refuse("no task %s to review" % target_task_id, "R-04")
    if verdict not in ("accepted", "rework", "rejected"):
        refuse("the answer is accepted, rework or rejected")
    prov = person_session_provenance(ctx, channel, operator)
    doc = {"task_id": new_id(), "schema_version": MAXGEN_VERSION, "task_type": "judge_review", "status": "done",
           "priority": 5, "is_living_flag": bool(target.get("is_living_flag")),
           "payload": {"kind": "person_answer", "target_task_id": low(target_task_id)},
           "result": {"review": {"target_task_id": low(target_task_id), "verdict": verdict, "notes": words}},
           "created_by": prov["agent_id"], "provenance": prov, "completed_at": now()}
    ctx.store("task", doc, new=True)
    refresh_claims_citing_proof(ctx, target_task_id)
    return doc["task_id"]


def record_person_page_review(db, target_task_id, verdict, words, schemas=None):
    """For Probable Pedigree's 'Decisions waiting for you' only: the person pressed the button on
    their own page, so the review carries channel person_page (the only path to 'proven')."""
    if not words or not str(words).strip():
        raise Refused("the person's own words are required")
    ctx = Ctx(db, schemas, write=True)
    try:
        ensure_person_session(ctx, "person_page")
        rid = record_person_review(ctx, target_task_id, verdict, str(words).strip(), channel="person_page")
        ctx.log("person_page_review", rid)
        ctx.commit()
        return rid
    finally:
        ctx.close()


def ensure_person_session(ctx, channel="person_page"):
    """The person page's own human session (kind human, role person, name person)."""
    prov = person_session_provenance(ctx, channel)
    prov.pop("claimed_at", None)
    sid = "person-page-" + ctx.database_id()
    if not ctx.con.execute("SELECT 1 FROM agent_session WHERE session_id=?", (sid,)).fetchone():
        ctx.con.execute("INSERT INTO agent_session(session_id,provenance,started_at) VALUES (?,?,?)",
                        (sid, json.dumps(prov), now()))
    ctx.as_session = sid
    ctx.session = None
    ctx.require_session()
    return sid


def append_step(ctx, task, step):
    res = task.setdefault("result", {})
    steps = res.setdefault("steps", [])
    step["step_no"] = max([s["step_no"] for s in steps] or [0]) + 1
    step["at"] = now()
    steps.append(step)
    errs = ctx.schemas.errors("task", task)
    if errs:
        raise Refused("the step does not match the MaxTask schema (R-01)\n   - %s" % "\n   - ".join(errs))
    ctx.store("task", task, content=False)
    return step["step_no"]


def cmd_step(ctx, a):
    task = require_task(ctx, a.task, annotation=True, purpose="a search step")
    if a.verdict not in STEP_VERDICTS:
        refuse("--verdict must be one of %s" % ", ".join(STEP_VERDICTS))
    step = {"source": a.source, "query": a.query, "found": a.found or "(nothing)", "verdict": a.verdict}
    for k, v in (("source_id", a.source_id), ("record_id", a.record), ("person_id", a.person)):
        if v:
            step[k] = low(v)
    if a.person and not ctx.row("person", ctx.resolve(a.person)):
        refuse("person %s is not in this database" % a.person, "R-04")
    if a.record and not ctx.row("record", ctx.resolve(a.record)):
        refuse("record %s is not in this database" % a.record, "R-04")
    if a.source_id and not ctx.row("source", ctx.resolve(a.source_id)):
        refuse("source %s is not in this database" % a.source_id, "R-04")
    no = append_step(ctx, task, step)
    ctx.commit()
    print("OK  step %d recorded on task %s: %s @ %s -> %s" % (no, task["task_id"], a.query, a.source, a.verdict))


def cmd_dead_end(ctx, a):
    task = require_task(ctx, a.task, annotation=True, purpose="a dead end")
    for s in (task.get("result") or {}).get("steps") or []:
        if s.get("verdict") == "dead_end" and s.get("source") == a.source and s.get("query") == a.query and \
                low(s.get("person_id")) == low(a.person):
            print("OK  already recorded (same person, source and query) -- not repeated: %s @ %s: %s"
                  % (a.person or "-", a.source, a.query))
            return
    if a.person and not ctx.row("person", ctx.resolve(a.person)):
        refuse("person %s is not in this database" % a.person, "R-04")
    step = {"source": a.source, "query": a.query, "found": a.note or "(nothing found)", "verdict": "dead_end"}
    if a.person:
        step["person_id"] = low(a.person)
    no = append_step(ctx, task, step)
    ctx.commit()
    print("OK  dead end recorded (step %d of task %s): %s @ %s: %s" % (no, task["task_id"], a.person or "-",
                                                                       a.source, a.query))


# ------------------------------------------------------------------ retract (R-29: never delete)

def author_of(ctx, task_id):
    t = ctx.doc("task", task_id) if task_id else None
    return task_agent(t) if t else None


def retract_target(ctx, target, task_id, reason, by_review=False):
    """Retract one thing (8.5 id forms). Returns a plain description. Content write: the holder is
    re-staged; nothing is deleted."""
    agent = ctx.agent_id

    def allowed(creating_task):
        if by_review:
            return
        if author_of(ctx, creating_task) != agent:
            refuse("only the author (the session whose task made it) or an accepted run review may retract %s"
                   % target, "R-29")
    if "#" in target:
        base, _, label = target.partition("#")
        kind = ctx.kind_of_id(base)
        doc = ctx.doc(kind, base) if kind else None
        if doc is None:
            refuse("%s is not a document here" % base, "R-04")
        allowed(doc.get("created_by_task_id"))
        if kind == "record" and label.startswith("event:"):
            ref = label[6:]
            e = entry_of(doc, ref)
            if not e or e.get("status", "active") != "active":
                refuse("no active event entry %s on record %s" % (ref, base), "R-04")
            e.update(status="retracted", status_reason=reason, status_task_id=low(task_id))
            ctx.store("record", doc, content=True)
            occ = ctx.doc("event", e.get("occurrence_id"))
            if occ and occ.get("lifecycle") == "active":
                occ.update(lifecycle="retracted", lifecycle_reason=reason, lifecycle_task_id=low(task_id))
                ctx.store("event", occ, content=True)
            cited = ctx.con.execute("SELECT count(*) n FROM claim_index WHERE source_record_id=? AND record_event_ref=? "
                                    "AND status IN ('proposed','proven','disputed')", (low(base), ref)).fetchone()["n"]
            return "event entry %s on record %s (and its occurrence); %d live claim(s) cite it -- Quality Control will list them" % (ref, base, cited)
        if kind == "record" and label.startswith("copy:"):
            sha = label[5:]
            c = next((x for x in doc.get("saved_copies") or [] if x.get("sha256") == sha), None)
            if c is None:
                refuse("no saved copy %s on record %s" % (sha, base), "R-04")
            for r in c.get("regions") or []:
                r["status"] = "retracted"
            c.setdefault("notes", []).append({"text": "Withdrawn: %s" % reason, "note_type": "other", "task_id": low(task_id)})
            ctx.store("record", doc, content=True)
            return "saved copy %s on record %s (its regions withdrawn; the description is kept)" % (sha, base)
        if kind == "record":
            m = mention_of(doc, label)
            if not m or m.get("status", "active") != "active":
                refuse("no active mention %s on record %s" % (label, base), "R-04")
            m.update(status="retracted", status_reason=reason)
            ctx.store("record", doc, content=True)
            return "mention %s on record %s" % (label, base)
        if kind == "relationship" and label.startswith("partner:"):
            who = label[8:]
            for p in doc.get("partners") or []:
                if p.get("status", "active") != "active":
                    continue
                if (who == "absent" and p.get("absent")) or (p.get("person_id") and ctx.resolve(p["person_id"]) == ctx.resolve(who)):
                    p.update(status="retracted", status_reason=reason, status_task_id=low(task_id))
                    ctx.store("relationship", doc, content=True)
                    return "partner %s of family %s" % (who, base)
            refuse("no active partner %s in family %s" % (who, base), "R-04")
        if kind == "dna" and label.startswith("match:"):
            mid = label[6:]
            for mt in doc.get("matches") or []:
                if low(mt.get("match_id")) == low(mid) and live_claim(mt):
                    ctx.set_status(mt, "retracted", low(task_id), reason)
                    ctx.store("dna", doc, content=True)
                    return "DNA match %s on test %s" % (mid, base)
            refuse("no live match %s on test %s" % (mid, base), "R-04")
        refuse("%s is not a retractable id form (record#mention, record#event:ref, record#copy:sha, "
               "family#partner:id, dna#match:id)" % target, "R-04")
    ident = low(target)
    hit = ctx.con.execute("SELECT holder_table, holder_id FROM claim_index WHERE assertion_id=?", (ident,)).fetchone()
    if hit:
        kind = KIND_OF_TABLE[hit["holder_table"]]
        doc = ctx.doc(kind, hit["holder_id"])
        for ln, c in iter_claims(kind, doc):
            if low(c.get("assertion_id")) == ident:
                if not live_claim(c):
                    refuse("claim %s is already %s" % (ident, c.get("status")), "R-29")
                allowed(c.get("task_id"))
                ctx.set_status(c, "retracted", low(task_id), reason)
                ctx.store(kind, doc, content=True)
                after_claim_change(ctx, kind, doc, [c], task_id)
                return "claim %s (%s of %s %s)" % (ident, ln, TITLE[kind], doc[KEY[kind]])
    m = ctx.con.execute("SELECT * FROM membership_index WHERE membership_id=?", (ident,)).fetchone()
    if m:
        for fid in ctx.merged_into(m["family_id"]):
            fam = ctx.doc("relationship", fid)
            mem = find_membership(fam or {}, ident)
            if mem is None:
                continue
            if mem.get("status", "active") != "active":
                refuse("membership %s is already %s" % (ident, mem.get("status")), "R-29")
            allowed(mem.get("task_id") or fam.get("created_by_task_id"))
            mem.update(status="retracted", status_reason=reason)
            ctx.store("relationship", fam, content=True)
            recompute_preferred(ctx, m["child_id"], task_id, reason="retract")
            return "membership %s of %s" % (ident, m["child_id"])
    reg = ctx.con.execute("SELECT * FROM region_index WHERE region_id=?", (ident,)).fetchone()
    if reg:
        doc = ctx.doc("record", reg["record_id"])
        allowed(doc.get("created_by_task_id"))
        for c in doc.get("saved_copies") or []:
            for r in c.get("regions") or []:
                if low(r["region_id"]) == ident:
                    r["status"] = "retracted"
        ctx.store("record", doc, content=True)
        return "region %s on record %s" % (ident, reg["record_id"])
    kind = ctx.kind_of_id(ident)
    if kind is None:
        refuse("%s is not an id in this database" % target, "R-04")
    doc = ctx.doc(kind, ident)
    if kind == "task":
        if author_of(ctx, ident) not in (agent, None) and doc.get("created_by") != agent and not by_review:
            refuse("only the session that claimed or created task %s may cancel it" % ident, "R-29")
        doc["status"] = "cancelled"
        doc.setdefault("completed_at", now())
        if not doc.get("provenance"):
            doc["provenance"] = dict(ctx.require_session()["provenance"], claimed_at=now())
        doc["error_message"] = reason
        ctx.store("task", doc, content=True)
        return "task %s (cancelled)" % ident
    if doc.get("lifecycle") != "active":
        refuse("%s %s is %s" % (TITLE[kind], ident, doc.get("lifecycle")), "R-29")
    allowed(doc.get("created_by_task_id"))
    doc.update(lifecycle="retracted", lifecycle_reason=reason, lifecycle_task_id=low(task_id))
    ctx.store(kind, doc, content=True)
    if kind == "record":
        for e in doc.get("events_documented") or []:
            occ = ctx.doc("event", e.get("occurrence_id"))
            if occ and occ.get("lifecycle") == "active":
                occ.update(lifecycle="retracted", lifecycle_reason=reason, lifecycle_task_id=low(task_id))
                ctx.store("event", occ, content=True)
    if kind == "relationship":
        for p in relationship_persons(ctx, doc):
            recompute_preferred(ctx, p, task_id, reason="retract")
    return "%s %s" % (TITLE[kind], ident)


def after_claim_change(ctx, kind, doc, claims, task_id):
    """After claim statuses change: preferences (a link that lost its facts), owners, floors."""
    if kind == "relationship" and doc["kind"] == "parent_child":
        recompute_preferred(ctx, ctx.resolve(doc["child_id"]), task_id, reason="retract")
    refresh_event_owners(ctx, [c.get("occurrence_id") for c in claims if c.get("occurrence_id")])
    if kind == "person":
        raise_floors_for_person(ctx, doc["person_id"], task_id)
    ctx.forget()


def cmd_retract(ctx, a):
    require_task(ctx, a.task, annotation=True, purpose="a retraction")
    if not a.reason:
        refuse("retract needs --reason")
    what = retract_target(ctx, a.id, a.task, a.reason)
    ctx.log("retract", what)
    ctx.commit()
    print("OK  retracted %s (kept, marked retracted: %s)" % (what, a.reason))


# ------------------------------------------------------------------ proofs (4.3)

def _relied_reliable(ctx, item):
    k = item["kind"]
    if k == "claim":
        hit = ctx.con.execute("SELECT holder_table, holder_id, list_name, status FROM claim_index WHERE assertion_id=?",
                              (low(item["assertion_id"]),)).fetchone()
        if not hit or hit["status"] not in LIVE:
            return False
        holder = ctx.doc(KIND_OF_TABLE[hit["holder_table"]], hit["holder_id"])
        for ln, c in iter_claims(KIND_OF_TABLE[hit["holder_table"]], holder or {}):
            if low(c.get("assertion_id")) == low(item["assertion_id"]):
                return reliable(ctx, c.get("evidence"), ln)
        return False
    if k == "record":
        r = ctx.row("record", ctx.resolve(item["record_id"]))
        return bool(r) and r["source_class"] in ("original", "derivative")
    if k == "dna_match":
        return True
    if k == "proof":
        t = ctx.doc("task", item["task_id"])
        return bool(t) and has_proof(t) and accepted_now(ctx, item["task_id"])
    return False


def _relied_indirect(ctx, item):
    k = item["kind"]
    if k in ("dna_match", "search_step"):
        return True
    if k == "claim":
        hit = ctx.con.execute("SELECT holder_table, holder_id FROM claim_index WHERE assertion_id=?",
                              (low(item["assertion_id"]),)).fetchone()
        holder = ctx.doc(KIND_OF_TABLE[hit["holder_table"]], hit["holder_id"]) if hit else None
        for ln, c in iter_claims(KIND_OF_TABLE[hit["holder_table"]], holder or {}) if hit else []:
            if low(c.get("assertion_id")) == low(item["assertion_id"]):
                ev = c.get("evidence") or {}
                return ev.get("kind") in ("indirect", "negative") or ev.get("basis") in ("dna", "search")
    return False


def find_claim(ctx, assertion_id):
    hit = ctx.con.execute("SELECT holder_table, holder_id, list_name FROM claim_index WHERE assertion_id=?",
                          (low(assertion_id),)).fetchone()
    if not hit:
        return None, None, None, None
    kind = KIND_OF_TABLE[hit["holder_table"]]
    doc = ctx.doc(kind, hit["holder_id"])
    for ln, c in iter_claims(kind, doc or {}):
        if low(c.get("assertion_id")) == low(assertion_id):
            return kind, doc, ln, c
    return None, None, None, None


def cmd_apply_proof(ctx, a):
    ctx.require_session()
    task = ctx.doc("task", a.task_id)
    if not task:
        refuse("no task %s" % a.task_id, "R-04")
    proof = (task.get("result") or {}).get("proof")
    if task["task_type"] not in ("resolve_conflict", "merge_person") or task["status"] != "done" or not proof:
        refuse("apply-proof needs a done resolve_conflict or merge_person task with a result.proof", "P1")
    if (proof.get("origin") or "maxgen") != "maxgen":
        refuse("a proof from another program (origin %s) is never accepted" % proof.get("origin"), "P1")
    reviews = review_tasks(ctx, task["task_id"])
    if not reviews or _verdict(reviews[-1]) != "accepted":
        refuse("the proof has no accepted review now; a judge_review task targeting it must say accepted", "P2")
    settles = proof["conclusion"].get("settles") or []
    wants_proven = any(s["status"] == "proven" for s in settles)
    if wants_proven:
        good = [r for r in reviews if _verdict(r) == "accepted" and (
            (independence(r, task) == "human" and (r.get("provenance") or {}).get("channel") == "person_page") or
            independence(r, task) == "different_model")]
        if not good:
            refuse("'proven' needs the person's own decision on their Probable Pedigree page (a relayed answer or a "
                   "self-reported AI review is not enough)", "P2")
    settled_ids = {low(s["assertion_id"]) for s in settles}
    for item in proof["relied_on"]:
        if item["kind"] == "claim" and low(item.get("assertion_id")) in settled_ids:
            refuse("the proof relies on claim %s, which it settles; a proof cannot rely on what it settles"
                   % item["assertion_id"], "P3")
    if wants_proven:
        if not any(_relied_reliable(ctx, item) for item in proof["relied_on"]):
            refuse("nothing the proof relies on is reliable evidence now (an original or derivative record, a DNA "
                   "match, a claim with reliable evidence, or an accepted proof)", "P4")
        if any(_relied_indirect(ctx, item) for item in proof["relied_on"]) and proof["proof_form"] != "argument":
            refuse("the proof relies on indirect, negative, DNA or search evidence, so it must be a full written "
                   "argument (proof_form argument)", "P5")
    changed = []
    for s in settles:
        kind, doc, ln, c = find_claim(ctx, s["assertion_id"])
        if c is None:
            refuse("the proof settles claim %s, which is not stored" % s["assertion_id"], "R-04")
        if not live_claim(c) and s["status"] != "proposed":
            refuse("claim %s is %s; a proof settles only live claims" % (s["assertion_id"], c.get("status")), "R-08")
        if s["status"] == "proven" and (c.get("evidence") or {}).get("basis") == "imported":
            refuse("claim %s rests on an imported file; imported evidence never supports proven" % s["assertion_id"], "P6")
        changed.append((kind, doc, ln, c, s["status"]))
    for kind, doc, ln, c, st in changed:
        doc = ctx.doc(kind, doc[KEY[kind]])
        for ln2, c2 in iter_claims(kind, doc):
            if low(c2.get("assertion_id")) == low(c["assertion_id"]):
                ctx.set_status(c2, st, task["task_id"], proof["conclusion"]["statement"])
        ctx.store(kind, doc, content=False)
        after_claim_change(ctx, kind, doc, [c], task["task_id"])
    # P7: the proof is as private as what it relies on
    living = any(item["kind"] == "dna_match" for item in proof["relied_on"])
    for item in proof["relied_on"]:
        if item["kind"] in ("record", "authored_record", "mention") and record_private(ctx, item["record_id"]):
            living = True
    task = ctx.doc("task", task["task_id"])
    if living and raise_doc_floor(ctx, "task", task, task["task_id"]):
        ctx.store("task", task, content=False)
    # parent sets settled by the proof (a membership combined into another by a merge is followed, 4.1)
    for mu in proof["conclusion"].get("membership_updates") or []:
        apply_membership_update(ctx, task, mu, proof["conclusion"]["statement"])
    # occurrence verdicts
    for ov in (task.get("result") or {}).get("occurrence_verdicts") or []:
        ids = [ctx.resolve(x) for x in ov["occurrence_ids"]]
        if ov["verdict"] == "same":
            survivor = ctx.resolve(ov.get("survivor_id") or ids[0])
            for o in ids:
                if o != survivor and ctx.resolve(o) != survivor:
                    do_merge(ctx, "event", survivor, o, task["task_id"], method="same_occasion", gate=False)
        else:
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    do_distinct(ctx, "event", ids[i], ids[j], task["task_id"], ov["reason"])
    ctx.log("apply-proof", task["task_id"])
    ctx.commit()
    print("OK  proof %s applied: %s" % (task["task_id"], ", ".join("%s -> %s" % (c["assertion_id"], st)
                                                                   for _k, _d, _l, c, st in changed) or "no claim statuses"))


def resolve_membership(ctx, membership_id):
    """The membership_index row a membership id names now: a membership combined into another by a
    merge (status merged_away, merged_into_membership_id set; an undone merge clears both) is followed
    to the end of its chain (4.1, Appendix C)."""
    cur, seen = low(membership_id), set()
    while True:
        r = ctx.con.execute("SELECT * FROM membership_index WHERE membership_id=?", (cur,)).fetchone()
        if not r or r["status"] != "merged_away" or not r["merged_into_membership_id"] or cur in seen:
            return r
        seen.add(cur)
        cur = low(r["merged_into_membership_id"])


def membership_holder(ctx, m):
    """The family document that stores a membership (the family or a merged-away shell of it)."""
    for fid in ctx.merged_into(m["family_id"]):
        fam = ctx.doc("relationship", fid)
        if fam and find_membership(fam, m["membership_id"]):
            return fam
    return None


def apply_membership_update(ctx, task, mu, statement):
    """One membership_updates item of an accepted proof: set kind, added parent links, retraction,
    preference -- under the gates apply-proof has already passed (P1, P2) plus R-04, R-20 and R-31."""
    tid = task["task_id"]
    m = resolve_membership(ctx, mu["membership_id"])
    if not m or m["status"] != "active":
        refuse("membership %s is not live" % mu["membership_id"], "R-04")
    m = dict(m)
    fam = membership_holder(ctx, m)
    if fam is None:
        refuse("membership %s is not stored on a family here" % mu["membership_id"], "R-04")
    child = ctx.resolve(m["child_id"])
    path = ["children", {"k": "membership_id", "v": m["membership_id"]}]
    if mu.get("retract"):
        ctx.annotate("relationship", fam, path + ["status"], "retracted", tid)
        ctx.annotate("relationship", fam, path + ["status_reason"], ("settled by proof %s: %s" % (tid, statement))[:4000], tid)
        ctx.annotate("relationship", fam, path + ["is_preferred"], False, tid)
        ctx.annotate("relationship", fam, path + ["preferred_by_task_id"], None, tid)
        ctx.store("relationship", fam, content=False)
        recompute_preferred(ctx, child, tid, reason="proof")
        return
    if mu.get("set_kind") and mu["set_kind"] != m["set_kind"]:
        if mu["set_kind"] == "adoptive" and not person_accepted(ctx, tid):
            refuse("setting a parent set to adoptive needs the person's yes", "R-31")
        for other in memberships_of(ctx, child):
            if (other["membership_id"] != m["membership_id"] and other["set_kind"] == mu["set_kind"]
                    and ctx.resolve(other["family_id"]) == ctx.resolve(m["family_id"])):
                refuse("child %s already has a live %s membership in family %s (%s); one per child and set kind -- "
                       "retract one of them in the same proof" % (child, mu["set_kind"], m["family_id"],
                                                                   other["membership_id"]), "R-20")
        ctx.annotate("relationship", fam, path + ["set_kind"], mu["set_kind"], tid)
        ctx.store("relationship", fam, content=False)
    if mu.get("add_parent_link_ids"):
        fam = membership_holder(ctx, m)
        mem = find_membership(fam, m["membership_id"])
        links = [low(x) for x in mem.get("parent_link_ids") or []]
        partners_added = False
        for lid in mu["add_parent_link_ids"]:
            pc = ctx.doc("relationship", ctx.resolve(lid))
            if (not pc or pc["kind"] != "parent_child" or pc.get("lifecycle", "active") != "active"
                    or ctx.resolve(pc["child_id"]) != child or not live_facts(pc)):
                refuse("add_parent_link_ids names %s, which is not a live parent_child record with a live fact for "
                       "child %s" % (lid, child), "R-04")
            rid = low(pc["relationship_id"])
            if rid in [ctx.resolve(x) for x in links]:
                continue
            links.append(rid)
            parent = ctx.resolve(pc["parent_id"])
            if fam.get("lifecycle", "active") == "active" and parent not in partner_ids(ctx, fam):
                _add_partner(ctx, fam, parent, pc.get("parent_role_as_written"), tid)
                partners_added = True
        ctx.annotate("relationship", fam, path + ["parent_link_ids"], links, tid)
        check_relationship_doc(ctx, fam, task, stored=fam, adoption_checked=True)
        ctx.store("relationship", fam, content=partners_added)
    if mu.get("prefer"):
        prefer_membership(ctx, m["membership_id"], tid)
    recompute_preferred(ctx, child, tid, reason="proof")


def cmd_withdraw_proof(ctx, a):
    ctx.require_session()
    task = ctx.doc("task", a.task_id)
    if not task or not has_proof(task):
        refuse("no proof task %s" % a.task_id, "R-04")
    review = ctx.doc("task", a.task)
    if not review or review["task_type"] != "judge_review" or \
            low(((review.get("result") or {}).get("review") or {}).get("target_task_id")) != low(task["task_id"]) or \
            _verdict(review) == "accepted":
        refuse("withdraw-proof needs --task <a judge_review of this proof whose verdict is rework or rejected>", "R-08")
    rows = ctx.con.execute("SELECT assertion_id FROM claim_index WHERE status IN ('proven','disputed','disproven')").fetchall()
    back = []
    for r in rows:
        kind, doc, ln, c = find_claim(ctx, r["assertion_id"])
        if c is not None and low(c.get("status_task_id")) == low(task["task_id"]):
            ctx.set_status(c, "proposed", low(a.task), "proof withdrawn: %s" % a.reason)
            ctx.store(kind, doc, content=False)
            after_claim_change(ctx, kind, doc, [c], a.task)
            back.append(c["assertion_id"])
    task = ctx.doc("task", task["task_id"])
    task.setdefault("notes", []).append({"text": "Proof withdrawn: %s" % a.reason, "note_type": "other",
                                         "task_id": low(a.task)})
    ctx.store("task", task, content=False)
    pending = {"task_id": new_id(), "schema_version": MAXGEN_VERSION, "task_type": "judge_review", "status": "pending",
               "priority": 3, "is_living_flag": bool(task.get("is_living_flag")),
               "payload": {"kind": "withdrawn_proof", "target_task_id": low(task["task_id"]), "assertion_ids": back},
               "result": {"what_we_did_and_result": "The proof was withdrawn; these claims went back to proposed."}}
    write_task(ctx, pending)
    ctx.store("task", pending, new=True)
    refresh_claims_citing_proof(ctx, task["task_id"])
    ctx.commit()
    print("OK  proof %s withdrawn: %d claim(s) back to proposed; review task %s opened" % (task["task_id"], len(back),
                                                                                         pending["task_id"]))


# ------------------------------------------------------------------ merges (4.1): redirects, never rewrites

def _distinct_ids(ctx, doc):
    return {ctx.resolve(d["id"]) for d in doc.get("distinct_from") or []} | {low(d["id"]) for d in doc.get("distinct_from") or []}


def merge_gate(ctx, kind, task):
    t = task["task_type"]
    if kind == "person":
        if t != "merge_person" or not person_accepted(ctx, task["task_id"]) or not accepted_now(ctx, task["task_id"]):
            refuse("merging persons needs an accepted merge_person task with the person's recorded yes", "R-28")
    elif kind in ("record", "relationship", "source", "name", "dna"):
        if t != "resolve_conflict" or not accepted_now(ctx, task["task_id"]):
            refuse("merging a %s needs an accepted resolve_conflict task" % TITLE[kind], "R-28")
    elif kind in ("place", "group", "event"):
        if t not in ANALYSIS_TASKS:
            refuse("merging a %s needs a resolve_conflict, build_tree or import_file task" % TITLE[kind], "R-28")


def person_merge_problems(ctx, a_id, b_id):
    """4.1 step 7: would the merge make one family's two partners the same person, or anyone their
    own parent, partner or child?"""
    same = {a_id, b_id}
    problems = []
    for pid in same:
        for rid, role in links_of(ctx, pid):
            d = ctx.doc("relationship", rid)
            if not d:
                continue
            if d["kind"] == "family":
                ps = [ctx.resolve(x) for x in partner_ids(ctx, d)]
                mapped = [a_id if x in same else x for x in ps]
                if len(mapped) == 2 and mapped[0] == mapped[1]:
                    problems.append("family %s would have the same person as both partners" % rid)
                kids = [a_id if ctx.resolve(m["child_id"]) in same else ctx.resolve(m["child_id"])
                        for m in d.get("children") or [] if m.get("status", "active") == "active"]
                if a_id in mapped and a_id in kids:
                    problems.append("family %s would make a person a partner and a child of the same family" % rid)
            if d["kind"] == "parent_child":
                p, c = ctx.resolve(d["parent_id"]), ctx.resolve(d["child_id"])
                p, c = (a_id if p in same else p), (a_id if c in same else c)
                if p == c:
                    problems.append("parent_child %s would make a person their own parent" % rid)
    return sorted(set(problems))


def do_merge(ctx, kind, survivor, absorbed, task_id, method="manual", label_map=None, gate=True):
    a_id, b_id = ctx.resolve(survivor), ctx.resolve(absorbed)
    if a_id == b_id:
        refuse("%s and %s are already the same %s" % (survivor, absorbed, TITLE[kind]))
    a_doc, b_doc = ctx.doc(kind, a_id), ctx.doc(kind, b_id)
    if not a_doc or not b_doc:
        refuse("both ids must be %s documents here" % TITLE[kind], "R-04")
    if a_doc.get("lifecycle") != "active" or b_doc.get("lifecycle") != "active":
        refuse("both documents must be active", "R-28")
    if a_id in _distinct_ids(ctx, b_doc) or b_id in _distinct_ids(ctx, a_doc):
        refuse("%s and %s are recorded as distinct; that blocks a merge" % (a_id, b_id), "R-28")
    if kind == "relationship" and gate:
        if a_doc["kind"] != b_doc["kind"]:
            refuse("only relationships of the same kind can be merged", "R-28")
        if a_doc["kind"] == "family":
            proof = (ctx.doc("task", task_id).get("result") or {}).get("proof") or {}
            subj = [s for s in proof.get("subject") or [] if s.get("kind") == "union"]
            named = {low(s.get("id")) for s in subj} | {low(s.get("id2")) for s in subj if s.get("id2")}
            if not ({a_id, b_id} <= named or ({low(survivor), low(absorbed)} <= named)):
                refuse("two families are merged only under a proof whose subject is the union, naming both", "R-28")
    if kind == "person":
        problems = person_merge_problems(ctx, a_id, b_id)
        if problems:
            refuse("this merge would break the family structure: %s" % "; ".join(problems), "R-06")
    batch = ctx.batch or "merge:%s:%s" % (b_id, new_id())
    old_batch, ctx.batch = ctx.batch, batch
    try:
        at = now()
        ctx.annotate(kind, b_doc, ["lifecycle"], "merged_away", task_id)
        ctx.annotate(kind, b_doc, ["duplicate_of"], a_id, task_id)
        ctx.annotate(kind, b_doc, ["lifecycle_task_id"], low(task_id), task_id)
        ctx.annotate(kind, b_doc, ["lifecycle_reason"], "merged into %s" % a_id, task_id)
        entry = {"merged_id": b_id, "task_id": low(task_id), "merged_at": at, "method": method}
        if label_map:
            entry["label_map"] = label_map
        a_doc.setdefault("merge_history", []).append(entry)
        ctx.store(kind, b_doc, content=False)
        ctx.store(kind, a_doc, content=True)
        ctx.con.execute("INSERT INTO id_redirect(old_id,kind,new_id,task_id,merged_at) VALUES (?,?,?,?,?)",
                        (b_id, kind, a_id, low(task_id), at))
        ctx.con.execute("INSERT INTO annotation_log(table_name,target_id,field,old_value,new_value,task_id,batch_id,at) "
                        "VALUES (?,?,?,?,?,?,?,?)", (TABLE[kind], b_id, json.dumps(["$redirect"]), "null",
                                                     json.dumps(a_id), low(task_id), batch, at))
        rebuild_indexes(ctx)
        if kind == "person":
            _merge_person_structure(ctx, a_id, task_id)
        if kind == "relationship" and a_doc["kind"] == "family":
            _combine_memberships(ctx, a_id, task_id)
        if kind == "record" and label_map:
            for lm in label_map:
                if lm["kind"] != "event":
                    continue
                ea, eb = entry_of(a_doc, lm["to_label"]), entry_of(b_doc, lm["from_label"])
                if ea and eb and ea.get("occurrence_id") and eb.get("occurrence_id"):
                    oa, ob = ctx.resolve(ea["occurrence_id"]), ctx.resolve(eb["occurrence_id"])
                    if oa != ob:
                        do_merge(ctx, "event", oa, ob, task_id, method="duplicate_record", gate=False)
        rebuild_indexes(ctx)
        for pid in (relationship_persons(ctx, a_doc) if kind == "relationship" else [a_id] if kind == "person" else []):
            recompute_preferred(ctx, pid, task_id, reason="merge")
        if kind == "person":
            raise_floors_for_person(ctx, a_id, task_id)
    finally:
        ctx.batch = old_batch
    return batch


def _merge_person_structure(ctx, a_id, task_id):
    """After a person merge: duplicate parent_child records and associations are merged, duplicate
    memberships of one child and kind in one family are combined (all logged, all undone by unmerge)."""
    seen = {}
    for rid, role in links_of(ctx, a_id):
        d = ctx.doc("relationship", rid)
        if not d or d["kind"] != "parent_child":
            continue
        pair = (ctx.resolve(d["parent_id"]), ctx.resolve(d["child_id"]))
        if pair in seen and seen[pair] != rid:
            keep, drop = sorted([seen[pair], rid], key=lambda x: (ctx.row("relationship", x)["added_at"], x))
            do_merge(ctx, "relationship", keep, drop, task_id, method="manual", gate=False)
            seen[pair] = keep
        else:
            seen[pair] = rid
    assoc = {}
    for rid, role in links_of(ctx, a_id):
        d = ctx.doc("relationship", rid)
        if not d or d["kind"] != "association":
            continue
        k1 = ctx.resolve((d.get("party1") or {}).get("person_id")) or str(d.get("party1"))
        k2 = ctx.resolve((d.get("party2") or {}).get("person_id")) or str(d.get("party2"))
        key = (d["association_type"],) + ((k1, k2) if d["association_type"] not in UNORDERED_ASSOCIATIONS else tuple(sorted((k1, k2))))
        if key in assoc and assoc[key] != rid:
            keep, drop = sorted([assoc[key], rid], key=lambda x: (ctx.row("relationship", x)["added_at"], x))
            do_merge(ctx, "relationship", keep, drop, task_id, method="manual", gate=False)
        else:
            assoc[key] = rid
    for rid, role in links_of(ctx, a_id, "member_child"):
        _combine_memberships(ctx, rid, task_id)
    # the absorbed person's recorded preference is cleared; the survivor's is kept
    ms = memberships_of(ctx, a_id)
    recorded = [m for m in ms if m["preferred_by_task_id"]]
    if len(recorded) > 1:
        for m in recorded:
            fam = ctx.doc("relationship", m["family_id"])
            mem = find_membership(fam or {}, m["membership_id"])
            if mem is not None and ctx.resolve(mem["child_id"]) != a_id:
                ctx.annotate("relationship", fam, ["children", {"k": "membership_id", "v": m["membership_id"]},
                                                   "preferred_by_task_id"], None, task_id)
                ctx.store("relationship", fam, content=False)


def _combine_memberships(ctx, family_id, task_id):
    """One live membership per child, family and set kind: duplicates are combined into the
    earliest (the other is merged_away with merged_into_membership_id)."""
    fid = ctx.resolve(family_id)
    rows = ctx.con.execute("SELECT * FROM membership_index WHERE family_id=? AND status='active' "
                           "ORDER BY created_at, membership_id", (fid,)).fetchall()
    # Ties on created_at (whole seconds) must not fall to a random id: the surviving family's own
    # membership wins over one carried in from an absorbed family, so merges are deterministic.
    survivor = ctx.doc("relationship", fid)
    rows = sorted(rows, key=lambda m: (m["created_at"],
                                       0 if (survivor and find_membership(survivor, m["membership_id"])) else 1,
                                       m["membership_id"]))
    first = {}
    for m in rows:
        key = (m["child_id"], m["set_kind"])
        if key not in first:
            first[key] = m["membership_id"]
            continue
        for d_id in ctx.merged_into(fid):
            fam = ctx.doc("relationship", d_id)
            if fam and find_membership(fam, m["membership_id"]):
                path = ["children", {"k": "membership_id", "v": m["membership_id"]}]
                ctx.annotate("relationship", fam, path + ["status"], "merged_away", task_id)
                ctx.annotate("relationship", fam, path + ["merged_into_membership_id"], first[key], task_id)
                ctx.annotate("relationship", fam, path + ["is_preferred"], False, task_id)
                ctx.store("relationship", fam, content=False)


def undo_batch(ctx, batch, task_id):
    """Reverse every logged annotation of one batch, newest first."""
    rows = ctx.con.execute("SELECT * FROM annotation_log WHERE batch_id=? ORDER BY id DESC", (batch,)).fetchall()
    docs = {}
    for r in rows:
        path = json.loads(r["field"])
        if path == ["$redirect"]:
            continue
        kind = KIND_OF_TABLE[r["table_name"]]
        key = (kind, r["target_id"])
        if key not in docs:
            docs[key] = ctx.doc(kind, r["target_id"])
        doc = docs[key]
        if doc is None:
            continue
        old = json.loads(r["old_value"]) if r["old_value"] is not None else ABSENT
        path_set(doc, path, old if old is not None else ABSENT)
    for (kind, _ident), doc in docs.items():
        if doc is not None:
            ctx.store(kind, doc, content=False)
    ctx.con.execute("INSERT INTO annotation_log(table_name,target_id,field,old_value,new_value,task_id,batch_id,at) "
                    "VALUES (?,?,?,?,?,?,?,?)", ("annotation_log", batch, json.dumps(["$undo"]), None, None,
                                                 low(task_id), "undo:" + batch, now()))
    return len(rows)


def do_unmerge(ctx, kind, absorbed, task_id):
    b_id = low(absorbed)
    r = ctx.con.execute("SELECT * FROM id_redirect WHERE old_id=? AND reverted_at IS NULL", (b_id,)).fetchone()
    if not r or r["kind"] != kind:
        refuse("%s is not a merged-away %s here" % (absorbed, TITLE[kind]), "R-04")
    a_id = r["new_id"]
    log = ctx.con.execute("SELECT batch_id FROM annotation_log WHERE target_id=? AND field=? AND batch_id IS NOT NULL "
                          "ORDER BY id DESC LIMIT 1", (b_id, json.dumps(["$redirect"]))).fetchone()
    batch = log["batch_id"] if log else None
    # every redirect this batch wrote (nested merges of links, memberships, occurrences) is reverted too
    redirect_rows = ctx.con.execute("SELECT target_id FROM annotation_log WHERE batch_id=? AND field=?",
                                    (batch, json.dumps(["$redirect"]))).fetchall() if batch else [{"target_id": b_id}]
    at = now()
    for rr in redirect_rows:
        red = ctx.con.execute("SELECT * FROM id_redirect WHERE old_id=? AND reverted_at IS NULL", (rr["target_id"],)).fetchone()
        if not red:
            continue
        ctx.con.execute("UPDATE id_redirect SET reverted_at=?, revert_task_id=? WHERE id=?", (at, low(task_id), red["id"]))
        surv_kind = red["kind"]
        surv = ctx.doc(surv_kind, red["new_id"])
        for e in (surv or {}).get("merge_history") or []:
            if low(e["merged_id"]) == red["old_id"] and not e.get("reverted_at"):
                e["reverted_at"] = at
                e["revert_task_id"] = low(task_id)
        if surv:
            ctx.store(surv_kind, surv, content=False)
    if batch:
        undo_batch(ctx, batch, task_id)
    rebuild_indexes(ctx)
    persons = {a_id, b_id} if kind == "person" else set()
    if kind == "relationship":
        for d in (ctx.doc("relationship", a_id), ctx.doc("relationship", b_id)):
            persons |= set(relationship_persons(ctx, d or {"kind": "x"})) if d else set()
    for p in persons:
        if ctx.row("person", p):
            recompute_preferred(ctx, p, task_id, reason="link")
    return a_id


def do_distinct(ctx, kind, id1, id2, task_id, reason):
    for x, y in ((id1, id2), (id2, id1)):
        d = ctx.doc(kind, x)
        if d is None:
            refuse("%s is not a %s here" % (x, TITLE[kind]), "R-04")
        if low(y) in {low(e["id"]) for e in d.get("distinct_from") or []}:
            continue
        d.setdefault("distinct_from", []).append({"id": low(y), "task_id": low(task_id), "reason": reason, "at": now()})
        ctx.store(kind, d, content=False)


def cmd_merge(ctx, a):
    if a.kind not in MERGE_KINDS:
        refuse("merge kinds are %s" % ", ".join(MERGE_KINDS))
    task = require_task(ctx, a.task, annotation=True, purpose="a merge")
    merge_gate(ctx, a.kind, task)
    label_map = None
    if a.label_map:
        if a.kind != "record":
            refuse("--label-map is for record merges only")
        label_map = json.loads(Path(a.label_map).read_text(encoding="utf-8"))
    do_merge(ctx, a.kind, a.survivor, a.absorbed, task["task_id"], label_map=label_map)
    ctx.log("merge", "%s %s <- %s" % (a.kind, a.survivor, a.absorbed))
    ctx.commit()
    print("OK  %s %s merged into %s (forwarding; undo with unmerge)" % (a.kind, low(a.absorbed), ctx.resolve(a.survivor)))


def cmd_unmerge(ctx, a):
    task = require_task(ctx, a.task, annotation=True, purpose="an unmerge")
    merge_gate(ctx, a.kind, task)
    a_id = do_unmerge(ctx, a.kind, a.absorbed, task["task_id"])
    ctx.log("unmerge", "%s %s from %s" % (a.kind, a.absorbed, a_id))
    ctx.commit()
    print("OK  %s %s is active again (unmerged from %s)" % (a.kind, low(a.absorbed), a_id))


def cmd_distinct(ctx, a):
    if a.kind not in MERGE_KINDS:
        refuse("distinct kinds are %s" % ", ".join(MERGE_KINDS))
    require_task(ctx, a.task, annotation=True, purpose="recording two documents as distinct")
    do_distinct(ctx, a.kind, low(a.id1), low(a.id2), a.task, a.reason)
    ctx.commit()
    print("OK  %s %s and %s recorded as distinct (a merge between them is now refused)" % (a.kind, low(a.id1), low(a.id2)))


# ------------------------------------------------------------------ split (4.1 "Splitting")

def cmd_split(ctx, a):
    if a.kind not in ("person", "group", "place"):
        refuse("split works on person, group or place")
    task = require_task(ctx, a.task, annotation=True, purpose="a split", types=("resolve_conflict",), rule="R-28")
    if not accepted_now(ctx, task["task_id"]) or (a.kind == "person" and not person_accepted(ctx, task["task_id"])):
        refuse("split needs an accepted resolve_conflict task%s" % (" with the person's recorded yes" if a.kind == "person" else ""),
               "R-28")
    old = ctx.doc(a.kind, a.id)
    if not old or old.get("lifecycle") != "active":
        refuse("%s is not an active %s" % (a.id, TITLE[a.kind]), "R-04")
    moves = {low(x) for x in a.move or []}
    moved = []
    for ln, c in iter_claims(a.kind, old):
        if low(c.get("assertion_id")) in moves:
            if not live_claim(c):
                refuse("claim %s is not live" % c["assertion_id"], "R-29")
            moved.append((ln, c))
    if len(moved) != len(moves):
        refuse("some --move claims are not on %s" % a.id, "R-04")
    new_doc = copy.deepcopy(old)
    for f in ("created_at", "updated_at", "merge_history", "distinct_from", "split_history", "lifecycle",
              "lifecycle_reason", "lifecycle_task_id", "duplicate_of"):
        new_doc.pop(f, None)
    new_doc[KEY[a.kind]] = new_id()
    new_doc["created_by_task_id"] = low(task["task_id"])
    for ln in CLAIM_LISTS[a.kind]:
        new_doc.pop(ln, None)
    ctx.splitting = True
    try:
        task_docs = {low(task["task_id"]): task}
        for ln, c in moved:
            nc = copy.deepcopy(c)
            for f in HELPER_CLAIM_FIELDS + ("occurrence_id",):
                nc.pop(f, None)
            if isinstance(nc.get("evidence"), dict):
                for f in HELPER_EVIDENCE_FIELDS:
                    nc["evidence"].pop(f, None)
                if c.get("occurrence_id"):
                    pass
            nc["supersedes_assertion_id"] = c["assertion_id"]
            fill_new_claim(ctx, a.kind, ln, nc, task["task_id"])
            nc["task_id"] = low(task["task_id"])
            new_doc.setdefault(ln, []).append(nc)
        if a.kind == "person" and not new_doc.get("name_assertions"):
            new_doc["person_kind"] = "placeholder"
            new_doc["placeholder_label"] = "split from %s" % old["person_id"]
        if a.kind == "group" and not new_doc.get("name_assertions") or a.kind == "place" and not new_doc.get("name_assertions"):
            refuse("the new %s needs at least one name claim among the moved claims" % TITLE[a.kind])
        for ln in CLAIM_LISTS[a.kind]:
            for c in new_doc.get(ln) or []:
                check_claim(ctx, a.kind, new_doc, ln, c, task_docs)
        new_doc["split_history"] = [{"other_id": low(old[KEY[a.kind]]), "direction": "split_from",
                                     "task_id": low(task["task_id"]), "at": now()}]
        errs = ctx.schemas.errors(a.kind, new_doc)
        if errs:
            raise Refused("the new %s would not match its schema (R-01)\n   - %s" % (TITLE[a.kind], "\n   - ".join(errs)))
        ctx.store(a.kind, new_doc, new=True, added_by=ctx.agent_id)
        old = ctx.doc(a.kind, a.id)
        for ln, c in iter_claims(a.kind, old):
            if low(c.get("assertion_id")) in moves:
                ctx.set_status(c, "retracted", low(task["task_id"]), "split")
        old.setdefault("split_history", []).append({"other_id": new_doc[KEY[a.kind]], "direction": "split_into",
                                                    "task_id": low(task["task_id"]), "at": now()})
        ctx.store(a.kind, old, content=True)
        moved_links = []
        for rid in a.move_link or []:
            pc = ctx.doc("relationship", ctx.resolve(rid))
            if not pc or pc["kind"] != "parent_child":
                refuse("--move-link %s is not a parent_child record" % rid, "R-04")
            me = ctx.resolve(old["person_id"])
            if me not in (ctx.resolve(pc["parent_id"]), ctx.resolve(pc["child_id"])):
                refuse("--move-link %s does not name %s" % (rid, old["person_id"]), "R-04")
            facts = live_facts(pc)
            retract_target(ctx, pc["relationship_id"], task["task_id"], "split", by_review=True)
            for f in facts:
                parent = new_doc["person_id"] if ctx.resolve(pc["parent_id"]) == me else ctx.resolve(pc["parent_id"])
                child = new_doc["person_id"] if ctx.resolve(pc["child_id"]) == me else ctx.resolve(pc["child_id"])
                ev = {k: v for k, v in f["evidence"].items() if k not in HELPER_EVIDENCE_FIELDS}
                req = {"link_request": "2.0", "kind": "parents", "child_id": child, "evidence": ev,
                       "confidence": f["confidence"],
                       "parents": [{"person_id": parent, "lineage": f["fact_type"],
                                    "parent_role": pc.get("parent_role") if pc.get("parent_role") in ("father", "mother", "parent") else "unknown"}]}
                if f.get("parent_mention_id"):
                    req["parents"][0]["mention_id"] = f["parent_mention_id"]
                if f.get("child_mention_id"):
                    req["child_mention_id"] = f["child_mention_id"]
                link_parents(ctx, req, task)
            moved_links.append(rid)
    finally:
        ctx.splitting = False
    ctx.log("split", "%s %s -> %s" % (a.kind, a.id, new_doc[KEY[a.kind]]))
    ctx.commit()
    print("OK  split: new %s %s with %d claim(s)%s" % (a.kind, new_doc[KEY[a.kind]], len(moved),
                                                      (" and %d link(s)" % len(moved_links)) if moved_links else ""))


# ------------------------------------------------------------------ resolve (attach a place or group, 3.4)

def cmd_resolve(ctx, a):
    task = require_task(ctx, a.task, annotation=True, purpose="resolve", types=ANALYSIS_TASKS, rule="R-24")
    if a.undo:
        n = undo_batch(ctx, "resolve:" + low(a.undo), task["task_id"])
        if not n:
            refuse("no resolution %s to undo" % a.undo, "R-04")
        rebuild_indexes(ctx)
        ctx.commit()
        print("OK  resolution %s undone (%d field(s) restored)" % (a.undo, n))
        return
    if a.kind not in ("place", "group") or not a.target:
        refuse("resolve <place|group> <id> --task <task> (--claims <assertion_id>... | --written \"words\")")
    target = ctx.resolve(a.target)
    row = ctx.row(a.kind, target)
    if not row or row["lifecycle"] != "active":
        refuse("%s %s is not an active %s" % (a.kind, a.target, TITLE[a.kind]), "R-04")
    if not a.claims and not a.written:
        refuse("resolve needs --claims or --written")
    resolution = new_id()
    ctx.batch = "resolve:" + resolution
    count = 0
    tid = low(task["task_id"])
    claim_ids = {low(x) for x in a.claims or []}
    holders = ctx.con.execute("SELECT DISTINCT holder_table, holder_id FROM claim_index WHERE holder_table IN "
                              "('max_person','max_relationship','max_group','max_place')").fetchall()
    for h in holders:
        kind = KIND_OF_TABLE[h["holder_table"]]
        doc = ctx.doc(kind, h["holder_id"])
        changed = False
        for ln, c in iter_claims(kind, doc):
            cid = low(c.get("assertion_id"))
            path = [ln, {"k": "assertion_id", "v": cid}]
            if a.kind == "place" and isinstance(c.get("place"), dict):
                hit = cid in claim_ids or (a.written and c["place"].get("place_as_written") == a.written)
                if hit:
                    changed |= ctx.annotate(kind, doc, path + ["place", "place_id"], target, tid)
                    changed |= ctx.annotate(kind, doc, path + ["place", "place_task_id"], tid, tid)
            if a.kind == "group" and kind == "person" and ln in ("event_assertions", "membership_assertions"):
                hit = cid in claim_ids or (a.written and c.get("group_as_written") == a.written)
                if hit:
                    changed |= ctx.annotate(kind, doc, path + ["group_id"], target, tid)
            if changed and cid in claim_ids:
                claim_ids.discard(cid)
                count += 1
            elif changed and a.written:
                count += 1
        if changed:
            ctx.store(kind, doc, content=False)
    if a.claims and claim_ids:
        refuse("claim(s) %s were not found (or have no place)" % sorted(claim_ids), "R-04")
    if a.written:
        for r in ctx.con.execute("SELECT record_id FROM max_record WHERE lifecycle='active'").fetchall():
            rec = ctx.doc("record", r["record_id"])
            changed = False
            if a.kind == "place":
                for e in rec.get("events_documented") or []:
                    if (e.get("place") or {}).get("place_as_written") == a.written:
                        p = ["events_documented", {"k": "ref", "v": e["ref"]}, "place"]
                        changed |= ctx.annotate("record", rec, p + ["place_id"], target, tid)
                        changed |= ctx.annotate("record", rec, p + ["place_task_id"], tid, tid)
                if (rec.get("record_place") or {}).get("place_as_written") == a.written:
                    changed |= ctx.annotate("record", rec, ["record_place", "place_id"], target, tid)
                    changed |= ctx.annotate("record", rec, ["record_place", "place_task_id"], tid, tid)
            else:
                for m in rec.get("persons_mentioned") or []:
                    if m.get("group_as_written") == a.written:
                        changed |= ctx.annotate("record", rec, ["persons_mentioned", {"k": "mention_id", "v": m["mention_id"]},
                                                                "group_id"], target, tid)
            if changed:
                count += 1
                ctx.store("record", rec, content=False)
    ctx.batch = None
    ctx.log("resolve", resolution)
    ctx.commit()
    print("OK  resolution %s: %s %s attached to %d item(s) (nothing re-staged; undo with resolve --undo %s)"
          % (resolution, a.kind, target, count, resolution))


# ------------------------------------------------------------------ run reviews (R-29)

def cmd_apply_run_review(ctx, a):
    ctx.require_session()
    task = ctx.doc("task", a.task_id)
    if not task or task["task_type"] != "judge_review" or (task.get("payload") or {}).get("kind") != "run":
        refuse("apply-run-review needs a judge_review task with payload.kind run", "R-29")
    decisions = (task.get("result") or {}).get("run_decisions") or []
    if not decisions:
        refuse("the run review has no run_decisions; a blanket retraction of a whole run is refused -- one decision "
               "per object", "R-29")
    if task["status"] != "done":
        refuse("the run review must be done before it is applied", "R-29")
    done = []
    for d in decisions:
        if d["decision"] == "retract":
            done.append(retract_target(ctx, d["id"], task["task_id"], d["reason"], by_review=True))
    ctx.log("apply-run-review", task["task_id"])
    ctx.commit()
    print("OK  run review %s applied: %d retraction(s)%s" % (task["task_id"], len(done),
                                                             ("\n    " + "\n    ".join(done)) if done else ""))


# ------------------------------------------------------------------ init and sessions

INIT_DDL = DDL.replace("PRAGMA journal_mode=WAL;", "", 1)    # init runs it inside its one transaction


def cmd_init(a):
    # While a restore or move of this tree runs, init neither writes the tree nor makes a new one in
    # its place (the tree file may be away for a moment while the restore swaps it). Both checks look at
    # the tree's real name, whatever spelling of the path was given (fix round 4).
    refuse_unsafe_tree_file(a.db)
    con = connect(a.db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if names and "meta" not in names:
        refuse(V1_DB_MESSAGE)
    if "meta" in names:
        v = con.execute("SELECT value FROM meta WHERE key='maxgen_version'").fetchone()
        if not v or not str(v[0]).startswith("2."):
            refuse(V1_DB_MESSAGE)
        written = con.execute("SELECT value FROM meta WHERE key='max_minor_written'").fetchone()
        if written and int(written[0] or 0) > KNOWN_MINOR:
            refuse(UPDATE_MESSAGE % (written[0], KNOWN_MINOR))     # a newer kit's tree is never written
        if missing_bookkeeping(con):
            refuse(OLD_FORMAT_WORDS % Path(tree_file(a.db)).name, code="old_format")
    con.execute("PRAGMA journal_mode=WAL")        # outside a transaction, where SQLite allows the change
    try:
        # one transaction; the marker is read again while this writer holds the turn, as every kit
        # write does, so a restore that makes it never races init
        con.executescript("BEGIN IMMEDIATE;\n" + INIT_DDL)
        refuse_unsafe_tree_file(a.db)
        for stmt in BOOKKEEPING_DDL:
            con.execute(stmt)
        for _name, stmt in ADDED_INDEXES:
            con.execute(stmt)
        have = {r[0] for r in con.execute("SELECT key FROM meta")}
        # kit_key: the secret for DNA kit-number fingerprints (kit-hash); never exported, never in a document
        defaults = dict(META_DEFAULTS, max_minor_written=str(KNOWN_MINOR), database_id=new_id(),
                        kit_version=__version__, created_at=now(), kit_key=secrets.token_hex(32), tree_id=new_id(),
                        tree_kind=getattr(a, "kind", None) or "own")
        for k, v in defaults.items():
            if k not in have:
                con.execute("INSERT INTO meta(key,value) VALUES (?,?)", (k, str(v)))
        con.execute("INSERT INTO run_log(at,kind,detail) VALUES (?,?,?)", (now(), "init", a.db))
        for stmt in trigger_ddl():        # last: the file's own birth is not a change to log
            con.execute(stmt)
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    print("OK  %s ready (MAXGEN %s, database %s)" % (a.db, MAXGEN_VERSION,
                                                    con.execute("SELECT value FROM meta WHERE key='database_id'").fetchone()[0]))


def kit_hash(con, number: str, schemas=None) -> str:
    """The keyed fingerprint of a DNA kit number (HMAC-SHA-256 with this database's secret kit_key),
    for kit_id_hash, match_kit_hash and kit_aliases[].id_hash. Only reads, unless the database was
    made before the key existed: then make_kit_key gives it one first, as a write of its own."""
    row = con.execute("SELECT value FROM meta WHERE key='kit_key'").fetchone()
    key = row[0] if row else make_kit_key(con, schemas)
    return hmac.new(bytes.fromhex(key), number.strip().upper().encode("utf-8"), "sha256").hexdigest()


def make_kit_key(con, schemas=None) -> str:
    """Give a database made before kit_key existed its key, and return it. It is a write like any
    other, through the kit's write context on its own connection: the maintenance marker, a tree an
    older kit made (until `upgrade`) and a tree a newer kit wrote refuse it, and it is stamped and
    logged. Refused inside an open transaction, whose turn and checks this cannot see."""
    if con.in_transaction:
        refuse("this tree has no kit key yet, and one is made only outside an open transaction; run "
               "`python maxgen_db.py kit-hash genealogy.db` once", code="kit_key")
    files = [r[2] for r in con.execute("PRAGMA database_list").fetchall() if r[1] == "main"]
    if not files or not files[0]:
        refuse("this database has no file, so it cannot be given a kit key", code="kit_key")
    ctx = Ctx(files[0], schemas, write=True, quiet=True, track=False)
    try:
        ctx.command = "kit-hash"
        key = ctx.meta("kit_key")         # another writer may have made it in the meantime
        if not key:
            key = secrets.token_hex(32)
            ctx.con.execute("INSERT INTO meta(key,value) VALUES ('kit_key',?)", (key,))
        ctx.commit()
        return key
    finally:
        ctx.close()


def cmd_kit_hash(ctx, a):
    """Read one kit number from standard input and print its fingerprint; nothing is logged or stored
    (a tree made before the kit key existed gets its key first, a logged write of its own)."""
    number = sys.stdin.readline()
    if not number.strip():
        refuse("kit-hash reads the kit number from standard input, for example: echo <kit number> | "
               "python maxgen_db.py kit-hash genealogy.db")
    print(kit_hash(ctx.con, number, getattr(a, "schemas", None)))


def cmd_session(ctx, a):
    name = a.name.strip()
    for pat in AGENT_NAME_BAD:
        if pat.search(name):
            refuse("an agent name holds no version or date (%r); put the version in --version" % name, "R-30")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,99}$", name):
        refuse("an agent name is letters, digits and . _ : @ - (got %r)" % name, "R-30")
    # the role and the name "person" belong to the person's own session in Probable Pedigree: a
    # command-line session that took them would look like the person in the change and action logs
    # (and would keep the person's longer undo time), and would share the person's agent id
    if re.sub(r"[^a-z]", "", str(a.role).lower()) == "person" or name.lower() == "person":
        refuse(PERSON_ROLE_WORDS, "R-14")
    dbid = ctx.database_id()
    prov = {"agent_id": "db:%s/%s" % (dbid, name), "agent_kind": a.kind, "role": a.role, "party": "db:%s" % dbid,
            "channel": "cli"}
    if a.run_id:
        prov["run_id"] = a.run_id
    if a.model_name:
        prov["model"] = {"name": a.model_name, "attested_by": "self_reported"}
        if a.model_provider:
            prov["model"]["provider"] = a.model_provider
        if a.model_version:
            prov["model"]["version"] = a.model_version
    for k, v in (("operator", a.operator), ("version", a.version), ("display_name", a.display_name)):
        if v:
            prov[k] = v
    errs = ctx.schemas.errors("provenance", prov)
    if errs:
        refuse("the session's provenance is incomplete: %s (an ai session needs --model-name and --run-id)"
               % "; ".join(errs), "R-14")
    sid = new_id()
    ctx.con.execute("INSERT INTO agent_session(session_id,provenance,started_at) VALUES (?,?,?)",
                    (sid, json.dumps(prov), now()))
    ctx.log("session", "%s %s" % (sid, prov["agent_id"]))
    ctx.as_session, ctx.session = sid, None
    ctx.require_session()               # the transaction stamp names the new session
    ctx.commit()
    print("OK  session %s (%s)" % (sid, prov["agent_id"]))
    print("MAXGEN_SESSION=%s" % sid)


# ------------------------------------------------------------------ reading: status, list, export, occurrence

def cmd_status(ctx, a):
    q = lambda s, *p: ctx.con.execute(s, p).fetchall()
    print("DATABASE  %s  (MAXGEN %s, kit %s, database id %s)" % (a.db, ctx.meta("maxgen_version"), __version__,
                                                                ctx.meta("database_id")))
    for kind in KEY:
        t = TABLE[kind]
        if kind in STAGED_KINDS:
            rows = q("SELECT stage, count(*) n FROM %s WHERE lifecycle='active' GROUP BY stage" % t)
            detail = "  ".join("%s=%d" % (r["stage"], r["n"]) for r in rows)
        elif kind == "task":
            rows = q("SELECT status, count(*) n FROM max_task GROUP BY status")
            detail = "  ".join("%s=%d" % (r["status"], r["n"]) for r in rows)
        else:
            rows = q("SELECT lifecycle, count(*) n FROM %s GROUP BY lifecycle" % t)
            detail = "  ".join("%s=%d" % (r["lifecycle"], r["n"]) for r in rows)
        total = q("SELECT count(*) n FROM %s" % t)[0]["n"]
        print("  %-17s %6d   %s" % (t, total, detail or "-"))
    rows = q("SELECT status, count(*) n FROM claim_index GROUP BY status")
    print("  %-17s %6d   %s" % ("claims", sum(r["n"] for r in rows), "  ".join("%s=%d" % (r["status"], r["n"]) for r in rows) or "-"))
    print("  %-17s %6d" % ("dead ends", q("SELECT count(*) n FROM dead_end")[0]["n"]))
    print("  %-17s %6d" % ("reviews", q("SELECT count(*) n FROM review")[0]["n"]))
    stale = [r["person_id"] for r in q("SELECT person_id FROM max_person WHERE lifecycle='active' AND is_living=1")
             if dead_by_rules(ctx, r["person_id"])]
    if stale:
        print("  stored living but the documents now show death or age (lower with the person's yes): %s"
              % ", ".join(stale))
    last = q("SELECT at, kind, detail FROM run_log ORDER BY id DESC LIMIT 1")
    if last:
        print("  last event  %s %s %s" % (last[0]["at"], last[0]["kind"], last[0]["detail"] or ""))
    fmt = tree_format(ctx.con)
    if fmt["bookkeeping"]:
        print("  tree file   MAXGEN %s, kit 1.2.0 bookkeeping: %d changes logged, %d actions"
              % (fmt["version"], fmt["seq"], q("SELECT count(*) n FROM action_log")[0]["n"]))
    else:
        print("  tree file   MAXGEN %s, made by an older kit: run `upgrade` before writing" % fmt["version"])


LIST_TABLES = DATA_TABLES + ("review", "dead_end", "run_log", "search_step", "id_redirect")


def _rows(ctx, table, a):
    if table not in LIST_TABLES:
        refuse("unknown table %s; one of %s" % (table, ", ".join(LIST_TABLES)))
    is_view = ctx.con.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (table,)).fetchone()
    where, args = [], []
    if getattr(a, "stage", None):
        if table not in [TABLE[k] for k in STAGED_KINDS]:
            refuse("--stage applies to %s only" % ", ".join(TABLE[k] for k in STAGED_KINDS))
        where.append("stage=?")
        args.append(a.stage)
    if getattr(a, "status", None):
        if table != "max_task":
            refuse("--status applies to max_task only")
        where.append("status=?")
        args.append(a.status)
    sql = "SELECT * FROM %s%s%s" % (table, (" WHERE " + " AND ".join(where)) if where else "",
                                    "" if is_view else " ORDER BY rowid")
    return ctx.con.execute(sql, args).fetchall()


def cmd_list(ctx, a):
    rows = _rows(ctx, a.table, a)
    for r in rows[: a.limit]:
        d = dict(r)
        label = d.get("display_name") or d.get("record_type") or d.get("task_type") or d.get("name") or \
            d.get("kind") or d.get("event_type") or d.get("group_kind") or d.get("canonical_form") or d.get("query") or ""
        state = d.get("stage") or d.get("status") or d.get("verdict") or d.get("lifecycle") or ""
        ident = next((d[k] for k in KEY.values() if d.get(k)), None) or d.get("id") or d.get("task_id")
        print("  %-38s %-10s %s" % (ident, state, label))
    print("  (%d of %d shown)" % (min(a.limit, len(rows)), len(rows)))


def _drop_private_notes(x):
    """Every notes list loses its private notes and its working notes (research, to-do, imported); every
    nested extensions block and imported credit is dropped too (9.4)."""
    if isinstance(x, dict):
        x.pop("extensions", None)
        x.pop("imported_attribution", None)
        if isinstance(x.get("notes"), list):
            kept = [n for n in x["notes"] if not (isinstance(n, dict) and (
                n.get("is_private") or n.get("note_type") in ("research", "to_do", "imported")))]
            if kept:
                x["notes"] = kept
            else:
                x.pop("notes")
        for v in x.values():
            _drop_private_notes(v)
    elif isinstance(x, list):
        for v in x:
            _drop_private_notes(v)


def shareable_doc(ctx, kind, doc):
    """The copy of one document a shareable export may hold (4.2), or None when it is private."""
    if private_now(ctx, kind, doc) or (kind == "person" and _person_private(ctx, doc["person_id"])):
        return None
    out = copy.deepcopy(doc)
    for ln, _c in list(iter_claims(kind, doc)):
        out[ln] = [c for c in out.get(ln) or [] if live_claim(c) and not claim_private(ctx, kind, doc, ln, c)]
        for c in out[ln]:
            c.pop("status_reason", None)        # written from a task, and tasks are always private
            c.pop("status_task_id", None)
        if not out[ln]:
            out.pop(ln)
    if kind == "relationship" and doc["kind"] == "family":
        out["children"] = [m for m in out.get("children") or []
                           if m.get("status", "active") == "active" and m["set_kind"] not in ADOPTION_SET_KINDS
                           and not m.get("is_private") and not m.get("is_living_flag")
                           and not person_living_now(ctx, m["child_id"])
                           and not _person_private(ctx, m["child_id"])]
        if not out["children"]:
            out.pop("children")
    if kind == "relationship" and doc["kind"] == "parent_child":
        out["lineage_assertions"] = [f for f in out.get("lineage_assertions") or [] if f.get("fact_type") not in ADOPTION_FACTS]
        if not out["lineage_assertions"]:
            return None
        # a link used only by a candidate (alternative_hypothesis) set is never shared (9.4)
        rid = low(doc["relationship_id"])
        uses = [m for m in memberships_of(ctx, doc["child_id"]) if rid in json.loads(m["parent_link_ids"])]
        if not any(m["set_kind"] != "alternative_hypothesis" for m in uses):
            return None
    if kind == "record" and doc.get("redistribution_license") == "all_rights_reserved":
        out.pop("transcription_parts", None)    # its owner keeps every right: the citation only
        out.pop("transcription_sha256", None)
    for c in out.get("saved_copies") or []:
        c["locations"] = [loc for loc in c.get("locations") or [] if loc.get("is_public")]
        if not c["locations"]:
            c.pop("locations")
    for f in ("archive_ref", "imported_attribution", "provenance", "extensions"):
        out.pop(f, None)
    lic = doc.get("redistribution_license")
    if out.get("attribution") and lic not in ("CC-BY", "CC-BY-SA"):
        out.pop("attribution")
    for ln, _c in list(iter_claims(kind, out)):
        for c in out.get(ln) or []:
            c.pop("imported_attribution", None)
    if out.get("source_url", "").lower().startswith("file:"):
        out.pop("source_url")
    _drop_private_notes(out)
    return out


def cmd_export(ctx, a):
    if a.shareable and not getattr(a, "stage", None) and a.table in DATA_TABLES and \
            KIND_OF_TABLE[a.table] in STAGED_KINDS:
        a.stage = "approved"        # a shareable export holds only what Quality Control has passed
    rows = _rows(ctx, a.table, a)
    if a.shareable and a.table in ("max_task", "max_dna"):
        refuse("%s is always private; a shareable export never holds tasks or DNA" % a.table)
    if a.shareable and a.table not in DATA_TABLES:
        refuse("a shareable export holds MAXGEN documents only")
    n = held = 0
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            d = dict(r)
            if "doc" not in d:
                f.write(json.dumps(d) + "\n")
                n += 1
                continue
            doc = json.loads(d["doc"])
            if a.shareable:
                kind = KIND_OF_TABLE[a.table]
                if kind in LIFECYCLE_KINDS and doc.get("lifecycle") != "active":
                    held += 1
                    continue
                doc = shareable_doc(ctx, kind, doc)
                if doc is None:
                    held += 1
                    continue
            f.write(canon(doc) + "\n")
            n += 1
    print("OK  %d row(s) -> %s%s" % (n, a.out, (" (%d withheld: living, private or unsure)" % held) if a.shareable else ""))


def cmd_occurrence(ctx, a):
    occ = ctx.resolve(a.id)
    row = ctx.row("event", occ)
    if not row:
        refuse("%s is not an occurrence here" % a.id, "R-04")
    doc = json.loads(row["doc"])
    ids = ctx.merged_into(occ)
    qs = ",".join("?" * len(ids))
    owner = event_owner(ctx, occ)
    print("OCCURRENCE %s  %s  owner=%s %s" % (occ, doc["event_type"], owner[0] or "-", owner[1] or ""))
    origin = doc.get("origin") or {}
    print("  origin: %s %s %s" % (origin.get("kind"), origin.get("record_id") or "", origin.get("record_event_ref") or ""))
    claims = ctx.con.execute("SELECT * FROM claim_index WHERE occurrence_id IN (%s) ORDER BY holder_id" % qs, list(ids)).fetchall()
    holders = set()
    for c in claims:
        kind, hdoc, ln, item = find_claim(ctx, c["assertion_id"])
        role = (item or {}).get("role", "principal" if ln in ("birth_assertions", "death_assertions") else "")
        when = date_display((item or {}).get("date")) if item else ""
        where = ((item or {}).get("place") or {}).get("place_as_written", "")
        print("  claim %s %-8s %s %s role=%s  %s  %s  record=%s" % (c["assertion_id"], c["status"], c["holder_table"],
                                                                   c["effective_holder_id"], role or "-", when, where,
                                                                   c["source_record_id"] or "-"))
        holders.add((c["holder_table"], c["effective_holder_id"]))
    cands = []
    for table, hid in holders:
        for c in ctx.con.execute("SELECT * FROM claim_index WHERE holder_table=? AND effective_holder_id=? AND "
                                 "(occurrence_id IS NULL OR occurrence_id NOT IN (%s)) AND fact_type=? AND status IN "
                                 "('proposed','proven','disputed')" % qs,
                                 [table, hid] + list(ids) + [doc["event_type"]]).fetchall():
            cands.append(c)
    for c in ctx.con.execute("SELECT * FROM claim_index WHERE occurrence_id IN (%s)" % qs, list(ids)).fetchall():
        for c2 in ctx.con.execute("SELECT * FROM claim_index WHERE holder_table=? AND effective_holder_id=? AND "
                                  "list_name=? AND assertion_id != ? AND status IN ('proposed','proven','disputed') AND "
                                  "(occurrence_id IS NULL OR occurrence_id NOT IN (%s))" % qs,
                                  [c["holder_table"], c["effective_holder_id"], c["list_name"], c["assertion_id"]] + list(ids)).fetchall():
            if c2["assertion_id"] not in [x["assertion_id"] for x in cands]:
                cands.append(c2)
    for c in cands:
        print("  candidate (same %s, another document; never merged): claim %s record=%s occurrence=%s"
              % (doc["event_type"], c["assertion_id"], c["source_record_id"] or "-", c["occurrence_id"] or "-"))


# ------------------------------------------------------------------ check and reindex

def cmd_reindex(ctx, a):
    before = current_index_rows(ctx)
    rebuild_indexes(ctx)
    after = current_index_rows(ctx)
    diffs = [t for t in INDEX_TABLES if {_normal(r) for r in before[t]} != {_normal(r) for r in after[t]}]
    ctx.log("reindex", ",".join(diffs) or "no differences")
    ctx.commit()
    print("OK  indexes rebuilt from the documents%s" % ((": differences found in " + ", ".join(diffs)) if diffs else
                                                        " (no differences)"))


def reference_problems(ctx, kind, doc):
    """Every reference of one stored document that names nothing here (Appendix C), and evidence labels
    that are not on the record named."""
    problems = []
    for row in REFERENCES:
        for path, target in row["paths"]:
            holder, _, dotted = path.partition(":")
            tkind = ref_target(target, kind)
            if holder not in ("*", kind) or tkind is None:
                continue
            for v in values_at(doc, [s for s in dotted.split(".") if s]):
                for x in (v if isinstance(v, list) else [v]):
                    if isinstance(x, str) and ID_RE.match(x) and low(x) != low(doc[KEY[kind]]):
                        if not ctx.row(tkind, ctx.resolve(x)) and not ctx.row(tkind, x):
                            problems.append("%s %s: %s names %s %s, which is not here"
                                            % (TITLE[kind], doc[KEY[kind]], dotted, tkind, x))
    for ln, c in iter_claims(kind, doc):
        ev = c.get("evidence") or {}
        if ev.get("basis") == "document":
            rec = ctx.doc("record", ev.get("source_record_id"))
            if rec:
                for key in ("mention_id", "subject_mention_id"):
                    if ev.get(key) and not mention_of(rec, ev[key]):
                        problems.append("%s %s: evidence %s %r is not on record %s"
                                        % (TITLE[kind], doc[KEY[kind]], key, ev[key], rec["record_id"]))
                if ev.get("record_event_ref") and not entry_of(rec, ev["record_event_ref"]):
                    problems.append("%s %s: evidence record_event_ref %r is not on record %s"
                                    % (TITLE[kind], doc[KEY[kind]], ev["record_event_ref"], rec["record_id"]))
    return problems


def check_problems(ctx):
    problems = []
    for kind in KEY:
        for r in ctx.con.execute("SELECT doc FROM %s" % TABLE[kind]).fetchall():
            doc = json.loads(r["doc"])
            try:
                stored_check(ctx, kind, doc)
            except StoredFormError as e:
                problems.append(str(e))
            problems += reference_problems(ctx, kind, doc)
    for r in ctx.con.execute("SELECT old_id FROM id_redirect WHERE reverted_at IS NULL").fetchall():
        try:
            ctx.resolve(r["old_id"])
        except Refused as e:
            problems.append(str(e))
    for r in ctx.con.execute("SELECT child_id, count(*) n, sum(is_preferred) p FROM membership_index WHERE "
                             "status='active' GROUP BY child_id").fetchall():
        live = [m for m in memberships_of(ctx, r["child_id"])]
        if live and sum(1 for m in live if m["is_preferred"]) != 1:
            problems.append("child %s has %d preferred live memberships (exactly one is required, R-20)"
                            % (r["child_id"], sum(1 for m in live if m["is_preferred"])))
    for r in ctx.con.execute("SELECT family_id, child_id, set_kind, count(*) n FROM membership_index WHERE status='active' "
                             "GROUP BY family_id, child_id, set_kind HAVING n > 1").fetchall():
        problems.append("child %s has %d live %s memberships in family %s (R-20)" % (r["child_id"], r["n"],
                                                                                   r["set_kind"], r["family_id"]))
    for r in ctx.con.execute("SELECT DISTINCT child_id FROM membership_index WHERE status='active' AND set_kind='birth'"
                             ).fetchall():
        if ctx.row("person", r["child_id"]) and competing_birth_parents(ctx, r["child_id"]) and \
                not conflict_task_of(ctx, r["child_id"]):
            problems.append("child %s has two birth parent sets (or two birth fathers/mothers) and no open or settled "
                            "resolve_conflict task (R-32)" % r["child_id"])
    problems += index_drift(ctx)
    problems += bookkeeping_problems(ctx.con)
    return problems


def bookkeeping_problems(con) -> list:
    """Kit 1.2.0: on a tree in the 2.1 format, every bookkeeping table, view and trigger is there, and
    nothing removed a row the kit never removes (a 'd' row in the change log is an alarm)."""
    fmt = tree_format(con)
    if fmt["minor"] < 1:
        return []                       # an older tree: `upgrade` adds the bookkeeping
    missing = missing_bookkeeping(con)
    if missing:
        return ["the kit 1.2.0 bookkeeping is incomplete (%d of its tables, views and triggers are missing, such as "
                "%s); something outside the kit changed the file; `upgrade` puts them back" % (len(missing), missing[0])]
    out = []
    for r in con.execute("SELECT tbl, count(*) n FROM change_log WHERE op='d' GROUP BY tbl ORDER BY tbl").fetchall():
        if r["tbl"] in NEVER_DELETED:
            out.append("something outside the kit removed %d row(s) from %s (the change log shows it); restore them "
                       "from a backup" % (r["n"], r["tbl"]))
    return out


# ------------------------------------------------------------------ MAXGEN-to-MAXGEN import (4.4 rule 13)

IMPORT_ORDER = ("task", "source", "name", "place", "group", "record", "event", "person", "relationship", "dna",
                "recognition")


def _same_content(a, b) -> bool:
    strip = lambda d: {k: v for k, v in d.items() if k != "updated_at"}
    return canon(strip(a)) == canon(strip(b))


def cmd_import_maxgen(ctx, a):
    """Bring in the documents another MAXGEN database exported (one <table>.jsonl file per table, made with
    `export`), keeping every id and the history: lifecycle and merge, split and distinct history, the tasks
    that made them and their provenance (party db:<their id>). Tasks come first, then the documents in kind
    order; id_redirect is rebuilt from the merge history; their proofs become origin foreign_maxgen (never
    accepted here) and a claim they had proven comes in as proposed. A document that is already here with
    the same content is skipped; one with different content is refused (never overwritten: bring the change
    in with add --replace, which is append-only, R-07). Everything lands staged for Quality Control."""
    task = require_task(ctx, a.task, purpose="a MAXGEN import", types=("import_file",), rule="R-24")
    payload = task.get("payload") or {}
    if payload.get("format") != "maxgen":
        refuse("import-maxgen runs under an import_file task whose payload.format is \"maxgen\"", "R-24")
    their = low(payload.get("source_database_id") or "")
    if not ID_RE.match(their):
        refuse("the import task's payload.source_database_id must be the other database's id (its `status` shows it)",
               "R-04")
    if their == low(ctx.database_id()):
        refuse("payload.source_database_id is this database; there is nothing to import")
    folder = Path(a.folder)
    docs = []
    for kind in IMPORT_ORDER:
        f = folder / (TABLE[kind] + ".jsonl")
        if not f.exists():
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                doc = json.loads(line)
            except ValueError as e:
                refuse("%s line %d is not JSON: %s" % (f.name, n, e), "R-01")
            if not isinstance(doc, dict) or KEY[kind] not in doc:
                refuse("%s line %d is not a %s" % (f.name, n, TITLE[kind]), "R-03")
            docs.append((kind, doc))
    if not docs:
        refuse("no max_*.jsonl files in %s; make them in the other database with `export <db> <table> "
               "<folder>/<table>.jsonl` for each table" % folder)
    added, skipped, adoption, imported = 0, 0, False, []
    for kind, doc in docs:
        problem = version_problem(doc.get("schema_version"))
        if problem:
            refuse("%s %s: %s" % (TITLE[kind], doc.get(KEY[kind]), problem), "R-02")
        ident = low(doc[KEY[kind]])
        doc[KEY[kind]] = ident
        if kind == "task":
            proof = (doc.get("result") or {}).get("proof")
            if proof:
                proof["origin"] = "foreign_maxgen"      # never accepted, reliable or proven here (4.3)
            prov = doc.get("provenance")
            if prov and not prov.get("party"):
                prov["party"] = "db:" + their
        for ln, c in iter_claims(kind, doc):
            if c.get("status") == "proven":
                c["status"] = "proposed"
                c["status_reason"] = ("proven in MAXGEN database %s; a proof from another database is never "
                                      "accepted here" % their)
                c["status_task_id"] = low(task["task_id"])
                c["status_at"] = now()
        row = ctx.row(kind, ident)
        if row is not None:
            if _same_content(json.loads(row["doc"]), doc):
                skipped += 1
                continue
            refuse("%s %s is already here with different content; a MAXGEN import never overwrites -- bring the "
                   "change in with `add --replace` (append-only, R-07)" % (TITLE[kind], ident), "R-07")
        errs = ctx.schemas.errors(kind, doc)
        if errs:
            raise Refused("%s %s does not match %s (R-01)\n   - %s" % (TITLE[kind], ident, FILE[kind],
                                                                      "\n   - ".join(errs)))
        if kind == "record" and doc.get("lifecycle", "active") == "active":
            check_record(ctx, doc, None)                # R-09 and the labels, for live records only
        if kind == "relationship":
            adoption = adoption or any(m.get("set_kind") == "adoptive" and m.get("status", "active") == "active"
                                       for m in doc.get("children") or []) or \
                any(f.get("fact_type") == "adoptive_parent" for f in live_facts(doc))
        ctx.store(kind, doc, new=True, added_by=ctx.agent_id)
        imported.append((kind, ident))
        added += 1
    if adoption and not person_accepted(ctx, task["task_id"]):
        refuse("the import holds an adoption; recording it needs the person's yes on the import task first (R-31)",
               "R-31")
    # id_redirect, rebuilt from the merge history the documents carry (4.1)
    redirects = 0
    for kind, doc in docs:
        for e in doc.get("merge_history") or []:
            old = low(e["merged_id"])
            if ctx.con.execute("SELECT 1 FROM id_redirect WHERE old_id=? AND new_id=? AND merged_at=?",
                               (old, low(doc[KEY[kind]]), e.get("merged_at") or "")).fetchone():
                continue
            if not e.get("reverted_at") and ctx.con.execute(
                    "SELECT 1 FROM id_redirect WHERE old_id=? AND reverted_at IS NULL", (old,)).fetchone():
                refuse("%s %s is already forwarded to another document here" % (TITLE[kind], old), "R-28")
            ctx.con.execute("INSERT INTO id_redirect(old_id,kind,new_id,task_id,merged_at,reverted_at,revert_task_id) "
                            "VALUES (?,?,?,?,?,?,?)", (old, kind, low(doc[KEY[kind]]), low(e.get("task_id")),
                                                       e.get("merged_at") or now(), e.get("reverted_at"),
                                                       low(e.get("revert_task_id"))))
            redirects += 1
    rebuild_indexes(ctx)
    refresh_event_owners(ctx)
    problems = []
    for kind, ident in imported:
        problems += reference_problems(ctx, kind, ctx.doc(kind, ident))
    if problems:
        refuse("the imported files name documents that are neither here nor in the files (R-04):\n   - %s"
               % "\n   - ".join(problems[:20]), "R-04")
    # floors are left as the other database stored them, so a second import of the same files is skipped
    # line by line; living and privacy are worked out when read (4.2), never from these stored values
    ctx.log("import-maxgen", "%s from db:%s: %d added, %d skipped, %d redirects" % (a.folder, their, added, skipped,
                                                                                    redirects))
    ctx.commit()
    print("OK  import from MAXGEN database %s: %d document(s) added (staged for Quality Control), %d already here, "
          "%d merge redirect(s) rebuilt" % (their, added, skipped, redirects))


def cmd_check(ctx, a):
    problems = check_problems(ctx)
    if problems:
        print("CHECK: %d problem(s)" % len(problems))
        for p in problems:
            print("   - " + p)
        sys.exit(1)
    print("OK  check: stored forms, references, redirects, parent sets and indexes are all sound")


# ------------------------------------------------------------------ the pedigree chart and the two pages (10.2)

# Plain words for schema values on every page (shared with probable_pedigree.py); a schema word appears only
# in a tooltip.
PLAIN_WORDS = {
    "status": {"pending": "Waiting to start", "processing": "Being worked on", "done": "Finished",
               "failed": "Could not be run", "escalated": "Needs you", "on_hold": "Paused",
               "cancelled": "Cancelled"},
    "verdict": {"key_finding": "Found it", "usable": "Found a lead", "inconclusive": "Found something, not settled",
                "ruled_out": "Proved it is someone else", "dead_end": "Searched, found nothing",
                "inaccessible": "Could not search there", "infra": "Support work", "other": "Other result"},
    "task_kind": {"extract_record": "Look for a document", "find_original": "Look for the original document",
                  "fetch_internet_archive": "Look in the Internet Archive",
                  "validate_record": "A Quality Control check", "merge_person": "Are these two people one person?",
                  "build_tree": "Link family members", "resolve_conflict": "Settle documents that disagree",
                  "judge_review": "A question for you", "import_file": "Bring in a family-tree file",
                  "ingest_to_qdrant": "Support work", "cost_report": "Support work", "other": "Other work"},
    "record": {"birth_certificate": "Birth certificate", "birth_register": "Birth register entry",
               "death_certificate": "Death certificate", "death_register": "Death register entry",
               "marriage_certificate": "Marriage certificate", "marriage_register": "Marriage register entry",
               "marriage_license": "Marriage licence", "marriage_banns": "Marriage banns",
               "divorce_record": "Divorce record", "census_household": "Census entry",
               "parish_register": "Church register entry", "probate_record": "Will or probate record",
               "will": "Will", "military_record": "Military record", "immigration_record": "Immigration record",
               "passenger_list": "Passenger list", "naturalization_record": "Citizenship record",
               "land_deed": "Land deed", "land_patent": "Land grant", "tax_record": "Tax record",
               "court_record": "Court record", "adoption_record": "Adoption record", "gravestone": "Gravestone",
               "obituary": "Obituary", "newspaper_article": "Newspaper article", "city_directory": "Town directory",
               "school_record": "School record", "church_record": "Church record",
               "institution_record": "Institution record", "employment_record": "Work record",
               "voter_list": "Voters' list", "family_bible": "Family Bible page", "letter": "Letter",
               "oral_history": "Someone's spoken account", "photograph": "Photograph",
               "media_item": "Picture or recording", "map": "Map", "tree_import": "A family-tree file",
               "wikidata_entity": "Wikidata entry", "open_library_work": "Book (Open Library)",
               "dpla_item": "Library item (DPLA)", "gazetteer_entry": "Gazetteer entry",
               "dna_match_list": "DNA match list", "other": "Other document"},
    "licence_short": {"CC0": "Free to reuse", "CC-BY": "Reuse with credit", "CC-BY-SA": "Reuse with credit, share alike",
                      "public-domain": "No copyright", "all_rights_reserved": "All rights reserved",
                      "tier2-private": "Private"},
    "relationship_kind": {"family": "Family", "parent_child": "Parent and child", "association": "Tie between two people"},
    "table": {"record": "document", "person": "person", "relationship": "family link", "place": "place",
              "group": "group", "dna": "DNA test"},
}


def plain_word(kind, value) -> str:
    """The plain word for one schema value (the raw value when there is none)."""
    return PLAIN_WORDS.get(kind, {}).get(value) or str(value if value is not None else "")


def pw(kind, value):
    """A table cell shown in plain words, with the schema value in its tooltip (html_table)."""
    return ("__plain__", plain_word(kind, value), "" if value is None else str(value))


CONF_BANDS = [
    (0.95, "near-certain", "Near-certain"),
    (0.85, "strong", "Strong"),
    (0.70, "good", "Good"),
    (0.50, "moderate", "Moderate"),
    (0.30, "weak", "Weak"),
    (0.00, "speculative", "Speculative"),
]
BAND_COLOUR = {"near-certain": "#1b7f3b", "strong": "#3a9a5c", "good": "#c8a24a", "moderate": "#d9822b",
               "weak": "#c0392b", "speculative": "#8e44ad", "none": "#9a9a9a"}
BOX_W, BOX_H, COL_GAP, ROW_H, PAD = 170, 56, 70, 64, 20
MAX_GENS, DEFAULT_GENS = 8, 5
LINE_WORDS = {"b": "solid: birth parent", "l": "dashed: adoptive, step, foster or guardian",
              "u": "dotted: kind of parent not stated"}


def conf_band(c) -> str:
    if c is None:
        return "none"
    for threshold, cls, _label in CONF_BANDS:
        if c >= threshold:
            return cls
    return "speculative"


def ancestors(persons, root):
    seen, stack = set(), [root]
    while stack:
        p = persons.get(stack.pop())
        if not p:
            continue
        for q in (p["f"], p["m"]):
            if q and q in persons and q not in seen:
                seen.add(q)
                stack.append(q)
    return len(seen)


def default_root(persons):
    if not persons:
        return None
    return min(persons, key=lambda pid: (-ancestors(persons, pid), persons[pid]["name"].lower(), pid))


def ahnentafel(persons, root, gens):
    """{ahnentafel number: person_id}; root = 1, father of n = 2n, mother of n = 2n+1. The walk
    stops when an id repeats on the current path (a loop is a data error)."""
    ahn = {}

    def walk(pid, n, depth, path):
        if not pid or depth >= gens or pid not in persons or pid in path:
            return
        ahn[n] = pid
        walk(persons[pid]["f"], 2 * n, depth + 1, path | {pid})
        walk(persons[pid]["m"], 2 * n + 1, depth + 1, path | {pid})
    walk(root, 1, 0, frozenset())
    return ahn


def layout(ahn, gens):
    slots = 1 << (gens - 1)
    pos = {}
    for n in ahn:
        g = n.bit_length() - 1
        idx = n - (1 << g)
        cy = (2 * idx + 1) * slots * ROW_H // (1 << (g + 1))
        pos[n] = (PAD + g * (BOX_W + COL_GAP), PAD + cy - BOX_H // 2)
    return pos, PAD * 2 + gens * (BOX_W + COL_GAP) - COL_GAP, PAD * 2 + slots * ROW_H


def date_line(p):
    parts = []
    if p.get("b") is not None:
        parts.append("b. %d" % p["b"])
    if p.get("d") is not None:
        parts.append("d. %d" % p["d"])
    return " - ".join(parts)


def short_name(name):
    return name if len(name) <= 26 else name[:25] + "..."


def box_conf(persons, ahn, n):
    """Box colour: the confidence of the link that placed this person; the root uses its name."""
    pid = ahn[n]
    if n == 1:
        return persons[pid].get("c")
    child = persons.get(ahn.get(n // 2))
    if not child:
        return None
    return child.get("fc") if n % 2 == 0 else child.get("mc")


ROLE_WORD = {"father": "Father", "mother": "Mother"}


def slot_role_words(persons, ahn):
    """{ahnentafel number: "Mother"|"Father"} for a parent box whose role does not match its slot: two
    mothers or two fathers, or a mother in the first slot (9.2: the box shows each one's role word
    instead of assuming a father)."""
    out = {}
    for n in ahn:
        p = persons[ahn[n]]
        fr, mr = p.get("fr"), p.get("mr")
        same = fr in ROLE_WORD and fr == mr
        if 2 * n in ahn and fr in ROLE_WORD and (fr == "mother" or same):
            out[2 * n] = ROLE_WORD[fr]
        if 2 * n + 1 in ahn and mr in ROLE_WORD and (mr == "father" or same):
            out[2 * n + 1] = ROLE_WORD[mr]
    return out


def svg_tree(persons, ahn, gens, owner):
    pos, width, height = layout(ahn, gens)
    role_words = slot_role_words(persons, ahn)
    out = ['<svg id="tree" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-label="Pedigree chart">'
           % (width, height, width, height)]
    for n in sorted(ahn):
        p = persons[ahn[n]]
        for pn, key in ((2 * n, "fk"), (2 * n + 1, "mk")):
            if pn in ahn:
                style = p.get(key) or "u"
                if not owner and style == "l":
                    style = "u"
                x1, y1 = pos[n][0] + BOX_W, pos[n][1] + BOX_H // 2
                x2, y2 = pos[pn][0], pos[pn][1] + BOX_H // 2
                out.append('<path class="link lk-%s" d="M%d %dH%dV%dH%d"/>' % (style, x1, y1, x1 + COL_GAP // 2, y2, x2))
    for n in sorted(ahn):
        p = persons[ahn[n]]
        x, y = pos[n]
        band = conf_band(box_conf(persons, ahn, n))
        tags = []
        if n in role_words:
            tags.append(role_words[n])
        if p.get("u"):
            tags.append("(role unknown)")
        if owner and p.get("living"):
            tags.append("living")
        if owner and p.get("sets", 0) > 1:
            tags.append("%d parent sets" % p["sets"])
        if owner and p.get("waiting"):
            tags.append("parents waiting for a check")
        if owner and p.get("note"):
            tags.append(p["note"])
        out.append('<g class="node%s" data-id="%s" data-ahn="%d"><title>%s</title>'
                   '<rect x="%d" y="%d" width="%d" height="%d" rx="4" class="band-%s" stroke="%s"/>'
                   '<text class="t1" x="%d" y="%d">%s</text><text class="t2" x="%d" y="%d">%s</text>'
                   '<text class="t3" x="%d" y="%d">%s</text></g>'
                   % (" root" if n == 1 else "", esc(p["id"]), n, esc(p["name"] + " - " + p["id"]),
                      x, y, BOX_W, BOX_H, band, BAND_COLOUR[band],
                      x + 8, y + 18, esc(short_name(p["name"])), x + 8, y + 33, esc(date_line(p)),
                      x + 8, y + 47, esc(" - ".join(tags))))
    out.append("</svg>")
    return "".join(out)


def tree_json(persons, root, gens, owner):
    """The trimmed projection the script works from -- never whole documents."""
    proj = {}
    for pid, p in persons.items():
        e = {"n": p["name"], "b": p.get("b"), "d": p.get("d"), "f": p["f"], "m": p["m"], "c": p.get("c"),
             "fc": p.get("fc"), "mc": p.get("mc"),
             "fk": (p.get("fk") if owner or p.get("fk") != "l" else "u") if p["f"] else None,
             "mk": (p.get("mk") if owner or p.get("mk") != "l" else "u") if p["m"] else None,
             "l": bool(p.get("living")) if owner else False}
        if p.get("u"):
            e["u"] = 1
        if p["f"] and p.get("fr") in ROLE_WORD:
            e["fr"] = p["fr"]
        if p["m"] and p.get("mr") in ROLE_WORD:
            e["mr"] = p["mr"]
        if owner:
            e["s"] = p.get("sets", 0)
            if p.get("waiting"):
                e["w"] = 1
        proj[pid] = e
    txt = json.dumps({"persons": proj, "root": root, "gens": gens}, ensure_ascii=True, separators=(",", ":"))
    return txt.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


TREE_TEMPLATE = ('<template id="tree-tpl"><svg><g class="node"><title></title><rect rx="4"/>'
                 '<text class="t1"></text><text class="t2"></text><text class="t3"></text></g>'
                 '<path class="link"/></svg></template>')

TREE_SCRIPT = r"""<script>
(function(){
'use strict';
var raw=document.getElementById('tree-data'),host=document.getElementById('tree-host'),
    tpl=document.getElementById('tree-tpl');
if(!raw||!host||!tpl||!tpl.content)return;
var data;try{data=JSON.parse(raw.textContent);}catch(e){return;}
var P=data.persons||{},root0=data.root,root=root0,gens=data.gens||5;
if(!P[root])return;
var W=170,H=56,GAP=70,RH=64,PAD=20,MAXG=8;
var BANDS=[[0.95,'near-certain'],[0.85,'strong'],[0.70,'good'],[0.50,'moderate'],[0.30,'weak'],[0,'speculative']];
var COLOUR={'near-certain':'#1b7f3b',strong:'#3a9a5c',good:'#c8a24a',moderate:'#d9822b',weak:'#c0392b',speculative:'#8e44ad',none:'#9a9a9a'};
function str(s){return s===null||s===undefined?'':String(s);}
function band(c){if(c===null||c===undefined)return 'none';for(var i=0;i<BANDS.length;i++){if(c>=BANDS[i][0])return BANDS[i][1];}return 'speculative';}
function dates(p){var a=[];if(p.b!==null&&p.b!==undefined)a.push('b. '+p.b);if(p.d!==null&&p.d!==undefined)a.push('d. '+p.d);return a.join(' - ');}
function shortName(n){var a=Array.from(str(n));return a.length<=26?a.join(''):a.slice(0,25).join('')+'...';}
function walk(ahn,id,n,depth,path){if(!id||depth>=gens||!P[id]||path[id])return;ahn[n]=id;var q=Object.assign({},path);q[id]=1;walk(ahn,P[id].f,2*n,depth+1,q);walk(ahn,P[id].m,2*n+1,depth+1,q);}
var nodeT=tpl.content.querySelector('g.node'),linkT=tpl.content.querySelector('path.link'),svgT=tpl.content.querySelector('svg');
var svg=null,vb={x:0,y:0,w:1,h:1},size={w:1,h:1};
function setVB(){if(svg)svg.setAttribute('viewBox',vb.x+' '+vb.y+' '+vb.w+' '+vb.h);}
function fit(){vb={x:0,y:0,w:size.w,h:size.h};setVB();}
function build(){
  var ahn={};walk(ahn,root,1,0,{});
  var slots=Math.pow(2,gens-1),pos={},keys=Object.keys(ahn).map(Number).sort(function(a,b){return a-b;});
  keys.forEach(function(n){var g=31-Math.clz32(n),idx=n-Math.pow(2,g);var cy=(2*idx+1)*slots*RH/Math.pow(2,g+1);pos[n]=[PAD+g*(W+GAP),PAD+cy-H/2];});
  size={w:PAD*2+gens*(W+GAP)-GAP,h:PAD*2+slots*RH};
  var s=svgT.cloneNode(false);
  s.setAttribute('id','tree');s.setAttribute('width',size.w);s.setAttribute('height',size.h);
  s.setAttribute('role','img');s.setAttribute('aria-label','Pedigree chart');
  keys.forEach(function(n){[[2*n,'fk'],[2*n+1,'mk']].forEach(function(t){var pn=t[0];if(!(pn in ahn))return;
    var x1=pos[n][0]+W,y1=pos[n][1]+H/2,x2=pos[pn][0],y2=pos[pn][1]+H/2;
    var l=linkT.cloneNode(true);l.setAttribute('class','link lk-'+(P[ahn[n]][t[1]]||'u'));l.setAttribute('d','M'+x1+' '+y1+'H'+(x1+GAP/2)+'V'+y2+'H'+x2);s.appendChild(l);});});
  keys.forEach(function(n){var id=ahn[n],p=P[id],g=nodeT.cloneNode(true),x=pos[n][0],y=pos[n][1];
    var c=n===1?p.c:(n%2===0?P[ahn[Math.floor(n/2)]].fc:P[ahn[Math.floor(n/2)]].mc);var b=band(c);
    g.setAttribute('class','node'+(n===1?' root':''));g.setAttribute('data-id',id);g.setAttribute('data-ahn',n);
    g.querySelector('title').textContent=str(p.n)+' - '+id;
    var r=g.querySelector('rect');r.setAttribute('x',x);r.setAttribute('y',y);r.setAttribute('width',W);r.setAttribute('height',H);
    r.setAttribute('class','band-'+b);r.setAttribute('stroke',COLOUR[b]);
    var tags=[],ch=n>1?P[ahn[Math.floor(n/2)]]:null,rw={father:'Father',mother:'Mother'};
    if(ch){var mine=n%2===0?ch.fr:ch.mr,other=n%2===0?ch.mr:ch.fr;
      if(rw[mine]&&((n%2===0&&mine==='mother')||(n%2===1&&mine==='father')||mine===other))tags.unshift(rw[mine]);}
    if(p.u)tags.push('(role unknown)');if(p.l)tags.push('living');if(p.s&&p.s>1)tags.push(p.s+' parent sets');if(p.w)tags.push('parents waiting for a check');
    var t1=g.querySelector('text.t1'),t2=g.querySelector('text.t2'),t3=g.querySelector('text.t3');
    t1.setAttribute('x',x+8);t1.setAttribute('y',y+18);t1.textContent=shortName(p.n);
    t2.setAttribute('x',x+8);t2.setAttribute('y',y+33);t2.textContent=dates(p);
    t3.setAttribute('x',x+8);t3.setAttribute('y',y+47);t3.textContent=tags.join(' - ');
    s.appendChild(g);});
  var old=host.querySelector('svg');
  if(old)host.replaceChild(s,old);else host.appendChild(s);
  svg=s;fit();
  var seen={};keys.forEach(function(n){seen[ahn[n]]=1;});var np=Object.keys(seen).length;
  cap.textContent=str(P[root].n)+' - '+gens+' generation'+(gens===1?'':'s')+' - '+np+(np===1?' person shown':' people shown')+' - drag to pan, wheel to zoom, click a box to re-root';
}
var bar=document.createElement('div');bar.className='treebar';
function btn(txt,fn){var b=document.createElement('button');b.type='button';b.textContent=txt;b.addEventListener('click',fn);bar.appendChild(b);return b;}
function zoomAt(k,px,py){if(px===null){px=vb.x+vb.w/2;py=vb.y+vb.h/2;}vb.x=px-(px-vb.x)*k;vb.y=py-(py-vb.y)*k;vb.w*=k;vb.h*=k;setVB();}
btn('+',function(){zoomAt(1/1.25,null,null);});
btn('-',function(){zoomAt(1.25,null,null);});
btn('Reset',fit);
btn('Back to '+str(P[root0].n),function(){root=root0;build();});
var lab=document.createElement('label');lab.textContent='Generations ';
var sel=document.createElement('select');
for(var i=1;i<=MAXG;i++){var o=document.createElement('option');o.value=String(i);o.textContent=String(i);if(i===gens)o.selected=true;sel.appendChild(o);}
sel.addEventListener('change',function(){var v=parseInt(sel.value,10);gens=(v>=1&&v<=MAXG)?v:gens;build();});
lab.appendChild(sel);bar.appendChild(lab);
var cap=document.createElement('span');cap.className='treecap';bar.appendChild(cap);
host.parentNode.insertBefore(bar,host);
host.className+=' js';
function point(ev){var r=host.getBoundingClientRect();var sc=Math.max(vb.w/r.width,vb.h/r.height);
  var ox=(r.width-vb.w/sc)/2,oy=(r.height-vb.h/sc)/2;return {x:vb.x+(ev.clientX-r.left-ox)*sc,y:vb.y+(ev.clientY-r.top-oy)*sc,sc:sc};}
var drag=null;
host.addEventListener('pointerdown',function(ev){if(ev.button!==0&&ev.pointerType==='mouse')return;drag={id:ev.pointerId,x:ev.clientX,y:ev.clientY,vx:vb.x,vy:vb.y,moved:0,target:ev.target};
  if(host.setPointerCapture){try{host.setPointerCapture(ev.pointerId);}catch(e){}}ev.preventDefault();});
host.addEventListener('pointermove',function(ev){if(!drag||drag.id!==ev.pointerId)return;var sc=point(ev).sc;
  var dx=ev.clientX-drag.x,dy=ev.clientY-drag.y;drag.moved=Math.max(drag.moved,Math.abs(dx)+Math.abs(dy));
  vb.x=drag.vx-dx*sc;vb.y=drag.vy-dy*sc;setVB();});
function endDrag(ev){if(!drag||drag.id!==ev.pointerId)return;var d=drag;drag=null;
  if(d.moved<4){var g=d.target;while(g&&g!==host&&!(g.getAttribute&&g.getAttribute('data-id')))g=g.parentNode;
    if(g&&g!==host){var id=g.getAttribute('data-id');if(P[id]&&id!==root){root=id;build();}}}}
host.addEventListener('pointerup',endDrag);host.addEventListener('pointercancel',function(ev){drag=null;});
host.addEventListener('wheel',function(ev){ev.preventDefault();var p=point(ev);zoomAt(ev.deltaY<0?1/1.2:1.2,p.x,p.y);},{passive:false});
build();
})();
</script>"""

# The shareable page never names parent sets: its script carries no badge code at all (9.4).
SHAREABLE_SCRIPT = TREE_SCRIPT.replace("if(p.s&&p.s>1)tags.push(p.s+' parent sets');", "").replace(
    "if(p.w)tags.push('parents waiting for a check');", "").replace("if(p.l)tags.push('living');", "")
assert SHAREABLE_SCRIPT.count("tags.push") == 1

PAGE_CSS = """
body{font-family:Georgia,serif;margin:0;background:#f6f3ec;color:#1d1a14;line-height:1.45}
header{background:#1d1a14;color:#f6f3ec;padding:18px 28px}header h1{margin:0;font-size:1.3rem;letter-spacing:.04em}
header p{margin:4px 0 0;opacity:.7;font:14px system-ui,sans-serif}
.banner{margin:10px 0 0;padding:8px 12px;border-radius:4px;font:600 14px system-ui,sans-serif;opacity:1}
.banner.private{background:#7a1f1f;color:#fff}.banner.share{background:#2f5d3a;color:#fff}
main{max-width:1100px;margin:0 auto;padding:20px 28px 60px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0 28px}
.tile{background:#fff;border:1px solid #d9d2c2;border-radius:6px;padding:12px 14px}
.tile b{display:block;font-size:1.7rem;color:#8a6a1f}.tile span{font:12px system-ui,sans-serif;color:#555}
.tile em{display:block;font:13px/1.3 system-ui,sans-serif;color:#333;margin-top:4px;font-style:normal}
h2{font-size:1.05rem;margin:28px 0 8px;border-bottom:2px solid #c8a24a;padding-bottom:4px}
table{width:100%;border-collapse:collapse;background:#fff;font:13px system-ui,sans-serif}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #eae4d6;vertical-align:top;word-break:break-word}
th{background:#f0ebe0;font-weight:600}td:last-child{color:#777;font-family:ui-monospace,monospace;font-size:11px}
td.plain{color:#1d1a14;font-family:system-ui,sans-serif;font-size:13px}
.empty{color:#777;font:13px system-ui,sans-serif}
.note{font:12px system-ui,sans-serif;color:#555;margin:6px 0 0}
.treewrap{overflow:auto;background:#fff;border:1px solid #d9d2c2;border-radius:6px;max-height:80vh;margin-top:8px}
.treewrap.js{overflow:hidden;touch-action:none;cursor:grab;height:70vh}
.treewrap.js svg{width:100%;height:100%;display:block}
.node rect{fill:#fffdf7;stroke-width:2}.node.root rect{fill:#fbf3dc}
.node text{font:12px system-ui,sans-serif;fill:#1d1a14;pointer-events:none}
.node text.t1{font-weight:600;font-size:13px}.node text.t3{font-size:10px;font-style:italic;fill:#7a1f1f}
.link{fill:none;stroke:#9c8f74;stroke-width:1.5}.lk-l{stroke-dasharray:7 4}.lk-u{stroke-dasharray:2 3}
.treebar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font:13px system-ui,sans-serif;margin-top:8px}
.treebar button{font:13px system-ui,sans-serif;padding:3px 10px;border:1px solid #b8ad96;background:#fff;border-radius:4px;cursor:pointer}
.treebar select{font:13px system-ui,sans-serif}.treecap{color:#555}
.legend{font:12px system-ui,sans-serif;color:#555;margin:8px 0 0}
.legend i{display:inline-block;width:14px;height:10px;border:2px solid;border-radius:2px;vertical-align:middle;margin:0 4px 0 10px;background:#fffdf7}
"""


def legend_html(owner):
    items = "".join('<i style="border-color:%s"></i>%s (%s)' % (BAND_COLOUR[cls], esc(label),
                    "no score" if cls == "none" else "%.2f+" % th)
                    for th, cls, label in CONF_BANDS + [(0, "none", "Grey")])
    lines = ("solid: birth parent; dashed: adoptive, step, foster or guardian; dotted: kind of parent not stated"
             if owner else "solid: birth parent; dotted: parent")
    return ('<p class="legend">Box colour = how sure the link to this person is:%s</p>'
            '<p class="legend">Lines: %s.</p>' % (items, esc(lines)))


def tree_section(persons, root, gens, owner):
    if not root or root not in persons:
        return '<h2>The pedigree - ancestor chart</h2><p class="empty">no one to chart yet</p>'
    ahn = ahnentafel(persons, root, gens)
    return ("<h2>The pedigree - ancestor chart</h2>"
            '<p class="note">Root: %s - %d generation%s - %s shown. Main parents. Root at the left, ancestors to the '
            "right. Click a box to re-root (needs JavaScript); with scripts off the chart is still complete.</p>"
            '<div class="treewrap" id="tree-host">%s</div>%s%s'
            '<script type="application/json" id="tree-data">%s</script>%s'
            % (esc(persons[root]["name"]), gens, "" if gens == 1 else "s", n_people(len(set(ahn.values()))),
               svg_tree(persons, ahn, gens, owner), legend_html(owner), TREE_TEMPLATE,
               tree_json(persons, root, gens, owner), TREE_SCRIPT if owner else SHAREABLE_SCRIPT))


PRIVATE_ROW = "(private record withheld)"
LOCAL_FILE = "(local file on the owner's computer)"


def sources_section(ctx, persons, root, gens, owner):
    """Every record a charted person's live claims cite. On the shareable page a private record is
    never printed (only one withheld row) and a credit is kept only when the licence requires it."""
    ahn = ahnentafel(persons, root, gens) if root in persons else {}
    positions = {}
    for n, pid in sorted(ahn.items()):
        positions.setdefault(pid, []).append(n)
    order = list(positions) + [pid for pid in sorted(persons, key=lambda x: (persons[x]["name"].lower(), x))
                               if pid not in positions]
    rows, hidden = [], 0
    for pid in order:
        p = persons[pid]
        n = ", ".join(str(x) for x in positions.get(pid, []))
        recs = set()
        for ln, c, h in effective_claims(ctx, "person", pid):
            if not owner and claim_private(ctx, "person", h, ln, c):
                continue
            ev = c.get("evidence") or {}
            if ev.get("source_record_id"):
                recs.add(ctx.resolve(ev["source_record_id"]))
        if not recs:
            rows.append((n, p["name"], "(no records cited)", "", "", "", ""))
            continue
        for rid in sorted(recs):
            rec = ctx.doc("record", rid)
            if not rec:
                continue
            if not owner and record_private(ctx, rid):
                hidden += 1
                rows.append((n, p["name"], PRIVATE_ROW, "", "", "", ""))
                continue
            credit = ""
            if rec["redistribution_license"] in ("CC-BY", "CC-BY-SA"):
                att = rec.get("attribution") or {}
                credit = att.get("contributor_name") or "(credit missing)"
                if att.get("source_organization"):
                    credit += " (%s)" % att["source_organization"]
            elif owner and (rec.get("attribution") or {}).get("contributor_name"):
                credit = rec["attribution"]["contributor_name"]
            src = str(rec.get("source_url") or rec.get("citation_as_written") or rec.get("repository_as_written") or "")
            if not owner and src.lower().startswith("file:"):
                src = LOCAL_FILE
            rows.append((n, p["name"], pw("record", rec["record_type"]), pw("licence_short", rec["redistribution_license"]),
                         src, credit, rid))
    if not rows:
        body = '<p class="empty">nothing yet</p>'
    else:
        body = ("<table><thead><tr><th>#</th><th>person</th><th>document</th><th>licence</th>"
                "<th>source (text, not a link)</th><th>credit</th><th>document id</th></tr></thead><tbody>"
                + "".join("<tr>%s</tr>" % "".join(_cell(x, "plain" if i < 6 else None) for i, x in enumerate(row))
                          for row in rows)
                + "</tbody></table>")
    if hidden:
        body += ('<p class="note">%d private record%s withheld from this copy (not for redistribution, or may name '
                 'a living person).</p>' % (hidden, "" if hidden == 1 else "s"))
    return ("<h2>Sources and credit</h2>"
            '<p class="note"># = position in the chart (1 = root, father of n = 2n, mother = 2n+1). CC-BY and CC-BY-SA '
            "records legally require the credit shown. Addresses are written out as text; copy one to visit it.</p>"
            + body)


def page_html(title, subtitle, banner, body):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>'
            "<style>%s</style></head><body><header><h1>%s</h1><p>%s</p>%s</header><main>%s</main></body></html>"
            % (esc(title), PAGE_CSS, esc(title), subtitle, banner, body))


def _cell(c, cls=None):
    attr = ' class="%s"' % cls if cls else ""
    if isinstance(c, tuple) and len(c) == 3 and c[0] == "__plain__":
        return '<td%s title="%s">%s</td>' % (attr, esc(c[2]), esc(c[1]))
    return "<td%s>%s</td>" % (attr, esc(c))


def html_table(headers, rows):
    if not rows:
        return '<p class="empty">nothing yet</p>'
    head = "".join("<th>%s</th>" % esc(x) for x in headers)
    body = "".join("<tr>%s</tr>" % "".join(_cell(c) for c in r) for r in rows)
    return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (head, body)


def cmd_portal(ctx, a):
    """Two self-contained pages: the owner's page (everything) and pedigree_shareable.html (the only
    page meant for others: living, private and unsure people, private records, adoptions and all
    DNA withheld)."""
    out = Path(a.out)
    share = Path(a.shareable_out) if a.shareable_out else out.parent / "pedigree_shareable.html"
    if out.resolve() == share.resolve():
        refuse("the owner page and the shareable page must be two different files (both are %s)" % out)
    gens = a.gens if a.gens is not None else DEFAULT_GENS
    if not 1 <= gens <= MAX_GENS:
        clamped = max(1, min(MAX_GENS, gens))
        print("NOTE: --gens %d is outside 1..%d; using %d" % (gens, MAX_GENS, clamped))
        gens = clamped
    q = lambda s, *p: ctx.con.execute(s, p).fetchall()
    persons = chart_persons(ctx, "preferred")
    if a.root:
        rid = ctx.resolve(a.root)
        row = ctx.row("person", rid)
        if not row:
            refuse("--root %s is not a person_id in this database" % a.root)
        if row["stage"] != "approved":
            refuse("--root %s is not approved (it is %s); only approved people can be charted" % (a.root, row["stage"]))
        root = rid
    else:
        root = default_root(persons)
    shared = chart_persons(ctx, "preferred", shareable=True)
    withheld = len(persons) - len(shared)
    share_root = root if root in shared else None
    if root and share_root is None:
        share_root = default_root(shared)
        print("NOTE: shareable page: root %s is withheld (living, private, or unsure); using %s"
              % (root, share_root or "nobody -- no one can be shared yet"))
    private_copy = withheld > 0 or q("SELECT count(*) n FROM max_dna")[0]["n"] > 0
    tiles = []
    for t, label in (("max_record", "documents"), ("max_person", "people"), ("max_relationship", "family links")):
        by = {r["stage"]: r["n"] for r in q("SELECT stage, count(*) n FROM %s WHERE lifecycle='active' GROUP BY stage" % t)}
        tiles.append((label, sum(by.values()), "approved %d - waiting %d - rejected %d"
                      % (by.get("approved", 0), by.get("staged", 0), by.get("rejected", 0))))
    by = {r["status"]: r["n"] for r in q("SELECT status, count(*) n FROM max_task GROUP BY status")}
    tiles.append(("tasks", sum(by.values()), " - ".join("%s %d" % (plain_word("status", k).lower(), by[k])
                                                        for k in TASK_STATUS if k in by) or "none"))
    tiles.append(("dead ends", q("SELECT count(*) n FROM dead_end")[0]["n"], "searches that found nothing"))
    tiles.append(("sources", q("SELECT count(*) n FROM max_source")[0]["n"], "places records live"))
    tiles.append(("places / groups", q("SELECT count(*) n FROM max_place")[0]["n"] + q("SELECT count(*) n FROM max_group")[0]["n"],
                  "%d places, %d groups" % (q("SELECT count(*) n FROM max_place")[0]["n"], q("SELECT count(*) n FROM max_group")[0]["n"])))
    multi = []
    for pid, p in sorted(persons.items(), key=lambda kv: (kv[1]["name"].lower(), kv[0])):
        sets = parent_sets(ctx, pid)
        if len(sets) > 1:
            words = "; ".join("%s%s: %s" % ("* " if s["is_preferred"] else "", SET_WORDS.get(s["set_kind"], s["set_kind"]),
                                             ", ".join(person_display(ctx, x["person_id"])[0] for x in s["parents"]) or "-")
                              for s in sets)
            multi.append((p["name"], words, pid))
    pedigree = []
    for pid, p in sorted(persons.items(), key=lambda kv: (kv[1]["name"].lower(), kv[0])):
        claims = effective_claims(ctx, "person", pid)
        pedigree.append((p["name"], len(claims), len({(c.get("evidence") or {}).get("source_record_id") for _l, c, _h in claims} - {None}), pid))
    approved_docs = [(pw("record", r["record_type"]), pw("licence_short", r["redistribution_license"]),
                      (json.loads(r["doc"]).get("source_url") or ""), r["record_id"])
                     for r in q("SELECT record_type, redistribution_license, doc, record_id FROM max_record "
                                "WHERE stage='approved' AND lifecycle='active' ORDER BY record_type, added_at")]
    waiting = [(r["kind"], pw("record", r["label"]) if r["kind"] == "document" else
                pw("relationship_kind", r["label"]) if r["kind"] == "family link" else r["label"], r["added_at"], r["id"])
               for r in
               q("SELECT 'document' kind, record_type label, added_at, record_id id FROM max_record WHERE stage='staged' AND lifecycle='active' "
                 "UNION ALL SELECT 'person', display_name, added_at, person_id FROM max_person WHERE stage='staged' AND lifecycle='active' "
                 "UNION ALL SELECT 'family link', kind, added_at, relationship_id FROM max_relationship WHERE stage='staged' AND lifecycle='active' "
                 "ORDER BY added_at")]
    rejected = [(pw("table", r["table_name"].replace("max_", "")), r["note"], r["at"], r["target_id"]) for r in
                q("SELECT table_name, note, at, target_id FROM review WHERE verdict='fail' ORDER BY id DESC LIMIT 200")]
    tasks = [(pw("task_kind", r["task_type"]), pw("status", r["status"]),
              pw("verdict", r["verdict"]) if r["verdict"] else "", r["priority"], r["updated_at"], r["task_id"]) for r in
             q("SELECT task_type, status, verdict, priority, updated_at, task_id FROM max_task ORDER BY updated_at DESC LIMIT 300")]
    dead = [(r["source"], r["query"], r["note"], r["at"], r["person_id"]) for r in
            q("SELECT source, query, note, at, person_id FROM dead_end ORDER BY at DESC LIMIT 200")]
    subtitle = "%s - generated %s - read-only: this page is a snapshot, the database is the truth" % (esc(Path(a.db).name), esc(now()))
    banner = ('<p class="banner private">Private copy - contains living, private, or unsure people - do not share this file</p>'
              if private_copy else "")
    owner_body = ('<div class="tiles">%s</div>' % "".join(
        '<div class="tile"><span>%s</span><b>%s</b><em>%s</em></div>' % (esc(l), esc(n), esc(d)) for l, n, d in tiles)
        + tree_section(persons, root, gens, owner=True)
        + "<h2>People with more than one parent set</h2>"
        + '<p class="note">* = the main parents shown on the chart. Change them on the person page.</p>'
        + html_table(("name", "parent sets", "person id"), multi)
        + sources_section(ctx, persons, root, gens, owner=True)
        + "<h2>The pedigree - approved people</h2>"
        + html_table(("name", "live claims", "documents", "person id"), pedigree)
        + "<h2>Approved documents</h2>" + html_table(("document", "licence", "source", "document id"), approved_docs)
        + "<h2>Waiting for Quality Control</h2>" + html_table(("kind", "what", "since", "id"), waiting)
        + "<h2>Rejected - with the reason</h2>" + html_table(("kind", "reason", "when", "id"), rejected)
        + "<h2>Tasks - most recent first</h2>" + html_table(("kind of work", "state", "result", "priority", "updated",
                                                             "task id"), tasks)
        + "<h2>Dead ends - searched, found nothing</h2>" + html_table(("source", "searched", "result", "when", "person id"), dead))
    out.write_text(page_html("Genealogy - progress", subtitle, banner, owner_body), encoding="utf-8")
    print("OK  portal -> %s  (%s approved, %d waiting, %d task%s shown)"
          % (a.out, n_people(len(pedigree)), len(waiting), len(tasks), "" if len(tasks) == 1 else "s"))
    share_banner = '<p class="banner share">Shareable copy - %s withheld (living, private, or unsure)</p>' % n_people(withheld)
    share_body = tree_section(shared, share_root, gens, owner=False) + sources_section(ctx, shared, share_root, gens, owner=False)
    share.write_text(page_html("Genealogy - pedigree (shareable)", subtitle, share_banner, share_body), encoding="utf-8")
    print("OK  shareable -> %s  (%d of %s shown, %s withheld)" % (share, len(shared), n_people(len(persons)), n_people(withheld)))


# ------------------------------------------------------------------ the Gate (kit 1.2.0; design 00 4.5.1 a, 02 4.7)
#
# One Gate is one write transaction for a program (Probable Pedigree, or an agent's own script):
#
#     with maxgen_db.Gate(db, session=maxgen_db.PERSON, action_id=aid, action_name="person.add_fact",
#                         owner_key=key) as g:
#         g.add(doc, task_id=t); g.claim(person_id, {"birth_assertions": [...]}, task_id=t)
#     # leaving the block commits it all; a refusal raises Refused(rule, words) and rolls it all back
#
# It prints nothing, keeps no list of connections (each Gate owns its own) and is safe when each
# thread uses its own Gate. The command line runs the same functions. The maintenance calls
# (upgrade, checkpoint) run on their own, outside a with-block.

PERSON = "person"               # session=PERSON: the person's own session (person-page-<database id>)
OWNER_KEY_BYTES = 32
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
_ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,3}$")
_COLOUR_TOKEN_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")


def owner_key_check_value(key: bytes, salt: bytes | None = None) -> str:
    """The salted check value kept in meta owner_key_check (design 00 8.2 SF-3): never the key, and
    the key cannot be worked back from it."""
    salt = secrets.token_bytes(16) if salt is None else bytes(salt)
    return "v1:%s:%s" % (salt.hex(), hmac.new(bytes(key), b"maxgen owner key check|" + salt, "sha256").hexdigest())


def owner_key_matches(check, key) -> bool:
    try:
        version, salt, digest = str(check).split(":")
        if version != "v1" or not isinstance(key, (bytes, bytearray)) or len(key) != OWNER_KEY_BYTES:
            return False
        return hmac.compare_digest(owner_key_check_value(key, bytes.fromhex(salt)).split(":")[2], digest)
    except (ValueError, TypeError):
        return False


def is_person_session(session) -> bool:
    """The person's own session: the one ensure_person_session makes (kind human, role person). The
    command line never writes under it; an owner-only write also needs the owner key (R-33)."""
    prov = (session or {}).get("provenance") or {}
    return (str((session or {}).get("session_id", "")).startswith("person-page-") and prov.get("role") == "person"
            and prov.get("agent_kind") == "human")


def backup_copy(db, dest) -> str:
    """A checked copy of the tree at ``dest``: SQLite's online backup from a reading connection of its own
    (so it works while a writer holds the turn), written to <dest>.partial, checked (integrity, and every
    table's row count against the tree), then renamed into place. It never overwrites a file."""
    dest = Path(tree_file(dest))                    # one name for the copy and its .partial, as for a tree
    part = dest.with_name(dest.name + ".partial")
    for p in (dest, part):
        if p.exists():
            refuse("%s already exists, and a backup never overwrites a file; choose another name" % p.name, code="backup")
    src = connect(str(db), track=False)
    ok = False
    try:
        src.execute("BEGIN")
        tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {t: src.execute('SELECT count(*) FROM "%s"' % t.replace('"', '""')).fetchone()[0] for t in tables}
        out = sqlite3.connect(str(part))
        try:
            src.backup(out)
            ok = out.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and all(
                out.execute('SELECT count(*) FROM "%s"' % t.replace('"', '""')).fetchone()[0] == n
                for t, n in counts.items())
        finally:
            out.close()
            src.execute("COMMIT")
    finally:
        src.close()
    if not ok:
        part.unlink(missing_ok=True)
        refuse("the backup copy did not check out, so nothing was changed", code="backup")
    if dest.exists():
        refuse("%s appeared while the backup was made; nothing was changed" % dest.name, code="backup")
    os.replace(part, dest)
    return str(dest)


def owner_settings(con) -> dict:
    """The tree's current owner settings, {key: value} (read only)."""
    if not has_table(con, "owner_setting_now"):
        return {}
    return {r[0]: json.loads(r[1]) if r[1] is not None else None
            for r in con.execute("SELECT key, value_json FROM owner_setting_now ORDER BY key")}


def owner_marks(con, kind=None, target_id=None) -> list:
    """The current owner marks (read only), each a dict; optionally of one kind or on one id."""
    if not has_table(con, "owner_mark_now"):
        return []
    sql, args = "SELECT mark_id, kind, target_kind, target_id, mark_key, value_json, reason, row_no FROM owner_mark_now", []
    where = []
    if kind:
        where.append("kind=?")
        args.append(kind)
    if target_id:
        where.append("target_id=?")
        args.append(low(target_id))
    if where:
        sql += " WHERE " + " AND ".join(where)
    out = []
    for r in con.execute(sql + " ORDER BY row_no", args):
        out.append({"mark_id": r[0], "kind": r[1], "target_kind": r[2], "target_id": r[3], "mark_key": r[4],
                    "value": json.loads(r[5]) if r[5] is not None else None, "reason": r[6], "row_no": r[7]})
    return out


class Gate:
    """The write interface for programs (design 00 4.5.1 a). See the section comment above."""
    PERSON = PERSON

    def __init__(self, db, *, session=None, action_id=None, action_name="gate", summary_key=None, summary_args=None,
                 owner_key=None, schemas=None):
        if owner_key is not None and (not isinstance(owner_key, (bytes, bytearray)) or len(owner_key) != OWNER_KEY_BYTES):
            raise ValueError("the owner key is %d bytes" % OWNER_KEY_BYTES)
        if action_id is not None and not _CODE_RE.match(str(action_id)):
            raise ValueError("an action id is a short code of letters, digits and . _ : -")
        if not _ACTION_NAME_RE.match(str(action_name)) or (summary_key and not _ACTION_NAME_RE.match(summary_key)):
            raise ValueError("an action name and a words key look like area.verb_object")
        if summary_args is not None and not all(isinstance(v, (int, float, str)) and not isinstance(v, bool)
                                                for v in summary_args.values()):
            raise ValueError("summary_args hold numbers and ids only, never names")
        self.db, self.session, self.schemas = str(db), session, schemas
        self.action_id = str(action_id) if action_id is not None else new_id()
        self.action_name, self.summary_key, self.summary_args = action_name, summary_key, summary_args
        self._key = bytes(owner_key) if owner_key is not None else None
        self.ctx = None
        self._used = False

    def __repr__(self):                 # never the owner key
        return "<maxgen_db.Gate %s%s>" % (self.action_name, " (open)" if self.ctx is not None else "")

    # -- the transaction
    def _context(self, upgrading=False):
        if not self.session:
            refuse("every write runs under a session: pass session=maxgen_db.PERSON or an agent's session id", "R-14")
        person = self.session == PERSON
        return person, Ctx(self.db, self.schemas, write=True, as_session=None if person else str(self.session),
                           quiet=True, track=False, upgrading=upgrading)

    def __enter__(self):
        if self._used:
            raise RuntimeError("a Gate is one transaction; make a new Gate for the next one")
        self._used = True
        person, ctx = self._context()
        try:
            if person:
                ensure_person_session(ctx, "person_page")
            else:
                ctx.require_session()
            if ctx.con.execute("SELECT 1 FROM action_log WHERE action_id=?", (self.action_id,)).fetchone():
                raise AlreadyDone("action %s was saved before" % self.action_id, rule="already_saved",
                                  words="that change was saved before")
            ctx.action = {"action_id": self.action_id, "action_name": self.action_name,
                          "summary_key": self.summary_key or self.action_name, "summary_args": self.summary_args}
        except BaseException:
            ctx.close()
            raise
        self.ctx = ctx
        return self

    def __exit__(self, exc_type, exc, tb):
        ctx, self.ctx = self.ctx, None
        try:
            if exc_type is None:
                ctx.commit()
        finally:
            ctx.close()                 # rolls back whatever was not committed
        return False

    def _live(self) -> "Ctx":
        if self.ctx is None:
            raise RuntimeError("Gate calls run inside `with Gate(...) as g:`")
        return self.ctx

    @property
    def session_id(self) -> str:
        return self._live().require_session()["session_id"]

    def doc(self, ident):
        """A document as this transaction sees it (through no redirect), or None."""
        ctx = self._live()
        kind = ctx.kind_of_id(ident)
        return ctx.doc(kind, ident) if kind else None

    # -- the kit's own writes (K-16): the cores of add, claim, link and retract
    def add(self, doc, task_id=None, replace=False, expect_sha256=None) -> dict:
        ctx = self._live()
        kind, stored, state = add_document(ctx, copy.deepcopy(doc), task_id, replace, expect_sha256, quiet=True)
        ctx.log("add", "%s %s" % (kind, stored[KEY[kind]]))
        return {"kind": kind, "id": stored[KEY[kind]], "state": state, "sha256": sha256_of(stored)}

    def claim(self, holder_id, claims, task_id) -> list:
        ctx = self._live()
        data = copy.deepcopy(dict(claims))
        data["person_id"] = holder_id
        added = claim_core(ctx, data, task_id)
        ctx.log("claim", "%s +%d" % (low(holder_id), len(added)))
        return [c["assertion_id"] for _l, c in added]

    def link(self, request, task_id=None) -> dict:
        ctx = self._live()
        line, notes = link_request(ctx, copy.deepcopy(request), task_id)
        ctx.log("link", line)
        return {"words": line, "notes": list(notes)}

    def retract(self, target, task_id, reason) -> str:
        ctx = self._live()
        require_task(ctx, task_id, annotation=True, purpose="a retraction")
        if not reason or not str(reason).strip():
            refuse("a retraction needs a reason")
        what = retract_target(ctx, str(target), task_id, str(reason).strip())
        ctx.log("retract", what)
        return what

    # -- the tree's meta keys (the allow-list, K-7)
    def set_tree_meta(self, key, value) -> bool:
        """Set tree_name, home_person_id or tree_kind (the person's own session only). Returns
        whether anything changed. tree_id is made once, and owner_key_check only with the key."""
        ctx = self._live()
        if key not in TREE_META_KEYS:
            refuse("%r is not a meta key a program may set (the allow-list is %s)" % (key, ", ".join(TREE_META_KEYS)),
                   code="meta_key")
        if key in ("tree_id", "owner_key_check"):
            refuse("%s is made once by the kit and is never changed this way" % key, code="meta_key")
        if not is_person_session(ctx.require_session()):
            refuse("only the person's own session may rename the tree or change its home person or kind", "R-33")
        if key == "tree_name":
            value = str(value or "").strip()
            if not 1 <= len(value) <= TREE_NAME_MAX or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
                refuse("a tree's name is 1 to %d letters on one line" % TREE_NAME_MAX, code="meta_value")
        elif key == "home_person_id":
            value = low(value) if value else ""
            if value and not ctx.row("person", ctx.resolve(value)):
                refuse("%s is not a person in this tree" % value, "R-04")
        elif value not in TREE_KINDS:
            refuse("a tree's kind is one of %s" % ", ".join(TREE_KINDS), code="meta_value")
        if ctx.meta(key) == value:
            return False
        ctx.set_meta(key, value)
        return True

    # -- the owner tables (R-33, D-28): the person's own session, with the owner key
    def _owner(self) -> "Ctx":
        ctx = self._live()
        if not is_person_session(ctx.require_session()):
            refuse(OWNER_WORDS + ", and this came from another session", "R-33")
        check = ctx.meta("owner_key_check")
        if not check:
            refuse(OWNER_WORDS + ", and this tree has no owner key yet (Probable Pedigree sets it when it opens "
                                 "the tree)", "R-33")
        if self._key is None or not owner_key_matches(check, self._key):
            refuse(OWNER_WORDS + ", and the owner key was missing or wrong", "R-33")
        return ctx

    @staticmethod
    def _json(value):
        if value is None:
            return None
        text = canon(value)
        if len(text) > OWNER_JSON_MAX:
            refuse("an owner value is at most %d characters of JSON" % OWNER_JSON_MAX, code="owner_value")
        return text

    @staticmethod
    def _code(value, what):
        if not isinstance(value, str) or not _CODE_RE.match(value):
            refuse("%s is a short code of letters, digits and . _ : -" % what, code="owner_value")
        return value

    def _row(self, table, cols, op) -> int:
        ctx = self._live()
        cols = dict(cols, op=op, session_id=ctx.require_session()["session_id"], action_id=self.action_id, at=now())
        names = list(cols)
        cur = ctx.con.execute("INSERT INTO %s (%s) VALUES (%s)" % (table, ",".join(names), ",".join("?" * len(names))),
                              [cols[n] for n in names])
        return cur.lastrowid

    def _now(self, table, **keys):
        return self._live().con.execute("SELECT * FROM %s_now WHERE %s" % (table, " AND ".join("%s=?" % k for k in keys)),
                                        list(keys.values())).fetchone()

    def owner_set(self, key, value) -> int:
        self._owner()
        if key not in OWNER_SETTING_KEYS:
            refuse("%r is not an owner setting (they are %s)" % (key, ", ".join(OWNER_SETTING_KEYS)), code="owner_value")
        if key in OWNER_SETTING_VALUES and value not in OWNER_SETTING_VALUES[key]:
            refuse("%s is one of %s" % (key, ", ".join(OWNER_SETTING_VALUES[key])), code="owner_value")
        return self._row("owner_setting", {"key": key, "value_json": self._json(value)}, "set")

    def owner_clear(self, key):
        """Clear an owner setting (a 'clear' row; nothing is deleted). None when it was not set."""
        self._owner()
        if self._now("owner_setting", key=key) is None:
            return None
        return self._row("owner_setting", {"key": key, "value_json": None}, "clear")

    def _mark_target(self, target_kind, target_id):
        ctx = self._live()
        if target_kind not in OWNER_TARGET_KINDS:
            refuse("a mark is on one of %s" % ", ".join(OWNER_TARGET_KINDS), code="owner_value")
        if not isinstance(target_id, str) or not 1 <= len(target_id) <= 200:
            refuse("a mark names the id it is on", code="owner_value")
        tid = low(target_id) if ID_RE.match(target_id) else target_id
        if target_kind in KEY or target_kind == "event":
            if ctx.kind_of_id(tid) != target_kind:
                refuse("%s is not a %s in this tree" % (tid, target_kind), "R-04")
        elif target_kind == "claim":
            if not ctx.con.execute("SELECT 1 FROM claim_index WHERE assertion_id=?", (tid,)).fetchone():
                refuse("%s is not a claim in this tree" % tid, "R-04")
        elif target_kind == "branch":
            if self._now("owner_branch", branch_id=tid) is None:
                refuse("%s is not one of this tree's named branches" % tid, "R-04")
        return tid

    def owner_mark(self, kind, target_kind, target_id, mark_key="", value=None, reason=None) -> str:
        """Set a mark (hold back, lock, pick, bookmark ...). Returns its mark_id, the same on every row
        about one mark. Only an outside_holdings mark may come from any session (D-28)."""
        if kind not in OWNER_MARK_KINDS:
            refuse("a mark's kind is one of %s" % ", ".join(OWNER_MARK_KINDS), code="owner_value")
        ctx = self._owner() if kind != "outside_holdings" else self._live()
        tid = self._mark_target(target_kind, target_id)
        mark_key = "" if mark_key in (None, "") else self._code(mark_key, "a mark's key")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
            refuse("a mark's reason is at most 500 characters", code="owner_value")
        row = ctx.con.execute("SELECT mark_id FROM owner_mark WHERE kind=? AND target_kind=? AND target_id=? AND "
                              "mark_key=? ORDER BY row_no DESC LIMIT 1", (kind, target_kind, tid, mark_key)).fetchone()
        mark_id = row[0] if row else new_id()
        self._row("owner_mark", {"mark_id": mark_id, "kind": kind, "target_kind": target_kind, "target_id": tid,
                                 "mark_key": mark_key, "value_json": self._json(value), "reason": reason}, "set")
        ctx._locks = None
        return mark_id

    def owner_unmark(self, kind, target_kind, target_id, mark_key=""):
        """Clear a mark (a 'clear' row). None when it was not set."""
        if kind not in OWNER_MARK_KINDS:
            refuse("a mark's kind is one of %s" % ", ".join(OWNER_MARK_KINDS), code="owner_value")
        ctx = self._owner() if kind != "outside_holdings" else self._live()
        tid = low(target_id) if isinstance(target_id, str) and ID_RE.match(target_id) else target_id
        cur = self._now("owner_mark", kind=kind, target_kind=target_kind, target_id=tid, mark_key=mark_key or "")
        if cur is None:
            return None
        self._row("owner_mark", {"mark_id": cur["mark_id"], "kind": kind, "target_kind": target_kind, "target_id": tid,
                                 "mark_key": mark_key or "", "value_json": None, "reason": None}, "clear")
        ctx._locks = None
        return cur["mark_id"]

    def owner_branch(self, branch_id, kind=None, person_id=None, name=None, clear=False):
        ctx = self._owner()
        branch_id = self._code(branch_id, "a branch id")
        cur = self._now("owner_branch", branch_id=branch_id)
        if clear:
            return None if cur is None else self._row(
                "owner_branch", {"branch_id": branch_id, "kind": cur["kind"], "person_id": cur["person_id"],
                                 "name": cur["name"]}, "clear")
        if kind not in OWNER_BRANCH_KINDS:
            refuse("a branch is one of %s" % ", ".join(OWNER_BRANCH_KINDS), code="owner_value")
        if kind != "whole_tree":
            if not person_id or not ctx.row("person", ctx.resolve(person_id)):
                refuse("a branch of ancestors or descendants starts from a person in this tree", "R-04")
            person_id = low(person_id)
        else:
            person_id = None
        if name is not None and (not isinstance(name, str) or len(name) > 120):
            refuse("a branch's name is at most 120 letters", code="owner_value")
        return self._row("owner_branch", {"branch_id": branch_id, "kind": kind, "person_id": person_id, "name": name},
                         "set")

    def owner_rule(self, rule_id, branch_id=None, mode=None, rules=None, clear=False):
        self._owner()
        rule_id = self._code(rule_id, "a rule id")
        cur = self._now("owner_rule", rule_id=rule_id)
        if clear:
            return None if cur is None else self._row(
                "owner_rule", {"rule_id": rule_id, "branch_id": cur["branch_id"], "mode": cur["mode"],
                               "rules_json": None}, "clear")
        if mode not in OWNER_RULE_MODES:
            refuse("an incoming rule is one of %s" % ", ".join(OWNER_RULE_MODES), code="owner_value")
        if self._now("owner_branch", branch_id=self._code(branch_id, "a branch id")) is None:
            refuse("%s is not one of this tree's named branches" % branch_id, "R-04")
        return self._row("owner_rule", {"rule_id": rule_id, "branch_id": branch_id, "mode": mode,
                                        "rules_json": self._json(rules)}, "set")

    def owner_tag(self, tag_id, name=None, colour_token=None, clear=False):
        self._owner()
        tag_id = self._code(tag_id, "a tag id")
        if clear:
            cur = self._now("owner_tag", tag_id=tag_id)
            return None if cur is None else self._row("owner_tag", {"tag_id": tag_id, "name": cur["name"],
                                                                    "colour_token": cur["colour_token"]}, "clear")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            refuse("a tag's name is 1 to 60 letters", code="owner_value")
        if colour_token is not None and not _COLOUR_TOKEN_RE.match(str(colour_token)):
            refuse("a tag's colour is a colour name from the program's own list, never a raw colour", code="owner_value")
        return self._row("owner_tag", {"tag_id": tag_id, "name": name.strip(), "colour_token": colour_token}, "set")

    def owner_list(self, list_id, name=None, definition=None, clear=False):
        self._owner()
        list_id = self._code(list_id, "a list id")
        if clear:
            cur = self._now("owner_list", list_id=list_id)
            return None if cur is None else self._row("owner_list", {"list_id": list_id, "name": cur["name"],
                                                                     "definition_json": None}, "clear")
        if not isinstance(definition, dict) or definition.get("kind") not in OWNER_LIST_KINDS:
            refuse("a saved list's definition says its kind: one of %s" % ", ".join(OWNER_LIST_KINDS), code="owner_value")
        if name is not None and (not isinstance(name, str) or len(name) > 120):
            refuse("a saved list's name is at most 120 letters", code="owner_value")
        return self._row("owner_list", {"list_id": list_id, "name": name, "definition_json": self._json(definition)},
                         "set")

    # -- maintenance calls (K-26), each on its own, outside a with-block
    def upgrade(self, backup_to=None, tree_kind="own") -> dict:
        """Add the 1.2.0 bookkeeping to an older tree (CREATE ... IF NOT EXISTS only), after a checked
        backup copy at ``backup_to``; give it a tree_id and a tree_kind if it has none; and, with the
        owner key under the person's own session, its owner_key_check if it has none. Run twice, the
        second run changes nothing. Returns {"changed", "added", "backup"}: "added" names the bookkeeping
        tables, views and triggers added, then any column added as "table.column" (ADDED_COLUMNS); a run
        that changed something also says which speed indexes it made ("indexes", ADDED_INDEXES)."""
        if self.ctx is not None:
            raise RuntimeError("upgrade runs on its own, outside a with-block")
        if tree_kind not in TREE_KINDS:
            refuse("a tree's kind is one of %s" % ", ".join(TREE_KINDS), code="meta_value")
        person, ctx = self._context(upgrading=True)
        try:
            missing = missing_bookkeeping(ctx.con)
            columns = missing_columns(ctx.con)
            indexes = missing_indexes(ctx.con)
            want_key = self._key is not None and person and not ctx.meta("owner_key_check")
            if not (missing or columns or indexes or want_key or not ctx.meta("tree_id") or not ctx.meta("tree_kind")
                    or int(ctx.meta("max_minor_written") or 0) < KNOWN_MINOR):
                return {"changed": False, "added": [], "backup": None}
            backup = None
            if missing or columns or indexes:
                if not backup_to:
                    refuse("upgrade adds tables or columns to this tree, so it makes a backup copy first: say where "
                           "(backup_to)", code="backup")
                # the copy is of the tree as it is: nothing has been written to it yet
                backup = backup_copy(self.db, backup_to)
                add_missing_columns(ctx.con)
                for stmt in BOOKKEEPING_DDL:
                    ctx.con.execute(stmt)
                add_missing_indexes(ctx.con)
            ctx.begin_bookkeeping()
            if person:
                ensure_person_session(ctx, "person_page")
            else:
                ctx.require_session()
            for stmt in trigger_ddl():
                ctx.con.execute(stmt)
            if not ctx.meta("tree_id"):
                ctx.set_meta("tree_id", new_id())
            if not ctx.meta("tree_kind"):
                ctx.set_meta("tree_kind", tree_kind)
            if want_key:
                ctx.set_meta("owner_key_check", owner_key_check_value(self._key))
            ctx.log("upgrade", "kit %s: %d bookkeeping objects added%s" % (
                __version__, len(missing), "; columns added: %s" % ", ".join(columns) if columns else ""))
            ctx.action = {"action_id": self.action_id, "action_name": "tree.upgrade", "summary_key": "saved.tree_upgraded",
                          "summary_args": {"added": len(missing)}, "no_undo": True}
            ctx.commit()
            return {"changed": True, "added": missing + columns, "backup": backup, "indexes": indexes}
        finally:
            ctx.close()

    def checkpoint(self, mode="PASSIVE", during_maintenance=False) -> dict:
        """Fold the write-ahead log back into the tree file (design 00 4.6.1). PASSIVE makes nobody
        wait. TRUNCATE is not offered here: it runs only inside the restore procedure, once nobody else
        has the file open. Refused while the maintenance marker is there, unless the restore itself asks."""
        if self.ctx is not None:
            raise RuntimeError("a checkpoint runs on its own, outside a with-block")
        mode = str(mode).upper()
        if mode not in ("PASSIVE", "FULL", "RESTART"):
            refuse("a checkpoint here is PASSIVE, FULL or RESTART; TRUNCATE runs only inside the restore procedure",
                   code="checkpoint_mode")
        if not during_maintenance:
            refuse_unsafe_tree_file(self.db)        # the tree's real name, whatever spelling was given
        con = connect(self.db, track=False)
        try:
            busy, log_frames, done = con.execute("PRAGMA wal_checkpoint(%s)" % mode).fetchone()
        finally:
            con.close()
        return {"mode": mode, "busy": bool(busy), "log_frames": log_frames, "checkpointed_frames": done}


def cmd_upgrade(a):
    sid = getattr(a, "as_session", None) or os.environ.get("MAXGEN_SESSION")
    if not sid:
        refuse("upgrade runs under a session: run `maxgen_db.py session` once and pass --as <session_id>", "R-14")
    db = Path(tree_file(a.db))                      # the backup goes beside the tree's real name
    backup = a.backup or str(db.with_name("%s.before-upgrade-%s%s" % (
        db.stem, datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"), db.suffix or ".db")))
    res = Gate(a.db, session=sid, schemas=getattr(a, "schemas", None)).upgrade(backup_to=backup, tree_kind=a.kind)
    if not res["changed"]:
        print("OK  %s already has the kit %s bookkeeping (MAXGEN 2.%d); nothing was changed" % (db.name, __version__,
                                                                                            KNOWN_MINOR))
        return
    columns = [n for n in res["added"] if "." in n]
    print("OK  %s upgraded for kit %s (MAXGEN 2.%d): %d tables, views and triggers added%s%s"
          % (db.name, __version__, KNOWN_MINOR, len(res["added"]) - len(columns),
             "; columns added: %s" % ", ".join(columns) if columns else "",
             "; backup copy %s" % Path(res["backup"]).name if res["backup"] else ""))


# ------------------------------------------------------------------ main

WRITE_COMMANDS = ("session", "add", "claim", "link", "review", "task", "step", "dead-end", "retract", "apply-proof",
                  "withdraw-proof", "prefer", "merge", "unmerge", "distinct", "split", "resolve", "apply-run-review",
                  "reindex", "import-maxgen")


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schemas", help="folder holding the twelve .schema.json files")
    ap.add_argument("--as", dest="as_session", help="the session id to write under (else MAXGEN_SESSION)")
    ap.add_argument("--version", action="version", version="maxgen_db.py " + __version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--schemas", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--as", dest="as_session", default=argparse.SUPPRESS, help="session id to write under")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def P(name, **kw):
        return sub.add_parser(name, parents=[common], **kw)
    p = P("init"); p.add_argument("db")
    p.add_argument("--kind", choices=TREE_KINDS, default="own", help="the kind of tree (meta tree_kind)")
    p = P("upgrade", help="add the kit 1.2.0 bookkeeping to a tree made by an older kit, after a backup copy")
    p.add_argument("db"); p.add_argument("--backup", help="where the checked backup copy goes")
    p.add_argument("--kind", choices=TREE_KINDS, default="own", help="the kind of tree, if it has none yet")
    p = P("kit-hash", help="print the keyed fingerprint of a DNA kit number read from standard input")
    p.add_argument("db")
    p = P("import-maxgen", help="bring in another MAXGEN database's exported tables (4.4 rule 13)")
    p.add_argument("db"); p.add_argument("folder"); p.add_argument("--task", required=True)
    p = P("session"); p.add_argument("db"); p.add_argument("--name", required=True)
    p.add_argument("--kind", required=True, choices=("human", "ai", "ocr_htr", "rule_engine", "importer", "sync_bridge",
                                                     "organization", "other"))
    p.add_argument("--role", required=True); p.add_argument("--model-name"); p.add_argument("--model-provider")
    p.add_argument("--model-version"); p.add_argument("--run-id"); p.add_argument("--operator")
    p.add_argument("--version", dest="version"); p.add_argument("--display-name")
    p = P("add"); p.add_argument("db"); p.add_argument("file"); p.add_argument("--task")
    p.add_argument("--replace", action="store_true"); p.add_argument("--expect-sha256")
    p.add_argument("--by", help="(1.x; ignored -- the session says who writes)")
    p = P("claim"); p.add_argument("db"); p.add_argument("file"); p.add_argument("--task", required=True)
    p = P("show"); p.add_argument("db"); p.add_argument("id")
    p = P("link"); p.add_argument("db"); p.add_argument("file"); p.add_argument("--task")
    p = P("review"); p.add_argument("db"); p.add_argument("id"); p.add_argument("verdict", choices=("pass", "fail"))
    p.add_argument("--by", required=True); p.add_argument("--note", required=True)
    p = P("task"); p.add_argument("db"); p.add_argument("id"); p.add_argument("status", choices=TASK_STATUS)
    p.add_argument("--verdict", choices=STEP_VERDICTS); p.add_argument("--note")
    p.add_argument("--answer", choices=("accepted", "rejected", "rework"))
    p = P("step"); p.add_argument("db"); p.add_argument("--task", required=True); p.add_argument("--source", required=True)
    p.add_argument("--query", required=True); p.add_argument("--found", required=True)
    p.add_argument("--verdict", required=True, choices=STEP_VERDICTS); p.add_argument("--source-id")
    p.add_argument("--record"); p.add_argument("--person")
    p = P("dead-end"); p.add_argument("db"); p.add_argument("--task", required=True); p.add_argument("--person")
    p.add_argument("--source", required=True); p.add_argument("--query", required=True); p.add_argument("--note", default="")
    p = P("retract"); p.add_argument("db"); p.add_argument("id"); p.add_argument("--task", required=True)
    p.add_argument("--reason", required=True)
    p = P("apply-proof"); p.add_argument("db"); p.add_argument("task_id")
    p = P("withdraw-proof"); p.add_argument("db"); p.add_argument("task_id"); p.add_argument("--task", required=True)
    p.add_argument("--reason", required=True)
    p = P("prefer"); p.add_argument("db"); p.add_argument("membership_id"); p.add_argument("--task", required=True)
    p = P("merge"); p.add_argument("db"); p.add_argument("kind"); p.add_argument("survivor"); p.add_argument("absorbed")
    p.add_argument("--task", required=True); p.add_argument("--label-map")
    p = P("unmerge"); p.add_argument("db"); p.add_argument("kind"); p.add_argument("absorbed"); p.add_argument("--task", required=True)
    p = P("distinct"); p.add_argument("db"); p.add_argument("kind"); p.add_argument("id1"); p.add_argument("id2")
    p.add_argument("--task", required=True); p.add_argument("--reason", required=True)
    p = P("split"); p.add_argument("db"); p.add_argument("kind"); p.add_argument("id"); p.add_argument("--task", required=True)
    p.add_argument("--move", nargs="+", required=True); p.add_argument("--move-link", nargs="*")
    p = P("resolve"); p.add_argument("db"); p.add_argument("kind", nargs="?"); p.add_argument("target", nargs="?")
    p.add_argument("--task", required=True); p.add_argument("--claims", nargs="+"); p.add_argument("--written")
    p.add_argument("--undo")
    p = P("apply-run-review"); p.add_argument("db"); p.add_argument("task_id")
    p = P("status"); p.add_argument("db")
    for name in ("list", "export"):
        p = P(name); p.add_argument("db"); p.add_argument("table")
        if name == "export":
            p.add_argument("out"); p.add_argument("--shareable", action="store_true")
        p.add_argument("--stage", choices=STAGES); p.add_argument("--status", choices=TASK_STATUS)
        p.add_argument("--limit", type=int, default=20)
    p = P("occurrence"); p.add_argument("db"); p.add_argument("id")
    p = P("lineage"); p.add_argument("db"); p.add_argument("person")
    p.add_argument("--mode", default="preferred"); p.add_argument("--gens", type=int, default=4)
    p.add_argument("--json", action="store_true")
    p = P("check"); p.add_argument("db")
    p = P("reindex"); p.add_argument("db")
    p = P("portal"); p.add_argument("db"); p.add_argument("out", nargs="?", default="portal.html")
    p.add_argument("--shareable-out"); p.add_argument("--root"); p.add_argument("--gens", type=int)
    return ap


COMMANDS = {"session": cmd_session, "add": cmd_add, "claim": cmd_claim, "show": cmd_show, "link": cmd_link,
            "review": cmd_review, "task": cmd_task, "step": cmd_step, "dead-end": cmd_dead_end, "retract": cmd_retract,
            "apply-proof": cmd_apply_proof, "withdraw-proof": cmd_withdraw_proof, "prefer": cmd_prefer,
            "merge": cmd_merge, "unmerge": cmd_unmerge, "distinct": cmd_distinct, "split": cmd_split,
            "resolve": cmd_resolve, "apply-run-review": cmd_apply_run_review, "status": cmd_status, "list": cmd_list,
            "export": cmd_export, "occurrence": cmd_occurrence, "lineage": cmd_lineage, "check": cmd_check,
            "reindex": cmd_reindex, "portal": cmd_portal, "kit-hash": cmd_kit_hash, "import-maxgen": cmd_import_maxgen}


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    ctx = None
    try:
        if a.cmd == "init":
            cmd_init(a)
            return
        if not Path(a.db).exists():
            refuse("%s does not exist; run: python maxgen_db.py init %s" % (a.db, a.db))
        as_session = getattr(a, "as_session", None)
        if as_session and str(as_session).startswith("person-page-"):
            refuse("that session belongs to the person page; the command line cannot write under it", "R-14")
        if not as_session and str(os.environ.get("MAXGEN_SESSION", "")).startswith("person-page-"):
            refuse("that session belongs to the person page; the command line cannot write under it", "R-14")
        if a.cmd == "upgrade":
            cmd_upgrade(a)
            return
        ctx = Ctx(a.db, getattr(a, "schemas", None), write=a.cmd in WRITE_COMMANDS, as_session=as_session)
        ctx.command = a.cmd
        if a.cmd in WRITE_COMMANDS and a.cmd != "session":
            ctx.require_session()
        COMMANDS[a.cmd](ctx, a)
        if ctx.write:
            ctx.commit()
    except Refused as e:
        if ctx is not None:
            ctx.rollback()
        print("REFUSED: %s" % e)
        sys.exit(1)
    except BaseException:
        if ctx is not None:
            ctx.rollback()
        raise
    finally:
        close_all()


if __name__ == "__main__":
    main()
