"""Keyless arXiv discovery and shared delivery tracking for the daily digest."""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from digestlib.daily_topics import DAILY_TOPIC, TOPICS, VAD as TOPIC, matches_feed, query_for
from digestlib.identity import normalize_doi, normalize_paper, normalize_title, parse_arxiv_id, parse_date, version_number
from digestlib.dedup import deduplicate
from digestlib.sources import ArxivSource, HTTP, parse_version_history
from digestlib.state import atomic_json, find_previous, load_seen, observe, read_json, run_lock

TZ = ZoneInfo('Asia/Shanghai')
MAX_AGE = timedelta(days=14)
PAGE_SIZE = 25
RSS = 'https://rss.arxiv.org/rss/cs.CV+cs.AI+cs.LG+eess.IV+cs.MM'
EXTRA_RSS = 'https://rss.arxiv.org/rss/eess.SP+eess.SY+cs.RO+cs.GR'
QUERY = ('(ti:video OR abs:video OR ti:surveillance OR abs:surveillance) AND '
         '(ti:anomaly OR abs:anomaly OR ti:anomalies OR abs:anomalies OR '
         'ti:abnormal OR abs:abnormal)')


def clean(html):
    return ' '.join(unescape(re.sub(r'<[^>]+>', ' ', html or '')).split())


class CitationMeta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name') and attrs.get('content'):
            self.fields.setdefault(attrs['name'], []).append(attrs['content'])


def from_abstract_page(http, base, hint=''):
    html = http.request('https://arxiv.org/abs/' + base).decode('utf-8', errors='replace')
    history = parse_version_history(html)
    first = next((d for v, d in history if v == 'v1'), None)
    if not first:
        raise ValueError('submission history has no verified v1 date')
    version, updated = max(history, key=lambda entry: version_number(entry[0]))
    meta = CitationMeta()
    meta.feed(html)
    fields = meta.fields
    title = fields.get('citation_title', [''])[0]
    if not title:
        raise ValueError('missing arXiv citation title')
    block = re.search(r'<blockquote\b[^>]*\bclass=["\'][^"\']*\babstract\b[^"\']*["\'][^>]*>(.*?)</blockquote>', html, re.I | re.S)
    abstract = re.sub(r'^Abstract:\s*', '', clean(block[1])) if block else clean(hint)
    return normalize_paper({'entry_id': f'https://arxiv.org/abs/{base}{version}',
                            'title': title, 'abstract': abstract, 'authors': fields.get('citation_author', []),
                            'doi': fields.get('citation_doi', [''])[0],
                            'published': first, 'updated': updated,
                            'first_submission_date_source': 'arxiv.submission_history.v1',
                            'version_source': 'arxiv.submission_history', 'version_date_source': 'arxiv.submission_history'})


def rss_candidates(http, topic=TOPIC):
    feed = EXTRA_RSS if topic == 'motor_fault_diagnosis' else RSS
    channel = ET.fromstring(http.request(feed)).find('channel')
    if channel is None:
        raise ValueError('invalid arXiv RSS channel')
    hints = {}
    for item in channel.findall('item'):
        base, _ = parse_arxiv_id(item.findtext('link', ''))
        title = clean(item.findtext('title', ''))
        abstract = clean(item.findtext('description', ''))
        text = title + ' ' + abstract
        if base and matches_feed(topic, text):
            hints[base] = abstract
    return hints


def eligible(paper, now):
    try:
        first = parse_date(paper.get('first_submission_date'))
    except ValueError:
        return False
    return (bool(paper.get('arxiv_base_id') and paper.get('abstract') and first)
            and paper.get('first_submission_date_source') in {'arxiv_api.published', 'arxiv.submission_history.v1'}
            and now - MAX_AGE <= first <= now)


def daily_identity_keys(paper):
    """Strong aliases for suppressing already delivered work, even under a second ID."""
    base, _ = parse_arxiv_id(paper.get('arxiv_base_id') or paper.get('entry_id', ''))
    doi = normalize_doi(paper.get('doi', ''))
    title = normalize_title(paper.get('title', ''))
    authors = paper.get('authors') or []
    first_author = normalize_title(authors[0]) if authors else ''
    keys = {('arxiv', base)} if base else set()
    if doi and not doi.startswith('10.48550/arxiv.'):
        keys.add(('doi', doi))
    if title and first_author:
        keys.add(('title_author', title, first_author))
    return keys


def already_pushed(paper, records):
    _, match = find_previous(paper, records)
    if match and (match.get('last_pushed_at') or match.get('last_pushed_version')):
        return True
    keys = daily_identity_keys(paper)
    return any((previous.get('last_pushed_at') or previous.get('last_pushed_version')) and
               keys.intersection(daily_identity_keys(previous)) for previous in records.values())


