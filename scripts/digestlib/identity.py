from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone


def stable_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_title(title):
    return " ".join(re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKC", title).casefold()).split())


def parse_arxiv_id(value):
    value = re.sub(r"^(?:arxiv:|https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/)", "", str(value).strip(), flags=re.I)
    value = value.split("?")[0].split("#")[0].removesuffix(".pdf")
    match = re.fullmatch(r"((?:\d{4}\.\d{4,5})|(?:[a-zA-Z.-]+/\d{7}))(v\d+)?", value)
    return (match[1], match[2] or "unknown") if match else ("", "unknown")


def normalize_doi(value):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value or "", flags=re.I).strip().lower()


def canonical_key(paper):
    base, _ = parse_arxiv_id(paper.get("arxiv_base_id") or paper.get("arxiv_id") or paper.get("entry_id", ""))
    if base:
        return f"arxiv:{base}"
    doi = normalize_doi(paper.get("doi", ""))
    if doi:
        return f"doi:{doi}"
    if paper.get("semantic_scholar_id"):
        return f"s2:{paper['semantic_scholar_id']}"
    return "title:" + stable_hash(normalize_title(paper.get("title", "")))


def identity_aliases(paper):
    aliases = {canonical_key(paper)}
    if paper.get("doi"):
        aliases.add("doi:" + normalize_doi(paper["doi"]))
    if paper.get("semantic_scholar_id"):
        aliases.add("s2:" + paper["semantic_scholar_id"])
    if paper.get("title"):
        aliases.add("title:" + stable_hash(normalize_title(paper["title"])))
    return aliases


def parse_date(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def version_number(value):
    return int(value[1:]) if re.fullmatch(r"v\d+", value or "") else 0


def normalize_paper(raw):
    paper = dict(raw)
    base, version = parse_arxiv_id(raw.get("entry_id") or raw.get("arxiv_id") or raw.get("arxiv_base_id", ""))
    explicit_version = raw.get("latest_version", version)
    latest_date = raw.get("latest_version_date") or raw.get("updated", "")
    date_source = raw.get("version_date_source") or ("arxiv_api.updated" if raw.get("updated") else "unknown")
    if not latest_date and explicit_version == "v1" and raw.get("published"):
        latest_date, date_source = raw["published"], "arxiv_api.published (v1)"
    paper.update(
        arxiv_base_id=base, latest_version=explicit_version,
        first_submission_date=raw.get("first_submission_date") or raw.get("published", ""),
        latest_version_date=latest_date,
        last_updated_date=raw.get("updated") or raw.get("latest_version_date", ""),
        version_source=raw.get("version_source", "arxiv_api.entry_id" if version != "unknown" else "unknown"),
        version_date_source=date_source,
        title=" ".join(raw.get("title", "").split()), doi=normalize_doi(raw.get("doi", "")),
    )
    paper["published_date"] = paper["first_submission_date"]
    paper["title_normalized"] = normalize_title(paper["title"])
    paper["title_hash"] = stable_hash(paper["title_normalized"])
    paper["abstract_hash"] = stable_hash(paper.get("abstract", ""))
    suffix = explicit_version if version_number(explicit_version) else ""
    paper["abstract_url"] = f"https://arxiv.org/abs/{base}" if base else ""
    paper["pdf_url"] = f"https://arxiv.org/pdf/{base}{suffix}" if base else ""
    paper["canonical_key"] = canonical_key(paper)
    return paper
