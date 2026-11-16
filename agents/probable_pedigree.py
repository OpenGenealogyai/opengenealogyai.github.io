#!/usr/bin/env python3
"""probable_pedigree.py -- the Probable Pedigree person page, on this computer only (MAXGEN 2.0).

    python probable_pedigree.py genealogy.db [--port 8790] [--schemas <folder>]

It reads the same genealogy.db the agents write (through maxgen_db.py) and shows one person at a
time: the family chart (which can follow the main parents, the birth line, the adoptive line or
every parent set), what the documents say about them, their parent sets, partners and children,
the research done and waiting, the places and groups their facts point at, and a few ways to add
to it. It prints an address that holds this run's key; open it in your own web browser, and give
it to no one else.

Safety, in six lines:
  1. It listens on 127.0.0.1 only and answers only to 127.0.0.1:<port> or localhost:<port>.
     It also holds [::1]:<port> without answering there, so no other program can.
  2. Every data page and every write needs this run's key. The printed address carries it once;
     the browser then keeps it as a cookie (HttpOnly, SameSite=Strict) until the program stops.
  3. No internet, no AI, no outside files: the page, its style and its script come from here.
  4. Reading never writes: every page comes from a read-only connection.
  5. Writing only adds, through maxgen_db.py's own functions and gates. Nothing already there is
     changed or deleted; what the person types is kept as their own words, capped at 0.4.
  6. Every write also needs this run's token (issued inside the page) and a same-origin request.
"""
# Copyright (c) 2026 OpenGenealogyAI Contributors. MIT License; see LICENSE.
__version__ = "0.3.0"

import argparse
import contextlib
import errno
import hmac
import io
import json
import re
import secrets
import socket
import sqlite3
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import maxgen_db  # noqa: E402

esc = maxgen_db.esc
low = maxgen_db.low

DEFAULT_PORT = 8790        # 8771 and 8780 belong to other tools on this computer
PORT_TRIES = 10
MAX_BODY = 16384           # bytes in one write request
PERSON_CLAIM_CAP = 0.4     # RULES.md "Confidence": what a person says is capped until a document is found
MAX_REQUEST = 2000
MAX_NAME = 200
MAX_LINK = 2000
MAX_NOTE = 2000
MAX_PICKS = 20             # person:parent-set pairs one chart address may carry
# \A and \Z, not ^ and $: "$" also matches just before a final newline.
ID_RE = re.compile(r"\A[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\Z")
TAB_KEY_RE = re.compile(r"\A[a-z][a-z0-9_]{0,30}\Z")
ANCHOR_RE = re.compile(r"\A[a-z][a-z0-9-]{0,80}\Z")
MARK_RE = re.compile(r"\A[a-z][a-z_]{0,30}\Z")
KIT_TABLES = ("meta", "max_person", "max_record", "max_task", "max_relationship", "claim_index", "membership_index")
LINEAGES = ("preferred", "birth", "adoptive", "all")
PROGRAM = "probable_pedigree.py %s" % __version__
WRITE_LOCK = threading.RLock()

# ------------------------------------------------------------------ words on screen (HUB_A_DESIGN section 9)
# A schema word may appear only in a tooltip. Everything visible uses these words.

WORDS = {
    "stage": {
        "staged": ("Waiting for a check", "Quality Control has not looked at it yet."),
        "approved": ("Checked", "Quality Control opened the source and passed it."),
        "rejected": ("Turned down", "Quality Control failed it. It is kept, with the reason."),
    },
    "status": maxgen_db.PLAIN_WORDS["status"],
    "verdict": maxgen_db.PLAIN_WORDS["verdict"],
    "answer": {"accepted": "You said yes", "rejected": "You said no", "rework": "You asked for more work"},
    "task_kind": maxgen_db.PLAIN_WORDS["task_kind"],
    "other_kind": {"person_request": "Your request", "interview": "Your first interview",
                   "person_page_session": "What you added on this page",
                   "main_parents_changed": "Please confirm who should be shown as the main parents",
                   "person_answer": "Your answer"},
    "licence": {"CC0": "Anyone may reuse it, no conditions",
                "CC-BY": "Anyone may reuse it if they credit %s",
                "CC-BY-SA": "Anyone may reuse it if they credit %s and share it the same way",
                "public-domain": "No copyright left; anyone may reuse it",
                "all_rights_reserved": "Its owner keeps every right; kept here for your own research, not for sharing",
                "tier2-private": ("Private: this document stays on this computer, and nothing taken from it "
                                  "shows on the chart you share")},
    "record": maxgen_db.PLAIN_WORDS["record"],
    "told_us": "What you told us",
    "source": {"original": "The original, or a picture of it",
               "derivative": "A copy or index made from the original",
               "authored": "Someone's own account or conclusions",
               "undetermined": "Not yet looked at closely"},
    "information": {"primary": "Written by someone who was there",
                    "secondary": "Written by someone who heard it, or long afterwards",
                    "undetermined": "Not known who gave the information"},
    "role": {"father": "Father", "mother": "Mother", "parent": "Parent", "unknown": "Parent", "other": "Parent"},
    "lineage": {"birth_parent": "birth parent", "adoptive_parent": "adoptive parent", "step_parent": "step-parent",
                "foster_parent": "foster parent", "guardian": "guardian", "social_parent": "raised them",
                "donor_parent": "donor parent", "gestational_carrier": "carried the baby",
                "sealing": "sealed in a church ordinance", "related": "related, kind not stated",
                "unknown": "kind of parent not stated", "entering_heir": "heir (coming in)",
                "exiting_heir": "heir (going out)", "other": "another kind of parent"},
    "dimension": {"genetic": "by DNA", "gestational": "by carrying the baby", "legal": "by law",
                  "social": "by upbringing", "religious": "by a church ordinance", "household": "in the household"},
    "child_kind": {"birth": "child", "adoptive": "adopted child", "step_household": "step-child",
                   "foster": "foster child", "guardianship": "ward", "sealing": "sealed child",
                   "alternative_hypothesis": "possible child, not settled", "other": "child, kind not stated"},
    "view": {"preferred": "Main parents", "birth": "Birth lineage", "adoptive": "Adoptive lineage",
             "all": "All parent sets"},
    "how_known": {"saw_original": "I saw the original document",
                  "saw_image_online": "I saw a picture of it online",
                  "online_tree_or_index": "An online family tree or index",
                  "family_story": "A family story or my own memory",
                  "other": "Something else (say what in the note)"},
    "fact": {"name": "Name", "birth_year": "Year of birth", "death_year": "Year of death"},
    "fact_lower": {"name": "name", "birth_year": "year of birth", "death_year": "year of death"},
    "claim_fact": {"name": "Name", "birth": "Year of birth", "death": "Year of death", "gender": "Sex or gender",
                   "event": "Event", "attribute": "Lasting fact", "non_event": "Did not happen",
                   "membership": "Group", "photo": "Photograph", "external_id": "Elsewhere"},
    "quote": {"matched": "The quoted words were found on this person's line",
              "matched_whole_text": "The quoted words were found in the document",
              "outside_line": "The quoted words were found elsewhere on the page",
              "not_found": "The quoted words were not found; Quality Control will look",
              "too_short": "The quote is too short to check",
              "not_checkable": "There is no word-for-word copy to check the quote against",
              "stale": "The document changed since; Quality Control will look again"},
    "event": {"birth": "Birth", "baptism": "Baptism", "christening": "Christening", "death": "Death",
              "burial": "Burial", "cremation": "Cremation", "census": "Census", "residence": "Lived at",
              "emigration": "Left the country", "immigration": "Arrived in the country", "adoption": "Adoption",
              "marriage": "Marriage", "military_service": "Military service", "naturalization": "Became a citizen",
              "will": "Will", "probate": "Probate", "occupation": "Occupation"},
    "event_role": {"godparent": "godparent", "sponsor": "sponsor", "witness": "witness", "informant": "informant",
                   "officiant": "led the ceremony", "clergy": "clergy", "passenger": "passenger",
                   "captain": "master", "head": "head of the household", "child": "child", "spouse": "husband or wife",
                   "father": "father", "mother": "mother", "deceased": "the person who died",
                   "principal": "the person it is about"},
    "attribute": {"occupation": "Occupation", "residence": "Lived at", "religion": "Religion",
                  "nationality": "Nationality", "ethnicity": "Ethnicity", "title": "Title",
                  "physical_description": "Looks", "id_number": "Number on a document", "education": "Education",
                  "property": "Property", "military_rank": "Military rank", "caste": "Caste", "other": "Other"},
    "family_fact": {"marriage": "Married", "marriage_banns": "Banns read", "marriage_license": "Marriage licence",
                    "marriage_contract": "Marriage contract", "marriage_notice": "Marriage notice",
                    "marriage_settlement": "Marriage settlement", "engagement": "Engaged", "separation": "Separated",
                    "divorce": "Divorced", "divorce_filing": "Divorce filed", "annulment": "Annulled",
                    "civil_union": "Civil union", "common_law_marriage": "Lived as married",
                    "domestic_partnership": "Domestic partnership", "number_of_children": "Number of children",
                    "census": "In one household in a census", "residence": "Lived together",
                    "existence": "Shown as a couple", "other": "Other"},
    "group_fact": {"organized": "Formed", "departed": "Departed", "arrived": "Arrived", "mustered": "Mustered",
                   "disbanded": "Disbanded", "residence": "Lived at", "existence": "Shown to exist",
                   "other": "Other"},
    "group_kind": {"emigrant_company": "Emigrant company", "handcart_company": "Handcart company",
                   "wagon_train": "Wagon train", "ship_company": "Ship's company", "regiment": "Regiment",
                   "military_unit": "Military unit", "congregation": "Congregation", "ward": "Ward",
                   "institution": "Institution", "school_class": "School class", "settlement": "Settlement",
                   "enslaved_community": "Enslaved community", "household": "Household", "work_crew": "Work crew",
                   "society": "Society", "other": "Other group"},
    "tie": {"godparent": ("Godparent of %s", "%s was their godparent"),
            "enslaved_by": ("Enslaved by %s", "Held %s enslaved"),
            "ancestor_descendant": ("An ancestor of %s", "%s is an ancestor of theirs"),
            "employer": ("Employer of %s", "Worked for %s"),
            "neighbour": ("Neighbour of %s", "Neighbour of %s"), "friend": ("Friend of %s", "Friend of %s"),
            "multiple_birth": ("Born at the same birth as %s", "Born at the same birth as %s"),
            "other": ("Another tie with %s", "Another tie with %s")},
    "number": {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine",
               10: "Ten"},
}
BAND_LABEL = {cls: label for _t, cls, label in maxgen_db.CONF_BANDS}
BAND_LABEL["none"] = "No score"
FACT_LIST = {"name": "name_assertions", "birth_year": "birth_assertions", "death_year": "death_assertions"}
FACT_OF_LIST = {"name_assertions": "name", "birth_assertions": "birth", "death_assertions": "death",
                "gender_assertions": "gender", "event_assertions": "event", "attribute_assertions": "attribute",
                "non_event_assertions": "non_event", "membership_assertions": "membership",
                "photo_assertions": "photo", "external_id_assertions": "external_id"}

BANNER = ('<p class="banner private" id="living-banner">Private: this page shows living relatives. '
          "It is for you only. Do not share it, print it for others, or send a screenshot.</p>")
FORBIDDEN = ("This page is out of date, or it did not come from Probable Pedigree. "
             "Reload the page and try again.")
NO_PERSON = "No one with that id is in this file."
TOO_LONG = "That is too long to save. Please shorten it."
TURNED_DOWN_ADD = ("Quality Control turned this person down, so nothing can be added to them. "
                   "You can still ask the Genealogist.")
HOST_ONLY = "Probable Pedigree only answers at the address it printed when it started."
NOT_STORABLE = "That did not pass the database's checks, so nothing was saved. Please check what you typed."
REFUSED = "Nothing was saved: the database's checks refused it."
NO_KEY_TITLE = "Open the address from the window"
NO_KEY = ("Open the address Probable Pedigree printed in its window when it started. It holds a key that "
          "lets this browser in. If you closed the browser since then, open that address again.")
NEEDS_HUMAN = "NEEDS HUMAN:"
GONE_SET = "That parent set can no longer be chosen. Reload the page to see the parent sets as they are now."
GONE_DECISION = "That decision is no longer waiting for you. Reload the page to see what is waiting now."
SAVED = {
    "fact": ("Saved as 'added by you'. Quality Control will check %s again. Until then they show as "
             "waiting for a check, and they and everyone above them in the family are off the chart."),
    "ask": ("Sent. Your request is on the Research list. The Orchestrator (the agent you talk to) reads it at "
            "the start of the next batch and plans the research, or tells you why not."),
    "prefer": "Saved. The chart now shows the parents you chose as %s's main parents.",
    "decision": ("Saved. Your answer is kept with the decision. The Genealogist and Quality Control read it "
                 "in the next batch."),
}


# ------------------------------------------------------------------ small helpers

def _int(v):
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _conf(c) -> float:
    if not isinstance(c, dict):
        return 0.0
    return _num(c.get("confidence")) or 0.0


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _list(v) -> list:
    return v if isinstance(v, list) else []


def _text(v) -> str:
    return "" if v is None else str(v)


def _first_upper(s: str) -> str:
    return s[:1].upper() + s[1:]


def plain(v) -> str:
    """A schema word as plain words, for when no better wording is known."""
    return _text(v).replace("_", " ")


def local_day(ts) -> str:
    """The day a stored time falls on here, as YYYY-MM-DD. The kit stores UTC, so late in the
    evening the stored day can already be tomorrow. Text that is not a time with a zone is shown
    as stored (its first ten characters)."""
    s = _text(ts)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:10]
    if dt.tzinfo is None:
        return s[:10]
    return dt.astimezone().date().isoformat()


def needs_human(note) -> bool:
    """A Quality Control note that hands the decision to the person (any letter case)."""
    return _text(note).strip()[:len(NEEDS_HUMAN)].upper() == NEEDS_HUMAN


def qc_note_html(note) -> str:
    """A Quality Control note in plain words. A NEEDS HUMAN note says the person must decide;
    any other note was written for the Genealogist. The note as written stays in the tooltip."""
    raw = _text(note).strip()
    if needs_human(raw):
        rest = raw[len(NEEDS_HUMAN):].strip() or "no reason was written"
        return ('<span class="qc-note needs-you" title="%s">Quality Control needs your decision on this. '
                'Tell the Orchestrator what you think: "%s".</span>' % (esc(raw), esc(rest)))
    return '<span class="qc-note" title="%s">%s</span>' % (esc(raw), esc(raw or "no reason was written"))


def stage_words(stage) -> str:
    return WORDS["stage"].get(stage, (_text(stage) or "Unknown", ""))[0]


def stage_badge(stage) -> str:
    return ('<span class="badge stage-%s" title="%s">%s</span>'
            % (esc(stage), esc(WORDS["stage"].get(stage, ("", ""))[1]), esc(stage_words(stage))))


def norm_name(name) -> str:
    """Names compare as the same after: lower case, 'Surname, Given' read as 'Given Surname',
    '.' and ',' as spaces, and spaces collapsed."""
    s = _text(name).strip().lower()
    if "," in s:
        surname, given = s.split(",", 1)
        s = given + " " + surname
    return " ".join(s.replace(".", " ").replace(",", " ").split())


def fmt_date(d) -> str:
    """A MAXGEN 2.0 date block in plain words ('4 Jun 1921', 'between 1920 and 1921 (worked out)')."""
    return maxgen_db.date_display(d) if isinstance(d, dict) else ""


def chip(conf, capped=False, where_from="") -> str:
    """A confidence chip: a word and a colour, the number only in the tooltip."""
    c = _num(conf)
    band = maxgen_db.conf_band(c)
    if c is None:
        tip = "No confidence number was recorded."
    else:
        tip = "%.2f out of 1: how clearly the document says this. It is not proof." % c
        if capped:
            tip += (" Capped at %.1f: it rests on %s, and it never stands alone until a document is found."
                    % (PERSON_CLAIM_CAP, where_from or "a source that is not a document from the time"))
    return '<span class="chip b-%s" title="%s">%s</span>' % (band, esc(tip), esc(BAND_LABEL.get(band, "No score")))


def doc_anchor(rid) -> str:
    rid = _text(rid)
    return ("doc-" + rid.lower()) if ID_RE.match(rid) else ""


def doc_link(rid, stage=None) -> str:
    a = doc_anchor(rid)
    if not a:
        return ""
    out = '<a href="#%s">see the document</a>' % a
    if stage == "staged":
        out += ' <span class="muted">(its document is waiting for a check)</span>'
    elif stage == "rejected":
        out += ' <span class="muted">(Quality Control turned its document down)</span>'
    elif stage is None:
        out += ' <span class="muted">(its document is not in this file)</span>'
    return out


def person_href(pid, gens=None, lineage=None, picks=None) -> str:
    pid = _text(pid)
    if not ID_RE.match(pid):
        return ""
    q = []
    if gens:
        q.append("gens=%d" % gens)
    if lineage and lineage != "preferred":
        q.append("lineage=" + lineage)
    if picks:
        q.append("pick=" + quote(pick_text(picks), safe=":,"))
    return "/person/" + pid + (("?" + "&".join(q)) if q else "")


def pick_text(picks: dict) -> str:
    return ",".join("%s:%s" % (k, v) for k, v in sorted(picks.items()))


def parse_picks(raw) -> dict:
    """`pick`: comma-separated person_id:membership_id pairs, at most MAX_PICKS, each part an id.
    Anything else is ignored (a malformed pair is dropped; too many pairs drop the whole list)."""
    out = {}
    parts = [p for p in _text(raw).split(",") if p.strip()]
    if not parts or len(parts) > MAX_PICKS:
        return out
    for part in parts:
        bits = part.strip().split(":")
        if len(bits) == 2 and ID_RE.match(bits[0]) and ID_RE.match(bits[1]):
            out[bits[0].lower()] = bits[1].lower()
    return out


# ------------------------------------------------------------------ reading (read-only, always)

def ro_connect(db) -> sqlite3.Connection:
    """A read-only connection. Never maxgen_db.connect, which opens read-write."""
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
    except Exception:
        con.close()
        raise
    return con


class ReadCtx(maxgen_db.Ctx):
    """maxgen_db's own command context, over a read-only connection: no schemas, no session and no
    writes. Being a Ctx, every maxgen_db reader (parent_sets, chart_persons, living_now ...) takes
    it as it is."""

    def __init__(self, con):     # deliberately not Ctx.__init__: that opens the file read-write
        self.db, self.schemas, self.con = None, None, con
        self.write, self.as_session, self.session, self.batch, self.quiet = False, None, None, None, True
        self.today = maxgen_db.today()
        self.today_day = maxgen_db.jdn_gregorian(self.today.year, self.today.month, self.today.day)
        self._living, self._warned, self.warnings = {}, set(), []
        self.settings = maxgen_db.date_settings(con)

    def commit(self):
        raise sqlite3.OperationalError("a page never writes")


