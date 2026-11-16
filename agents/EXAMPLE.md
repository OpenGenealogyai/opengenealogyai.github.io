# EXAMPLE — one person, end to end

Read this once. It shows every piece of the kit doing its job for one made-up family, in
MAXGEN 2.0. Every name, place and address here is invented; `example.org` is a reserved
example domain. Each file below was run through `maxgen_db.py` exactly as shown. Commands leave
out `--as <session id>` to stay short; every write needs it (START, step 3).

## 1. The person speaks

In the first conversation (ORCHESTRATOR, "The first conversation") the person reaches
question 3 and says: *"My grandmother was Edith Marlow. She died in 1988."*

## 2. The Orchestrator asks, one thing at a time

- "What was her name before she married?" — *Pennywhistle.*
- "Roughly when and where was she born?" — *About 1902, in Greywater, I think.*
- "Do you know her parents' names?" — *Her father was Thomas. I never knew her mother's name.*

Under the first task of the batch (`task_type: other`, `payload.kind: "interview"`) the
Genealogist writes the interview as one MaxRecord of `record_type: other`, one labelled line
per question and answer (`[a1] A: My grandmother was Edith Marlow. She died in 1988.`),
classified `authored` / `secondary`, `tier2-private`. It adds a MaxPerson for Edith and one for
Thomas, each claim citing the interview line at 0.4:

```json
"evidence": {"basis": "document", "source_record_id": "6b1f0a2e-3c4d-4e5f-8a9b-0c1d2e3f4a00",
             "mention_id": "e", "quote": "My grandmother was Edith Marlow", "kind": "direct"}
```

Both are stored `is_living: true`, `tier2-private`: the family's own account never shows that
anyone has died (RULES, "Who may be living"). Edith is the most recent deceased ancestor the
person named, so the work starts with her (ORCHESTRATOR, "Work backward").

## 3. One MaxTask

The Erewhon Record Office's terms do not forbid automated access and it offers no API, so the
Genealogist may search it, politely (RULES 8). Edith is still stored living, so the task is too.

```json
{
  "task_id": "2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c01",
  "schema_version": "2.0",
  "task_type": "extract_record",
  "status": "pending",
  "priority": 8,
  "is_living_flag": true,
  "contributor_eligibility": "first_party_only",
  "payload": {
    "person_id": "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b01",
    "name_as_known": "Edith Marlow, born Pennywhistle, about 1902",
    "source": {"name": "Erewhon 1911 census", "url": "https://records.example.org/1911/"},
    "look_for": "the household of Thomas Pennywhistle in Greywater with a daughter Edith",
    "record_types": ["census_household"]
  },
  "acceptance_criteria": [
    "a MaxRecord of the whole household, copied exactly as written",
    "a claim on Edith for each fact her line states, each with its evidence link"
  ],
  "evidence_required": ["the persistent address of the record page", "the exact search run"],
  "min_confidence": 0.7
}
```

The Orchestrator adds it (`add genealogy.db staging/tasks/<task_id>.json`). The Genealogist
claims it and logs the search:

```
python maxgen_db.py task genealogy.db 2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c01 processing
python maxgen_db.py step genealogy.db --task 2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c01 --source "Erewhon Record Office" --query "1911 census, Greywater, surname Pennywhistle" --found "the Pennywhistle household, sheet 3, lines 14-16" --verdict key_finding
```

## 4. A MaxRecord of the whole household

The whole household is copied, exactly — "Scholer" and "Thos." stay as written (RULES 4) — and
every line carries the page's own line number as its label. Everyone on it is a mention. The
image of the original page was read, so the source is `original`; nobody knows who answered the
census taker, so the information is `undetermined`.

