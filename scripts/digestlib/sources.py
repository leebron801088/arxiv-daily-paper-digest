from __future__ import annotations

import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape

from .identity import normalize_paper

NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom",
      "o": "http://a9.com/-/spec/opensearch/1.1/"}


class HTTP:
    def __init__(self, timeout=25, retries=2, arxiv_delay=3.1):
        self.timeout, self.retries, self.arxiv_delay = timeout, retries, arxiv_delay
        self.last_arxiv = 0

    def request(self, url, max_bytes=25_000_000):
        host = urllib.parse.urlparse(url).hostname or ""
        if not url.startswith("https://") and host not in {"localhost", "127.0.0.1"}:
            raise ValueError("Remote endpoints must use HTTPS")
        for attempt in range(self.retries + 1):
            if host in {"arxiv.org", "export.arxiv.org", "rss.arxiv.org"}:
                time.sleep(max(0, self.arxiv_delay - (time.monotonic() - self.last_arxiv)))
                self.last_arxiv = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": "arxiv-daily-paper-digest/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ValueError("Response exceeds configured size limit")
                return body
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                code = getattr(exc, "code", None)
                if attempt == self.retries or (code and code not in {429, 500, 502, 503, 504}):
                    # Do not expose request headers or server response bodies containing secrets.
                    reason = getattr(exc, "reason", exc)
                    if isinstance(reason, (TimeoutError, socket.timeout)):
                        detail = f"request timed out ({self.timeout}s)"
                    else:
                        detail = f"HTTP {code}" if code else "connection failure"
                    raise RuntimeError(f"{host}: {detail}") from None
                time.sleep(min(2 ** attempt, 8))


def parse_atom(body):
    root = ET.fromstring(body)
    if root.tag != f"{{{NS['a']}}}feed":
        raise ValueError("Expected an arXiv Atom feed")
    entries = []
    for entry in root.findall("a:entry", NS):
        def text(field):
            return entry.findtext(field, default="", namespaces=NS).strip()
        identifier = text("a:id")
        if "api/errors" in identifier:
            raise ValueError("arXiv returned an API error entry")
        entries.append(normalize_paper({
            "entry_id": identifier, "title": text("a:title"), "abstract": text("a:summary"),
            "published": text("a:published"), "updated": text("a:updated"),
            "authors": [a.findtext("a:name", default="", namespaces=NS) for a in entry.findall("a:author", NS)],
            "categories": [c.get("term") for c in entry.findall("a:category", NS)],
            "comment": text("x:comment"), "journal_ref": text("x:journal_ref"), "doi": text("x:doi"),
        }))
    return entries, int(root.findtext("o:totalResults", default=str(len(entries)), namespaces=NS))


class ArxivSource:
    endpoint = "https://export.arxiv.org/api/query"

    def __init__(self, http):
        self.http = http

    def query(self, **params):
        return parse_atom(self.http.request(self.endpoint + "?" + urllib.parse.urlencode(params)))

def parse_version_history(html):
    plain = unescape(re.sub(r"<[^>]+>", " ", html))
    plain = " ".join(plain.split())
    result = []
    for version, date in re.findall(r"\[(v\d+)\]\s*([A-Za-z]{3},\s*\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC)", plain):
        parsed = datetime.strptime(date, "%a, %d %b %Y %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        result.append((version, parsed.isoformat()))
    return result
