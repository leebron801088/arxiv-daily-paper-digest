"""Render a checked five-topic daily arXiv issue in the reference PDF style."""
from __future__ import annotations

import argparse
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

from daily_digest import TZ, already_pushed, daily_identity_keys, eligible
from digestlib.daily_topics import DAILY_TOPIC, PAPERS_PER_TOPIC, TOPICS
from digestlib.identity import parse_arxiv_id, parse_date
from digestlib.state import atomic_json, load_seen, read_json

TEXT = colors.HexColor('#26353A')
MUTED = colors.HexColor('#596C72')
TEAL = colors.HexColor('#007C8C')
STRIPE = colors.HexColor('#13AEBE')
BAND = colors.HexColor('#EFF2F3')
PILL = colors.HexColor('#E6F6F7')
RULE = colors.HexColor('#CEDADC')
W, H = A4
MARGIN = 54
WIDTH = W - 2 * MARGIN
LABEL = 91
FLOOR = 54
SECTIONS = [('problem', '研究问题'), ('method', '核心方法'), ('innovation', '主要创新'),
            ('actual_work', '它到底做了什么'), ('takeaway', '借鉴点'), ('caution', '阅读注意')]
NUMERALS = '一二三四五'


def validated_issue(snapshot, editorial, history=None):
    if snapshot.get('topic') != DAILY_TOPIC:
        raise ValueError('Expected a five-topic daily snapshot')
    now = parse_date(snapshot['end'])
    if not now:
        raise ValueError('Missing snapshot end timestamp')
    by_id = {p['arxiv_base_id']: p for p in snapshot['papers']}
    grouped = {key: [] for key in TOPICS}
    seen = set()
    for item in editorial['papers']:
        base, _ = parse_arxiv_id(item['id'])
        topic = item['topic']
        paper = by_id.get(base)
        if topic not in grouped or not paper or not eligible(paper, now):
            raise ValueError(f'Invalid or ineligible selection: {item["id"]}')
        keys = daily_identity_keys(paper)
        if seen.intersection(keys) or already_pushed(paper, history or {}):
            raise ValueError(f'{base}: duplicate selection or already delivered')
        if len(grouped[topic]) >= PAPERS_PER_TOPIC:
            raise ValueError(f'{topic}: at most {PAPERS_PER_TOPIC} papers per topic')
        for field in ('tag', 'venue', 'evidence', *(key for key, _ in SECTIONS)):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f'{base}: missing editorial field {field}')
        seen.update(keys)
        grouped[topic].append((paper, item))
    return now, grouped


def register_styles(font=None, font_index=0):
    song = Path('/System/Library/Fonts/Supplemental/Songti.ttc')
    if font:
        pdfmetrics.registerFont(TTFont('DailyBody', str(font), subfontIndex=font_index))
        body = bold = 'DailyBody'
    elif song.exists():
        for name, index in [('DailyBody', 6), ('DailyBold', 1)]:
            pdfmetrics.registerFont(TTFont(name, str(song), subfontIndex=index))
        body, bold = 'DailyBody', 'DailyBold'
    else:
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        body = bold = 'STSong-Light'
    return {
        'headline': ParagraphStyle('headline', fontName=bold, fontSize=26, leading=31, alignment=TA_CENTER, textColor=TEXT, wordWrap='CJK'),
        'band': ParagraphStyle('band', fontName=bold, fontSize=14, leading=20, textColor=TEXT, wordWrap='CJK'),
        'tag': ParagraphStyle('tag', fontName=bold, fontSize=9, leading=13, textColor=TEAL, wordWrap='CJK'),
        'title': ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=13, leading=17, textColor=TEXT),
        'field': ParagraphStyle('field', fontName=bold, fontSize=9.8, leading=16, textColor=MUTED, wordWrap='CJK'),
        'body': ParagraphStyle('body', fontName=body, fontSize=11.1, leading=17.5, textColor=TEXT, wordWrap='CJK'),
        'note': ParagraphStyle('note', fontName=body, fontSize=9.6, leading=15, textColor=MUTED, wordWrap='CJK'),
        'meta': ParagraphStyle('meta', fontName=body, fontSize=10, leading=15.5, textColor=MUTED, wordWrap='CJK'),
    }


