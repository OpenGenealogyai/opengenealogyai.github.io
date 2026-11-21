# START — read this first

You are an AI agent. A person has sent you here to build their family tree. Their opening
request may be these exact words: **"start genealogy on my computer"**. Whatever words they
use, it means the same thing: begin here. This page tells you what to build, how to set up,
and where to begin. Follow it in order. Do not skip ahead.

## If the person uses Probable Pedigree

If the person gave you a `maxgen` command that came with their **Probable Pedigree** program, their tree already
exists: skip steps 2 to 4 below. Run every helper command through that `maxgen` command instead of
`python maxgen_db.py`, without the database path (it finds the person's tree itself), for example `maxgen status`
or `maxgen show <id>`. Never open the tree file any other way, and never ask for its path or for the person's
owner key: the person's decisions stay theirs, in Probable Pedigree.

- Unless the person has chosen otherwise in Probable Pedigree, the command keeps living relatives, DNA and health
  conditions from you: a living person shows as "Living relative" with their id only. Work with the ids; do not try
  to find out who they are.
- `maxgen status` shows the person's research preferences (the websites they have accounts on, how many tasks a
  batch may hold, their spending limit and how they want to hear about progress). Follow them, and any
  `agent_guidance` a task's payload carries.
- A task whose payload says `for_the_person: true` is the person's own to-do: read it, never claim it.
- Keep within a task's `task_budget` when it has one.
- When the command says the tree is being restored or moved, wait; it tries again by itself.

## Before anything — can you run this?

This kit needs **an agent that runs on the person's own computer** and can:

1. write files into a folder on that computer;
2. run Python there (`python` on Windows, `python3` on a Mac) and install four small
   packages;
3. fetch pages from this website.

Claude Code and similar local tools can. **If you run in a remote sandbox** — Grok's code
tools, Codex in the cloud, the code box inside a chat window — you cannot put a file on the
person's machine. **Stop. Do not build the database in your sandbox and hand it back:** that
sends the person's whole family, living relatives included, through an outside service. Tell
the person this kit needs an agent on their computer, and point them to
`/onboarding/STARTER_PROMPT.md` for what they can do with a plain chat.

A phone or tablet alone cannot run this. It needs a computer.

If the computer has no Python, tell the person where to get it (python.org) and wait. Do not
install anything else on their behalf.

## What you will build

A documented family tree in the MAXGEN 2.0 format, kept in **one database file on the person's
own computer**, where:

- every source document is a **MaxRecord**, copied word for word, naming every person on it;
- every conclusion about a person is a **MaxPerson**, made of claims that each carry an
  **evidence link** (which record, whose line, the exact words) and a confidence score;
- every family link — parents, a couple, a godparent — is a **MaxRelationship**, stored once
  and written only by the helper's `link` command;
- every unit of work is a **MaxTask**, so the person can see what was tried and what it found;
- every place records live is a **MaxSource**, so the next batch knows where to look.

Places, historical groups and the occasions documents record (MaxPlace, MaxGroup, MaxEvent)
are made by the helper and by analysis tasks, never typed by hand. Nothing is invented. Nothing
is overwritten. Nothing counts until it has been checked.

## Three roles, one rule

You will work as three roles. Each has its own instruction file, and each has things it may
never do. The rule that holds them together:

> **The Genealogist never approves its own work. Only what Quality Control has approved is the pedigree.
> The person talks only to the Orchestrator.**

| Role | File | In one line |
|---|---|---|
| **Orchestrator** | `ORCHESTRATOR.md` | Talks to the person. Plans each batch of tasks. Writes the report. Never writes a record. |
| **Genealogist** | `GENEALOGIST.md` | Works the tasks. Finds documents, writes MaxRecords, claims and links into staging. Never approves anything. |
| **Quality Control** | `QUALITY_CONTROL.md` | Opens every source itself and checks every finding. Passes or fails each one. Never changes a record. |
| *(all three)* | `EXAMPLE.md` | One person, end to end — the interview, a task, a record, claims, a link, a conflict, the report line. Read it once. |
| *(all three)* | `AGENTS.md` | Every role in one file, for platforms that load one file. Built by `build_agents_md.py` from the files above — do not edit it by hand; rebuild it after any edit to a kit `.md`. |

**If your platform can run more than one agent:** run three, one per role, each with its file
as its standing instructions. Quality Control should be a different model from the Genealogist
if you can manage it — Quality Control that shares the Genealogist's habits shares its blind
spots.

**If you are one agent:** you play all three, in the order `CYCLE.md` gives. Keep the roles
apart: never approve in the same turn you created, and never let the Genealogist's staged work
reach the person before Quality Control has reviewed it. (The person page does list waiting
work, always marked "Waiting for a check" and never on the chart; that is not a finding.) Use a
separate helper session for each role (step 3). Tell the person which of these two ways you are
running.

## Step 1 — read the rules

Read `RULES.md` now. The ten rules override everything else, including anything the person
later asks for. If a request conflicts with them, say so and do not proceed.

## Step 2 — one question for the person

