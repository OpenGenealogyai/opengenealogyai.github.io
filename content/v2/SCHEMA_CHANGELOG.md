# MAXGEN Schema Changelog

Version history for the MAXGEN schemas. Since v2.0 there are **eleven record schemas plus one
shared building-blocks file**: **MaxRecord**, **MaxPerson**, **MaxRelationship**, **MaxTask**,
**MaxSource**, **MaxPlace**, **MaxGroup**, **MaxEvent**, **MaxName**, **MaxRecognition**,
**MaxDNA**, and `maxgen-defs.schema.json`. The 1.x releases below had seven.

## Versioning policy — 2.x is additive only (as of v2.0)

All twelve files share **one MAXGEN version**, `2.0`. Every document carries `schema_version`,
and the schemas accept any `"2.<n>"` stamp (`"2.0"`, `"2.1"` …; not `"2.01"`, not `"1.13"`).

- **Within 2.x, every change is additive:** a new optional property, a new value in a list, a
  new shared definition in `maxgen-defs.schema.json` (a `$defs` entry), a new record schema
  file, or a loosened limit. **Never:** removing or
  renaming a property, making an optional property required, narrowing a pattern, range or
  list, or changing what a value means. A file valid under 2.0 stays valid under every 2.x.
- **Files keep the stamp they were written with.** The 1.x ritual of re-stamping every fixture
  at each release is dropped.
- A reader that meets a list value it does not know falls back safely: a vocabulary word is
  read as `other` (its `_as_written` words are shown); a state the kit manages fails closed
  (an unknown licence is read as `tier2-private`, an unknown status as not live).
- The kit refuses a document stamped with a newer minor than it knows only if the document
  fails its schemas (or is of a kind it does not know), and then says to update the schemas
  folder and `maxgen_db.py`. A stamp with a later major is refused with "written by MAXGEN
  3.x"; a malformed stamp with "not a MAXGEN stamp".
- **Major version** (`v2` → `v3`): breaking changes, a new `$id` namespace, and the
  maintainer's own approval.

From v1.3 to v1.13 the seven schemas versioned in **lockstep** (all bumped together): every release bumped every
schema's `schema_version` const, even schemas that did not change, and every fixture was
re-stamped. That history is kept below as written.

---

## v2.0 — 2026-10-16

**Driver:** the review of 16 October 2026 (a unanimous review of the whole schema set, plus two
critiques of the first build specification, 62 findings). It found that 1.13 kept family links
twice (on both people, kept in step by a two-sided rule), stored computed scores beside the
evidence, let a claim cite a record without saying where on it, had no record of its own for
places, groups or the occasions documents record, and could not let an adopted person follow
both their birth and their adoptive lines. The maintainer decided on one release, 2.0, with
eleven record schemas plus one shared file. The full specification is
[`docs/standard/SPEC-2.0.md`](standard/SPEC-2.0.md).

### What breaks from 1.x

- **The 2.0 kit does not read 1.x files**, and opens no database a 1.x kit made. There is no
  migration: backward compatibility with 1.x is not a goal (only the maintainer has 1.x data,
  and it is being redone under 2.0).
- **New `$id` namespace:** `https://opengenealogyai.org/schemas/maxgen/v2/<file name>`. The `/v1/`
  addresses stay with the archived 1.x files (`schemas/archive/`), whose rules are unchanged (the
  1.12 and 1.13 MaxRecord `archive_ref` description no longer gives a local folder as its example).
- Every record schema now points into `maxgen-defs.schema.json` (`{"$ref":
  "maxgen-defs.schema.json#/$defs/<name>"}`). Validators load all twelve files into one
  registry and never fetch anything over the network.
- Every object is closed: unknown field names are refused everywhere except `extensions`, a
  task's `payload`, and three holders of another program's own fields (`match_signals`,
  `citation_template.fields`, `original_key.parts`).

### Across every schema (in `maxgen-defs.schema.json`)

- **The evidence link (Citation A).** Every claim cites exactly one piece of evidence, with no
  exception: `document` (a record, the line holding the words, the event entry, the exact
  quote, and whether it is direct, indirect or negative), `imported` (a fact from an imported
  tree file), `dna` (permanent match ids), `conclusion` (an accepted proof) or `search` (a
  search step that found nothing). Replaces 1.13's bare `source_record_id`. The kit checks
  every quote against the record's words and stores the result.
- **The date block.** The date as stated (`as_written`) plus what it means: modifiers (about,
  before, by, after, between, from, to, from-to, or, text only), how it was obtained
  (`calculated` from an age, `interpreted`, `estimated`), precision, calendar kept separate from
  year start and style, dual dates, Quaker numbered months, BC years, month 13. Replaces the
  flat `year_min`/`year_max`/`date_type` keys. Bounds are computed when read, never stored.
- **The place block** on every claim and event entry: the place as written, an optional point
  with its uncertainty, and a `place_id` attached later by analysis.
- **The claim header** on every claim: a permanent `assertion_id`, the task that produced it,
  a status ladder (`proposed`, `proven`, `disputed`, `disproven`, `retracted`) written only by
  the kit, `supersedes_assertion_id`, privacy, and the other program's words for imported
  facts. Claims are append-only.
- **Living A.** Every document that names a person says whether that person may be alive; a
  document that leaves it out is refused; living means `tier2-private`. Privacy is computed when
  read, never from a stored copy.
- **Common and lifecycle fields:** `created_by_task_id`, notes, outside ids, import fields,
  and `lifecycle` (active, retracted, merged away) with undoable merge history, `distinct_from`
  and split history.
- **Licences:** `all_rights_reserved` added; one order from loosest to strictest.
- **Media** (folded in, not a schema): each file is described once, on the record that
  captured it (`saved_copies`, with a fingerprint and regions); others point at it by
  fingerprint, or, while an imported file is not yet on this computer, by its address
  (`location_uri`).
- **Imports keep everything:** memberships, partner items, mentions, event entries and tree
  citations carry their own `extensions`, so a field another program had and MAXGEN does not is
  kept where it belongs.
- **Provenance** (folded in): who did each task, written once by the kit, on MaxTask only.
- No stored scores anywhere: per-claim `confidence` stays; computed scores are worked out when
  read.

### MaxPerson (`person.schema.json`)

