---
title: MaxPerson
slug: maxperson
schema: person
tagline: A probable person. Every claim is an assertion with a source and a score.
fixture: person/valid-mary-todd-lincoln.json
description: MaxPerson is the identity layer of MAXGEN. A person is a set of scored assertions from many sources, with several possible parents allowed to coexist.
---
## You cannot ask the dead who they are

Genealogy reconstructs people from fragments: names written by clerks who may have misspelled them, ages self-reported to a census taker, dates in calendars that no longer match ours. Most file formats can hold only one answer per fact, so the uncertainty has to be resolved before it can be saved: one John Maxwell, one father, and the doubt lives in a notebook instead of the data.

MaxPerson keeps the uncertainty in the data, where software and researchers can work with it.

## What it is

A MaxPerson is a **probable identity**: a bundle of assertions, each saying *this source claims this, with this confidence, recorded by this agent on this date*. There are no bare facts in a MaxPerson. There are only assertions with evidence behind them.

- `name_assertions[]`: every name this person was called, including the misspellings, each with its source.
- `birth_assertions[]` and `death_assertions[]`: date ranges and places, one per source.
- `parent_assertions[]` and `child_assertions[]`: **several candidates are normal.** A probable father at 0.75 and a possible father at 0.35 both stay in the record. The tree shows the likelier one; nothing is erased.
- `spouse_assertions[]`: marriages with type, dates, place and how they ended. Kept in both spouses' records.
- `event_assertions[]`: everything between the bookends: immigration, residence, military service, probate, burial. Event types map to GEDCOM tags.
- `external_id_assertions[]`: scored links to the same person on FamilySearch, WikiTree, Find a Grave and others. Scored, not merged, because those databases contain duplicates too.
- `dna_evidence[]`: DNA matches that touch this person as a candidate common ancestor, feeding the overall confidence.

## Composite confidence

`composite_confidence` is one number, 0 to 1, for how sure we are about the identity as a whole. It is computed from all the assertions, weighted by source quality, using a "noisy-OR" combination (two independent sources agreeing raise confidence more than either alone, but not as if they were certain). Because every assertion cites a `source_record_id`, the engine can notice when two assertions really come from the same underlying document and avoid double-counting.

## The merge model

When evidence shows two MaxPerson records are the same individual, one absorbs the other. `merge_history[]` records the confidence, method, signals, who merged and when. The absorbed record stays, marked `merge_status: merged_away`. If later evidence shows they were different people after all, the merge is reversed on the same quality of evidence that made it.

## Privacy

`is_living: true` locks the record. Public endpoints return "not found". The record still exists internally and can be researched, but it is never exposed. Linked DNA records inherit the same lock. This is a design requirement of the standard, not a policy any implementation can switch off.

## Fields

<!--FIELDS-->

## Example

Mary Todd Lincoln as a MaxPerson: two names (birth and married), an exact birth and death, one marriage, every claim citing the same marriage record.

<!--EXAMPLE-->

## Listen

<!--PODCAST-->