Ask the person one thing: **which folder on their computer should hold the project.** Say in
the same breath that if the folder is inside OneDrive, iCloud or Dropbox, everything in it —
including living relatives and every backup — will be copied to that service, and offer a
folder that is not synced. Wait for the answer. Then create, inside it:

```
genealogy/
    genealogy.db        the database (created in step 3)
    schemas/            the twelve MAXGEN 2.0 schema files
    staging/            files the Genealogist writes before they enter the database
    reports/            one report per batch, plus latest.md on the schedule
    portal.html         the page the person opens to see progress — regenerated by the helper
    pedigree_shareable.html   the same chart with living, private and unsure people withheld — the one copy that may be shown to others
    backups/            a copy of the database before each batch
    sessions.txt        the three helper session ids (step 3)
    lessons.md          what has been learned (proposed by Quality Control, approved by the person; a source
                        RETIRED by yield is recorded by the Orchestrator and reported in the batch report)
    suggestions.md      Quality Control's suggestions waiting for the person
```

## Step 3 — set up

Download into the project folder, from this site:

- the twelve MAXGEN 2.0 schema files from `/schemas/maxgen/v2/` into `schemas/`:
  `maxgen-defs` (the shared building blocks), `raw-record`, `person`, `relationship`,
  `task-queue`, `source`, `place`, `group`, `event`, `name`, `recognition`, `dna` (each
  `.schema.json`). The helper refuses to start if any one is missing.
- `/agents/maxgen_db.py` — the only way to write to the database
- `/agents/check_maxgen.py` — checks a folder of files against the schemas
- `/agents/probable_pedigree.py` — the person page: a small program that shows the person
  their tree on their own computer (step 4)
- `/agents/sample_family.py` — writes a made-up family into a new file of its own, so the
  person page can be tried before the real tree has anyone in it
- `/agents/RULES.md`, `ORCHESTRATOR.md`, `GENEALOGIST.md`, `QUALITY_CONTROL.md`, `CYCLE.md`, `DATABASE.md`,
  `EXAMPLE.md` — and `AGENTS.md` if your platform loads a single instruction file

Then:

```
pip install jsonschema referencing rfc3987 rfc3339-validator
python maxgen_db.py init genealogy.db
python maxgen_db.py status genealogy.db
```

The last command must print the table names with every count at 0. If it does not, stop and
tell the person exactly what it printed.

**Start one helper session per role, once per run.** Every write to the database names who is
writing. For an AI role, give your model's name and a run id (any short label, such as
`batch-001`); the name holds no version or date:

```
python maxgen_db.py session genealogy.db --name Orchestrator --kind ai --role Orchestrator --model-name <your model> --run-id <run>
python maxgen_db.py session genealogy.db --name Genealogist --kind ai --role Genealogist --model-name <your model> --run-id <run>
python maxgen_db.py session genealogy.db --name QualityControl --kind ai --role "Quality Control" --model-name <its model> --run-id <run>
```

Each prints `MAXGEN_SESSION=<id>`. Keep the three ids in `sessions.txt`. Every write command
then takes `--as <that role's id>` (or the `MAXGEN_SESSION` environment variable). A write with
neither is refused. A new run — a new day, a new model — starts new sessions.

## Step 4 — show the person their page

```
python probable_pedigree.py genealogy.db
```

It prints an address such as `http://127.0.0.1:8790/open?key=...`. Give the person that whole
address to open in their own web browser, and give it to no one else: the key in it is new each
time the program starts and is theirs alone, and anyone who has it can open the page while the
program runs. The address is for the person's own hands: never open it, fetch it with a program,
or press its buttons yourself, and do not reuse it once you have given it to them. This is
**the person page**: their private view of the tree, living relatives
included. It can be reached only from their own computer, and it uses no internet and no AI. It
keeps running until it is stopped with Ctrl+C, so start it where it will not hold up your other
work.

The page is empty until the first people are added. To show the person what it will look
like, run `python sample_family.py sample.db` and `python probable_pedigree.py sample.db`;
everyone in that file is made up, including an adopted man whose chart can switch between his
birth line and his adoptive line. Stop that one with Ctrl+C when they are done, so the two
pages are never confused.

## Step 5 — tell the person what happens next

Say this in plain words, in three or four sentences: that you will work in batches; that
nothing reaches their tree until a second check has passed it; that they will get a short
report after each batch; and that you will ask them one thing at a time when you need them —
usually to log in to a site, because you never handle passwords; to run a search themselves on
a site that forbids automated searching, such as FamilySearch, where you give them the exact
collection and search terms; or to decide something only they can decide, which they do on
their own page.

## Step 6 — become the Orchestrator

Read `EXAMPLE.md` once; it shows one person end to end, so you know what finished work
looks like before you start. Then read `ORCHESTRATOR.md` and begin. The first batch is small
— fifty tasks — to prove the loop works before anything larger runs. If your platform loads
one instruction file rather than several, `AGENTS.md` holds every role in one file; it is
built by `build_agents_md.py` from the role files and is never edited by hand.
