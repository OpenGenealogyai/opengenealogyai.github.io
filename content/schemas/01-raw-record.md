---
title: MaxRecord
slug: maxrecord
schema: raw-record
tagline: A source document exactly as it was found. No interpretation added.
fixture: raw-record/valid-marriage-certificate.json
description: MaxRecord is the evidence layer of MAXGEN. It stores one original document, as written, with its people, dates, places, licence and privacy flag.
---
## The clerk who could not spell

In 1743 a parish clerk in Dumfriesshire wrote a baptism into his register and spelled the father's surname "Makeswell". The family's name was Maxwell. He was not wrong in any way that matters: he wrote what he heard, in the spelling of his day. Three centuries later a search engine that does not know Makeswell is Maxwell will skip the entry, and a researcher will lose a branch of her family because of a spelling choice made by a man dead for 250 years.

MaxRecord exists to keep that entry exactly as written. Not corrected. Not normalised. Not interpreted.

## What it is

MaxRecord is the **evidence layer**. One MaxRecord is one document: a baptism entry, a census page, a gravestone, a ship manifest, a newspaper notice, a will. It stores what is on the document, who is mentioned, when and where it was made, where the copy came from, and who transcribed it with what confidence.

What it never does is say what the document *means*. The record says "Makeswell". Whether Makeswell is Maxwell is an interpretation, and interpretations live in [MaxPerson](/schemas/maxperson/). Keeping the two apart is what lets a future researcher, or a better AI model, look at the original evidence again.

## How it is used

1. A document image is transcribed; the who-read-it-and-how details go in a [MaxRecognition](/schemas/maxrecognition/) record.
2. A MaxRecord is created from that transcription: the type of document, every person named (`persons_mentioned[]`, with their roles: subject, spouse, witness, officiant…), the date as a range, the place as written, the source URL, the licence.
3. Every claim later made in a MaxPerson cites this record's `record_id`. If the document turns out to be a forgery, the record is retracted with a note. It keeps its ID and its history.

## Things worth knowing

- **Dates are ranges.** `year_min` / `year_max` with optional month and day. "Spring 1847" is honest as `1847–1847` with no month. A precise date that the document does not contain is a lie.
- **Places are kept as written**, with optional normalised fields (country code, state, county, town) alongside.
- **Everyone on the page is kept**, not just the "principal". Witnesses and neighbours are the social network of the document.
- **Licence is mandatory.** `redistribution_license` is one of CC0, CC-BY, CC-BY-SA, public-domain, or `tier2-private` (never redistribute). If `is_living_flag` is true the record is private no matter what.
- **`extensions{}`** holds source-specific fields (a groom's regiment, a cemetery plot, an archive's own reference) without forking the schema.

## Record types

<!--ENUM:record_type-->

## Fields

<!--FIELDS-->

## Example

A marriage certificate, as a MaxRecord. This is one of the fixtures the schema is tested against.

<!--EXAMPLE-->

## Listen

<!--PODCAST-->
