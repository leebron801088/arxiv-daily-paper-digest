from datetime import timedelta

import pytest
from pypdf import PdfReader

from daily_digest import record
from daily_digest_pdf import SECTIONS, TOPICS, render, validated_issue
from test_daily_digest import NOW, paper


def issue():
    explanations = {
        'problem': '传统模型依赖稀疏采样，难以还原前兆、触发和后果之间的时序关系。',
        'method': '先定位候选事件，再用交叉注意力融合前后片段并对证据分段打分。',
        'innovation': '引入基于遮挡的训练目标，显式区分能支持判断的片段和背景。',
        'actual_work': '在两个公开数据集上完成事件定位和片段检索，并检验不同采样间隔下的表现。',
        'takeaway': '可将分段证据评分用于二次复核，但要重新标注目标任务中的关键事件。',
        'caution': '论文实验集中于固定相机；实际部署时仍需验证运动模糊和视角变化的影响。',
    }
    candidates, entries = [], []
    for i, topic in enumerate(TOPICS):
        for j in range(3):
            base = f'0000.{i * 3 + j + 1:05d}'
            candidates.append({
                **paper(base, NOW - timedelta(days=1)),
                'title': f'Fixture {base}: Evidence Alignment for Cross-Domain Temporal Representation and Retrieval',
                'authors': ['Givenname Familyname'] * 8,
            })
            entries.append({
                'id': base, 'topic': topic, 'tag': '时序证据对齐', 'venue': '预印本；录用未确认',
                'evidence': '合成测试内容', **explanations,
            })
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
    reader = PdfReader(pdf)
    assert len(reader.pages) == 5
    assert [entry.title for entry in reader.outline] == [topic['title'] for topic in TOPICS.values()]
    links = set()
    for page, topic in zip(reader.pages, TOPICS.values()):
        assert topic['title'] in page.extract_text()
        page_links = {str(ref.get_object()['/A']['/URI']) for ref in page.get('/Annots', [])}
        assert len(page_links) == 6
        links.update(page_links)
    assert links == {url for base in ids for url in (f'https://arxiv.org/pdf/{base}v1', f'https://arxiv.org/abs/{base}')}
    assert record(tmp_path / 'state', actual, ids, NOW, strict=True) == ids
    with pytest.raises(ValueError, match='already delivered'):
        record(tmp_path / 'state', actual, ids, NOW, strict=True)


def test_long_entries_continue_and_empty_topics_remain(tmp_path):
    snapshot, entries = issue()
    for entry in entries[:3]:
        for key, _ in SECTIONS:
            entry[key] *= 4
    pdf = tmp_path / 'continuation.pdf'
    render({**snapshot, 'complete': False, 'coverage': {entries[0]['topic']: {'complete': True}}},
           {'papers': entries[:3]}, pdf)
    reader = PdfReader(pdf)
    first_topic = next(iter(TOPICS.values()))['title']
    first_three = reader.pages[:3]
    assert len(reader.pages) == 7
    assert first_topic in first_three[0].extract_text()
    for page, entry in zip(first_three, entries[:3]):
        text = page.extract_text()
        assert first_topic in text
        assert f"Fixture {entry['id']}" in text
        assert '阅读注意' in text
        assert '检索覆盖可能不完整' not in text
        assert len(page['/Annots']) == 2
    assert '（续）' in first_three[1].extract_text()
    for page, topic in zip(reader.pages[3:], list(TOPICS.values())[1:]):
        text = page.extract_text()
        assert topic['title'] in text
        assert '检索覆盖可能不完整' in text.replace('\n', '')
        assert '未完成充分检索' in text


def test_fourth_in_topic_and_cross_topic_duplicates_are_rejected():
    snapshot, entries = issue()
    fourth = {**entries[3], 'topic': entries[0]['topic']}
    with pytest.raises(ValueError, match='at most 3'):
        validated_issue(snapshot, {'papers': [*entries[:3], fourth]})
    duplicate = {**entries[0], 'topic': entries[3]['topic']}
    with pytest.raises(ValueError, match='duplicate'):
        validated_issue(snapshot, {'papers': [entries[0], duplicate]})
