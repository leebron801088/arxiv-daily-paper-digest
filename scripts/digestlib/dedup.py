"""Merge candidates that resolve to the same paper identity."""
from datetime import datetime, timezone

from .identity import normalize_paper, parse_date, version_number
from .state import find_previous

def deduplicate(papers):
    result = {}
    for raw in papers:
        paper = normalize_paper(raw)
        old_key, old = find_previous(paper, result)
        if old:
            date = lambda p: parse_date(p.get("latest_version_date")) or datetime.min.replace(tzinfo=timezone.utc)
            newest = max([old, paper], key=lambda p: (version_number(p.get("latest_version")), date(p)))
            # Preserve the strongest stable identity when a DOI-only duplicate follows arXiv.
            strongest = min([old, paper], key=lambda p: ["arxiv", "doi", "s2", "title"].index(p["canonical_key"].split(":")[0]))
            merged = {**old, **{k: v for k, v in paper.items() if v not in (None, "", [])},
                      **{k: v for k, v in newest.items() if v not in (None, "", [])}}
            for field in ("arxiv_base_id", "canonical_key"):
                if strongest.get(field):
                    merged[field] = strongest[field]
            if strongest.get("arxiv_base_id"):
                suffix = merged["latest_version"] if version_number(merged.get("latest_version")) else ""
                merged["pdf_url"] = f"https://arxiv.org/pdf/{strongest['arxiv_base_id']}{suffix}"
                merged["abstract_url"] = strongest["abstract_url"]
            result.pop(old_key)
            result[merged["canonical_key"]] = merged
        else:
            result[paper["canonical_key"]] = paper
    return list(result.values())