class Reader:
    """One page's read-only view: maxgen_db's context over a read-only connection, plus small caches."""

    def __init__(self, con):
        self.con = con
        self.rc = ReadCtx(con)
        self.cache = {}

    def doc(self, kind, ident):
        key = (kind, low(ident))
        if key not in self.cache:
            self.cache[key] = self.rc.doc(kind, self.rc.resolve(ident)) if ident else None
        return self.cache[key]

    def row(self, kind, ident):
        return self.rc.row(kind, self.rc.resolve(ident)) if ident else None

    def living(self, pid) -> bool:
        return maxgen_db.person_living_now(self.rc, pid)

    def name(self, pid) -> str:
        key = ("name", low(pid))
        if key not in self.cache:
            row = self.row("person", pid)
            self.cache[key] = maxgen_db.person_display(self.rc, row["person_id"])[0] if row else "(not in this file)"
        return self.cache[key]

    def stage(self, kind, ident):
        row = self.row(kind, ident)
        return row["stage"] if row and "stage" in row.keys() else None

    def task(self, tid):
        return self.doc("task", tid) if tid else None

    def record(self, rid) -> dict:
        """What a page needs to know about one record: stage, kind in words, where it came from."""
        rid = low(rid)
        key = ("record", rid)
        if key in self.cache:
            return self.cache[key]
        row = self.row("record", rid) if rid else None
        if not row:
            info = {"id": rid, "stage": None, "kind": "", "byyou": False, "interview": False, "source": None,
                    "type": None, "doc": None}
        else:
            doc = json.loads(row["doc"])
            told = _told_us(doc)
            interview = (_dict(_dict(self.task(doc.get("created_by_task_id"))).get("payload")).get("kind")
                         == "interview")
            if told:
                kind = WORDS["told_us"]
            elif interview:
                kind = WORDS["other_kind"]["interview"]
            elif doc.get("record_type") == "other" and _text(doc.get("record_type_as_written")).strip():
                kind = _first_upper(_text(doc["record_type_as_written"]).strip())
            else:
                kind = WORDS["record"].get(doc.get("record_type"), "Other document")
            info = {"id": row["record_id"], "stage": row["stage"], "kind": kind, "byyou": told,
                    "interview": interview, "source": _dict(doc.get("source_classification")).get("source"),
                    "type": doc.get("record_type"), "doc": doc}
        self.cache[key] = info
        return info


def _told_us(doc) -> bool:
    return _dict(doc).get("record_type") == "other" and "person_page" in _dict(_dict(doc).get("extensions"))


def load_person(r: Reader, pid):
    """The person row (following merge redirects), or None."""
    if not isinstance(pid, str) or not ID_RE.match(pid):
        return None
    row = r.row("person", pid)
    if not row:
        return None
    out = dict(row)
    out["doc"] = json.loads(row["doc"])
    out["merged_from"] = pid.lower() if row["person_id"] != pid.lower() else None
    return out


def origin(r: Reader, c: dict):
    """(words, capped, by-you) for where a claim came from (spec 4.5: label by where it came from)."""
    ev = _dict(c.get("evidence"))
    basis = ev.get("basis")
    capped = maxgen_db.capped(r.rc, ev, c.get("_list"))
    if basis == "imported":
        return "From an imported file", capped, False
    if basis == "dna":
        return "From a DNA test", capped, False
    if basis == "conclusion":
        return "From a proof", capped, False
    if basis == "search":
        return "From a search that found nothing", capped, False
    info = r.record(ev.get("source_record_id"))
    if info["byyou"]:
        return "added by you", capped, True
    if info["interview"]:
        return "from your first interview", capped, False
    if info["source"] == "authored":
        if info["type"] == "gazetteer_entry":
            return "From a gazetteer", capped, False
        if info["type"] in ("oral_history", "letter"):
            return "From the family's account", capped, False
        return "From a family tree or a compiled work", capped, False
    if info["source"] == "undetermined":
        return "From a citation nobody has examined yet", capped, False
    role = _dict(_dict(r.task(c.get("task_id"))).get("provenance")).get("role")
    if role in (None, "Genealogist"):
        return "found by the Genealogist", capped, False
    return "added by the %s" % plain(role), capped, False


def origin_html(r: Reader, c: dict) -> str:
    words, _capped, mine = origin(r, c)
    return '<strong class="byyou">added by you</strong>' if mine else esc(words)


def chip_for(r: Reader, c: dict) -> str:
    words, capped, _mine = origin(r, c)
    return chip(c.get("confidence"), capped, words.lower())


def quote_words(c: dict) -> str:
    qc = _dict(_dict(c.get("evidence")).get("quote_check"))
    return WORDS["quote"].get(qc.get("status"), "")


def evidence_html(r: Reader, c: dict) -> str:
    """The document link and the quote check, in words."""
    ev = _dict(c.get("evidence"))
    rid = ev.get("source_record_id") or ev.get("import_record_id")
    out = doc_link(rid, r.record(rid)["stage"]) if rid else ""
    q = quote_words(c)
    if q:
        out += ('<span class="qc-words" title="%s">%s</span>'
                % (esc(ev.get("quote") or ""), esc(q)))
    return out


def place_words(r: Reader, p) -> str:
    p = _dict(p)
    words = _text(p.get("place_as_written") or p.get("address_as_written")).strip()
    if not words and p.get("place_id"):
        doc = r.doc("place", p["place_id"])
        words = _text(maxgen_db.best_claim_value(_dict(doc).get("name_assertions"), "name_as_written"))
    return words


def when_where(r: Reader, c) -> str:
    c = _dict(c)
    out = fmt_date(c.get("date"))
    where = place_words(r, c.get("place"))
    return (out + " · " + where) if (out and where) else (out or where)


def claims_of(r: Reader, pid) -> list:
    """Every claim whose holder is this person (merged-away documents count as theirs), live and
    withdrawn, one entry each."""
    out = []
    for ln, c, holder in maxgen_db.effective_claims(r.rc, "person", pid, live_only=False):
        c = dict(c)
        c["_list"] = ln
        ev = _dict(c.get("evidence"))
        rid = low(ev.get("source_record_id") or ev.get("import_record_id"))
        info = r.record(rid) if rid else {"stage": None, "kind": ""}
        out.append({"fact": FACT_OF_LIST.get(ln, ln[:-len("_assertions")]), "list": ln, "claim": c, "rid": rid,
                    "doc_stage": info["stage"], "doc_kind": info["kind"], "live": maxgen_db.live_claim(c),
                    "holder": holder.get("person_id")})
    out.sort(key=lambda e: _text(e["claim"].get("asserted_at")))
    return out


def best(items):
    """The highest-confidence live claim (earliest wins a tie): maxgen_db's own rule."""
    return maxgen_db._best(items)


def best_name_claim(entries):
    names = [e["claim"] for e in entries if e["fact"] == "name" and e["live"]
             and e["claim"].get("name_status", "known") == "known" and e["claim"].get("name_as_written")]
    if not names:
        return None
    pref = [n for n in names if n.get("is_preferred")]
    return max(pref or names, key=_conf)


def span(entry):
    """The years a dated claim states: (year, year), or (first, last) for between / from-to / or.
    None when it states no year."""
    d = _dict(entry["claim"].get("date"))
    if not isinstance(d.get("value"), dict):
        return None
    lo = maxgen_db.date_year(d)
    if lo is None:
        return None
    hi = lo
    if isinstance(d.get("value2"), dict) and d.get("modifier") in ("between", "from_to", "or"):
        y2 = maxgen_db.date_year({"value": d["value2"]})
        if y2 is not None:
            hi = y2
    return (min(lo, hi), max(lo, hi))


def conflicts(entries: list) -> list:
    """[(label, [entries])] when claims state years that cannot all be true together. A claim joins
    the first candidate whose every claim overlaps it (strongest first); candidates are ordered by
    their best confidence. [] when nothing conflicts."""
    groups = []
    for e in sorted(entries, key=lambda x: -_conf(x["claim"])):
        s = span(e)
        if s is None:
            continue
        for g in groups:
            if all(s[0] <= t[1] and t[0] <= s[1] for t, _x in g):
                g.append((s, e))
                break
        else:
            groups.append([(s, e)])
    if len(groups) < 2:
        return []
    out = []
    for g in groups:
        lo, hi = min(t[0] for t, _x in g), max(t[1] for t, _x in g)
        out.append(("%d" % lo if lo == hi else "%d–%d" % (lo, hi), [x for _t, x in g]))
    return out


def tasks_for(r: Reader, pid: str) -> list:
    """Every task whose payload.person_id is this person (letter case ignored), matched in Python."""
    want = low(pid)
    out = []
    for row in r.con.execute("SELECT task_id, task_type, status, priority, verdict, doc, added_at, updated_at "
                             "FROM max_task WHERE doc LIKE ?", ("%" + want + "%",)):
        try:
            doc = json.loads(row["doc"])
        except ValueError:
            continue
        if low(_text(_dict(doc.get("payload")).get("person_id"))) != want:
            continue
        out.append({"row": dict(row), "doc": doc})
    return out


def task_counts(tasks: list) -> dict:
    """The one count both the summary and the Research tab use."""
    by = {s: 0 for s in maxgen_db.TASK_STATUS}
    verdicts: dict = {}
    for t in tasks:
        s = t["row"]["status"]
        by[s] = by.get(s, 0) + 1
        if s == "done" and t["row"]["verdict"]:
            verdicts[t["row"]["verdict"]] = verdicts.get(t["row"]["verdict"], 0) + 1
    return {"waiting": by.get("pending", 0) + by.get("processing", 0), "needs_you": by.get("escalated", 0),
            "done": by.get("done", 0), "failed": by.get("failed", 0), "paused": by.get("on_hold", 0),
            "cancelled": by.get("cancelled", 0), "verdicts": verdicts}


def counts_line(c: dict) -> str:
    n = c["needs_you"]
    return "%d waiting · %d %s you" % (c["waiting"], n, "needs" if n == 1 else "need")


def dead_ends_for(r: Reader, pid: str) -> list:
    return [dict(x) for x in r.con.execute(
        "SELECT source, query, note, at, verdict FROM dead_end WHERE lower(person_id) = lower(?) "
        "ORDER BY at DESC, task_id, step_no DESC", (pid,))]


def latest_review(r: Reader, target_id: str, table: str):
    row = r.con.execute("SELECT verdict, by, note, at FROM review WHERE lower(target_id)=lower(?) AND table_name=? "
                        "ORDER BY id DESC LIMIT 1", (target_id, table)).fetchone()
    return dict(row) if row else None


def person_reviews(r: Reader, task_id) -> list:
    """The person's own answers about one task, oldest first."""
    return [t for t in maxgen_db.review_tasks(r.rc, task_id)
            if maxgen_db.is_person_review(t, r.rc.meta("database_id"))]


# ------------------------------------------------------------------ the tab registry (Viewer A)

@dataclass
class PersonCtx:
    """What a tab's row function may read about the person on the page."""
    con: sqlite3.Connection
    pid: str
    doc: dict
    stage: str
    name: str
    claims: list
    cited: set
    names: set = field(default_factory=set)
    memo: dict = field(default_factory=dict)
    reader: "Reader | None" = None

    def person_name(self, ident) -> str:
        return self.reader.name(ident) if self.reader else "(not in this file)"

    def tasks(self) -> list:
        if "tasks" not in self.memo:
            self.memo["tasks"] = tasks_for(self.reader, self.pid)
        return self.memo["tasks"]


@dataclass(frozen=True)
class Tab:
    key: str                                   # ^[a-z][a-z0-9_]{0,30}$, unique; used in ids and ?tab=
    label: str                                 # plain words on the tab button
    schema: str                                # the schema behind it; shown only as the tab's tooltip
    table: str                                 # the genealogy.db table it reads
    rows: Callable[["PersonCtx"], list]        # how rows are filtered to this person
    columns: tuple                             # ((row cell key, plain heading), ...)
    empty: str                                 # the sentence shown when there are no rows
    intro: Callable[["PersonCtx", list], str] | None = None   # optional one-line summary


def claim_line(r: Reader, c: dict, fact: str, rel=None) -> str:
    """One claim as one line of plain words (used in the document details)."""
    label = WORDS["claim_fact"].get(fact, _first_upper(plain(fact)))
    if fact == "name":
        value = _text(c.get("name_as_written"))
    elif fact in ("birth", "death"):
        value = when_where(r, c)
    elif fact == "event":
        value = " ".join(x for x in (event_words(c), when_where(r, c)) if x)
    elif fact == "attribute":
        label = WORDS["attribute"].get(c.get("attribute_type"), _first_upper(plain(c.get("attribute_type"))))
        value = " ".join(x for x in (_text(c.get("value_as_written")), when_where(r, c)) if x)
    elif fact == "membership":
        value = _text(c.get("group_as_written"))
    elif fact == "gender":
        value = _text(c.get("gender_as_written"))
    elif fact == "lineage":
        label = "Parent"
        value = WORDS["lineage"].get(c.get("fact_type"), "kind of parent not stated")
    elif fact == "family":
        label = "Couple"
        value = " ".join(x for x in (WORDS["family_fact"].get(c.get("fact_type"), plain(c.get("fact_type"))),
                                     when_where(r, c)) if x)
    elif fact == "tie":
        label = "Tie"
        t = _dict(rel).get("association_type")
        value = {"godparent": "godparent", "enslaved_by": "held enslaved", "ancestor_descendant": "ancestor",
                 "employer": "employer", "neighbour": "neighbour", "friend": "friend",
                 "multiple_birth": "born at the same birth"}.get(t, "another tie")
    else:
        value = when_where(r, c)
    band = BAND_LABEL.get(maxgen_db.conf_band(_num(c.get("confidence"))), "No score")
    words, _capped, _mine = origin(r, c)
    return "%s: %s · %s · %s" % (label, value or "(nothing written)", band, words)


def event_words(c: dict) -> str:
    et = c.get("event_type")
    words = WORDS["event"].get(et) or _first_upper(plain(c.get("event_type_as_written") or et))
    role = c.get("role")
    if role and role != "principal":
        words += " (as %s)" % WORDS["event_role"].get(role, plain(c.get("role_as_written") or role))
    return words


def _where_it_is(doc: dict, rid) -> tuple:
    u = _text(doc.get("source_url"))
    lowu = u.lower()
    if lowu.startswith(("http:", "https:")):
        return ("Where it is", u, "addr")
    if lowu.startswith("file:"):
        return ("Where it is", "A file on this computer: " + unquote(urlsplit(u).path), "text")
    if lowu == "urn:uuid:" + _text(rid).lower():
        return ("Where it is", "Kept in this file: what was typed here is the whole document.", "text")
    held = _text(doc.get("repository_as_written")).strip()
    where = _text(_dict(doc.get("locator")).get("as_written")).strip()
    if held or where:
        return ("Where it is", ", ".join(x for x in (held, where) if x), "text")
    return ("Where it is", u or "Not recorded", "text")


def _credit(doc: dict) -> str:
    att = _dict(doc.get("attribution"))
    name = _text(att.get("contributor_name")).strip()
    if not name:
        return ""
    org = _text(att.get("source_organization")).strip()
    return name + (" (%s)" % org if org else "")


def _licence_words(lic, doc: dict) -> str:
    words = WORDS["licence"].get(lic)
    if not words:
        return "Not stated"
    if "%s" in words:
        name = _text(_dict(doc.get("attribution")).get("contributor_name")).strip()
        return words % (name or "its maker (credit missing)")
    return words


def _classification(doc: dict) -> str:
    sc = _dict(doc.get("source_classification"))
    parts = [WORDS["source"].get(sc.get("source")), WORDS["information"].get(sc.get("information"))]
    parts = [p for p in parts if p]
    return ". ".join(parts) + "." if parts else "Not stated yet"


def record_date(doc: dict):
    """The record's own date, else its main event's date."""
    if isinstance(doc.get("record_date"), dict):
        return doc["record_date"]
    entries = [e for e in _list(doc.get("events_documented")) if isinstance(e, dict) and isinstance(e.get("date"), dict)]
    main = [e for e in entries if e.get("is_primary")] or entries
    return main[0]["date"] if main else None


def _record_date_key(doc: dict):
    d = record_date(doc)
    y = maxgen_db.date_year(d) if d else None
    return y if y is not None else 10 ** 6


def transcription(doc: dict) -> str:
    return "\n\n".join(_text(p.get("text")) for p in _list(doc.get("transcription_parts")) if isinstance(p, dict))


def relationship_claims(r: Reader, pid) -> list:
    """[(fact word, claim, link)] of every live or withdrawn claim on a family link naming this person."""
    out = []
    for rid, role in maxgen_db.links_of(r.rc, pid):
        rel = r.doc("relationship", rid)
        if not rel:
            continue
        if rel["kind"] == "parent_child":
            out += [("lineage", c, rel) for c in _list(rel.get("lineage_assertions")) if isinstance(c, dict)]
        elif rel["kind"] == "family" and role == "partner":
            out += [("family", c, rel) for c in _list(rel.get("fact_assertions")) if isinstance(c, dict)]
        elif rel["kind"] == "association":
            out += [("tie", c, rel) for c in _list(rel.get("association_assertions")) if isinstance(c, dict)]
    return out


