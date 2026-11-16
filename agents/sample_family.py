#!/usr/bin/env python3
"""sample_family.py -- a MADE-UP family, written into a new MAXGEN 2.0 genealogy.db the kit's own way.

    python sample_family.py out.db [--schemas <folder>]

Every person, place, ship and address in it is invented: the Quillfeathers, Larches,
Larkspurs, Thistlewoods, Mossburys and Bramblecotts of Tumblebrook, in the invented country of
Erewhon. Every web address is on example.org, a domain reserved for examples. No real person,
no real family and no living person's data appears.

Every row goes in through maxgen_db.py's own commands (session, add, claim, link, prefer,
resolve, step, task, review), exactly as the agents would write it, so the sample passes every
gate. It is for trying Probable Pedigree (probable_pedigree.py) and for its tests. It refuses
to touch a file that exists.

What it shows:
  * an adopted person who can follow both lines: Ezra Quillfeather, born Ezra Larch in 1921 to
    Nell Larch (birth register), adopted in 1923 by Thaddeus and Clementine Quillfeather (a
    private decree, recorded only after the person's yes), with his birth line (Nell, then her
    parents Josiah and Martha Larch) and his adoptive line (Thaddeus and Clementine, then
    Obadiah and Prudence Quillfeather and Edmund and Rose Larkspur), each two generations up.
    His main parents are the adoptive ones (recorded with `prefer`, again after the person's
    yes); a bastardy bond gives a possible birth father who is not settled.
  * Mabel's birth family, her step-household (the 1940 census "Step-dau") and her 1942
    adoption by her step-father, as two parent sets in one family;
  * a godparent (Clementine at Mabel's baptism: one shared occasion, plus the standing tie);
  * a place whose county changed (Tumblebrook: Wrenshire until 31 March 1974, Mossdale from
    1 April 1974);
  * a voyage group (the brig Swallowtail, 1895) with its dated facts and two members;
  * research in every task state, searches that found nothing, a withdrawn claim, a person
    waiting for a check, a person turned down, and one proof waiting for the person's decision.
"""
# Copyright (c) 2026 OpenGenealogyAI Contributors. MIT License; see LICENSE.
import argparse
import contextlib
import io
import json
import re
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import maxgen_db  # noqa: E402

__version__ = "0.2.0"
MADE_UP = "Every person, place and address in this file is made up."
QC = "Quality Control"
V = "2.0"
NOTE = {"sample": MADE_UP}


def uid(key: str) -> str:
    """Stable ids: the same on every run, so tests can name things (spec 4.1 allows fixed ids
    in samples; no id is ever worked out from another object's id at run time)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ogai-sample-family/" + key))


# ------------------------------------------------------------------ small builders

def dt(y, m=None, d=None, as_written=None, derivation=None, note=None, **kw):
    """A date block as stated. A date with no words written needs a derivation."""
    v = {"year": y}
    if m:
        v["month"] = m
    if d:
        v["day"] = d
    out = {"value": v}
    if as_written:
        out["as_written"] = as_written
    if derivation:
        out["derivation"] = derivation
    elif not as_written:
        out["derivation"] = "interpreted"
    if note:
        out["derivation_note"] = note
    out.update(kw)
    return out


def worked_out(y1, y2, note):
    """A year of birth worked out from a stated age: between two years, calculated."""
    return {"value": {"year": y1}, "value2": {"year": y2}, "modifier": "between", "derivation": "calculated",
            "derivation_note": note}


def place(words):
    return {"place_as_written": words}


def men(mid, name, role_w, role=None, events=(), lines=(), living=False, age=None, **kw):
    d = {"mention_id": mid, "name_as_written": name, "role_as_written": role_w, "is_living": living}
    if role:
        d["role"] = role
    if events:
        d["in_event_refs"] = list(events)
    if lines:
        d["lines"] = list(lines)
    if age:
        d["age_as_written"] = age
    d.update(kw)
    return d


def ent(ref, etype, info, date=None, primary=False, informant=None, where=None):
    d = {"ref": ref, "event_type": etype, "information": info}
    if date:
        d["date"] = date
    if primary:
        d["is_primary"] = True
    if informant:
        d["informant_mention_id"] = informant
    if where:
        d["place"] = place(where)
    return d


def record(key, rtype, lines, mentions, events=(), licence="CC0", cls=("original", "primary"), living=False,
           url=None, locator=None, repository=None, scope="complete_entry", conf=0.95, **extra):
    """One MaxRecord: the words as written, line by line ([label] text), with every person named."""
    text = "\n".join("[%s] %s" % (label, words) for label, words in lines)
    d = {"record_id": uid(key), "schema_version": V, "record_type": rtype, "redistribution_license": licence,
         "is_living_flag": living, "source_classification": {"source": cls[0], "information": cls[1]},
         "transcription_parts": [{"part_id": "p1", "text": text}], "transcription_scope": scope,
         "extraction_confidence": conf, "persons_mentioned": list(mentions), "extensions": dict(NOTE)}
    if events:
        d["events_documented"] = list(events)
    if url:
        d["source_url"] = url
    if repository:
        d["repository_as_written"] = repository
    if locator:
        d["locator"] = locator
    d.update(extra)
    return d


def loc(words, **parts):
    return dict({"as_written": words}, **parts)


def ev(rkey, mid, quote, ref=None, kind="direct", **kw):
    """One evidence link: which record, whose line, the exact words."""
    d = {"basis": "document", "source_record_id": uid(rkey), "mention_id": mid, "quote": quote, "kind": kind}
    if ref:
        d["record_event_ref"] = ref
    d.update(kw)
    return d


def claim(evidence, conf, **fields):
    return dict(fields, confidence=conf, evidence=evidence)


R = "https://records.example.org/sample/"
CHAPEL = R + "mill-lane-chapel/"
OFFICE = "Erewhon Record Office (made up)"

# ------------------------------------------------------------------ documents (MaxRecords)

INTERVIEW = [
    ("h", "First interview with Robin Quillfeather, 1 October 2026 (a made-up sample)."),
    ("q1", "Q: What is your full name, and when were you born?"),
    ("a1", "A: Robin Quillfeather. I was born in 1988."),
    ("q2", "Q: Who are your parents?"),
    ("a2", "A: My dad was Alden Quillfeather. He died in 2019. My mum is June Quillfeather; she was a "
           "Bramblecott before she married. I think she was born in 1960."),
    ("q3", "Q: Who were your dad's parents?"),
    ("a3", "A: Grandad Ezra and Grandma Mabel Quillfeather, in Tumblebrook. Grandad Ezra was adopted as a "
           "baby, I was always told."),
    ("q4", "Q: And your mum's parents?"),
    ("a4", "A: Walter and Iris Bramblecott. Grandma Iris was born in 1933, I'm fairly sure."),
]


def census(key, year, url, district, sheet, rows):
    """A census household: one line per person, labelled with the page's own line number."""
    first, last = rows[0][0], rows[-1][0]
    lines = [("h", "Census of Erewhon, %d. Tumblebrook, district %s, sheet %s." % (year, district, sheet))]
    lines += [(str(n), words) for n, words, *_ in rows]
    mentions = [men(str(n), name, rw, role, ("e1",), (str(n),), age=age) for n, _w, name, rw, role, age in rows]
    return record(key, "census_household", lines, mentions,
                  [ent("e1", "census", "undetermined", dt(year, as_written=str(year)), primary=True,
                       where="Tumblebrook")],
                  licence="public-domain", cls=("original", "undetermined"), url=url,
                  locator=loc("%d census, Tumblebrook district %s, sheet %s, lines %d-%d" % (year, district, sheet,
                                                                                            first, last),
                              series="%d census" % year, district=district, sheet=sheet, line_first=first,
                              line_last=last),
                  repository=OFFICE, collection_as_written="%d census of Erewhon (made up)" % year, conf=0.9)


