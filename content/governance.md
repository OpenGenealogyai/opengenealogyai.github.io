---
title: Governance
nav: /governance/
description: Who decides how MAXGEN changes, how to propose a change, and the published path from a single steward to an advisory board.
---
<p class="eyebrow">How the standard changes</p>
# Governance

MAXGEN is dedicated to the public domain. Anyone can use it, fork it, extend it, or build on it without asking. What is stewarded is the **canonical version**: the one set of files at `opengenealogyai.org/schemas/maxgen/` that may be called MAXGEN. This page says who decides what goes into it, how, and how that will change as the community grows.

> "MAXGEN is open because anyone can use it and build on it. It is not open to fragmentation. One canonical version. One steward. Every change earns its place in the record." — Garlon Maxwell

## Today: a steward

The standard's author, Garlon Maxwell, is the steward: the technical lead who has final say on what enters the canonical schemas until the advisory board exists. Every decision is public, reasoned, and recorded in the [changelog](/versions/#changelog). This is the same arrangement most successful standards began with, including the OpenAPI specification before it moved to a foundation, and the page below says exactly when and how it ends.

## How to propose a change

1. **Open a GitHub Issue** in the schema repository using the *Schema Change Proposal* template. It asks four things:
    - **Problem**: what real use the current schema fails to support.
    - **Proposed change**: the new or altered fields, with type and description.
    - **Backward compatibility**: does any existing valid record become invalid? If so, how would it migrate?
    - **Examples**: at least one before-and-after JSON snippet.
2. **Open discussion.** Anyone may comment. Minor (additive) changes stay open at least **7 days**; major (breaking) changes at least **30 days**.
3. **Review.** The steward reads the proposal and the discussion, may ask for more examples, and may put the question to a wider vote.
4. **Decision with reasons.** Accepted or declined, in writing, on the issue. Nothing merges without explicit sign-off.
5. **Implementation.** The change is applied to the schema files, fixtures are re-validated, the changelog is written, and a new version is released under the [versioning policy](/versions/).

Challenges that did **not** lead to a change are logged too. A rule that survived a good argument is worth knowing about.

If you do not use GitHub, write to **maxgen@opengenealogyai.org** and the steward will open the issue on your behalf.

## Tomorrow: an advisory board

A standard controlled by one person is a standard institutions hesitate to adopt. The path to shared control is published now so that adopters can see it.

**Trigger.** The board convenes when there are three or more independent implementations of MAXGEN, or when the first institution (an archive, a library, a genealogical society, a software vendor) adopts it, whichever comes first.

**Composition.** Five to seven seats: the steward, working genealogists, developers who have shipped an implementation, and at least one representative of an archive or library. No single company may hold a majority.

**What the board decides.** Major versions, additions to the core schemas, and changes to the normative privacy rules. Extensions need nobody's permission and are never voted on.

**What the steward keeps.** A tie-breaking vote and custody of the name and the canonical address. The steward may be **overruled on a major version by a two-thirds vote**. That clause is the point: it is the guarantee that the standard cannot be held hostage by its author.

**Openness.** Meetings are announced in advance, minutes are published on this site, and every vote is recorded.

**How seats are filled.** The first board is appointed by the steward from people who have actually implemented or adopted MAXGEN, announced publicly with a 30-day objection period. After that, seats are two-year terms; the sitting board fills vacancies by simple majority from public nominations. Anyone may nominate anyone, including themselves.

**Quorum and voting.** A quorum is a majority of seats. Additive changes pass by simple majority of those voting. Major versions and changes to the privacy rules need two-thirds of all seats. Every vote is recorded with names.

**Emergency fixes.** A security or privacy defect in a schema may be patched immediately by the steward as a patch release, with a public notice the same day and retrospective board review within 30 days. Nothing else skips the process.

## Continuity: what happens if the steward is gone

A standard that dies with one person is not a standard. Three safeguards:

1. **The licence is CC0.** Nobody needs permission to carry on. If the canonical site went dark, every schema file and this whole site could be republished by anyone, from the public git history, the same day.
2. **A named successor.** The steward keeps a named successor on file with the board (or, before the board exists, publicly in the repository). If the steward is unreachable for 90 days, the successor, or the board by majority, assumes stewardship and the name.
3. **Frozen releases.** Every version is archived at a permanent address, so an adopter never depends on the steward being available to fetch the files they validated against.

## What the steward does not control

- Private forks and extensions. Add fields in `extensions{}` or fork the files outright. No permission needed.
- Tools built on MAXGEN. Commercial or free, open or closed.
- Records you publish in MAXGEN format.
- Proposals. All are welcome and all get an answer.

What you may not do is call a diverging fork "MAXGEN". The name means the canonical version.

## Conflict of interest

The steward's own genealogy platform, The Probable Pedigree, implements MAXGEN. It gets no special treatment: its change requests go through the same public process, and anything specific to its business (pricing, payments, accounts) stays in `extensions{}` and never enters the standard. The MaxTask schema deliberately contains no payment fields for exactly this reason.
