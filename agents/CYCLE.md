# CYCLE — the loop

One batch, start to finish. Then the next. The loop runs on **tasks**, not on days: a batch
ends when its tasks are done and checked, however long that takes.

```
    backup the database; start one helper session per role (session)
         │
    The Orchestrator plans the batch ── writes MaxTasks, all `pending`
         │
    The Genealogist works the tasks ──── MaxRecords, claims, links (link), MaxSources → staged
         │                    every search logged (step); tasks closed with a verdict
    Quality Control checks ─────────── opens every source; records, then persons, then links
         │                    staged → approved | rejected | NEEDS HUMAN
    The Orchestrator reports ────────── reports/batch_<NNN>.md + portal.html, from database counts
         │                    shows the person Quality Control's suggestions, one at a time,
         │                    and what waits for their decision on their own page
    The Orchestrator plans the next ─── from yield; retires what stopped producing
         │
         └──────────── repeat
```

At any moment, **the pedigree is**:

```
python maxgen_db.py list genealogy.db max_person --stage approved
python maxgen_db.py list genealogy.db max_relationship --stage approved
```

and the approved records they cite. Nothing else.

## Sizes

- **Batch 1: 50 tasks.** It proves the loop end to end before anything large runs.
- **Then 1,000 per batch**, or 2,000 if the person chose it.
- The Orchestrator checks for a stall every two days: no task has changed state in two days, or the same failure is
  repeating.
  Silence from the person is **not** a stall. The loop continues on the current rules.

## Reading the rules again

| When | What |
|---|---|
| every batch | `RULES.md`, in full |
| every 2,000 tasks | the schemas, in full |
| whenever a rules or schema file changes | that file, at once |

Check for change by comparing a fingerprint of each file (a hash — a short code computed
from the file's contents that changes if one character does). If it matches last batch, the
file did not change.

## Learning

- Quality Control proposes; the person approves; the Orchestrator records in `lessons.md`, with the batch number
  and task IDs that justify it.
- Two kinds of lesson: **PROVEN** (an angle that produces findings — use more of it) and
  **RETIRED** (an angle that produced nothing for two batches — do not try it again).
- A lesson nobody approved is not a lesson. It stays in `suggestions.md`. The one exception is a source or task type
  RETIRED by yield (ORCHESTRATOR, "Mix rule"), which the Orchestrator records itself and reports in the batch report.

## Stop rules

The loop ends, and the Orchestrator says which rule ended it, when:

- the goal is met;
- two batches in a row approve nothing — the sources are exhausted for this tree;
- the spending limit is reached;
- continuing would break a rule in `RULES.md`.

Ending for one of these reasons is a result. Say it plainly, with the numbers.