RECORDS = [
    record("rec_interview", "other", INTERVIEW,
           [men("i1", "Robin Quillfeather", "the person interviewed", "principal", lines=("h", "a1"), living=True),
            men("i2", "Alden Quillfeather", "my dad", "father", lines=("a2",)),
            men("i3", "June Quillfeather", "my mum", "mother", lines=("a2",), living=True),
            men("i4", "Ezra", "Grandad", "relative", lines=("a3",)),
            men("i5", "Mabel Quillfeather", "Grandma", "relative", lines=("a3",)),
            men("i6", "Walter Bramblecott", "mum's father", "relative", lines=("a4",)),
            men("i7", "Iris Bramblecott", "Grandma", "relative", lines=("a4",))],
           licence="tier2-private", cls=("authored", "secondary"), living=True,
           url="file:///genealogy/staging/interview_2026-10-01.md", conf=1.0,
           record_type_as_written="First interview, written down word for word",
           record_date=dt(2026, 10, 1, as_written="1 October 2026")),
    census("rec_census_1930", 1930, R + "census-1930/tumblebrook/0412", "2", "4", [
        (21, "Quillfeather, Thaddeus | Head | M | 40 | Wheelwright | Tumblebrook", "Quillfeather, Thaddeus",
         "Head", "head", "40"),
        (22, "Quillfeather, Clementine | Wife | F | 36 | | Tumblebrook", "Quillfeather, Clementine", "Wife",
         "spouse", "36"),
        (23, "Quillfeather, Ezra | Son | M | 9 | Scholar | Tumblebrook", "Quillfeather, Ezra", "Son", "child", "9")]),
    census("rec_census_1940", 1940, R + "census-1940/tumblebrook/0157", "4", "12", [
        (11, "Mossbury, Amos | Head | M | 44 | Miller | Tumblebrook", "Mossbury, Amos", "Head", "head", "44"),
        (12, "Mossbury, Hester | Wife | F | 40 | | Tumblebrook", "Mossbury, Hester", "Wife", "spouse", "40"),
        (13, "Thistlewood, Mabel | Step-dau | F | 16 | | Tumblebrook", "Thistlewood, Mabel", "Step-dau", "child",
         "16")]),
    census("rec_census_1960", 1960, R + "census-1960/tumblebrook/0388", "2", "7", [
        (31, "Quillfeather, Ezra | Head | M | 39 | Wheelwright | Tumblebrook", "Quillfeather, Ezra", "Head", "head",
         "39"),
        (32, "Quillfeather, Mabel | Wife | F | 36 | | Tumblebrook", "Quillfeather, Mabel", "Wife", "spouse", "36"),
        (33, "Quillfeather, Alden | Son | M | 4 | | Tumblebrook", "Quillfeather, Alden", "Son", "child", "4")]),
    census("rec_census_1900_larkspur", 1900, R + "census-1900/tumblebrook/0233", "2", "9", [
        (41, "Larkspur, Edmund | Head | M | 37 | Thatcher | Tumblebrook", "Larkspur, Edmund", "Head", "head", "37"),
        (42, "Larkspur, Rose | Wife | F | 33 | | Tumblebrook", "Larkspur, Rose", "Wife", "spouse", "33"),
        (43, "Larkspur, Clementine | Dau | F | 6 | Scholar | Tumblebrook", "Larkspur, Clementine", "Dau", "child",
         "6")]),
    record("rec_baptism_mabel", "parish_register",
           [("1", "1924 March 9. Mabel, daughter of Silas Thistlewood, labourer, and Hester his wife, of Mill "
                  "Lane, Tumblebrook. Born Feb 20."),
            ("2", "Godmother: Clementine Quillfeather.")],
           [men("m1", "Mabel", "daughter", "principal", ("e1", "e2"), ("1",)),
            men("m2", "Silas Thistlewood", "father", "father", (), ("1",)),
            men("m3", "Hester", "his wife", "mother", (), ("1",)),
            men("m4", "Clementine Quillfeather", "Godmother", "godparent", ("e1",), ("2",))],
           [ent("e1", "baptism", "primary", dt(1924, 3, 9, as_written="1924 March 9"), primary=True,
                where="Mill Lane Chapel, Tumblebrook"),
            ent("e2", "birth", "secondary", dt(1924, 2, 20, as_written="Born Feb 20", derivation="interpreted",
                                               note="the year is the year of the baptism"))],
           url=CHAPEL + "baptisms/1924/folio-09", locator=loc("Mill Lane Chapel baptisms 1924, folio 9, entry 31",
                                                             folio="9", entry="31"),
           repository="Mill Lane Chapel (made up)"),
    record("rec_burial_silas", "parish_register",
           [("1", "1931 November 3. Buried: Silas Thistlewood, labourer, of Mill Lane, aged 38.")],
           [men("s1", "Silas Thistlewood", "Buried", "deceased", ("e1",), ("1",), age="38")],
           [ent("e1", "burial", "primary", dt(1931, 11, 3, as_written="1931 November 3"), primary=True,
                where="Mill Lane Chapel, Tumblebrook")],
           url=CHAPEL + "burials/1931/folio-14", locator=loc("Mill Lane Chapel burials 1931, folio 14, entry 12",
                                                            folio="14", entry="12"),
           repository="Mill Lane Chapel (made up)"),
    record("rec_grave_ezra", "gravestone",
           [("1", "IN LOVING MEMORY OF"), ("2", "EZRA QUILLFEATHER"), ("3", "BORN 1923 DIED 1987"),
            ("4", "AND MABEL HIS WIFE"), ("5", "1924 - 1999")],
           [men("g1", "EZRA QUILLFEATHER", "IN LOVING MEMORY OF", "deceased", ("e1", "e2"), ("2", "3")),
            men("g2", "MABEL", "HIS WIFE", "spouse", ("e3", "e4"), ("4", "5"))],
           [ent("e1", "death", "secondary", dt(1987, as_written="1987")),
            ent("e2", "birth", "secondary", dt(1923, as_written="1923")),
            ent("e3", "death", "secondary", dt(1999, as_written="1999")),
            ent("e4", "birth", "secondary", dt(1924, as_written="1924"))],
           licence="CC-BY", cls=("original", "secondary"), url="https://graves.example.org/sample/tumblebrook/2231",
           conf=0.8, attribution={"contributor_name": "Wren Hollis", "contributor_role": "photographer",
                                  "source_organization": "Wrenshire Graves Project (made up)"},
           record_place=place("Tumblebrook churchyard")),
    record("rec_death_thaddeus", "death_certificate",
           [("1", "Thaddeus Quillfeather. Died 14 November 1950, Tumblebrook, Wrenshire. Age 60. Wheelwright."),
            ("2", "Informant: E. Quillfeather, son.")],
           [men("d1", "Thaddeus Quillfeather", "Died", "deceased", ("e1",), ("1",), age="60"),
            men("d2", "E. Quillfeather", "Informant", "informant", ("e1",), ("2",))],
           [ent("e1", "death", "primary", dt(1950, 11, 14, as_written="14 November 1950"), primary=True,
                informant="d2", where="Tumblebrook, Wrenshire")],
           licence="public-domain", url=R + "deaths/1950/1187",
           locator=loc("Deaths 1950, Wrenshire, certificate 1187", certificate="1187"), repository=OFFICE),
    record("rec_death_alden", "death_certificate",
           [("1", "Alden Ezra Quillfeather. Died 3 June 2019, Port Erewhon. Born 1956, Tumblebrook."),
            ("2", "Informant: Robin Quillfeather, child.")],
           [men("a1", "Alden Ezra Quillfeather", "Died", "deceased", ("e1", "e2"), ("1",)),
            men("a2", "Robin Quillfeather", "Informant", "informant", ("e1",), ("2",), living=True)],
           [ent("e1", "death", "primary", dt(2019, 6, 3, as_written="3 June 2019"), primary=True, informant="a2",
                where="Port Erewhon"),
            ent("e2", "birth", "secondary", dt(1956, as_written="1956"), where="Tumblebrook")],
           licence="tier2-private", living=True, url=R + "deaths/2019/0603",
           locator=loc("Deaths 2019, Port Erewhon, certificate 0603", certificate="0603"), repository=OFFICE),
    record("rec_birth_alden", "birth_certificate",
           [("1", "Alden Ezra, born 12 January 1956 at Tumblebrook."),
            ("2", "Father: Ezra Quillfeather, wheelwright."),
            ("3", "Mother: Mabel Quillfeather, formerly Thistlewood.")],
           [men("b1", "Alden Ezra", "born", "principal", ("e1",), ("1",)),
            men("b2", "Ezra Quillfeather", "Father", "father", (), ("2",)),
            men("b3", "Mabel Quillfeather", "Mother", "mother", (), ("3",))],
           [ent("e1", "birth", "primary", dt(1956, 1, 12, as_written="12 January 1956"), primary=True,
                where="Tumblebrook")],
           url=R + "births/1956/0044", locator=loc("Births 1956, Wrenshire, certificate 0044", certificate="0044"),
           repository=OFFICE),
    record("rec_marriage_1985", "marriage_certificate",
           [("1", "Alden Ezra Quillfeather, 29, bachelor, and June Bramblecott, 25, spinster, both of Tumblebrook, "
                  "married 12 August 1985 at the Mill Lane Chapel.")],
           [men("w1", "Alden Ezra Quillfeather", "bachelor", "groom", ("e1",), ("1",), age="29"),
            men("w2", "June Bramblecott", "spinster", "bride", ("e1",), ("1",), living=True, age="25")],
           [ent("e1", "marriage", "primary", dt(1985, 8, 12, as_written="12 August 1985"), primary=True,
                where="Mill Lane Chapel, Tumblebrook")],
           licence="tier2-private", living=True, url=R + "marriages/1985/0812",
           locator=loc("Marriages 1985, Wrenshire, certificate 0812", certificate="0812"), repository=OFFICE),
    record("rec_obit_iris", "obituary",
           [("1", "BRAMBLECOTT, Iris (born Fenwhistle), of Tumblebrook, died 4 May 2010."),
            ("2", "Survived by her daughter June Quillfeather.")],
           [men("o1", "BRAMBLECOTT, Iris", "died", "deceased", ("e1",), ("1",)),
            men("o2", "June Quillfeather", "her daughter", "child", (), ("2",), living=True)],
           [ent("e1", "death", "secondary", dt(2010, 5, 4, as_written="4 May 2010"), primary=True, where="Tumblebrook")],
           licence="tier2-private", cls=("original", "secondary"), living=True,
           url="https://news.example.org/sample/wrenshire-gazette/2010/obituaries/iris", conf=0.9,
           record_date=dt(2010, 5, 6, derivation="interpreted", note="the newspaper's date")),
    record("rec_walter_notice", "newspaper_article",
           [("1", "BRAMBLECOTT, Walter, 74, of Tumblebrook, died 2 March 2004."),
            ("2", "Survived by his daughter June Quillfeather.")],
           [men("n1", "BRAMBLECOTT, Walter", "died", "deceased", ("e1",), ("1",), age="74"),
            men("n2", "June Quillfeather", "his daughter", "child", (), ("2",), living=True)],
           [ent("e1", "death", "secondary", dt(2004, 3, 2, as_written="2 March 2004"), primary=True,
                where="Tumblebrook")],
           licence="tier2-private", cls=("original", "secondary"), living=True,
           url="https://news.example.org/sample/wrenshire-gazette/2004/notices/bramblecott", conf=0.9),
    record("rec_online_tree", "other",
           [("1", "Thaddeus Quilfeather b. 1888 Tumblebrook, m. Clementine Larkspur")],
           [men("t1", "Thaddeus Quilfeather", "the person on this page", "principal", ("e1",), ("1",)),
            men("t2", "Clementine Larkspur", "m.", "spouse", (), ("1",))],
           [ent("e1", "birth", "undetermined", dt(1888, as_written="1888"))],
           licence="CC-BY-SA", cls=("authored", "secondary"), url="https://trees.example.org/sample/quilfeather-thaddeus",
           conf=0.9, record_type_as_written="A page of an online family tree",
           attribution={"contributor_name": "M. Ashgrove", "contributor_role": "compiler",
                        "source_organization": "trees.example.org"}),
    record("rec_birth_ezra", "birth_register",
           [("1", "1921 June 4. Ezra, son of Nell Larch, spinster, of Brook Row, Tumblebrook. Father not named.")],
           [men("b1", "Ezra", "son", "principal", ("e1",), ("1",)),
            men("b2", "Nell Larch", "spinster", "mother", (), ("1",))],
           [ent("e1", "birth", "primary", dt(1921, 6, 4, as_written="1921 June 4"), primary=True,
                where="Brook Row, Tumblebrook")],
           url=R + "births/tumblebrook/1921/0158", locator=loc("Tumblebrook births 1921, entry 158", entry="158"),
           repository=OFFICE),
    record("rec_decree_ezra", "adoption_record",
           [("1", "Decree of adoption, 1923: Ezra Larch, aged two, is adopted by Thaddeus Quillfeather, wheelwright, "
                  "and Clementine his wife, of Tumblebrook."),
            ("2", "His mother, Nell Larch, consenting.")],
           [men("a1", "Ezra Larch", "is adopted", "child", ("e1",), ("1",), age="two"),
            men("a2", "Thaddeus Quillfeather", "adopted by", "parent", ("e1",), ("1",)),
            men("a3", "Clementine", "his wife", "parent", ("e1",), ("1",)),
            men("a4", "Nell Larch", "His mother", "mother", (), ("2",))],
           [ent("e1", "adoption", "primary", dt(1923, as_written="1923"), primary=True)],
           licence="tier2-private", repository="Wrenshire County Court (made up)",
           locator=loc("Adoption decrees 1923, number 7", entry="7")),
    record("rec_bond_fairweather", "court_record",
           [("1", "Bastardy bond, 1921: Jesse Fairweather, labourer, aged 24, is named as the reputed father of the "
                  "child of Nell Larch, spinster, of Brook Row.")],
           [men("f1", "Jesse Fairweather", "reputed father", "father", (), ("1",), age="24"),
            men("f2", "Nell Larch", "spinster", "mother", (), ("1",)),
            men("f3", "the child of Nell Larch", "the child", "child", (), ("1",), name_status="relational_only")],
           licence="tier2-private", repository="Wrenshire Quarter Sessions (made up)",
           locator=loc("Bastardy bonds 1921, bond 19", entry="19")),
    record("rec_baptism_nell", "parish_register",
           [("1", "1898 April 17. Nell, daughter of Josiah Larch, carter, and Martha Larch his wife, of Brook Row, "
                  "Tumblebrook.")],
           [men("n1", "Nell", "daughter", "principal", ("e1",), ("1",)),
            men("n2", "Josiah Larch", "carter", "father", (), ("1",)),
            men("n3", "Martha Larch", "his wife", "mother", (), ("1",))],
           [ent("e1", "baptism", "primary", dt(1898, 4, 17, as_written="1898 April 17"), primary=True,
                where="Mill Lane Chapel, Tumblebrook")],
           url=CHAPEL + "baptisms/1898/folio-03", locator=loc("Mill Lane Chapel baptisms 1898, folio 3, entry 12",
                                                             folio="3", entry="12"),
           repository="Mill Lane Chapel (made up)"),
    record("rec_death_nell", "death_certificate",
           [("1", "Nell Larch, spinster, of Brook Row, Tumblebrook. Died 8 February 1960, aged 61."),
            ("2", "Informant: Ezra Quillfeather, son.")],
           [men("d1", "Nell Larch", "Died", "deceased", ("e1",), ("1",), age="61"),
            men("d2", "Ezra Quillfeather", "Informant", "informant", ("e1",), ("2",))],
           [ent("e1", "death", "primary", dt(1960, 2, 8, as_written="8 February 1960"), primary=True,
                informant="d2", where="Tumblebrook")],
           licence="public-domain", url=R + "deaths/1960/0071",
           locator=loc("Deaths 1960, Wrenshire, certificate 0071", certificate="0071"), repository=OFFICE),
    record("rec_passenger_swallowtail", "passenger_list",
           [("1", "The brig Swallowtail, Abel Crane master, sailed from Port Mirren on 2 April 1895."),
            ("2", "Arrived at Port Erewhon 30 May 1895."),
            ("3", "Larch, Josiah, 30, carter."),
            ("4", "Larch, Martha, 27, his wife.")],
           [men("c1", "Abel Crane", "master", "captain", ("e1", "e2"), ("1", "2")),
            men("p3", "Larch, Josiah", "passenger", "passenger", ("e1", "e2"), ("3",), age="30"),
            men("p4", "Larch, Martha", "his wife", "passenger", ("e1", "e2"), ("4",), age="27", relative_to="p3")],
           [ent("e1", "emigration", "primary", dt(1895, 4, 2, as_written="2 April 1895"), where="Port Mirren"),
            ent("e2", "immigration", "primary", dt(1895, 5, 30, as_written="30 May 1895"), primary=True,
                where="Port Erewhon")],
           url=R + "passenger-lists/1895/swallowtail", scope="complete_list",
           locator=loc("Arrivals at Port Erewhon 1895, the brig Swallowtail, lines 1-4", line_first=1, line_last=4),
           repository=OFFICE),
    record("rec_baptism_thaddeus", "parish_register",
           [("1", "1890 March 2. Thaddeus, son of Obadiah Quillfeather, wheelwright, and Prudence Quillfeather his "
                  "wife, of Tumblebrook.")],
           [men("t1", "Thaddeus", "son", "principal", ("e1",), ("1",)),
            men("t2", "Obadiah Quillfeather", "wheelwright", "father", (), ("1",)),
            men("t3", "Prudence Quillfeather", "his wife", "mother", (), ("1",))],
           [ent("e1", "baptism", "primary", dt(1890, 3, 2, as_written="1890 March 2"), primary=True,
                where="Mill Lane Chapel, Tumblebrook")],
           url=CHAPEL + "baptisms/1890/folio-02", locator=loc("Mill Lane Chapel baptisms 1890, folio 2, entry 5",
                                                             folio="2", entry="5"),
           repository="Mill Lane Chapel (made up)"),
    record("rec_grave_obadiah", "gravestone",
           [("1", "OBADIAH QUILLFEATHER"), ("2", "1858 - 1911"), ("3", "AND PRUDENCE HIS WIFE"), ("4", "1861 - 1930")],
           [men("o1", "OBADIAH QUILLFEATHER", "OBADIAH QUILLFEATHER", "deceased", ("e1",), ("1", "2")),
            men("o2", "PRUDENCE", "HIS WIFE", "spouse", ("e2",), ("3", "4"))],
           [ent("e1", "death", "secondary", dt(1911, as_written="1911")),
            ent("e2", "death", "secondary", dt(1930, as_written="1930"))],
           cls=("original", "secondary"), url="https://graves.example.org/sample/tumblebrook/1874", conf=0.8,
           record_place=place("Tumblebrook churchyard")),
    record("rec_baptism_clementine", "parish_register",
           [("1", "1894 January 14. Clementine, daughter of Edmund Larkspur, thatcher, and Rose Larkspur his wife, "
                  "of Tumblebrook.")],
           [men("c1", "Clementine", "daughter", "principal", ("e1",), ("1",)),
            men("c2", "Edmund Larkspur", "thatcher", "father", (), ("1",)),
            men("c3", "Rose Larkspur", "his wife", "mother", (), ("1",))],
           [ent("e1", "baptism", "primary", dt(1894, 1, 14, as_written="1894 January 14"), primary=True,
                where="Mill Lane Chapel, Tumblebrook")],
           url=CHAPEL + "baptisms/1894/folio-05", locator=loc("Mill Lane Chapel baptisms 1894, folio 5, entry 9",
                                                             folio="5", entry="9"),
           repository="Mill Lane Chapel (made up)"),
    record("rec_decree_mabel", "adoption_record",
           [("1", "Decree of adoption, 1942: Mabel Thistlewood, aged seventeen, is adopted by Amos Mossbury, miller, "
                  "of Tumblebrook."),
            ("2", "Her mother, Hester Mossbury, consenting.")],
           [men("d1", "Mabel Thistlewood", "is adopted", "child", ("e1",), ("1",), age="seventeen"),
            men("d2", "Amos Mossbury", "adopted by", "parent", ("e1",), ("1",)),
            men("d3", "Hester Mossbury", "Her mother", "mother", (), ("2",))],
           [ent("e1", "adoption", "primary", dt(1942, as_written="1942"), primary=True)],
           licence="tier2-private", repository="Wrenshire County Court (made up)",
           locator=loc("Adoption decrees 1942, number 3", entry="3")),
    record("rec_gazetteer", "gazetteer_entry",
           [("1", "Tumblebrook, a village of Erewhon. Parish clerk: Mr. H. Brindle."),
            ("2", "In the county of Wrenshire until 31 March 1974."),
            ("3", "In Mossdale District from 1 April 1974."),
            ("4", "Wrenshire, a county of Erewhon until 31 March 1974."),
            ("5", "Mossdale, a district of Erewhon, formed 1 April 1974."),
            ("6", "Erewhon, a country.")],
           [men("g1", "H. Brindle", "Parish clerk", "other", (), ("1", "2", "3", "4", "5", "6"))],
           cls=("authored", "secondary"), url="https://gazetteer.example.org/erewhon/tumblebrook", scope="partial",
           conf=0.9),
]
STAYS_WAITING = {"rec_walter_notice"}
# The interview is the Orchestrator's; every other document was found under the Genealogist's
# gathering task. (A task may name only a person who is already in the file, so the research
# tasks about each person are planned once the people are in.)
RECORD_TASK = {doc["record_id"]: ("t_interview" if doc["record_id"] == uid("rec_interview") else "t_gather")
               for doc in RECORDS}
