#!/usr/bin/env python3
"""check_maxgen.py -- checks MAXGEN 2.0 files in a folder against the real schemas.

HOW TO RUN
    pip install jsonschema referencing rfc3987 rfc3339-validator     (once)
    python check_maxgen.py <folder>
    python check_maxgen.py <folder> --schemas <folder holding the twelve .schema.json files>

  or, without installing anything globally:
    uv run --no-project --with jsonschema --with referencing --with rfc3987 --with rfc3339-validator \
        python check_maxgen.py <folder>

Without rfc3987 and rfc3339-validator the checker still runs, but it cannot check web
addresses or times. It says so in a NOTE at the top, and those checks are then skipped.

WHAT IT CHECKS
  1. Each .json file is one MAXGEN document. Its kind is the first of these keys found at its
     top level: dna_id, recognition_id, name_id, relationship_id, occurrence_id, place_id,
     group_id, record_id, task_id, source_id, person_id (a MaxDNA also has a person_id, so the
     order matters).
  2. The document must match its schema exactly, through all twelve schema files loaded
     together (the eleven record schemas and maxgen-defs.schema.json). Unknown fields are
     refused. Nothing is ever fetched from the internet: a reference the schemas cannot find
     on this computer is an error, not a download.
  3. A MAXGEN 1.x file (or one with no schema_version) is reported as such: MAXGEN 2.0 does not
     read 1.x files.
  4. No invented sources. Every evidence link must point at a document in the same folder:
     a cited record (and the line, event entry or marked area named on it), an imported tree
     file, a DNA test (and the match ids on it), a proof task or a search step. When the kit's
     maxgen_db.py 2.0 is found (next to this file, else in ../agents/), its reference map
     REFERENCES is used as well, so every other reference path is checked the same way the
     kit checks it.

THE REFERENCE MAP (how this file reads maxgen_db.REFERENCES)
  Each row is a dict {"path": ..., "target": ...} or a tuple (path, target, scope, redirects,
  new_write). `path` is "<kind>:<dotted path>" ("*" for any kind), where "name[]" steps into a
  list and "**" matches any depth, e.g. "record:part_of_source_id" or "*:**.created_by_task_id".
  `target` is a kind name (record, person, task ...). A row whose target is a label (a mention,
  an event entry) or whose path has another form is counted and reported as not checked here;
  labels inside evidence links are checked by this file's own evidence check.

WHAT IT CANNOT CHECK
  - whether a transcription copies its document correctly (a person must compare them)
  - whether a document is genuine
  - whether a confidence number is sensible
  - the rules the kit applies when it stores a document (the 0.4 cap, the quote check,
    living people worked out from dates, and the other rules of maxgen_db.py)

It reads files and prints results. It changes nothing and makes no network calls.
"""
# Copyright (c) 2026 OpenGenealogyAI Contributors. MIT License; see LICENSE.
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

__version__ = "0.2.1"   # bump on any behaviour change; the website publishes a SHA-256 per version

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    from referencing.exceptions import NoSuchResource
except ImportError:  # pragma: no cover - only without the packages
    print("This checker needs the jsonschema (4.18 or later) and referencing packages:\n"
          "    pip install jsonschema referencing rfc3987 rfc3339-validator")
    raise SystemExit(2)

SCHEMA_BASE = "https://opengenealogyai.org/schemas/maxgen/v2/"
SCHEMA_FILES = ("maxgen-defs.schema.json", "raw-record.schema.json", "person.schema.json",
                "task-queue.schema.json", "source.schema.json", "name.schema.json",
                "recognition.schema.json", "dna.schema.json", "relationship.schema.json",
                "place.schema.json", "group.schema.json", "event.schema.json")

