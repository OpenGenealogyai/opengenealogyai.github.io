---
title: The Standard
nav: /standard/
toc: true
description: The Maxwell Genealogy Standard explained in plain English: what MAXGEN is, how it differs from GEDCOM, the eleven record schemas, evidence standards, confidence scores, who may be living, an example, files, versioning, why JSON, how to contribute, and governance.
---
<p class="eyebrow">The Maxwell Genealogy Standard · v<!--CURRENT--> · CC0</p>
# The Standard

MAXGEN is a language for describing family-history evidence. It is not a product, not a database, and not a website. It is a set of eleven record schemas (written rule-sets for data records), plus one shared file of building blocks, that any program can read and write, so that research done in one place can be understood everywhere else. This page explains the standard in plain English. The exact rules live in the [schema files](/schemas/).

## 1. What is MAXGEN?

MAXGEN is an open JSON standard for representing genealogical evidence and the people it describes in a way that is **honest about uncertainty**.

Some or most genealogy formats today can hold one answer per fact. When two records disagree about who someone's father was, one has to win and the other is set aside. MAXGEN takes a different approach: **every fact is a claim** that cites its evidence and carries a confidence score. Nothing is overwritten. Several possible parents can coexist. Conflicting birth years both survive. The uncertainty is the data.

Five ideas define it:

1. **Uncertainty is data.** Every claim carries a 0-to-1 confidence score (section 6). A date keeps the document's own words plus what they mean (about, before, between, worked out from an age), and its earliest and latest possible days are worked out whenever it is read, because a census age is a guess and "spring 1847" is not a day.
2. **Evidence and interpretation are kept apart.** MaxRecord holds what a document says, exactly as written. MaxPerson and MaxRelationship hold what we think it means: who the people were and how they were related. You can change your mind about a person without touching the document.
3. **Nothing is ever deleted.** A wrong claim is retracted with a reason and stays visible as withdrawn, and a wrong merge can be undone. A contested claim is marked `disputed`, not removed. Every claim names the task that produced it, and every task records who did the work.
4. **Privacy is built into the schema.** Every document that names a person must say whether that person may be alive, and when unsure the answer is yes. Anything about a person who may be alive is locked to the most restrictive licence, `tier2-private`, and privacy is worked out again whenever the data is read, never from a stored copy. DNA records are always private and never hold raw genetic data.
5. **Extensions are the escape hatch.** Each schema is closed, so validation is exact, but every schema has an `extensions{}` object that accepts anything. You never need to fork.

> **The core principle:** a claim with low confidence and a real source is worth more than a certain-looking answer with no source. MAXGEN never invents. When the document is unclear, the confidence score says so.

## 2. How MAXGEN differs from GEDCOM

GEDCOM is the 1984 file format that most genealogy programs can import and export. It is one of the great successes of the field: forty years of trees have moved between programs because of it, and GEDCOM 7 continues to modernise it. MAXGEN is not a replacement for exchanging finished trees. It is a companion for exchanging the **evidence and reasoning** behind a tree, a job GEDCOM was never asked to do. The two are meant to work together.

| | GEDCOM | MAXGEN |
|---|---|---|
| Format | Custom plain text (`.ged`) | JSON, validated by JSON Schema |
| Relationships | A child can be linked to more than one family, with a pedigree type (`PEDI`) | Every family link stored once, in MaxRelationship; a child can belong to several parent sets (birth, adoptive, step, foster and more), and a competing candidate is kept until a written proof settles it |
| Uncertainty | A quality rating on a citation (`QUAY`) | The point of the design: a confidence on every claim |
| Source for each fact | Optional | Required on every claim: exactly one piece of evidence |
| Dates | A date value, with an optional phrase (`PHRASE`) | The words as written, plus what they mean (about, before, between, worked out from an age); earliest and latest days are worked out when read |
| Conflicts | A child's family link can be marked challenged or disproven (`STAT`) | Competing claims are kept side by side, and each claim's status says where it stands (proposed, proven, disputed, disproven, retracted) |
| Living people | A restriction marker (`RESN`) | Every document that names a person must say whether they may be alive; if so, the schema locks it private |
| Transcription provenance | None | MaxRecognition, down to word-level confidence; on a MaxRecord, the chain of copies back to the original |
| Name variants across languages | Translations of a name (`TRAN`) | MaxName: variants and cross-language cognates, each citing evidence |
| AI readiness | Predates it | Structured evidence on every claim, quotes checked against the document's words, and a `fabricated_or_hallucinated` rejection reason for reviews in MaxTask |
| Control | A steering committee | Public domain (CC0); anyone may use, fork, or extend |

