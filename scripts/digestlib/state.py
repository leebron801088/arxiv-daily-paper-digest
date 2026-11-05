from __future__ import annotations

import copy
import json
import os
import tempfile
from contextlib import contextmanager

from .identity import identity_aliases, normalize_doi, normalize_paper, parse_arxiv_id, parse_date, version_number


def read_json(path, default):
    if not path.exists():
        return copy.deepcopy(default)
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".digest-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_seen(path):
    raw = read_json(path, {})
    if isinstance(raw, list):
        migrated = {}
        for identifier in raw:
            base, version = parse_arxiv_id(identifier)
            key = f"arxiv:{base}" if base else f"legacy:{identifier}"
            previous = migrated.get(key, {})
            latest = max([previous.get("latest_version_seen", "unknown"), version], key=version_number)
            first = min([previous.get("first_version_seen", version), version], key=version_number)
            migrated[key] = {"canonical_key": key, "arxiv_base_id": base,
                             "latest_version_seen": latest, "first_version_seen": first,
                             "last_pushed_version": latest, "last_pushed_at": "legacy",
                             "legacy_ids": list(set(previous.get("legacy_ids", []) + [identifier]))}
        return migrated
    if not isinstance(raw, dict):
        raise ValueError("Unrecognized seen_papers state; original file left untouched")
    return raw


def find_previous(paper, records):
    key = paper["canonical_key"]
    if key in records:
        return key, records[key]
    aliases = identity_aliases(paper)
    for old_key, record in records.items():
        if paper.get("arxiv_base_id") and record.get("arxiv_base_id") and paper["arxiv_base_id"] != record["arxiv_base_id"]:
            continue
        if paper.get("doi") and record.get("doi") and normalize_doi(paper["doi"]) != normalize_doi(record["doi"]):
            continue
        if aliases.intersection(identity_aliases(record)):
            return old_key, record
    return key, None


def observe(paper, previous, now, pushed=False, pending=False):
    record = dict(previous or {})
    incoming = normalize_paper(paper)
    old_version = record.get("latest_version_seen", "unknown")
    old_date = parse_date(record.get("latest_version_date"))
    new_date = parse_date(incoming.get("latest_version_date"))
    if (version_number(old_version) > version_number(paper["latest_version"]) or
            (old_date and new_date and old_date > new_date)):
        record["last_seen_at"] = now
        return record
    record.update({k: v for k, v in incoming.items() if v not in (None, "", [], "unknown")})
    record["first_seen_at"] = (previous or {}).get("first_seen_at", now)
    record["last_seen_at"] = now
    record["first_version_seen"] = (previous or {}).get("first_version_seen", paper["latest_version"])
    record["latest_version_seen"] = max([old_version, paper["latest_version"]], key=version_number)
    record.setdefault("last_pushed_at", None)
    record["pending"] = pending
    if pushed:
        record.update(last_pushed_at=now, last_pushed_version=paper["latest_version"],
                      last_pushed_date=paper["latest_version_date"], pending=False)
    return record


@contextmanager
def run_lock(state_dir):
    import fcntl
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / ".watcher.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another watcher is running for this state directory") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