```json
{
  "record_id": "6b1f0a2e-3c4d-4e5f-8a9b-0c1d2e3f4a01",
  "schema_version": "2.0",
  "record_type": "census_household",
  "redistribution_license": "public-domain",
  "is_living_flag": false,
  "source_classification": {"source": "original", "information": "undetermined"},
  "transcription_parts": [
    {"part_id": "p1", "text": "[14] Pennywhistle, Thos. | Head | M | 41 | Carter | Greywater\n[15] Pennywhistle, Ada | Wife | F | 38 | | Greywater\n[16] Pennywhistle, Edith | Dau | | 9 | Scholer | Greywater"}
  ],
  "transcription_scope": "complete_entry",
  "extraction_confidence": 0.9,
  "persons_mentioned": [
    {"mention_id": "14", "name_as_written": "Pennywhistle, Thos.", "role_as_written": "Head", "role": "head",
     "age_as_written": "41", "gender_as_written": "M", "in_event_refs": ["e1"], "lines": ["14"], "is_living": false},
    {"mention_id": "15", "name_as_written": "Pennywhistle, Ada", "role_as_written": "Wife", "role": "spouse",
     "relative_to": "14", "age_as_written": "38", "gender_as_written": "F", "in_event_refs": ["e1"], "lines": ["15"], "is_living": false},
    {"mention_id": "16", "name_as_written": "Pennywhistle, Edith", "role_as_written": "Dau", "role": "child",
     "relative_to": "14", "age_as_written": "9", "in_event_refs": ["e1"], "lines": ["16"], "is_living": false}
  ],
  "events_documented": [
    {"ref": "e1", "event_type": "census", "is_primary": true,
     "date": {"value": {"year": 1911, "month": 4, "day": 2}, "precision": "day", "derivation": "interpreted",
              "derivation_note": "census day of the made-up 1911 Erewhon census"},
     "place": {"place_as_written": "Greywater, Erewhon"},
     "information": "undetermined"}
  ],
  "repository_as_written": "Erewhon Record Office",
  "collection_as_written": "1911 census of Erewhon",
  "locator": {"as_written": "1911 census, Greywater district 7, sheet 3, lines 14-16", "series": "1911 census",
              "district": "7", "sheet": "3", "line_first": 14, "line_last": 16},
  "source_url": "https://records.example.org/ark:/99999/r1911-0427"
}
```

```
python maxgen_db.py add genealogy.db staging/records/6b1f0a2e-3c4d-4e5f-8a9b-0c1d2e3f4a01.json --task 2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c01
OK  ... -> max_record 6b1f0a2e-3c4d-4e5f-8a9b-0c1d2e3f4a01 (staged) (1 occurrence)
```

The helper gave the census entry its occasion id (one MaxEvent), and will key the record so no
one can add the same household twice.

## 5. Claims on Edith, each with its evidence link

Edith is already in the database, so her new claims go in a claims file, through `claim`. Each
cites her own line (`16`) and copies the exact words. Age 9 on census day is *calculated* to
1901 or 1902 — worked out, not stated, so the evidence is `indirect`:

```json
{
  "person_id": "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b01",
  "name_assertions": [
    {"name_as_written": "Pennywhistle, Edith", "given_name": "Edith", "surname": "Pennywhistle", "name_type": "birth",
     "confidence": 0.9,
     "evidence": {"basis": "document", "source_record_id": "6b1f0a2e-3c4d-4e5f-8a9b-0c1d2e3f4a01",
                  "mention_id": "16", "record_event_ref": "e1", "quote": "Pennywhistle, Edith", "kind": "direct"}}
  ],
  "birth_assertions": [
    {"date": {"modifier": "between", "value": {"year": 1901}, "value2": {"year": 1902}, "derivation": "calculated",
              "derivation_note": "aged 9 on census day, 2 April 1911"},
     "confidence": 0.8,
     "evidence": {"basis": "document", "source_record_id": "6b1f0a2e-3c4d-4e5f-8a9b-0c1d2e3f4a01",
                  "mention_id": "16", "record_event_ref": "e1", "quote": "Pennywhistle, Edith | Dau | | 9", "kind": "indirect"}}
  ]
}
```

```
python maxgen_db.py claim genealogy.db staging/claims/edith-1911.json --task 2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c01
OK  2 claim(s) added to person 9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b01 (staged for Quality Control): ...
```

Thomas's claims from his own line (`14`) go in the same way.

Because Edith and Thomas were still stored living when their claims cited this page, the helper
also marked the census record private: privacy fails closed. Now `status` says the documents
show both were born more than 110 years ago, so the Orchestrator plans one `living_review` task
and asks the person once; with their yes, the Genealogist lowers both people's flags
(ORCHESTRATOR, "When the documents show someone has died").

## 6. One link — Thomas is Edith's father, as far as this page says

The page shows Edith as the head's daughter. A census line never makes a birth parent (the
household is not a birth record), so the lineage is `unknown`, and no parent set kind is given.
Ada, the head's wife on the same page, is **not** linked as Edith's mother: the page does not
say she is. Her line is a lead for the next task.

