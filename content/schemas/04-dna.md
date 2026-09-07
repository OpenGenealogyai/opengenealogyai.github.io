---
title: MaxDNA
slug: maxdna
schema: dna
tagline: Genetic evidence, always private, never the genome itself.
fixture: 
description: MaxDNA stores DNA match evidence linked to a MaxPerson with the strongest privacy rules in MAXGEN: hashed kit IDs, mandatory consent, and no raw genotype data ever.
---
## Evidence that belongs to your relatives too

More than fifty million people have taken a consumer DNA test. A 47-centimorgan match (a centimorgan is the unit used to measure how much DNA two people share) does not prove a relationship on its own, but combined with a documentary trail to the same ancestor it raises confidence sharply. Genetic data is also information about every biological relative you have, including people who never consented. MaxDNA is designed to capture the genealogical value while making a leak structurally impossible.

## What it stores, and what it cannot

MaxDNA stores **match metadata**: shared centimorgans, longest segment, segment count, optional chromosome segments for triangulation, haplogroups (deep ancestral lineages on the direct paternal or maternal line), ancestry composition, and kit metadata.

It cannot store a genome. `raw_genotype_stored` is a constant `false` that cannot be set otherwise. There is no field for a raw file.

## The privacy architecture

- **Always `tier2-private`.** The licence field has exactly one allowed value. A MaxDNA record can never appear in a public dataset, API response, or embedding, even for people long dead, because a living tester's data describes their ancestors too.
- **Hashed kit IDs.** `kit_id_hash` holds an HMAC-SHA-256 hash (a one-way scramble that uses a secret key, so it cannot be reversed by guessing) of the kit number. Matches store `match_kit_hash` the same way. You can triangulate without ever seeing your cousin's kit number.
- **Consent is a required field.** Values include explicit opt-in, guardian consent, public-dataset redistribution allowed, deceased before 2000, pending, and withdrawn. `withdrawn` starts a 30-day purge; after that the record is deleted outright, which is the one place MAXGEN deletes anything.

## Methodology fields most tools forget

- `cm_map_version`: different recombination maps (HapMap, deCODE and others) give centimorgan values that differ by up to 15% for the same segment. Recording which map was used makes matches comparable.
- `phasing_status`: whether a shared segment is on the paternal or maternal chromosome, both, unphased, or unknown. Essential for serious triangulation.
- `endogamy_flag` and `endogamy_population`: communities that married within themselves for generations (Ashkenazi, Acadian, Mennonite, island populations) break the standard relationship estimates. The flag tells downstream tools to recalibrate.

## How it connects to the rest

`person_id` links the test to the [MaxPerson](/schemas/maxperson/) who took it. Confirmed common-ancestor candidates on a match carry a confidence and an inference method (manual, tree intersection, triangulation, shared surname and geography). Those flow into MaxPerson's `dna_evidence[]`, where they strengthen documentary relationship claims through the same noisy-OR combination used everywhere else.

## Fields

<!--FIELDS-->

## Example

Because MaxDNA records are private by definition, this site does not publish a real one. The [validator](/validator/) offers a synthetic minimal record you can inspect and check.

## Listen

<!--PODCAST-->
