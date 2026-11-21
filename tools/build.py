"""Static site generator for opengenealogyai.org.

Run:  python tools/build.py          (from the site/ folder, Python 3.12 with `markdown`)

Inputs
  content/*.md              ordinary pages (front matter: title, nav, out, description)
  content/schemas/*.md      one per schema (front matter adds: schema, podcast, fixture, tagline)
  schemas/maxgen/           synced by tools/sync_schemas.py (never edited by hand)
  podcasts source           E:\\BuildGenealogy\\podcast-scripts\\audio (study guides copied in)

Outputs: HTML files written next to their folders (index.html per section) so URLs are clean:
  /standard/  /schemas/  /schemas/maxperson/  /faq/  /podcasts/  /versions/  /governance/ ...
"""
from __future__ import annotations
import hashlib
import html
import json
import re
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

import markdown

SITE = Path(__file__).resolve().parents[1]
CONTENT = SITE / "content"
SCHEMA_DIR = SITE / "schemas" / "maxgen"
# Pinned copies of the published text. Never read the schema repo's working files here: they can
# hold an unreleased version (2026-11-16 a rebuild pulled the unpublished 2.0 changelog onto /versions/).
FIXTURES = CONTENT / "fixtures-v1"
CHANGELOG = CONTENT / "changelog-v1.html"   # rendered HTML exactly as published for 1.13
PODCAST_PUBDATE = "Mon, 07 Sep 2026 19:00:00 +0000"   # site launch; fixed so rebuilds do not re-date episodes
AUDIO_SRC = Path(r"E:\BuildGenealogy\podcast-scripts\audio")

BASE_URL = "https://opengenealogyai.org"
MEDIA_BASE = f"{BASE_URL}/podcasts/audio"   # MP3s ship inside the site repo (GitHub Pages); absolute so the RSS enclosures validate

# MAXGEN 2.0 — every input is a pinned copy written by tools/sync_v2.py (never the schema repo itself)
V2_DIR = SITE / "schemas" / "maxgen" / "v2"
V2_PIN = CONTENT / "v2"
V2_CHANGELOG = V2_PIN / "SCHEMA_CHANGELOG.md"
V2_DEFS_FILE = "maxgen-defs.schema.json"
# (page slug, title, schema file, example folder, podcast episode number or None)
V2_KINDS = [
    ("maxrecord", "MaxRecord", "raw-record.schema.json", "record", 1),
    ("maxperson", "MaxPerson", "person.schema.json", "person", 2),
    ("maxrelationship", "MaxRelationship", "relationship.schema.json", "relationship", None),
    ("maxtask", "MaxTask", "task-queue.schema.json", "task", 3),
    ("maxsource", "MaxSource", "source.schema.json", "source", 5),
    ("maxplace", "MaxPlace", "place.schema.json", "place", None),
    ("maxgroup", "MaxGroup", "group.schema.json", "group", None),
    ("maxevent", "MaxEvent", "event.schema.json", "event", None),
    ("maxname", "MaxName", "name.schema.json", "name", 7),
    ("maxrecognition", "MaxRecognition", "recognition.schema.json", "recognition", 6),
    ("maxdna", "MaxDNA", "dna.schema.json", "dna", 4),
]
GITHUB_REPO = "https://github.com/OpenGenealogyai/opengenealogyai/blob/main/"
V2_ENTRY_ANCHOR = "/changelog/#v20-2026-10-16"
V2_PREV_DIR = SITE / "schemas" / "maxgen" / "v2.0"      # the 2.0 release, frozen; 2.1 is compared against it
V21_ANCHOR_ID = "v21-2026-11-21"
V21_ANCHOR = f"/changelog/#{V21_ANCHOR_ID}"

NAV = [
    ("The Standard", "/standard/"),
    ("Schemas", "/schemas/"),
    ("FAQ", "/faq/"),
    ("Podcasts", "/podcasts/"),
    ("Versions", "/versions/"),
    ("Governance", "/governance/"),
    ("Contribute", "/contribute/"),
]

MD = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "attr_list", "md_in_html"],
                       extension_configs={"toc": {"permalink": False, "toc_depth": "2-3"}})


def md(text: str) -> str:
    MD.reset()
    return MD.convert(text)


def md_toc(text: str) -> tuple[str, str]:
    """Convert Markdown and also return the generated table of contents (h2/h3) as HTML."""
    MD.reset()
    body = MD.convert(text)
    toc = MD.toc if MD.toc and "<li>" in MD.toc else ""
    return body, toc


def toc_from_html(body_html: str) -> str:
    """Build a table of contents from h2/h3 tags that already carry ids (for pages assembled from parts)."""
    items = re.findall(r'<h([23]) id="([^"]+)">(.*?)</h\1>', body_html, flags=re.S)
    if not items:
        return ""
    out = ['<div class="toc"><ul>']
    for level, hid, text in items:
        text = re.sub(r"<[^>]+>", "", text)
        out.append(f'<li class="lvl{level}"><a href="#{hid}">{text}</a></li>')
    out.append("</ul></div>")
    return "".join(out)


def front_matter(text: str) -> tuple[dict, str]:
    meta: dict = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        block = text[3:end].strip()
        text = text[end + 4:]
        for line in block.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta, text.lstrip("\n")


def load_index() -> dict:
    return json.loads((SCHEMA_DIR / "index.json").read_text(encoding="utf-8"))


INDEX = load_index()
CURRENT = INDEX["current"]
YEAR = datetime.now().year


ORG_JSONLD = {
    "@type": "Organization",
    "@id": f"{BASE_URL}/#org",
    "name": "OpenGenealogyAI",
    "url": BASE_URL + "/",
    "logo": f"{BASE_URL}/assets/img/logo/maxgen-icon-dark-512.png",
    "sameAs": ["https://github.com/OpenGenealogyai"],
    "description": "Steward of MAXGEN, the open, public-domain genealogy data standard.",
}


def jsonld(*objs) -> str:
    graph = {"@context": "https://schema.org", "@graph": list(objs)}
    return '<script type="application/ld+json">' + json.dumps(graph, ensure_ascii=False) + "</script>"