def document_rows(ctx: PersonCtx) -> list:
    """Every record this person's claims and family links cite (by date), then records that name
    them but that nothing links to them yet."""
    r = ctx.reader
    lines: dict = {}
    for e in ctx.claims:
        if e["rid"]:
            lines.setdefault(e["rid"], []).append(claim_line(r, e["claim"], e["fact"]))
    for fact, c, rel in relationship_claims(r, ctx.pid):
        rid = low(_dict(c.get("evidence")).get("source_record_id"))
        if rid:
            lines.setdefault(rid, []).append(claim_line(r, c, fact, rel))
    named = {}
    if ctx.names:
        for m in r.con.execute("SELECT record_id, mention_id, name_as_written FROM mention_index WHERE status='active'"):
            if m["record_id"] in lines or m["record_id"] in named:
                continue
            if norm_name(m["name_as_written"]) in ctx.names:
                named[m["record_id"]] = m["name_as_written"]

    def build(rid, connects, cited):
        info = r.record(rid)
        doc = info["doc"]
        if doc is None:
            return None
        kind = info["kind"] + (": added by you" if info["byyou"] else "")
        text = transcription(doc)
        detail = [("Word for word", text or "No word-for-word copy was kept.", "pre" if text else "text"),
                  _where_it_is(doc, info["id"])]
        given = _text(_dict(_dict(doc.get("extensions")).get("person_page")).get("link")).strip() if info["byyou"] else ""
        if given:
            detail.append(("Link you gave", given, "addr"))
        lic = doc.get("redistribution_license")
        detail.append(("Licence", _licence_words(lic, doc), "text"))
        if lic in ("CC-BY", "CC-BY-SA"):
            detail.append(("Credit", _credit(doc) or "credit missing", "text"))
        detail.append(("What kind of source", _classification(doc), "text"))
        names = ["%s (%s)" % (_text(m.get("name_as_written")) or "(no name written)",
                              WORDS["event_role"].get(m.get("role"), _text(m.get("role_as_written")) or "also named"))
                 for m in _list(doc.get("persons_mentioned")) if isinstance(m, dict)]
        if names:
            detail.append(("Names in it", names, "list"))
        if cited:
            detail.append(("Facts on this page that cite it", lines.get(rid, []), "list"))
        rv = latest_review(r, info["id"], "max_record")
        if rv and _text(rv.get("note")).strip():
            detail.append(("Quality Control's question for you" if needs_human(rv["note"])
                           else "Quality Control's note (written for the Genealogist)", _text(rv["note"]), "qc"))
        marks = {"doc"}
        if info["byyou"]:
            marks.add("byyou")
        if info["stage"] == "staged":
            marks.add("waiting")
        elif info["stage"] == "rejected":
            marks.add("turned_down")
        return {"id": doc_anchor(info["id"]) or None,
                "cells": {"what": kind, "date": fmt_date(record_date(doc)) or "No date on it",
                          "connects": connects, "checked": stage_words(info["stage"])},
                "detail": detail, "marks": marks, "_key": (_record_date_key(doc), kind)}

    rows_cited, rows_named = [], []
    for rid, ls in lines.items():
        n = len(ls)
        row = build(rid, "Cited by %d fact%s on this page" % (n, "" if n == 1 else "s"), True)
        if row:
            rows_cited.append(row)
    for rid, words in named.items():
        row = build(rid, "Names '%s'; no fact links it to this person yet" % words, False)
        if row:
            rows_named.append(row)
    rows_cited.sort(key=lambda x: x["_key"])
    rows_named.sort(key=lambda x: x["_key"])
    return rows_cited + rows_named


def document_intro(ctx: PersonCtx, rows: list) -> str:
    named = sum(1 for r in rows if r["cells"]["connects"].startswith("Names "))
    cited = len(rows) - named
    if not rows:
        return ""
    parts = ["%d cited by the facts on this page" % cited]
    if named:
        parts.append("%d more that name them" % named)
    return " · ".join(parts) + ". Open one to read it word for word."


def _task_what(t: dict) -> str:
    doc = t["doc"]
    p, res = _dict(doc.get("payload")), _dict(doc.get("result"))
    kind = doc.get("task_type")
    if p.get("kind") == "person_request" and _text(p.get("request")).strip():
        what = "Your request: " + _text(p["request"]).strip()
    elif p.get("kind") in WORDS["other_kind"] and p.get("kind") != "person_request":
        what = WORDS["other_kind"][p["kind"]]
    elif _text(p.get("look_for")).strip():
        what = _first_upper(_text(p["look_for"]).strip())
    elif _text(_dict(res.get("proof")).get("question")).strip():
        what = _text(res["proof"]["question"]).strip()
    elif _text(res.get("description")).strip():
        what = _text(res["description"]).strip()
    else:
        what = WORDS["task_kind"].get(kind, "Other work")
    src = _dict(p.get("source"))
    if _text(src.get("name")).strip():
        what += " at " + _text(src["name"]).strip()
    return what


ORDER = ("escalated", "waiting", "done", "failed", "on_hold", "cancelled", "other")


def research_rows(ctx: PersonCtx) -> list:
    """Needs you, then waiting (highest priority first, then oldest), then finished (newest
    first), then could not run, paused and cancelled."""
    r = ctx.reader
    buckets = {k: [] for k in ORDER}
    for t in ctx.tasks():
        s = t["row"]["status"]
        buckets["waiting" if s in ("pending", "processing") else s if s in buckets else "other"].append(t)

    def created(t):
        return _text(t["doc"].get("created_at") or t["row"]["added_at"])

    buckets["escalated"].sort(key=created)
    buckets["waiting"].sort(key=lambda t: (-(_int(t["row"]["priority"]) or 5), created(t)))
    buckets["done"].sort(key=lambda t: _text(t["doc"].get("completed_at") or t["row"]["updated_at"]), reverse=True)
    out = []
    for n, t in enumerate([t for k in ORDER for t in buckets[k]], 1):
        row, doc = t["row"], t["doc"]
        p, res = _dict(doc.get("payload")), _dict(doc.get("result"))
        status = row["status"]
        stands = WORDS["status"].get(status, plain(status))
        if status == "done" and row["verdict"]:
            stands = "Finished: " + WORDS["verdict"].get(row["verdict"], plain(row["verdict"])).lower()
        what = _task_what(t)
        if status == "done":
            came = _text(res.get("verdict_reason") or res.get("what_we_did_and_result"))
        elif status == "escalated":
            came = _text(doc.get("escalation_reason"))
        elif status == "failed":
            came = _text(doc.get("error_message"))
        elif status == "on_hold":
            came = "Paused until it can go on"
        elif status == "cancelled":
            came = "Cancelled; nothing more will be done"
        else:
            came = "Priority %d of 10 (higher goes first)" % (_int(row["priority"]) or 5)
        detail = []
        steps = ["%s: searched '%s' · found: %s · %s" % (_text(s.get("source")), _text(s.get("query")),
                                                         _text(s.get("found")) or "nothing",
                                                         WORDS["verdict"].get(s.get("verdict"), plain(s.get("verdict"))))
                 for s in _list(res.get("steps")) if isinstance(s, dict)]
        if steps:
            detail.append(("Each search", steps, "list"))
        if _text(res.get("next_step")).strip():
            detail.append(("Next step", _text(res["next_step"]), "text"))
        for rv in person_reviews(r, row["task_id"]):
            review = _dict(_dict(rv.get("result")).get("review"))
            words = WORDS["answer"].get(review.get("verdict"), "Your answer")
            note = _text(review.get("notes")).strip()
            detail.append(("Your answer", words + (": " + note if note else ""), "text"))
        request = _text(p.get("request")) if p.get("kind") == "person_request" else ""
        if len(what) > 160:
            what = what[:157] + "…"
            if request:
                detail.insert(0, ("Your words", request, "pre"))
        marks = {"task"}
        if status == "escalated":
            marks.add("needs_you")
        if status in ("pending", "processing"):
            marks.add("waiting")
        if request or p.get("kind") == "person_page_session":
            marks.add("byyou")
        out.append({"id": "task-%d" % n,
                    "cells": {"stands": stands, "what": what, "came": came or "Nothing written yet",
                              "when": local_day(row["updated_at"])},
                    "detail": detail, "marks": marks})
    return out


def research_intro(ctx: PersonCtx, rows: list) -> str:
    c = task_counts(ctx.tasks())
    parts = []
    if c["needs_you"]:
        parts.append("Needs you %d" % c["needs_you"])
    if c["waiting"]:
        parts.append("Waiting %d" % c["waiting"])
    if c["done"]:
        found = ", ".join("%s %d" % (WORDS["verdict"][v], c["verdicts"][v])
                          for v in WORDS["verdict"] if c["verdicts"].get(v))
        parts.append("Finished %d" % c["done"] + (" (%s)" % found if found else ""))
    if c["failed"]:
        parts.append("Could not run %d" % c["failed"])
    if c["paused"]:
        parts.append("Paused %d" % c["paused"])
    if c["cancelled"]:
        parts.append("Cancelled %d" % c["cancelled"])
    dead = len(dead_ends_for(ctx.reader, ctx.pid))
    if dead:
        parts.append("Searches on record that found nothing: %d (see that tab)" % dead)
    return " · ".join(parts)


def nothing_found_rows(ctx: PersonCtx) -> list:
    out = []
    for d in dead_ends_for(ctx.reader, ctx.pid):
        note = _text(d["note"]).strip()
        came = note or "nothing"
        if d.get("verdict") == "inaccessible" or note.lower() == "access restricted":
            came += (" — the site does not allow automated searching, so this search is yours to run "
                     "(see Research)")
        out.append({"id": None, "cells": {"where": _text(d["source"]), "searched": _text(d["query"]),
                                          "came": came, "when": local_day(d["at"])},
                    "detail": [], "marks": set()})
    return out


def _period_words(d) -> str:
    return fmt_date(d) if isinstance(d, dict) else ""


def place_rows(ctx: PersonCtx) -> list:
    """The places this person's facts point at (once a place has been identified), with their
    history: names, kinds and the larger places they lay in, each with its dates."""
    r = ctx.reader
    facts: dict = {}
    for e in ctx.claims:
        pid_ = _dict(e["claim"].get("place")).get("place_id")
        if pid_ and e["live"]:
            facts.setdefault(r.rc.resolve(pid_), []).append(claim_line(r, e["claim"], e["fact"]))
    for fact, c, rel in relationship_claims(r, ctx.pid):
        pid_ = _dict(c.get("place")).get("place_id")
        if pid_ and maxgen_db.live_claim(c):
            facts.setdefault(r.rc.resolve(pid_), []).append(claim_line(r, c, fact, rel))
    out = []
    for place_id, lines in facts.items():
        doc = r.doc("place", place_id)
        if not doc:
            continue
        live = lambda key: [c for c in _list(doc.get(key)) if isinstance(c, dict) and maxgen_db.live_claim(c)]
        name = _text(maxgen_db.best_claim_value(doc.get("name_assertions"), "name_as_written")) or "(no name)"
        kinds = live("kind_assertions")
        kind_now = _first_upper(plain(max(kinds, key=_conf)["kind"])) if kinds else "Kind not recorded"
        parents = []
        for c in live("parent_place_assertions"):
            pdoc = r.doc("place", c.get("parent_place_id"))
            pname = _text(maxgen_db.best_claim_value(_dict(pdoc).get("name_assertions"), "name_as_written")) or \
                "a place not in this file"
            pkinds = [k for k in _list(_dict(pdoc).get("kind_assertions")) if maxgen_db.live_claim(k)]
            pkind = (" (%s)" % plain(max(pkinds, key=_conf)["kind"])) if pkinds else ""
            parents.append(("%s%s%s" % (pname, pkind, (", " + _period_words(c.get("date"))) if c.get("date") else ""),
                            c))
        detail = [("Names", ["%s%s" % (_text(c.get("name_as_written")),
                                       (", " + _period_words(c.get("date"))) if c.get("date") else "")
                             for c in live("name_assertions")], "list"),
                  ("Kinds", ["%s%s" % (_first_upper(plain(c.get("kind"))),
                                       (", " + _period_words(c.get("date"))) if c.get("date") else "")
                             for c in kinds], "list"),
                  ("Part of", [w for w, _c in parents] or ["Not recorded"], "list"),
                  ("Facts on this page here", lines, "list")]
        out.append({"id": "place-" + place_id, "cells": {"place": name, "kind": kind_now,
                                                         "within": "; ".join(w for w, _c in parents) or "Not recorded",
                                                         "facts": "%d fact%s" % (len(lines), "" if len(lines) == 1 else "s"),
                                                         "checked": stage_words(r.stage("place", place_id))},
                    "detail": detail, "marks": {"place"}})
    out.sort(key=lambda x: x["cells"]["place"].lower())
    return out


def _bounds(d, cls="other"):
    return maxgen_db.date_bounds(d, cls) if isinstance(d, dict) else (None, None)


def group_facts_in_period(r: Reader, group: dict, period) -> list:
    """The group's own live facts that fall inside a membership period (all of them when the
    period is not known), each as (words, claim)."""
    lo, hi = _bounds(period)
    out = []
    for c in _list(_dict(group).get("fact_assertions")):
        if not isinstance(c, dict) or not maxgen_db.live_claim(c):
            continue
        flo, fhi = _bounds(c.get("date"))
        inside = (lo is None and hi is None) or flo is None or (
            (hi is None or flo <= hi) and (lo is None or (fhi if fhi is not None else flo) >= lo))
        if inside:
            words = " · ".join(x for x in (WORDS["group_fact"].get(c.get("fact_type"), _first_upper(plain(c.get("fact_type")))),
                                           when_where(r, c)) if x)
            out.append((words, c))
    return out


def group_rows(ctx: PersonCtx) -> list:
    """The groups this person belonged to: one row per membership claim."""
    r = ctx.reader
    out = []
    for n, e in enumerate([e for e in ctx.claims if e["fact"] == "membership" and e["live"]], 1):
        c = e["claim"]
        gid = c.get("group_id")
        group = r.doc("group", gid) if gid else None
        name = (_text(maxgen_db.best_claim_value(_dict(group).get("name_assertions"), "name_as_written"))
                if group else "") or _text(c.get("group_as_written")) or "(no name)"
        kind = (WORDS["group_kind"].get(group.get("group_kind"), _first_upper(plain(group.get("group_kind"))))
                if group else "Not yet identified as a group")
        role = WORDS["event_role"].get(c.get("role"), _text(c.get("role_as_written")) or "member")
        detail = [("How they belonged", "%s · %s · %s" % (role, _period_words(c.get("date")) or "no dates given",
                                                          origin(r, c)[0]), "text")]
        if group:
            facts = ["%s — from the company's record" % w for w, _c in group_facts_in_period(r, group, c.get("date"))]
            detail.append(("The group's own record, in their time", facts or ["Nothing dated in this period"], "list"))
            docs = [r.record(o.get("record_id"))["kind"] or "a document not in this file"
                    for o in _list(group.get("observed_in")) if isinstance(o, dict)]
            if docs:
                detail.append(("Documents about the group", docs, "list"))
        out.append({"id": "group-%d" % n,
                    "cells": {"group": name, "kind": kind, "role": _first_upper(role),
                              "when": _period_words(c.get("date")) or "No dates given",
                              "checked": stage_words(r.stage("group", gid)) if group else "Not identified yet"},
                    "detail": detail, "marks": {"group"}})
    return out


DOCUMENTS = Tab(key="documents", label="Documents", schema="MaxRecord", table="max_record", rows=document_rows,
                columns=(("what", "What it is"), ("date", "Date on it"), ("connects", "How it connects"),
                         ("checked", "Checked?")),
                empty="No documents for this person yet. When the Genealogist finds one, it appears here.",
                intro=document_intro)
RESEARCH = Tab(key="research", label="Research", schema="MaxTask", table="max_task", rows=research_rows,
               columns=(("stands", "Where it stands"), ("what", "What"), ("came", "What came of it"),
                        ("when", "Last change")),
               empty="No research for this person yet. Use 'Ask the Genealogist' above to start some.",
               intro=research_intro)
NOTHING_FOUND = Tab(key="nothing_found", label="Searches that found nothing",
                    schema="search steps (a kit view, not a schema)", table="dead_end", rows=nothing_found_rows,
                    columns=(("where", "Where"), ("searched", "What was searched"), ("came", "What came back"),
                             ("when", "When")),
                    empty="No searches have come back empty for this person yet.")
PLACES = Tab(key="places", label="Places", schema="MaxPlace", table="max_place", rows=place_rows,
             columns=(("place", "Place"), ("kind", "What kind of place"), ("within", "Part of"),
                      ("facts", "Facts here"), ("checked", "Checked?")),
             empty="No places are linked to this person's facts yet. When a place is identified, it appears here "
                   "with its history.")
GROUPS = Tab(key="groups", label="Groups", schema="MaxGroup", table="max_group", rows=group_rows,
             columns=(("group", "Group"), ("kind", "What kind"), ("role", "Their part"), ("when", "When"),
                      ("checked", "Checked?")),
             empty="No groups for this person yet: a ship's company, a regiment or a congregation they belonged "
                   "to appears here.")
TAB_REGISTRY: list = [DOCUMENTS, RESEARCH, NOTHING_FOUND, PLACES, GROUPS]


# ------------------------------------------------------------------ HTML

def page(server, title: str, body: str, token: bool = False, bare: bool = False) -> str:
    """bare: a page for a request from the wrong address -- it names no file and carries no data."""
    meta = '<meta name="pp-token" content="%s">' % esc(server.token) if token else ""
    name = esc(Path(server.db).name) if (server.db and not bare) else ""
    line = ("%s · on this computer only · read just now" % name) if name else "on this computer only"
    foot = ("Probable Pedigree %s · maxgen_db %s · MAXGEN %s · reads %s · nothing on this page leaves this computer"
            % (esc(__version__), esc(maxgen_db.__version__), esc(maxgen_db.MAXGEN_VERSION), name)) if name else \
        "Probable Pedigree · nothing on this page leaves this computer"
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">%s<title>%s</title>'
            '<link rel="stylesheet" href="/app.css"><script src="/app.js" defer></script></head><body>'
            '<header class="top"><h1>Probable Pedigree</h1><p>%s</p>%s</header><main>%s</main>'
            "<footer>%s</footer></body></html>" % (meta, esc(title), line, BANNER, body, foot))


def goto_form(r: Reader, current: str = "") -> str:
    groups = {"approved": [], "staged": [], "rejected": []}
    for row in r.con.execute("SELECT person_id, stage FROM max_person WHERE lifecycle='active'"):
        if not ID_RE.match(row["person_id"]):
            continue
        y = maxgen_db.person_dates(r.rc, row["person_id"])[0]
        label = r.name(row["person_id"]) + (" (b. %d)" % y if y is not None else "")
        groups.setdefault(row["stage"], []).append((label.lower(), label, row["person_id"]))
    if not any(groups.values()):
        return ""
    out = []
    for stage, heading in (("approved", "On the chart"), ("staged", "Waiting for a check"),
                           ("rejected", "Turned down")):
        if groups.get(stage):
            out.append('<optgroup label="%s">%s</optgroup>' % (esc(heading), "".join(
                '<option value="%s"%s>%s</option>' % (esc(pid), " selected" if pid == current else "", esc(label))
                for _k, label, pid in sorted(groups[stage]))))
    return ('<form class="goto" method="get" action="/person"><label for="goto-id">Go to a person</label> '
            '<select id="goto-id" name="id">%s</select> <button type="submit">Go</button></form>' % "".join(out))


def legend_html() -> str:
    items, prev = [], None
    for th, cls, label in maxgen_db.CONF_BANDS:
        rng = ("%.2f+" % th) if th > 0 else ("below %.2f" % prev if prev is not None else "any")
        items.append('<i class="sw sw-%s" aria-hidden="true"></i>%s (%s)' % (cls, esc(label), rng))
        prev = th
    items.append('<i class="sw sw-none" aria-hidden="true"></i>Grey (no score)')
    lines = " · ".join('<i class="ln ln-%s" aria-hidden="true"></i>%s' % (k, esc(maxgen_db.LINE_WORDS[k]))
                       for k in ("b", "l", "u"))
    return ('<p class="legend" id="legend-boxes">Box colour = how sure the link to this person is: %s</p>'
            '<p class="legend" id="legend-lines">Lines: %s.</p>' % (" · ".join(items), lines))