# Kind order (spec 2.2): the first of these keys present at the top level decides the kind.
KINDS = (
    ("dna_id", "dna", "dna.schema.json", "MaxDNA"),
    ("recognition_id", "recognition", "recognition.schema.json", "MaxRecognition"),
    ("name_id", "name", "name.schema.json", "MaxName"),
    ("relationship_id", "relationship", "relationship.schema.json", "MaxRelationship"),
    ("occurrence_id", "event", "event.schema.json", "MaxEvent"),
    ("place_id", "place", "place.schema.json", "MaxPlace"),
    ("group_id", "group", "group.schema.json", "MaxGroup"),
    ("record_id", "record", "raw-record.schema.json", "MaxRecord"),
    ("task_id", "task", "task-queue.schema.json", "MaxTask"),
    ("source_id", "source", "source.schema.json", "MaxSource"),
    ("person_id", "person", "person.schema.json", "MaxPerson"),
)
KIND_FILE = {kind: fname for _, kind, fname, _ in KINDS}
KIND_KEY = {kind: key for key, kind, _, _ in KINDS}
KIND_TITLE = {kind: title for _, kind, _, title in KINDS}

V1_MESSAGE = ("this is a MAXGEN 1.x file (or has no schema_version); "
              "MAXGEN 2.0 does not read 1.x files")

# A downloaded copy keeps the schemas in a `schemas` folder beside it (see agents/START.md).
# Inside the repo, docs/onboarding/check_maxgen.py -> repo root -> schemas/.
_HERE = Path(__file__).resolve().parent
DEFAULT_SCHEMAS = (_HERE / "schemas") if (_HERE / "schemas" / "maxgen-defs.schema.json").exists() \
    else _HERE.parent.parent / "schemas"


# ------------------------------------------------------------------ the registry (spec 2.4)

def _no_network(uri: str):
    # The kit never fetches anything. An unknown $ref is an error, not a download.
    raise NoSuchResource(ref=uri)


def load_registry(folder: Path):
    """Load the twelve schema files into one registry that never touches the network."""
    schemas = {}
    for name in SCHEMA_FILES:
        path = Path(folder) / name
        if not path.exists():
            raise SystemExit("REFUSED: %s is missing from %s" % (name, folder))
        doc = json.loads(path.read_text(encoding="utf-8"))
        expected = SCHEMA_BASE + name
        if doc.get("$id") != expected:
            raise SystemExit("REFUSED: %s has $id %r, expected %r" % (name, doc.get("$id"), expected))
        schemas[name] = doc
    registry = Registry(retrieve=_no_network).with_resources(
        (doc["$id"], Resource.from_contents(doc, default_specification=DRAFT202012))
        for doc in schemas.values())
    return registry, schemas


def validator_for(schemas: dict, registry, name: str):
    return Draft202012Validator(schemas[name], registry=registry, format_checker=FormatChecker())


def load_checkers(folder: Path):
    """The registry, the twelve schemas and one validator per kind, ready for explain()."""
    registry, schemas = load_registry(Path(folder))
    _INDEX["index"] = _SchemaIndex(schemas)
    checkers = {kind: validator_for(schemas, registry, fname) for kind, fname in KIND_FILE.items()}
    return registry, schemas, checkers


def kind_of(doc):
    """The kind of one document by the key order of spec 2.2, or None."""
    if not isinstance(doc, dict):
        return None
    for key, kind, _, _ in KINDS:
        if key in doc:
            return kind
    return None


def is_v1(doc) -> bool:
    """True for a 1.x stamp or no stamp at all (a 2.x stamp that is merely mistyped is not 1.x)."""
    stamp = doc.get("schema_version") if isinstance(doc, dict) else None
    return not isinstance(stamp, str) or stamp.startswith("1.") or stamp == "1"


# ------------------------------------------------------------------ plain-English errors

def _where(path) -> str:
    return ".".join(str(p) for p in path) or "the top level of the file"