RECORD_KEYS = ["rec_interview", "rec_census_1930", "rec_census_1940", "rec_census_1960", "rec_census_1900_larkspur",
               "rec_baptism_mabel", "rec_burial_silas", "rec_grave_ezra", "rec_death_thaddeus", "rec_death_alden",
               "rec_birth_alden", "rec_marriage_1985", "rec_obit_iris", "rec_walter_notice", "rec_online_tree",
               "rec_birth_ezra", "rec_decree_ezra", "rec_bond_fairweather", "rec_baptism_nell", "rec_death_nell",
               "rec_passenger_swallowtail", "rec_baptism_thaddeus", "rec_grave_obadiah", "rec_baptism_clementine",
               "rec_decree_mabel", "rec_gazetteer"]


# ------------------------------------------------------------------ people (MaxPersons; no family links in them)

def name(rkey, mid, words, quote, conf, **kw):
    return claim(ev(rkey, mid, quote), conf, name_as_written=words, **kw)


def birth_from_age(rkey, mid, quote, y1, y2, note, conf=0.8):
    return claim(ev(rkey, mid, quote, kind="indirect"), conf, date=worked_out(y1, y2, note))


def dated(rkey, mid, quote, ref, date, conf, where=None, **kw):
    out = claim(ev(rkey, mid, quote, ref=ref), conf, date=date, **kw)
    if where:
        out["place"] = place(where)
    return out


