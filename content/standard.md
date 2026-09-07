---
title: The Standard
nav: /standard/
toc: true
description: The Maxwell Genealogy Standard explained in plain English: what MAXGEN is, how it differs from GEDCOM, the seven schemas, confidence scores, examples, file extensions, versioning, why JSON, how to contribute, and governance.
---
<p class="eyebrow">The Maxwell Genealogy Standard · v<!--CURRENT--> · CC0</p>
# The Standard

MAXGEN is a language for describing family-history evidence. It is not a product, not a database, and not a website. It is a set of seven schemas (written rule-sets for data records) that any program can read and write, so that research done in one place can be understood everywhere else. This page is the whole standard in plain English. The exact rules live in the [schema files](/schemas/).

## 1. What is MAXGEN?

MAXGEN is an open JSON standard for representing genealogical evidence and the people it describes in a way that is **honest about uncertainty**.

Some or most genealogy formats today can hold one answer per fact. When two records disagree about who someone's father was, one has to win and the other is set aside. MAXGEN takes a different approach: **every claim is an assertion** with a confidence score, a source, and a timestamp. Nothing is overwritten. Several possible parents can coexist. Conflicting birth years both survive. The uncertainty is the data.

Five ideas define it:

1. **Uncertainty is data.** Every claim carries a 0-to-1 confidence score (section 4) and dates are ranges, because a census age is a guess and "spring 1847" is not a day.
2. **Evidence and interpretation are kept apart.** MaxRecord holds what a document says, exactly as written. MaxPerson holds what we think it means. You can change your mind about a person without touching the document.
3. **Nothing is ever deleted.** A wrong merge is reversed, not erased. A disputed reading gets a `disputed_by` entry. Every change has a who, a when, and a why.
4. **Privacy is built into the schema.** A record about a person who may be alive is locked to the most restrictive licence, `tier2-private`, and public systems answer "not found". DNA records are always private and never hold raw genetic data.
5. **Extensions are the escape hatch.** Each schema is closed, so validation is exact, but every schema has an `extensions{}` object that accepts anything. You never need to fork.

> **The core principle:** an assertion with low confidence and a real source is worth more than a certain-looking answer with no source. MAXGEN never invents. When the document is unclear, the confidence score says so.

## 2. How MAXGEN differs from GEDCOM

GEDCOM is the 1984 file format that almost every genealogy program can import and export. It is one of the great successes of the field: forty years of trees have moved between programs because of it, and GEDCOM 7 continues to modernise it. MAXGEN is not a replacement for exchanging finished trees. It is a companion for exchanging the **evidence and reasoning** behind a tree, a job GEDCOM was never asked to do. The two are meant to work together.

| | GEDCOM | MAXGEN |
|---|---|---|
| Format | Custom plain text (`.ged`) | JSON, validated by JSON Schema |
| Relationships | One answer | Several candidates, each with a confidence score |
| Uncertainty | Not represented | The point of the design |
| Source for each fact | Optional | Required on every assertion |
| Dates | A single value | Ranges (`year_min` / `year_max`) |
| Conflicts | Resolved before saving | Preserved and flagged |
| Living people | No enforcement | Locked private by the schema |
| Transcription provenance | None | MaxRecognition, down to word-level confidence |
| Name variants across languages | None | MaxName |
| AI readiness | Predates it | Embedding text, structured evidence, anti-hallucination rules in MaxTask |
| Control | A steering committee | Public domain (CC0); anyone may use, fork, or extend |

MAXGEN event types map to GEDCOM tags (RESI, EMIG, IMMI, CENS, NATU, PROB, WILL, MILI) so conversion runs in both directions.

## 3. The seven schemas

A schema is a written rule-set that says exactly what fields a record has and what each one means. MAXGEN began with three record types and has grown to seven, each covering one layer of the research process. Each has [its own page](/schemas/) with fields, an example and a podcast episode.