class _SchemaIndex:
    """Knows which schema file each sub-schema came from, so a $ref can be followed when
    working out which field names an object declares."""

    def __init__(self, schemas: dict):
        self.schemas = schemas
        self.owner = {}
        for name, doc in schemas.items():
            self._walk(doc, name)

    def _walk(self, node, name):
        if isinstance(node, dict):
            self.owner[id(node)] = name
            for value in node.values():
                self._walk(value, name)
        elif isinstance(node, list):
            for value in node:
                self._walk(value, name)

    def resolve(self, ref: str, base_name: str):
        file_part, _, pointer = ref.partition("#")
        name = file_part or base_name
        node = self.schemas.get(name)
        if node is None:
            return None, name
        for token in [t for t in pointer.split("/") if t]:
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and token in node:
                node = node[token]
            else:
                return None, name
        return node, name

    def declared(self, schema, name=None, depth=0) -> set:
        """Every property name an object schema declares, itself or through allOf / $ref."""
        if not isinstance(schema, dict) or depth > 8:
            return set()
        name = name or self.owner.get(id(schema))
        keys = set(schema.get("properties", {}))
        if "$ref" in schema and name:
            target, tname = self.resolve(schema["$ref"], name)
            keys |= self.declared(target, tname, depth + 1)
        for sub in schema.get("allOf", []):
            keys |= self.declared(sub, name, depth + 1)
        return keys


_INDEX = {"index": None}


def _quoted_names(message: str):
    inside = re.search(r"\((.*) (?:was|were) unexpected\)", message)
    return re.findall(r"'([^']*)'", inside.group(1)) if inside else []


def _required_forms(branches):
    forms = []
    for branch in branches if isinstance(branches, list) else []:
        if isinstance(branch, dict) and isinstance(branch.get("required"), list):
            forms.append(" + ".join(branch["required"]))
        else:
            return None
    return forms


def _branch_lines(err):
    """The first plain line from each failed branch of a oneOf / anyOf."""
    by_branch = {}
    for sub in err.context or []:
        index = sub.relative_schema_path[0] if sub.relative_schema_path else 0
        lines = plain(sub)
        if lines and index not in by_branch:
            by_branch[index] = lines[0]
    return ["(%d) %s" % (i + 1, by_branch[i]) for i in sorted(by_branch)]


def _range_text(schema):
    low = schema.get("minimum", schema.get("exclusiveMinimum"))
    high = schema.get("maximum", schema.get("exclusiveMaximum"))
    if low is not None and high is not None:
        return "outside the allowed range %s to %s" % (low, high)
    if high is not None:
        return "more than the largest allowed value %s" % high
    return "less than the smallest allowed value %s" % low


def _forbidden_key(err, doc):
    """jsonschema reports a field forbidden by a `false` schema at its parent's path. Find the
    field's own name by looking for the offending value inside that parent."""
    node = doc
    for step in err.absolute_path:
        try:
            node = node[step]
        except (KeyError, IndexError, TypeError):
            return None
    if not isinstance(node, dict):
        return None
    same = [k for k, v in node.items() if v is err.instance]
    if len(same) != 1:
        same = [k for k, v in node.items() if v == err.instance and type(v) is type(err.instance)]
    return same[0] if len(same) == 1 else None