GAZ_NOTE = "the gazetteer page names its parish clerk; the place entries are on the same page"
PEOPLE = {
    "you": dict(living=True, names=[
        name("rec_death_alden", "a2", "Robin Quillfeather", "Informant: Robin Quillfeather, child.", 0.9,
             is_preferred=True),
        name("rec_interview", "i1", "Robin Quillfeather", "Robin Quillfeather. I was born in 1988.", 0.4)],
        birth=[dated("rec_interview", "i1", "I was born in 1988.", None, dt(1988, as_written="1988"), 0.4)]),
    "alden": dict(living=False, licence="tier2-private", names=[
        name("rec_death_alden", "a1", "Alden Ezra Quillfeather", "Alden Ezra Quillfeather. Died 3 June 2019", 0.95,
             is_preferred=True, name_type="birth"),
        name("rec_census_1960", "33", "Quillfeather, Alden", "Quillfeather, Alden | Son", 0.85),
        name("rec_marriage_1985", "w1", "Alden Ezra Quillfeather", "Alden Ezra Quillfeather, 29, bachelor", 0.9)],
        birth=[dated("rec_birth_alden", "b1", "Alden Ezra, born 12 January 1956 at Tumblebrook.", "e1",
                     dt(1956, 1, 12, as_written="12 January 1956"), 0.95, "Tumblebrook"),
               birth_from_age("rec_census_1960", "33", "Quillfeather, Alden | Son | M | 4", 1955, 1956,
                              "aged 4 in the 1960 census")],
        death=[dated("rec_death_alden", "a1", "Died 3 June 2019, Port Erewhon", "e1", dt(2019, 6, 3, as_written="3 June 2019"),
                     0.95, "Port Erewhon")]),
    "june": dict(living=True, names=[
        name("rec_obit_iris", "o2", "June Quillfeather", "her daughter June Quillfeather", 0.9, is_preferred=True,
             name_type="married"),
        name("rec_marriage_1985", "w2", "June Bramblecott", "June Bramblecott, 25, spinster", 0.9, name_type="birth"),
        name("rec_interview", "i3", "June Quillfeather", "My mum is June Quillfeather", 0.4)],
        birth=[birth_from_age("rec_marriage_1985", "w2", "June Bramblecott, 25, spinster", 1959, 1960,
                              "aged 25 at her marriage in 1985", 0.7),
               dated("rec_interview", "i3", "I think she was born in 1960.", None, dt(1960, as_written="1960"), 0.4)]),
    "ezra": dict(living=False, names=[
        name("rec_death_nell", "d2", "Ezra Quillfeather", "Informant: Ezra Quillfeather, son.", 0.9, is_preferred=True),
        name("rec_census_1930", "23", "Quillfeather, Ezra", "Quillfeather, Ezra | Son", 0.85),
        name("rec_decree_ezra", "a1", "Ezra Larch", "Ezra Larch, aged two", 0.9, name_type="birth"),
        name("rec_grave_ezra", "g1", "EZRA QUILLFEATHER", "EZRA QUILLFEATHER", 0.6)],
        birth=[dated("rec_birth_ezra", "b1", "1921 June 4. Ezra, son of Nell Larch", "e1",
                     dt(1921, 6, 4, as_written="1921 June 4"), 0.95, "Brook Row, Tumblebrook"),
               birth_from_age("rec_census_1930", "23", "Quillfeather, Ezra | Son | M | 9", 1920, 1921,
                              "aged 9 in the 1930 census"),
               dated("rec_grave_ezra", "g1", "BORN 1923 DIED 1987", "e2", dt(1923, as_written="1923"), 0.6)],
        death=[dated("rec_grave_ezra", "g1", "BORN 1923 DIED 1987", "e1", dt(1987, as_written="1987"), 0.6)],
        attributes=[claim(ev("rec_census_1960", "31", "Quillfeather, Ezra | Head | M | 39 | Wheelwright"), 0.85,
                          attribute_type="occupation", value_as_written="Wheelwright", place=place("Tumblebrook")),
                    claim(ev("rec_census_1960", "31", "Quillfeather, Ezra | Head | M | 39 | Wheelwright | Tumblebrook"),
                          0.85, attribute_type="residence", value_as_written="Tumblebrook",
                          place=place("Tumblebrook"))]),
    "mabel": dict(living=False, names=[
        name("rec_birth_alden", "b3", "Mabel Quillfeather", "Mother: Mabel Quillfeather, formerly Thistlewood.", 0.9,
             is_preferred=True, name_type="married"),
        name("rec_census_1960", "32", "Quillfeather, Mabel", "Quillfeather, Mabel | Wife", 0.9, name_type="married"),
        name("rec_decree_mabel", "d1", "Mabel Thistlewood", "Mabel Thistlewood, aged seventeen", 0.9, name_type="birth"),
        name("rec_census_1940", "13", "Thistlewood, Mabel", "Thistlewood, Mabel | Step-dau", 0.85)],
        birth=[dated("rec_baptism_mabel", "m1", "Born Feb 20", "e2",
                     dt(1924, 2, 20, as_written="Born Feb 20", derivation="interpreted",
                        note="the year is the year of the baptism"), 0.85),
               birth_from_age("rec_census_1940", "13", "Thistlewood, Mabel | Step-dau | F | 16", 1923, 1924,
                              "aged 16 in the 1940 census"),
               dated("rec_grave_ezra", "g2", "1924 - 1999", "e4", dt(1924, as_written="1924"), 0.6)],
        death=[dated("rec_grave_ezra", "g2", "1924 - 1999", "e3", dt(1999, as_written="1999"), 0.6)],
        events=[dated("rec_baptism_mabel", "m1", "1924 March 9. Mabel, daughter of Silas Thistlewood", "e1",
                      dt(1924, 3, 9, as_written="1924 March 9"), 0.9, "Mill Lane Chapel, Tumblebrook",
                      event_type="baptism", role="principal")]),
    "walter": dict(living=False, names=[
        name("rec_interview", "i6", "Walter Bramblecott", "Walter and Iris Bramblecott", 0.4, is_preferred=True),
        name("rec_walter_notice", "n1", "BRAMBLECOTT, Walter", "BRAMBLECOTT, Walter, 74", 0.85)],
        birth=[birth_from_age("rec_walter_notice", "n1", "BRAMBLECOTT, Walter, 74, of Tumblebrook, died 2 March 2004",
                              1929, 1930, "aged 74 at his death in 2004", 0.7)],
        death=[dated("rec_walter_notice", "n1", "died 2 March 2004", "e1", dt(2004, 3, 2, as_written="2 March 2004"),
                     0.8, "Tumblebrook")]),
    "iris": dict(living=False, names=[
        name("rec_interview", "i7", "Iris Bramblecott", "Walter and Iris Bramblecott", 0.4, is_preferred=True),
        name("rec_obit_iris", "o1", "BRAMBLECOTT, Iris", "BRAMBLECOTT, Iris (born Fenwhistle)", 0.9,
             name_type="married"),
        claim(ev("rec_obit_iris", "o1", "Iris (born Fenwhistle)", kind="indirect"), 0.8,
              name_as_written="Iris Fenwhistle", name_type="maiden")],
        birth=[dated("rec_interview", "i7", "Grandma Iris was born in 1933", None, dt(1933, as_written="1933"), 0.4)],
        death=[dated("rec_obit_iris", "o1", "died 4 May 2010", "e1", dt(2010, 5, 4, as_written="4 May 2010"), 0.9,
                     "Tumblebrook")]),
    "thaddeus": dict(living=False, names=[
        name("rec_death_thaddeus", "d1", "Thaddeus Quillfeather", "Thaddeus Quillfeather. Died 14 November 1950", 0.95,
             is_preferred=True),
        name("rec_census_1930", "21", "Quillfeather, Thaddeus", "Quillfeather, Thaddeus | Head", 0.85),
        name("rec_baptism_thaddeus", "t1", "Thaddeus", "Thaddeus, son of Obadiah Quillfeather", 0.9)],
        birth=[birth_from_age("rec_census_1930", "21", "Quillfeather, Thaddeus | Head | M | 40", 1889, 1890,
                              "aged 40 in the 1930 census"),
               birth_from_age("rec_death_thaddeus", "d1", "Died 14 November 1950, Tumblebrook, Wrenshire. Age 60.",
                              1889, 1890, "aged 60 at his death in 1950", 0.7)],
        events=[dated("rec_baptism_thaddeus", "t1", "1890 March 2. Thaddeus, son of Obadiah Quillfeather", "e1",
                      dt(1890, 3, 2, as_written="1890 March 2"), 0.9, "Mill Lane Chapel, Tumblebrook",
                      event_type="baptism", role="principal")],
        death=[dated("rec_death_thaddeus", "d1", "Died 14 November 1950, Tumblebrook, Wrenshire", "e1",
                     dt(1950, 11, 14, as_written="14 November 1950"), 0.95, "Tumblebrook, Wrenshire")],
        attributes=[claim(ev("rec_census_1930", "21", "Quillfeather, Thaddeus | Head | M | 40 | Wheelwright"), 0.85,
                          attribute_type="occupation", value_as_written="Wheelwright", place=place("Tumblebrook"))]),
    "clementine": dict(living=False, names=[
        name("rec_baptism_mabel", "m4", "Clementine Quillfeather", "Godmother: Clementine Quillfeather.", 0.85,
             is_preferred=True, name_type="married"),
        name("rec_census_1930", "22", "Quillfeather, Clementine", "Quillfeather, Clementine | Wife", 0.85),
        name("rec_census_1900_larkspur", "43", "Larkspur, Clementine", "Larkspur, Clementine | Dau", 0.85,
             name_type="birth")],
        birth=[birth_from_age("rec_census_1930", "22", "Quillfeather, Clementine | Wife | F | 36", 1893, 1894,
                              "aged 36 in the 1930 census")],
        events=[dated("rec_baptism_clementine", "c1", "1894 January 14. Clementine, daughter of Edmund Larkspur", "e1",
                      dt(1894, 1, 14, as_written="1894 January 14"), 0.9, "Mill Lane Chapel, Tumblebrook",
                      event_type="baptism", role="principal"),
                dated("rec_baptism_mabel", "m4", "Godmother: Clementine Quillfeather.", "e1",
                      dt(1924, 3, 9, derivation="interpreted", note="the date of the baptism she stood at"), 0.85,
                      None, event_type="baptism", role="godparent", role_as_written="Godmother")]),
    "silas": dict(living=False, names=[
        name("rec_baptism_mabel", "m2", "Silas Thistlewood", "Silas Thistlewood, labourer", 0.9, is_preferred=True)],
        events=[dated("rec_burial_silas", "s1", "1931 November 3. Buried: Silas Thistlewood", "e1",
                      dt(1931, 11, 3, as_written="1931 November 3"), 0.9, "Mill Lane Chapel, Tumblebrook",
                      event_type="burial", role="principal")],
        attributes=[claim(ev("rec_baptism_mabel", "m2", "Silas Thistlewood, labourer"), 0.85,
                          attribute_type="occupation", value_as_written="labourer")]),
    "hester": dict(living=False, names=[
        name("rec_decree_mabel", "d3", "Hester Mossbury", "Her mother, Hester Mossbury, consenting.", 0.9,
             is_preferred=True, name_type="married"),
        name("rec_census_1940", "12", "Mossbury, Hester", "Mossbury, Hester | Wife", 0.9, name_type="married"),
        name("rec_baptism_mabel", "m3", "Hester", "Hester his wife", 0.9)],
        birth=[birth_from_age("rec_census_1940", "12", "Mossbury, Hester | Wife | F | 40", 1899, 1900,
                              "aged 40 in the 1940 census")]),
    "amos": dict(living=False, names=[
        name("rec_decree_mabel", "d2", "Amos Mossbury", "adopted by Amos Mossbury, miller", 0.9, is_preferred=True),
        name("rec_census_1940", "11", "Mossbury, Amos", "Mossbury, Amos | Head", 0.85),
        name("rec_census_1940", "11", "Mossbury, Amon", "Mossbury, Amos | Head", 0.85)],    # a misreading, withdrawn
        birth=[birth_from_age("rec_census_1940", "11", "Mossbury, Amos | Head | M | 44", 1895, 1896,
                              "aged 44 in the 1940 census")],
        attributes=[claim(ev("rec_census_1940", "11", "Mossbury, Amos | Head | M | 44 | Miller"), 0.85,
                          attribute_type="occupation", value_as_written="Miller", place=place("Tumblebrook"))]),
    "thaddeus_tree": dict(living=True, names=[
        name("rec_online_tree", "t1", "Thaddeus Quilfeather", "Thaddeus Quilfeather b. 1888", 0.4)],
        birth=[dated("rec_online_tree", "t1", "Thaddeus Quilfeather b. 1888 Tumblebrook", "e1",
                     dt(1888, as_written="1888"), 0.4, "Tumblebrook")]),
    "nell": dict(living=False, names=[
        name("rec_death_nell", "d1", "Nell Larch", "Nell Larch, spinster", 0.95, is_preferred=True),
        name("rec_birth_ezra", "b2", "Nell Larch", "son of Nell Larch, spinster", 0.9),
        name("rec_baptism_nell", "n1", "Nell", "Nell, daughter of Josiah Larch", 0.9)],
        events=[dated("rec_baptism_nell", "n1", "1898 April 17. Nell, daughter of Josiah Larch", "e1",
                      dt(1898, 4, 17, as_written="1898 April 17"), 0.9, "Mill Lane Chapel, Tumblebrook",
                      event_type="baptism", role="principal")],
        death=[dated("rec_death_nell", "d1", "Died 8 February 1960, aged 61.", "e1",
                     dt(1960, 2, 8, as_written="8 February 1960"), 0.95, "Tumblebrook")]),
    "jesse": dict(living=False, names=[
        name("rec_bond_fairweather", "f1", "Jesse Fairweather", "Jesse Fairweather, labourer, aged 24", 0.9,
             is_preferred=True)],
        birth=[birth_from_age("rec_bond_fairweather", "f1", "Jesse Fairweather, labourer, aged 24", 1896, 1897,
                              "aged 24 in 1921")]),
    "josiah": dict(living=False, names=[
        name("rec_baptism_nell", "n2", "Josiah Larch", "Josiah Larch, carter", 0.9, is_preferred=True),
        name("rec_passenger_swallowtail", "p3", "Larch, Josiah", "Larch, Josiah, 30, carter.", 0.85)],
        birth=[birth_from_age("rec_passenger_swallowtail", "p3", "Larch, Josiah, 30, carter.", 1864, 1865,
                              "aged 30 on the 1895 passenger list", 0.7)],
        memberships=[claim(ev("rec_passenger_swallowtail", "p3", "Larch, Josiah, 30, carter."), 0.85,
                           group_as_written="The brig Swallowtail", role="passenger", role_as_written="passenger",
                           date={"value": {"year": 1895, "month": 4, "day": 2},
                                 "value2": {"year": 1895, "month": 5, "day": 30}, "modifier": "from_to",
                                 "derivation": "interpreted", "derivation_note": "the voyage, sailing to arrival"})]),
    "martha": dict(living=False, names=[
        name("rec_baptism_nell", "n3", "Martha Larch", "Martha Larch his wife", 0.9, is_preferred=True),
        name("rec_passenger_swallowtail", "p4", "Larch, Martha", "Larch, Martha, 27, his wife.", 0.85)],
        birth=[birth_from_age("rec_passenger_swallowtail", "p4", "Larch, Martha, 27, his wife.", 1867, 1868,
                              "aged 27 on the 1895 passenger list", 0.7)],
        memberships=[claim(ev("rec_passenger_swallowtail", "p4", "Larch, Martha, 27, his wife."), 0.85,
                           group_as_written="The brig Swallowtail", role="passenger", role_as_written="his wife",
                           date={"value": {"year": 1895, "month": 4, "day": 2},
                                 "value2": {"year": 1895, "month": 5, "day": 30}, "modifier": "from_to",
                                 "derivation": "interpreted", "derivation_note": "the voyage, sailing to arrival"})]),
    "obadiah": dict(living=False, names=[
        name("rec_baptism_thaddeus", "t2", "Obadiah Quillfeather", "Obadiah Quillfeather, wheelwright", 0.9,
             is_preferred=True),
        name("rec_grave_obadiah", "o1", "OBADIAH QUILLFEATHER", "OBADIAH QUILLFEATHER", 0.6)],
        death=[dated("rec_grave_obadiah", "o1", "1858 - 1911", "e1", dt(1911, as_written="1911"), 0.6)]),
    "prudence": dict(living=False, names=[
        name("rec_baptism_thaddeus", "t3", "Prudence Quillfeather", "Prudence Quillfeather his wife", 0.9,
             is_preferred=True),
        name("rec_grave_obadiah", "o2", "PRUDENCE", "AND PRUDENCE HIS WIFE", 0.6)],
        death=[dated("rec_grave_obadiah", "o2", "1861 - 1930", "e2", dt(1930, as_written="1930"), 0.6)]),
    "edmund": dict(living=False, names=[
        name("rec_baptism_clementine", "c2", "Edmund Larkspur", "Edmund Larkspur, thatcher", 0.9, is_preferred=True),
        name("rec_census_1900_larkspur", "41", "Larkspur, Edmund", "Larkspur, Edmund | Head", 0.85)],
        birth=[birth_from_age("rec_census_1900_larkspur", "41", "Larkspur, Edmund | Head | M | 37", 1862, 1863,
                              "aged 37 in the 1900 census")]),
    "rose": dict(living=False, names=[
        name("rec_baptism_clementine", "c3", "Rose Larkspur", "Rose Larkspur his wife", 0.9, is_preferred=True),
        name("rec_census_1900_larkspur", "42", "Larkspur, Rose", "Larkspur, Rose | Wife", 0.85)],
        birth=[birth_from_age("rec_census_1900_larkspur", "42", "Larkspur, Rose | Wife | F | 33", 1866, 1867,
                              "aged 33 in the 1900 census")]),
}
STAYS_WAITING_PEOPLE = {"walter"}
TURNED_DOWN = {"thaddeus_tree": "NEEDS HUMAN: may be the same man as Thaddeus Quillfeather; the only "
                                "source is an online tree (RULES 3)"}