def _numbered_svg(svg: str, n: int) -> str:
    return svg if n == 1 else svg.replace('<svg id="tree"', '<svg id="tree-%d"' % n, 1)


def chart_section(r: Reader, person, gens: int, lineage: str, picks: dict) -> str:
    mode = "preferred" if lineage == "all" else lineage
    persons = maxgen_db.chart_persons(r.rc, mode, picks)
    pid = person["person_id"] if person else None
    on_chart = pid in persons if pid else False
    root = pid if on_chart else maxgen_db.default_root(persons)
    head = '<section id="chart" aria-label="Family chart" data-lineage="%s" data-pick="%s" data-gens="%d"><h2>Family chart</h2>' \
        % (esc(lineage), esc(pick_text(picks)), gens)
    if not root:
        return (head + '<p class="note empty" id="chart-line">There is no one to chart yet. The chart shows only '
                "people Quality Control has checked.</p></section>")
    charts = []                                   # (title, persons, ahnentafel)
    if lineage == "all":
        for s, title in maxgen_db.all_root_sets(r.rc, root):
            view = maxgen_db.chart_persons(r.rc, "preferred", dict(picks, **{root: s["membership_id"]}))
            charts.append((title, view, maxgen_db.ahnentafel(view, root, gens)))
    if not charts:
        charts.append(("", persons, maxgen_db.ahnentafel(persons, root, gens)))
    shown = set()
    for _t, _v, ahn in charts:
        shown |= set(ahn.values())
    words = WORDS["view"][lineage]
    line = "%s and their ancestors · %s · %d generation%s · %s shown." % (
        esc(r.name(root)), esc(words), gens, "" if gens == 1 else "s", maxgen_db.n_people(len(shown)))
    how = " Drag to move, scroll or press + / − to zoom, click a box to open that person."
    if not on_chart and person:
        why = "turned them down" if person["stage"] == "rejected" else "has not checked them yet"
        line = ("%s is not on the chart because Quality Control %s. The chart shows %s instead. "
                % (esc(r.name(pid)), why, esc(r.name(root)))) + line
    nav = []
    for key in LINEAGES:
        href = person_href(pid or root, gens, key, picks)
        if key == lineage:
            nav.append('<a href="%s" data-lineage="%s" aria-current="page" class="current">%s</a>'
                       % (esc(href), key, esc(WORDS["view"][key])))
        else:
            nav.append('<a href="%s" data-lineage="%s">%s</a>' % (esc(href), key, esc(WORDS["view"][key])))
    nav_html = ('<nav id="lineage" aria-label="Which parents the chart follows"><span class="h">Follow:</span> %s</nav>'
                '<p class="note" id="lineage-help">Main parents: each person\'s main parent set. Birth lineage: birth '
                "parents wherever they are recorded. Adoptive lineage: adoptive parents where there are any, the "
                "main parents elsewhere. All parent sets: one chart for each of this person's parent sets.</p>"
                % " ".join(nav))
    if picks:
        nav_html += ('<p class="note" id="picks-line">You picked a parent set for %s. <a href="%s">Clear the picks</a></p>'
                     % (esc(", ".join(sorted(r.name(p) for p in picks))), esc(person_href(pid or root, gens, lineage))))
    opts = "".join('<option value="%d"%s>%d</option>' % (i, " selected" if i == gens else "", i)
                   for i in range(1, maxgen_db.MAX_GENS + 1))
    action = "/person/" + (pid or root)
    hidden = '<input type="hidden" name="lineage" value="%s">' % esc(lineage) if lineage != "preferred" else ""
    if picks:
        hidden += '<input type="hidden" name="pick" value="%s">' % esc(pick_text(picks))
    bar = ('<div class="treebar" id="treebar"><form id="gens-form" method="get" action="%s">'
           '<label for="gens">Generations</label> <select id="gens" name="gens">%s</select>%s '
           '<button type="submit" id="gens-go">Show</button></form></div>' % (esc(action), opts, hidden))
    hosts = []
    extras = []
    for n, (title, view, ahn) in enumerate(charts, 1):
        if title:
            hosts.append('<h3 class="chart-title" id="chart-title-%d">%s</h3>' % (n, esc(title)))
        hosts.append('<div class="treewrap" id="%s" data-chart="%d">%s</div>'
                     % ("tree-host" if n == 1 else "tree-host-%d" % n, n,
                        _numbered_svg(maxgen_db.svg_tree(view, ahn, gens, owner=True), n)))
        for x in sorted(set(ahn.values())):
            for extra_id, role in view[x].get("extra") or []:
                extras.append("%s (%s) is also a parent of %s; a chart box has room for two"
                              % (r.name(extra_id), WORDS["role"].get(role, "parent").lower(), r.name(x)))
    extra_html = ('<p class="note" id="chart-extra">%s.</p>' % esc("; ".join(sorted(set(extras))))) if extras else ""
    return (head + '<p class="note" id="chart-line">%s%s</p>' % (line, how) + nav_html + bar + "".join(hosts)
            + extra_html + legend_html() + "</section>")


FACTS_HEADING = {"staged": "What the documents say (not checked yet)",
                 "rejected": "What the documents say (turned down)"}


def facts_section(ctx: PersonCtx, living: bool) -> str:
    r = ctx.reader
    rows = []
    for fact in ("name", "birth", "death"):
        entries = [e for e in ctx.claims if e["fact"] == fact and e["live"]]
        top = best_name_claim(entries) if fact == "name" else best([e["claim"] for e in entries])
        label = WORDS["claim_fact"][fact]
        if top is None:
            value = "Living, as far as we know" if (fact == "death" and living) else "Not known yet"
            rows.append('<tr data-fact="%s"><th scope="row">%s</th><td>%s</td><td></td><td></td><td></td></tr>'
                        % (fact, label, value))
            continue
        value = _text(top.get("name_as_written")) if fact == "name" else when_where(r, top)
        rows.append('<tr data-fact="%s"><th scope="row">%s</th><td class="val">%s</td><td>%s</td><td>%s</td>'
                    "<td>%s</td></tr>" % (fact, label, esc(value or "(nothing written)"), chip_for(r, top),
                                          origin_html(r, top), evidence_html(r, top)))
    return ('<table id="facts"><thead><tr><th>Fact</th><th>%s</th><th>How sure</th><th>Where it came from</th>'
            "<th>Document</th></tr></thead><tbody>%s</tbody></table>"
            % (esc(FACTS_HEADING.get(ctx.stage, "Best supported")), "".join(rows)))


def conflict_section(ctx: PersonCtx) -> str:
    r = ctx.reader
    out = []
    for fact, noun in (("birth", "years of birth"), ("death", "years of death")):
        groups = conflicts([e for e in ctx.claims if e["fact"] == fact and e["live"]])
        if not groups:
            continue
        cols = []
        for label, entries in groups:
            entries = sorted(entries, key=lambda e: -_conf(e["claim"]))
            items = "".join("<li>%s · %s · %s · %s%s</li>" % (
                esc(fmt_date(e["claim"].get("date"))), chip_for(r, e["claim"]), origin_html(r, e["claim"]),
                esc(e["doc_kind"] or "Document not in this file"),
                (" · " + doc_link(e["rid"], e["doc_stage"])) if doc_anchor(e["rid"]) else "") for e in entries)
            cols.append('<div class="candidate"><p class="val">%s</p><ul>%s</ul></div>' % (esc(label), items))
        n = len(groups)
        number, kept = WORDS["number"].get(n, str(n)), "both" if n == 2 else "all"
        out.append('<div class="conflict" data-fact="%s" role="group" aria-label="%s different %s">'
                   '<p class="conflict-h">%s different %s: %s kept</p><div class="candidates">%s</div>'
                   '<p class="conflict-f">Nothing is thrown away. A better document settles it; until then '
                   "%s stay.</p></div>" % (fact, esc(number), noun, esc(number), noun, kept, "".join(cols),
                                           "both" if n == 2 else "they all"))
    return "".join(out)


def also_section(ctx: PersonCtx) -> str:
    r = ctx.reader
    names = [e for e in ctx.claims if e["fact"] == "name" and e["live"]]
    top = best_name_claim(names)
    if top is None:
        return ""
    seen = {norm_name(top.get("name_as_written"))}
    items = []
    for e in sorted(names, key=lambda x: -_conf(x["claim"])):
        n = norm_name(e["claim"].get("name_as_written"))
        if not n or n in seen:
            continue
        seen.add(n)
        items.append("<li>%s · %s · %s</li>" % (esc(e["claim"].get("name_as_written")), chip_for(r, e["claim"]),
                                                origin_html(r, e["claim"])))
    if not items:
        return ""
    return '<div class="also"><h3>Also written as</h3><ul class="plist">%s</ul></div>' % "".join(items)


def occasion_others(r: Reader, pid, occ) -> list:
    """Who else holds a live claim on the same occasion: [(person id, role words)]."""
    if not occ:
        return []
    ids = r.rc.merged_into(r.rc.resolve(occ))
    out = []
    qs = ",".join("?" * len(ids))
    for row in r.con.execute("SELECT DISTINCT holder_id, effective_holder_id, assertion_id, list_name FROM claim_index "
                             "WHERE holder_table='max_person' AND occurrence_id IN (%s) AND status IN "
                             "('proposed','proven','disputed')" % qs, list(ids)):
        if row["effective_holder_id"] == low(pid):
            continue
        holder = r.doc("person", row["holder_id"]) or {}
        c = next((x for x in _list(holder.get(row["list_name"])) if low(_dict(x).get("assertion_id")) == row["assertion_id"]),
                 {})
        role = c.get("role") or ("principal" if row["list_name"] in ("birth_assertions", "death_assertions") else None)
        words = WORDS["event_role"].get(role, plain(c.get("role_as_written") or role or "also there"))
        if (row["effective_holder_id"], words) not in out:
            out.append((row["effective_holder_id"], words))
    return out


def ties_of(r: Reader, pid) -> list:
    """[(association doc, my side, other side)] of every live association naming this person."""
    out = []
    for rid, role in maxgen_db.links_of(r.rc, pid):
        if role not in ("party1", "party2"):
            continue
        rel = r.doc("relationship", rid)
        if not rel or rel.get("kind") != "association":
            continue
        other = rel.get("party2") if role == "party1" else rel.get("party1")
        out.append((rel, role, _dict(other)))
    return out


def party_html(r: Reader, party: dict) -> str:
    if party.get("person_id"):
        return '<a href="%s">%s</a>' % (esc(person_href(r.rc.resolve(party["person_id"]))), esc(r.name(party["person_id"])))
    return "%s (named on a document, not yet a person in this file)" % esc(party.get("name_as_written") or "someone")


def rite_of(r: Reader, godparent, child, events=None):
    """A godparent's claim at a rite whose principal is the child: (claim, occasion words) or None."""
    holder = r.doc("person", godparent) or {}
    for c in _list(holder.get("event_assertions")):
        if not isinstance(c, dict) or not maxgen_db.live_claim(c) or c.get("role") != "godparent":
            continue
        kind, owner = maxgen_db.event_owner(r.rc, r.rc.resolve(c.get("occurrence_id"))) if c.get("occurrence_id") \
            else (None, None)
        if kind == "person" and owner == r.rc.resolve(child):
            return c, " ".join(x for x in (WORDS["event"].get(c.get("event_type"), plain(c.get("event_type"))).lower(),
                                           fmt_date(c.get("date"))) if x)
    return None


def more_facts_section(ctx: PersonCtx) -> str:
    """Events, lasting facts, things that did not happen and groups; then ties to other people."""
    r = ctx.reader
    live = [e for e in ctx.claims if e["live"]]
    ties = ties_of(r, ctx.pid)
    godchildren = {r.rc.resolve(o.get("person_id")) for rel, side, o in ties
                   if rel.get("association_type") == "godparent" and side == "party1" and o.get("person_id")}
    blocks = []
    items = []
    for e in [e for e in live if e["fact"] == "event"]:
        c = e["claim"]
        if c.get("role") == "godparent" and c.get("occurrence_id"):
            kind, owner = maxgen_db.event_owner(r.rc, r.rc.resolve(c["occurrence_id"]))
            if kind == "person" and owner in godchildren:
                continue                     # shown once, with the lasting tie, below (spec 3.8)
        others = occasion_others(r, ctx.pid, c.get("occurrence_id"))
        also = ""
        if others:
            also = ('<span class="also-there">Also at this occasion: %s</span>'
                    % ", ".join('<a href="%s">%s</a> (%s)' % (esc(person_href(p)), esc(r.name(p)), esc(w))
                                for p, w in others))
        items.append('<li data-list="event_assertions">%s · %s · %s · %s%s</li>'
                     % (esc(" · ".join(x for x in (event_words(c), when_where(r, c)) if x)), chip_for(r, c),
                        origin_html(r, c), evidence_html(r, c), also))
    if items:
        blocks.append('<h4>Events</h4><ul class="plist" id="events">%s</ul>' % "".join(items))
    items = []
    for e in [e for e in live if e["fact"] == "attribute"]:
        c = e["claim"]
        label = WORDS["attribute"].get(c.get("attribute_type"), _first_upper(plain(c.get("attribute_type"))))
        value = " · ".join(x for x in (_text(c.get("value_as_written")), when_where(r, c)) if x)
        items.append('<li data-list="attribute_assertions">%s: %s · %s · %s · %s</li>'
                     % (esc(label), esc(value), chip_for(r, c), origin_html(r, c), evidence_html(r, c)))
    if items:
        blocks.append('<h4>Lasting facts</h4><ul class="plist" id="lasting">%s</ul>' % "".join(items))
    items = []
    for e in [e for e in live if e["fact"] == "non_event"]:
        c = e["claim"]
        words = "No %s" % (WORDS["event"].get(c.get("event_type")) or plain(c.get("event_type"))).lower()
        items.append('<li data-list="non_event_assertions">%s · %s · %s · %s · %s</li>'
                     % (esc(words), esc(when_where(r, c) or "no date given"), chip_for(r, c), origin_html(r, c),
                        evidence_html(r, c)))
    if items:
        blocks.append('<h4>Did not happen</h4><ul class="plist" id="non-events">%s</ul>' % "".join(items))
    items = []
    for e in [e for e in live if e["fact"] == "membership"]:
        c = e["claim"]
        group = r.doc("group", c["group_id"]) if c.get("group_id") else None
        name = (_text(maxgen_db.best_claim_value(_dict(group).get("name_assertions"), "name_as_written"))
                if group else "") or _text(c.get("group_as_written"))
        role = WORDS["event_role"].get(c.get("role"), _text(c.get("role_as_written")) or "member")
        sub = ""
        if group:
            facts = group_facts_in_period(r, group, c.get("date"))
            if facts:
                sub = '<ul class="group-facts">%s</ul>' % "".join(
                    "<li>%s · %s <span class=\"muted\">from the company's record</span></li>"
                    % (esc(w), chip_for(r, gc)) for w, gc in facts)
        items.append('<li data-list="membership_assertions">%s (%s) · %s · %s · %s · %s%s</li>'
                     % (esc(name), esc(role), esc(_period_words(c.get("date")) or "no dates given"), chip_for(r, c),
                        origin_html(r, c), evidence_html(r, c), sub))
    if items:
        blocks.append('<h4>Groups</h4><ul class="plist" id="memberships">%s</ul>' % "".join(items))
    items = []
    for rel, side, other in ties:
        t = rel.get("association_type")
        mine, theirs = WORDS["tie"].get(t, WORDS["tie"]["other"])
        words = (mine if side == "party1" else theirs) % party_html(r, other) if "%s" in mine else mine
        facts = [c for c in _list(rel.get("association_assertions")) if isinstance(c, dict) and maxgen_db.live_claim(c)]
        top = best(facts)
        rite = ""
        if t == "godparent" and other.get("person_id"):
            hit = rite_of(r, ctx.pid, other["person_id"]) if side == "party1" else rite_of(r, other["person_id"], ctx.pid)
            if hit:
                rite = " · at the %s (the rite and the lasting tie are one fact here)" % esc(hit[1])
        items.append('<li data-list="association" data-tie="%s">%s%s · %s · %s</li>'
                     % (esc(t), words, rite, chip_for(r, top) if top else "", evidence_html(r, top) if top else ""))
    if items:
        blocks.append('<h4>Ties to other people</h4><ul class="plist" id="ties">%s</ul>' % "".join(items))
    if not blocks:
        return ""
    return '<div class="more" id="more-facts"><h3>More facts</h3>%s</div>' % "".join(blocks)


def withdrawn_section(ctx: PersonCtx) -> str:
    r = ctx.reader
    gone = [e for e in ctx.claims if not e["live"]]
    if not gone:
        return ""
    items = "".join('<li>%s · %s · reason: %s</li>' % (
        esc(claim_line(r, e["claim"], e["fact"])), esc("Disproved" if e["claim"].get("status") == "disproven"
                                                       else "Withdrawn"),
        esc(_text(e["claim"].get("status_reason")) or "none written")) for e in gone)
    return ('<details class="withdrawn" id="withdrawn"><summary>Withdrawn (%d): kept, never deleted, and no longer '
            "counted</summary><ul class=\"plist\">%s</ul></details>" % (len(gone), items))


def lineage_fact_html(r: Reader, f: dict) -> str:
    words = WORDS["lineage"].get(f.get("fact_type"), "kind of parent not stated")
    ev = _dict(f.get("evidence"))
    if ev.get("basis") == "dna":
        dim = WORDS["dimension"]["genetic"]
    elif f.get("dimension") in WORDS["dimension"]:
        dim = WORDS["dimension"][f["dimension"]]
    elif f.get("fact_type") == "birth_parent":
        dim = "by birth"
    else:
        dim = ""
    return '<span class="lfact">%s%s · %s · %s · %s</span>' % (
        esc(words), (" (%s)" % esc(dim)) if dim else "", chip_for(r, f), origin_html(r, f), evidence_html(r, f))