def plain(err, all_errors=None, doc=None):
    """Turn one schema error into plain-English lines (one per problem found).

    An object that fails inside a shared part pulled in with allOf (the claim header, the
    common fields) is also reported by jsonschema as having "unevaluated" properties. Those
    names are declared, so they are dropped here: only names MAXGEN really does not have are
    reported as unknown fields, and an unknown-field report with nothing left is dropped."""
    where = _where(err.absolute_path)
    kind = err.validator
    index = _INDEX["index"]

    if kind in ("additionalProperties", "unevaluatedProperties"):
        if isinstance(err.instance, dict):
            if kind == "additionalProperties":
                known = set(err.schema.get("properties", {}))
                patterns = list(err.schema.get("patternProperties", {}))
                bad = [k for k in err.instance if k not in known
                       and not any(re.search(p, k) for p in patterns)]
            else:
                named = _quoted_names(err.message)
                bad = named or list(err.instance)
                if index is not None:
                    declared = index.declared(err.schema)
                    bad = [k for k in bad if k not in declared]
            if all_errors:
                prefix = list(err.absolute_path)
                deeper = {e.absolute_path[len(prefix)] for e in all_errors
                          if e is not err and len(e.absolute_path) > len(prefix)
                          and list(e.absolute_path)[:len(prefix)] == prefix}
                bad = [k for k in bad if k not in deeper]
            if not bad:
                return []
            return ["%s: unknown field(s) %s -- MAXGEN has no field with that name"
                    % (where, sorted(bad))]
        return ["%s: %s" % (where, err.message)]
    if kind == "required":
        missing = [k for k in err.validator_value if k not in err.instance]
        lines = []
        for k in missing:
            line = "missing required field '%s' in %s" % (k, where)
            if k == "schema_version":
                line += " (%s)" % V1_MESSAGE
            elif k in ("is_living", "is_living_flag"):
                line += (" (Living A: say whether the person named may be alive; "
                         "when unsure, write true)")
            elif k == "evidence":
                line += " (every claim cites evidence)"
            lines.append(line)
        return lines
    if kind in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        return ["%s is %r, %s" % (where, err.instance, _range_text(err.schema))]
    if kind == "enum":
        return ["%s is %r; the allowed values are: %s"
                % (where, err.instance, ", ".join(str(v) for v in err.validator_value))]
    if kind == "type":
        return ["%s should be %s, but you wrote %r" % (where, err.validator_value, err.instance)]
    if kind == "format":
        return ["%s is %r, which is not a valid %s" % (where, err.instance, err.validator_value)]
    if kind == "const":
        line = "%s must be exactly %r" % (where, err.validator_value)
        last = err.absolute_path[-1] if err.absolute_path else ""
        dna_list = isinstance(doc, dict) and doc.get("record_type") == "dna_match_list"
        if dna_list and last in ("redistribution_license", "is_living_flag", "is_living"):
            line += " (a DNA match list is always living and tier2-private)"
        elif last == "redistribution_license" and err.validator_value == "tier2-private":
            line += " (anything that may name a living person, and every DNA test, stays tier2-private)"
        elif last == "is_living_flag" and err.validator_value is True:
            line += " (a mention with is_living true makes the whole record living)"
        elif last == "contributor_eligibility":
            line += " (a task about someone who may be alive is first_party_only)"
        return [line]
    if kind == "pattern":
        last = err.absolute_path[-1] if err.absolute_path else ""
        if last == "schema_version":
            if isinstance(err.instance, str) and re.match(r"^1(\.[0-9]+)*$", err.instance):
                return ["schema_version is %r: %s" % (err.instance, V1_MESSAGE)]
            later = re.match(r"^([3-9]|[1-9][0-9]+)\.[0-9]+$", err.instance) if isinstance(err.instance, str) else None
            if later:
                return ["schema_version is %r: written by MAXGEN %s.x; this checker reads 2.x"
                        % (err.instance, later.group(1))]
            return ["schema_version is %r, which is not a MAXGEN stamp (write \"2.0\", \"2.1\" ...)"
                    % (err.instance,)]
        return ["%s is %r, which does not have the allowed form %s"
                % (where, err.instance, err.validator_value)]
    if kind is None or err.schema is False:
        key = _forbidden_key(err, doc) if doc is not None else None
        if key is not None:
            where = _where(list(err.absolute_path) + [key])
        return ["%s is not allowed here" % where]
    if kind == "minLength":
        if err.instance == "":
            return ["%s is empty" % where]
        return ["%s is shorter than %s character(s)" % (where, err.validator_value)]
    if kind == "maxLength":
        return ["%s is longer than %s characters" % (where, err.validator_value)]
    if kind == "minItems":
        return ["%s needs at least %s item(s)" % (where, err.validator_value)]
    if kind == "maxItems":
        return ["%s has more than %s item(s)" % (where, err.validator_value)]
    if kind == "maxContains":
        return ["%s has more than %s live item(s); at most %s are allowed"
                % (where, err.validator_value, err.validator_value)]
    if kind in ("minContains", "contains"):
        return ["%s does not hold an item of the required kind" % where]
    if kind == "uniqueItems":
        return ["%s holds the same value twice" % where]
    if kind == "dependentRequired":
        lines = []
        for prop, needs in (err.validator_value or {}).items():
            if isinstance(err.instance, dict) and prop in err.instance:
                for need in needs:
                    if need not in err.instance:
                        lines.append("%s: '%s' needs '%s' as well" % (where, prop, need))
        return lines or ["%s: %s" % (where, err.message)]
    if kind == "anyOf":
        forms = _required_forms(err.validator_value)
        if forms:
            return ["%s needs one of: %s" % (where, "; ".join(forms))]
        branches = _branch_lines(err)
        return ["%s fits none of the allowed forms: %s" % (where, "; ".join(branches))] \
            if branches else ["%s fits none of the allowed forms" % where]
    if kind == "oneOf":
        forms = _required_forms(err.validator_value)
        if not err.context or all(not plain(sub) for sub in err.context):
            if forms:
                return ["%s matches more than one allowed form; give exactly one of: %s"
                        % (where, " / ".join(forms))]
            return ["%s matches more than one allowed form; it must match exactly one" % where]
        if forms:
            return ["%s needs exactly one of: %s" % (where, " / ".join(forms))]
        branches = _branch_lines(err)
        return ["%s fits none of the allowed forms: %s" % (where, "; ".join(branches))] \
            if branches else ["%s fits none of the allowed forms" % where]
    if kind == "not":
        banned = err.validator_value if isinstance(err.validator_value, dict) else {}
        if isinstance(banned.get("required"), list):
            return ["%s may not have %s together" % (where, " and ".join(banned["required"]))]
        pairs = _required_forms(banned.get("anyOf"))
        if pairs:
            return ["%s may not combine these fields: %s" % (where, "; ".join(pairs))]
        return ["%s has a combination of fields that is not allowed" % where]
    return ["%s: %s" % (where, err.message)]