def paragraph(value, style, width, markup=False):
    p = Paragraph(value if markup else escape(value), style)
    return p, p.wrap(width, H)[1]


def draw(p, c, x, y, height):
    p.drawOn(c, x, y - height)


def card(paper, item, order, styles):
    tag = paragraph(item['tag'], styles['tag'], LABEL - 13)
    title = paragraph(f'{order}. {paper["title"]}', styles['title'], WIDTH - LABEL)
    dates = parse_date(paper['first_submission_date']).astimezone(TZ)
    base = paper['arxiv_base_id']
    version = paper.get('latest_version') or 'unknown'
    version = version if parse_arxiv_id(base + version) == (base, version) and version.startswith('v') else '未核实'
    suffix = '' if version == '未核实' else version
    links = (f'<link href="https://arxiv.org/pdf/{base}{suffix}" color="#007C8C">arXiv 论文 PDF</link>'
             f'　|　<link href="https://arxiv.org/abs/{base}" color="#007C8C">arXiv 摘要页面</link>')
    fields = [('作者', ', '.join(paper.get('authors', [])) or '作者未核实', 'meta'),
              ('链接', links, 'link'), ('会议 / 状态', item['venue'], 'meta'),
              ('首发 / 版本', f'{dates:%Y-%m-%d %H:%M}（北京时间）  |  {version}  |  arXiv:{base}', 'meta')]
    fields += [(label, item[key], 'body') for key, label in SECTIONS]
    fields.append(('阅读依据', item['evidence'], 'meta'))
    rows = []
    for label, value, role in fields:
        left = paragraph(label, styles['field'], LABEL - 9)
        right = paragraph(value, styles['meta' if role == 'link' else role], WIDTH - LABEL, markup=role == 'link')
        rows.append((left, right, max(left[1], right[1]) + (8 if role == 'body' else 4)))
    head_height = max(title[1], tag[1] + 8) + 13
    return tag, title, rows, head_height, head_height + sum(row[2] for row in rows) + 18


def header(c, date, number, title, note, snapshot, count, styles, continued):
    text, height = paragraph(f'{date.year}.{date.month}.{date.day} 论文推送', styles['headline'], WIDTH)
    draw(text, c, MARGIN, H - 41, height)
    c.setFont('Helvetica', 8.3)
    c.setFillColor(MUTED)
    c.drawCentredString(W / 2, H - 88, 'DAILY RESEARCH DIGEST   |   arXiv PDF direct links')
    y = H - 106
    topic, ht = paragraph(f'{NUMERALS[number]}、{title}' + ('（续）' if continued else ''), styles['band'], WIDTH - 32)
    box = max(39, ht + 20)
    c.setFillColor(BAND)
    c.roundRect(MARGIN, y - box, WIDTH, box, 5, stroke=0, fill=1)
    c.setFillColor(STRIPE)
    c.roundRect(MARGIN, y - box, 4, box, 2, stroke=0, fill=1)
    draw(topic, c, MARGIN + 15, y - (box - ht) / 2, ht)
    y -= box + 13
    details = f'本期收录 {count} 篇。{note}'
    if not snapshot.get('complete'):
        details += ' 检索覆盖可能不完整。'
    detail, dh = paragraph(details, styles['note'], WIDTH)
    draw(detail, c, MARGIN, y, dh)
    return y - dh - 27


