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
  withdrawn, unmerge a merge, put back an owner choice or a meta value). A replace made with
  `Gate.add(doc, task, replace=True, expect_sha256=..., restorable=True)` (a person's own edit of a
  document's words) also carries the earlier version whole (`doc_restore`), put back only while the
  document is still exactly as that replace left it. Probable Pedigree uses it for Undo and Recent changes. Recipes are kept 365 days for the person's own changes and 30
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
<plain sentence> (R-nn)`, exits 1 and changes nothing. When another write holds the tree (the person attaching
thousands of places in Probable Pedigree, say) a command waits 5 seconds, then prints `REFUSED: the tree is busy ...`,
exits 3 and changes nothing: try again in a minute.

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
- **R-35 — The person's decision route.** The person may withdraw anything in their tree, with
  their own words: what they added themselves is withdrawn as its author, and what someone else
  added is withdrawn by the person's recorded decision (a review in their words, then the
  withdrawal). The person may bring back anything that was withdrawn; any other session only what
  its own task withdrew. These decisions need the person's own session and the tree's owner key.
  Nothing is deleted: the withdrawn thing stays, marked withdrawn, with the reason.
- **R-38 — The person's word that a relative died.** The person may mark a relative as having
  died with no document. The death claim cites the person's own typed record (at most 0.4) under
  an `owner_death` task carrying the person's yes, and the living answer accepts it everywhere,
  even over a stored `is_living` true. Withdrawing that claim makes the relative living again.
- **R-40 — Dated claims count for the living answer.** In the helper's living answer a person is
  not living when any evidence but DNA (imported and unchecked included) gives a death year, a
  birth 110 or more years ago, or a marriage 90 or more years ago or a child born 90 or more
  years ago (the dates the sharing rule R-39 already reads); such a person may be shared. A stored
  `is_living` true wins over the death year and the marriage and child clauses (only R-38 and
  R-40b beat it). Text-only dates and claims worked out from absence never count, nor does a
  marriage in a family, or a child through a parent link, that Quality Control rejected. The
  switch is `DATES_A_ON` in the helper; with it off the living answer is the four rules alone
  (the rejected family and link fix stays in both modes, for the sharing rule).
- **R-40b — A birth 110 or more years ago beats a stored living flag.** Same switch. A recorded
  birth whose latest possible year is 110 or more years ago (any evidence but DNA, imported and
  unchecked included) shows the person as deceased, and lets them be shared, even when
  `is_living` is true. Only R-38 comes before it. A death year, or an old marriage or child,
  alone still leaves a stored `true` standing. Common-sense guard: the birth does not beat a
  stored `true` when the same person's other dated records (a census, a residence, a marriage,
  a child's birth ...; never a burial, probate or other posthumous record) show them alive more
  than 110 years after that birth, which is most likely a mistyped year; the flag stands.

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
python maxgen_db.py store-media genealogy.db <file>     (stores a photo, scan or other file in Media/)
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
  (`<record_id>#copy:<sha256>`), one marked area of a saved copy, such as a face
  (`<record_id>#region:<region_id>`), a partner (`<family_id>#partner:<person_id>`) or a task (it
  becomes `cancelled`). Only the author, or the person's own review of a run (`apply-run-review`,
  one decision and one reason per object), may retract. A run review is the person's decision
  alone: the kit refuses `apply-run-review` from Quality Control or any AI agent session (R-35),
  so the command above is refused to every command-line session; the person's program applies it
  with the owner key. It also refuses a decision on anything the run did not write, a thing decided
  twice, and a review that takes back every live finding of the run at once (R-29).
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
  claim, note or person, the citation only of an all-rights-reserved record, no saved copy of a
  photo that a photo claim ties to someone living, private or held back, and no place on a fact
  or a document when that place is private: a street, an address, a building, a farm, an estate,
  a plantation or a place of no known kind that someone who may be living is tied to, by its id
  or by the same words written without one; a residence at such a place is left out whole), and
  refuses tasks and DNA.