def _path_key(err):
    return [(0, p, "") if isinstance(p, int) else (1, 0, str(p)) for p in err.absolute_path]


def explain(validator, doc) -> list:
    """Every problem with one document, in plain English, in a fixed order (by where it is).
    The first line is "the first error" that the fixture tests compare against."""
    errors = sorted(validator.iter_errors(doc), key=_path_key)
    lines = []
    for err in errors:
        for line in plain(err, errors, doc):
            if line not in lines:
                lines.append(line)
    if errors and not lines:   # never hide a failure behind the filtering above
        lines = ["%s: %s" % (_where(e.absolute_path), e.message) for e in errors]
    return lines


# ------------------------------------------------------------------ references

def _iter_dicts(node, path=()):
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _iter_dicts(value, path + (key,))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _iter_dicts(value, path + (i,))


def _labels_on_record(record):
    mentions = {m.get("mention_id") for m in record.get("persons_mentioned", []) if isinstance(m, dict)}
    events = {e.get("ref") for e in record.get("events_documented", []) if isinstance(e, dict)}
    regions = set()
    for copy in record.get("saved_copies", []) or []:
        for region in copy.get("regions", []) or []:
            regions.add(str(region.get("region_id", "")).lower())
    for ref in record.get("media_refs", []) or []:
        regions.update(str(r).lower() for r in ref.get("region_ids", []) or [])
    return mentions, events, regions


