---
title: The seven schemas
description: MaxRecord, MaxPerson, MaxTask, MaxDNA, MaxSource, MaxRecognition and MaxName — the seven MAXGEN schema files, explained and downloadable.
---
<p class="eyebrow">MAXGEN v<!--CURRENT--> · lockstep release</p>
# The seven schemas

All seven files share one version number. When any of them changes, the whole standard moves to the next number, so you adopt "MAXGEN v<!--CURRENT-->" as a set and never have to wonder which schema goes with which.

<!--CARDS-->

## Canonical addresses

Every schema file declares its own permanent address in its `$id` field. Tools that validate records fetch the schema from there.

| Schema | Latest v1 (always current) | File |
|---|---|---|
| MaxRecord | `/schemas/maxgen/v1/raw-record.schema.json` | [download](/schemas/maxgen/v1/raw-record.schema.json) |
| MaxPerson | `/schemas/maxgen/v1/person.schema.json` | [download](/schemas/maxgen/v1/person.schema.json) |
| MaxTask | `/schemas/maxgen/v1/task-queue.schema.json` | [download](/schemas/maxgen/v1/task-queue.schema.json) |
| MaxDNA | `/schemas/maxgen/v1/dna.schema.json` | [download](/schemas/maxgen/v1/dna.schema.json) |
| MaxSource | `/schemas/maxgen/v1/source.schema.json` | [download](/schemas/maxgen/v1/source.schema.json) |
| MaxRecognition | `/schemas/maxgen/v1/recognition.schema.json` | [download](/schemas/maxgen/v1/recognition.schema.json) |
| MaxName | `/schemas/maxgen/v1/name.schema.json` | [download](/schemas/maxgen/v1/name.schema.json) |

Frozen copies of every past release live under `/schemas/maxgen/v1.NN/` and are listed on the [versions page](/versions/). A machine-readable list is at [`/schemas/maxgen/index.json`](/schemas/maxgen/index.json).

## Check a record

Paste any JSON record into the [browser validator](/validator/) to see whether it conforms. Nothing you paste leaves your computer.
