from datetime import timedelta

import pytest
from pypdf import PdfReader

from daily_digest import record
from daily_digest_pdf import SECTIONS, TOPICS, render, validated_issue
from test_daily_digest import NOW, paper


def issue():
    candidates, entries = [], []
    for i, topic in enumerate(TOPICS):
        for j in range(3):
            base = f'0000.{i * 3 + j + 1:05d}'
            candidates.append(paper(base, NOW - timedelta(days=1)))
            entries.append({'id': base, 'topic': topic, **{
                k: '合成测试。' for k in ('tag', 'venue', 'evidence', *(k for k, _ in SECTIONS))}})
    return {'topic': 'daily_research', 'start': (NOW - timedelta(days=14)).isoformat(),
            'end': NOW.isoformat(), 'complete': True, 'papers': candidates}, entries


def test_render_fifteen_papers_and_register_exact_delivery(tmp_path):
    snapshot, entries = issue()
    pdf, delivery = tmp_path / 'issue.pdf', tmp_path / 'delivery.json'
    render(snapshot, {'papers': entries}, pdf, delivery)
    from digestlib.state import read_json
    actual = read_json(delivery, {})
    ids = [row['id'] for row in entries]
    assert [row['arxiv_base_id'] for row in actual['papers']] == ids
    links = {str(ref.get_object()['/A']['/URI']) for page in PdfReader(pdf).pages for ref in page.get('/Annots', [])}
    assert links == {url for base in ids for url in (f'https://arxiv.org/pdf/{base}v1', f'https://arxiv.org/abs/{base}')}
    assert record(tmp_path / 'state', actual, ids, NOW, strict=True) == ids
    with pytest.raises(ValueError, match='already delivered'):
        record(tmp_path / 'state', actual, ids, NOW, strict=True)


def test_fourth_in_topic_and_cross_topic_duplicates_are_rejected():
    snapshot, entries = issue()
    fourth = {**entries[3], 'topic': entries[0]['topic']}
    with pytest.raises(ValueError, match='at most 3'):
        validated_issue(snapshot, {'papers': [*entries[:3], fourth]})
    duplicate = {**entries[0], 'topic': entries[3]['topic']}
    with pytest.raises(ValueError, match='duplicate'):
        validated_issue(snapshot, {'papers': [entries[0], duplicate]})