```json
{
  "link_request": "2.0",
  "kind": "parents",
  "child_id": "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b01",
  "child_mention_id": "16",
  "parents": [
    {"person_id": "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b02", "parent_role": "father", "parent_role_as_written": "Head",
     "lineage": "unknown", "mention_id": "14"}
  ],
  "evidence": {"basis": "document", "source_record_id": "6b1f0a2e-3c4d-4e5f-8a9b-0c1d2e3f4a01",
               "mention_id": "16", "record_event_ref": "e1", "quote": "Pennywhistle, Edith | Dau", "kind": "direct"},
  "confidence": 0.85
}
```

```
python maxgen_db.py link genealogy.db staging/links/thomas-edith-1911.json --task 2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c01
OK link: parent_child b036... (new), family ada0... (new), membership e813... other (preferred) -- staged for Quality Control
```

One parent-child record, one family (a parent set "kind not stated"), and Edith's membership in
it. No marriage was made: shared parenthood never makes one. On the chart the line from Edith
to Thomas is dotted — "kind of parent not stated" — until a birth record says more.

The Genealogist closes the task: `task genealogy.db <task_id> done --verdict key_finding`.

## 7. A conflict, kept as two candidates

A later task finds her gravestone, `https://graves.example.org/ark:/99999/g-5531`:
`[2] EDITH MARLOW` / `[3] BORN 1903 DIED 1988`. That is a second MaxRecord (`gravestone`,
worn, `extraction_confidence` 0.6; source `original`, information `secondary` — carved years
later by family) with two event entries, `e1` the death and `e2` the birth. Her birth claim
from it cites the birth entry:

```json
{"date": {"value": {"year": 1903}, "precision": "year", "as_written": "1903"}, "confidence": 0.6,
 "evidence": {"basis": "document", "source_record_id": "6b1f0a2e-3c4d-4e5f-8a9b-0c1d2e3f4a02",
              "mention_id": "m1", "record_event_ref": "e2", "quote": "BORN 1903", "kind": "direct"}}
```

Nothing is overwritten. Edith now has two birth claims, 1901–1902 (census, 0.8) and 1903
(stone, 0.6), both `proposed`. Quality Control opens both sources, confirms each claim is on
its page and on Edith's line, and passes both. To settle it, the Orchestrator plans a
`resolve_conflict` task; the Genealogist writes a proof — the census was taken in her lifetime,
the stone was carved after 1988 and is worn — concluding that the stone's 1903 is `disputed`.
The person reads it on their page under "Decisions waiting for you" and accepts it; the
Orchestrator runs `apply-proof`, and the 1903 claim becomes `disputed`. It is still there, and
still shown, marked as disputed.

## 8. Quality Control, in order

```
python maxgen_db.py review genealogy.db <each record_id> pass --by "Quality Control" --note "opened the source; every line matches; classification fits"
python maxgen_db.py review genealogy.db 9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b01 pass --by "Quality Control" --note "each claim on Edith's own line; quote checks matched; age arithmetic right"
python maxgen_db.py review genealogy.db <the parent_child id> pass --by "Quality Control" --note "census Dau line; lineage unknown, as it must be"
python maxgen_db.py review genealogy.db <the family id> pass --by "Quality Control" --note "a parent set of kind not stated; no marriage"
```

Records first, then persons, then links: the helper refuses any other order.

## 9. What the person sees

From `reports/batch_001.md`, built from database counts:

```
New this batch: Edith Marlow (Pennywhistle) — 1911 census, Greywater, shows her name, a birth
  in 1901 or 1902 (worked out from her age), and Thomas Pennywhistle as the head of her household,
  her father as far as the census says. Birth year has two candidates: 1901–1902 (census, 0.80)
  and 1903 (gravestone, 0.60) — both kept; the stone's year is now marked disputed.
Waiting for your decision on your page: nothing.
```

"Proven" waits for the Genealogical Proof Standard (RULES, "Evidence standards") and the
person's own decision on their page. Her birth register entry, searched for in the next batch,
would settle the year and name her parents. Sent as one `link` with both parents as
`birth_parent` and `set_kind: birth`, it makes a birth parent set of Thomas and Ada, which
becomes Edith's main parents; Thomas's dotted line turns solid, and the census set stays, as
the record of a household.