def scan(http, now, records, ids=(), topic=TOPIC):
    rows, warnings = [], []
    start = now - MAX_AGE
    complete = False
    if ids:
        hints = {base: '' for base in ids}
        backend = 'arxiv_abstract_pages'
    else:
        backend = 'arxiv_api'
        source = ArxivSource(http)
        minute = lambda date: date.astimezone(timezone.utc).strftime('%Y%m%d%H%M')
        terms = QUERY if topic == TOPIC else query_for(topic)
        query = f'({terms}) AND submittedDate:[{minute(start)} TO {minute(now + timedelta(minutes=1))}]'
        try:
            for offset in range(0, 250, PAGE_SIZE):
                entries, total = source.query(search_query=query, start=offset, max_results=PAGE_SIZE,
                                              sortBy='submittedDate', sortOrder='descending')
                for paper in entries:
                    paper['first_submission_date_source'] = 'arxiv_api.published'
                rows.extend(entries)
                if offset + len(entries) >= total:
                    complete = True
                    break
                if not entries:
                    raise ValueError('empty arXiv page before the reported end')
            if not complete:
                warnings.append('arXiv query reached the pagination cap; coverage is partial')
        except (RuntimeError, ValueError, ET.ParseError, OSError) as exc:
            warnings.append(f'arXiv API search failed: {exc}')
        hints = {}
        if not complete:
            backend = 'arxiv_api+rss' if rows else 'arxiv_rss'
            try:
                hints = rss_candidates(http, topic)
                warnings.append('RSS covers recent announcements only; a full 14-day search was not completed')
            except (RuntimeError, ValueError, ET.ParseError, OSError) as exc:
                warnings.append(f'arXiv RSS unavailable: {exc}')
    for base, hint in hints.items():
        # Avoid one extra request per previously delivered paper during RSS fallback.
        if already_pushed(normalize_paper({'arxiv_base_id': base}), records):
            continue
        try:
            rows.append(from_abstract_page(http, base, hint))
        except (RuntimeError, ValueError, OSError) as exc:
            warnings.append(f'arXiv {base}: cannot verify v1 date: {exc}')
    papers = sorted((p for p in deduplicate(rows) if eligible(p, now) and not already_pushed(p, records)),
                    key=lambda p: (parse_date(p['first_submission_date']), p['arxiv_base_id']), reverse=True)
    return {'topic': topic, 'start': start.astimezone(TZ).isoformat(), 'end': now.astimezone(TZ).isoformat(),
            'backend': backend, 'complete': complete, 'warnings': warnings, 'papers': papers}


def scan_all(http, now, records, ids=()):
    if ids:
        result = scan(http, now, records, ids)
        for paper in result['papers']:
            paper['candidate_topics'] = list(TOPICS)
        return {**result, 'topic': DAILY_TOPIC, 'coverage': {}}
    snapshots = [scan(http, now, records, topic=topic) for topic in TOPICS]
    topics_by_id, papers = {}, []
    for part in snapshots:
        for paper in part['papers']:
            topics_by_id.setdefault(paper['arxiv_base_id'], []).append(part['topic'])
            papers.append(paper)
    unique = deduplicate(papers)
    for paper in unique:
        paper['candidate_topics'] = topics_by_id[paper['arxiv_base_id']]
    return {**snapshots[0], 'topic': DAILY_TOPIC, 'backend': 'arxiv',
            'complete': all(part['complete'] for part in snapshots), 'papers': unique,
            'coverage': {part['topic']: {'complete': part['complete'], 'backend': part['backend'],
                                         'candidates': len(part['papers']), 'warnings': part['warnings']}
                         for part in snapshots},
            'warnings': [f"{part['topic']}: {warning}" for part in snapshots for warning in part['warnings']]}


def record(state_dir, snapshot, ids, now, *, strict=False):
    if snapshot.get('topic') not in {*TOPICS, DAILY_TOPIC}:
        raise ValueError('not a daily research snapshot')
    by_id = {p['arxiv_base_id']: p for p in snapshot['papers']}
    if any(base not in by_id or not eligible(by_id[base], now) for base in ids):
        raise ValueError('all delivered IDs must be eligible candidates from the snapshot')
    state_file = state_dir / 'seen_papers.json'
    with run_lock(state_dir):
        records = load_seen(state_file)
        saved = []
        for base in dict.fromkeys(ids):
            paper = by_id[base]
            topic = paper.get('digest_topic', snapshot['topic'])
            if topic not in TOPICS:
                raise ValueError(f'{base}: missing or invalid digest_topic')
            key, previous = find_previous(paper, records)
            if already_pushed(paper, records):
                if strict:
                    raise ValueError(f'{base}: already delivered or duplicated in this issue; nothing recorded')
                continue
            # observe preserves metadata if a newer revision was already seen.
            row = observe({**paper, 'primary_topic': topic, 'status': 'NEW'}, previous, now.isoformat(), pushed=True)
            row.update(last_pushed_at=now.isoformat(), last_pushed_version=paper['latest_version'],
                       last_pushed_date=paper['latest_version_date'], pending=False)
            records[key] = row
            saved.append(base)
        if saved:
            backup = state_dir / 'seen_papers.legacy-backup.json'
            raw = read_json(state_file, {})
            if isinstance(raw, list) and not backup.exists():
                atomic_json(backup, raw)
            atomic_json(state_file, records)
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path.cwd() / '.arxiv-digest-state')
    sub = parser.add_subparsers(dest='mode', required=True)
    discover = sub.add_parser('scan', help='Find unseen candidates without writing delivery state')
    discover.add_argument('--vad-only', action='store_true', help='Limit discovery to video anomaly detection')
    discover.add_argument('--id', action='append', default=[], help='Verify a candidate found on the official arXiv website')
    deliver = sub.add_parser('record', help='Record only the IDs actually included in the prepared digest')
    deliver.add_argument('snapshot', type=Path)
    deliver.add_argument('ids', nargs='+')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    ids = [parse_arxiv_id(value)[0] for value in (args.id if args.mode == 'scan' else args.ids)]
    if any(not base for base in ids):
        parser.error('invalid arXiv ID')
    if args.mode == 'scan':
        discover = scan if args.vad_only else scan_all
        result = discover(HTTP(timeout=15, retries=1), now, load_seen(args.state_dir / 'seen_papers.json'), ids)
    else:
        result = {'recorded': record(args.state_dir, read_json(args.snapshot, {}), ids, now, strict=True)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        raise SystemExit(1)
