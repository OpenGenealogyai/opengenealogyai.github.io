---
title: MaxTask
slug: maxtask
schema: task-queue
tagline: A unit of research work for a human or an AI, with proof required.
fixture: task-queue/valid-escalated-with-opus.json
description: MaxTask describes a unit of distributed genealogy work: what to do, who may do it, what counts as done, and what evidence must come back.
---
## The work is irreducibly distributed

The UK National Archives holds eleven million documents. The US National Archives holds thirteen billion pages. No organisation, model, or volunteer community can process all of it. Genealogical research will always be done by millions of people and machines in parallel, over decades. MaxTask is the standard way to describe one piece of that work.

## What it is

A task is a request: "transcribe this image", "decide whether the Johann Müller in record X is the John Miller in record Y", "search the Württemberg emigration lists for Müllers who left between 1840 and 1860", "resolve these two contradictory birth years". A task can go to an AI agent, a volunteer, or a specialist organisation. It can carry a cost cap, a deadline, dependencies on other tasks, and a review step before its result is accepted.

## What makes it different

- **Acceptance criteria** (`acceptance_criteria[]`): objective, checkable conditions that define "done" before work starts.
- **Evidence required** (`evidence_required[]`): the anti-hallucination rule. A task that needs a birth record must return a link to the birth record. A search must return the exact query and what it found. An AI cannot pass by producing plausible text.
- **A structured result**, not a text blob: what was tested, what was done, what was found, what changed, what to do next. Each step carries a **verdict**:

| Verdict | Colour | Meaning |
|---|---|---|
| `key_finding` | green | Confirmed. |
| `usable` | blue | A useful lead. |
| `inconclusive` | amber | Mixed. |
| `ruled_out` | red | Definitively disproved. A disproof is information, not a dead end. |
| `dead_end` | grey | Searched, found nothing. |
| `infra` | grey | Infrastructure work. |

- **An independent review** (`review{}`): the reviewer must not be the contributor. Rejection reasons include `fabricated_or_hallucinated` and `privacy_violation`, which build a quality signal over time.
- **A privacy gate** (`contributor_eligibility`): defaults to `first_party_only`. Any task about a living person can never be sent to outside volunteers or third-party AI services. This rule is part of the standard.

## What is deliberately not in it

Payment. An open standard must be neutral on business models. Who gets paid, how much, and how lives in `extensions{}`, so the standard describes the work and the quality gate while each implementation decides the economics.

## Task types

<!--ENUM:task_type-->

## Fields

<!--FIELDS-->

## Example

A conflict-resolution task escalated to a stronger model because two candidate mothers were within 0.05 of each other.

<!--EXAMPLE-->

## Listen

<!--PODCAST-->