WITHDRAWN = {"amos": ("Mossbury, Amon", "misread: the census line says Amos, not Amon")}


def person_doc(key: str) -> dict:
    p = PEOPLE[key]
    living = p["living"]
    doc = {"person_id": uid(key), "schema_version": V, "is_living": living,
           "redistribution_license": p.get("licence") or ("tier2-private" if living else "CC0"),
           "name_assertions": [dict(c) for c in p["names"]], "extensions": dict(NOTE)}
    for field, list_name in (("birth", "birth_assertions"), ("death", "death_assertions"),
                             ("events", "event_assertions"), ("attributes", "attribute_assertions"),
                             ("memberships", "membership_assertions")):
        if p.get(field):
            doc[list_name] = json.loads(json.dumps(p[field]))
    return doc


# ------------------------------------------------------------------ family links (sent with `link`)

def parents(child, items, evidence, conf, set_kind=None, child_mention=None):
    """items: (person key, lineage, role, mention, own evidence or None)."""
    req = {"link_request": "2.0", "kind": "parents", "child_id": uid(child), "evidence": evidence,
           "confidence": conf, "parents": []}
    for key, lineage, role, mention, own in items:
        item = {"person_id": uid(key), "lineage": lineage, "parent_role": role, "mention_id": mention}
        if own:
            item["evidence"] = own
        req["parents"].append(item)
    if set_kind:
        req["set_kind"] = set_kind
    if child_mention:
        req["child_mention_id"] = child_mention
    return req


def couple(a, b, fact, evidence, conf, date=None, where=None, **fact_extra):
    req = {"link_request": "2.0", "kind": "couple", "partners": [dict({"person_id": uid(k)}, **extra)
                                                                  for k, extra in ((a[0], a[1]), (b[0], b[1]))],
           "fact": dict({"fact_type": fact}, **fact_extra), "evidence": evidence, "confidence": conf}
    if date:
        req["date"] = date
    if where:
        req["place"] = place(where)
    return req


def mention(mid, age=None):
    return dict({"mention_id": mid}, **({"age_as_written": age} if age else {}))


# (task key, request). Adoption links go under tasks that carry the person's recorded yes (R-31).
LINKS = [
    ("t_build", parents("you", [("alden", "birth_parent", "father", "i2",
                                 ev("rec_interview", "i2", "My dad was Alden Quillfeather.")),
                                ("june", "birth_parent", "mother", "i3",
                                 ev("rec_interview", "i3", "My mum is June Quillfeather"))],
                        ev("rec_interview", "i2", "My dad was Alden Quillfeather."), 0.4, "birth", "i1")),
    ("t_build", parents("alden", [("ezra", "birth_parent", "father", "b2",
                                   ev("rec_birth_alden", "b2", "Father: Ezra Quillfeather, wheelwright.")),
                                  ("mabel", "birth_parent", "mother", "b3",
                                   ev("rec_birth_alden", "b3", "Mother: Mabel Quillfeather, formerly Thistlewood."))],
                        ev("rec_birth_alden", "b2", "Father: Ezra Quillfeather, wheelwright."), 0.95, "birth", "b1")),
    ("t_build", parents("alden", [("ezra", "unknown", "father", "31", None), ("mabel", "unknown", "mother", "32", None)],
                        ev("rec_census_1960", "33", "Quillfeather, Alden | Son"), 0.85, None, "33")),
    ("t_build", parents("june", [("walter", "birth_parent", "father", "i6", None),
                                 ("iris", "birth_parent", "mother", "i7", None)],
                        ev("rec_interview", "i6", "Walter and Iris Bramblecott"), 0.4, "birth", "i3")),
    ("t_build", parents("ezra", [("nell", "birth_parent", "mother", "b2", None)],
                        ev("rec_birth_ezra", "b1", "Ezra, son of Nell Larch, spinster"), 0.9, "birth", "b1")),
    ("t_adoption", parents("ezra", [("thaddeus", "adoptive_parent", "father", "a2", None),
                                    ("clementine", "adoptive_parent", "mother", "a3", None)],
                           ev("rec_decree_ezra", "a1", "Ezra Larch, aged two, is adopted by Thaddeus Quillfeather, "
                                                       "wheelwright, and Clementine his wife"), 0.95, "adoptive", "a1")),
    ("t_build", parents("ezra", [("thaddeus", "unknown", "father", "21", None),
                                 ("clementine", "unknown", "mother", "22", None)],
                        ev("rec_census_1930", "23", "Quillfeather, Ezra | Son"), 0.85, None, "23")),
    ("t_larch_birth", parents("ezra", [("jesse", "birth_parent", "father", "f1", None)],
                              ev("rec_bond_fairweather", "f3", "named as the reputed father of the child of Nell Larch"),
                              0.5, "alternative_hypothesis", "f3")),
    ("t_build", parents("mabel", [("silas", "birth_parent", "father", "m2", None),
                                  ("hester", "birth_parent", "mother", "m3", None)],
                        ev("rec_baptism_mabel", "m1", "Mabel, daughter of Silas Thistlewood, labourer, and Hester his wife"),
                        0.9, "birth", "m1")),
    ("t_step_link", parents("mabel", [("amos", "step_parent", "father", "11", None),
                                      ("hester", "unknown", "mother", "12", None)],
                            ev("rec_census_1940", "13", "Thistlewood, Mabel | Step-dau"), 0.85, "step_household", "13")),
    ("t_adoption_mabel", parents("mabel", [("amos", "adoptive_parent", "father", "d2", None),
                                           ("hester", "birth_parent", "mother", "d3", None)],
                                 ev("rec_decree_mabel", "d1", "Mabel Thistlewood, aged seventeen, is adopted by Amos "
                                                              "Mossbury"), 0.95, "adoptive", "d1")),
    ("t_build", parents("thaddeus", [("obadiah", "birth_parent", "father", "t2", None),
                                     ("prudence", "birth_parent", "mother", "t3", None)],
                        ev("rec_baptism_thaddeus", "t1", "Thaddeus, son of Obadiah Quillfeather, wheelwright, and "
                                                         "Prudence Quillfeather his wife"), 0.9, "birth", "t1")),
    ("t_build", parents("clementine", [("edmund", "birth_parent", "father", "c2", None),
                                       ("rose", "birth_parent", "mother", "c3", None)],
                        ev("rec_baptism_clementine", "c1", "Clementine, daughter of Edmund Larkspur, thatcher, and "
                                                           "Rose Larkspur his wife"), 0.9, "birth", "c1")),
    ("t_build", parents("clementine", [("edmund", "unknown", "father", "41", None),
                                       ("rose", "unknown", "mother", "42", None)],
                        ev("rec_census_1900_larkspur", "43", "Larkspur, Clementine | Dau"), 0.85, None, "43")),
    ("t_build", parents("nell", [("josiah", "birth_parent", "father", "n2", None),
                                 ("martha", "birth_parent", "mother", "n3", None)],
                        ev("rec_baptism_nell", "n1", "Nell, daughter of Josiah Larch, carter, and Martha Larch his wife"),
                        0.9, "birth", "n1")),
    ("t_build", couple(("alden", mention("w1", "29")), ("june", mention("w2", "25")), "marriage",
                       ev("rec_marriage_1985", "w1", "married 12 August 1985 at the Mill Lane Chapel", ref="e1"), 0.95,
                       dt(1985, 8, 12, as_written="12 August 1985"), "Mill Lane Chapel, Tumblebrook",
                       ceremony="religious")),
    ("t_build", couple(("ezra", mention("31", "39")), ("mabel", mention("32", "36")), "census",
                       ev("rec_census_1960", "32", "Quillfeather, Mabel | Wife", ref="e1"), 0.85, None, "Tumblebrook")),
    ("t_build", couple(("thaddeus", mention("21", "40")), ("clementine", mention("22", "36")), "census",
                       ev("rec_census_1930", "22", "Quillfeather, Clementine | Wife", ref="e1"), 0.85, None,
                       "Tumblebrook")),
    ("t_build", couple(("amos", mention("11", "44")), ("hester", mention("12", "40")), "census",
                       ev("rec_census_1940", "12", "Mossbury, Hester | Wife", ref="e1"), 0.85, None, "Tumblebrook")),
    ("t_build", couple(("edmund", mention("41", "37")), ("rose", mention("42", "33")), "census",
                       ev("rec_census_1900_larkspur", "42", "Larkspur, Rose | Wife", ref="e1"), 0.85, None,
                       "Tumblebrook")),
    ("t_build", couple(("josiah", mention("p3", "30")), ("martha", mention("p4", "27")), "existence",
                       ev("rec_passenger_swallowtail", "p4", "Larch, Martha, 27, his wife."), 0.8)),
    ("t_build", couple(("obadiah", mention("o1")), ("prudence", mention("o2")), "existence",
                       ev("rec_grave_obadiah", "o2", "AND PRUDENCE HIS WIFE"), 0.6)),
    ("t_build", couple(("silas", mention("m2")), ("hester", mention("m3")), "existence",
                       ev("rec_baptism_mabel", "m3", "Hester his wife"), 0.9)),
    ("t_godparent", {"link_request": "2.0", "kind": "association", "association_type": "godparent",
                     "party1": {"person_id": uid("clementine"), "role": "godparent", "role_as_written": "Godmother"},
                     "party2": {"person_id": uid("mabel")},
                     "evidence": ev("rec_baptism_mabel", "m4", "Godmother: Clementine Quillfeather."),
                     "confidence": 0.85}),
]


