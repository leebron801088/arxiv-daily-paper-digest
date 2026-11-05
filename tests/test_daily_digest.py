import socket
from datetime import datetime, timedelta, timezone

import pytest

import daily_digest as daily
from digestlib.identity import normalize_paper
from digestlib.sources import HTTP
from digestlib.state import load_seen

NOW = datetime(2026, 11, 5, 1, tzinfo=timezone.utc)


def paper(base, first, version='v1'):
    return normalize_paper({'entry_id': base + version, 'title': 'VAD ' + base,
                            'abstract': 'Video anomaly detection.', 'published': first.isoformat(),
                            'updated': NOW.isoformat(), 'first_submission_date_source': 'arxiv_api.published'})


def test_limits_first_submission_and_reuses_push_history(monkeypatch, tmp_path):
    boundary = paper('2610.00001', NOW - timedelta(days=14))
    too_old = paper('2610.00002', NOW - timedelta(days=14, seconds=1), 'v2')
    fresh = paper('2611.00003', NOW - timedelta(days=2), 'v2')
    future = paper('2611.00004', NOW + timedelta(seconds=1))
    without_date = {**paper('2611.00005', NOW), 'first_submission_date': '', 'published': ''}

    def query(_, **params):
        assert 'submittedDate:[202610220100 TO 202611050101]' in params['search_query']
        assert params['max_results'] == 25
        return [boundary, too_old, fresh, future, without_date], 5

    monkeypatch.setattr(daily.ArxivSource, 'query', query)
    snapshot = daily.scan(None, NOW, {})
    assert snapshot['complete'] and [p['arxiv_base_id'] for p in snapshot['papers']] == ['2611.00003', '2610.00001']
    assert daily.record(tmp_path, snapshot, ['2611.00003'], NOW) == ['2611.00003']
    assert daily.record(tmp_path, snapshot, ['2611.00003'], NOW) == []
    records = load_seen(tmp_path / 'seen_papers.json')
    assert [p['arxiv_base_id'] for p in daily.scan(None, NOW, records)['papers']] == ['2610.00001']
    with pytest.raises(ValueError):
        daily.record(tmp_path, snapshot, ['2610.00001'], NOW + timedelta(seconds=1))
    assert not (tmp_path / 'last_run.json').exists()


def test_api_failure_uses_rss_but_does_not_confuse_announcement_with_v1(monkeypatch):
    def failed(*a, **kw):
        raise RuntimeError('read timed out')

    class FakeHTTP:
        def request(self, url):
            if url == daily.RSS:
                return b'<rss><channel><item><link>https://arxiv.org/abs/2610.00001</link><title>Video anomaly detection</title><pubDate>Thu, 05 Nov 2026 00:00:00 GMT</pubDate></item><item><link>https://arxiv.org/abs/2611.00002</link><title>Video anomaly detection</title></item></channel></rss>'
            first = 'Wed, 21 Oct 2026 00:00:00 UTC' if url.endswith('2610.00001') else 'Wed, 04 Nov 2026 00:00:00 UTC'
            return f'<meta name="citation_title" content="Video anomaly detection"><meta name="citation_author" content="Author"><blockquote class="abstract mathjax">Abstract: Paper abstract</blockquote>[v1] {first} [v2] Wed, 04 Nov 2026 12:00:00 UTC'.encode()

    monkeypatch.setattr(daily.ArxivSource, 'query', failed)
    result = daily.scan(FakeHTTP(), NOW, {})
    assert result['backend'] == 'arxiv_rss' and not result['complete']
    assert [p['arxiv_base_id'] for p in result['papers']] == ['2611.00002']
    assert result['papers'][0]['first_submission_date_source'] == 'arxiv.submission_history.v1'


def test_socket_read_timeout_retries(monkeypatch):
    attempts = []

    def opener(*args, **kwargs):
        attempts.append(1)
        raise socket.timeout('test read timeout')

    monkeypatch.setattr('urllib.request.urlopen', opener)
    monkeypatch.setattr('time.sleep', lambda _: None)
    with pytest.raises(RuntimeError, match=r'timed out \(15s\)'):
        HTTP(timeout=15, retries=1).request('https://export.arxiv.org/api/query')
    assert len(attempts) == 2


def test_five_topics_share_identity_and_record_editorial_topic(monkeypatch, tmp_path):
    spatial = 'spatial_intelligence_world_models'
    candidate = paper('2611.00001', NOW - timedelta(days=1))

    def mocked_scan(_, now, records, ids=(), topic=daily.TOPIC):
        return {'topic': topic, 'complete': True, 'backend': 'arxiv_api', 'warnings': [],
                'papers': [candidate] if topic in {daily.TOPIC, spatial} else []}

    monkeypatch.setattr(daily, 'scan', mocked_scan)
    snapshot = daily.scan_all(None, NOW, {})
    assert len(snapshot['coverage']) == 5
    assert len(snapshot['papers']) == 1
    assert snapshot['papers'][0]['candidate_topics'] == [daily.TOPIC, spatial]
    with pytest.raises(ValueError, match='digest_topic'):
        daily.record(tmp_path, snapshot, [candidate['arxiv_base_id']], NOW)
    snapshot['papers'][0]['digest_topic'] = spatial
    assert daily.record(tmp_path, snapshot, [candidate['arxiv_base_id']], NOW) == [candidate['arxiv_base_id']]
    assert load_seen(tmp_path / 'seen_papers.json')[candidate['canonical_key']]['primary_topic'] == spatial


def test_published_doi_or_exact_title_and_author_detect_second_arxiv_id():
    old = {**paper('2611.00001', NOW), 'title': 'A Video-Anomaly Method',
           'authors': ['A. Chen'], 'doi': '10.1000/xyz', 'last_pushed_at': NOW.isoformat()}
    same_doi = {**paper('2611.00002', NOW), 'doi': 'https://doi.org/10.1000/XYZ'}
    same_title_author = {**paper('2611.00003', NOW), 'title': 'A video anomaly method', 'authors': ['A Chen']}
    different_author = {**same_title_author, 'authors': ['B Li']}
    history = {old['canonical_key']: old}
    assert daily.already_pushed(same_doi, history)
    assert daily.already_pushed(same_title_author, history)
    assert not daily.already_pushed(different_author, history)


def test_strict_registration_never_commits_half_a_pdf(tmp_path):
    previous = paper('2611.00001', NOW)
    new = paper('2611.00002', NOW)
    snapshot = {'topic': daily.TOPIC, 'papers': [previous, new]}
    daily.record(tmp_path, snapshot, ['2611.00001'], NOW)
    state = tmp_path / 'seen_papers.json'
    before = state.read_bytes()
    with pytest.raises(ValueError, match='already delivered'):
        daily.record(tmp_path, snapshot, ['2611.00002', '2611.00001'], NOW, strict=True)
    assert state.read_bytes() == before