| Schema | What one record is | Page |
|---|---|---|
| **MaxRecord** | One source document exactly as found, uninterpreted | [MaxRecord](/schemas/maxrecord/) |
| **MaxPerson** | One probable person: a bundle of scored, sourced assertions | [MaxPerson](/schemas/maxperson/) |
| **MaxTask** | One unit of research work for a human or an AI, with proof required | [MaxTask](/schemas/maxtask/) |
| **MaxDNA** | DNA match evidence linked to a person, always private | [MaxDNA](/schemas/maxdna/) |
| **MaxSource** | One place where records live, with coverage and access details | [MaxSource](/schemas/maxsource/) |
| **MaxRecognition** | One transcription of one image: who read it and how sure they were | [MaxRecognition](/schemas/maxrecognition/) |
| **MaxName** | One name and every form it has taken, with sources | [MaxName](/schemas/maxname/) |

How they fit together:

```
  document image ──► MaxRecognition (who read it, how confident, word by word)
                            │
                            ▼
                       MaxRecord (the document, exactly as written)
                            │  cited by
                            ▼
   MaxName ◄──────────  MaxPerson  ◄────── MaxDNA (genetic evidence, always private)
 (is "Makeswell"      (a probable person:
  the same name?)      every claim scored)
                            │  spawns
                            ▼
                        MaxTask (work for a human or an AI, with proof required)
                            ▲
                            │  where to look next
                        MaxSource (a catalogue of where records live)
```

Each schema can be adopted on its own. An archive that only wants to publish transcriptions with provenance can use MaxRecognition alone.

## 4. The confidence score

Every assertion carries a `confidence` value from 0.0 to 1.0. The numbers are anchored, not decorative:

| Score | Meaning |
|---|---|
| 0.90 to 1.00 | The field appears verbatim in the source with no ambiguity. |
| 0.70 to 0.89 | Minor spelling or transcription variation; well attested. |
| 0.50 to 0.69 | Inferred from context, not stated explicitly. |
| 0.30 to 0.49 | Ambiguous or damaged source. |
| 0.00 to 0.29 | Best guess only. Recorded so it is not lost, not so it is believed. |

Scores must be honest. A consistent 0.4 is more valuable than an inflated 0.9. A person's `composite_confidence` combines all assertions, weighted by source quality, using a noisy-OR rule: independent sources that agree raise confidence, but never to certainty, and two assertions that trace to the same document are not counted twice.

Why this matters for AI: models that read handwriting, match records and draft leads need an honest confidence on every claim, a source they can be checked against, and the losing candidates as well as the winner. In our own benchmarks, ordinary keyword search returned 0 to 5 percent of the right records for queries with a partial name and an approximate year; meaning-based search over embedded text is the baseline for this field. That is why every schema has an `embedding_text` field or equivalent, why every claim cites a `source_record_id`, and why MaxTask requires an AI to return the evidence it found rather than a summary of what it believes.

## 5. JSON examples

A MaxPerson is a set of assertion arrays. Every field that says something about the person is an array, never a single value:

```json
{
  "person_id": "b1000000-0006-4000-8000-000000000006",
  "schema_version": "<!--CURRENT-->",
  "is_living": false,
  "composite_confidence": 0.95,
  "name_assertions": [
    { "name_as_written": "Mary Todd", "name_type": "birth", "confidence": 0.95,
      "source_record_id": "a1b2c3d4-0003-4000-8000-000000000003",
      "asserted_by": "extractor-agent-haiku-001", "asserted_at": "2026-05-01T14:00:00Z" },
    { "name_as_written": "Mary Lincoln", "name_type": "married", "confidence": 0.99,
      "source_record_id": "a1b2c3d4-0003-4000-8000-000000000003",
      "asserted_by": "extractor-agent-haiku-001", "asserted_at": "2026-05-01T14:00:00Z" }
  ],
  "birth_assertions": [
    { "year_min": 1818, "year_max": 1818, "month": 12, "day": 13, "place_as_written": "Lexington, Kentucky",
      "confidence": 0.98, "source_record_id": "a1b2c3d4-0003-4000-8000-000000000003",
      "asserted_by": "extractor-agent-haiku-001", "asserted_at": "2026-05-01T14:00:00Z" }
  ],
  "redistribution_license": "public-domain",
  "asserted_by": "extractor-agent-haiku-001",
  "asserted_at": "2026-05-01T14:00:00Z"
}
```

