---
title: MaxName
slug: maxname
schema: name
tagline: One name, every form it has ever taken, with sources.
fixture: name/valid-v113-unresolved-cognate.json
description: MaxName is a structured record of one canonical name and all its historical variants, cognates across languages, and phonetic keys, each with provenance, time scope and confidence.
---
## Why Maxwell and Makeswell are the same family

Search a Scottish record set for "Maxwell" and you will not find Makeswell, Maccuswell, M'Axwell, McAxwell or Maxwel, though every one of them is the same name written by a different clerk in a different decade. The problem is bigger than spelling. Ó Briain became O'Brien when colonial administrators wrote it down. Müller became Miller on the far side of the Atlantic. Yochanan became Iohannes became Jean became John became Ivan.

No open standard captured this before. We checked GEDCOM X, WeRelate, Behind the Name, JRC-Names, the Library of Congress name authority, VIAF, schema.org, HistNorm and Popolo. None records typed, directed, sourced name relationships across languages, scripts, places and centuries. MaxName fills that gap.

## What it is

One MaxName record is one **canonical form** (the modern reference spelling) plus everything known about how that name has been written and adapted.

- **`regional_canonical[]`**: the preferred form in other communities. "John" carries Seán, Eoin, Ioan, Johann. None is more real than another.
- **`variants[]`**: every known form, each with its own UUID (so it can be cited or retracted on its own), the text exactly as it appears in records, a **type** (spelling variant, nickname, anglicisation, latinisation, abbreviation, scribal corruption, phonetic rendering, emigration adaptation, translation, patronymic form, legal substitution…), its **scope** (place, language, script, period), a **frequency** (rare, occasional, common, dominant), an **attestation source**, a confidence, and `disputed_by[]` for scholars who disagree.
- **`cognates[]`**: links to other MaxName records across languages, with a relationship type (etymological root, cross-language cognate, derivative, contracted form) and a **direction**: Maxwell *derives from* Maccus; John and Ivan are *parallel cognates* of Iohannes. Every link cites its source.
- **`phonetic_keys{}`**: pre-computed codes for fuzzy search under six algorithms (Soundex, NYSIIS, Metaphone, Double Metaphone, Daitch-Mokotoff, Beider-Morse), each with its algorithm version so a code computed in 2026 can be reconciled with one computed in 2040.

## Two-pass cognates (new in v1.13)

Cognate relationships are discovered while researching the first name, before a record for the second exists. A cognate may therefore be recorded **unresolved**, carrying only `related_canonical_form` ("John"), and later **resolved** by a linking pass that adds the `related_name_id`. Both states are valid; a cognate pointing at nothing is not.

## The Maxwell example

Canonical form Maxwell, a surname of Old Norse origin: Maccus's pool, first attested about 1144. Variants: *Maccuswell* (Latin charters, 1144 to 1300, dominant), *Makeswell* (Scots parish registers, 1600 to 1750, occasional), *M'Axwell* (an abbreviation, 1700s to 1800s), *McAxwell* (Virginia colonial records, rare, with one scholar disputing whether it is corruption or anglicisation). Phonetic keys Soundex M240, Daitch-Mokotoff 645400. One cognate: *derives from* Maccus, citing Reaney's Dictionary of English Surnames. With that record, a search for Makeswell finds Maxwell, and a transcription of Maccuswell from a twelfth-century charter lands in the right family.

## Variant types

<!--ENUM:variant_type-->

## Fields

<!--FIELDS-->

## Example

Seán, the Irish form of John, with an anglicisation, an older spelling, and an unresolved cognate link.

<!--EXAMPLE-->

## Listen

<!--PODCAST-->
