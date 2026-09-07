---
title: MaxRecognition
slug: maxrecognition
schema: recognition
tagline: Who read this page, how, and how sure they were about every word.
fixture: recognition/valid-v19-human-htr-kurrent.json
description: MaxRecognition is the provenance record for an OCR or handwriting transcription: the contributor (human or AI), word-level confidence, labelled inferences, and cross-run consensus.
---
## The handwriting problem

German Kurrent script was the everyday handwriting of German-speaking Europe from the 1500s to the mid-1900s. Its "e" looks nothing like an e; its "n" looks like a "u"; the word for mother looks like "Mnttev". The church registers that matter most for German-American research are written in it, by the million. Kurrent is one of many: Secretary Hand, Court Hand, Hebrew and Greek scribal hands, Arabic nastaliq, Cyrillic ustav.

MaxRecognition records what happens when a machine or a trained human reads such a page, so that the reading is citable, checkable and improvable.

## One schema for print and handwriting

OCR (reading printed text) and HTR (reading handwriting) share more than 90% of their fields, so there is one schema with a `recognition_type` switch. Handwriting records add `script_type` and `approx_century` for routing to the right specialist; print records add `font_type` and `scan_dpi`.

## Who did the reading

`contributor{}` has a `contributor_type` of `human` or `ai`, and fields that follow from it.

- **Humans** carry credentials, specialty scripts, languages and regions, `records_transcribed` (a reputation signal that accumulates), and training notes. A Kurrent reading by someone with fifteen thousand records and a Kurrent certification carries more weight than the same reading by a newcomer.
- **AI models** carry model name, version, provider and type, the **exact prompt** used (a prompt that says "transcribe exactly, do not correct" is a different method from "extract the key facts"), and fine-tuning details. Storing the prompt makes the transcription reproducible.

## Word-level confidence and labelled guesses

`word_confidences[]` gives each word a score, a position, alternatives, and a **`reading_type`**:

| `reading_type` | Meaning |
|---|---|
| `observed` | Read directly from the image. |
| `inferred_context` | Filled in from surrounding entries. |
| `inferred_pattern` | Filled in from a known naming convention. |
| `partial_read` | Some characters legible, the rest inferred. |

Traditional practice says "leave it blank if you cannot read it". MaxRecognition says: if you can make a reasonable inference, make it, **label it**, explain it in `inference_notes`, and give it a lower score. "Five other entries on this page read Maxwell; only 'Ma' is legible here; letterform height at position 3 fits 'xw' not 'rk'; inference Maxwell, 0.78." That is how a trained genealogist reasons, captured in a form a reviewer or a future model can audit.

## Consensus and searchability

- **`consensus_group_id`** links independent readings of the same image. `is_independent` says whether each run was blind to the others. Agreement raises confidence; disagreement flags the word for human review. The schema stores the evidence; the matching engine does the arithmetic.
- **`searchable_variants[]`** lists every spelling under which this record should be findable when a word is uncertain between, say, "Maxwell" and "Maxweil". [MaxName](/schemas/maxname/) handles the general fact that names vary across history; this field handles one hard reading in one document.
- **`character_error_rate`** and **`word_error_rate`** record quality against ground truth when it exists, so over time the system learns which models and which people read which scripts best.

## Fields

<!--FIELDS-->

## Example

A human Kurrent specialist's reading of a Bavarian baptism, with one uncertain word and its alternative.

<!--EXAMPLE-->

## Listen

<!--PODCAST-->
