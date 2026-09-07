---
title: The Standard
nav: /standard/
description: What MAXGEN is, the ideas behind it, and how it differs from GEDCOM.
---
<p class="eyebrow">The Maxwell Genealogy Standard · v<!--CURRENT--></p>
# The Standard, in plain English

MAXGEN is a language for describing family-history evidence. It is not a product, not a database, and not a website. It is a set of seven schemas (written rule-sets for data records) that any program can read and write, so that research done in one place can be understood everywhere else.

## Five ideas that define it

### 1. Uncertainty is data
Every claim in MAXGEN carries a confidence score from 0.0 to 1.0. The scores mean something specific:

| Score | Meaning |
|---|---|
| 0.95 and above | Documented certainty. The fact appears verbatim in a source with no ambiguity. |
| 0.70 to 0.95 | Well attested, with some ambiguity (a spelling variant, a self-reported age). |
| 0.50 to 0.70 | Plausible but needing more evidence. |
| Below 0.50 | Speculative. Recorded so it is not lost, not so it is believed. |

Scores must be honest. A consistent 0.4 is worth more than an inflated 0.9. Dates are stored as ranges (`year_min`, `year_max`) because a census age is a guess and "spring 1847" is not a day.

### 2. Evidence and interpretation are kept apart
**MaxRecord** holds what a document says, exactly as written, including the clerk's misspellings. **MaxPerson** holds what we think it means. You can change your mind about a person without touching the document, and add a document without touching your conclusions. This is how science keeps its data clean, and it is how a future AI can re-read the same evidence and reach its own conclusion.

### 3. Nothing is ever deleted
A merge that turns out to be wrong is reversed, not erased. A disputed reading gets a `disputed_by` entry. A dead link is marked `dead_link`. Every change has a who, a when, and a why.

### 4. Privacy is built into the schema, not bolted on
A record about a person who may be alive carries `is_living: true` and is locked to the most restrictive licence, `tier2-private`. Public endpoints return "not found" for it. DNA records are always `tier2-private` and can never contain raw genetic data. These rules travel with the data when it moves between systems, so a forgetful application cannot leak what the standard protects.

### 5. Extensions are the escape hatch
Each schema is closed: unknown fields are rejected, which makes validation exact. But every schema also has an `extensions{}` object that accepts anything. Add your own fields there. You never need to fork the standard, and your records still validate.

## How the seven schemas fit together

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

Each schema can be adopted on its own. An archive that only wants to publish transcriptions with provenance can use MaxRecognition alone. The full pipeline, from a scanned page to a scored ancestor, appears only when they work together.

## How MAXGEN differs from GEDCOM

GEDCOM is the 1984 file format that almost every genealogy program can import and export. It is one of the great successes of the field: forty years of trees have moved between programs because of it, and GEDCOM 7 continues to modernise it. MAXGEN is not a replacement for exchanging finished trees. It is a companion for exchanging the **evidence and reasoning** behind a tree, which is a job GEDCOM was never asked to do. The two are meant to work together, and MAXGEN's event types map to GEDCOM tags so conversion runs both ways.

| | GEDCOM | MAXGEN |
|---|---|---|
| Format | Custom plain text (`.ged`) | JSON, validated by JSON Schema |
| Relationships | One answer forced | Several candidates, each with a confidence score |
| Uncertainty | Lost | The point of the design |
| Source for each fact | Optional | Required on every assertion |
| Dates | A single value | Ranges (`year_min` / `year_max`) |
| Conflicts | Overwritten | Preserved and flagged |
| Living people | No enforcement | Locked private by the schema |
| Transcription provenance | None | MaxRecognition, down to word-level confidence |
| Name variants across languages | None | MaxName |
| AI readiness | Not designed for it | Embedding text fields, structured evidence, anti-hallucination rules in MaxTask |
| Control | A steering committee | Public domain (CC0); anyone may use, fork, or extend |

MAXGEN event types map to GEDCOM tags (RESI, EMIG, IMMI, CENS, NATU, PROB, WILL, MILI) so conversion in both directions is possible.

## Why this matters for AI

Genealogy is moving into a period where AI models read handwriting, match records across archives and draft research leads. Those models need three things a single-answer format cannot give them: an honest confidence on every claim, a source they can be checked against, and the losing candidates as well as the winner. Two findings from our own benchmarks shaped the design:

> Ordinary keyword search (the BM25 and full-text index that most databases use) returned 0 to 5 percent of the right records for genealogy queries with a partial name and an approximate year. Meaning-based search over embedded text is not optional for this field; it is the baseline.

> A low-confidence assertion with a real source is worth more than a certain-looking answer with no source. Scores drive the matching engine, the task dispatcher and the display. They are not decoration.

That is why every MAXGEN schema has an `embedding_text` or equivalent, why every claim cites a `source_record_id`, and why MaxTask requires an AI to return the evidence it found rather than a summary of what it believes.

## What "adopting MAXGEN" means

- **You write records** that validate against the schema files at their canonical addresses (`https://opengenealogyai.org/schemas/maxgen/v1/…`).
- **You keep your own fields** inside `extensions{}`.
- **You respect three normative rules**: living-person records stay private, DNA records stay private and never hold raw genotypes, and tasks about living people are never sent to outside contributors.
- **You may call your data MAXGEN** as long as it validates against the canonical schemas. A diverging fork may not use the name.

## Where to go next

- [The seven schemas](/schemas/), each with fields, an example, and a podcast.
- [Versions and the stability promise](/versions/).
- [How the standard changes](/governance/) and how to propose a change.
- [Frequently asked questions](/faq/).