def breadcrumb_ld(path: str, title: str) -> str:
    crumbs = [{"@type": "ListItem", "position": 1, "name": "MAXGEN", "item": BASE_URL + "/"}]
    parts = [p for p in path.strip("/").split("/") if p]
    acc = ""
    for i, part in enumerate(parts):
        acc += "/" + part
        name = title if i == len(parts) - 1 else part.replace("-", " ").title()
        crumbs.append({"@type": "ListItem", "position": i + 2, "name": name, "item": f"{BASE_URL}{acc}/"})
    if len(crumbs) == 1:
        return ""
    return '<script type="application/ld+json">' + json.dumps({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": crumbs}, ensure_ascii=False) + "</script>"


def layout(title: str, body: str, active: str = "", description: str = "", extra_head: str = "",
           path: str = "/", ld: str = "", toc: str = "") -> str:
    nav = "".join(
        f'<a href="{href}"{" class=\"active\"" if href == active else ""}>{label}</a>'
        for label, href in NAV
    )
    desc = html.escape(description or "MAXGEN — the open, probabilistic genealogy data standard. Eleven JSON record schemas plus one shared building-blocks file, CC0.")
    full_title = html.escape(f"{title} — MAXGEN · OpenGenealogyAI")
    url = BASE_URL + path
    if toc:
        main_inner = f'<div class="with-toc"><aside class="toc-side" aria-label="On this page"><p class="toc-title">On this page</p>{toc}</aside><div class="content">{body}</div></div>'
    else:
        main_inner = body
    body = main_inner
    ld = ld + breadcrumb_ld(path, title)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{full_title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="MAXGEN · OpenGenealogyAI">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{BASE_URL}/assets/img/logo/maxgen-og-1200x630.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" href="/assets/img/logo/favicon-32.png" type="image/png" sizes="32x32">
<link rel="icon" href="/assets/img/logo/maxgen-icon-dark.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/assets/img/logo/favicon-180.png">
<link rel="stylesheet" href="/assets/css/site.css">
<link rel="alternate" type="application/rss+xml" title="MAXGEN Podcast" href="/podcasts/feed.xml">
{ld}
{extra_head}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="/"><img class="lockup" src="/assets/img/logo/maxgen-horizontal-dark.svg" alt="MAXGEN — Maxwell Genealogy Standard" width="180" height="68"><span class="brand-text"><small>by OpenGenealogyAI</small></span></a>
    <button class="nav-toggle" aria-expanded="false" aria-controls="nav" onclick="var n=document.getElementById('nav');var o=n.classList.toggle('open');this.setAttribute('aria-expanded',o)">Menu</button>
    <nav id="nav" class="nav">{nav}<a class="gh" href="https://github.com/OpenGenealogyai" rel="noopener">GitHub</a></nav>
  </div>
</header>
<main id="main" class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="wrap">
    <p><strong>MAXGEN</strong> — the Maxwell Genealogy Standard. Current release <a href="/versions/">v{CURRENT}</a>. The standard and its schemas are dedicated to the public domain under <a href="/license/">CC0</a>.</p>
    <p>Stewarded by <a href="/governance/">OpenGenealogyAI</a> · <a href="https://github.com/OpenGenealogyai" rel="noopener">GitHub</a> · <a href="/podcasts/feed.xml">Podcast RSS</a> · <a href="/contribute/">Ask a question</a></p>
    <p class="muted">© {YEAR} OpenGenealogyAI. Site text CC0. Logo and the names OpenGenealogyAI and MAXGEN are reserved for the canonical standard.</p>
  </div>
</footer>
</body>
</html>
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))   # LF everywhere, whatever the OS


# ---------------------------------------------------------------- schema helpers
def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / "v1" / f"{name}.schema.json").read_text(encoding="utf-8"))


def type_of(prop: dict) -> str:
    t = prop.get("type")
    if isinstance(t, list):
        t = " | ".join(t)
    if t == "array":
        items = prop.get("items", {})
        inner = items.get("type") or ("object" if "properties" in items or "$ref" in items else "any")
        return f"array of {inner}"
    if "const" in prop:
        return f"const {json.dumps(prop['const'])}"
    if "enum" in prop:
        return "enum"
    if "$ref" in prop:
        return prop["$ref"].split("/")[-1]
    return t or "object"


def first_sentence(s: str) -> str:
    s = (s or "").strip().replace("\n", " ")
    m = re.match(r"(.+?[.!?])(\s|$)", s)
    return (m.group(1) if m else s)[:220]


