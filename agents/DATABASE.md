# DATABASE — the one file the person can open and read

Everything the three roles produce lives in one SQLite file (SQLite is a database kept in a
single file, with no server to run). Call it `genealogy.db`, in the project folder. The person
can open it any time and see exactly what has been found, what has been checked, and what is
still waiting.

**The only way to write to it is `maxgen_db.py`.** It checks every file against the twelve
MAXGEN 2.0 schema files on the way in, and against the rules below, so nothing invalid can
enter. Do not write to the database any other way. It reads no MAXGEN 1.x file and opens no
database made by a 1.x kit: start a new one.

## What is in it

**Documents.** One table per kind. Every row keeps the full MAXGEN document in a `doc` column,
plus a few columns copied out for searching. The document is the truth; the columns are for
convenience.

| Table | Holds | Who writes it |
|---|---|---|
| `max_record` | one row per source document (a MaxRecord) | The Genealogist adds; the person adds what they know through the person page; Quality Control marks pass or fail |
| `max_person` | one row per person (a MaxPerson): claims only, no family links | The Genealogist adds and appends claims; the person page appends a claim the person typed; Quality Control marks pass or fail |
| `max_relationship` | one row per family link: a `parent_child` pair, a `family` (a couple or parent set, with its children's memberships), or an `association` | Only the helper's `link` command (and import or analysis tasks); Quality Control marks pass or fail |
| `max_task` | one row per unit of work (a MaxTask), including reviews and proofs | The Orchestrator creates; the Genealogist claims, logs searches and closes; the helper writes the person's answers as review tasks |
| `max_source` | one row per place records live (a MaxSource) | The Genealogist adds when it finds a new one |
| `max_event` | one row per occasion a document records (a MaxEvent) | The helper, one for each event entry on a record |
| `max_place`, `max_group` | real places and historical groups (MaxPlace, MaxGroup) | Analysis and import tasks only |
| `max_name`, `max_recognition`, `max_dna` | knowledge about a name; a machine reading of an image; a DNA test (always private) | The tasks that make them |

**Indexes** (`claim_index`, `link_index`, `membership_index`, `mention_index`, `media_index`,
`region_index`, `search_step`, `id_redirect`): the helper keeps them up to date on every write
and can rebuild every one from the documents (`reindex`). They are never data. A person's
parents, children and partners are found here, from the relationship records.

**Logs**, append-only: `review` (every pass or fail Quality Control has given, with a note),
`run_log` (a line per event), `agent_session` (who wrote under which session),
`status_log` (every change of a claim's status) and `annotation_log` (every value the helper
filled or raised). `dead_end` is a view: every search step whose verdict is `dead_end` or
`inaccessible`. `meta` holds the database's own id, the MAXGEN version and a few settings, and
the tree's own keys: `tree_id` (a random id, never made from anyone's identity), `tree_kind`
(`own`, `friend`, `one_name`, `sample` or `staging`), and, once Probable Pedigree has opened
the tree, `tree_name`, `home_person_id` and `owner_key_check`.

**Bookkeeping (kit 1.2.0, the tree file format MAXGEN 2.1).** Never in a MAXGEN file or a
shareable export:

- `write_tx`: one row for every write transaction, saying which session wrote and what kind of
  writer it was (`you` only for the person's own session in Probable Pedigree, `genealogist`,
  `quality_control`, `orchestrator`, `import`, `agent` for any other AI session, or `other`).
- `change_log`: one row for every row added, changed or removed in the data tables, `review`,
  `id_redirect`, `meta`, the owner tables and `action_log`. Triggers stored inside the file fill
  it (a trigger is a small rule the database runs by itself), so it sees every writer, even a
  program that writes SQL by hand; such a change has no transaction stamp. The kit never
  removes a row from a data, review or owner table, so a removal in this log means something
  other than the kit changed the file: `check` reports it. The log keeps 30 days, and at least
  the newest 200,000 changes.
- `action_log`: one row for every transaction, with a plain summary and an **undo recipe** (the
  steps that would take the change back: withdraw what was added, bring back what was
  withdrawn, unmerge a merge, put back an owner choice or a meta value). Probable Pedigree uses
  it for Undo and Recent changes. Recipes are kept 365 days for the person's own changes and 30
  days for everyone else's.
