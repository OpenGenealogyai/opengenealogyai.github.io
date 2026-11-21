---
title: Versions
toc: true
description: MAXGEN versioning policy, the 2.x additive-only promise, and every released version of the schema files with permanent download links.
---
<p class="eyebrow">Releases · current v<!--CURRENT--></p>
# Versions and the stability promise

## How versions work

**One version number for all twelve files.** The eleven record schemas and the shared building-blocks file are released together as one MAXGEN version, so you adopt "MAXGEN <!--CURRENT-->" as a set and never have to wonder which file goes with which. Every document carries `schema_version`, and the 2.x schemas accept any `"2.<n>"` stamp (`"2.0"`, `"2.1"` and so on; not `"2.01"`, not `"1.13"`).

## The 2.x promise

> **Within 2.x, every change is additive:** a new optional property, a new value in a list, a new shared definition in `maxgen-defs.schema.json` (a `$defs` entry), a new record schema file, or a loosened limit. **Never:** removing or renaming a property, making an optional property required, narrowing a pattern, range or list, or changing what a value means. A file valid under 2.0 stays valid under every 2.x.

- **Files keep the stamp they were written with.** There is no re-stamping of old files at each release.
- **Unknown values fall back safely.** A reader that meets a list value it does not know reads a vocabulary word as `other` and shows the words as written; a state that software manages fails closed (an unknown licence is read as `tier2-private`).
- **A major version** (`v2` → `v3`) means breaking changes and a new `$id` address space.

## What 2.0 changed

MAXGEN 2.0 is the first major version. It reshaped the seven 1.x schemas, added four new ones (MaxRelationship, MaxPlace, MaxGroup and MaxEvent) and a shared building-blocks file, and moved family links out of MaxPerson into MaxRelationship, where each link is stored once. **2.0 does not read 1.x files, and there is no migration from 1.x.** The 1.x files are not deleted: they stay at their permanent addresses, unchanged. The [changelog](/changelog/#v20-2026-10-16) lists what changed in every schema and what breaks.

## What 2.1 added

MAXGEN 2.1 adds optional fields and allowed values to six of the twelve files: where a fact came from, life stories and biographies, notes that keep their history, comparing a reading with an index, health conditions kept private, archive addresses and old-map alignment. Nothing was removed or narrowed, so every 2.0 file stays valid. The [changelog](/changelog/#v21-2026-11-21) lists every addition in the schema files' own words.

2.0 and 2.1 were published without the public comment period described on the [governance page](/governance/), because the standard had no outside users yet.

## Permanent addresses

- `/schemas/maxgen/v2/<file>` holds the **current 2.x files** (now 2.1). This is the `$id` inside every 2.x file.
- `/schemas/maxgen/v2.1/<file>` and `/schemas/maxgen/v2.0/<file>` are **frozen copies** of each release.
- `/schemas/maxgen/v1/<name>.schema.json` and `/schemas/maxgen/v1.NN/<name>.schema.json` hold the **archived 1.x releases**, unchanged.
- `/schemas/maxgen/index.json` lists every version, with each 2.x file's SHA-256, for machines.

<!--VERSIONS-->

## Changelog

Every release, what changed in each schema and why, is on the [changelog page](/changelog/).