# ------------------------------------------------------------------ places and the voyage group (analysis work)

def gaz(quote):
    return ev("rec_gazetteer", "g1", quote, about="other_mention", note=GAZ_NOTE)


def place_doc(key, words, kind, parents_=()):
    doc = {"place_id": uid(key), "schema_version": V, "redistribution_license": "CC0",
           "name_assertions": [claim(gaz(words), 0.4, name_as_written=words.split(",")[0], name_type="official",
                                     is_preferred=True)],
           "kind_assertions": [claim(gaz(words), 0.4, kind=kind)], "cited_record_ids": [uid("rec_gazetteer")],
           "extensions": dict(NOTE)}
    if parents_:
        doc["parent_place_assertions"] = [claim(gaz(q), 0.4, parent_place_id=uid(p), hierarchy="civil", date=d)
                                          for p, q, d in parents_]
    return doc


PLACES = [
    place_doc("place_erewhon", "Erewhon, a country.", "country"),
    place_doc("place_wrenshire", "Wrenshire, a county of Erewhon until 31 March 1974.", "county",
              [("place_erewhon", "Wrenshire, a county of Erewhon until 31 March 1974.",
                {"modifier": "to", "value": {"year": 1974, "month": 3, "day": 31}, "as_written": "until 31 March 1974"})]),
    place_doc("place_mossdale", "Mossdale, a district of Erewhon, formed 1 April 1974.", "district",
              [("place_erewhon", "Mossdale, a district of Erewhon, formed 1 April 1974.",
                {"modifier": "from", "value": {"year": 1974, "month": 4, "day": 1}, "as_written": "1 April 1974"})]),
    place_doc("place_tumblebrook", "Tumblebrook, a village of Erewhon.", "village",
              [("place_wrenshire", "In the county of Wrenshire until 31 March 1974.",
                {"modifier": "to", "value": {"year": 1974, "month": 3, "day": 31}, "as_written": "until 31 March 1974"}),
               ("place_mossdale", "In Mossdale District from 1 April 1974.",
                {"modifier": "from", "value": {"year": 1974, "month": 4, "day": 1}, "as_written": "from 1 April 1974"})]),
]
RESOLVE_PLACES = [("place_tumblebrook", "Tumblebrook"), ("place_tumblebrook", "Tumblebrook, Wrenshire")]

SHIP_NOTE = "the voyage is described on the lines that name the ship's master"
GROUPS = [{
    "group_id": uid("group_swallowtail"), "schema_version": V, "is_living_flag": False,
    "redistribution_license": "CC0", "group_kind": "ship_company",
    "name_assertions": [claim(ev("rec_passenger_swallowtail", "c1", "The brig Swallowtail, Abel Crane master",
                                 about="other_mention", note=SHIP_NOTE), 0.9,
                              name_as_written="The brig Swallowtail", name_type="as_recorded")],
    "fact_assertions": [
        claim(ev("rec_passenger_swallowtail", "c1", "sailed from Port Mirren on 2 April 1895", ref="e1",
                 about="other_mention", note=SHIP_NOTE), 0.9, fact_type="departed",
              date=dt(1895, 4, 2, as_written="2 April 1895"), place=place("Port Mirren")),
        claim(ev("rec_passenger_swallowtail", "c1", "Arrived at Port Erewhon 30 May 1895", ref="e2",
                 about="other_mention", note=SHIP_NOTE), 0.9, fact_type="arrived",
              date=dt(1895, 5, 30, as_written="30 May 1895"), place=place("Port Erewhon"))],
    "observed_in": [{"record_id": uid("rec_passenger_swallowtail")}], "extensions": dict(NOTE)}]
RESOLVE_GROUPS = [("group_swallowtail", "The brig Swallowtail")]


# ------------------------------------------------------------------ research (MaxTasks)

def _src(name_, url):
    return {"name": name_, "url": url}


def task(key, person, ttype, payload, priority=5, by="Genealogist", **more):
    """A task as the Orchestrator plans it (pending). `by` is the session that takes it up."""
    p = dict(payload)
    if person:
        p["person_id"] = uid(person)
    doc = {"task_id": uid(key), "schema_version": V, "task_type": ttype, "status": "pending", "priority": priority,
           "is_living_flag": False, "payload": p,
           "acceptance_criteria": ["a MaxRecord of what was found, copied word for word, or the exact search "
                                   "recorded as a step"],
           "extensions": dict(NOTE)}
    doc.update(more)
    return key, by, doc