Every schema page carries a full, validated example: [MaxRecord](/schemas/maxrecord/), [MaxPerson](/schemas/maxperson/), [MaxTask](/schemas/maxtask/), [MaxSource](/schemas/maxsource/), [MaxRecognition](/schemas/maxrecognition/), [MaxName](/schemas/maxname/). Paste any record into the [validator](/validator/) to check it.

### 5b. Contributor attribution

Credit travels with the data, not in a site footer. A MaxRecord carries an `attribution{}` object naming the contributor (`contributor_name`, `contributor_url`, `contributor_role`) and the organisation the document came from (`source_organization`, `source_organization_url`). Every assertion in a MaxPerson names who made it (`asserted_by`) and when (`asserted_at`), and cites the record it came from, so a person's page can list every contributor whose work touched that person. A MaxRecognition names its transcriber, human or model, with credentials. Attribution is part of the record and stays with it wherever the record is copied.

## 6. File extensions

MAXGEN uses two file extensions so record types can be told apart at a glance:

- **`.maxgen`** for MaxPerson records: `william_whitfield_1842.maxgen`
- **`.mxrecord`** for MaxRecord source documents: `1880_census_ohio_p42.mxrecord`

Both are ordinary JSON files. MaxTask records live in a work queue and are not usually stored as standalone files. Any `.json` file that validates against a schema is also a valid MAXGEN record; the extensions are a convenience, not a requirement.

## 7. Versioning

All seven schemas share one version number and move together. Within a major version, fields are never renamed or removed; changes are additive only. A breaking change means a new major version, a new address space, a migration guide, and a 30-day public comment period. Records written to any version stay readable forever. The full policy, the stability promise, and every released version with permanent download links are on the [versions page](/versions/).

## 8. Why JSON?

- **Human-readable and machine-readable.** No proprietary parser required; you can open a record in a text editor.
- **Supported everywhere.** Every modern programming language reads and writes JSON natively.
- **Validated by JSON Schema 2020-12.** Structure is enforced at the moment a record is written, not discovered later.
- **Embeddable.** A MAXGEN record can be fed to an AI embedding model without conversion, which is what makes meaning-based search possible.
- **Version-control friendly.** Records can be diffed, reviewed and merged exactly like source code, so a community can review each other's work.
- **No lock-in.** Export your data at any time with zero loss.

## 9. How to contribute

Ask a question, report a problem, propose a change, or build something on the standard. You do not need permission for any of it. The [contribute page](/contribute/) has the details; the short version is: open a GitHub Issue using the *Schema Change Proposal* template (problem, proposed change, backward compatibility, examples) or write to maxgen@opengenealogyai.org.

## 10. Governance

MAXGEN is public domain, so anyone can use it, fork it, or extend it. What is stewarded is the canonical version: the one set of files at this address that may be called MAXGEN. Today the author, Garlon Maxwell, is the steward, with a public proposal process (7-day comment for minor changes, 30-day for major) and written decisions. A published charter describes the advisory board that takes over major-version decisions once there are independent adopters, including how it can overrule the steward. What the steward never controls: your forks, your extensions, your tools, or your records. Read the full [governance page](/governance/).

> "MAXGEN is open because anyone can use it and build on it. It is not open to fragmentation. One canonical version. One steward. Every change earns its place in the record." — Garlon Maxwell