- `store-media` stores one file in the tree folder's `Media/ab/<sha256>.<ext>` and prints its
  fingerprint, so a record can describe it as a saved copy (`saved_copies`, `sha256`). What the
  file is comes from its contents, never its name: a program, a script, an archive or a kind the
  kit cannot tell is refused, and so is a file larger than its kind may be (pictures 200 MB, PDF
  and sound 500 MB, video 4 GB, text and web pages 20 MB). A stored file is never overwritten and
  nothing is ever deleted; storing the same file again changes nothing.
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
- **A program that shows the whole tree at once** (Probable Pedigree's speed cache) reads it with
  `maxgen_db.TreeSnapshot.build(con)`: one read transaction that streams each table once and keeps
  only compact facts. Its answers equal the per-document readers' (who is living, parent sets, the
  chosen set, the chart's main parents, names and dates), and `TreeSnapshot.build(con, people=[...])`
  reads only what the answers about those people need, so a program can keep a copy current after
  each change. `maxgen_db.checked_view(con, person_id)` gives the claims Quality Control passed for a
  person sent back to "Waiting for a check" by new claims. Merge redirects are followed backwards
  through the index `ix_redirect_new`, which `init`, `upgrade` and every write make.
- **A program that makes trees** (Probable Pedigree's "Start a new tree") calls
  `maxgen_db.init_tree(path, kind)`, which does `init`'s work without printing and returns the
  database id. A copy of a tree that is to become a tree of its own gets a new id through the
  Gate under the person's own session (`Gate.new_tree_id()`); the made-up sample family keeps its id,
  so it can never become a real tree.
- **The person's own page** (Probable Pedigree) writes through the Gate under the person's session:
  `Gate.withdraw(target, words, reason_code)` and `Gate.reinstate(target, words)` (R-35; a target
  is a claim, a document, a membership, `family#partner:<id>`, `record#<mention>`,
  `record#region:<region id>` or `document#note:<note id>`), `Gate.note(holder, text, ...)` (a note with its own id; a translation names its language, `lang`; a changed note
  is a new note naming the one it replaces, which is kept, withdrawn), `Gate.set_private(claim, on)`
  (anyone may make a claim private; only the person may make it shareable again, with their words)
  and `Gate.owner_death(person, words)` (R-38). `Gate.decide(task, verdict, words)` records the
  person's own answer on a task (the yes an adoptive link needs, R-31). Notes and the private switch
  never send a checked person back for a check. A name the person makes preferred shows first, even
  beside an older preferred name (`maxgen_db.shown_name`). Undo never withdraws the person's recorded
  decisions; a task an undo cancelled comes back with `Gate.reinstate(task, words, status=...)`.
- **Everything that leaves** goes through one filter, `maxgen_db.outgoing_view(ctx, kind, doc, purpose)`.
  `shareable` gives what `export --shareable` writes: never a person held back (`owner.hold_back`), nor anything
  naming them. Every piece of text in it (causes, values, notes, quotes, places, dates as written, citations,
  captions, nicknames; everything but codes, ids, hashes and times) has each name of a person who may not be named
  (living or possibly living, held back, removed, or merged into one of those) replaced by "(living relative)" or
  "(a relative)". Names are matched however they are written: invisible characters taken out, accents, strokes and
  look-alike letters of other alphabets folded, letters also read as commonly spelt out ("Mueller" is "Müller",
  "Oestergaard" is "Østergaard"), case ignored, plurals and possessives, halves joined or split by any dash or a
  space, a whole name run together as one word either way round ("WrenCanarywing"), letters spelt out one at a time,
  and a hidden person's first and last names with initials, middle names or "née" between, in brackets, surname
  first with a comma or in capitals, with a nickname in quotes, in a list of given names before one surname, or the
  first name with the surname's initial ("Wren Canarywing", "Wren E. Canarywing", "Canarywing, Wren", "CANARYWING
  Wren", 'Wren "Birdie" Canarywing', "Wren and Ada Canarywing", "Wren C.") even when each word alone is also a
  shareable person's. A name that is also an ordinary or a short word ("Grace",
  "An") counts only with a capital; a word a shareable person also bears is kept unless it is part of a hidden
  person's whole name, and words written as part of a longer whole name of a shareable person, in its order or surname
  first with a comma, name that person ("Mary Jane Pettibone", dead, beside a living Jane Pettibone; but "Mary, Jane
  Pettibone" is two people); text naming someone hidden whose whole name a shareable
  person shares is left out whole. `person_may_leave(ctx, doc)` answers whether a shareable copy of a person exists at
  all, without building it. A small place (a street, an address, a building, a farm, an estate, a plantation, or a
  place of no known kind) where someone who may be living has a fact never leaves, nor a small place holding one
  ("14 Quill Lane" holding the flat a living person lives in, through every place merged into each), nor one whose
  name is a run of whole words of a place they write or of their own small place's name ("40 Heron Road Apt 2").
  `health_report` gives the copy of one person a doctor may see (Probable Pedigree's "Print for a doctor"):
  nothing for anyone living or possibly living, held back, withdrawn or merged away; for everyone else no name and
  no id, years only, and only the causes of death and the health conditions. Their words are cut to the year, and
  printed only when every word is one the doctor's vocabulary knows (`DOCTOR_VOCABULARY`: everyday words and the
  names of illnesses, body parts and causes of death, and the short illness names it lists in capitals such as
  "COPD", so initials never pass; a disease called by people's names such as "Huntington's disease" or
  "Guillain-Barré syndrome", unless that name is also a living or hidden relative's). Words that tell of another
  person or people ("like her sister", "as her youngest has", "she", "they", "some of the family") are never in it,
  whatever their ending. They are left out whole ("(words left out: they may
  name someone)") when any word is unknown, is a living or hidden person's name in any letters, or is anyone
  else's name with a capital, or when they hold letters of another alphabet, or still give a day, a week, a
  season with its year, a feast, a month, a day or month as a number ("2019 6 5", "2019-6"), a day by its ordinal ("the
  fifth"), a part of a year ("early 2019", "the third quarter"), a time of day or a year of another calendar, or
  when a date finer than the year had no year to keep ("at Christmas"), or was placed by a word such as "before",
  "after", "into", "over" or "near" ("3 days before Christmas 2019" is not "before 2019"), or when a count of days,
  weeks, months, nights or hours stands beside a year ("6 months into 1999"; an age such as "aged 3 months, 1890"
  stays); an initial ("like C.") is never printed. A
  health condition is private everywhere else and never shared. A temple ordinance (an lds_* event, a marriage
  with ceremony "sealing", a sealing to parents, an event of type "sealing", in any letters or written as one in any
  of its words, its evidence quote or its notes such as "temple sealing", "endowed", "proxy baptism" or the short
  names "BIC", "SP", "SS" and GEDCOM's tags, "LDS" (also "L.D.S."), "Latter-day Saint", "Mormon", "covenant",
  "anointed", "stake", "eternal marriage", "posthumous baptism", "baptized after death", "post-mortem baptism", "baptized on behalf of", tags such as
  "BAPTISM_LDS" or "TEMPLE_SEALING" and joined words such as "TempleBaptism", "Slg"; in any claim, a name's or a birth's too), a record that documents one or
  whose words (its transcription, citation, collection, repository or kind as written) tell of one, a source about
  them, and anything taken from such a record never leave in a shared copy.
- **Merging and splitting people** (Probable Pedigree, rule R-28) also go through the Gate under the
  person's session with the owner key, each by the person's own yes in their own words, which the kit
  records on a task of the person's own before it changes anything: `Gate.merge_people(survivor,
  absorbed, words, choices)` (nothing is deleted; the absorbed id forwards to the survivor),
  `Gate.unmerge_person(absorbed, words)`, `Gate.mark_not_same(a, b, words)` and
  `Gate.distinct_clear(kind, a, b, words)` ("not the same person" and taking it back, K-11), and
  `Gate.split_person(person, move_assertion_ids, move_link_ids, words)`. Two sources the person says are
  the same are merged the same way, on a `resolve_conflict` task of their own: `Gate.merge_sources(survivor,
  absorbed, words, choices)` and `Gate.unmerge_source(absorbed, words)`. Places too (Probable Pedigree slice 21):
  `Gate.merge_places(survivor, absorbed, words)` and `Gate.unmerge_place(absorbed, words)`. Each is undone exactly by
  the Gate's own undo recipe.
- **Places the person adds or attaches** (Probable Pedigree slice 21, R-24): `Gate.place_task(words)` makes the
  person's own `build_tree` task for a new place; `Gate.resolve(kind, target, words, claims=..., written=...)`
  attaches a place or group to the claims named, or to everything that writes it exactly so (the `resolve` command's
  work, in chunks of 500 documents inside the one transaction). Only the link is added; the words never change. The
  whole batch is one undo step (`unresolve`), and redo applies it again (`reresolve`).
- **Stories and life summaries** (Probable Pedigree slice 23, MAXGEN 2.1 G-06, G-07, G-29): a `life_story` record
  is the person's own words, so its `source_classification.source` is `authored` (facts drawn from it stay at 0.4,
  R-11); one that names someone who may be living (a person mentioned as living, a linked person living now, a living
  name in its words in any letters, or a small place of theirs: `names_living_place`) is `tier2-private` with
  `is_living_flag` true (R-05); its
  `cited_record_ids` (the documents it draws on) must each be another record in the tree (R-04), and only a life
  story carries them. A `biography` note (`Gate.note(person, text, note_type="biography", cited_record_ids=...,
  ai_badge={"model_name": ...})`) is a short life summary: on a person only, never on someone who may be living
  (R-05, by any session's write). A session writes or changes a note (its words, kind, badge or task) only under
  a task of its own, and one that is not the person's changes or withdraws only notes it wrote itself, and the words
  of only the life stories it wrote itself (R-14). An AI agent writing one gives its `ai_badge`; Probable Pedigree treats any summary
  not written in the person's own session as an AI's, badge or not, and shows it only with its badge.

## Backups

The database is one file. Copy it. Before any batch, the Orchestrator copies `genealogy.db` to
`backups/genealogy_<date>_<batch>.db`. A bad batch is undone by restoring the copy.
