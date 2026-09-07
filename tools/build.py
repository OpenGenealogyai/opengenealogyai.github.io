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
FIXTURES = Path(r"C:\Users\stock\dev\opengenealogyai\test\fixtures")
CHANGELOG = Path(r"C:\Users\stock\dev\opengenealogyai\docs\SCHEMA_CHANGELOG.md")
AUDIO_SRC = Path(r"E:\BuildGenealogy\podcast-scripts\audio")

BASE_URL = "https://opengenealogyai.org"
MEDIA_BASE = "https://media.opengenealogyai.org/podcasts"   # SiteGround subdomain for MP3s

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
                       extension_configs={"toc": {"permalink": False}})


def md(text: str) -> str:
    MD.reset()
    return MD.convert(text)


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
    "logo": f"{BASE_URL}/assets/img/ogai-logo.png",
    "sameAs": ["https://github.com/OpenGenealogyai"],
    "description": "Steward of MAXGEN, the open, public-domain genealogy data standard.",
}


def jsonld(*objs) -> str:
    graph = {"@context": "https://schema.org", "@graph": list(objs)}
    return '<script type="application/ld+json">' + json.dumps(graph, ensure_ascii=False) + "</script>"


def layout(title: str, body: str, active: str = "", description: str = "", extra_head: str = "",
           path: str = "/", ld: str = "") -> str:
    nav = "".join(
        f'<a href="{href}"{" class=\"active\"" if href == active else ""}>{label}</a>'
        for label, href in NAV
    )
    desc = html.escape(description or "MAXGEN — the open, probabilistic genealogy data standard. Seven JSON schemas, CC0.")
    full_title = html.escape(f"{title} — MAXGEN · OpenGenealogyAI")
    url = BASE_URL + path
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
<meta property="og:image" content="{BASE_URL}/assets/img/ogai-logo.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
<link rel="icon" href="/assets/img/logo-transparent.svg" type="image/svg+xml">
<link rel="stylesheet" href="/assets/css/site.css">
<link rel="alternate" type="application/rss+xml" title="MAXGEN Podcast" href="/podcasts/feed.xml">
{ld}
{extra_head}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="/"><img src="/assets/img/logo-transparent.svg" alt="" width="40" height="40"><span>MAXGEN<small>by OpenGenealogyAI</small></span></a>
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
    path.write_text(text, encoding="utf-8")


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


def player(ep) -> str:
    n, stem, title, blurb, secs, size = ep
    guide = f"/podcasts/study-guides/{stem}-StudyGuide/"
    return f"""<div class="episode" id="ep{n}">
  <div class="ep-head"><span class="ep-num">Episode {n}</span><h3>{html.escape(title)}</h3><span class="ep-len">{mmss(secs)}</span></div>
  <p>{html.escape(blurb)}</p>
  <audio controls preload="none" src="{MEDIA_BASE}/{stem}.mp3"></audio>
  <p class="ep-links"><a href="{MEDIA_BASE}/{stem}.mp3">Download MP3</a> · <a href="{guide}">Study guide</a></p>
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

    # study guides
    for f in sorted(AUDIO_SRC.glob("*-StudyGuide.md")):
        text = f.read_text(encoding="utf-8")
        text = re.sub(r"\bCC-BY\b", "CC0", text)  # guides were written before the licence wording was settled
        name = f.stem
        body = md(text) + '<p><a href="/podcasts/">← All episodes</a></p>'
        write(SITE / "podcasts" / "study-guides" / name / "index.html",
              layout(name.replace("-", " "), body, "/podcasts/", f"Study guide for the MAXGEN podcast episode {name}.",
                     path=f"/podcasts/study-guides/{name}/"))

    # RSS feed
    now = format_datetime(datetime.now(timezone.utc))
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


# ---------------------------------------------------------------- versions
def build_versions() -> None:
    meta, intro = front_matter((CONTENT / "versions.md").read_text(encoding="utf-8"))
    rows = []
    for v in INDEX["versions"]:
        ver = v["version"]
        links = " · ".join(f'<a href="/schemas/maxgen/v{ver}/{s}.schema.json">{s}</a>' for s in v["schemas"])
        tag = ' <span class="badge">current</span>' if ver == CURRENT else ""
        rows.append(f"<tr><td><strong>v{ver}</strong>{tag}</td><td>{len(v['schemas'])}</td><td class=\"links\">{links}</td></tr>")
    table = ('<div class="table-wrap"><table><thead><tr><th>Version</th><th>Schemas</th><th>Files (permanent links)</th></tr></thead>'
             f"<tbody>{''.join(rows)}</tbody></table></div>")
    changelog = CHANGELOG.read_text(encoding="utf-8")
    changelog = changelog.split("\n", 1)[1]  # drop the H1
    body = md(intro).replace("<!--CURRENT-->", CURRENT).replace("<!--VERSIONS-->", table) + "<h2 id=\"changelog\">Changelog</h2>" + md(changelog)
    write(SITE / "versions" / "index.html", layout("Versions", body, "/versions/", meta.get("description", ""), path="/versions/", ld=jsonld(ORG_JSONLD)))


# ---------------------------------------------------------------- schemas
def build_schemas() -> None:
    pages = []
    for f in sorted((CONTENT / "schemas").glob("*.md")):
        meta, text = front_matter(f.read_text(encoding="utf-8"))
        name = meta["schema"]            # e.g. person
        slug = meta["slug"]              # e.g. maxperson
        schema = load_schema(name)
        ep = EPISODES[SCHEMA_EPISODE[name]]
        canonical = f"{BASE_URL}/schemas/maxgen/v1/{name}.schema.json"
        head = f"""<div class="schema-head">
  <p class="eyebrow">MAXGEN schema · v{CURRENT}</p>
  <h1>{html.escape(meta['title'])}</h1>
  <p class="tagline">{html.escape(meta['tagline'])}</p>
  <p class="dl"><a class="btn" href="/schemas/maxgen/v1/{name}.schema.json">Download {html.escape(meta['title'])} v{CURRENT}</a>
     <a class="btn ghost" href="/versions/">All versions</a>
     <code class="canon">{canonical}</code></p>
