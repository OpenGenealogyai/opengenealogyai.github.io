---
title: The MAXGEN schemas
description: The eleven MAXGEN record schemas (MaxRecord, MaxPerson, MaxRelationship, MaxTask, MaxSource, MaxPlace, MaxGroup, MaxEvent, MaxName, MaxRecognition, MaxDNA) and the shared building-blocks file, explained and downloadable.
---
<p class="eyebrow">MAXGEN v<!--CURRENT--> · twelve files, one version</p>
# The schemas

Since version 2.0, MAXGEN has eleven record schemas plus one shared building-blocks file; the current release is <!--CURRENT-->. Every file is JSON Schema Draft 2020-12. All twelve files share one version number, and within 2.x every change is additive: a file valid under 2.0 stays valid under every 2.x.

<!--CARDS-->

## Shared building blocks

`maxgen-defs.schema.json` is not a record type: no document is valid against it alone. It holds the parts every record schema uses: ids and small types, the date block, the place block, the evidence link (the part of every claim that says which evidence it rests on), the quote check, saved copies, regions and media pointers, roles and event types, the claim header, the common and lifecycle fields, and the task provenance block. [Download the shared file](/schemas/maxgen/v2/maxgen-defs.schema.json).

## Canonical addresses

<!--ADDRESSES-->

Every file declares its own address in its `$id`. A record schema points into the shared file by a relative address, so all twelve files sit side by side in one folder. Validators load all twelve files into one registry and never fetch anything over the network; the addresses do not need to be reachable for validation to work. The fingerprints are also in [SHA256SUMS.txt](/schemas/maxgen/v2/SHA256SUMS.txt), and a machine-readable list of every version is at [`/schemas/maxgen/index.json`](/schemas/maxgen/index.json).

## MAXGEN 1.13 (archived) {#archived-113}

The seven 1.13 schema files stay exactly as they were, at their permanent addresses under `/schemas/maxgen/v1/` and `/schemas/maxgen/v1.NN/`. MAXGEN 2.0 does not read 1.x files. The 1.13 pages are kept for reference: <!--ARCHIVED-->.

## Check a record

Paste any JSON record into the [browser validator](/validator/) to see whether it conforms. Nothing you paste leaves your computer.