C1930 = R + "census-1930/"
TASKS = [
    task("t_interview", None, "other", {"kind": "interview", "name_as_known": "Robin Quillfeather",
                                        "file": "staging/interview_2026-10-01.md"}, 10, by="Orchestrator"),
    task("t_gather", None, "extract_record", {"kind": "analysis",
                                              "look_for": "the documents the interview points to, and the ones "
                                                          "they lead to"}, 9),
    task("t_build", None, "build_tree", {"kind": "analysis",
                                         "look_for": "put the people the documents name into the tree, with their links"},
         8),
    task("t_census_1930", "ezra", "extract_record",
         {"name_as_known": "Ezra Quillfeather, born about 1921", "source": _src("Erewhon 1930 census (made up)", C1930),
          "look_for": "the household of Thaddeus Quillfeather in Tumblebrook with a son Ezra",
          "record_types": ["census_household"]}, 8),
    task("t_grave_ezra", "ezra", "extract_record",
         {"name_as_known": "Ezra Quillfeather", "look_for": "Ezra's gravestone in Tumblebrook",
          "source": _src("Wrenshire Graves Project (made up)", "https://graves.example.org/sample/"),
          "record_types": ["gravestone"]}, 7),
    task("t_births_ezra", "ezra", "extract_record",
         {"name_as_known": "Ezra Quillfeather, born about 1921",
          "source": _src("Erewhon Record Office: Tumblebrook birth registers", R + "births/tumblebrook/"),
          "look_for": "Ezra's birth entry, 1919 to 1924, under the name Quillfeather",
          "record_types": ["birth_register"]}, 7),
    task("t_larch_birth", "ezra", "extract_record",
         {"name_as_known": "Ezra Quillfeather, perhaps born Ezra Larch",
          "source": _src("Erewhon Record Office: Tumblebrook birth registers", R + "births/tumblebrook/"),
          "look_for": "Ezra's birth entry under other surnames, after Robin said he was adopted as a baby",
          "record_types": ["birth_register", "death_certificate"]}, 8),
    task("t_adoption", "ezra", "extract_record",
         {"name_as_known": "Ezra Quillfeather, born Ezra Larch",
          "source": _src("Wrenshire County Court adoption decrees (made up)", "https://court.example.org/sample/adoptions/"),
          "look_for": "the decree of Ezra's adoption by Thaddeus and Clementine Quillfeather",
          "record_types": ["adoption_record"]}, 8),
    task("t_main_parents", "ezra", "build_tree",
         {"name_as_known": "Ezra Quillfeather", "look_for": "which parents to show as Ezra's main parents"}, 6),
    task("t_baptism_ezra", "ezra", "extract_record",
         {"name_as_known": "Ezra Quillfeather",
          "source": _src("Mill Lane Chapel baptisms (made up)", CHAPEL + "baptisms/"),
          "look_for": "Ezra's baptism, as Larch or as Quillfeather (the gravestone says 1923; the register says 1921)",
          "record_types": ["parish_register"]}, 4),
    task("t_census_1900", "thaddeus", "extract_record",
         {"name_as_known": "Thaddeus Quillfeather, born about 1890",
          "source": _src("Erewhon 1900 census (made up)", R + "census-1900/"),
          "look_for": "Thaddeus as a boy in his parents' household", "record_types": ["census_household"]}, 6),
    task("t_census_1920", "thaddeus", "extract_record",
         {"name_as_known": "Thaddeus Quillfeather, born about 1890",
          "source": _src("Erewhon 1920 census (made up)", R + "census-1920/"),
          "look_for": "Thaddeus and Clementine's household", "record_types": ["census_household"]}, 6),
    task("t_baptism_thaddeus", "thaddeus", "extract_record",
         {"name_as_known": "Thaddeus Quillfeather, born about 1890",
          "source": _src("Mill Lane Chapel baptisms (made up)", CHAPEL + "baptisms/"),
          "look_for": "Thaddeus's baptism, and his parents' gravestone", "record_types": ["parish_register"]}, 6),
    task("t_death_thaddeus", "thaddeus", "extract_record",
         {"name_as_known": "Thaddeus Quillfeather", "source": _src("Erewhon Record Office: deaths", R + "deaths/"),
          "look_for": "Thaddeus's death certificate", "record_types": ["death_certificate"]}, 6),
    task("t_marriage_thaddeus", "thaddeus", "extract_record",
         {"name_as_known": "Thaddeus Quillfeather",
          "source": _src("Mill Lane Chapel marriages (made up)", CHAPEL + "marriages/"),
          "look_for": "the marriage of Thaddeus Quillfeather and Clementine Larkspur, about 1915 to 1920",
          "record_types": ["marriage_register", "parish_register"]}, 7),
    task("t_online_tree", "thaddeus_tree", "extract_record",
         {"name_as_known": "Thaddeus Quilfeather", "look_for": "an online tree's page for a Thaddeus Quilfeather",
          "source": _src("trees.example.org (made up)", "https://trees.example.org/sample/")}, 3),
    task("t_larkspur", "clementine", "extract_record",
         {"name_as_known": "Clementine Quillfeather, born Larkspur",
          "source": _src("Erewhon 1900 census and Mill Lane Chapel baptisms (made up)", R + "census-1900/"),
          "look_for": "Clementine as a girl with her parents", "record_types": ["census_household", "parish_register"]},
         6),
    task("t_clementine_death", "clementine", "extract_record",
         {"name_as_known": "Clementine Quillfeather, born about 1894",
          "source": _src("Erewhon Record Office: deaths (made up)", R + "deaths/"),
          "look_for": "Clementine Quillfeather's death record", "record_types": ["death_certificate"]}, 6),
    task("t_census_1940", "mabel", "extract_record",
         {"name_as_known": "Mabel Thistlewood", "source": _src("Erewhon 1940 census (made up)", R + "census-1940/"),
          "look_for": "Mabel in 1940, perhaps in her mother's new household", "record_types": ["census_household"]}, 6),
    task("t_baptism_mabel", "mabel", "extract_record",
         {"name_as_known": "Mabel Thistlewood, born 1924",
          "source": _src("Mill Lane Chapel registers (made up)", CHAPEL),
          "look_for": "Mabel's baptism, and her father's burial", "record_types": ["parish_register"]}, 7),
    task("t_step_link", "mabel", "build_tree",
         {"name_as_known": "Mabel Quillfeather, born Thistlewood", "look_for": "how Amos Mossbury is related to Mabel"},
         5),
    task("t_adoption_mabel", "mabel", "extract_record",
         {"name_as_known": "Mabel Thistlewood",
          "source": _src("Wrenshire County Court adoption decrees (made up)", "https://court.example.org/sample/adoptions/"),
          "look_for": "the decree of Mabel's adoption by her step-father", "record_types": ["adoption_record"]}, 5),
    task("t_godparent", "mabel", "build_tree",
         {"name_as_known": "Mabel Quillfeather", "look_for": "who stood as Mabel's godparent"}, 4),
    task("t_census_1960", "alden", "extract_record",
         {"name_as_known": "Alden Ezra Quillfeather, born 1956",
          "source": _src("Erewhon 1960 census (made up)", R + "census-1960/"),
          "look_for": "Alden as a child in Ezra and Mabel's household, and his birth certificate",
          "record_types": ["census_household", "birth_certificate"]}, 7),
    task("t_death_alden", "alden", "extract_record",
         {"name_as_known": "Alden Ezra Quillfeather", "source": _src("Erewhon Record Office", R),
          "look_for": "Alden's death certificate and his marriage to June", "record_types": ["death_certificate"]}, 7),
    task("t_gazette_index", "walter", "extract_record",
         {"name_as_known": "Walter Bramblecott, died about 2004",
          "source": _src("Wrenshire Gazette index (made up)", "https://news.example.org/sample/index/"),
          "look_for": "a death notice for Walter Bramblecott", "record_types": ["newspaper_article"]}, 6),
    task("t_gazette_archive", "walter", "extract_record",
         {"name_as_known": "Walter Bramblecott, died about 2004",
          "source": _src("Wrenshire Gazette archive (made up)", "https://news.example.org/sample/archive/"),
          "look_for": "the death notice itself, March 2004", "record_types": ["newspaper_article"]}, 6),
    task("t_iris_obit", "iris", "extract_record",
         {"name_as_known": "Iris Bramblecott, born Fenwhistle",
          "source": _src("Wrenshire Gazette (made up)", "https://news.example.org/sample/wrenshire-gazette/"),
          "look_for": "Iris's obituary", "record_types": ["obituary"]}, 6),
    task("t_iris_archive", "iris", "fetch_internet_archive",
         {"name_as_known": "Iris Bramblecott, born Fenwhistle",
          "source": _src("Wrenshire newspaper scans (made up)", "https://archive.example.org/sample/wrenshire/"),
          "look_for": "Iris's marriage announcement", "record_types": ["newspaper_article"]}, 5, retry_count=3),
    task("t_iris_birth", "iris", "extract_record",
         {"name_as_known": "Iris Fenwhistle, born about 1933",
          "source": _src("Erewhon Record Office: births (made up)", R + "births/"),
          "look_for": "Iris Fenwhistle's birth record, about 1933", "record_types": ["birth_certificate"]}, 8),
    task("t_passenger", "josiah", "extract_record",
         {"name_as_known": "Josiah Larch", "source": _src("Port Erewhon arrival lists (made up)", R + "passenger-lists/"),
          "look_for": "when Josiah and Martha Larch came to Erewhon", "record_types": ["passenger_list"]}, 5),
    task("t_voyage", "josiah", "build_tree",
         {"name_as_known": "Josiah Larch", "look_for": "the ship's company Josiah and Martha sailed with"}, 4),
    task("t_crane_log", "josiah", "extract_record",
         {"name_as_known": "Josiah Larch", "look_for": "the master's log of the Swallowtail, 1895",
          "source": _src("Port Erewhon harbour office (made up)", "https://harbour.example.org/sample/")}, 3),
    task("t_duplicate", "nell", "extract_record",
         {"name_as_known": "Nell Larch", "look_for": "Nell's baptism (planned twice by mistake)"}, 2),
    task("t_places", None, "build_tree", {"kind": "analysis", "look_for": "the places the documents name"}, 3),
]
TASK_PEOPLE = {key: next(k for k in PEOPLE if uid(k) == doc["payload"]["person_id"])
               for key, _by, doc in TASKS if doc["payload"].get("person_id")}
EARLY = [t for t in TASKS if not t[2]["payload"].get("person_id")]    # planned before anyone is in the file
YES = {
    "t_adoption": "Yes. Grandad Ezra was adopted by Thaddeus and Clementine; please record it.",
    "t_adoption_mabel": "Yes, Grandma Mabel was adopted by her step-father Amos. You can record it.",
    "t_main_parents": "Please show Thaddeus and Clementine as Grandad Ezra's main parents; they raised him.",
}

# (task, source, query, found, verdict, person key for the "nothing found" list)
STEPS = [
    ("t_census_1930", "Erewhon 1930 census (made up)", "Quillfeather, Tumblebrook", "Thaddeus 40, Clementine 36, Ezra 9",
     "key_finding", None),
    ("t_grave_ezra", "Wrenshire Graves Project (made up)", "Quillfeather, Tumblebrook churchyard",
     "One stone: Ezra and Mabel Quillfeather", "key_finding", None),
    ("t_births_ezra", "Erewhon Record Office: Tumblebrook birth registers", "Quillfeather births 1919-1924",
     "no entry", "dead_end", "ezra"),
    ("t_larch_birth", "Erewhon Record Office: Tumblebrook birth registers", "Ezra, births 1920-1922, any surname",
     "1921 June 4: Ezra, son of Nell Larch", "key_finding", None),
    ("t_census_1900", "Erewhon 1900 census (made up)", "Thaddeus Quillfeather, Wrenshire",
     "Two boys named Thaddeus, aged 10 and 11, in two villages", "inconclusive", None),
    ("t_census_1920", "Erewhon 1920 census (made up)", "Thaddeus Quillfeather, Port Erewhon",
     "Thaddeus Quillfeather, 49, born 1871", "ruled_out", None),
    ("t_gazette_index", "Wrenshire Gazette index (made up)", "Bramblecott, 2004",
     "W. Bramblecott, death notice, March 2004", "usable", None),
    ("t_gazette_archive", "Wrenshire Gazette archive", "Bramblecott death notice March 2004",
     "the site does not allow automated searching", "inaccessible", "walter"),
]
# (task, final status, verdict, note) -- the order the work finished in
FINISH = [
    ("t_interview", "done", "infra", "The interview is written down word for word. Every answer is a lead to check, "
                                     "not a proven fact."),
    ("t_census_1930", "done", "key_finding", "Ezra, son, age 9, in Thaddeus's household."),
    ("t_grave_ezra", "done", "key_finding", "A stone for Ezra (born 1923, died 1987) and Mabel (1924 - 1999)."),
    ("t_births_ezra", "done", "dead_end", "No birth entry for an Ezra Quillfeather in 1919 to 1924."),
    ("t_larch_birth", "done", "key_finding", "Found him as Ezra Larch, born 4 June 1921 to Nell Larch."),
    ("t_adoption", "done", "key_finding", "The 1923 decree: Thaddeus and Clementine adopted Ezra Larch."),
    ("t_main_parents", "done", "infra", "Robin asked for the adoptive parents to be shown as Ezra's main parents."),
    ("t_census_1900", "done", "inconclusive", "Two Thaddeus Quillfeathers of about the right age in Wrenshire in "
                                              "1900; nothing yet says which one is ours."),
    ("t_census_1920", "done", "ruled_out", "The 1920 Port Erewhon Thaddeus was born in 1871: someone else."),
    ("t_baptism_thaddeus", "done", "key_finding", "Thaddeus's 1890 baptism names Obadiah and Prudence."),
    ("t_death_thaddeus", "done", "key_finding", "Died 14 November 1950; his son E. Quillfeather gave the information."),
    ("t_online_tree", "done", "usable", "An online tree has a Thaddeus Quilfeather born 1888: a lead only."),
    ("t_larkspur", "done", "key_finding", "Clementine, 6, with Edmund and Rose Larkspur in 1900, and her baptism."),
    ("t_census_1940", "done", "key_finding", "Mabel is Amos Mossbury's step-daughter in 1940."),
    ("t_baptism_mabel", "done", "key_finding", "Mabel's baptism names Silas and Hester; Clementine was her godmother."),
    ("t_step_link", "done", "infra", "Recorded Amos as Mabel's step-father: the 1940 census lists her as his "
                                     "step-daughter."),
    ("t_adoption_mabel", "done", "key_finding", "Amos adopted Mabel in 1942."),
    ("t_godparent", "done", "infra", "Clementine Quillfeather stood as Mabel's godmother."),
    ("t_census_1960", "done", "key_finding", "Alden, 4, son, in Ezra and Mabel's household; his birth certificate."),
    ("t_death_alden", "done", "key_finding", "Alden's death certificate and his 1985 marriage to June."),
    ("t_gazette_index", "done", "usable", "The index lists \"W. Bramblecott\", death notice, March 2004: a lead."),
    ("t_gazette_archive", "escalated", None, "Wrenshire Gazette archive: its terms do not allow automated searching. "
                                             "Please search it yourself for: Bramblecott, death notices, March 2004."),
    ("t_iris_obit", "done", "key_finding", "Iris's obituary names her daughter June."),
    ("t_iris_archive", "failed", None, "The archive was not answering (tried three times)."),
    ("t_passenger", "done", "key_finding", "Josiah and Martha Larch arrived on the brig Swallowtail in May 1895."),
    ("t_voyage", "done", "infra", "Recorded the Swallowtail's company and its two dated facts."),
    ("t_places", "done", "infra", "Recorded Tumblebrook and the county change of 1974."),
    ("t_build", "done", "infra", "Every person and link from the documents is in the tree."),
    ("t_gather", "done", "key_finding", "Twenty-five documents copied word for word."),
    ("t_crane_log", "on_hold", None, None),
    ("t_duplicate", "cancelled", None, None),
]
TAKEN_UP = {key for key, *_ in FINISH} - {"t_duplicate"} | {"t_marriage_thaddeus"}


