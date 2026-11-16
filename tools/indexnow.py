"""Tell Bing (and the other IndexNow search engines) that the site's pages changed.

Run after a deploy is live:  python tools/indexnow.py            (every URL in the live sitemap)
                             python tools/indexnow.py URL [URL]  (just these)

The key file /<KEY>.txt at the site root proves we own the domain. It is public by design.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

HOST = "opengenealogyai.org"
KEY = "fb3aa21780821a44faaf0b3a99cf7a26"
ENDPOINTS = ["https://www.bing.com/indexnow", "https://api.indexnow.org/indexnow"]


def sitemap_urls() -> list[str]:
    sm = urllib.request.urlopen(f"https://{HOST}/sitemap.xml", timeout=20).read().decode("utf-8")
    return re.findall(r"<loc>([^<]+)</loc>", sm) + [f"https://{HOST}/llms.txt", f"https://{HOST}/llms-full.txt"]


def main() -> int:
    urls = sys.argv[1:] or sitemap_urls()
    body = json.dumps({"host": HOST, "key": KEY, "keyLocation": f"https://{HOST}/{KEY}.txt", "urlList": urls}).encode()
    ok = True
    for ep in ENDPOINTS:
        req = urllib.request.Request(ep, data=body, headers={"Content-Type": "application/json; charset=utf-8",
                                                             "User-Agent": "opengenealogyai-site-indexnow/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                print(f"{ep}: HTTP {r.status} ({len(urls)} URLs)")
        except urllib.error.HTTPError as e:
            ok = False
            print(f"{ep}: HTTP {e.code} {e.read()[:200]!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
