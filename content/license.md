---
title: Licence
toc: true
description: MAXGEN schemas and specification text are dedicated to the public domain under CC0. Records carry their own licence. Repository code is MIT.
---
<p class="eyebrow">Legal</p>
# Licence

## The standard: CC0

The seven MAXGEN schema files and the text of the standard on this site are dedicated to the public domain under the [Creative Commons CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/) dedication. You may copy, change, redistribute and build on them for any purpose, commercial or not, without asking and without attribution.

Why CC0 rather than a licence that requires credit? Because a standard only works when adopting it costs nothing, legally or administratively. An archive's counsel should be able to approve MAXGEN in one reading. Credit is welcome, never required.

### If you want to credit us anyway

- In software or documentation: *"Uses the MAXGEN standard by OpenGenealogyAI (opengenealogyai.org)."*
- In a dataset: the `schema_version` field and the `$id` reference to `opengenealogyai.org` in every MAXGEN record already say where it came from.
- In a paper or article: cite as shown at the bottom of this page.

## Records: each carries its own

Every record written in MAXGEN declares its own `redistribution_license`: `CC0`, `public-domain`, `CC-BY`, `CC-BY-SA`, or `tier2-private` (never redistribute). Where a source requires attribution, the `attribution{}` object in the record holds the credit. The standard is public domain; the data written in it is whatever its owner says it is.

## Code: MIT

The validators, fixtures and tooling in the schema repository are released under the [MIT licence](https://opensource.org/license/mit).

## Names and logo

"MAXGEN", "OpenGenealogyAI" and the star-tree logo identify the canonical standard and its steward. Use them to say your software implements MAXGEN. Do not use them for a fork that diverges from the canonical schemas, and do not imply that OpenGenealogyAI endorses your product unless it does.

## How to cite

> Maxwell, G. *MAXGEN: The Maxwell Genealogy Standard*, version <!--CURRENT-->. OpenGenealogyAI. https://opengenealogyai.org/ — CC0 1.0.