</div>"""
        body_html = md(text)
        body_html = body_html.replace("<!--FIELDS-->", fields_table(schema))
        body_html = re.sub(r"<!--ENUM:([a-z_]+)-->", lambda m: enum_block(schema, m.group(1)), body_html)
        body_html = body_html.replace("<!--EXAMPLE-->", example_json(meta.get("fixture", "")))
        body_html = body_html.replace("<!--PODCAST-->", player(ep))
        page = head + body_html + '<p class="backlink"><a href="/schemas/">← All seven schemas</a></p>'
        ld = jsonld(ORG_JSONLD, {
            "@type": "TechArticle", "headline": f"{meta['title']} — MAXGEN schema v{CURRENT}",
            "description": meta.get("description", meta["tagline"]), "url": f"{BASE_URL}/schemas/{slug}/",
            "author": {"@type": "Person", "name": "Garlon Maxwell"}, "publisher": {"@id": f"{BASE_URL}/#org"},
            "license": "https://creativecommons.org/publicdomain/zero/1.0/", "isPartOf": {"@id": f"{BASE_URL}/#standard"},
            "encoding": {"@type": "MediaObject", "contentUrl": canonical, "encodingFormat": "application/schema+json"},
        })
        write(SITE / "schemas" / slug / "index.html",
              layout(meta["title"], page, "/schemas/", meta.get("description", meta["tagline"]), path=f"/schemas/{slug}/", ld=ld))
        pages.append((slug, meta["title"], meta["tagline"], name))

    # index of schemas
    meta, text = front_matter((CONTENT / "schemas-index.md").read_text(encoding="utf-8"))
    cards = "".join(
        f'<a class="card" href="/schemas/{slug}/"><h3>{html.escape(t)}</h3><p>{html.escape(tag)}</p><span class="file">{n}.schema.json</span></a>'
        for slug, t, tag, n in pages
    )
    body = md(text).replace("<!--CARDS-->", f'<div class="cards">{cards}</div>').replace("<!--CURRENT-->", CURRENT)
    standard_ld = {
        "@type": "Dataset", "@id": f"{BASE_URL}/#standard", "name": f"MAXGEN — The Maxwell Genealogy Standard v{CURRENT}",
        "description": "Seven JSON Schema files (MaxRecord, MaxPerson, MaxTask, MaxDNA, MaxSource, MaxRecognition, MaxName) defining an open, probabilistic genealogy data standard.",
        "url": f"{BASE_URL}/schemas/", "version": CURRENT, "license": "https://creativecommons.org/publicdomain/zero/1.0/",
        "creator": {"@type": "Person", "name": "Garlon Maxwell"}, "publisher": {"@id": f"{BASE_URL}/#org"}, "isAccessibleForFree": True,
        "distribution": [{"@type": "DataDownload", "name": f"{t} schema", "contentUrl": f"{BASE_URL}/schemas/maxgen/v1/{n}.schema.json", "encodingFormat": "application/schema+json"} for _, t, _, n in pages],
    }
    write(SITE / "schemas" / "index.html", layout("The seven schemas", body, "/schemas/", meta.get("description", ""), path="/schemas/", ld=jsonld(ORG_JSONLD, standard_ld)))
    return pages


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
        if f.stem in {"podcasts", "versions", "schemas-index"}:
            continue
        meta, text = front_matter(f.read_text(encoding="utf-8"))
        out = meta.get("out", f"{f.stem}/index.html")
        path = "/" + out.replace("index.html", "")
        body = md(text)
        body = body.replace("<!--CURRENT-->", CURRENT)
        ld_objs = [ORG_JSONLD]
        if f.stem == "faq":
            ld_objs.append(faq_ld(body))
        write(SITE / out, layout(meta.get("title", f.stem.title()), body, meta.get("nav", path), meta.get("description", ""),
                                 path=path, ld=jsonld(*ld_objs)))


def build_home(pages) -> None:
    meta, text = front_matter((CONTENT / "index.md").read_text(encoding="utf-8"))
    cards = "".join(
        f'<a class="card" href="/schemas/{slug}/"><h3>{html.escape(t)}</h3><p>{html.escape(tag)}</p></a>'
        for slug, t, tag, n in pages
    )
    body = md(text).replace("<!--CARDS-->", f'<div class="cards">{cards}</div>').replace("<!--CURRENT-->", CURRENT)
    site_ld = {"@type": "WebSite", "@id": f"{BASE_URL}/#website", "url": BASE_URL + "/", "name": "MAXGEN · OpenGenealogyAI",
               "publisher": {"@id": f"{BASE_URL}/#org"}, "inLanguage": "en"}
    write(SITE / "index.html", layout("MAXGEN — the open genealogy standard", body, "/", meta.get("description", ""),
                                      path="/", ld=jsonld(ORG_JSONLD, site_ld)))


def build_llms_txt(pages) -> None:
    lines = [
        "# MAXGEN — The Maxwell Genealogy Standard",
        "",
        f"> MAXGEN is an open, public-domain (CC0) data standard for genealogy, stewarded by OpenGenealogyAI. Current release v{CURRENT}. "
        "Seven JSON Schema files describe evidence (MaxRecord), probable identities (MaxPerson), research work (MaxTask), DNA evidence (MaxDNA), "
        "where records live (MaxSource), transcription provenance (MaxRecognition) and name variants (MaxName). Every claim carries a source and a 0-1 confidence score; "
        "conflicting evidence is kept, nothing is deleted, and records about living people are private by rule.",
        "",
        "MAXGEN is designed to work alongside existing genealogy sites, archives and the GEDCOM format, not to replace them.",
        "",
        "## Read",
        f"- [The standard in plain English]({BASE_URL}/standard/)",
        f"- [FAQ]({BASE_URL}/faq/)",
        f"- [Versioning policy and changelog]({BASE_URL}/versions/)",
        f"- [Governance]({BASE_URL}/governance/)",
        f"- [Licence (CC0)]({BASE_URL}/license/)",
        "",
        "## Schemas (canonical JSON Schema files)",
    ]
    for slug, t, tag, n in pages:
        lines.append(f"- [{t}]({BASE_URL}/schemas/{slug}/): {tag} — file: {BASE_URL}/schemas/maxgen/v1/{n}.schema.json")
    lines += ["", f"- Machine-readable version index: {BASE_URL}/schemas/maxgen/index.json", "",
              "## Listen", f"- [Podcast episodes and study guides]({BASE_URL}/podcasts/) — RSS: {BASE_URL}/podcasts/feed.xml", "",
              "## Contribute", f"- [Ask a question or propose a change]({BASE_URL}/contribute/)", "- GitHub: https://github.com/OpenGenealogyai", ""]
    write(SITE / "llms.txt", "\n".join(lines))


def main() -> None:
    pages = build_schemas()
    build_pages()
    build_home(pages)
    build_podcasts()
    build_versions()
    build_llms_txt(pages)
    # sitemap
    urls = sorted({"/" + str(p.relative_to(SITE)).replace("\\", "/").replace("index.html", "")
                   for p in SITE.rglob("index.html") if ".git" not in p.parts})
    sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
         "".join(f"  <url><loc>{BASE_URL}{u}</loc></url>\n" for u in urls) + "</urlset>\n"
    write(SITE / "sitemap.xml", sm)
    write(SITE / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")
    print(f"built {len(urls)} pages (MAXGEN v{CURRENT})")


if __name__ == "__main__":
    main()