def check_evidence_links(name, doc, folder_docs) -> list:
    """No invented sources: every evidence link names documents (and labels on them) that
    are in this folder."""
    problems = []
    for path, node in _iter_dicts(doc):
        if not path or path[-1] != "evidence" or not isinstance(node, dict):
            continue
        where = _where(path)
        basis = node.get("basis")
        if basis == "document":
            rid = str(node.get("source_record_id", "")).lower()
            record = folder_docs["record"].get(rid)
            if record is None:
                problems.append("%s cites record %r, but no MaxRecord with that record_id is in "
                                "this folder. A claim with no document behind it is an invented "
                                "source." % (where, node.get("source_record_id")))
                continue
            mentions, events, regions = _labels_on_record(record)
            for key in ("mention_id", "subject_mention_id"):
                if key in node and node[key] not in mentions:
                    problems.append("%s.%s %r is not a line (mention) on record %s"
                                    % (where, key, node[key], rid))
            if "record_event_ref" in node and node["record_event_ref"] not in events:
                problems.append("%s.record_event_ref %r is not an event entry on record %s"
                                % (where, node["record_event_ref"], rid))
            if "region_id" in node and str(node["region_id"]).lower() not in regions:
                problems.append("%s.region_id %r is not a marked area on record %s"
                                % (where, node["region_id"], rid))
        elif basis == "imported":
            rid = str(node.get("import_record_id", "")).lower()
            if rid not in folder_docs["record"]:
                problems.append("%s cites import file record %r, but it is not in this folder"
                                % (where, node.get("import_record_id")))
            for i, cite in enumerate(node.get("tree_citations", []) or []):
                cid = str(cite.get("record_id", "")).lower()
                if cid and cid not in folder_docs["record"]:
                    problems.append("%s.tree_citations.%d names record %r, but it is not in this "
                                    "folder" % (where, i, cite.get("record_id")))
        elif basis == "dna":
            did = str(node.get("dna_id", "")).lower()
            test = folder_docs["dna"].get(did)
            if test is None:
                problems.append("%s cites DNA test %r, but no MaxDNA with that dna_id is in this "
                                "folder" % (where, node.get("dna_id")))
            else:
                known = {str(m.get("match_id", "")).lower() for m in test.get("matches", []) or []}
                for mid in node.get("match_ids", []) or []:
                    if str(mid).lower() not in known:
                        problems.append("%s cites match %r, which is not a match on DNA test %s"
                                        % (where, mid, did))
        elif basis == "conclusion":
            tid = str(node.get("conclusion_task_id", "")).lower()
            task = folder_docs["task"].get(tid)
            if task is None or not (task.get("result") or {}).get("proof"):
                problems.append("%s cites proof task %r, but no MaxTask with that task_id and a "
                                "proof is in this folder" % (where, node.get("conclusion_task_id")))
        elif basis == "search":
            tid = str(node.get("task_id", "")).lower()
            task = folder_docs["task"].get(tid)
            steps = {s.get("step_no") for s in ((task or {}).get("result") or {}).get("steps", []) or []}
            if task is None or node.get("step_no") not in steps:
                problems.append("%s cites search step %r of task %r, but that task and step are "
                                "not in this folder" % (where, node.get("step_no"), node.get("task_id")))
    return problems