# ------------------------------------------------------------------ writing it

class Builder:
    """Runs maxgen_db.py commands into one new database, the way the agents run them."""

    def __init__(self, db, schemas=None, tmp=None):
        self.db = str(db)
        self.pre = ["--schemas", str(schemas)] if schemas else []
        self.tmp = Path(tmp)
        self.n = 0
        self.sessions = {}

    def run(self, *argv, as_=None) -> str:
        """One maxgen_db.py command, output captured. Any refusal stops the build."""
        argv = [str(x) for x in argv]
        if as_:
            argv += ["--as", self.sessions[as_]]
        buf, code = io.StringIO(), 0
        with contextlib.redirect_stdout(buf):
            try:
                maxgen_db.main(self.pre + argv)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        out = buf.getvalue()
        if code != 0:
            raise RuntimeError("maxgen_db.py refused %s: %s" % (" ".join(argv[:2]), out.strip()))
        return out

    def file(self, doc) -> str:
        self.n += 1
        p = self.tmp / ("doc%04d.json" % self.n)
        p.write_text(json.dumps(doc), encoding="utf-8")
        return str(p)

    def session(self, name_, role):
        out = self.run("session", self.db, "--name", name_, "--kind", "ai", "--role", role, "--model-name",
                       "sample-model", "--run-id", "sample-run")
        self.sessions[role] = re.search(r"MAXGEN_SESSION=(\S+)", out).group(1)

    def add(self, doc, task_key=None, as_="Genealogist"):
        args = ["add", self.db, self.file(doc)] + (["--task", uid(task_key)] if task_key else [])
        return self.run(*args, as_=as_)

    def rows(self, sql, *args):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def claim_id(self, person, list_name, record_key, value=None):
        for r in self.rows("SELECT assertion_id, holder_id FROM claim_index WHERE holder_table='max_person' AND "
                           "holder_id=? AND list_name=? AND source_record_id=?", uid(person), list_name, uid(record_key)):
            if value is None:
                return r["assertion_id"]
            doc = json.loads(self.rows("SELECT doc FROM max_person WHERE person_id=?", uid(person))[0]["doc"])
            for c in doc.get(list_name) or []:
                if c.get("assertion_id") == r["assertion_id"] and value in json.dumps(c):
                    return r["assertion_id"]
        raise RuntimeError("no %s claim on %s citing %s" % (list_name, person, record_key))

    def membership(self, child, set_kind):
        return self.rows("SELECT membership_id FROM membership_index WHERE child_id=? AND set_kind=? AND status='active'",
                         uid(child), set_kind)[0]["membership_id"]


def proof_doc(b) -> dict:
    """A proof waiting for the person's decision: Ezra's year of birth (register 1921, gravestone 1923)."""
    register = b.claim_id("ezra", "birth_assertions", "rec_birth_ezra")
    census_ = b.claim_id("ezra", "birth_assertions", "rec_census_1930")
    grave = b.claim_id("ezra", "birth_assertions", "rec_grave_ezra")
    return {"task_id": uid("t_proof_ezra_birth"), "schema_version": V, "task_type": "resolve_conflict",
            "status": "done", "priority": 6, "is_living_flag": False,
            "payload": {"kind": "analysis", "person_id": uid("ezra"), "name_as_known": "Ezra Quillfeather"},
            "result": {"verdict": "key_finding", "verdict_reason": "The birth register settles it: 4 June 1921.",
                       "proof": {
                           "subject": [{"kind": "person", "id": uid("ezra")}, {"kind": "claim", "id": grave}],
                           "question": "Was Ezra born in 1921, as the birth register says, or in 1923, as his "
                                       "gravestone says?",
                           "relied_on": [{"kind": "claim", "assertion_id": register,
                                          "note": "the 1921 birth register entry, written at the time"},
                                         {"kind": "claim", "assertion_id": census_,
                                          "note": "aged 9 in April 1930, which fits 1921 and not 1923"}],
                           "argument": "The birth register was written within days of the birth and names his "
                                       "mother. The 1930 census age agrees with it. The gravestone was carved in "
                                       "1987, more than sixty years later, by people who may only have known the "
                                       "year of his adoption, 1923.",
                           "proof_form": "argument",
                           "conclusion": {"statement": "Ezra was born on 4 June 1921. The gravestone's 1923 is "
                                                       "mistaken and is marked as disputed.",
                                          "settles": [{"assertion_id": grave, "status": "disputed"}]}}},
            "extensions": dict(NOTE)}


def build(db_path, schemas=None) -> dict:
    """Write the made-up family into a NEW database. Returns {"people", "records", "tasks", "places",
    "groups", "sessions"}, each mapping a key to its id."""
    db = Path(db_path)
    if db.exists():
        raise FileExistsError("%s already exists; sample_family.py never overwrites a file" % db)
    with tempfile.TemporaryDirectory(prefix="sample-family-") as d:
        b = Builder(db, schemas, d)
        b.run("init", db)
        for name_, role in (("Orchestrator", "Orchestrator"), ("Genealogist", "Genealogist"),
                            ("QualityControl", QC)):
            b.session(name_, role)

        def plan(tasks):
            """The Orchestrator plans (every task starts pending); the one who does it takes it up."""
            for _key, _by, doc in tasks:
                b.add(doc, as_="Orchestrator")
            for key, by, _doc in tasks:
                if key in TAKEN_UP:
                    b.run("task", db, uid(key), "processing", as_=by)

        # 1. the interview, the gathering of documents, and the tree-building work
        plan(EARLY)
        for doc in RECORDS:
            b.add(doc, RECORD_TASK[doc["record_id"]],
                  as_="Orchestrator" if RECORD_TASK[doc["record_id"]] == "t_interview" else "Genealogist")
        # 2. people (no family links inside them), then a misreading withdrawn (retract, never delete)
        for key in PEOPLE:
            b.add(person_doc(key), "t_build")
        for key, (words, reason) in WITHDRAWN.items():
            doc = json.loads(b.rows("SELECT doc FROM max_person WHERE person_id=?", uid(key))[0]["doc"])
            aid = next(c["assertion_id"] for c in doc["name_assertions"] if c["name_as_written"] == words)
            b.run("retract", db, aid, "--task", uid("t_build"), "--reason", reason, as_="Genealogist")
        # 3. the research about each person (a task may name only someone already in the file)
        plan([t for t in TASKS if t not in EARLY])
        # 4. the person's yes, relayed by the Orchestrator, before an adoption or a choice of main parents
        for key, words in YES.items():
            b.run("task", db, uid(key), "processing", "--answer", "accepted", "--note", words, as_="Orchestrator")
        # 5. family links: the helper finds or makes every MaxRelationship
        for task_key, req in LINKS:
            b.run("link", db, b.file(req), "--task", uid(task_key), as_="Genealogist")
        b.run("prefer", db, b.membership("ezra", "adoptive"), "--task", uid("t_main_parents"), as_="Genealogist")
        # 6. places and the voyage group (analysis work), attached to the claims that name them
        for doc in PLACES:
            b.add(doc, "t_places")
        for key, words in RESOLVE_PLACES:
            b.run("resolve", db, "place", uid(key), "--task", uid("t_places"), "--written", words, as_="Genealogist")
        for doc in GROUPS:
            b.add(doc, "t_voyage")
        for key, words in RESOLVE_GROUPS:
            b.run("resolve", db, "group", uid(key), "--task", uid("t_voyage"), "--written", words, as_="Genealogist")
        # 7. searches, and how each piece of work ended
        for t, source, query, found, verdict, person in STEPS:
            args = ["step", db, "--task", uid(t), "--source", source, "--query", query, "--found", found,
                    "--verdict", verdict] + (["--person", uid(person)] if person else [])
            b.run(*args, as_="Genealogist")
        for t, status, verdict, note in FINISH:
            by = "Orchestrator" if t in ("t_interview", "t_duplicate") else "Genealogist"
            args = ["task", db, uid(t), status] + (["--verdict", verdict] if verdict else []) +                 (["--note", note] if note else [])
            b.run(*args, as_=by)
        # 8. a proof waiting for the person's own decision on their page
        b.add(proof_doc(b), as_="Genealogist")
        # 9. Quality Control: documents first, then people, then links, places and the group (R-26)
        for doc in RECORDS:
            if doc["record_id"] not in {uid(k) for k in STAYS_WAITING}:
                b.run("review", db, doc["record_id"], "pass", "--by", QC,
                      "--note", "sample: opened the source; the transcription matches", as_=QC)
        for key in PEOPLE:
            if key in STAYS_WAITING_PEOPLE:
                continue
            if key in TURNED_DOWN:
                b.run("review", db, uid(key), "fail", "--by", QC, "--note", TURNED_DOWN[key], as_=QC)
            else:
                b.run("review", db, uid(key), "pass", "--by", QC,
                      "--note", "sample: every claim checked against its document", as_=QC)
        waiting = {uid(k) for k in STAYS_WAITING_PEOPLE}
        for r in b.rows("SELECT relationship_id, doc FROM max_relationship WHERE stage='staged' AND lifecycle='active'"):
            named = set(re.findall(r'"(?:person_id|parent_id|child_id)":"([0-9a-f-]{36})"', r["doc"]))
            if not named & waiting:
                b.run("review", db, r["relationship_id"], "pass", "--by", QC,
                      "--note", "sample: each link checked against its document", as_=QC)
        for doc in PLACES:
            b.run("review", db, doc["place_id"], "pass", "--by", QC, "--note", "sample: checked against the gazetteer",
                  as_=QC)
        for doc in GROUPS:
            b.run("review", db, doc["group_id"], "pass", "--by", QC, "--note", "sample: checked against the list",
                  as_=QC)
    return {"people": {k: uid(k) for k in PEOPLE},
            "records": {k: uid(k) for k in RECORD_KEYS},
            "tasks": dict({key: uid(key) for key, _by, _doc in TASKS}, t_proof_ezra_birth=uid("t_proof_ezra_birth")),
            "places": {k: uid(k) for k in ("place_erewhon", "place_wrenshire", "place_mossdale", "place_tumblebrook")},
            "groups": {k: uid(k) for k, _w in RESOLVE_GROUPS},
            "sessions": dict(b.sessions)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write a made-up family into a new MAXGEN 2.0 genealogy.db.")
    ap.add_argument("out", help="the new database file to write (it must not exist yet)")
    ap.add_argument("--schemas", help="folder holding the .schema.json files (as in maxgen_db.py)")
    a = ap.parse_args(argv)
    try:
        build(a.out, a.schemas)
    except FileExistsError:
        print("%s already exists. Choose a new file name; this program never overwrites a file." % a.out)
        return 1
    except RuntimeError as e:
        print(str(e))
        return 1
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        maxgen_db.main(["status", a.out])
    print(buf.getvalue().rstrip())
    print(MADE_UP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