def parent_sets_section(ctx: PersonCtx, gens: int, lineage: str, picks: dict, person_stage: str) -> str:
    """Every live parent set: on the chart (eligible) or waiting for a check; the main one starred."""
    r = ctx.reader
    eligible = {s["membership_id"]: s for s in maxgen_db.parent_sets(r.rc, ctx.pid)}
    every = maxgen_db.parent_sets(r.rc, ctx.pid, approved_only=False, include_waiting=True)
    if not every:
        return ('<div class="parents" id="parent-sets"><h3>Parent sets</h3><p class="empty">No parents recorded '
                "yet.</p></div>")
    every.sort(key=lambda s: (0 if s["is_preferred"] else 1, maxgen_db.SET_RANK.get(s["set_kind"], 6), s["created_at"]))
    blocks = []
    for s in every:
        mid = s["membership_id"]
        on_chart = mid in eligible
        ok_parents = {p["person_id"] for p in (eligible.get(mid) or {}).get("parents", [])}
        kind = s["set_kind"]
        heading = maxgen_db.SET_WORDS.get(kind, "Parents")
        star = (' <span class="star" title="the parents the chart shows first">★ Main parents</span>'
                if s["is_preferred"] else "")
        items = []
        for p in s["parents"]:
            ppid = p["person_id"]
            name = '<a href="%s">%s</a>' % (esc(person_href(ppid)), esc(r.name(ppid)))
            bits = [name, esc(WORDS["role"].get(p["role"], "Parent"))]
            facts = list(p.get("facts") or [])
            want = "birth" if kind == "alternative_hypothesis" else kind
            primary = [f for f in facts if maxgen_db.LINEAGE_TO_SET.get(f.get("fact_type"), "other") == want] or facts
            rest = [f for f in facts if not any(f is x for x in primary)]
            # one fact waiting on a link already on the chart; a whole waiting link gets the badge below
            waiting_ids = set(p.get("waiting_fact_ids") or ()) if p["person_id"] in ok_parents else set()
            shown = " ".join(lineage_fact_html(r, dict(f, _list="lineage_assertions")) +
                             (' <span class="badge stage-staged" title="Quality Control has not checked this fact '
                              'yet; the chart does not use it">Waiting for a check</span>'
                              if maxgen_db.low(f.get("assertion_id")) in waiting_ids else "")
                             for f in primary)
            if rest:
                shown += ('<details class="link-more"><summary>Also on this link: %d more</summary>%s</details>'
                          % (len(rest), " ".join(lineage_fact_html(r, dict(f, _list="lineage_assertions"))
                                                 for f in rest)))
            bits.append(shown or '<span class="muted">no live fact on this link</span>')
            stage = r.stage("person", ppid)
            if ppid not in ok_parents:
                bits.append('<span class="badge stage-staged" title="Quality Control has not passed this person or '
                            'this link yet; it is not on the chart">Waiting for a check</span>')
            elif stage and stage != "approved":
                bits.append(stage_badge(stage))
            items.append('<li class="parent" data-role="%s" data-person="%s">%s</li>'
                         % (esc(p["role"]), esc(ppid), " · ".join(bits)))
        actions = []
        if on_chart:
            href = person_href(ctx.pid, gens, lineage, dict(picks, **{ctx.pid: mid}))
            actions.append('<a class="pick" href="%s">Show this set on the chart</a>' % esc(href))
            if not s["is_preferred"] and person_stage != "rejected":
                actions.append('<form class="prefer-form" method="post" action="/api/prefer">'
                               '<input type="hidden" name="person_id" value="%s"><input type="hidden" '
                               'name="membership_id" value="%s"><input type="hidden" name="kind" value="%s">'
                               '<button type="submit" disabled>Make these the main parents</button>'
                               '<span class="status" aria-live="polite"></span></form>'
                               % (esc(ctx.pid), esc(mid), esc(heading)))
        else:
            actions.append('<span class="badge stage-staged">Waiting for a check: not on the chart yet</span>')
        cls = ["pset", "main" if s["is_preferred"] else "other"]
        if not on_chart:
            cls.append("waiting")
        if kind == "alternative_hypothesis":
            cls.append("possible")
        note = ('<p class="help">Candidates only: kept side by side until a proof settles them. They are never '
                "drawn as the main parents unless you choose them; when they are the only parents recorded, the "
                "chart draws them dotted with the note “Possible parents, not settled”.</p>"
                if kind == "alternative_hypothesis" else "")
        blocks.append('<div class="%s" id="set-%s" data-kind="%s" data-membership="%s"><h4>%s%s</h4>%s'
                      '<ul class="plist">%s</ul><div class="set-actions">%s</div></div>'
                      % (" ".join(cls), esc(mid), esc(kind), esc(mid), esc(heading), star, note,
                         "".join(items) or '<li class="empty">No parent named in this set</li>', " ".join(actions)))
    intro = ""
    if len(every) > 1:
        intro = ('<p class="help">%s has %d parent sets. The chart shows the main parents unless you follow another '
                 "line above, or pick a set here.</p>" % (esc(ctx.name), len(every)))
    return '<div class="parents" id="parent-sets"><h3>Parent sets</h3>%s%s</div>' % (intro, "".join(blocks))


def families_section(ctx: PersonCtx) -> str:
    """The families this person is a partner in: the other partner, the couple's facts, the children."""
    r = ctx.reader
    blocks = []
    for rid, role in maxgen_db.links_of(r.rc, ctx.pid, "partner"):
        fam = r.doc("relationship", rid)
        if not fam or fam.get("kind") != "family":
            continue
        partners = [p for p in maxgen_db.active_partners(fam)]
        others = []
        for p in partners:
            if p.get("person_id") and r.rc.resolve(p["person_id"]) == r.rc.resolve(ctx.pid):
                continue
            if p.get("person_id"):
                others.append('<a href="%s">%s</a>' % (esc(person_href(p["person_id"])), esc(r.name(p["person_id"]))))
            else:
                others.append(esc(p.get("label_as_written") or "a partner the documents do not name"))
        facts = [c for c in _list(fam.get("fact_assertions")) if isinstance(c, dict) and maxgen_db.live_claim(c)]
        fact_items = "".join('<li>%s · %s · %s · %s</li>' % (
            esc(" · ".join(x for x in (WORDS["family_fact"].get(c.get("fact_type"), plain(c.get("fact_type"))),
                                       when_where(r, c)) if x)), chip_for(r, c), origin_html(r, c), evidence_html(r, c))
            for c in facts)
        kids = []
        for m in _list(fam.get("children")):
            if not isinstance(m, dict) or m.get("status", "active") != "active":
                continue
            child = r.rc.resolve(m["child_id"])
            stage = r.stage("person", child)
            kids.append('<li data-set-kind="%s"><a href="%s">%s</a> · %s%s</li>' % (
                esc(m["set_kind"]), esc(person_href(child)), esc(r.name(child)),
                esc(WORDS["child_kind"].get(m["set_kind"], "child")),
                (" · " + stage_badge(stage)) if stage and stage != "approved" else ""))
        with_words = ("With " + " and ".join(others)) if others else "No other partner named"
        blocks.append('<div class="family" data-family="%s"><h4>%s</h4>%s%s</div>'
                      % (esc(rid), with_words,
                         ('<ul class="plist">%s</ul>' % fact_items) if fact_items else
                         '<p class="muted">No document about the couple yet.</p>',
                         ('<p class="kids-h">Children</p><ul class="plist">%s</ul>' % "".join(kids)) if kids else ""))
    if not blocks:
        return ('<div class="families" id="families"><h3>Partners and children</h3><p class="empty">No partners or '
                "children recorded yet.</p></div>")
    return '<div class="families" id="families"><h3>Partners and children</h3>%s</div>' % "".join(blocks)


def proof_concerns(r: Reader, task: dict, pid) -> bool:
    me = r.rc.resolve(pid)
    if r.rc.resolve(_dict(task.get("payload")).get("person_id")) == me:
        return True
    for s in _list(_dict(_dict(task.get("result")).get("proof")).get("subject")):
        if not isinstance(s, dict):
            continue
        if s.get("kind") in ("person", "person_pair") and me in (r.rc.resolve(s.get("id")), r.rc.resolve(s.get("id2"))):
            return True
        if s.get("kind") == "claim":
            row = r.con.execute("SELECT holder_table, effective_holder_id FROM claim_index WHERE assertion_id=?",
                                (low(s.get("id")),)).fetchone()
            if row and row["holder_table"] == "max_person" and row["effective_holder_id"] == me:
                return True
    return False


def decisions_waiting(r: Reader, pid) -> list:
    """Proofs about this person that are done and not accepted now: the person decides them."""
    out = []
    for row in r.con.execute("SELECT task_id, doc FROM max_task WHERE task_type IN ('resolve_conflict','merge_person') "
                             "AND status='done' ORDER BY added_at"):
        task = json.loads(row["doc"])
        if not maxgen_db.has_proof(task) or not proof_concerns(r, task, pid):
            continue
        if maxgen_db.accepted_now(r.rc, row["task_id"]):
            continue
        out.append(task)
    return out


def relied_on_words(r: Reader, item: dict) -> str:
    k = item.get("kind")
    note = _text(item.get("note")).strip()
    if k == "claim":
        row = r.con.execute("SELECT holder_table, holder_id, list_name FROM claim_index WHERE assertion_id=?",
                            (low(item.get("assertion_id")),)).fetchone()
        if row and row["holder_table"] == "max_person":
            holder = r.doc("person", row["holder_id"]) or {}
            c = next((x for x in _list(holder.get(row["list_name"])) if low(_dict(x).get("assertion_id")) ==
                      low(item.get("assertion_id"))), None)
            if c:
                line = esc(claim_line(r, c, FACT_OF_LIST.get(row["list_name"], "event")))
                rid = _dict(c.get("evidence")).get("source_record_id")
                return line + ((" · " + doc_link(rid, r.record(rid)["stage"])) if rid else "") + \
                    ((" · " + esc(note)) if note else "")
        return esc(note or "a fact on another page")
    if k in ("record", "authored_record", "mention"):
        info = r.record(item.get("record_id"))
        return "%s · %s%s" % (esc(info["kind"] or "a document"), doc_link(item.get("record_id"), info["stage"]),
                              (" · " + esc(note)) if note else "")
    if k == "search_step":
        return "A search that found nothing" + ((" · " + esc(note)) if note else "")
    if k == "dna_match":
        return "A DNA match" + ((" · " + esc(note)) if note else "")
    if k == "proof":
        return "Another decision already taken" + ((" · " + esc(note)) if note else "")
    return esc(note or plain(k))


def decisions_section(ctx: PersonCtx) -> str:
    r = ctx.reader
    tasks = decisions_waiting(r, ctx.pid)
    if not tasks:
        return ""
    blocks = []
    for t in tasks:
        proof = t["result"]["proof"]
        concl = _dict(proof.get("conclusion"))
        relied = "".join("<li>%s</li>" % relied_on_words(r, i) for i in _list(proof.get("relied_on"))
                         if isinstance(i, dict))
        last = person_reviews(r, t["task_id"])
        last_html = ""
        if last:
            rv = _dict(_dict(last[-1].get("result")).get("review"))
            last_html = ('<p class="help">Your last answer: %s: "%s" (%s).</p>'
                         % (esc(WORDS["answer"].get(rv.get("verdict"), "an answer")), esc(rv.get("notes") or ""),
                            esc(local_day(last[-1].get("completed_at") or last[-1].get("created_at")))))
        argument = _text(proof.get("argument")).strip()
        blocks.append(
            '<div class="decision" data-task="%s"><p class="q"><strong>The question:</strong> %s</p>'
            '<p><strong>What the Genealogist concluded:</strong> %s</p>%s'
            '<p class="h">What it rests on</p><ul class="plist">%s</ul>%s'
            '<form class="decide-form" method="post" action="/api/decide">'
            '<input type="hidden" name="person_id" value="%s"><input type="hidden" name="task_id" value="%s">'
            '<label for="words-%s">In your own words: why do you decide this way?</label>'
            '<textarea id="words-%s" name="words" rows="2" maxlength="%d" required></textarea>'
            '<p class="help">Only you can accept a conclusion here; only then can it count as proven. Your words '
            "are kept with your answer.</p>"
            '<div class="buttons"><button type="submit" name="verdict" value="accepted" disabled>Accept</button>'
            '<button type="submit" name="verdict" value="rework" disabled>Needs more work</button>'
            '<button type="submit" name="verdict" value="rejected" disabled>Reject</button></div>'
            "<noscript>Turn on JavaScript to use this.</noscript>"
            '<p class="status" aria-live="polite"></p></form></div>'
            % (esc(t["task_id"]), esc(proof.get("question")), esc(concl.get("statement")),
               ('<details class="argument"><summary>The full argument</summary><pre class="words">%s</pre></details>'
                % esc(argument)) if argument else "", relied, last_html, esc(ctx.pid), esc(t["task_id"]),
               esc(t["task_id"]), esc(t["task_id"]), MAX_NOTE))
    return ('<section id="decisions" class="card" aria-label="Decisions waiting for you"><h2>Decisions waiting for '
            "you</h2>%s</section>" % "".join(blocks))


def actions_section(person, name: str) -> str:
    pid = esc(person["person_id"])
    ask = ('<div class="action"><h3>Ask the Genealogist</h3>'
           '<form id="ask-form" method="post" action="/api/ask"><input type="hidden" name="person_id" value="%s">'
           '<label for="ask-text">What would you like the Genealogist to look into about %s?</label>'
           '<textarea id="ask-text" name="request" maxlength="%d" rows="3" placeholder="For example: find their '
           "parents' marriage record. I think they married around 1919.\"></textarea>"
           '<p class="help">This adds your request to the research list for the Orchestrator to plan. '
           "Nothing else changes.</p>"
           '<button type="submit" disabled>Send to the Genealogist</button>'
           "<noscript>Turn on JavaScript to use this.</noscript>"
           '<p id="ask-status" class="status" aria-live="polite"></p></form></div>' % (pid, esc(name), MAX_REQUEST))
    if person["stage"] == "rejected":
        add = ('<div class="action"><h3>Add something you know</h3><p class="strip turned-down">%s</p></div>'
               % esc(TURNED_DOWN_ADD))
    else:
        radios = "".join('<label class="radio"><input type="radio" name="how_known" value="%s"> %s</label>'
                         % (k, esc(v)) for k, v in WORDS["how_known"].items())
        facts = "".join('<option value="%s">%s</option>' % (k, esc(v)) for k, v in WORDS["fact"].items())
        add = ('<div class="action"><h3>Add something you know</h3>'
               '<form id="add-form" method="post" action="/api/add-fact"><input type="hidden" name="person_id" '
               'value="%s"><label for="add-fact">What is it?</label><select id="add-fact" name="fact">%s</select>'
               '<label for="add-value">What you know</label><input type="text" id="add-value" name="value" '
               'maxlength="%d" autocomplete="off">'
               '<fieldset><legend>How do you know?</legend>%s</fieldset>'
               '<label for="add-link">Link (optional)</label><input type="text" id="add-link" name="link" '
               'maxlength="%d" inputmode="url" autocomplete="off">'
               '<p class="help">If you saw it online, paste the address. Never paste one that holds a password '
               "or a sign-in code.</p>"
               '<label for="add-note">Note (optional)</label><textarea id="add-note" name="note" maxlength="%d" '
               'rows="2"></textarea>'
               "<p class=\"help\">What you add is kept as 'added by you', word for word, next to what documents "
               "say. It never replaces anything.</p>"
               "<p class=\"help\">It counts as Weak (0.4 out of 1) and never stands alone. Where a document says it "
               "more clearly, the document comes first. If you know where the document is, use 'Ask the "
               "Genealogist'.</p>"
               '<p class="help">Quality Control then checks this person again. Until then they, and their parents '
               "and grandparents above them, leave the chart. Best done while no batch is running.</p>"
               '<button type="submit" disabled>Add it</button><noscript>Turn on JavaScript to use this.</noscript>'
               '<p id="add-status" class="status" aria-live="polite"></p></form></div>'
               % (pid, facts, MAX_NAME, radios, MAX_LINK, MAX_NOTE))
    return '<section id="actions" class="actions" aria-label="Add to the research">%s%s</section>' % (ask, add)


def _cell_html(value) -> str:
    return esc(value)


def _detail_html(detail: list) -> str:
    out = []
    for label, value, kind in detail:
        if kind == "pre":
            dd = '<pre class="words">%s</pre>' % esc(value)
        elif kind == "addr":
            dd = ('<span class="addr">%s</span> <button type="button" class="copy" hidden>Copy address</button>'
                  % esc(value))
        elif kind == "list":
            dd = '<ul>%s</ul>' % "".join("<li>%s</li>" % esc(v) for v in (value or [])) if value else "None yet"
        elif kind == "qc":
            dd = qc_note_html(value)
        else:
            dd = esc(value)
        out.append("<dt>%s</dt><dd>%s</dd>" % (esc(label), dd))
    return '<dl class="detail">%s</dl>' % "".join(out) if out else ""


def render_tabs(ctx: PersonCtx, active: str) -> str:
    built = []
    for tab in TAB_REGISTRY:
        if not TAB_KEY_RE.match(tab.key):
            continue
        try:
            rows = list(tab.rows(ctx) or [])
        except sqlite3.OperationalError:     # a table this file does not have yet: no tab until it does
            continue
        intro = tab.intro(ctx, rows) if tab.intro else ""
        built.append((tab, rows, intro))
    if not built:
        return ""
    keys = [t.key for t, _r, _i in built]
    active = active if active in keys else keys[0]
    buttons, panels = [], []
    for tab, rows, intro in built:
        buttons.append('<button type="button" role="tab" id="tab-%s" data-tab="%s" aria-controls="panel-%s" '
                       'aria-selected="%s" title="%s">%s (%d)</button>'
                       % (tab.key, tab.key, tab.key, "true" if tab.key == active else "false", esc(tab.schema),
                          esc(tab.label), len(rows)))
        body = []
        for row in rows:
            anchor = _text(row.get("id"))
            marks = sorted(m for m in (row.get("marks") or ()) if MARK_RE.match(_text(m)))
            cells = _dict(row.get("cells"))
            summary = "".join('<span class="cell" data-col="%s"><span class="h">%s</span>%s</span>'
                              % (esc(k), esc(h), _cell_html(cells.get(k, ""))) for k, h in tab.columns)
            body.append('<details class="%s"%s><summary>%s</summary>%s</details>'
                        % (" ".join(["row"] + marks), (' id="%s"' % anchor) if ANCHOR_RE.match(anchor) else "",
                           summary, _detail_html(row.get("detail") or [])))
        panels.append('<section id="panel-%s" role="tabpanel" aria-labelledby="tab-%s" class="panel" data-tab="%s">'
                      "<h3>%s</h3>%s%s</section>"
                      % (tab.key, tab.key, tab.key, esc(tab.label),
                         '<p class="intro">%s</p>' % esc(intro) if intro else "",
                         "".join(body) if body else '<p class="empty" data-empty="%s">%s</p>'
                         % (tab.key, esc(tab.empty))))
    return ('<section id="research-tabs" aria-label="Documents and research"><div id="tabs" role="tablist" '
            'data-active="%s">%s</div>%s</section>' % (active, "".join(buttons), "".join(panels)))


def query_options(query: dict):
    gens = maxgen_db.DEFAULT_GENS
    try:
        gens = max(1, min(maxgen_db.MAX_GENS, int((query.get("gens") or [""])[0])))
    except ValueError:
        pass
    lineage = (query.get("lineage") or [""])[0]
    lineage = lineage if lineage in LINEAGES else "preferred"
    picks = parse_picks((query.get("pick") or [""])[0])
    return gens, lineage, picks