The MAXGEN 2.0 specification gives every GEDCOM 7 construct a home, so a GEDCOM import need not lose anything on the way in: `CENS`, `EMIG`, `IMMI`, `NATU`, `PROB` and `WILL` become events of the matching type, `RESI` and `OCCU` become residence and occupation facts, and a tag with no field of its own is kept word for word in `extensions`. It maps GEDCOM X, Gramps and RootsMagic the same way. The importers that follow this mapping are not published yet.

## 3. The eleven record schemas

A schema is a written rule-set that says exactly what fields a record has and what each one means. MAXGEN began with three record types, grew to seven during the 1.x releases, and in 2.0 has eleven record schemas plus one shared building-blocks file, each covering one layer of the research process. Each record schema has [its own page](/schemas/).

| Schema | What one document is | Page |
|---|---|---|
| **MaxRecord** | One document, copied word for word | [MaxRecord](/schemas/maxrecord/) |
| **MaxPerson** | One person, as a researched conclusion (holds no family links) | [MaxPerson](/schemas/maxperson/) |
| **MaxRelationship** | One family link, stored once: a `family`, a `parent_child` pair or an `association` | [MaxRelationship](/schemas/maxrelationship/) |
| **MaxTask** | One piece of work: research, an import, a review or a proof | [MaxTask](/schemas/maxtask/) |
| **MaxSource** | One work, collection or repository | [MaxSource](/schemas/maxsource/) |
| **MaxPlace** | One real place with its history | [MaxPlace](/schemas/maxplace/) |
| **MaxGroup** | One historical group (its own facts only; no member list) | [MaxGroup](/schemas/maxgroup/) |
| **MaxEvent** | The identity of one occasion (thin) | [MaxEvent](/schemas/maxevent/) |
| **MaxName** | Knowledge about one name | [MaxName](/schemas/maxname/) |
| **MaxRecognition** | One machine reading of an image | [MaxRecognition](/schemas/maxrecognition/) |
| **MaxDNA** | One person's DNA test and its matches (always private) | [MaxDNA](/schemas/maxdna/) |

The twelfth file, `maxgen-defs.schema.json`, is not a record type: no document is valid against it. It holds the parts every record schema uses: ids and small types, the date block, the place block, the evidence link (the part of every claim that says which evidence it rests on), the quote check, saved copies, regions and media pointers, roles and event types, the claim header, the common and lifecycle fields, and the task provenance block.

How they fit together:

```
                             MaxSource
                (a work, collection or repository)
                                 ▲
                                 │ part of
                                 │
  MaxRecognition ──reads───► MaxRecord ◄──── cited as evidence by claims
  (a machine reading         (one document,
   of its image)              copied word for word)

  MaxRelationship ──links──► MaxPerson ──claims point at──► MaxPlace  (one real place)
  (every family link,        (one person, as a              MaxGroup  (one historical group)
   stored once)               researched conclusion)        MaxEvent  (one occasion)
                                 ▲
                                 │ took the test
                                 │
                              MaxDNA (one person's test and matches, always private)

  MaxName   knowledge about one name: its variants and cognates
  MaxTask   the work behind every claim: proofs and search steps live here
```

Every claim, in every schema, cites exactly one piece of evidence: a document (a MaxRecord), an imported tree file, DNA matches, an accepted proof, or a recorded search. Every claim also names the MaxTask that produced it.

A MaxRecord needs no other document to be valid, only the shared file every schema uses, so an archive that only wants to publish its documents, copied word for word, can start with MaxRecord alone.

Sections 4 to 7 are the rules that MAXGEN's reference agent kit follows, copied word for word from its `RULES.md`. Where they mention "the kit", "the helper", the Orchestrator or the person's page, they mean that kit.

## 4. Who decides