**Holds no family links.** Removed: `parent_assertions`, `child_assertions`,
`spouse_assertions` (to MaxRelationship); `composite_confidence` (a stored score);
`external_ids` (a mirror); `dna_evidence` (DNA is cited through the evidence link);
`place_registry` (to MaxPlace); `source_record_ids` (an index); `occupation_assertions` (now
`attribute_assertions` with `attribute_type: occupation`); `asserted_by`, `asserted_at`,
`last_updated_at`, `judge_approved`, `judge_approved_at` (task provenance, kit timestamps and
the database's review stage); `merge_status` (now `lifecycle`); the flat date keys and
`country_code`/`state_province` (now the date and place blocks). Changed: every claim gains the
claim header and an evidence link; photo claims point at a photograph record. Added:
`person_kind` and `placeholder_label`, attribute, non-event and group-membership claims,
`name_parts` and `name_status`, `attached_sources`, `same_as_candidates`. `is_living` and
`redistribution_license` become required, with no default; `name_assertions` is required for a
documented person.

### MaxRecord (`raw-record.schema.json`)

One original, one record, holding the whole entry. `transcription` becomes
`transcription_parts` with permanent line labels, `transcription_scope` (complete entry,
complete list, partial) and a kit-computed fingerprint. Added: `persons_mentioned` with a
permanent `mention_id`, the page's own role words and `is_living` for every person;
`events_documented`, the events the document records, each with `information` judged per event
(each gets an occasion id); `source_classification` as a required core field (it was under
`extensions`); `copied_from` (the copying chain, required for a derivative); `part_of_source_id`
and `locator`; a kit-built `original_key` that refuses a second copy of the same original;
`record_status` (amended, delayed, reissued); `saved_copies` and `media_refs`;
`citation_as_written` and `citation_template`. `source_url` becomes optional, but "where it is"
is required (an address, a saved copy, or a repository or source with a locator; never an
invented URL). `record_date` is the date the entry was written. `census_row` is renamed
`census_household`; new types include `birth_register`, `death_register`, `tree_import`,
`gazetteer_entry` and `dna_match_list`. Removed: `transcription`, `image_url`, `locations`,
`repository`, `collection` (now `_as_written` fields), `language`, `alternate_names`,
`digital_object_id`, `extracted_by`, `extracted_at`.

### MaxRelationship (`relationship.schema.json`) — new

Every family link, stored once. Three kinds: **`family`** (0 to 2 partners — a person, or an
"absent" partner the document mentions without naming — family facts such as marriage, banns,
divorce or a census together, and child memberships, each with a `set_kind`: `birth`,
`adoptive`, `step_household`, `foster`, `guardianship`, `sealing`, `alternative_hypothesis`,
`other`, a `child_order` and a kit-computed `is_preferred`); **`parent_child`** (one per parent
and child, with lineage facts such as `birth_parent`, `adoptive_parent`, `step_parent`, each
with a dimension: genetic, gestational, legal, social, religious, household, unknown, other); and
**`association`** (godparent, enslaved by, ancestor-descendant, employer, neighbour, friend,
multiple birth, other). No two-sided lock: MaxPerson keeps no copy of any link. A child can belong to
several parent sets, so an adopted person can trace both their birth and their adoptive
lineage. Agents never write these files: the kit's `link` command finds or makes them. Replaces
1.13's `parent_assertions`, `child_assertions`, `spouse_assertions`, `relationship_type` on
parents and the `ecclesiastical` type.

### MaxTask (`task-queue.schema.json`)

Removed: `contributor` (now `provenance`, written once by the kit), the top-level `review`
(reviews are now separate `judge_review` tasks, never overwritten), a required `created_at`
(now kit-owned). Added: `provenance`, `is_living_flag` (required), statuses `on_hold` and
`cancelled`, task types `find_original` and `import_file`, the verdict `inaccessible` (could
not search; never a dead end), and a closed `result` holding the search steps (the only search
log), a proof write-up (the single authority for "proven"), occurrence verdicts, reviews, run
decisions and FAN-club associates (the friends, associates and neighbours studied around one
person). A proof's parent-set changes can set a set's kind, make it the main set, add a
parent's existing link to it, or retract it.

### MaxSource (`source.schema.json`)

`source_url` becomes optional; the `provenance` block is dissolved (`status` → `link_status`,
`asserted_by` → `created_by_task_id`, `asserted_at` → `created_at`); `coverage.time_period`
becomes `coverage.span` (a date block, no year cap); places use the place block. Added:
`bibliographic` (title, creators, publisher, edition), `holdings` (which repository holds the
original), `is_living_flag` (required), a wider `source_type` list, `discovered_via`.

### MaxPlace (`place.schema.json`) — new

A slim place authority: one real place with its names, kinds, parent places (dated, with
evidence) and coordinates (with their uncertainty when the evidence states one, never
invented) over time, related places (formed from, split from, merged into, succeeded by,
preceded by), gazetteer ids as outside ids, and undoable merges and `distinct_from`.
Every place claim cites evidence (a gazetteer page is saved as a record first). Made only by
import and analysis tasks.

### MaxGroup (`group.schema.json`) — new

One historical group — an emigrant company, a regiment, a congregation, a household that
continued across documents — with its kind, its own names and dated facts, the larger groups it
belonged to, and the documents that describe it, once. **No member list:** a
membership is a claim on the person. FAN clusters (the friends, associates and neighbours
studied around one person) stay as MaxTask results. Made only by import and analysis tasks.

### MaxEvent (`event.schema.json`) — new

The identity of one occasion, thin: an occurrence id, its event type, where it came from
(`origin`: a record's event entry, an import, or an analysis), outside ids, notes, pointers to
the preferred date and place claims, links to related occasions, and same-occasion merges; an
occasion made by analysis may also carry a reasoned conclusion that no single document states,
citing an accepted proof. Made by the kit for every event entry on a record, and by importers
and analysis tasks. It holds no participant list and no copies of the dates documents give: who
took part is worked out from role claims.

### MaxName (`name.schema.json`)

The 1.13 shape is kept. `contributor` is removed (the task's provenance replaces it). Every
variant and cognate becomes a claim, with a kit-owned `assertion_id`, a status and a required
evidence link and confidence. Lifecycle fields added; `redistribution_license` required.

### MaxRecognition (`recognition.schema.json`)

The 1.13 fields are kept. `is_living_flag` added (required); `contributor` removed;
`image_url` and `raw_record_id` both optional but one required; `copy_sha256` and `region_id`
added (the same copy and region evidence cites); `processing_date` renamed `processed_at`;
`review_status`, `is_human_reviewed` and `is_independent` removed (reviews are tasks;
independence is computed).

### MaxDNA (`dna.schema.json`)

The 1.13 fields are kept, always `tier2-private`. `asserted_by` and `asserted_at` removed; the
top-level `notes` string replaced by the shared notes list; `kit_id_hash` optional with `kit_id_status`; `test_date` a
date block; `external_ids` becomes `kit_aliases`; `source_records` becomes `source_record_ids`.
Each match becomes a claim with a permanent `match_id`, its own `is_living`, and evidence citing
the saved match list (a `dna_match_list` record, which the schema keeps living and
`tier2-private`); predicted relationships keep the company's own words and probabilities. A
match between two tests in one database is stored once. Kit numbers are never stored: their
fingerprints are keyed with a secret only the person's own database holds.

### Verification

- Schema and fixture tests: `tests/test_schemas_v2.py` and `tests/test_check_maxgen_v2.py`
  (all fixtures made up, under `tests/fixtures/v2/`).
- Kit tests: `docs/agents/test_maxgen_db.py` and `docs/agents/test_probable_pedigree.py`.

---

## v1.13 — 2026-08-14

**Driver:** a name-research project hit a structural wall during its first
calibration batch. It could not record a single cognate, because MaxName required
`related_name_id` — a UUID for a record that does not exist yet. Cognate
relationships are *discovered during* the research on the first name and would
have to be thrown away and re-derived later. This release fixes that, and clears
the version-stamp drift found while auditing.

### MaxName (`name.schema.json`) — cognates become two-pass

`cognates[].related_name_id` is **no longer required**. The item-level constraint
is now:

```json
"required": ["cognate_id", "relationship_type", "direction"],
"anyOf": [
  { "required": ["related_name_id"] },
  { "required": ["related_canonical_form"] }
]
```

At least one identifier must still be present — a cognate pointing at nothing is
still invalid. The two legal states are:

| State | Carries | Meaning |
|---|---|---|
| **Unresolved** | `related_canonical_form` only | Recorded during first-pass research. Correct and publishable, but not yet joinable. |
| **Resolved** | `related_name_id` (+ optionally the readable form) | A linking pass has matched it to a real MaxName record. |

`related_canonical_form` changes role: it was a denormalized convenience copy
alongside a mandatory UUID; it is now the field that makes first-pass capture
possible at all.

**Backward compatible.** Every v1.12 cognate carried `related_name_id`, so all
existing records remain valid — this release only *removes* a constraint. Readers
must now handle a cognate whose `related_name_id` is absent; treat it as an
unresolved link rather than an error.

New fixture: `test/fixtures/name/valid-v113-unresolved-cognate.json` (Seán) covers
all three cases — two unresolved cognates, one resolved.

### Housekeeping — version-stamp drift corrected

Auditing for this release surfaced metadata that the v1.12 bump missed. None of
it affected validation, but all of it misreported the standard's version:

- **`$id` normalized across all seven schemas** to
  `https://opengenealogyai.org/schemas/maxgen/v1/<name>.schema.json`.
  `name.schema.json` was pinned at `/schemas/name/1.11` and
  `recognition.schema.json` at `/schemas/recognition/1.10` — both stale, and both
  would drift again at every bump. The version belongs in the `schema_version`
  const and in `schemas/archive/`, not in the `$id`.
- **`description` version stamps corrected.** `dna` said v1.4, `name` said v1.11,
  `task-queue` said v1.5, and `recognition` had no stamp at all. All seven now
  read `MAXGEN v1.13 —`.
- **Changelog header** said "the four MAXGEN schemas"; there are seven.

### Unchanged in v1.13

MaxRecord, MaxPerson, MaxSource, MaxDNA, MaxRecognition, and MaxTask have no
structural changes — only the lockstep `schema_version` const and the description
stamp. Records valid under v1.12 remain valid; re-stamp `schema_version` to
`"1.13"`.

### Verification

- 75/75 fixtures pass (74 re-stamped + 1 new)
- All seven schemas validate against the draft-07 metaschema
- Cognate behavior tested in all five cases: canonical-form-only ✓ valid,
  UUID-only ✓ valid, both ✓ valid, neither ✓ rejected, missing `direction` ✓ rejected
- The canonical schemas and the `schemas/archive/v1.13/` copies verified
  byte-identical by SHA-256

---

## v1.12 — 2026-08-14

### MaxPerson — expanded relationship vocabulary + time-bounded gender records

**Typed parent relationships.** `parent_assertions[].relationship_type` and
`child_assertions[].relationship_type` expand from 5 values to 10, with renamed tokens that
are more precise and self-documenting:

| Old (v1.11) | New (v1.12) | Notes |
|---|---|---|
| `biological` | `birth_parent` | Clearer: the relationship is "birth parent", not just "biological" |
| `adoptive` | `adoptive_parent` | Noun-phrase form, parallel to the others |
| `step` | `step_parent` | Same |
| `foster` | `foster_parent` | Same |
| `unknown` | `unknown` | Unchanged |
| — | `donor_parent` | Sperm or egg donor — genetic link, no social/legal relationship |
| — | `gestational_carrier` | Surrogate with no genetic contribution |
| — | `guardian` | Legal guardianship without formal adoption |
| — | `social_parent` | Raised the child without legal mechanism (common historically) |
| — | `ecclesiastical` | LDS sealing or godparent in traditions where kinship is recognized |

Multiple simultaneous types per parent entry are allowed and expected: e.g. a person who was
both a `birth_parent` (DNA evidence, confidence 0.97) AND an `adoptive_parent` (court records,
confidence 1.0) can have both assertions without either overwriting the other.

All existing fixtures bumped: `biological` → `birth_parent`, `adoptive` → `adoptive_parent`,
`step` → `step_parent`, `foster` → `foster_parent`.

**Time-bounded gender records.** `gender_assertions[]` items gain three new optional fields:

- `evidence_type` enum: `assigned_at_birth` | `lived_as` | `self_identified` | `legal` | `documentary` — distinguishes what KIND of gender claim is being made (a birth record's assigned sex is different from a census record's lived gender)
- `date_start` (ISO 8601 string) — from when this gender assertion applies
- `date_end` (ISO 8601 string) — through when this assertion applies

Handles: the Billy Tipton problem (person lived as male, assigned female at birth), transgender
history, intersex conditions, two-spirit / indigenous gender traditions, and any case where a
person's documented gender changed over time. Living trans persons are protected by the existing
`is_living=TRUE` + `tier2-private` gate.

### MaxSource — sub-collection linking + record count as core field

**`parent_source_id`** (UUID, optional, root-level): links a sub-collection entry to its parent
institution. Without this field, 173 individual query-pattern entries for one Swiss cantonal
archive each embed independently, flooding vector search results with near-duplicate embeddings.
With it, sub-collections can be grouped by parent for display and rolled up for ranking.

**`coverage.record_count_estimate`** (integer, optional): moved from `extensions{}` to the core
`coverage` object. This is the primary ranking signal for the vector-DB router ("go to
FamilySearch with 14B records before a county society with 2K"). Having it in `extensions{}`
caused inconsistent key names across collectors; core placement gives it schema validation and
a stable canonical path.

**Description fixed:** the `description` field previously said "MAXGEN v1.7" (stale); now reads
"MAXGEN v1.12".

### MaxRecord — extensions{}, archive_ref, locations[] array

Three additions requested by a data-collection project after encountering the
`additionalProperties: false` wall with no escape hatch:

**`extensions{}`** — open-schema namespace for source-specific fields. Same pattern as
MaxPerson, MaxTask, MaxSource. Gives every producer a legal home for `groom_name`,
`battle_unit`, `cemetery`, `liber`, `folio`, `ark_id`, and any other source-specific fields
without requiring a schema bump. Omit it, the record still validates; add whatever keys you
need inside it.

**`archive_ref`** (string, optional) — local file path to the canonical MaxRecord JSON in a
local archive. Supports the dual-emit architecture: the queue is the transport format the embedder
reads; the local archive holds the canonical, schema-validated MaxRecord. Populated by
producers using dual-emit; omit otherwise.

**`locations[]`** (array, optional) — replaces the single `location` object. Each item adds a
`location_type` field (`event_location` | `birth_place` | `death_place` | `burial_place` |
`residence` | `origin` | `destination` | `other`) plus `latitude`/`longitude`. Multi-place
records (immigration: origin + destination; death cert: death_place + burial_place) now have a
clean model. All 19 existing fixtures migrated: `location: {...}` → `locations: [{location_type:
"event_location", ...}]`.

### All schemas

All 7 schemas at `"const": "1.12"`. All 74 test fixtures re-stamped and validated.
Archived at `schemas/archive/v1.12/`.

---

## v1.11 — 2026-07-26

### MaxName — new schema (`schemas/name.schema.json`)

Seventh MAXGEN schema. A structured open standard for historical name variants — the
first machine-readable format that captures typed, directed, provenance-bearing name
relationships across languages, scripts, locales, and time periods.

**Why this schema exists.** Every genealogy search engine fails on name matching because
names are not stable strings — they are living things that drift across centuries, clerks,
borders, and languages. "Whitcombe" and "Whitcomb" and "Witcombe" and "Whytcombe" are the
same family. "Müller" and "Mueller" and "Miller" are the same occupation-surname in three
forms. "John" and "Ivan" and "Giovanni" and "Seán" are the same baptismal name in four
languages. Without a structured record that says so — with provenance, time scope, and
confidence — no machine knows this. MaxName is that record.

**Design review.** A design review on 2026-07-26 led to 10 specific improvements:

1. **URI-extensible `variant_type`** — changed from a closed enum to a free
   string with documented standard values. New variant types (forced_substitution,
   administrative_reassignment, etc.) don't require a schema bump.
2. **`variant_id` UUID on each variant** — every variant entry is independently
   addressable, citable, and retractable without touching the parent record.
3. **`disputed_by[]` per variant** — scholarly disputes are preserved, not
   silently resolved. A variant with disputes is retained and flagged; both views coexist.
4. **`cognate.direction`** — `this_derives_from | this_is_source_of | parallel_cognate`
   makes the directional relationship explicit. Solves the "which way does the arrow point?"
   ambiguity in cross-language cognate links.
5. **`cognate.attestation_source`** — the cognate relationship is itself a scholarly
   claim. It needs provenance, not just assertion.
6. **`phonetic_key.algorithm_version`** — prevents silent mismatches when algorithm
   implementations change. A Soundex code computed in 2026 may differ from one computed in 2040.
7. **`transliteration_standard`** per variant — captures the standard used when
   transliterating from non-Latin scripts (ALA-LC, BGN/PCGN, Pinyin, ISO 233). Enables
   reverse-transliteration and cross-standard comparison.
8. **`regional_canonical[]`** array — non-English preferred forms are first-class.
   Seán, Eoin, Ioan, Johann are not "variants of John" — they are the preferred canonical forms
   in their own language communities.
9. **`legal_substitution` variant type** — captures name changes imposed by
   law or immigration administration (e.g. forced anglicization of immigrant names at Ellis Island).
10. **`canonical_script`** field — drives rendering decisions for bi-directional
    text, font selection, and search index configuration.

**Research gap confirmed.** No existing open, structured, machine-readable standard captures
typed, directed, provenance-bearing historical name variant relationships. Reviewed:
GEDCOM X (no typed variants, no time/place scope), WeRelate (wiki only, no schema),
Behind the Name API (read-only, proprietary, no cognate provenance), JRC-Names (NLP tool,
not genealogical), LCNAF (authority control, not variant-network standard), VIAF (aggregator,
not a schema), schema.org (no historical scope), HistNorm (NLP research, not standards-track),
Popolo (political entities only). MaxName fills a genuine gap.

**Schema fields:**
- Required: `name_id` (UUID), `schema_version` (const "1.11"), `canonical_form`, `name_type`
- Optional top-level: `canonical_script`, `regional_canonical[]`, `gender`, `language_of_origin`
  (ISO 639-3), `etymology`, `see_also_urls[]`, `redistribution_license`, `contributor{}`, `extensions{}`
- `variants[]`: each with `variant_id` (UUID), `variant_text`, `variant_type` (URI-extensible),
  `locale`, `language`, `script`, `transliteration_standard`, `period_start`, `period_end`,
  `frequency` (rare|occasional|common|dominant), `attestation_source`, `notes`, `confidence`,
  `contributor{}`, `disputed_by[]`
- `phonetic_keys{}`: soundex, nysiis, metaphone, double_metaphone, daitch_mokotoff, beider_morse
  — each with `value/values` + `algorithm_version`
- `cognates[]`: each with `cognate_id`, `related_name_id`, `related_canonical_form`,
  `relationship_type` (etymological_root|cross_language_cognate|derivative|contracted_form),
  `direction` (this_derives_from|this_is_source_of|parallel_cognate), `attestation_source`,
  `notes`, `contributor{}`

**Fixtures added (4):**
- A surname fixture with 4 spelling variants, including a disputed variant classification,
  full phonetic keys, and an etymological_root cognate link to an older root name.
- `test/fixtures/name/valid-v111-john-given.json` — John given name with regional_canonical[]
  for Seán, Eoin, Ioan, Johann; 4 variants (Jon, Joannes, Johnny, Jack); 3 cognate links
  (Johann, Giovanni, Ivan) all parallel_cognate direction.
- `test/fixtures/name/valid-v111-ai-contributed.json` — Müller surname showing AI-contributed
  records (contributor_type: "ai"), umlaut variant handling, emigration_adaptation Miller, and
  extensions{} with corpus frequency data.
- `test/fixtures/name/valid-v111-disputed-variant.json` — O'Brien/Ó Briain showing the
  disputed_by[] mechanism with two competing scholarly sources disputing the etymology of Brian
  (strength vs. hill vs. Brythonic root). Neither dispute is resolved — both are preserved.

### All other schemas (const bump only)
MaxRecord, MaxPerson, MaxTask, MaxDNA, MaxSource, MaxRecognition: `schema_version` const
1.10 → 1.11. No field changes.

### Archive
`schemas/archive/v1.11/` — all 7 schemas snapshotted at v1.11.

---

## v1.10 — 2026-07-01

### MaxRecognition — probabilistic transcription fields

Three new optional fields on `schemas/recognition.schema.json`, approved unanimously in
design review.

**`word_confidences[].reading_type`** (enum, optional)
Captures *how* each word was produced:
- `observed` — directly read from the image (default when omitted)
- `inferred_context` — logically filled in from surrounding entries
- `inferred_pattern` — filled in from known naming/place conventions
- `partial_read` — some characters legible, remainder inferred

**`word_confidences[].inference_notes`** (string, optional)
Free-text audit trail required whenever `reading_type` is not `observed`.
Explains the basis for the inference so future AI or human reviewers can
judge whether it was sound. Example: *"5 of 6 surrounding entries read
Whitcombe; characters 4-7 are water-damaged and unreadable."*

**`searchable_variants[]`** (string array, top-level, optional)
All possible full-text spellings this record should be findable under —
including uncertain readings, alternative spellings, and normalized forms.
Populated by the contributor at submission time or by a post-processing
normalizer. If omitted, search engines fall back to `output_text` only.
Enables a record to be found under "Whitcombe" AND "Whitcambe" without
duplicating the record.

**Design philosophy captured:**
Traditional genealogy treats a partial read as a dead end. MAXGEN treats
uncertainty as a first-class value: record everything observed and inferred,
label which is which, rate confidence, and let future AI runs re-examine the
same image to either confirm or correct the inference. Ten years of AI passes
over the same document will converge on the correct reading — and the audit
trail will show exactly how.

### All other schemas (const bump only)
MaxRecord, MaxPerson, MaxTask, MaxDNA, MaxSource: `schema_version` const 1.9 → 1.10.
No field changes.

---

## v1.9 — 2026-07-01

### New schema: MaxRecognition (`schemas/recognition.schema.json`)

Sixth MAXGEN schema. Provenance record for an OCR or HTR transcription of a
genealogical document image. Design principles:

- **One schema, not two.** `recognition_type: "ocr" | "htr"` is the discriminator.
  Modern AI models (and many humans) do both; splitting would duplicate 90% of fields.
- **Unified contributor object.** Human transcribers and AI models share the same
  `contributor{}` shape with a `contributor_type: "human" | "ai"` discriminator.
  Human-specific fields: credentials[], specialty_scripts[], specialty_languages[],
  specialty_regions[], records_transcribed (reputation signal), training_notes.
  AI-specific fields: model_name, model_provider, model_type, prompt{}, fine_tuning{}.
- **Self-reported confidence with alternatives.** `word_confidences[]` allows
  contributors to flag uncertain words and provide alternative readings with
  relative probabilities. AI models derive these from token log-probabilities;
  humans estimate from legibility.
- **Cross-run consensus via group ID.** `consensus_group_id` links multiple
  independent transcriptions of the same image. `is_independent: true` prevents
  double-counting correlated runs. Consensus *calculation* belongs in the matching
  engine, not the schema — the schema only stores the evidence.
- **HTR-specific:** `script_type` enum (kurrent, gothic_fraktur, secretary_hand,
  arabic, etc.), `approx_century` for routing to the right model.
- **OCR-specific:** `font_type`, `scan_dpi`.
- **Standard redistribution_license enum** reused from MaxRecord.

### All other schemas (const bump only)
MaxRecord, MaxPerson, MaxTask, MaxDNA, MaxSource: `schema_version` const 1.8 → 1.9.
No field changes.

### Fixtures added
- `test/fixtures/recognition/valid-v19-ai-htr-kurrent.json` — Claude Sonnet HTR
  of a 19th-century Württemberg Kirchenbuch entry, with word-level confidence and
  alternatives on the uncertain surname spelling.
- `test/fixtures/recognition/valid-v19-human-htr-kurrent.json` — Certified
  genealogist (14,720 records transcribed) transcribing the same image, linked via
  `consensus_group_id`. The two runs disagree on "Mäier" vs "Maier" — the matching
  engine will surface this for review.

---

## v1.8 — 2026-06-25

**Theme:** Attribution — structured human-contributor credit for CC-BY/CC-BY-SA compliance.
**Additive / backward-compatible.** All five schemas stamped `1.8`; 68/68 fixtures pass.
Council: Opus operator **PROCEED** (unanimous — no caution required; zero blast radius,
all fields optional, legally-motivated).

### `attribution{}` object — added in three places

One reusable object shape, placed wherever a human created or manages the original material:

```json
"attribution": {
  "contributor_name": "Robert Chen",
  "contributor_url": "https://www.findagrave.com/user/profile/12345",
  "contributor_role": "photographer",
  "source_organization": "Find a Grave",
  "source_organization_url": "https://www.findagrave.com"
}
```

`contributor_role` enum: `photographer` | `transcriber` | `compiler` | `manager` |
`donor` | `submitter` | `indexer` | `translator` | `other`.

**Where it appears:**
- **MaxRecord** (top-level) — credit the human who photographed, transcribed, or donated
  the original document. Distinct from `extracted_by` (the AI agent that processed it).
- **MaxPerson `photo_assertions[]`** — replaces the bare `photographer: string` field with
  the structured object. Existing bare-string data remains valid in `extensions{}` until
  migrated.
- **MaxPerson `external_id_assertions[]`** — credit the WikiTree volunteer who manages a
  linked profile, or the Find a Grave contributor who created a memorial.

**Why now:** CC-BY and CC-BY-SA licenses legally require a credit link. Without a structured
`contributor_url` field there is nowhere to store the legally-required hyperlink when
ingesting Find a Grave photos (CC-BY) or WikiTree / FS Research Wiki content (CC-BY-SA)
at scale. The previous bare `photographer: string` was legally insufficient for redistribution.

**New fixture:** `test/fixtures/raw-record/valid-v18-gravestone-with-attribution.json` —
Find a Grave gravestone photo (CC-BY) credited to volunteer Robert Chen.

---

## v1.7 — 2026-06-25

**Theme:** Source discovery — a neutral way to describe WHERE genealogy data lives,
so an AI/harvester can answer "where do I find records for X?" and route to the right
site. **Additive / backward-compatible** (all five schemas now stamped `1.7`; 67/67
fixtures pass). Reviewed by three-brain council (engineer + strategist + Opus operator):
GPT + Opus **PROCEED WITH CAUTION**; Gemini raised two objections — both addressed below.

### MaxSource — new schema (the 5th MAXGEN schema)
A SOURCE-DISCOVERY entry: a catalogued pointer to a data source, never genealogical
facts about persons. Fields: `source_type` (directory_index | research_wiki |
record_catalog | record_database | archive | dna_database | library | newspaper_archive
| forum | blog | society | other); `coverage{places[] (mirrors MaxRecord location),
record_types[], time_period, languages[], ethnic_or_religious_groups[]}`;
`access{access_method (free_web | paid_subscription | api | bulk_download | scrape_only
| onsite_only), requires_auth, api_base_url, robots_allowed, tos_url, crawl_notes}`;
`embedding_text` (denormalized text embedded into the vector DB — the semantic routing
brain); `provenance{discovered_via, external_ref, status (active | dead_link | superseded
| unverified), asserted_by/at, last_verified_at}`; `extensions{}`.

### Council fixes applied (Gemini's design objection)
- **Neutrality:** dropped a bespoke `visibility` enum in favor of the standard MAXGEN
  `redistribution_license` enum (CC0 | CC-BY | CC-BY-SA | public-domain | tier2-private),
  consistent with MaxRecord. `tier2-private` = a privately-held routing entry that MUST
  NEVER appear in any open dataset, public share, export, public embedding, API response,
  or commit.
- **Product infra out of core:** harvester routing (`harvester_adapter`, `query_pattern`)
  moved to `extensions{}` so the standard stays implementation-neutral.

### Note on Cyndi's List (recorded, not resolved by the standard)
Gemini flagged that bulk-ingesting a third party's curated directory (e.g. Cyndi's List)
is a copyright/ToS exposure even for private use; this is an operator/owner decision, NOT
something the schema sanctions. The standard only provides `redistribution_license:
tier2-private` so any such entry, if held, is never republished.

---

## v1.6 — 2026-06-02

**Theme:** Cross-database identity — merge the same person across FamilySearch,
WikiTree, Ancestry, Wikidata, Find a Grave, etc. **Additive / backward-compatible**
(all four schemas stamped `1.6`; 65/65 fixtures pass). Reviewed by three-brain
council (engineer + strategist + Opus operator + jury): unanimous **PROCEED WITH
CAUTION** — design endorsed, with one required privacy guardrail (below).

### MaxPerson — `external_id_assertions[]` (new)
Scored, sourced, **reversible** links from this *conclusion-person* to the same
individual as represented in an external database (a *persona*). Implements the
GEDCOM X persona/conclusion model and mirrors Wikidata `sameAs` practice, but with
genealogy-grade confidence + provenance. Each entry: `system` (open string — ANY
database works), `external_id`, `url`, `confidence` (0–1), `match_method`
(`exact_id_from_source` | `probabilistic_match` | `dna_confirmed` | `hub_crosswalk`
| `manual`), `match_signals{}`, `status` (`active` | `disputed` | `retracted`),
`asserted_by`/`asserted_at`, `retracted_at`.

Key properties:
- **Multiple entries per system are allowed** — external DBs (esp. FamilySearch)
  hold duplicate profiles for one person.
- **Wrong links are retracted (status), never deleted** — auditable, reversible.
- **Never forces one "truth"** — a link is a scored claim, not a fiat merge; this is
  the substrate an AI scorer reads/writes when reconciling across databases.
- Real-world notes baked into the field docs: Ancestry IDs are tree-scoped
  (`tree_id:person_id`); FamilySearch PIDs can become redirects; Wikidata QID is the
  best join hub (P6577 FamilySearch, P2949 WikiTree, P535 Find a Grave → `hub_crosswalk`).

### `external_ids` (existing) → now a denormalized mirror
The flat `{system → id}` map is retained as a top-confidence-per-system convenience
mirror of `external_id_assertions[]`. The merge model (`merge_history` with
`merge_method: exact_external_id`, reversible) is unchanged.

### PRIVACY GUARDRAIL (normative — required by council before publish)
For any record where `is_living = TRUE` (tier2-private), `external_id_assertions`
**MUST NOT** appear in any public share, export, embedding, API response, or commit.
A cross-system identity crosswalk is a powerful de-anonymization vector — strip these
for living subjects like every other tier2-private field. This contract is written
into the field's own schema description so it travels with the standard. (Living
fixture `valid-living-person-tier2.json` carries no external_id_assertions,
demonstrating the gate.)

### Lockstep + fixtures
MaxRecord, MaxTask, MaxDNA stamped `schema_version: "1.6"`. +1 fixture
(`valid-v16-cross-db-identity` — multiple FamilySearch IDs incl. a duplicate, a
WikiTree + Wikidata hub crosswalk, and one `disputed` Ancestry link). 65/65 validate.

---

## v1.5 — 2026-06-02

**Theme:** MaxTask becomes a real distributed-work unit — structured results, a
color-coded verdict vocabulary, and the neutral half of a contributor model
(quality + privacy). **Additive / backward-compatible** — all new fields are
optional; pre-1.5 tasks validate unchanged. Lockstep: all four schemas stamped
`1.5`. 64/64 fixtures pass. Reviewed by three-brain council (verdict: PROCEED
WITH CAUTION — *split economics out of the standard*; see below).

### MaxTask — structured `result` (replaces the free-form blob)
A client-readable result object: `description`, `hypothesis_tested`,
`why_this_matters`, `what_we_did_and_result`, `how_results_affect_goal`,
`what_we_did_with_results`, `next_step`, `confidence`,
`next_directions_suggested[]`, and `steps[]` — **one entry per search performed**
(`action`, `source`, `source_url`, `query`, `found`, `verdict`, `confidence`).
`additionalProperties` stays `true` for extensibility.

### MaxTask — `verdict` enum + UI colors (new `$defs/verdict`)
`key_finding` (GREEN, confirmed/found), `usable` (BLUE, usable lead),
`inconclusive` (AMBER, mixed), **`ruled_out` (RED — a *disproof*: definitively
NOT the right person/record)**, `dead_end` (GREY — searched, found nothing),
`infra` (GREY — support task). `ruled_out` is deliberately distinct from
`dead_end`: a disproof is information; an empty search is not.

### MaxTask — neutral contributor / quality fields (IN the standard)
`parent_task_id`, `acceptance_criteria[]` (objective conditions defining a
complete result), `min_confidence`, `evidence_required[]` (proof a worker must
return — anti-low-effort/anti-fabrication), `contributor{contributor_id,
contributor_type ∈ first_party_ai|third_party_ai|human|organization,
display_name, claimed_at}`, `deadline`, and an independent `review{reviewed_by
(MUST differ from contributor — enforced at app layer), review_verdict ∈
accepted|rework|rejected, quality_score, rejection_reason (incl.
fabricated_or_hallucinated, privacy_violation), review_notes, reviewed_at}`.
These describe *work and quality*, which is neutral, so they belong in the open
standard.

### MaxTask — `contributor_eligibility` PRIVACY GATE (IN the standard)
Enum `first_party_only | any_contributor`, **default `first_party_only`
(fail-closed)**. MUST be `first_party_only` whenever a task concerns a living
person (tier2-private): living-subject tasks are never dispatched to
third-party/human/organization contributors, and any externally dispatched
result must be scrubbed of tier2-private PII.

### Payment/economics are DELIBERATELY NOT in the standard
Per three-brain council (strategist + operator): an open standard must stay
**neutral** — a common work/quality language, not a business model. Hard-coding
`payout`, `list_price_usd`, or a revenue split into the public standard would
(a) fragment adoption (forces everyone into one business model), (b) publish a
portable fraud/sybil blueprint, and (c) push legal/tax burden (worker
classification, KYC/AML, 1099) onto every adopter. So payment lives in the
implementer's **product layer** (The Probable Pedigree) under the open
`extensions{}` namespace — never in core. MaxTask gains an `extensions` object
(`additionalProperties: true`) documenting this. This matches the locked
two-brand architecture (OpenGenealogyAI = standard; The Probable Pedigree =
product). Collusion-resistance, KYC, and worker-classification are the
implementer's responsibility.

### Lockstep version bump (no content change)
MaxRecord, MaxPerson, MaxDNA stamped `schema_version: "1.5"` per lockstep policy.

### Fixtures
+2 task-queue fixtures (`valid-v15-ruled-out`, `valid-v15-marketplace-paid` —
the latter shows `payout` living under `extensions`). 64/64 validate.

---

## v1.4 addendum — 2026-06-02 (NO schema version bump)

**Theme:** Migration / life-event storage clarified, and a biography convention.
This is a **documentation + infrastructure** release. The JSON Schema files did
**not** change — everything here uses fields that already exist. No `schema_version`
bump; no Human Gate. Approved by three-brain council (operator: PROCEED WITH
CAUTION — gate the public commit by diff-inspection).

### Migration & life events live in `event_assertions[]` — do NOT add a `vital_events` table
A contributor session proposed a new flat `vital_events` table for
migration/residence data. **Rejected — it would fork the standard.** The canonical
home already exists: `MaxPerson.event_assertions[]` (since v1.3), whose `event_type`
enum already covers `immigration`, `emigration`, `naturalization`, `residence`,
`census_enumeration`, `land_grant`, `military_service`, `will`, `probate`, `burial`,
etc. A person's migration story = an ordered set of these assertions, each with its
own `year_min/max`, `place_as_written`, `description` (the narrative), `confidence`,
and `source_record_id`.

Places whose name/jurisdiction changed over time (e.g. "Kentucky County, Virginia"
→ "Bourbon County, KY" after 1786) are represented in `place_registry[]` via
`historical_polity` + `valid_from_year`/`valid_to_year` + lat/long — something a
flat events table cannot express. The enum maps 1:1 to GEDCOM tags
(RESI/EMIG/IMMI/CENS/NATU/PROB/WILL/MILI), preserving round-trip.

**Mapping for anyone migrating a `vital_events`-style table:**
`event_type`→`event_type` · `event_date`/`event_year`/`±tol`→`year_min`+`year_max`+`month`+`day`+`date_type` · `event_place`→`place_as_written` · `source_*`→`source_record_id` · `confidence`→`confidence` · `notes`→`description`.

### `bio_summary` — a per-person hover biography (via `extensions`, not a core field)
A short prose biography shown on profile/hover. Stored as **`extensions.bio_summary`**
(string) plus **`extensions.bio_summary_ai_generated`** (boolean — a biography is
narrative, not a sourced assertion, so we mark machine-written ones). Uses the
`extensions{}` open namespace (added v1.3) by design: no schema bump, no Human Gate.
Promote to a first-class `biography_assertions[]` in a future version if it proves out.

### Postgres / portal mirror — migration `002_events_places.sql`
Added additive mirror tables backing the two JSON fields above:
- `person_events` — mirror of `event_assertions[]` (FK → `persons`, `ON DELETE CASCADE`).
- `place_registry` — mirror of `place_registry[]` (historical polity + valid years + lat/long).
- Portal SQLite `persons` gains `bio_summary` + `bio_summary_ai_generated` columns.
JSON MaxPerson remains the source of truth; mirrors exist for JOINs, sorting, and the
portal Life-Story / Migration views.

### Privacy / repo invariant (reaffirmed)
Family-tree **data rows are never committed to the public GitHub repo** — only schemas,
docs, and migration scripts. Living people (`is_living = TRUE` ⇔ `tier2-private`) never
appear in public commits, indexes, or embeddings.

---

## v1.4 — 2026-05-17

**Theme:** Photos as a first-class field. A face transforms genealogy from data
to story; previous versions had no model for portraits and that was a real gap
for end-user engagement. Garlon-requested.

### MaxPerson
- **`photo_assertions[]`** — portraits and snapshots OF this person (NOT
  gravestones or documents — those stay on MaxRecord). Each photo carries:
  - `photo_id`, `url`, `thumbnail_url`, `storage_tier`
    (internet_archive / local_private / contributor_upload / external)
  - `caption`, `year_min/max`, `place_as_written`, `photographer`, `license`
  - `is_primary` — designate ONE photo as the profile image (ties broken by
    confidence then most-recent `asserted_at`)
  - `confidence` — is this actually this person? (same scale as other
    assertions; see CONFIDENCE_CALIBRATION.md)
  - `subject_role` enum (solo / group / wedding / family / military /
    occupational / unknown) so UI knows whether to auto-crop
  - `face_bounding_box` (normalized 0–1 coords) — optional, for auto-cropping
    group photos to the subject's face in cards and pedigree nodes
  - `alt_text` — accessibility (screen-reader description)
  - `ia_id` — Internet Archive identifier when stored there
  - `source_record_id` — optional link back to a MaxRecord (e.g. obituary with
    a portrait)
- Living-subject photos inherit `tier2-private` regardless of declared
  `license`, per the existing privacy gate.
- Photography min year set to 1839 (invention of the technology).

### Lockstep version bump (no content change)
- MaxRecord, MaxTask, MaxDNA all bumped to `schema_version: "1.4"` per the
  lockstep policy. Their descriptions now note "schema content stable since
  vX.X; version stamp follows lockstep MAXGEN release versioning."

### Fixtures
- 58 fixtures re-stamped to v1.4; 62/62 validate against new schemas.

---

## v1.3 — 2026-05-17

**Theme:** Scale-readiness for the 1.5B-person dedup roadmap, plus lockstep
versioning. All additive — no breaking changes. Approved via six-brain review
2026-05-17 (option A: additive batch).

### MaxPerson
- **Merge/dedup provenance model** (the headline change):
  - `merge_history[]` — entities absorbed INTO this one, each with
    `merge_confidence`, `merge_method` (exact_external_id / probabilistic_match
    / manual / dna_confirmed), `match_signals`, `merged_by`, `merged_at`,
    `reverted_at`. A merge is a reversible assertion, never a deletion.
  - `duplicate_of` — when this entity was absorbed, points to the survivor.
  - `merge_status` — active / merged_away / split_pending.
  - Design principle: nothing is destroyed on merge. The absorbed entity
    persists with `merge_status='merged_away'`, preserving append-only history
    and full reversibility.
- **`event_assertions[]`** — long-tail life events (baptism, immigration,
  naturalization, residence, census, burial, military_service, will, probate,
  etc.) with an `event_type` enum. First-class birth/death/marriage stay
  separate (most-queried). Adding a new event kind no longer needs a schema bump.
- **`place_registry[]`** — gazetteer normalization. Resolves free-text
  `place_as_written` strings to GeoNames / Wikidata QID / lat-long without
  modifying every assertion (avoids a mass backfill). Handles historical
  polities ("Austria-Hungary 1880") via `historical_polity` + valid-year range.
- **`extensions`** object — designated `additionalProperties:true` namespace for
  experimental fields, so new ideas don't require a schema bump while the rest
  of the schema stays strict.
- **Fix:** birth/death/occupation `year_max` 2025 → 2100.

### MaxRecord, MaxTask, MaxDNA
- `schema_version` const bumped to `1.3` (lockstep; content unchanged this
  release except the version stamp).
- MaxTask jumped 0.1 → 1.3 (was the worst skew offender).

### Tooling
- All 62 fixtures re-stamped to `1.3` and re-validated (62 PASS / 0 FAIL).

### Companion docs
- ✅ **[`CONFIDENCE_CALIBRATION.md`](CONFIDENCE_CALIBRATION.md)** — defines the
  anchored confidence scale, per-assertion scoring procedure, noisy-OR
  corroboration (+ independence rule), conflict handling, the
  `composite_confidence` formula, and cross-agent calibration via a gold set.
  (Completed 2026-05-17.)

### Deferred to later work (not in v1.3)
- Atomicity enforcement for bidirectional-sync — an implementation task in the
  Postgres `persons`-mirror transaction layer, not a schema change.
- Gazetteer integration — populating `place_registry` from GeoNames / Wikidata
  (the field exists now; population happens after the embedding run stabilizes).

---

## v1.2 — 2026-05-15

**Theme:** DNA layer + Max* naming + symmetric-relationship discipline.

### Naming
- Renamed `MXRecord` → **MaxRecord**, `MXPerson` → **MaxPerson**,
  `TaskQueue` → **MaxTask** (Garlon preference: "Max" not "MX").
- MaxTask moved into the `maxgen/v1` `$id` namespace (was legacy `v0.1`).

### MaxDNA (new schema)
- Created `dna.schema.json` — DNA test metadata + match assertions linked to a
  MaxPerson. Purpose: strengthen ancestor confidence, not living-cousin
  discovery.
- Hard-locked safety constants: `redistribution_license` = `tier2-private`,
  `raw_genotype_stored` = `false`, `kit_id_hash` / `external_ids` = HMAC-SHA-256
  (64-hex regex).
- Six-brain review 2026-05-15 added 9 amendments: `shared_cm` max → 7000,
  `longest_segment_cm` max → 285, `cm_map_version`, `phasing_status`,
  `common_ancestor_candidates[]` (replacing the singular field), HMAC (not plain
  SHA-256), `is_living_flag` default policy.

### MaxPerson
- Added `dna_evidence[]` — DNA chains touching this person as candidate MRCA;
  feeds noisy-OR confidence boosting.
- `spouse_assertions` expanded: explicit bidirectional-sync requirement, plus
  `end_year_min/max`, `end_reason` (divorce / annulment / death_of_spouse /
  separation), `marriage_place_as_written`, `relationship_type` (marriage /
  civil_union / domestic_partnership / common_law), `conflict_flag`.
- `parent_assertions` — bidirectional-sync warning added (was only documented
  on the `child_assertions` side).

---

## v1.1 — (pre-2026-05-15)

**Theme:** Richer person assertions + more record types.

### MaxPerson
- Added `child_assertions[]` (bidirectional with `parent_assertions`).
- Added `occupation_assertions[]` (for disambiguation when name + date aren't
  enough).
- Added `external_ids` map (cross-source dedup: wikidata_qid, familysearch_pid,
  findagrave_id, ia_id, ssdi_id).

### MaxRecord
- Added record types: `land_patent`, `wikidata_entity`, `open_library_work`,
  `dpla_item`.

---

## v1.0 — initial release

- Three schemas: MaxRecord (source documents), MaxPerson (probabilistic
  identities), MaxTask (distributed work queue).
- Core design: uncertainty-as-data. Every claim is an assertion with confidence
  + source + timestamp. Nothing is ever overwritten. Multiple possible parents
  coexist.
- Evidence/interpretation split: MaxRecord = what the document says, MaxPerson =
  what we think it means, linked by `source_record_id`.
- Privacy by design: living-person 404 gate, most-restrictive-license
  propagation.

---

## Schema `$id` URLs (permanent)

These never change within a major version:

- `https://opengenealogyai.org/schemas/maxgen/v1/raw-record.schema.json`
- `https://opengenealogyai.org/schemas/maxgen/v1/person.schema.json`
- `https://opengenealogyai.org/schemas/maxgen/v1/task-queue.schema.json`
- `https://opengenealogyai.org/schemas/maxgen/v1/dna.schema.json`

A breaking change creates `/schemas/maxgen/v2/` and requires Human Gate HG-6.
