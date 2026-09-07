"""Check every internal link and asset reference in the built site resolves to a file.
Run: python tools/check_links.py   (exit code 1 if anything is broken)"""
from __future__ import annotations
import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
HREF = re.compile(r'(?:href|src)="([^"#?]+)')

ok = bad = 0
problems: list[str] = []
for page in SITE.rglob("*.html"):
    if ".git" in page.parts:
        continue
    text = page.read_text(encoding="utf-8")
    for link in HREF.findall(text):
        if link.startswith(("http://", "https://", "mailto:", "data:")):
            continue
        target = (SITE / link.lstrip("/")) if link.startswith("/") else (page.parent / link)
        if target.is_dir():
            target = target / "index.html"
        if target.exists():
            ok += 1
        else:
            bad += 1
            problems.append(f"{page.relative_to(SITE)} -> {link}")

print(f"internal links ok: {ok}, broken: {bad}")
for p in problems:
    print("  BROKEN:", p)
sys.exit(1 if bad else 0)