**The kit cannot tell who is typing.** A decision counts as the person's own only when the
person makes it on their Probable Pedigree page (`probable_pedigree.py`). An answer the
Orchestrator types for them (`task ... --answer`) is recorded as relayed. A relayed answer is
enough for a merge, an adoption, lowering a living flag, and for "disputed" or "disproven".
**"Proven" needs the person's own decision on their page.** So the person-page address is for
the person's own hands: an agent never opens it, fetches it with a program or presses its
buttons, and never reuses it after giving it to the person.

**The person's own choices are theirs alone (R-33, R-34).** Who is held back from sharing, what
is shared, which people are locked, and their bookmarks, tags and saved lists live in the tree's
owner tables. Only the person changes them, in their own Probable Pedigree, which holds the
tree's owner key; you never ask for that key, never look for it and never write those tables.
Never change a person the owner has locked, nor a family link of theirs: the helper refuses it
(R-34). If a locked person needs a change, tell the person through the Orchestrator and let
them unlock it. The one thing you may record there is how many records a website holds for a
person (an `outside_holdings` mark), because that is a finding, not a choice.

Retract, never delete. Nothing is removed from the database: a wrong claim, document or link is
withdrawn with `retract` and a reason, and stays visible as withdrawn. Every change you make is
also logged in the tree itself, with your session, so the person can see it and undo it.

## 5. Evidence standards

MAXGEN's `confidence` number measures how faithfully a fact was read from its source —
extraction confidence — **not** how good the source is. A clearly printed name copied from a
clearly printed page is 0.9 even if the page is a hearsay family tree. How much the source is
worth is a separate question, answered for every MaxRecord by its `source_classification`, a
required field of the record:

