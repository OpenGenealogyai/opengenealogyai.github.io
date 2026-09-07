---
title: Licence
description: MAXGEN schemas and specification text are licensed CC-BY 4.0. Free to use, build on and redistribute with credit to OpenGenealogyAI. Records carry their own licence. Repository code is MIT.
---
<p class="eyebrow">Legal</p>
# Licence

## The standard: CC-BY 4.0

The seven MAXGEN schema files and the text of the standard on this site are licensed under [Creative Commons Attribution 4.0 International (CC-BY 4.0)](https://creativecommons.org/licenses/by/4.0/). You may copy, change, redistribute and build on them for any purpose, commercial or not, without asking. The one condition is credit: say that your work uses or is derived from MAXGEN by OpenGenealogyAI, and link to this site or the licence.

That is the whole obligation. There is no fee, no registration, no share-alike requirement (you do not have to open-source your own work), and no restriction on commercial use.

### How to give credit

Any of these satisfies the licence:

- In software or documentation: *"Uses the MAXGEN standard by OpenGenealogyAI (opengenealogyai.org), CC-BY 4.0."*
- In a dataset or file: keep the `schema_version` field and the `$id` reference to `opengenealogyai.org` that every MAXGEN record already carries. That is attribution.
- In a paper or article: cite as shown at the bottom of this page.

Why attribution rather than the public domain? So that wherever the standard travels, people can find its home, its current version, and the process for proposing changes. A standard is only useful if adopters can find each other.

## Records: each carries its own

Every record written in MAXGEN declares its own `redistribution_license`: `CC0`, `public-domain`, `CC-BY`, `CC-BY-SA`, or `tier2-private` (never redistribute). Where a source requires attribution, the `attribution{}` object in the record holds the credit. The licence on the standard says nothing about the licence on your data; your records stay under whatever terms their owner sets.

## Code: MIT

The validators, fixtures and tooling in the schema repository are released under the [MIT licence](https://opensource.org/license/mit).

## Names and logo

"MAXGEN", "OpenGenealogyAI" and the star-tree logo identify the canonical standard and its steward. Use them to say your software implements MAXGEN. Do not use them for a fork that diverges from the canonical schemas, and do not imply that OpenGenealogyAI endorses your product unless it does.

## How to cite

> Maxwell, G. *MAXGEN: The Maxwell Genealogy Standard*, version <!--CURRENT-->. OpenGenealogyAI. https://opengenealogyai.org/ — licensed CC-BY 4.0.