def render_person_page(server, r: Reader, person, query: dict):
    gens, lineage, picks = query_options(query)
    active = (query.get("tab") or [""])[0]
    saved = (query.get("saved") or [""])[0]
    doc, stage, pid = person["doc"], person["stage"], person["person_id"]
    name = r.name(pid)
    claims = claims_of(r, pid)
    ctx = PersonCtx(con=r.con, pid=pid, doc=doc, stage=stage, name=name, claims=claims,
                    cited={e["rid"] for e in claims if e["rid"]},
                    names={norm_name(e["claim"].get("name_as_written")) for e in claims if e["fact"] == "name"}
                    - {""}, reader=r)
    living = r.living(pid)
    badges = [stage_badge(stage)]
    if living:
        badges.append('<span class="badge living" title="worked out when read: no document shows this person died '
                      'or was born long enough ago">Living, private</span>')
    if person["doc"].get("person_kind") == "placeholder":
        badges.append('<span class="badge">A placeholder for someone not yet named</span>')
    strip = ""
    if stage == "staged":
        strip = ('<p class="strip waiting">Quality Control has not checked this person yet. '
                 "Nothing below is confirmed.</p>")
    elif stage == "rejected":
        rv = latest_review(r, pid, "max_person")
        note = _text((rv or {}).get("note")).strip()
        if needs_human(note):
            strip = ('<p class="strip turned-down">Quality Control turned this person down. %s '
                     "The person is kept, not deleted.</p>" % qc_note_html(note))
        else:
            strip = ('<p class="strip turned-down">Quality Control turned this person down. Its note, written for '
                     'the Genealogist: "%s". The person is kept, not deleted.</p>' % qc_note_html(note))
    c = task_counts(ctx.tasks())
    saved_line = ""
    if saved in SAVED:
        saved_line = '<p class="saved" role="status">%s</p>' % (esc(SAVED[saved] % name) if "%s" in SAVED[saved]
                                                                 else esc(SAVED[saved]))
    person_html = ('<section id="person" class="card" aria-labelledby="person-name"><h2 id="person-name">%s</h2>'
                   '<p class="badges">%s</p>%s%s%s%s%s%s%s%s<p class="counts">Research for %s: '
                   '<a id="task-counts" href="#tab-research">%s</a></p></section>'
                   % (esc(name), " ".join(badges), strip, facts_section(ctx, living), conflict_section(ctx),
                      also_section(ctx), more_facts_section(ctx), withdrawn_section(ctx),
                      parent_sets_section(ctx, gens, lineage, picks, stage), families_section(ctx), esc(name),
                      esc(counts_line(c))))
    body = (goto_form(r, pid) + chart_section(r, person, gens, lineage, picks) + saved_line + person_html
            + decisions_section(ctx) + actions_section(person, name) + render_tabs(ctx, active))
    # The title names no one: browsers keep titles in their history (and may sync it).
    return 200, page(server, "Probable Pedigree", body, token=True)


def render_empty_page(server) -> str:
    body = ('<section class="card"><h2>No one is in this file yet.</h2><p>When the Genealogist adds people, they '
            "appear here. Nothing in this file has been checked by Quality Control yet.</p></section>"
            '<section id="chart" aria-label="Family chart"><h2>Family chart</h2><p class="note empty" '
            'id="chart-line">There is no one to chart yet. The chart shows only people Quality Control has '
            "checked.</p></section>")
    return page(server, "Probable Pedigree", body)


def render_not_found(server) -> str:
    return page(server, "Not found - Probable Pedigree",
                '<section class="card"><h2>%s</h2><p><a href="/">Go to the first person</a></p></section>'
                % esc(NO_PERSON))


def render_simple(server, title: str, sentence: str, bare: bool = False) -> str:
    return page(server, title + " - Probable Pedigree",
                '<section class="card"><h2>%s</h2><p>%s</p>%s</section>'
                % (esc(title), esc(sentence), "" if bare else '<p><a href="/">Go to the first person</a></p>'),
                bare=bare)


# ------------------------------------------------------------------ static assets

def _ink(colour: str) -> str:
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#1d1a14" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "#ffffff"


HUB_CSS = """
[hidden]{display:none!important}
main{max-width:1100px}
header.top p{margin:4px 0 0}
footer{max-width:1100px;margin:0 auto;padding:10px 28px 40px;font:12px system-ui,sans-serif;color:#777}
a{color:#7a4a00}
a:focus-visible,button:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible{outline:2px solid #c8a24a;outline-offset:2px}
.goto{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font:14px system-ui,sans-serif;margin:6px 0 0}
.goto select{max-width:100%;min-width:0}
.card{background:#fff;border:1px solid #d9d2c2;border-radius:8px;padding:18px 20px;margin:18px 0}
.card h2{border:0;margin:0 0 6px;padding:0}
#person h2{font-size:1.6rem}
h3{font:600 13px system-ui,sans-serif;text-transform:uppercase;letter-spacing:.08em;color:#8a6a1f;margin:18px 0 6px}
h4{font:600 14px system-ui,sans-serif;margin:12px 0 4px;color:#3d3420}
.muted{color:#777}
.badges{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 10px;font:13px system-ui,sans-serif}
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:999px;background:#f0ebe0;border:1px solid #d9d2c2;font:13px system-ui,sans-serif}
.badge.stage-staged{background:#fff7ea;border-color:#e0a83b}
.badge.stage-rejected{background:#f8e5e5;border-color:#c0392b}
.badge.living{background:#7a1f1f;border-color:#7a1f1f;color:#fff}
.chip{display:inline-block;padding:1px 9px;border-radius:999px;font:600 11px/1.6 system-ui,sans-serif;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap;vertical-align:middle;cursor:help}
.qc-words{display:block;font:12px system-ui,sans-serif;color:#555;margin-top:2px}
.strip{padding:8px 12px;border-radius:4px;font:14px system-ui,sans-serif;margin:8px 0}
.strip.waiting{background:#fff7ea;border:1px solid #e0a83b}
.strip.turned-down{background:#f8e5e5;border:1px solid #c0392b}
.saved{background:#e7f3ea;border:1px solid #3a9a5c;border-radius:4px;padding:8px 12px;font:14px system-ui,sans-serif;margin:18px 0 0}
td:last-child{color:inherit;font-family:system-ui,sans-serif;font-size:13px}
#facts{margin:10px 0 0}
#facts th[scope=row]{width:9em;background:#faf7f0}
#facts td.val{font:15px Georgia,serif}
.byyou{font-weight:700;color:#7a4a00}
.conflict{border:1px solid #e0a83b;border-radius:10px;overflow:hidden;background:#fff7ea;margin:14px 0}
.conflict-h{margin:0;padding:8px 14px;background:rgba(224,168,59,.18);font:600 12px system-ui,sans-serif;text-transform:uppercase;letter-spacing:.08em;color:#8a5a10}
.candidates{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
.candidate{background:#fff;padding:12px 14px;border-right:1px dashed #d9d2c2}
.candidate:last-child{border-right:0}
.candidate .val{font-size:1.2rem;margin:0 0 6px}
.candidate ul{list-style:none;margin:0;padding:0;font:13px system-ui,sans-serif}
.candidate li{margin:0 0 4px}
.conflict-f{margin:0;padding:6px 14px;font:italic 12px system-ui,sans-serif;color:#555;background:rgba(224,168,59,.08)}
.plist{list-style:none;padding:0;margin:6px 0;font:14px system-ui,sans-serif}
.plist li{padding:5px 0;border-bottom:1px solid #f0ebe0}
.also-there{display:block;font-size:13px;color:#555;margin-top:2px}
.group-facts{list-style:none;margin:4px 0 0 14px;padding:0;font-size:13px}
.pset{border:1px solid #d9d2c2;border-radius:8px;padding:8px 14px;margin:10px 0;background:#fffdf7}
.pset.main{border:2px solid #c8a24a}
.pset.waiting{background:#fffaf0;border-style:dashed}
.pset.possible{background:#f7f4ee;border:1px dashed #b8902d}
.pset h4{margin:4px 0}
.star{font:600 12px system-ui,sans-serif;color:#8a6a1f;margin-left:8px}
.lfact{display:inline-block;margin-right:8px}
details.link-more{display:inline-block;font-size:13px;color:#555}
details.link-more summary{cursor:pointer}
.set-actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center;font:13px system-ui,sans-serif;margin:6px 0 2px}
.set-actions form{display:inline-flex;gap:8px;align-items:center}
.family{border-left:3px solid #e6dcc6;padding:2px 12px;margin:8px 0}
.kids-h{font:600 12px system-ui,sans-serif;color:#555;margin:6px 0 0}
details.withdrawn{font:13px system-ui,sans-serif;margin:12px 0;color:#555}
details.withdrawn summary{cursor:pointer}
.decision{border:1px solid #c8a24a;border-radius:8px;padding:10px 14px;margin:10px 0;background:#fffdf7;font:14px system-ui,sans-serif}
.decision .h{font-weight:600;margin:8px 0 0}
.decide-form{display:flex;flex-direction:column;gap:6px}
.decide-form .buttons{display:flex;gap:8px;flex-wrap:wrap}
.counts{font:14px system-ui,sans-serif;margin:14px 0 0}
.actions{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin:18px 0}
.action{background:#fff;border:1px solid #d9d2c2;border-radius:8px;padding:14px 18px}
.action h3{margin-top:0}
.action form{display:flex;flex-direction:column;gap:6px;font:14px system-ui,sans-serif}
.action label{font-weight:600}
.action label.radio{font-weight:400;display:block;margin:2px 0}
textarea,input[type=text],select{font:14px system-ui,sans-serif;padding:6px;border:1px solid #b8ad96;border-radius:4px;background:#fff;color:#1d1a14;max-width:100%;box-sizing:border-box}
fieldset{border:1px solid #d9d2c2;border-radius:4px;margin:4px 0;padding:6px 10px}
legend{font-weight:600;padding:0 4px}
.help{font:12px system-ui,sans-serif;color:#555;margin:0}
button{font:14px system-ui,sans-serif;padding:6px 14px;border:1px solid #8a6a1f;background:#c8a24a;color:#1d1a14;border-radius:4px;cursor:pointer;align-self:flex-start}
button[disabled]{opacity:.5;cursor:not-allowed}
.status{min-height:1.2em;font:13px system-ui,sans-serif;color:#7a1f1f;margin:0}
#lineage{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;font:14px system-ui,sans-serif;margin:10px 0 0}
#lineage .h{font-weight:600;color:#555}
#lineage a{padding:3px 10px;border:1px solid #d9d2c2;border-radius:999px;text-decoration:none;background:#fff}
#lineage a.current{background:#c8a24a;border-color:#8a6a1f;color:#1d1a14;font-weight:600}
.chart-title{margin:16px 0 0}
#research-tabs{margin:24px 0 0}
#tabs{display:flex;gap:4px;border-bottom:2px solid #d9d2c2;overflow-x:auto}
#tabs button[role=tab]{background:transparent;border:0;border-bottom:3px solid transparent;border-radius:0;padding:10px 16px;color:#555;margin-bottom:-2px;white-space:nowrap}
#tabs button[role=tab][aria-selected=true]{color:#8a6a1f;border-bottom-color:#c8a24a;font-weight:600}
.panel{background:#fff;border:1px solid #d9d2c2;border-top:0;padding:14px 18px;margin:0 0 18px}
.panel>h3{margin-top:4px}
.intro{font:13px system-ui,sans-serif;color:#555;margin:0 0 8px}
details.row{border:1px solid #eae4d6;border-radius:6px;margin:8px 0;background:#fffdf7}
details.row>summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:4px 14px;padding:8px 12px 8px 28px;cursor:pointer;font:13px system-ui,sans-serif;position:relative;list-style:none}
details.row>summary::-webkit-details-marker{display:none}
details.row>summary::before{content:"\\25B8";position:absolute;left:10px;top:8px;color:#8a6a1f}
details.row[open]>summary::before{content:"\\25BE"}
details.row[open]>summary{border-bottom:1px solid #eae4d6}
.cell .h{display:block;font-size:11px;color:#777;text-transform:uppercase;letter-spacing:.05em}
details.row.byyou{border-left:4px solid #c8a24a}
details.row.needs_you{border-left:4px solid #c0392b}
details.row.waiting{background:#fffaf0}
details.row.turned_down{background:#fbf0f0}
dl.detail{margin:0;padding:10px 14px;font:13px system-ui,sans-serif;display:grid;grid-template-columns:minmax(8em,13em) 1fr;gap:6px 14px}
dl.detail dt{color:#555;font-weight:600}
dl.detail dd{margin:0;min-width:0}
dl.detail ul{margin:0;padding-left:18px}
pre.words{white-space:pre-wrap;word-break:break-word;font:13px/1.5 ui-monospace,Consolas,monospace;background:#f6f3ec;padding:8px 10px;border-radius:4px;margin:0}
.addr{font-family:ui-monospace,Consolas,monospace;word-break:break-all}
button.copy{padding:1px 8px;font-size:12px;margin-left:6px;background:#fff}
.offscreen{position:fixed;left:-9999px;top:0}
.sw{display:inline-block;width:14px;height:10px;border:2px solid;border-radius:2px;vertical-align:middle;margin:0 4px 0 10px;background:#fffdf7}
.ln{display:inline-block;width:28px;height:0;border-top:2px solid #9c8f74;vertical-align:middle;margin:0 6px 0 10px}
.ln-l{border-top-style:dashed}.ln-u{border-top-style:dotted}
.treewrap.js{height:62vh;min-height:320px}
.chartbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font:13px system-ui,sans-serif;margin:8px 0 0}
.chartbar button{font:13px system-ui,sans-serif;padding:3px 10px;border:1px solid #b8ad96;background:#fff;border-radius:4px}
.treebar form{display:inline-flex;gap:6px;align-items:center}
.node{cursor:pointer}
.node:focus{outline:none}
.node:focus rect{stroke-width:4}
@media print{.banner.private{background:none!important;color:#7a1f1f!important;border:3px solid #7a1f1f}.badge.living{background:none!important;color:#7a1f1f!important;border:2px solid #7a1f1f}}
@media (max-width:640px){main{padding:12px 16px 40px}header{padding:14px 16px}footer{padding:10px 16px 30px}dl.detail{grid-template-columns:1fr}.card{padding:14px}}
"""

APP_CSS = (maxgen_db.PAGE_CSS + HUB_CSS
           + "".join(".chip.b-%s{background:%s;color:%s}\n.sw-%s{border-color:%s}\n"
                     % (cls, colour, _ink(colour), cls, colour) for cls, colour in maxgen_db.BAND_COLOUR.items()))