def load_reference_map(here: Path = _HERE):
    """REFERENCES from the kit's maxgen_db.py 2.0 (next to this file, else ../agents/), or None.
    The map exists once, in maxgen_db.py; this checker only reads it."""
    for candidate in (here / "maxgen_db.py", here.parent / "agents" / "maxgen_db.py"):
        if not candidate.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("_maxgen_db_for_check", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            continue
        refs = getattr(module, "REFERENCES", None)
        if refs:
            return list(refs)
    return None


def _entry_fields(entry):
    if isinstance(entry, dict):
        return entry.get("path"), entry.get("target")
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        return entry[0], entry[1]
    return None, None


def _values_at(node, segments):
    """Values at a dotted path: 'a.b[].c' (a list step is 'name[]'); '**' matches any depth."""
    if not segments:
        yield node
        return
    seg, rest = segments[0], segments[1:]
    if seg == "**":
        yield from _values_at(node, rest)
        if isinstance(node, dict):
            for value in node.values():
                yield from _values_at(value, segments)
        elif isinstance(node, list):
            for value in node:
                yield from _values_at(value, segments)
        return
    if isinstance(node, list):
        for value in node:
            yield from _values_at(value, segments)
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
            yield from _values_at(item, rest)
    elif not many:
        yield from _values_at(value, rest)


def check_reference_map(name, kind, doc, folder_docs, references):
    """Whole-document references from the kit's map. A row whose path or target this version
    does not understand is counted, not guessed at."""
    problems, skipped = [], 0
    for entry in references:
        path, target = _entry_fields(entry)
        if not isinstance(path, str) or ":" not in path or target not in folder_docs:
            skipped += 1
            continue
        holder, _, dotted = path.partition(":")
        if holder not in ("*", kind):
            continue
        for value in _values_at(doc, [s for s in dotted.split(".") if s]):
            values = value if isinstance(value, list) else [value]
            for v in values:
                if isinstance(v, str) and v.lower() not in folder_docs[target]:
                    problems.append("%s names %s %r, but no %s with that id is in this folder"
                                    % (path, target, v, KIND_TITLE.get(target, target)))
    return problems, skipped


# ------------------------------------------------------------------ main

def main(argv=None):
    ap = argparse.ArgumentParser(description="Check MAXGEN 2.0 files against the schemas.")
    ap.add_argument("folder", help="the folder holding your MAXGEN .json files")
    ap.add_argument("--schemas", default=str(DEFAULT_SCHEMAS),
                    help="folder holding the twelve .schema.json files")
    ap.add_argument("--version", action="version", version="check_maxgen.py " + __version__)
    args = ap.parse_args(argv)

    registry, schemas, checkers = load_checkers(Path(args.schemas))

    skipped_formats = [f for f in ("uri", "date-time") if f not in FormatChecker.checkers]
    if skipped_formats:
        print("NOTE: this Python cannot check %s format(s). Those checks are SKIPPED, not passed."
              % " or ".join(skipped_formats))
        print()
    references = load_reference_map()
    if references is None:
        print("NOTE: the kit's reference map (REFERENCES in maxgen_db.py 2.0) was not found next to "
              "this file or in ../agents/. Evidence links are checked; other references are "
              "SKIPPED, not passed.")
        print()

    parsed = {}       # file name -> (kind, data)
    folder_docs = {kind: {} for kind in KIND_FILE}
    for f in sorted(Path(args.folder).glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            parsed[f.name] = ("bad", "not valid JSON: %s" % e)
            continue
        kind = kind_of(data)
        if kind is None:
            parsed[f.name] = ("other", None)
            continue
        parsed[f.name] = (kind, data)
        folder_docs[kind][str(data[KIND_KEY[kind]]).lower()] = data

    results, skipped_rows = {}, 0
    for name, (kind, data) in parsed.items():
        if kind == "other":
            continue
        if kind == "bad":
            results[name] = [data]
            continue
        if is_v1(data):
            results[name] = [V1_MESSAGE]
            continue
        problems = explain(checkers[kind], data)
        problems += check_evidence_links(name, data, folder_docs)
        if references is not None:
            more, skipped = check_reference_map(name, kind, data, folder_docs, references)
            problems += [p for p in more if p not in problems]
            skipped_rows = max(skipped_rows, skipped)
        results[name] = problems

    for name, (kind, _) in parsed.items():
        if kind == "other":
            print("SKIP  %s  (not a MAXGEN file)" % name)
    if references is not None and skipped_rows:
        print("NOTE: %d kind(s) of cross-reference inside files (for example a person's line on a "
              "record) are not checked here; the kit checks them when it stores the file." % skipped_rows)

    checked = [n for n, (k, _) in parsed.items() if k != "other"]
    if not checked:
        print("No MAXGEN files found in %s. Each MAXGEN file has one of these ids at its top level: "
              "%s." % (args.folder, ", ".join(key for key, _, _, _ in KINDS)))
        return 1

    failed = 0
    for name in checked:
        kind = parsed[name][0]
        label = KIND_TITLE.get(kind, "file")
        problems = results.get(name, [])
        if problems:
            failed += 1
            print("FAIL  %s  (%s)" % (name, label))
            for p in problems:
                print("        - %s" % p)
        else:
            print("PASS  %s  (%s)" % (name, label))

    print()
    print("%d file(s) checked: %d passed, %d failed."
          % (len(checked), len(checked) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