def footer(c, page, snapshot, styles):
    first = parse_date(snapshot['start']).astimezone(TZ)
    last = parse_date(snapshot['end']).astimezone(TZ)
    c.setStrokeColor(RULE)
    c.setLineWidth(.5)
    c.line(MARGIN, 43, W - MARGIN, 43)
    label, height = paragraph(f'首发窗口（北京时间） {first:%m-%d %H:%M} 至 {last:%m-%d %H:%M}', styles['note'], WIDTH - 25)
    draw(label, c, MARGIN, 35, height)
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 9)
    c.drawRightString(W - MARGIN, 23, str(page))
    c.showPage()


def render(snapshot, editorial, path, delivery_path=None, *, history=None, font=None, font_index=0):
    now, grouped = validated_issue(snapshot, editorial, history)
    styles = register_styles(font, font_index)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'PDF already exists: {path}')
    with NamedTemporaryFile(dir=path.parent, prefix='.daily-pdf-', suffix='.pdf', delete=False) as stream:
        temp = Path(stream.name)
    try:
        c = Canvas(str(temp), pagesize=A4, pageCompression=1)
        c.setTitle(f'{now.astimezone(TZ):%Y-%m-%d} 论文推送')
        c.setSubject('arXiv research digest')
        page = 0
        for number, (topic, definition) in enumerate(TOPICS.items()):
            entries = grouped[topic]
            note = editorial.get('notes', {}).get(topic, definition['focus'])
            y = header(c, now.astimezone(TZ), number, definition['title'], note, snapshot, len(entries), styles, False)
            if not entries:
                message = '本期未收录论文。已推送、超过首发时间窗口或相关性不足的论文均不纳入。'
                if not snapshot.get('coverage', {}).get(topic, {}).get('complete', snapshot.get('complete')):
                    message = '本主题未完成充分检索；暂未收录已核实的论文。'
                p, ht = paragraph(message, styles['body'], WIDTH)
                draw(p, c, MARGIN, y, ht)
            for order, (paper, item) in enumerate(entries, 1):
                tag, title, rows, head_height, total = card(paper, item, order, styles)
                if y - total < FLOOR and order > 1:
                    page += 1
                    footer(c, page, snapshot, styles)
                    y = header(c, now.astimezone(TZ), number, definition['title'], note, snapshot, len(entries), styles, True)
                if y - total < FLOOR:
                    raise ValueError(f'{paper["arxiv_base_id"]}: entry is too long for one page')
                c.setFillColor(PILL)
                c.roundRect(MARGIN, y - tag[1] - 8, LABEL - 11, tag[1] + 8, 4, stroke=0, fill=1)
                draw(tag[0], c, MARGIN + 6, y - 4, tag[1])
                draw(title[0], c, MARGIN + LABEL, y, title[1])
                y -= head_height
                for (left, lh), (right, rh), height in rows:
                    draw(left, c, MARGIN + 2, y, lh)
                    draw(right, c, MARGIN + LABEL, y, rh)
                    y -= height
                c.setStrokeColor(RULE)
                c.setLineWidth(.65)
                c.line(MARGIN, y - 6, W - MARGIN, y - 6)
                y -= 18
            page += 1
            footer(c, page, snapshot, styles)
        c.save()
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()
    if delivery_path:
        papers = [{**paper, 'digest_topic': topic} for topic, entries in grouped.items() for paper, _ in entries]
        atomic_json(delivery_path, {**snapshot, 'papers': papers})
    return page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('editorial', type=Path)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--delivery', type=Path, help='Write the exact PDF selection for the record command')
    parser.add_argument('--state-dir', type=Path, default=Path.cwd() / '.arxiv-digest-state')
    parser.add_argument('--font', type=Path, help='Embed a CJK TrueType font (.ttf or .ttc)')
    parser.add_argument('--font-index', type=int, default=0, help='Face index for a .ttc font')
    args = parser.parse_args()
    pages = render(read_json(args.snapshot, {}), read_json(args.editorial, {}), args.pdf, args.delivery,
                   history=load_seen(args.state_dir / 'seen_papers.json'), font=args.font, font_index=args.font_index)
    print(f'{pages} pages: {args.pdf}')


if __name__ == '__main__':
    main()