APP_JS = r"""(function () {
'use strict';
var ID = /^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$/;
var LINEAGES = ['preferred', 'birth', 'adoptive', 'all'];
var PICK = /^([0-9A-Fa-f-]{36}:[0-9A-Fa-f-]{36})(,[0-9A-Fa-f-]{36}:[0-9A-Fa-f-]{36})*$/;
var READABLE_SCALE = 1;          /* one chart unit per screen pixel: the boxes' words are readable at first open */
var BOX_H = 56;
function byId(id) { return document.getElementById(id); }
function each(list, fn) { Array.prototype.forEach.call(list || [], fn); }
function say(el, message, detail) { if (!el) return; el.textContent = message; el.title = detail || ''; }

/* Where a click on a chart box goes: that person's page, same generations, same line, same picks. */
function chartAddress(id) {
  var sec = byId('chart'), sel = byId('gens');
  var g = sel ? parseInt(sel.value, 10) : 5;
  if (!(g >= 1 && g <= 8)) g = 5;
  var url = '/person/' + id + '?gens=' + g;
  var lin = sec ? sec.getAttribute('data-lineage') : '';
  if (LINEAGES.indexOf(lin) > 0) url += '&lineage=' + lin;
  var pick = sec ? (sec.getAttribute('data-pick') || '') : '';
  if (pick && PICK.test(pick)) url += '&pick=' + pick;
  return url;
}

/* One chart: pan, zoom and open a person. The viewBox maths is the kit page's, rewritten. */
function oneChart(host) {
  var svg = host.querySelector('svg');
  if (!svg) return;
  svg.setAttribute('role', 'group');   /* not 'img': the boxes inside are links a screen reader must reach */
  var W = parseFloat(svg.getAttribute('width')) || 1, H = parseFloat(svg.getAttribute('height')) || 1;
  var vb = {x: 0, y: 0, w: W, h: H};
  function setVB() { svg.setAttribute('viewBox', vb.x + ' ' + vb.y + ' ' + vb.w + ' ' + vb.h); }
  function fit() { vb = {x: 0, y: 0, w: W, h: H}; setVB(); }
  /* First open: readable boxes, the root at the left edge and in the middle; the whole chart only
     when it already fits at that size. */
  function readable() {
    var r = host.getBoundingClientRect();
    if (!r.width || !r.height) { fit(); return; }
    var fitScale = Math.min(r.width / W, r.height / H);
    if (fitScale >= READABLE_SCALE * 0.85) { fit(); return; }
    var w = r.width / READABLE_SCALE, h = r.height / READABLE_SCALE;
    var root = svg.querySelector('g.root rect');
    var cy = root ? parseFloat(root.getAttribute('y')) + BOX_H / 2 : H / 2;
    var y = Math.max(0, Math.min(cy - h / 2, Math.max(0, H - h)));
    vb = {x: 0, y: y, w: w, h: h};
    setVB();
  }
  function zoomAt(k, px, py) {
    if (px === null) { px = vb.x + vb.w / 2; py = vb.y + vb.h / 2; }
    vb.x = px - (px - vb.x) * k; vb.y = py - (py - vb.y) * k; vb.w *= k; vb.h *= k; setVB();
  }
  var tools = document.createElement('div');
  tools.className = 'chartbar zoom';
  var add = function (txt, label, fn) {
    var b = document.createElement('button');
    b.type = 'button'; b.textContent = txt; b.setAttribute('aria-label', label);
    b.addEventListener('click', fn); tools.appendChild(b);
  };
  add('+', 'Zoom in', function () { zoomAt(1 / 1.25, null, null); });
  add('−', 'Zoom out', function () { zoomAt(1.25, null, null); });
  add('Readable size', 'Boxes at a size you can read', readable);
  add('Whole chart', 'Show the whole chart', fit);
  host.parentNode.insertBefore(tools, host);
  host.className += ' js';
  function open(id) { if (ID.test(id)) window.location.assign(chartAddress(id)); }
  function point(ev) {
    var r = host.getBoundingClientRect(), sc = Math.max(vb.w / r.width, vb.h / r.height);
    var ox = (r.width - vb.w / sc) / 2, oy = (r.height - vb.h / sc) / 2;
    return {x: vb.x + (ev.clientX - r.left - ox) * sc, y: vb.y + (ev.clientY - r.top - oy) * sc, sc: sc};
  }
  var drag = null;
  host.addEventListener('pointerdown', function (ev) {
    if (ev.button !== 0 && ev.pointerType === 'mouse') return;
    drag = {id: ev.pointerId, x: ev.clientX, y: ev.clientY, vx: vb.x, vy: vb.y, moved: 0, target: ev.target};
    if (host.setPointerCapture) { try { host.setPointerCapture(ev.pointerId); } catch (e) {} }
    ev.preventDefault();
  });
  host.addEventListener('pointermove', function (ev) {
    if (!drag || drag.id !== ev.pointerId) return;
    var sc = point(ev).sc, dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    drag.moved = Math.max(drag.moved, Math.abs(dx) + Math.abs(dy));
    vb.x = drag.vx - dx * sc; vb.y = drag.vy - dy * sc; setVB();
  });
  host.addEventListener('pointerup', function (ev) {
    if (!drag || drag.id !== ev.pointerId) return;
    var d = drag; drag = null;
    if (d.moved >= 4) return;
    var g = d.target;
    while (g && g !== host && !(g.getAttribute && g.getAttribute('data-id'))) g = g.parentNode;
    if (g && g !== host) open(g.getAttribute('data-id'));
  });
  host.addEventListener('pointercancel', function () { drag = null; });
  host.addEventListener('wheel', function (ev) {
    ev.preventDefault();
    var p = point(ev);
    zoomAt(ev.deltaY < 0 ? 1 / 1.2 : 1.2, p.x, p.y);
  }, {passive: false});
  each(svg.querySelectorAll('g[data-id]'), function (g) {
    g.setAttribute('tabindex', '0');
    g.setAttribute('role', 'link');
    g.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(g.getAttribute('data-id')); }
    });
  });
  readable();
}

function charts() {
  each(document.querySelectorAll('.treewrap[data-chart]'), oneChart);
  var go = byId('gens-go'), sel = byId('gens'), form = byId('gens-form');
  if (go) go.hidden = true;
  if (sel && form) sel.addEventListener('change', function () { form.submit(); });
}

/* The tabs: one panel at a time once the script runs; every panel shows without it. */
function tabs() {
  var bar = byId('tabs');
  if (!bar) return;
  var buttons = bar.querySelectorAll('button[data-tab]');
  if (!buttons.length) return;
  function keys() { var k = []; each(buttons, function (b) { k.push(b.getAttribute('data-tab')); }); return k; }
  function show(key, remember) {
    if (keys().indexOf(key) < 0) key = buttons[0].getAttribute('data-tab');
    each(buttons, function (b) {
      var k = b.getAttribute('data-tab'), on = (k === key), p = byId('panel-' + k);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      b.tabIndex = on ? 0 : -1;
      if (p) p.hidden = !on;
    });
    if (remember) {
      try {
        var u = new URL(window.location.href);
        u.searchParams.set('tab', key);
        u.searchParams.delete('saved');
        window.history.replaceState(null, '', u.pathname + u.search + u.hash);
      } catch (e) {}
    }
  }
  each(buttons, function (b) { b.addEventListener('click', function () { show(b.getAttribute('data-tab'), true); }); });
  bar.addEventListener('keydown', function (ev) {
    if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft') return;
    var k = keys(), active = document.activeElement, cur = active ? k.indexOf(active.getAttribute('data-tab')) : -1;
    if (cur < 0) return;
    var next = (cur + (ev.key === 'ArrowRight' ? 1 : k.length - 1)) % k.length;
    show(k[next], true); buttons[next].focus(); ev.preventDefault();
  });
  function follow(hash) {
    var id = String(hash || '').replace(/^#/, '');
    if (!id) return;
    var el = byId(id);
    if (!el) return;
    if (el.getAttribute('role') === 'tab') { show(el.getAttribute('data-tab'), true); el.focus(); return; }
    var panel = el.closest ? el.closest('[role=tabpanel]') : null;
    if (!panel) return;
    show(panel.getAttribute('data-tab'), true);
    if (el.tagName === 'DETAILS') el.open = true;
    el.scrollIntoView({block: 'start'});
  }
  show(bar.getAttribute('data-active') || '', false);
  follow(window.location.hash);
  window.addEventListener('hashchange', function () { follow(window.location.hash); });
  document.addEventListener('click', function (ev) {
    var a = ev.target && ev.target.closest ? ev.target.closest('a[href^="#"]') : null;
    if (a) { var h = a.getAttribute('href'); window.setTimeout(function () { follow(h); }, 0); }
  });
}

/* Copy an address: the person pastes it into their own browser, where they are logged in. */
function copyButtons() {
  function fallback(text) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.className = 'offscreen';
    document.body.appendChild(ta); ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  each(document.querySelectorAll('button.copy'), function (b) {
    b.hidden = false;
    b.addEventListener('click', function () {
      var t = b.previousElementSibling, text = t ? t.textContent : '';
      var done = function (ok) {
        b.textContent = ok ? 'Copied' : 'Select it and copy it';
        window.setTimeout(function () { b.textContent = 'Copy address'; }, 2000);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(fallback(text)); });
      } else { done(fallback(text)); }
    });
  });
}

/* Every write: only adds; the server checks everything again (token, origin, the kit's gates). */
function sender(form, status, action, collect) {
  if (!form || !status) return;
  var buttons = form.querySelectorAll('button[type=submit]');
  var pressed = null;
  each(buttons, function (b) {
    b.disabled = false;
    b.addEventListener('click', function () { pressed = b; });
  });
  function setAll(on) { each(buttons, function (b) { b.disabled = !on; }); }
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var data = collect(form, status, ev.submitter || pressed);
    if (!data) return;
    var meta = document.querySelector('meta[name="pp-token"]');
    var token = meta ? meta.getAttribute('content') : '';
    say(status, 'Saving…', '');
    setAll(false);
    fetch('/api/' + action, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
                             referrerPolicy: 'same-origin', redirect: 'error',
                             headers: {'Content-Type': 'application/json', 'X-PP-Token': token},
                             body: JSON.stringify(data)})
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function (j) {
        if (j && j.ok === true && typeof j.redirect === 'string' && j.redirect.indexOf('/person/') === 0) {
          window.location.assign(j.redirect);
          return;
        }
        setAll(true);
        say(status, (j && typeof j.message === 'string') ? j.message : 'Something went wrong. Reload the page and try again.',
            (j && typeof j.detail === 'string') ? j.detail : '');
      }, function () {
        setAll(true);
        say(status, 'Probable Pedigree is not answering. Is its window still open?', '');
      });
  });
}

function field(form, name) { var el = form.querySelector('[name="' + name + '"]'); return el ? el.value : ''; }

function forms() {
  var ask = byId('ask-form');
  sender(ask, byId('ask-status'), 'ask', function (form, status) {
    var text = field(form, 'request').trim();
    if (!text) { say(status, 'Please type what you would like the Genealogist to look into.', ''); return null; }
    return {person_id: field(form, 'person_id'), request: text};
  });
  var factSel = byId('add-fact'), value = byId('add-value');
  function shape() {
    if (!factSel || !value) return;
    var year = factSel.value !== 'name';
    value.setAttribute('maxlength', year ? '4' : '200');
    value.setAttribute('inputmode', year ? 'numeric' : 'text');
    value.setAttribute('placeholder', year ? 'Four digits, like 1922' : 'As it is written');
  }
  if (factSel) { factSel.addEventListener('change', shape); shape(); }
  sender(byId('add-form'), byId('add-status'), 'add-fact', function (form, status) {
    var fact = field(form, 'fact'), v = field(form, 'value').trim();
    var how = '';
    each(form.querySelectorAll('input[name="how_known"]'), function (r) { if (r.checked) how = r.value; });
    if (!v) { say(status, 'Please type what you know.', ''); return null; }
    if (fact !== 'name' && !/^[0-9]{4}$/.test(v)) { say(status, 'Please type a four-digit year, like 1922.', ''); return null; }
    if (!how) { say(status, 'Please choose how you know.', ''); return null; }
    var who = (byId('person-name') || {}).textContent || 'this person';
    var what = (factSel && factSel.selectedIndex >= 0) ? factSel.options[factSel.selectedIndex].text : 'This';
    if (!window.confirm(what + ': ' + v + ' for ' + who + '. It is kept for good; this page cannot take it back. Add it?')) {
      say(status, 'Nothing was added.', ''); return null;
    }
    return {person_id: field(form, 'person_id'), fact: fact, value: v, how_known: how,
            link: field(form, 'link').trim(), note: field(form, 'note')};
  });
  each(document.querySelectorAll('form.prefer-form'), function (form) {
    sender(form, form.querySelector('.status'), 'prefer', function (f, status) {
      var who = (byId('person-name') || {}).textContent || 'this person';
      if (!window.confirm('Show these ' + field(f, 'kind').toLowerCase() + ' as the main parents of ' + who +
                          '? The other parent sets are kept, and you can change this again.')) {
        say(status, 'Nothing was changed.', ''); return null;
      }
      return {person_id: field(f, 'person_id'), membership_id: field(f, 'membership_id')};
    });
  });
  each(document.querySelectorAll('form.decide-form'), function (form) {
    sender(form, form.querySelector('.status'), 'decide', function (f, status, button) {
      var words = field(f, 'words').trim(), verdict = button ? button.value : '';
      if (['accepted', 'rework', 'rejected'].indexOf(verdict) < 0) { say(status, 'Please press one of the three buttons.', ''); return null; }
      if (!words) { say(status, 'Please write, in your own words, why you decide this way.', ''); return null; }
      return {person_id: field(f, 'person_id'), task_id: field(f, 'task_id'), verdict: verdict, words: words};
    });
  });
}

function start() { charts(); tabs(); copyButtons(); forms(); }
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
"""


# ------------------------------------------------------------------ writing (only adds, only through maxgen_db.py)

class WriteRefused(Exception):
    pass


@contextlib.contextmanager
def writer(server):
    """One write transaction through maxgen_db.py's own context and gates, under the person page's
    own human session. A refusal rolls everything back."""
    with WRITE_LOCK, contextlib.redirect_stdout(io.StringIO()):
        try:
            ctx = maxgen_db.Ctx(server.db, server.schemas, write=True, quiet=True)
        except maxgen_db.Refused as e:
            maxgen_db.close_all()    # under WRITE_LOCK the only kit connection open is this one
            raise WriteRefused(str(e))
        except sqlite3.Error as e:   # e.g. the file is locked by a batch that is writing
            maxgen_db.close_all()
            raise WriteRefused("the kit could not open the file for writing: %s" % type(e).__name__)
        try:
            maxgen_db.ensure_person_session(ctx, "person_page")
            yield ctx
            if ctx.write:
                ctx.commit()
        except maxgen_db.Refused as e:
            ctx.rollback()
            raise WriteRefused(str(e))
        except sqlite3.Error as e:   # e.g. the file is locked by a batch that is writing
            ctx.rollback()
            raise WriteRefused("the kit could not write: %s" % type(e).__name__)
        finally:
            ctx.close()


def session_task(server, ctx, pid, name, living) -> str:
    """The person page's session task for this person, made on the first write of this run."""
    tid = server.session_tasks.get(pid)
    if tid and ctx.row("task", tid):
        return tid
    tid = str(uuid.uuid4())
    doc = {"task_id": tid, "schema_version": maxgen_db.MAXGEN_VERSION, "task_type": "other", "status": "done",
           "priority": 5, "is_living_flag": bool(living), "contributor_eligibility": "first_party_only",
           "payload": {"kind": "person_page_session", "person_id": pid, "name_as_known": name,
                       "asked_from": "Probable Pedigree person page"},
           "result": {"verdict": "infra", "description": "What the person added on their own page",
                      "verdict_reason": "Kept as the person's own words, each capped at 0.4."}}
    maxgen_db.add_document(ctx, doc, quiet=True)
    server.session_tasks[pid] = tid
    return tid


def fail(message: str, detail="") -> dict:
    if isinstance(detail, (list, tuple)):
        detail = "; ".join(str(x) for x in detail)
    return {"ok": False, "message": message, "detail": _text(detail)}


def _bad_text(s: str) -> bool:
    return "\x00" in s


def read_person(server, pid):
    """(person, name, living) read fresh, immediately before a write; None when not there."""
    con = ro_connect(server.db)
    try:
        r = Reader(con)
        person = load_person(r, pid)
        if not person:
            return None
        return person, r.name(person["person_id"]), r.living(person["person_id"])
    finally:
        con.close()


def ask_genealogist(server, data: dict):
    pid, req = data.get("person_id"), data.get("request")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        return 404, fail(NO_PERSON)
    if not isinstance(req, str) or not req.strip():
        return 400, fail("Please type what you would like the Genealogist to look into.")
    req = req.strip()
    if _bad_text(req):
        return 400, fail("Please remove the unusual characters and try again.")
    if len(req) > MAX_REQUEST:
        return 400, fail(TOO_LONG)
    with WRITE_LOCK:
        found = read_person(server, pid)
        if not found:
            return 404, fail(NO_PERSON)
        person, name, living = found
        tid = str(uuid.uuid4())
        doc = {"task_id": tid, "schema_version": maxgen_db.MAXGEN_VERSION, "task_type": "other", "status": "pending",
               "priority": 5, "is_living_flag": bool(living), "contributor_eligibility": "first_party_only",
               "payload": {"kind": "person_request", "person_id": person["person_id"], "name_as_known": name,
                           "request": req, "asked_from": "Probable Pedigree person page"},
               "acceptance_criteria": ["The Orchestrator has read this request and either planned research "
                                       "tasks for it or told the person why not."]}
        try:
            with writer(server) as ctx:
                maxgen_db.add_document(ctx, doc, quiet=True)
        except WriteRefused as e:
            return 400, fail(REFUSED, str(e))
    return 200, {"ok": True, "task_id": tid, "redirect": "/person/%s?saved=ask&tab=research" % person["person_id"]}


def link_ok(s: str) -> bool:
    if len(s) > MAX_LINK or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in s):
        return False
    lows = s.lower()
    if not (lows.startswith("http://") or lows.startswith("https://")):
        return False
    try:
        parts = urlsplit(s)
        # no user name or password in it: RULES 10, never store a password
        return bool(parts.hostname) and parts.username is None and parts.password is None
    except ValueError:
        return False