- `source`: `original` (the document made at the time — a register page, a certificate, a
  stone — or an image of it), `derivative` (a copy, index, transcription or abstract made from
  an original; it must say what it was copied from, in `copied_from`), `authored` (someone's
  written conclusions — a compiled history, an online tree, the family's own account), or
  `undetermined` (only for a citation in an imported tree that nobody has examined yet).
- `information`: `primary` (the informant was present at the event), `secondary` (the
  informant heard it from someone else, or wrote it long afterwards), or `undetermined` (a
  census row, where nobody knows who answered the door). A record that holds several events
  judges this per event: a death certificate's informant is first-hand for the death and
  second-hand for the birth.

Every claim also says what kind of evidence it is: `direct` (the words state it), `indirect`
(it is worked out — a birth year from an age), or `negative` (someone missing from a complete
list).

These three sets of words follow the evidence analysis process map in Elizabeth Shown Mills's
*Evidence Explained*
([QuickLesson 17](https://www.evidenceexplained.com/content/quicklesson-17-evidence-analysis-process-map)),
described here in our own words.

A claim is called **proven** only when it meets the Genealogical Proof Standard (published by the
[Board for Certification of Genealogists](https://bcgcertification.org/ethics-standards/)) — a
reasonably exhaustive search, complete citations, analysis and correlation of the evidence,
resolution of any conflicts, and a written conclusion. In this kit that written conclusion is a
proof on a `resolve_conflict` task (or, for a merge, a `merge_person` task), and the status
"proven" is set only when the person accepts that proof on their own page ("Who decides",
above). Until then it is a claim with a confidence, and the kit says so. An import can mark a claim disputed or disproven, never proven.

## 6. The confidence score

`confidence` is a number from 0 to 1. It is how strongly *this document* supports *this claim*,
not how sure you feel. A census row names a person: 0.8 to 0.9. A gravestone with a worn date:
0.5 to 0.7. A family tree on a website with no citations: never above 0.4, and never alone.
It is extraction confidence; the weight of the evidence is the source classification above.

Deliberate exception, enforced by the helper: a claim whose evidence is not reliable — the
family's own account in the interview, something the person typed on their page, an online
tree, an imported file, a compiled work, a citation nobody has examined — is capped at 0.4 and
**never stands alone**: it never counts as independent evidence, never supports a proof, and
never shows that someone has died. Pages label it by where it came from ("From the family's
account", "From a family tree", "From an imported file", "Added by you"). The cap lifts only
when a new claim cites a reliable document.

Why this matters for AI: models that read handwriting, match records and draft leads need an honest confidence on every claim, a source they can be checked against, and the losing candidates as well as the winner. That is why every claim cites exactly one piece of evidence (for a document: the record, the line holding the words, the exact quote, and whether the evidence is direct, indirect or negative), why claims are never overwritten, and why a MaxTask can list the evidence its worker must hand back (addresses, exact queries, records saved) and its search log keeps every step, including the searches that found nothing.

## 7. Who may be living

Treat a person as possibly living unless at least one of these holds: a reliable document
states their death, burial or cremation (a later record calling their husband or wife a widow
or widower also counts; being missing from a list never does); their latest possible birth
year is 110 or more years ago; a marriage of theirs is 90 or more years ago; or a birth child
of theirs was born 90 or more years ago (always counting the latest year the date allows). A
step-child, ward, foster child or adopted child does not count. If none of these holds — for
example, when the birth year is unknown and no death is on file — assume living. Never infer
"deceased" from silence — not finding a death record is not a death record.

Only reliable evidence counts here: a document classified `original` or `derivative`, or a
proof whose latest review is accepted. The family's own account, an online tree, an imported
file and a note typed on the person page never show that anyone has died. So a grandparent known only
from the interview stays living until a document shows otherwise.

Set `is_living` **explicitly** on every MaxPerson and every person named in a record, `true` or
`false`. The helper refuses a file that omits it. **The helper and every page work this out
again every time they read; a stored `false` never makes anyone shareable.** A stored `true` is
a floor: it stays until the person agrees to lower it (ORCHESTRATOR, "When the documents show
someone has died"). What the person tells you about living relatives stays `tier2-private` and
is used only for their own research — never published, exported or sent anywhere.

## 8. JSON examples

A MaxPerson is one person as a researched conclusion. Every fact about the person is a claim, kept in a list (the files name these lists `..._assertions`), and every claim cites exactly one piece of evidence and carries a confidence. A MaxPerson holds no family links: parents, children and partners live in MaxRelationship, where each link is stored once.

This made-up example takes three claims from one entry on a census page: a name, an occupation, and a birth year worked out from an age. The birth claim is `indirect` (the census gives an age, not a birth date), its date is marked `calculated` with a note saying how, and its confidence is lower than the others:

```json
{
  "person_id": "2a946e38-5769-5fdc-b888-e67cc4b40342",
  "schema_version": "2.0",
  "is_living": false,
  "redistribution_license": "CC0",
  "name_assertions": [
    {
      "name_as_written": "Mossbury, Amos",
      "given_name": "Amos",
      "surname": "Mossbury",
      "confidence": 0.9,
      "task_id": "f1f1f427-edc8-59ac-b068-c44d0daf5f82",
      "evidence": {
        "basis": "document",
        "source_record_id": "325d904c-1f07-52ff-85d2-856cb65a60cd",
        "mention_id": "11",
        "record_event_ref": "e1",
        "quote": "Mossbury, Amos",
        "kind": "direct"
      }
    }
  ],
  "birth_assertions": [
    {
      "date": {
        "modifier": "about",
        "value": {
          "year": 1896
        },
        "derivation": "calculated",
        "derivation_note": "age 44 on the 1940 census day"
      },
      "age_as_written": "44",
      "confidence": 0.6,
      "task_id": "f1f1f427-edc8-59ac-b068-c44d0daf5f82",
      "evidence": {
        "basis": "document",
        "source_record_id": "325d904c-1f07-52ff-85d2-856cb65a60cd",
        "mention_id": "11",
        "record_event_ref": "e1",
        "quote": "Mossbury, Amos | Head | M | 44",
        "kind": "indirect"
      }
    }
  ],
  "attribute_assertions": [
    {
      "attribute_type": "occupation",
      "value_as_written": "Miller",
      "occurrence_id": "8a37321b-3688-589b-8d1a-1a596a352edb",
      "confidence": 0.85,
      "task_id": "f1f1f427-edc8-59ac-b068-c44d0daf5f82",
      "evidence": {
        "basis": "document",
        "source_record_id": "325d904c-1f07-52ff-85d2-856cb65a60cd",
        "mention_id": "11",
        "record_event_ref": "e1",
        "quote": "Miller",
        "kind": "direct"
      }
    }
  ],
  "created_by_task_id": "f1f1f427-edc8-59ac-b068-c44d0daf5f82",
  "extensions": {
    "fixture": "made up"
  }
}
```

Paste any record into the [validator](/validator/) to check it, or press **Load a sample** there to see a made-up example of every kind.

### 8b. Contributor attribution

Credit travels with the data, not in a site footer. A MaxRecord can carry an `attribution{}` object crediting the human who photographed, transcribed or compiled it (`contributor_name`, `contributor_url`, `contributor_role`) and the organisation the document came from (`source_organization`, `source_organization_url`); a MaxSource and a MaxRecognition can carry one too. Every claim names the task that produced it (`task_id`), and each MaxTask records once, in its `provenance` block, who did the work: a person, an AI model (named), an OCR or handwriting reader, an importer, and so on. For a MaxRecognition, which holds one machine reading of an image, who or what made the reading is recorded the same way, on the task that produced it. A credit never makes a document count as naming a living person. In a copy meant for sharing, a credit is kept when it is already public or when the licence requires it (CC-BY, CC-BY-SA), exactly as the licence holder gave it; other credits, such as a name typed in as transcriber or photographer, are left out.

## 9. File format

A MAXGEN document is one JSON file of one kind: one MaxPerson, one MaxRecord, and so on. A document's kind is the first of these keys found at its top level: `dna_id`, `recognition_id`, `name_id`, `relationship_id`, `occurrence_id`, `place_id`, `group_id`, `record_id`, `task_id`, `source_id`, `person_id`. (MaxDNA also carries `person_id`, so the order matters.)

## 10. Versioning

All twelve files share one version number; the current release is v<!--CURRENT-->. Every document carries a `schema_version` stamp. The schemas accept any `"2.<n>"` stamp (`"2.0"`, `"2.1"` and so on, but not `"1.13"`), and a file keeps the stamp it was written with.

**Within 2.x, every change is additive:** a new optional property, a new value in a list, a new shared definition in `maxgen-defs.schema.json`, a new record schema file, or a loosened limit. **Never:** removing or renaming a property, making an optional property required, narrowing a pattern, range or list, or changing what a value means. A file valid under 2.0 stays valid under every 2.x. Breaking changes wait for a new major version, with a new address space; the [governance page](/governance/) describes how changes are proposed and decided.

The 1.x files stay archived at `/schemas/maxgen/v1/`, with a frozen copy of every 1.x release, and their rules are unchanged. MAXGEN 2.0 does not read 1.x files, and there is no migration from 1.x to 2.0. Every release, with permanent download links, is on the [versions page](/versions/), and the full history is in the [changelog](/changelog/).

## 11. Why JSON?

- **Human-readable and machine-readable.** No proprietary parser required; you can open a record in a text editor.
- **Supported everywhere.** Every modern programming language reads and writes JSON natively.
- **Validated by JSON Schema 2020-12.** Structure is enforced at the moment a record is written, not discovered later.
- **Embeddable.** A MAXGEN record can be fed to an AI embedding model without conversion, which is what makes meaning-based search possible.
- **Version-control friendly.** Records can be diffed, reviewed and merged exactly like source code, so a community can review each other's work.
- **No lock-in.** Export your data at any time with zero loss.

## 12. How to contribute

Ask a question, report a problem, propose a change, or build something on the standard. You do not need permission for any of it. The [contribute page](/contribute/) has the details; the short version is: open a GitHub Issue using the *Schema Change Proposal* template (problem, proposed change, backward compatibility, examples) or write to maxgen@opengenealogyai.org.

## 13. Governance

MAXGEN is public domain, so anyone can use it, fork it, or extend it. What is stewarded is the canonical version: the one set of files at this address that may be called MAXGEN. Today the author, Garlon Maxwell, is the steward, with a public proposal process (7-day comment for minor changes, 30-day for major) and written decisions. A published charter describes the advisory board that takes over major-version decisions once there are independent adopters, including how it can overrule the steward. What the steward never controls: your forks, your extensions, your tools, or your records. Read the full [governance page](/governance/).

> "MAXGEN is open because anyone can use it and build on it. It is not open to fragmentation. One canonical version. One steward. Every change earns its place in the record." — Garlon Maxwell
