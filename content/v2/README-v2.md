# MAXGEN 2.1 schemas

MAXGEN 2.1 is the current release: twelve files, being eleven record schemas plus one shared building-blocks file.
Every file is JSON Schema Draft 2020-12. The set began as 2.0; the 2.0 files are frozen in `archive/v2.0/`. The full
build specification, [`docs/standard/SPEC-2.0.md`](../docs/standard/SPEC-2.0.md), describes 2.0. For the additions
of 2.1, the v2.1 entry of [`docs/SCHEMA_CHANGELOG.md`](../docs/SCHEMA_CHANGELOG.md) is the description until the
specification is revised.

## The twelve files

| File | Title | One document is | Primary id |
|---|---|---|---|
| `maxgen-defs.schema.json` | MAXGEN shared building blocks | (not a record type: no document is valid against it) | -- |
| `raw-record.schema.json` | MaxRecord | one document, copied word for word | `record_id` |
| `person.schema.json` | MaxPerson | one person, as a researched conclusion (holds no family links) | `person_id` |
| `task-queue.schema.json` | MaxTask | one piece of work: research, an import, a review or a proof | `task_id` |
| `source.schema.json` | MaxSource | one work, collection or repository | `source_id` |
| `name.schema.json` | MaxName | knowledge about one name | `name_id` |
| `recognition.schema.json` | MaxRecognition | one machine reading of an image | `recognition_id` |
| `dna.schema.json` | MaxDNA | one person's DNA test and its matches (always private) | `dna_id` |
| `relationship.schema.json` | MaxRelationship | one family link, stored once: a `family`, a `parent_child` pair or an `association` | `relationship_id` |
| `place.schema.json` | MaxPlace | one real place with its history | `place_id` |
| `group.schema.json` | MaxGroup | one historical group (its own facts only; no member list) | `group_id` |
| `event.schema.json` | MaxEvent | the identity of one occasion (thin) | `occurrence_id` |

The shared file holds the parts every record schema uses: ids and small types, the date block, the place block,
the evidence link (the part of every claim that says which evidence it rests on), the quote check, saved copies, regions and media pointers, roles and event types,
the claim header, the common and lifecycle fields, and the task provenance block.

A document's kind is the first of these keys found at its top level: `dna_id`, `recognition_id`, `name_id`,
`relationship_id`, `occurrence_id`, `place_id`, `group_id`, `record_id`, `task_id`, `source_id`, `person_id`.
(MaxDNA also carries `person_id`, so the order matters.)

The 2.0 files are kept in `archive/v2.0/` and the 1.13 files in `archive/v1.13/`, each with its rules unchanged;
older versions are in the other `archive/` folders.

## Version policy

- Every document carries `schema_version`. The schemas accept any stamp `"2.<n>"` (pattern
  `^2\.(0|[1-9][0-9]{0,3})$`): `"2.0"`, `"2.1"` ... are accepted; `"2.01"`, `"1.13"` and a missing stamp are not.
- The 2.0 kit writes `"2.0"`. A document stamped with a newer minor is schema-checked first: if it passes it is
  stored with its own stamp; if it fails, the kit says to update its schemas folder and `maxgen_db.py`.
- From 2.0 on, every change inside 2.x is **additive**: a new optional property, a new enum value, a new `$defs`
  entry, a new record schema file, or a loosened constraint. Never: removing or renaming a property, making an
  optional property required, narrowing a pattern, range or enum, or changing what a value means. Files keep the
  stamp they were written with; there is no re-stamping.
- Readers that meet an enum value they do not know fall back safely: a vocabulary word is read as `other` (its
  `_as_written` words are shown); a kit-internal state fails closed
  ([specification 2.5.1](../docs/standard/SPEC-2.0.md#251-the-two-classes-of-enum)).
- 1.x files are not read by 2.0 and there is no migration. Backward compatibility with 1.x is not a goal.

## How references resolve

- Every file's `$id` is `https://opengenealogyai.org/schemas/maxgen/v2/<file name>`. The `/v1/` addresses stay
  with the archived 1.x files.
- A record schema points into the shared file only in this relative form:
  `{"$ref": "maxgen-defs.schema.json#/$defs/<name>"}`. It resolves against the referring file's `$id`, so it means
  `https://opengenealogyai.org/schemas/maxgen/v2/maxgen-defs.schema.json#/$defs/<name>`.
- Inside the shared file references are `{"$ref": "#/$defs/<name>"}`. A record schema's own parts live in its own
  `$defs` and are referenced the same way. No other external reference form is used.
- The shared file is never copied into the record schemas. Validators load all twelve files into one registry and
  never fetch anything over the network; the `$id` addresses do not need to resolve on the web.
- **Python** (`jsonschema` 4.18 or later with `referencing`): build a `referencing.Registry` whose `retrieve`
  function raises `NoSuchResource`, add every file under its `$id`, and validate with `Draft202012Validator(schema,
  registry=registry, format_checker=FormatChecker())`. The exact code is in
  [specification section 2.4](../docs/standard/SPEC-2.0.md#24-cross-file-references-the-exact-mechanism).
- **JavaScript** (Ajv 2020): call `ajv.addSchema(<maxgen-defs.schema.json>)` before compiling a record schema, with
  `new Ajv2020({strict: true, strictRequired: false, strictTypes: false, allErrors: true})` and `addFormats(ajv)`.
  The repository's validators do exactly this: `node validators/validate-any.js <file-or-folder>` checks any kind
  (picked by the key order above); `validate-person.js`, `validate-raw-record.js` and `validate-task-queue.js`
  check one kind each. All four share `validators/maxgen-ajv.js`.