def add_fact(server, data: dict):
    pid = data.get("person_id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        return 404, fail(NO_PERSON)
    fact = data.get("fact")
    if fact not in FACT_LIST:
        return 400, fail("Please choose what it is: a name, a year of birth or a year of death.")
    value = data.get("value")
    if not isinstance(value, str) or not value.strip():
        return 400, fail("Please type what you know.")
    value = value.strip()
    if fact == "name":
        if _bad_text(value):
            return 400, fail("Please remove the unusual characters and try again.")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            return 400, fail("Please type the name on one line.")
        if len(value) > MAX_NAME:
            return 400, fail("That name is too long. Please keep it under %d letters." % MAX_NAME)
    else:
        if not re.fullmatch(r"[0-9]{4}", value):
            return 400, fail("Please type a four-digit year, like 1922.")
        year, this_year = int(value), datetime.now(timezone.utc).year
        if not 1000 <= year <= this_year:
            return 400, fail("Please type a year between 1000 and %d." % this_year)
    how = data.get("how_known")
    if how not in WORDS["how_known"]:
        return 400, fail("Please choose how you know.")
    link = data.get("link") if data.get("link") is not None else ""
    if not isinstance(link, str):
        return 400, fail("Please paste the link as it is written, or leave it empty.")
    link = link.strip()
    if link and not link_ok(link):
        return 400, fail("The link must be a web address that starts with http:// or https://, with no spaces "
                         "and no user name or password in it.")
    note = data.get("note") if data.get("note") is not None else ""
    if not isinstance(note, str):
        return 400, fail("Please type the note as plain words.")
    if len(note) > MAX_NOTE:
        return 400, fail("The note is too long. Please keep it under %d letters." % MAX_NOTE)
    if _bad_text(note):
        return 400, fail("Please remove the unusual characters from the note and try again.")
    note = note.strip()

    with WRITE_LOCK:
        found = read_person(server, pid)          # re-read now, immediately before adding
        if not found:
            return 404, fail(NO_PERSON)
        person, name, living = found
        if person["stage"] == "rejected":
            return 409, fail(TURNED_DOWN_ADD)
        stored_id, ts = person["person_id"], maxgen_db.now()
        rid = str(uuid.uuid4())
        typed = "%s: %s" % (WORDS["fact"][fact], value)
        # every line of the note keeps its words; lines after the first are indented, so nothing typed
        # can look like a labelled line of the document
        note_text = "\n  ".join(note.splitlines()) if note else "none"
        text = "\n".join([
            "[1] %s" % typed,
            "[2] How they know: %s" % WORDS["how_known"][how],
            "[3] Link: %s" % (link or "none given"),
            "[4] Note: %s" % note_text,
            "[5] Typed on the Probable Pedigree page on %s about %s." % (local_day(ts), name)])
        record = {"record_id": rid, "schema_version": maxgen_db.MAXGEN_VERSION, "record_type": "other",
                  "record_type_as_written": "Typed on the person page", "redistribution_license": "tier2-private",
                  "is_living_flag": True,      # their own words may name living relatives: fails closed
                  "source_classification": {"source": "authored", "information": "secondary"},
                  "source_url": "urn:uuid:" + rid,   # the source is their words; a link is kept beside them
                  "transcription_parts": [{"part_id": "p1", "text": text}],
                  "transcription_scope": "complete_entry", "extraction_confidence": 1.0,
                  "persons_mentioned": [{"mention_id": "m1", "name_as_written": name, "role": "principal",
                                         "role_as_written": "the person this note is about",
                                         "is_living": bool(living), "lines": ["1", "5"]}],
                  "extensions": {"person_page": dict({"fact": fact, "how_known": how, "program": PROGRAM},
                                                     **({"link": link} if link else {}))}}
        evidence = {"basis": "document", "source_record_id": rid, "mention_id": "m1", "quote": typed, "kind": "direct"}
        claim = {"confidence": PERSON_CLAIM_CAP, "evidence": evidence}
        if fact == "name":
            claim["name_as_written"] = value
        else:
            claim["date"] = {"value": {"year": int(value)}, "as_written": value}
        claims = {"person_id": stored_id, FACT_LIST[fact]: [claim]}
        try:
            with tempfile.TemporaryDirectory(prefix="pp-add-") as d, writer(server) as ctx:
                tid = session_task(server, ctx, stored_id, name, living)
                maxgen_db.add_document(ctx, record, tid, quiet=True)
                p = Path(d) / "claims.json"
                p.write_text(json.dumps(claims), encoding="utf-8")
                maxgen_db.cmd_claim(ctx, argparse.Namespace(file=str(p), task=tid))
        except WriteRefused as e:
            server.session_tasks.pop(stored_id, None)
            return 400, fail(NOT_STORABLE, str(e))
    return 200, {"ok": True, "record_id": rid, "redirect": "/person/%s?saved=fact" % stored_id}


def prefer_set(server, data: dict):
    """'Make these the main parents': maxgen_db's `prefer` under the page's session task."""
    pid, mid = data.get("person_id"), data.get("membership_id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        return 404, fail(NO_PERSON)
    if not isinstance(mid, str) or not ID_RE.match(mid):
        return 409, fail(GONE_SET)
    with WRITE_LOCK:
        con = ro_connect(server.db)
        try:
            r = Reader(con)
            person = load_person(r, pid)
            if not person:
                return 404, fail(NO_PERSON)
            if person["stage"] == "rejected":
                return 409, fail(TURNED_DOWN_ADD)
            sets = {s["membership_id"]: s for s in maxgen_db.parent_sets(r.rc, person["person_id"])}
            chosen = sets.get(mid.lower())
            name, living = r.name(person["person_id"]), r.living(person["person_id"])
            parents = ", ".join(r.name(p["person_id"]) for p in (chosen or {}).get("parents", []))
        finally:
            con.close()
        if not chosen or chosen["is_preferred"]:
            return 409, fail(GONE_SET)
        try:
            with writer(server) as ctx:
                tid = session_task(server, ctx, person["person_id"], name, living)
                if chosen["set_kind"] == "adoptive":
                    # pressing the button (after the page's own question) is the person's yes (R-31)
                    maxgen_db.record_person_review(
                        ctx, tid, "accepted", "Make these the main parents: %s (%s), chosen on my own page."
                        % (parents or "no parent named", maxgen_db.SET_WORDS["adoptive"].lower()),
                        channel="person_page")
                maxgen_db.cmd_prefer(ctx, argparse.Namespace(membership_id=chosen["membership_id"], task=tid))
        except WriteRefused as e:
            server.session_tasks.pop(person["person_id"], None)
            return 400, fail(REFUSED, str(e))
    return 200, {"ok": True, "redirect": "/person/%s?saved=prefer" % person["person_id"]}


def decide(server, data: dict):
    """'Decisions waiting for you': the person's own review of a proof, channel person_page."""
    pid, tid = data.get("person_id"), data.get("task_id")
    verdict, words = data.get("verdict"), data.get("words")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        return 404, fail(NO_PERSON)
    if not isinstance(tid, str) or not ID_RE.match(tid):
        return 409, fail(GONE_DECISION)
    if verdict not in WORDS["answer"]:
        return 400, fail("Please press one of the three buttons: Accept, Needs more work or Reject.")
    if not isinstance(words, str) or not words.strip():
        return 400, fail("Please write, in your own words, why you decide this way.")
    words = words.strip()
    if len(words) > MAX_NOTE:
        return 400, fail(TOO_LONG)
    if _bad_text(words):
        return 400, fail("Please remove the unusual characters and try again.")
    with WRITE_LOCK:
        con = ro_connect(server.db)
        try:
            r = Reader(con)
            person = load_person(r, pid)
            if not person:
                return 404, fail(NO_PERSON)
            waiting = {t["task_id"] for t in decisions_waiting(r, person["person_id"])}
        finally:
            con.close()
        if tid.lower() not in waiting:
            return 409, fail(GONE_DECISION)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                maxgen_db.record_person_page_review(server.db, tid.lower(), verdict, words, server.schemas)
        except maxgen_db.Refused as e:
            return 400, fail(REFUSED, str(e))
        except sqlite3.Error as e:
            return 400, fail(REFUSED, "the kit could not write: %s" % type(e).__name__)
    return 200, {"ok": True, "redirect": "/person/%s?saved=decision" % person["person_id"]}


POST_ROUTES = {"/api/ask": ask_genealogist, "/api/add-fact": add_fact, "/api/prefer": prefer_set,
               "/api/decide": decide}


# ------------------------------------------------------------------ HTTP

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = (("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
                    ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store"),
                    ("Cross-Origin-Opener-Policy", "same-origin"), ("Cross-Origin-Resource-Policy", "same-origin"))
HTML = "text/html; charset=utf-8"
JSON_TYPE = "application/json; charset=utf-8"


BUSY = {errno.EADDRINUSE, errno.EACCES, getattr(errno, "WSAEADDRINUSE", 10048), getattr(errno, "WSAEACCES", 10013)}


def reserve_ipv6_loopback(port: int):
    """Hold [::1]:port without listening, so a browser that tries ::1 first for 'localhost' is
    refused at once and falls back to 127.0.0.1, and no other program can answer there instead.
    Returns the socket, or None when this computer has no IPv6 loopback. Raises OSError when
    another program already holds [::1]:port, so the port counts as busy."""
    if not socket.has_ipv6:
        return None
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return None
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("::1", port))
        return s
    except OSError as e:
        s.close()
        if e.errno in BUSY or getattr(e, "winerror", None) in BUSY:
            raise
        return None


class HubServer(ThreadingHTTPServer):
    """Always 127.0.0.1. Never reuses a port another program holds."""
    daemon_threads = True
    allow_reuse_address = False
    allow_reuse_port = False

    def __init__(self, port: int, db=None, schemas=None):
        self.db = str(db) if db else None
        self.schemas = str(schemas) if schemas else None
        self.token = secrets.token_urlsafe(32)     # proves a write came from this run's page
        self.key = secrets.token_urlsafe(32)       # proves the browser was given the printed address
        self.reserve6 = None
        self.session_tasks = {}                    # person id -> this run's person_page_session task
        super().__init__(("127.0.0.1", port), Handler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):     # Windows: a busy port must fail, not be shared
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super(HTTPServer, self).server_bind()          # the plain TCP bind: no name lookup
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]
        self.reserve6 = reserve_ipv6_loopback(self.server_port)   # busy there: TCPServer closes us, raises

    def server_close(self):
        super().server_close()
        if self.reserve6 is not None:
            self.reserve6.close()
            self.reserve6 = None

    def open_address(self) -> str:
        return "http://127.0.0.1:%d/open?key=%s" % (self.server_address[1], self.key)

    def cookie_pair(self) -> str:
        return "pp_%d=%s" % (self.server_address[1], self.key)


class Handler(BaseHTTPRequestHandler):
    server_version = "ProbablePedigree"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    default_request_version = "HTTP/1.0"   # every refusal carries a status line and the security headers
    timeout = 30

    def version_string(self):
        return self.server_version

    def log_message(self, format, *args):   # request paths hold person ids: no request log
        pass

    def end_headers(self):
        for k, v in SECURITY_HEADERS:
            self.send_header(k, v)
        super().end_headers()

    # one request: Host first, then the route, whatever the method
    def handle_one_request(self):
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = self.request_version = self.command = ""
                self.send_error(414)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return
            self.dispatch()
            self.wfile.flush()
        except (TimeoutError, socket.timeout):
            self.close_connection = True

    def dispatch(self):
        self._body = None
        if len(self.requestline.split()) != 3:   # an HTTP/0.9-style line: refuse, never serve a page
            self.close_connection = True
            return self.send_error(400)
        if self.command == "POST":
            self._body = self.read_body()
        if not self.host_ok():
            if self.command == "POST":
                return self.send_json(403, fail(FORBIDDEN))
            return self.send_page(403, render_simple(self.server, "Wrong address", HOST_ONLY, bare=True))
        try:
            if self.command == "GET":
                self.do_get()
            elif self.command == "POST":
                self.do_post()
            else:
                self.send_page(405, render_simple(self.server, "Not allowed",
                                                  "This page can only be read, or added to through its forms.",
                                                  bare=True),
                               extra=(("Allow", "GET, POST"),))
        except ConnectionError:  # the browser went away mid-answer: nothing to say
            self.close_connection = True
        except Exception as e:   # one plain line; never a document, a name or the token
            if sys.stderr:   # None when started without a console window (pythonw)
                sys.stderr.write("Probable Pedigree: could not answer a request (%s).\n" % type(e).__name__)
            try:
                if self.command == "POST":
                    self.send_json(500, fail("Something went wrong, and nothing was changed. "
                                             "Reload the page and try again."))
                else:
                    self.send_page(500, render_simple(self.server, "Something went wrong",
                                                      "Nothing was changed. Try reloading the page."))
            except Exception:
                pass

    def host_ok(self) -> bool:
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1:
            return False
        port = self.server.server_address[1]
        return hosts[0].strip().lower() in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def key_ok(self) -> bool:
        """The browser holds this run's key (set by /open). Any program can send the token and an
        Origin it makes up; only someone given the printed address has the key."""
        want = self.server.cookie_pair().encode("ascii")
        have = ";".join(self.headers.get_all("Cookie") or []).split(";")
        return any(hmac.compare_digest(c.strip().encode("utf-8", "replace"), want) for c in have)

    def refuse_key(self):
        return self.send_page(403, render_simple(self.server, NO_KEY_TITLE, NO_KEY, bare=True))

    # -- responses
    def send_body(self, code: int, body: bytes, ctype: str, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def send_page(self, code: int, html: str, extra=()):
        self.send_body(code, html.encode("utf-8"), HTML, extra)

    def send_json(self, code: int, obj: dict):
        self.send_body(code, json.dumps(obj).encode("utf-8"), JSON_TYPE)

    def redirect(self, location: str):
        self.send_body(303, b"", HTML, (("Location", location),))

    # -- GET
    def do_get(self):
        parts = urlsplit(self.path)
        path = parts.path
        try:
            query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=20)
        except ValueError:
            query = {}
        if path == "/app.css":
            return self.send_body(200, APP_CSS.encode("utf-8"), "text/css; charset=utf-8")
        if path == "/app.js":
            return self.send_body(200, APP_JS.encode("utf-8"), "text/javascript; charset=utf-8")
        if path == "/favicon.ico":
            return self.send_body(204, b"", "text/plain; charset=utf-8")
        if path == "/open":
            given = (query.get("key") or [""])[0]
            if hmac.compare_digest(given.encode("utf-8", "replace"), self.server.key.encode("ascii")):
                cookie = self.server.cookie_pair() + "; HttpOnly; SameSite=Strict; Path=/"
                return self.send_body(303, b"", HTML, (("Location", "/"), ("Set-Cookie", cookie)))
            if self.key_ok():
                return self.redirect("/")
            return self.refuse_key()
        if not self.key_ok():                # the style and the script hold no data; everything else does
            return self.refuse_key()
        if path == "/person":
            pid = (query.get("id") or [""])[0].strip()
            if ID_RE.match(pid):
                return self.redirect("/person/" + pid)
            return self.send_page(404, render_not_found(self.server))
        if path != "/" and not path.startswith("/person/"):
            return self.send_page(404, render_simple(self.server, "Not found", "There is no page at that address."))
        pid = path[len("/person/"):] if path.startswith("/person/") else ""
        if path != "/" and not ID_RE.match(pid):
            return self.send_page(404, render_not_found(self.server))
        con = ro_connect(self.server.db)
        try:
            r = Reader(con)
            if path == "/":
                root = maxgen_db.default_root(maxgen_db.chart_persons(r.rc, "preferred"))
                if not root:
                    row = con.execute("SELECT person_id FROM max_person WHERE lifecycle='active' "
                                      "ORDER BY lower(coalesce(display_name,'')), person_id LIMIT 1").fetchone()
                    root = row["person_id"] if row else None
                if root and ID_RE.match(root):
                    return self.redirect("/person/" + root)
                return self.send_page(200, render_empty_page(self.server))
            person = load_person(r, pid)
            if not person:
                return self.send_page(404, render_not_found(self.server))
            if person["merged_from"]:        # a merged-away id forwards to the person it was merged into
                return self.redirect("/person/" + person["person_id"] + (("?" + parts.query) if parts.query else ""))
            code, html = render_person_page(self.server, r, person, query)
            return self.send_page(code, html)
        finally:
            con.close()

    # -- POST
    def drain(self, limit: int = MAX_BODY):
        """Read and drop whatever was sent without a usable length, so closing the connection
        does not cut off the answer."""
        try:
            self.connection.settimeout(0.3)
            while limit > 0:
                chunk = self.rfile.read1(min(limit, 65536))
                if not chunk:
                    break
                limit -= len(chunk)
                self.connection.settimeout(0.05)
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.connection.settimeout(self.timeout)
            except OSError:
                pass

    def read_body(self):
        """Read the body before answering, so a refused request still gets its answer cleanly."""
        self._length = "missing"
        raw = self.headers.get("Content-Length")
        if raw is None or self.headers.get("Transfer-Encoding"):
            self.drain()
            return None
        try:
            n = int(raw.strip())
        except ValueError:
            n = -1
        if n < 0:
            self._length = "bad"
            self.drain()
            return None
        if n > MAX_BODY:
            self._length = "big"
            try:
                left = min(n, 1 << 20)
                while left > 0:
                    chunk = self.rfile.read(min(left, 65536))
                    if not chunk:
                        break
                    left -= len(chunk)
            except OSError:
                pass
            return None
        data = self.rfile.read(n) if n else b""
        if len(data) != n:
            self._length = "bad"
            return None
        self._length = "ok"
        return data

    def token_ok(self) -> bool:
        given = self.headers.get("X-PP-Token") or ""
        return hmac.compare_digest(given.encode("utf-8", "replace"), self.server.token.encode("ascii"))

    def origin_ok(self) -> bool:
        expected = "http://" + (self.headers.get("Host") or "").strip().lower()
        origins = self.headers.get_all("Origin") or []
        if len(origins) > 1:
            return False
        origin_ = origins[0].strip().lower() if origins else None
        if origin_ is not None and origin_ != "null":
            return origin_ == expected
        return (self.headers.get("Referer") or "").strip().lower().startswith(expected + "/")

    def fetch_site_ok(self) -> bool:
        v = self.headers.get("Sec-Fetch-Site")
        return v is None or v.strip().lower() == "same-origin"

    def do_post(self):
        route = POST_ROUTES.get(urlsplit(self.path).path)
        if route is None:
            return self.send_json(404, fail("There is nothing to save at that address."))
        if not (self.key_ok() and self.token_ok() and self.origin_ok() and self.fetch_site_ok()):
            return self.send_json(403, fail(FORBIDDEN))
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self.send_json(415, fail(FORBIDDEN))
        if self._length == "missing":
            return self.send_json(411, fail(TOO_LONG))
        if self._length == "big":
            return self.send_json(413, fail(TOO_LONG))
        try:
            if self._length != "ok":
                raise ValueError("bad length")
            data = json.loads((self._body or b"").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = None
        if not isinstance(data, dict):
            return self.send_json(400, fail("What was sent could not be read. Reload the page and try again."))
        code, payload = route(self.server, data)
        return self.send_json(code, payload)


# ------------------------------------------------------------------ starting

def check_db(db) -> str:
    """'' when db is a MAXGEN 2.0 kit database; otherwise one plain line saying what is wrong."""
    p = Path(db)
    if not p.is_file():
        return "%s does not exist. Make it first: python maxgen_db.py init %s" % (db, db)
    version = None
    try:
        con = ro_connect(p)
        try:
            names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
            if "meta" in names:
                row = con.execute("SELECT value FROM meta WHERE key='maxgen_version'").fetchone()
                version = row[0] if row else None
        finally:
            con.close()
    except sqlite3.Error:
        names = set()
    missing = [t for t in KIT_TABLES if t not in names]
    if "max_person" in names and "meta" not in names:
        return ("%s was made by a MAXGEN 1.x kit; this page reads MAXGEN 2.0. Start a new database with: "
                "python maxgen_db.py init genealogy.db" % db)
    if missing:
        return ("%s is not a genealogy.db made by maxgen_db.py (it has no %s table). "
                "Make one with: python maxgen_db.py init genealogy.db" % (db, missing[0]))
    if not str(version or "").startswith("2."):
        return ("%s was made by a MAXGEN 1.x kit; this page reads MAXGEN 2.0. Start a new database with: "
                "python maxgen_db.py init genealogy.db" % db)
    return ""


def find_schemas(arg=None):
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            return maxgen_db.schemas_dir(arg)
        except SystemExit:
            return None


def bind_with_fallback(start: int, tries: int = PORT_TRIES, db=None, schemas=None):
    """The first of start .. start+tries-1 that binds, or None."""
    for port in range(start, min(start + tries, 65536)):
        try:
            return HubServer(port, db, schemas)
        except OSError:
            continue
    return None


def make_server(db, port: int = 0, schemas=None) -> HubServer:
    """A ready server for db on 127.0.0.1 (port 0: the system picks a free one; tests only)."""
    problem = check_db(db)
    if problem:
        raise ValueError(problem)
    found = find_schemas(schemas)
    if found is None:
        raise ValueError("Cannot find the schemas folder. Pass --schemas <folder>.")
    if port:
        return HubServer(port, db, found)
    for attempt in range(5):     # the free IPv4 port may be held on [::1]: ask for another
        try:
            return HubServer(0, db, found)
        except OSError:
            if attempt == 4:
                raise
    raise OSError("no free port")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="probable_pedigree.py",
                                 description="The Probable Pedigree person page, on this computer only.")
    ap.add_argument("db", help="the genealogy.db the agents write")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="first port to try (default %d)" % DEFAULT_PORT)
    ap.add_argument("--schemas", help="folder holding the .schema.json files (as in maxgen_db.py)")
    return ap


def _ascii(s) -> str:
    return str(s).encode("ascii", "replace").decode("ascii")


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    problem = check_db(a.db)
    if problem:
        print(_ascii(problem))
        return 1
    found = find_schemas(a.schemas)
    if found is None:
        print("Cannot find the schemas folder. Pass --schemas <folder>.")
        return 1
    if not 1 <= a.port <= 65535:
        print("The port must be a number from 1 to 65535.")
        return 1
    server = bind_with_fallback(a.port, PORT_TRIES, db=a.db, schemas=found)
    if server is None:
        last = min(a.port + PORT_TRIES - 1, 65535)
        print(_ascii("Ports %d to %d are all in use. Try: python probable_pedigree.py %s --port %d"
                     % (a.port, last, a.db, a.port + 20)))
        return 1
    port = server.server_address[1]
    try:                     # print() does nothing without a console (pythonw); the socket still closes
        print(_ascii("Probable Pedigree %s - reading %s" % (__version__, Path(a.db).name)))
        if port != a.port:
            print("Port %d is being used by another program, so this one is using %d." % (a.port, port))
        print("Open this address in your web browser:  %s" % server.open_address())
        print("It works only on this computer. Nothing is sent anywhere.")
        print("The page shows living relatives. It is for you only.")
        print("The key in that address is yours alone. Anyone who has it can open the page while this "
              "window runs, so do not share it.")
        print("Leave this window open while you use the page. Press Ctrl+C here to stop.")
        if sys.stdout:
            sys.stdout.flush()
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