def fields_table(schema: dict) -> str:
    req = set(schema.get("required", []))
    rows = []
    for name, prop in schema.get("properties", {}).items():
        badge = '<span class="req">required</span>' if name in req else ""
        rows.append(
            f"<tr><td><code>{html.escape(name)}</code> {badge}</td>"
            f"<td class=\"type\">{html.escape(type_of(prop))}</td>"
            f"<td>{html.escape(first_sentence(prop.get('description', '')))}</td></tr>"
        )
    return (
        '<div class="table-wrap"><table class="fields"><thead><tr><th>Field</th><th>Type</th><th>Meaning</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def enum_block(schema: dict, field: str) -> str:
    prop = schema.get("properties", {}).get(field, {})
    vals = prop.get("enum") or prop.get("items", {}).get("enum")
    if not vals:
        return ""
    return "<p class=\"enum\">" + " ".join(f"<code>{html.escape(str(v))}</code>" for v in vals) + "</p>"


def example_json(rel: str) -> str:
    if not rel:
        return ""
    p = FIXTURES / rel
    if not p.is_file():
        return "<p class=\"muted\">Example coming soon.</p>"
    data = json.loads(p.read_text(encoding="utf-8"))
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return f"<pre class=\"json\"><code>{html.escape(text)}</code></pre>"


# ---------------------------------------------------------------- podcasts
EPISODES = [
    # (number, file stem, title, blurb, seconds, bytes)
    (0, "MAXGEN-00-overview-all-schemas", "The MAXGEN Overview", "Why genealogy data needs a common language, the seven schemas, and the design philosophy behind them.", 1297, 41730483),
    (1, "MAXGEN-01-maxrecord", "MaxRecord — Source documents as written", "Evidence versus interpretation, and why the clerk who wrote 'Makeswell' must never be corrected.", 1540, 49556371),
    (2, "MAXGEN-02-maxperson-v2", "MaxPerson — The person who might be your ancestor", "Assertions not facts, probabilistic parents, composite confidence, and the merge model.", 1366, 43958964),
    (3, "MAXGEN-03-maxtask", "MaxTask — Distributing the work", "Task types, the verdict vocabulary, acceptance criteria, and the anti-hallucination rule.", 1469, 47286056),
    (4, "MAXGEN-04-maxdna", "MaxDNA — Genetic evidence without giving up privacy", "Hashed kit IDs, consent, endogamy, phasing, and how DNA boosts documentary confidence.", 1403, 45161413),
    (5, "MAXGEN-05-maxsource", "MaxSource — The routing brain", "Where records hide: coverage, access, provenance, and semantic search over sources.", 1096, 35269268),
    (6, "MAXGEN-06-maxrecognition", "MaxRecognition — Teaching machines to read", "OCR and handwriting provenance, word-level confidence, labelled inference, and consensus.", 1521, 48937591),
    (7, "MAXGEN-07-maxname", "MaxName — Why Maxwell and Makeswell are the same family", "Variants, cognates, phonetic keys, and the council design process behind the newest schema.", 1411, 45419257),
]
SCHEMA_EPISODE = {"raw-record": 1, "person": 2, "task-queue": 3, "dna": 4, "source": 5, "recognition": 6, "name": 7}


def mmss(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def player(ep, note: bool = True) -> str:
    n, stem, title, blurb, secs, size = ep
    changed = (f'<p class="ep-note">This episode describes MAXGEN v1.11. '
               f'<a href="{V2_ENTRY_ANCHOR}">What changed since: the v2.0 changelog entry</a>.</p>') if note else ""
    return f"""<div class="episode" id="ep{n}">
  <div class="ep-head"><span class="ep-num">Episode {n}</span><h3>{html.escape(title)}</h3><span class="ep-len">{mmss(secs)}</span></div>
  <p>{html.escape(blurb)}</p>
  {changed}
  <audio controls preload="none" src="{MEDIA_BASE}/{stem}.mp3"></audio>
  <p class="ep-links"><a href="{MEDIA_BASE}/{stem}.mp3">Download MP3</a> ({size // 1_000_000} MB)</p>
</div>"""


def build_podcasts() -> None:
    intro_meta, intro = front_matter((CONTENT / "podcasts.md").read_text(encoding="utf-8"))
    body = md(intro) + "\n".join(player(e) for e in EPISODES)
    series = {
        "@type": "PodcastSeries", "@id": f"{BASE_URL}/podcasts/#series",
        "name": "MAXGEN — the open genealogy standard, explained",
        "url": f"{BASE_URL}/podcasts/", "webFeed": f"{BASE_URL}/podcasts/feed.xml",
        "publisher": {"@id": f"{BASE_URL}/#org"}, "inLanguage": "en",
        "description": "Eight conversations explaining MAXGEN: one overview and one episode per schema.",
    }
    eps = [{
        "@type": "PodcastEpisode", "name": t, "description": b, "url": f"{BASE_URL}/podcasts/#ep{n}",
        "episodeNumber": n, "timeRequired": f"PT{s // 60}M{s % 60}S", "partOfSeries": {"@id": f"{BASE_URL}/podcasts/#series"},
        "associatedMedia": {"@type": "MediaObject", "contentUrl": f"{MEDIA_BASE}/{stem}.mp3", "encodingFormat": "audio/mpeg"},
    } for n, stem, t, b, s, _ in EPISODES]
    write(SITE / "podcasts" / "index.html",
          layout("Podcasts", body, "/podcasts/", intro_meta.get("description", ""), path="/podcasts/", ld=jsonld(ORG_JSONLD, series, *eps)))

    # (study guides are deliberately not published — Garlon, 2026-09-07)

    # RSS feed
    now = PODCAST_PUBDATE
    items = []
    for n, stem, title, blurb, secs, size in EPISODES:
        items.append(f"""  <item>
    <title>{html.escape(title)}</title>
    <description>{html.escape(blurb)}</description>
    <link>{BASE_URL}/podcasts/#ep{n}</link>
    <guid isPermaLink="false">maxgen-podcast-{n}</guid>
    <enclosure url="{MEDIA_BASE}/{stem}.mp3" length="{size}" type="audio/mpeg"/>
    <itunes:duration>{mmss(secs)}</itunes:duration>
    <pubDate>{now}</pubDate>
  </item>""")
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>MAXGEN — the open genealogy standard, explained</title>
  <link>{BASE_URL}/podcasts/</link>
  <atom:link href="{BASE_URL}/podcasts/feed.xml" rel="self" type="application/rss+xml"/>
  <description>Eight conversations explaining MAXGEN, the open probabilistic genealogy data standard: one overview and one episode per schema.</description>
  <language>en-us</language>
  <itunes:author>OpenGenealogyAI</itunes:author>
  <itunes:image href="{BASE_URL}/assets/img/ogai-logo.png"/>
  <itunes:explicit>false</itunes:explicit>
  <itunes:category text="Education"/>
{chr(10).join(items)}
</channel>
</rss>
"""
    write(SITE / "podcasts" / "feed.xml", feed)


# ---------------------------------------------------------------- MAXGEN 2.0 helpers
def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def v2_schema(file: str) -> dict:
    return json.loads((V2_DIR / file).read_bytes())


def fix_repo_links(text: str, base_dir: str) -> str:
    """Relative links in the schema repo's Markdown point at files in that repo; send them to GitHub."""
    import posixpath

    def repl(m):
        target = m.group(1)
        return "](" + GITHUB_REPO + posixpath.normpath(posixpath.join(base_dir, target)) + ")"
    return re.sub(r"\]\((?!https?://|/|#|mailto:)([^)\s]+)\)", repl, text)


def readme_one_liners() -> dict:
    """The 'One document is' column of schemas/README-v2.md, word for word."""
    rows = {}
    for line in (V2_PIN / "README-v2.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*`([a-z-]+\.schema\.json)`\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*[^|]*\|\s*$", line)
        if m:
            rows[m.group(1)] = m.group(3)
    return rows


def tag_html(text: str) -> str:
    """README wording with `code` spans shown as code."""
    return re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", html.escape(text))


def changelog_v2_entry() -> str:
    text = V2_CHANGELOG.read_text(encoding="utf-8")
    a = text.index("\n## v2.0")
    b = text.index("\n## ", a + 5)
    return text[a + 1:b]


def changelog_part(title: str) -> str:
    """One schema's part of the v2.0 changelog entry, word for word (heading line dropped)."""
    m = re.search(rf"^### {re.escape(title)} \(.*?$\n(.*?)(?=^### |\Z)", changelog_v2_entry(), flags=re.M | re.S)
    return m.group(1).strip() if m else ""


def ref_name(ref: str) -> str:
    return ref.split("/")[-1]


def type_of_v2(prop) -> str:
    if isinstance(prop, bool):          # a boolean schema: true accepts anything
        return "any" if prop else "never allowed"
    if "$ref" in prop:
        return ref_name(prop["$ref"])
    t = prop.get("type")
    if isinstance(t, list):
        t = " | ".join(t)
    if t == "array":
        it = prop.get("items", {})
        if "$ref" in it:
            return f"array of {ref_name(it['$ref'])}"
        return f"array of {it.get('type') or ('object' if 'properties' in it else 'any')}"
    if "const" in prop:
        return f"const {json.dumps(prop['const'])}"
    if "enum" in prop:
        return "enum"
    if "oneOf" in prop or "anyOf" in prop:
        return "one of several shapes"
    return t or "object"


def prop_desc_v2(prop, schema: dict) -> str:
    if isinstance(prop, bool):
        return ""
    d = prop.get("description", "")
    if not d:
        ref = prop.get("$ref") or prop.get("items", {}).get("$ref", "")
        if ref.startswith("#/$defs/"):
            d = schema.get("$defs", {}).get(ref_name(ref), {}).get("description", "")
        elif ref.startswith(V2_DEFS_FILE):
            d = v2_schema(V2_DEFS_FILE).get("$defs", {}).get(ref_name(ref), {}).get("description", "")
    return first_sentence(d)


def fields_table_v2(schema: dict) -> str:
    req = set(schema.get("required", []))
    rows, refused = [], []
    for name, prop in schema.get("properties", {}).items():
        if prop is False:                # the schema names this field only to refuse it
            refused.append(name)
            continue
        badge = '<span class="req">required</span>' if name in req else ""
        rows.append(f"<tr><td><code>{html.escape(name)}</code> {badge}</td>"
                    f"<td class=\"type\">{html.escape(type_of_v2(prop))}</td>"
                    f"<td>{html.escape(prop_desc_v2(prop, schema))}</td></tr>")
    note = ""
    if refused:
        note = ('<p class="muted">Refused by name: ' + ", ".join(f"<code>{html.escape(n)}</code>" for n in refused)
                + ". A document that contains " + ("this field" if len(refused) == 1 else "any of these fields") + " is not valid.</p>")
    return ('<div class="table-wrap"><table class="fields"><thead><tr><th>Field</th><th>Type</th><th>Meaning</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></div>" + note)


def _schema_index(node, path: tuple, props: set, enums: dict, descs: dict) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("description"), str):
            descs[path] = node["description"]
        if isinstance(node.get("enum"), list):
            enums[path] = node["enum"]
        if isinstance(node.get("properties"), dict):
            for k in node["properties"]:
                props.add(path + ("properties", k))
        for k, v in node.items():
            if k != "description":
                _schema_index(v, path + (k,), props, enums, descs)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _schema_index(v, path + (str(i),), props, enums, descs)


def _nice(path: tuple) -> str:
    keep = [p for p in path if p not in ("properties", "$defs", "items", "allOf", "anyOf", "oneOf", "then", "else", "if")
            and not p.isdigit()]
    return ".".join(keep) or "(the document)"


def additions_21(file: str) -> list[tuple[str, str, str]]:
    """What 2.1 adds to one schema file, compared with the frozen 2.0 file: new fields, new allowed values and
    descriptions that changed, each in the file's own words. Returns (kind, path, text) rows."""
    prev = V2_PREV_DIR / file
    if not prev.exists():
        return []
    a, b = (set(), {}, {}), (set(), {}, {})
    _schema_index(json.loads(prev.read_bytes()), (), *a)
    _schema_index(v2_schema(file), (), *b)
    new_props = sorted(p for p in b[0] - a[0] if not any(p[:i] in (b[0] - a[0]) for i in range(1, len(p))))
    rows = [("field", _nice(p), b[2].get(p, "")) for p in new_props]
    for p, vals in b[1].items():
        if p in a[1]:
            added = [v for v in vals if v not in a[1][p]]
            if added:
                rows.append(("value", _nice(p), ", ".join(str(v) for v in added)))
    inside_new = lambda p: any(p[:i] in set(new_props) for i in range(1, len(p) + 1))
    for p, d in b[2].items():
        if p in a[2] and a[2][p] != d and not inside_new(p):
            rows.append(("wording", _nice(p), d))
    return rows


def additions_html(rows) -> str:
    items = []
    for kind, path, text in rows:
        if kind == "field":
            items.append(f"<li><code>{html.escape(path)}</code> (new field): {html.escape(text)}</li>")
        elif kind == "value":
            vals = ", ".join(f"<code>{html.escape(v.strip())}</code>" for v in text.split(","))
            items.append(f"<li><code>{html.escape(path)}</code>: new allowed value {vals}</li>")
        else:
            items.append(f"<li><code>{html.escape(path)}</code> (description updated): {html.escape(text)}</li>")
    return "<ul class=\"additions\">" + "".join(items) + "</ul>"


def additions_text(rows) -> list[str]:
    out = []
    for kind, path, text in rows:
        out.append({"field": f"- {path} (new field): {text}", "value": f"- {path}: new allowed value {text}",
                    "wording": f"- {path} (description updated): {text}"}[kind])
    return out


def v2_example(kind: str) -> Path:
    return next((V2_PIN / "examples" / kind).glob("*.json"))


# ---------------------------------------------------------------- versions
def build_versions() -> None:
    meta, intro = front_matter((CONTENT / "versions.md").read_text(encoding="utf-8"))
    rows = []
    for v in INDEX["versions"]:
        ver = v["version"]
        status = v.get("status") or ("current" if ver == CURRENT else "")
        if v.get("files"):   # 2.x entries list their files with fingerprints
            base = v.get("frozen_url", v["url"]).replace(BASE_URL, "")
            links = " · ".join(f'<a href="{base}{f["file"]}">{f["file"].replace(".schema.json", "")}</a>' for f in v["files"])
            count = f"{len(v['files']) - 1} + shared file"
        else:
            links = " · ".join(f'<a href="/schemas/maxgen/v{ver}/{s}.schema.json">{s}</a>' for s in v["schemas"])
            count = str(len(v["schemas"]))
        tag = {"current": ' <span class="badge">current</span>', "previous": ' <span class="badge archived">earlier 2.x</span>',
               "archived": ' <span class="badge archived">archived</span>'}.get(status, "")
        rows.append(f"<tr><td><strong>v{ver}</strong>{tag}</td><td>{count}</td><td class=\"links\">{links}</td></tr>")
    table = ('<div class="table-wrap"><table><thead><tr><th>Version</th><th>Schemas</th><th>Files (permanent links)</th></tr></thead>'
             f"<tbody>{''.join(rows)}</tbody></table></div>")
    body = md(intro).replace("<!--CURRENT-->", CURRENT).replace("<!--VERSIONS-->", table)
    write(SITE / "versions" / "index.html", layout("Versions", body, "/versions/", meta.get("description", ""), path="/versions/", ld=jsonld(ORG_JSONLD),
                                                   toc=toc_from_html(body)))


def build_changelog() -> None:
    """docs/SCHEMA_CHANGELOG.md from the schema repo, word for word (pinned copy)."""
    text = V2_CHANGELOG.read_text(encoding="utf-8")
    first, rest = text.split("\n", 1)
    h1 = first.lstrip("# ").strip()
    added = ""
    if CURRENT == "2.1" and "## v2.1" not in text:
        # The schema repo has no 2.1 entry yet: list what 2.1 adds, in the schema files' own words, until it does.
        parts = []
        for file in [V2_DEFS_FILE] + [f for _, _, f, _, _ in V2_KINDS]:
            rows = additions_21(file)
            if rows:
                parts.append(f"<h3>{html.escape(file)}</h3>" + additions_html(rows))
        added = (f'<h2 id="{V21_ANCHOR_ID}">v2.1 — 2026-11-21</h2>\n'
                 '<p>MAXGEN 2.1 adds optional fields and allowed values to six of the twelve files. Nothing was removed '
                 'or narrowed, so every file valid under 2.0 stays valid under 2.1. The written changelog entry for 2.1 '
                 'is still to come; until then, this list gives every addition in the schema files\' own words.</p>\n'
                 + "\n".join(parts) + "\n<hr>\n")
    body = (f'<p class="eyebrow">Releases · current v{CURRENT}</p>\n<h1 id="changelog">{html.escape(h1)}</h1>\n'
            + added + md(fix_repo_links(rest, "docs/")))
    toc = toc_from_html(re.sub(r"<h3 .*?</h3>", "", body, flags=re.S))   # versions only, not every sub-heading
    write(SITE / "changelog" / "index.html",
          layout("Changelog", body, "/versions/", "Every MAXGEN release, what changed in each schema and why, newest first.",
                 path="/changelog/", ld=jsonld(ORG_JSONLD), toc=toc))


# ---------------------------------------------------------------- schemas
def build_schemas_v113() -> list:
    """The seven 1.13 pages, kept at /schemas/v1.13/<slug>/ with an 'archived' banner."""
    pages = []
    for f in sorted((CONTENT / "schemas").glob("*.md")):
        meta, text = front_matter(f.read_text(encoding="utf-8"))
        name = meta["schema"]            # e.g. person
        slug = meta["slug"]              # e.g. maxperson
        schema = load_schema(name)
        ep = EPISODES[SCHEMA_EPISODE[name]]
        canonical = f"{BASE_URL}/schemas/maxgen/v1/{name}.schema.json"
        head = f"""<p class="banner-archived"><strong>MAXGEN 1.13, archived — see <a href="/schemas/{slug}/">MAXGEN 2.0</a></strong></p>
<div class="schema-head">
  <p class="eyebrow">MAXGEN schema · v1.13 · archived</p>
  <h1>{html.escape(meta['title'])} 1.13</h1>
  <p class="tagline">{html.escape(meta['tagline'])}</p>
  <p class="dl"><a class="btn" href="/schemas/maxgen/v1/{name}.schema.json">Download {html.escape(meta['title'])} v1.13</a>
     <a class="btn ghost" href="/versions/">All versions</a>
     <code class="canon">{canonical}</code></p>
</div>"""
        body_html = md(text.replace("<!--CURRENT-->", "1.13"))
        body_html = body_html.replace("<!--FIELDS-->", fields_table(schema))
        body_html = re.sub(r"<!--ENUM:([a-z_]+)-->", lambda m: enum_block(schema, m.group(1)), body_html)
        body_html = body_html.replace("<!--EXAMPLE-->", example_json(meta.get("fixture", "")))
        body_html = body_html.replace("<!--PODCAST-->", player(ep))
        page = head + body_html + f'<p class="backlink"><a href="/schemas/{slug}/">← {html.escape(meta["title"])} in MAXGEN 2.0</a> · <a href="/schemas/#archived-113">All archived 1.13 pages</a></p>'
        ld = jsonld(ORG_JSONLD, {
            "@type": "TechArticle", "headline": f"{meta['title']} — MAXGEN schema v1.13 (archived)",
            "description": meta.get("description", meta["tagline"]), "url": f"{BASE_URL}/schemas/v1.13/{slug}/",
            "author": {"@type": "Person", "name": "Garlon Maxwell"}, "publisher": {"@id": f"{BASE_URL}/#org"},
            "license": "https://creativecommons.org/publicdomain/zero/1.0/",
            "encoding": {"@type": "MediaObject", "contentUrl": canonical, "encodingFormat": "application/schema+json"},
        })
        write(SITE / "schemas" / "v1.13" / slug / "index.html",
              layout(f"{meta['title']} 1.13 (archived)", page, "/schemas/", meta.get("description", meta["tagline"]),
                     path=f"/schemas/v1.13/{slug}/", ld=ld, toc=toc_from_html(body_html)))
        pages.append((slug, meta["title"], meta["tagline"], name))
    return pages


def build_schemas_v2() -> list:
    """One page per 2.0 record schema: its description word for word, raw file and SHA-256, the fields,
    one made-up example as it is, and that schema's part of the v2.0 changelog entry."""
    one = readme_one_liners()
    pages = []
    for slug, title, file, kind, ep_no in V2_KINDS:
        schema = v2_schema(file)
        sha = sha256_file(V2_DIR / file)
        sid = schema["$id"]
        is_new = ep_no is None
        tagline = one.get(file, "")
        ex = v2_example(kind)
        changed = changelog_part(title)
        head = f"""<div class="schema-head">
  <p class="eyebrow">MAXGEN schema · v{CURRENT}{" · new in 2.0" if is_new else ""}</p>
  <h1>{html.escape(title)}</h1>
  <p class="tagline">One {html.escape(title)} is {tag_html(tagline)}.</p>
  <p class="dl"><a class="btn" href="/schemas/maxgen/v2/{file}">Download {html.escape(title)} ({CURRENT})</a>
     <a class="btn ghost" href="/versions/">All versions</a>
     <code class="canon">{sid}</code></p>
  <p class="sha">SHA-256 <code>{sha}</code></p>
</div>"""
        rows21 = additions_21(file)
        added_21 = ("<h2 id=\"added-in-21\">Added in 2.1</h2>\n"
                    f"<p>2.1 only adds; every 2.0 {html.escape(title)} stays valid. In the schema file's own words:</p>\n"
                    f"{additions_html(rows21)}\n"
                    f"<p><a href=\"{V21_ANCHOR}\">Everything 2.1 added</a></p>\n") if rows21 else ""
        listen = (player(EPISODES[ep_no]) if not is_new else
                  f'<p>There is no episode for {html.escape(title)} yet: it is new in MAXGEN 2.0. '
                  f'The <a href="/podcasts/#ep0">overview episode</a> explains the ideas it builds on.</p>')
        body_html = f"""<h2 id="definition">Definition</h2>
<blockquote class="schema-desc"><p>{html.escape(schema.get("description", ""))}</p></blockquote>
<p class="muted">The schema's own description, word for word.</p>
<h2 id="fields">Fields</h2>
<p>The top-level fields of a {html.escape(title)} document. Parts shared by every schema (the date block, the place block, the evidence link, the claim header and others) are defined once in the <a href="/schemas/#shared-building-blocks">shared building-blocks file</a>.</p>
{fields_table_v2(schema)}
<h2 id="example">A made-up example</h2>
<p>Every example on this site is invented: no real person, family or place. This one is <code>{html.escape(ex.name)}</code> from the standard's test files, copied as it is.</p>
<pre class="json"><code>{html.escape(ex.read_text(encoding="utf-8"))}</code></pre>
{added_21}<h2 id="what-changed">What changed from 1.13</h2>
{md(fix_repo_links(changed, "docs/"))}
<p><a href="{V2_ENTRY_ANCHOR}">The whole v2.0 changelog entry</a></p>
<h2 id="listen">Listen</h2>
{listen}"""
        back = '<p class="backlink"><a href="/schemas/">← All eleven schemas</a>'
        if not is_new:
            back += f' · <a href="/schemas/v1.13/{slug}/">The archived 1.13 page</a>'
        back += "</p>"
        page = head + body_html + back
        ld = jsonld(ORG_JSONLD, {
            "@type": "TechArticle", "headline": f"{title} — MAXGEN schema v{CURRENT}",
            "description": schema.get("description", ""), "url": f"{BASE_URL}/schemas/{slug}/",
            "author": {"@type": "Person", "name": "Garlon Maxwell"}, "publisher": {"@id": f"{BASE_URL}/#org"},
            "license": "https://creativecommons.org/publicdomain/zero/1.0/", "isPartOf": {"@id": f"{BASE_URL}/#standard"},
            "encoding": {"@type": "MediaObject", "contentUrl": sid, "encodingFormat": "application/schema+json"},
        })
        write(SITE / "schemas" / slug / "index.html",
              layout(title, page, "/schemas/", f"{title}, the MAXGEN 2.0 schema: one {title} is {tagline.replace('`', '')}.", path=f"/schemas/{slug}/", ld=ld,
                     toc=toc_from_html(body_html)))
        pages.append((slug, title, tagline, file))
    return pages


def build_schema_index(pages: list, archived: list) -> None:
    meta, text = front_matter((CONTENT / "schemas-index.md").read_text(encoding="utf-8"))
    cards = "".join(
        f'<a class="card" href="/schemas/{slug}/"><h3>{html.escape(t)}</h3><p>One {html.escape(t)} is {tag_html(tag)}.</p><span class="file">{f}</span></a>'
        for slug, t, tag, f in pages)
    files = [V2_DEFS_FILE] + [f for _, _, _, f in pages]
    titles = {V2_DEFS_FILE: "Shared building blocks"} | {f: t for _, t, _, f in pages}
    rows = "".join(f'<tr><td>{html.escape(titles[f])}</td><td><a href="/schemas/maxgen/v2/{f}"><code>/schemas/maxgen/v2/{f}</code></a></td>'
                   f'<td class="sha"><code>{sha256_file(V2_DIR / f)}</code></td></tr>' for f in files)
    addresses = ('<div class="table-wrap"><table><thead><tr><th>Schema</th><th>Address (its <code>$id</code>)</th><th>SHA-256</th></tr></thead>'
                 f"<tbody>{rows}</tbody></table></div>")
    arch = " · ".join(f'<a href="/schemas/v1.13/{slug}/">{html.escape(t)}</a>' for slug, t, _, _ in archived)
    body = (md(text).replace("<!--CARDS-->", f'<div class="cards">{cards}</div>').replace("<!--CURRENT-->", CURRENT)
            .replace("<!--ADDRESSES-->", addresses).replace("<!--ARCHIVED-->", arch))
    standard_ld = {
        "@type": "Dataset", "@id": f"{BASE_URL}/#standard", "name": f"MAXGEN — The Maxwell Genealogy Standard v{CURRENT}",
        "description": "Eleven JSON Schema record files plus one shared building-blocks file (MaxRecord, MaxPerson, MaxRelationship, MaxTask, MaxSource, MaxPlace, MaxGroup, MaxEvent, MaxName, MaxRecognition, MaxDNA) defining an open, probabilistic genealogy data standard.",
        "url": f"{BASE_URL}/schemas/", "version": CURRENT, "license": "https://creativecommons.org/publicdomain/zero/1.0/",
        "creator": {"@type": "Person", "name": "Garlon Maxwell"}, "publisher": {"@id": f"{BASE_URL}/#org"}, "isAccessibleForFree": True,
        "distribution": [{"@type": "DataDownload", "name": titles[f], "contentUrl": f"{BASE_URL}/schemas/maxgen/v2/{f}",
                          "encodingFormat": "application/schema+json"} for f in files],
    }
    write(SITE / "schemas" / "index.html", layout(meta.get("title", "The schemas"), body, "/schemas/", meta.get("description", ""),
                                                 path="/schemas/", ld=jsonld(ORG_JSONLD, standard_ld)))


def faq_ld(body_html: str) -> dict:
    qa = re.findall(r"<summary>(.*?)</summary>(.*?)</details>", body_html, flags=re.S)
    items = []
    for q, a in qa:
        text = re.sub(r"<[^>]+>", "", a).strip()
        items.append({"@type": "Question", "name": html.unescape(q), "acceptedAnswer": {"@type": "Answer", "text": html.unescape(text)}})
    return {"@type": "FAQPage", "mainEntity": items}


# ---------------------------------------------------------------- plain pages
def build_pages() -> None:
    for f in sorted(CONTENT.glob("*.md")):
        if f.stem in {"podcasts", "versions", "schemas-index", "changelog-v1", "index"}:   # index.md is the home page (build_home)
            continue
        meta, text = front_matter(f.read_text(encoding="utf-8"))
        text = expand_rules(text)
        out = meta.get("out", f"{f.stem}/index.html")
        path = "/" + out.replace("index.html", "")
        body, toc = md_toc(text)
        body = body.replace("<!--CURRENT-->", CURRENT).replace("<!--KIT-->", kit_table())
        if meta.get("toc") != "true":
            toc = ""
        ld_objs = [ORG_JSONLD]
        if f.stem == "faq":
            ld_objs.append(faq_ld(body))
        write(SITE / out, layout(meta.get("title", f.stem.title()), body, meta.get("nav", path), meta.get("description", ""),
                                 path=path, ld=jsonld(*ld_objs), toc=toc))


def expand_rules(text: str) -> str:
    """<!--RULES:Section title--> becomes that section of the served /agents/RULES.md, word for word, so the
    standard page always quotes exactly the rules the kit ships (BuildGenealogy's handoff, section 6)."""
    rules_path = SITE / "agents" / "RULES.md"
    if "<!--RULES:" not in text or not rules_path.exists():
        return text
    rules = rules_path.read_text(encoding="utf-8")

    def section(m):
        title = m.group(1).strip()
        found = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", rules, flags=re.M | re.S)
        if not found:
            raise SystemExit(f"RULES.md has no section {title!r}")
        return found.group(1).strip("\n")
    return re.sub(r"<!--RULES:([^>]+?)-->", section, text)


def kit_table() -> str:
    """Files of the agent kit with the SHA-256 and __version__ computed by tools/sync_v2.py from the served files."""
    mf = SITE / "agents" / "manifest.json"
    if not mf.exists():
        return ""
    m = json.loads(mf.read_text(encoding="utf-8"))
    rows = "".join(f'<tr><td><a href="{s["path"]}"><code>{s["path"]}</code></a></td><td>{html.escape(s["version"])}</td>'
                   f'<td class="sha"><code>{s["sha256"]}</code></td></tr>' for s in m["scripts"])
    rows += "".join(f'<tr><td><a href="{d["path"]}"><code>{d["path"]}</code></a></td><td></td>'
                    f'<td class="sha"><code>{d["sha256"]}</code></td></tr>' for d in m["documents"])
    return ('<div class="table-wrap"><table><thead><tr><th>File</th><th><code>__version__</code></th><th>SHA-256</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div>")


def build_home(pages) -> None:
    meta, text = front_matter((CONTENT / "index.md").read_text(encoding="utf-8"))
    cards = "".join(
        f'<a class="card" href="/schemas/{slug}/"><h3>{html.escape(t)}</h3><p>One {html.escape(t)} is {tag_html(tag)}.</p></a>'
        for slug, t, tag, n in pages
    )
    body = md(text).replace("<!--CARDS-->", f'<div class="cards">{cards}</div>').replace("<!--CURRENT-->", CURRENT)
    site_ld = {"@type": "WebSite", "@id": f"{BASE_URL}/#website", "url": BASE_URL + "/", "name": "MAXGEN · OpenGenealogyAI",
               "publisher": {"@id": f"{BASE_URL}/#org"}, "inLanguage": "en"}
    write(SITE / "index.html", layout("MAXGEN — the open genealogy standard", body, "/", meta.get("description", ""),
                                      path="/", ld=jsonld(ORG_JSONLD, site_ld)))


def agents_llms_sections() -> dict:
    """The sections of the agent kit's llms.txt (docs/agents/llms.txt, pinned), keyed by heading, word for word."""
    text = (V2_PIN / "agents-llms.txt").read_text(encoding="utf-8")
    parts = re.split(r"^## ", text, flags=re.M)
    out = {"_head": parts[0]}
    for p in parts[1:]:
        heading, _, rest = p.partition("\n")
        out[heading.strip()] = "## " + heading.strip() + "\n" + rest.rstrip("\n")
    return out


def build_llms_txt(pages) -> None:
    """MERGE (BuildGenealogy handoff): the kit's 'For AI agents — start here' section at the top, the 2.0 schema
    line in place of the old schema lines, the kit's file list and tools; every section of the old file kept."""
    kit = agents_llms_sections()
    schema_line = next(l for l in kit["The standard"].splitlines() if l.startswith("- [Schemas]"))
    starter = next((l for l in kit["The standard"].splitlines() if "STARTER_PROMPT" in l), "")
    lines = [
        "# MAXGEN — The Maxwell Genealogy Standard",
        "",
        f"> MAXGEN is an open, public-domain (CC0) data standard for genealogy, stewarded by OpenGenealogyAI. Current release v{CURRENT}: "
        "eleven JSON Schema record files plus one shared building-blocks file. Every fact traces to a source document through an evidence link, "
        "every claim carries a confidence score, every family link is stored once, and nothing is overwritten; records about living people are private by rule. "
        "If you are an AI agent that a person has sent here to build their family tree, start at /agents/START.md and follow it exactly.",
        "",
        "MAXGEN is designed to work alongside existing genealogy sites, archives and the GEDCOM format, not to replace them.",
        "",
        kit["For AI agents — start here"],
        "",
        kit["The kit, file by file"],
        "",
        kit["Tools (download into the project folder)"],
        "",
        "## Read",
        f"- [The standard in plain English]({BASE_URL}/standard/)",
        f"- [FAQ]({BASE_URL}/faq/)",
        f"- [Versioning policy]({BASE_URL}/versions/) and [changelog]({BASE_URL}/changelog/)",
        f"- [Governance]({BASE_URL}/governance/)",
        f"- [Licence (CC0)]({BASE_URL}/license/)",
    ]
    if starter:
        lines.append(starter)
    lines += ["", "## Schemas (canonical JSON Schema files)", schema_line]
    for slug, t, tag, f in pages:
        lines.append(f"- [{t}]({BASE_URL}/schemas/{slug}/): one {t} is {tag} — file: {BASE_URL}/schemas/maxgen/v2/{f}")
    lines += [f"- Shared building blocks (not a record type) — file: {BASE_URL}/schemas/maxgen/v2/{V2_DEFS_FILE}",
              f"- What 2.1 added: {BASE_URL}{V21_ANCHOR}",
              f"- SHA-256 of every file: {BASE_URL}/schemas/maxgen/v2/SHA256SUMS.txt",
              f"- Machine-readable version index: {BASE_URL}/schemas/maxgen/index.json", "",
              "## Listen", f"- [Podcast episodes]({BASE_URL}/podcasts/) — RSS: {BASE_URL}/podcasts/feed.xml "
              "(the episodes describe MAXGEN v1.11; the schema pages and the changelog are authoritative)", "",
              "## Contribute", f"- [Ask a question or propose a change]({BASE_URL}/contribute/)", "- GitHub: https://github.com/OpenGenealogyai", ""]
    for extra in ("What it needs", "Licence"):
        if extra in kit:
            lines += [kit[extra], ""]
    write(SITE / "llms.txt", "\n".join(lines))


HIDDEN = ("/agents/", "/index/")   # not in the sitemap: the hidden beta kit page, and the old duplicate of the home page

REDIRECT_HOME = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>MAXGEN — OpenGenealogyAI</title>
<link rel="canonical" href="{BASE_URL}/"><meta name="robots" content="noindex">
<meta http-equiv="refresh" content="0; url=/"></head>
<body><p>This page moved to <a href="/">the home page</a>.</p></body></html>
"""


def main() -> None:
    pages = build_schemas_v2()
    archived = build_schemas_v113()
    build_schema_index(pages, archived)
    build_pages()
    build_home(pages)
    build_podcasts()
    build_versions()
    build_changelog()
    build_llms_txt(pages)
    write(SITE / "index" / "index.html", REDIRECT_HOME)   # /index/ was a duplicate of the home page until 2026-11-16
    # sitemap
    urls = sorted({"/" + str(p.relative_to(SITE)).replace("\\", "/").replace("index.html", "")
                   for p in SITE.rglob("index.html") if ".git" not in p.parts})
    urls = [u for u in urls if not u.startswith(HIDDEN)]
    sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
         "".join(f"  <url><loc>{BASE_URL}{u}</loc></url>\n" for u in urls) + "</urlset>\n"
    write(SITE / "sitemap.xml", sm)
    robots = [
        "# opengenealogyai.org — everything here is public and meant to be read by people, search engines and AI.",
        "# The MAXGEN standard is CC0. Crawl freely; a machine-readable summary is at /llms.txt and the full text at /llms-full.txt.",
        "User-agent: *", "Allow: /", "",
    ]
    for bot in ["Googlebot", "Bingbot", "GPTBot", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "Claude-Web", "anthropic-ai",
                "Google-Extended", "PerplexityBot", "CCBot", "Applebot", "Applebot-Extended", "DuckDuckBot", "Amazonbot", "Bytespider", "meta-externalagent"]:
        robots += [f"User-agent: {bot}", "Allow: /", ""]
    robots += [f"Sitemap: {BASE_URL}/sitemap.xml", ""]
    write(SITE / "robots.txt", "\n".join(robots))
    build_llms_full(pages)
    print(f"built {len(urls)} pages (MAXGEN v{CURRENT}); hidden: {', '.join(HIDDEN)}")


def plain(text: str) -> str:
    text = text.replace("<!--CURRENT-->", CURRENT)
    text = re.sub(r"<!--[A-Z:_a-z]+-->", "", text)
    text = re.sub(r"<script.*?</script>", "", text, flags=re.S)
    return re.sub(r"<[^>]+>", "", text).strip()


def build_llms_full(pages) -> None:
    """One plain-text file with the whole site's prose and every 2.0 schema's definition, fields, example and
    podcast, for AI systems that prefer a single fetch."""
    order = ["index.md", "standard.md", "schemas-index.md", "faq.md", "versions.md", "governance.md", "contribute.md", "license.md", "podcasts.md"]
    parts = [f"# MAXGEN — The Maxwell Genealogy Standard (v{CURRENT}) — full text of opengenealogyai.org", ""]
    for n in order:
        f = CONTENT / n
        if f.exists():
            meta, text = front_matter(f.read_text(encoding="utf-8"))
            parts += [f"\n\n---\n## {meta.get('title', f.stem)}\n", plain(expand_rules(text))]
    one = readme_one_liners()
    for slug, title, file, kind, ep_no in V2_KINDS:
        schema = v2_schema(file)
        req = set(schema.get("required", []))
        parts += [f"\n\n---\n## {title} (MAXGEN {CURRENT} schema)\n",
                  f"Page: {BASE_URL}/schemas/{slug}/",
                  f"File: {schema['$id']}  SHA-256 {sha256_file(V2_DIR / file)}",
                  f"One {title} is {one.get(file, '')}.", "",
                  f"Definition: {schema.get('description', '')}", "", "### Fields", ""]
        for name, prop in schema.get("properties", {}).items():
            if prop is False:
                parts.append(f"- {name}: refused by name (a document that contains it is not valid)")
                continue
            parts.append(f"- {name} ({type_of_v2(prop)}{', required' if name in req else ''}): {prop_desc_v2(prop, schema)}")
        ex = v2_example(kind)
        parts += ["", f"### Example (made up; {ex.name})", "", "```json", ex.read_text(encoding="utf-8").rstrip(), "```", "",
                  "### What changed from 1.13", "", changelog_part(title), ""]
        rows21 = additions_21(file)
        if rows21:
            parts += ["### Added in 2.1", ""] + additions_text(rows21) + [""]
        parts += ["### Listen", ""]
        if ep_no is None:
            parts.append(f"No episode yet: {title} is new in MAXGEN 2.0.")
        else:
            n, stem, t, blurb, secs, size = EPISODES[ep_no]
            parts.append(f"Episode {n}, {t} ({mmss(secs)}): {MEDIA_BASE}/{stem}.mp3 — {blurb} "
                         f"It describes MAXGEN v1.11; see {BASE_URL}{V2_ENTRY_ANCHOR} for what changed since.")
    parts += ["\n\n---\n## Schema files (JSON Schema 2020-12)\n",
              f"- {BASE_URL}/schemas/maxgen/v2/{V2_DEFS_FILE} (shared building blocks)"]
    parts += [f"- {BASE_URL}/schemas/maxgen/v2/{f}" for _, _, f, _, _ in V2_KINDS]
    parts += [f"- SHA-256: {BASE_URL}/schemas/maxgen/v2/SHA256SUMS.txt",
              f"- The archived MAXGEN 1.13 files stay at {BASE_URL}/schemas/maxgen/v1/"]
    write(SITE / "llms-full.txt", "\n".join(parts) + "\n")


if __name__ == "__main__":
    main()
