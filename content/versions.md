---
title: Versions
toc: true
description: MAXGEN versioning policy, the stability promise, and every released version of the seven schemas with permanent download links.
---
<p class="eyebrow">Releases · current v<!--CURRENT--></p>
# Versions and the stability promise

## How versions work

**One number for all seven schemas.** When any schema changes, the whole standard moves to the next number and every file is stamped with it, even the ones that did not change. You adopt "MAXGEN v<!--CURRENT-->" as a set. This is how HL7 FHIR (the healthcare data standard) and GEDCOM release, and it makes it impossible for two files to disagree about which version they belong to.

**Semantic versioning.** The number has the industry-standard meaning:

| Change | Bump | Example |
|---|---|---|
| Wording, descriptions, a regex fix. No field changes. | patch: 1.13.0 → 1.13.1 | Clarifying that `output_text` may hold a JSON string |
| Additive: a new optional field, a new enum value, a loosened constraint | minor: 1.13 → 1.14 | Adding `parent_source_id` to MaxSource |
| Anything that could make an existing valid record invalid | major: v1 → v2 | Making a field required, renaming or removing a field |

## The stability promise

> Within a major version, **fields are never renamed or removed**. A field can be marked deprecated with a note, but it stays valid. Every record that validated against v1.3 validates against every later v1.x.

A major version gets a new address space (`/schemas/maxgen/v2/`), a written migration guide, and the full [review process](/governance/) including the longer comment period. There is no forced migration: a tool that understands v2 must still read v1 records without failing silently.

## Permanent addresses

- `/schemas/maxgen/v1/<name>.schema.json` always points at the **latest v1.x**. This is the `$id` inside every schema file.
- `/schemas/maxgen/v1.NN/<name>.schema.json` is a **frozen copy** of that release and never changes.
- `/schemas/maxgen/index.json` lists every version for machines.

<!--VERSIONS-->

## Release ritual

Every release, in this order: bump the `schema_version` constant in all seven files, re-run every fixture through the validators, write the changelog entry, tag the release in git, copy the files to `/v1/` and `/v1.NN/` on this site, update the version badge, and regenerate any podcast episode whose schema changed.