- **The owner tables** (`owner_setting`, `owner_branch`, `owner_rule`, `owner_mark`,
  `owner_tag`, `owner_list`, each with a `..._now` view of what is in force): the person's own
  choices for this tree, such as who is held back from sharing, what is shared, locks,
  bookmarks, tags and saved lists. They only grow: each change adds a row that sets or clears,
  and the newest row wins.

A tree made by kit 1.1.0 or earlier has none of these. The kit reads it, but writes to it only
after `python maxgen_db.py upgrade genealogy.db`, which saves a checked backup copy first
(beside the tree, or where `--backup` says) and then only adds tables, views, triggers, meta
keys and any column an early 2.0 tree lacks (the review table's `approved_ids`). Run twice, the
second run changes nothing. Once upgraded, an older kit refuses to write
the file, so every writer keeps the stamps, the log and the owner rules.

**The maintenance file.** While `genealogy.db.pp-maintenance` exists beside the tree, Probable
Pedigree is restoring or moving it, and every write is refused with a plain sentence, `init`
included, before anything in the file changes. Wait until the file is gone, then carry on.

**One tree, one name.** The kit opens a tree by its real name, however the path was written (a
short name such as `GENEAL~1.DB`, a trailing dot, a link), so every way of naming the file shares
one save log and finds the same maintenance file. A tree file that has a second name on the
computer (a hard link) is never written: remove the extra name and keep one file.

## Staged, approved, rejected

A record, person, link, place, group or DNA test enters as **staged**. Quality Control moves it to
**approved** or **rejected**. Tasks, sources and names have no stage.

**"The pedigree" means the approved persons, the approved links between them and the approved
records they cite.** Nothing staged or rejected is part of the pedigree, and nothing staged is
ever shown to the person as a finding. This is the gate: the Genealogist cannot approve its own
work, and `maxgen_db.py` will not approve a person while any record it cites is still staged or
rejected, nor a link while its persons or records are.

Two kinds of write. A **content write** changes what a document says (a new claim, a new
record, a new membership, a retraction) and puts it back to staged. An **annotation write**
fills or raises a value the helper owns (a quote check, a raised living flag, the preferred
parent set, a place attached by `resolve`, a status copied from an accepted proof) and changes
no stage; each is logged.

## How a write goes in

Every write command runs the same steps, in order: read the file; strip any value the helper
owns (the writer cannot set them); on `--replace`, match every stored item by its permanent key
(claims by `assertion_id`, mentions by `mention_id`, event entries by `ref` …) and refuse if
one is changed or missing; put the helper's stored values back; check the schema; apply the
rules below; check the stored document is complete; commit. A refusal prints `REFUSED:
<plain sentence> (R-nn)`, exits 1 and changes nothing.

## The rules the helper enforces

In plain words, with the numbers a refusal prints:

- **R-01 to R-04 — Right shape, right version, real references.** The file matches its schema
  exactly (unknown field names are refused); it says `schema_version` `2.x`; the helper fills
  its own fields; and every id it names exists — no invented sources, no link to a person who
  is not in the database.
- **R-05 — Living A.** Every file that names a person says whether they may be alive; living
  means `tier2-private`; when the helper learns someone may be alive it raises the flag on
  everything that names them; and living is always worked out again when read. Lowering a flag
  needs the person's yes. A DNA match list is always private and living.
- **R-06 — Sound links.** No link from a person to themselves, no parent who is also the
  child's child, no family whose two partners are one person.
- **R-07, R-29 — Append-only.** A replace must name the stored version's fingerprint
  (`--expect-sha256`) and may add but never change or drop a claim, mention, event entry,
  line label, saved copy, step, membership or partner. Withdrawing is `retract`; nothing is
  ever deleted.
- **R-08 — Status only by the four paths:** new claims are `proposed`; `retract`; `apply-proof`
  of an accepted proof (the only way to `proven`); and an import's disputed or disproven marks.
- **R-09 — One original, one record.** A second record of the same original (same archive
  reference, same file fingerprint, or same address and line) is refused.
- **R-10 — References into records** point at a live record, an active line and a fitting event
  entry. **R-12 — Dates** must be possible; a date worked out from an age is `calculated`; a
  date's written words appear in the quote; nothing in the future.
- **R-11 — The 0.4 cap.** A claim whose evidence is not reliable is refused above 0.4.
- **R-13, R-15 — Quote checks** are run on every claim and re-run when a record is corrected;
  a claim that no longer matches puts its holder back to staged.
- **R-14 — Sessions.** Every write runs under a session and a task that session has claimed.
  The role and the name `person` belong to the person's own session in Probable Pedigree, so
  `session` refuses them.
- **R-16 — Copies.** A saved file is described once; a copy is never more open than its record;
  a file not yet on this computer is pointed at by its address until it is fingerprinted.
- **R-17 — A census line never makes a birth parent.** **R-18 — Genetic** parentage needs DNA
  or an accepted proof; facts citing an amended record are flagged. **R-19** — a disproven fact
  is not re-added from the same record.
- **R-20 — One of each.** One `parent_child` record per pair; one membership per child per
  family per set kind; exactly one preferred parent set per child.
- **R-21, R-22 — Events.** One principal per birth, baptism, death or burial occasion; a
  couple's own marriage is a family fact, not a person claim.
- **R-23, R-24 — Who makes what.** No placeholder person at import; MaxRelationship, MaxPlace,
  MaxGroup and MaxEvent files are added only by import or analysis tasks (agents use `link`).
- **R-25** — a DNA match between two tests here is stored once.
- **R-26 — Approval order:** records before the persons and links that cite them.
- **R-27** — content writes re-stage; annotation writes do not.
- **R-28 — Merges and splits** need the right accepted task (persons: with the person's yes);
  two things marked distinct are never merged.
- **R-30** — a session's name holds no version or date.
- **R-31 — Adoption needs the person's yes**, recorded on the task first.
- **R-32 — Two birth parent sets** are stored but flagged with a WARNING and a new
  `resolve_conflict` task, whether they came by `link` or by an import.
- **R-33 — The owner tables are the person's own.** Only the person's own session in Probable
  Pedigree, presenting the tree's owner key, may write them: hold-back, sharing settings, picks,
  locks, bookmarks, tags, saved lists and the tree's other owner settings. The owner key lives
  in the computer's own password vault, outside the tree folder; the tree keeps only a check
  value made from it, never the key. The kit never asks for the key, the command line never
  accepts it, and the command line refuses to write under the person's session. The one
  exception: any session may record an `outside_holdings` mark, a dated count of what a
  website holds, which is a finding and not a choice.
- **R-34 — A locked person is the person's alone.** An AI, importer or sync session may not
  change a person the owner has locked, nor add or withdraw a family link of theirs. Quality
  Control's reviews and the person's own session still may.

## Commands

```
pip install jsonschema referencing rfc3987 rfc3339-validator          once

python maxgen_db.py init     genealogy.db
python maxgen_db.py session  genealogy.db --name <role name> --kind ai|human --role <role> --model-name <model> --run-id <run>

Writing (every one takes --as <session id>, or MAXGEN_SESSION):
python maxgen_db.py add      genealogy.db <file.json> --task <task_id> [--replace --expect-sha256 <sha>]
python maxgen_db.py claim    genealogy.db <claims.json> --task <task_id>
python maxgen_db.py link     genealogy.db <request.json> --task <task_id>
python maxgen_db.py task     genealogy.db <task_id> pending|processing|done|failed|escalated|on_hold|cancelled [--verdict X] [--note "..."]
python maxgen_db.py task     genealogy.db <task_id> pending --answer accepted|rejected|rework --note "<the person's words>"
python maxgen_db.py step     genealogy.db --task <task_id> --source "<site>" --query "<what>" --found "<what came back>" --verdict <verdict>
python maxgen_db.py dead-end genealogy.db --task <task_id> --person <person_id> --source "<site>" --query "<what>" --note "<result>"
python maxgen_db.py review   genealogy.db <id> pass|fail --by "Quality Control" --note "<why>"
python maxgen_db.py retract  genealogy.db <id> --task <task_id> --reason "<why>"
python maxgen_db.py apply-proof    genealogy.db <proof_task_id>
python maxgen_db.py withdraw-proof genealogy.db <proof_task_id> --task <review_task_id> --reason "<why>"
python maxgen_db.py prefer   genealogy.db <membership_id> --task <task_id>
python maxgen_db.py merge    genealogy.db <kind> <survivor_id> <absorbed_id> --task <task_id>
python maxgen_db.py unmerge  genealogy.db <kind> <absorbed_id> --task <task_id>
python maxgen_db.py distinct genealogy.db <kind> <id1> <id2> --task <task_id> --reason "<why>"
python maxgen_db.py split    genealogy.db person|group|place <id> --task <task_id> --move <assertion_id>... [--move-link <relationship_id>...]
python maxgen_db.py resolve  genealogy.db place|group <place_or_group_id> --task <task_id> (--claims <assertion_id>... | --written "<words>")
python maxgen_db.py apply-run-review genealogy.db <judge_review_task_id>
python maxgen_db.py import-maxgen    genealogy.db <folder> --task <import_file task_id>
python maxgen_db.py reindex  genealogy.db
python maxgen_db.py upgrade  genealogy.db [--backup <copy.db>]       (a tree made by kit 1.1.0 or earlier)

Reading (no session needed):
python maxgen_db.py status     genealogy.db
python maxgen_db.py show       genealogy.db <id>
python maxgen_db.py list       genealogy.db <table> [--stage approved] [--status pending] [--limit 20]
python maxgen_db.py export     genealogy.db <table> <out.jsonl> [--stage approved] [--status escalated] [--shareable]
python maxgen_db.py lineage    genealogy.db <person_id> [--mode preferred|birth|adoptive|all] [--gens 4] [--json]
python maxgen_db.py occurrence genealogy.db <occurrence_id>
python maxgen_db.py check      genealogy.db
python maxgen_db.py portal     genealogy.db portal.html [--root <person_id>] [--gens 5] [--shareable-out pedigree_shareable.html]
python maxgen_db.py kit-hash   genealogy.db            (reads one DNA kit number from standard input)
```

What the less obvious ones do:

- `claim` appends claims to an existing person without resending the file. `link` writes a
  family link (GENEALOGIST, "Family links"): `parents`, `couple`, `association`, and, only
  under an `import_file`, `resolve_conflict` or `build_tree` task, `siblings` (children who share
  parents nobody knows yet).
- `task --note` is stored as `escalation_reason` (`escalated`), `error_message` (`failed`) or
  `result.verdict_reason` (`done`). `task --answer` records the person's answer, relayed by
  the Orchestrator, as a new `judge_review` task; `--note` holds their own words.
- `step` appends a search to the task's log (`result.steps`); `dead-end` is its short form.
- `show` prints the stored document and, on its last line, its `doc_sha256` — the fingerprint
  a `--replace` must name.
- `retract` withdraws, never deletes: a claim (`<assertion_id>`), a document, a membership, a
  mention (`<record_id>#<mention_id>`), an event entry (`<record_id>#event:<ref>`), a saved copy
  (`<record_id>#copy:<sha256>`), a partner (`<family_id>#partner:<person_id>`) or a task (it
  becomes `cancelled`). Only the author, or an accepted review of a whole run
  (`apply-run-review`, one decision per object), may retract.
- `apply-proof` copies an accepted proof's conclusion onto the claims it settles, and its
  parent-set changes (a set's kind, the main set, a parent's link added to a set, a wrong set
  retracted); `withdraw-proof` puts them back to proposed and opens a review. `prefer` records
  the main parent set (and closes the helper's "main parents changed" question about that
  child). `merge`, `unmerge`, `distinct` and `split` handle duplicates and conflations,
  never rewriting anything: a merged-away id forwards to its survivor, and `unmerge` restores
  it exactly. `resolve` attaches a place or group to claims that wrote it in words, and
  `--undo` takes it off again.
- `lineage` prints a person's parent sets and which one each view would draw.
  `occurrence` gathers one occasion: every claim citing it, and other documents' dates and
  places for it as candidates. `check` is a read-only health check. `reindex` rebuilds the
  indexes from the documents and reports any difference.
- `export --shareable` writes only what may be shown to others (approved documents, no private
  claim, note or person, the citation only of an all-rights-reserved record), and refuses tasks
  and DNA.
- `kit-hash` prints the fingerprint of a DNA kit number for `kit_id_hash` and `match_kit_hash`,
  keyed with this database's own secret; a kit number itself is never written down anywhere.
- `import-maxgen` brings in the tables another MAXGEN database exported (one
  `<table>.jsonl` file each, made with `export`), keeping every id and the merge history, under
  an `import_file` task whose `payload` says `"format": "maxgen"` and names the other database's
  id (`status` prints it). Everything arrives staged for Quality Control.

`--schemas <folder>` points the helper at the twelve `.schema.json` files if they are not in
the default place (a `schemas` folder next to the script, or `../../schemas`).

`portal` takes `--root <person_id>` (who sits at the left of the chart; must be approved;
default: the person with the most ancestors), `--gens` (generations drawn, 1 to 8, default 5)
and `--shareable-out` (where the shareable page goes; default `pedigree_shareable.html` beside
the owner's page).

## How the person looks at it

Three pages. Only the last one is meant for anyone else.

- **The person page — private, live, this computer only.** `python probable_pedigree.py
  genealogy.db [--port 8790] [--schemas <folder>]` prints an address such as
  `http://127.0.0.1:8790/open?key=...` (the next free port, up to 8799, if that one is busy).
  The key in it is new each run and is the person's alone: anyone who has the address can open
  the page while the program runs, so it goes to the person and no one else. The person opens
  it in their own browser, which then keeps the key until the program stops. It shows one
  person at a time, anyone in the file: the approved chart (it can follow the main parents, the
  birth line, the adoptive line or every parent set, so an adopted person can follow both
  lines), their parent sets, their facts with the document and the quoted words behind each,
  where documents disagree, the research tasks about them and the places already searched —
  living relatives included. It also shows what is waiting for Quality Control (people,
  documents, links and their facts), always marked "Waiting for a check" and never on the
  chart; that is not a finding. It answers only on this computer, uses no internet and no AI,
  and runs until stopped with Ctrl+C. It changes nothing, apart from four actions that only add,
  through `maxgen_db.py`'s own functions and gates, under the page's own human session (the
  person). **Ask the Genealogist** adds a `pending` MaxTask (`task_type: other`, `payload.kind:
  "person_request"`) for the Orchestrator. **Add something you know** adds a MaxRecord of what
  the person typed (`record_type: other`, "Typed on the person page", `tier2-private`,
  `source_url` its own `urn:uuid:`; a link they gave is kept in its text and in
  `extensions.person_page.link`) and one claim citing it at 0.4, filed under that run's
  `person_page_session` task. That puts the person back to staged: off the chart, with everyone
  above them, until Quality Control checks them again. **Make these the main parents** records
  the person's choice of parent set (`prefer`). **Decisions waiting for you** records the
  person's own answer to a proof, in their own words (a `judge_review` with `channel:
  person_page`, the only way a claim can become proven). No claim or document already there is
  changed or deleted.
- **`portal.html` — the private snapshot.** `python maxgen_db.py portal genealogy.db
  portal.html` writes it: counts, the approved pedigree drawn as a chart of the main parents
  (click a person to re-centre on them, drag to pan, scroll to zoom; with scripts off it is
  still a complete picture), the people with more than one parent set, the sources behind it,
  what is waiting for Quality Control, what was rejected and why, tasks, dead ends — living
  relatives included. It is for the person only. Lines on its chart, and on the person page:
  solid = birth parent; dashed = adoptive, step, foster or guardian; dotted = kind of parent
  not stated (or possible parents, not settled).
- **`pedigree_shareable.html` — the only page meant for others.** The same command writes it
  beside `portal.html`: the chart and its sources only, with living, private and unsure people
  withheld, birth parents drawn solid and every other parent dotted, and nothing said about
  adoptions. Names and years on it come only from what may be shared (never from a private
  document or from what the person typed), and possible parents are never drawn. Quality
  Control checks it before anyone else sees it.

Double-click either of the last two. They open in any browser from the folder, need no server
and no internet, and are snapshots: regenerate to refresh. Any agent that can write a file can
make them, so no agent needs to control a browser to show the person progress.

Other ways to look:

- `python maxgen_db.py status genealogy.db` prints the database id and the counts: how many
  records, persons, links and tasks, how many of each are staged, approved and rejected, the
  claims by status, and anyone stored living whom the documents now show has died.
- `python maxgen_db.py list genealogy.db max_person --stage approved` lists the pedigree's
  people.
- **DB Browser for SQLite** (free, sqlitebrowser.org) opens `genealogy.db` as tables you can
  scroll and search, with no commands at all.
- `export` writes any table to a `.jsonl` file (one JSON document per line) for backup or for
  another program.

## Backups

The database is one file. Copy it. Before any batch, the Orchestrator copies `genealogy.db` to
`backups/genealogy_<date>_<batch>.db`. A bad batch is undone by restoring the copy.
