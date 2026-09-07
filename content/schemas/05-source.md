---
title: MaxSource
slug: maxsource
schema: source
tagline: A catalogue of where records live, built for the question "where do I look next?"
fixture: source/valid-v17-fs-wiki-public.json
description: MaxSource describes genealogical sources (archives, wikis, catalogues, databases) with structured coverage, access and provenance so a system can route a researcher to the right place.
---
## The navigator's problem

A researcher needs the church records of a Lutheran family in Württemberg in the 1840s. If she knows German genealogy, she knows the answer: Archion and Matricula for the parish books, the state archive and Ancestry for emigration lists, the FamilySearch Research Wiki for which parishes cover which years. If she does not know, finding that out takes hours of catalogues, link directories, forum posts and emails.

MaxSource makes that knowledge machine-readable.

## What it is

MaxSource is **not** a database of records. It is a database of **where records live**. One MaxSource record describes one source: its name and URL, what kind of thing it is, what it covers, how to get in, and how we came to know about it.

- **`source_type`**: directory index, research wiki, record catalogue, record database, archive, DNA database, library, newspaper archive, forum, blog, society, other. The type decides how a system interacts with the source.
- **`coverage{}`**: places (with country, state, county, town), record types, a time period as `year_min` / `year_max`, languages, and ethnic or religious groups. It answers queries like "everything covering Bavaria, death records, 1840 to 1870".
- **`access{}`**: free web, paid subscription, API, bulk download, scrape-only, or on-site only; whether a login is needed; the API base URL; whether robots.txt allows crawling; and crawl notes that hold the etiquette robots.txt does not ("batch 50 items per call, five seconds between requests").
- **`embedding_text`**: a natural-language description built for a vector database (a search index that matches meaning rather than exact words), so "Lutheran records in Württemberg in the 1840s" finds Archion even though no keyword matched.
- **`provenance{}`**: which seed source found this, its status (active, dead link, superseded, unverified), who asserted it and when it was last verified. Dead links are marked, not deleted.
- **`parent_source_id`** and **`coverage.record_count_estimate`**: sub-collections link to their parent, and an estimated record count is the primary ranking signal between a national index and a single parish page.

## The copyright question

Some source catalogues are other people's curated work. MaxSource handles this with the licence field: an entry derived from a third-party directory is marked `tier2-private` and used internally for routing only. Openly licensed entries can be published. The standard makes the decision explicit and auditable; the operator makes the decision.

## Source types

<!--ENUM:source_type-->

## Fields

<!--FIELDS-->

## Example

A FamilySearch Research Wiki article catalogued as a public MaxSource.

<!--EXAMPLE-->

## Listen

<!--PODCAST-->
