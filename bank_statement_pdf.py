"""Read ruled, debit/credit-column statement PDFs without losing empty cells."""
import io
import re
import unicodedata
from datetime import date
from decimal import Decimal


HEADERS = {'date': '交易日期', 'debit': '借方(出账)', 'credit': '贷方(入账)',
           'balance': '余额', 'note': '摘要', 'counterparty': '收(付)方名称',
           'account': '收(付)方账号', 'type': '交易类型'}


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def _page_table(page):
    """Use the printed cell rules, including rows taller than their date cell."""
    spans, rules = [], []
    pen = None

    def point(x, y, matrix):
        a, b, c, d, e, f = map(float, matrix)
        return float(x) * a + float(y) * c + e, float(x) * b + float(y) * d + f

    def line(start, end):
        if abs(start[1] - end[1]) < .2 and abs(start[0] - end[0]) > 10:
            rules.append((min(start[0], end[0]), max(start[0], end[0]), start[1]))

    def operand(op, args, cm, tm):
        nonlocal pen
        if op == b'm':
            pen = point(*args, cm)
        elif op == b'l':
            end = point(*args, cm)
            if pen is not None: line(pen, end)
            pen = end
        elif op == b're':
            x, y, w, h = map(float, args)
            line(point(x, y, cm), point(x + w, y, cm))
            line(point(x, y + h, cm), point(x + w, y + h, cm))
        elif op in (b'n', b'S', b's', b'f', b'F', b'f*', b'Q'):
            pen = None

    def text(value, cm, tm, font, size):
        if not value.strip(): return
        # A multiline run without individual coordinates is not a cell map.
        if len(value.strip().splitlines()) > 1: raise ValueError('Unpositioned text lines')
        x, y = point(tm[4], tm[5], cm)
        spans.append({'text': value.strip(), 'x': x, 'y': y})

    page_text = page.extract_text(visitor_text=text, visitor_operand_before=operand) or ''
    headings = {}
    for key, label in HEADERS.items():
        matches = [span for span in spans if compact(span['text']) == label]
        if len(matches) != 1: return None
        headings[key] = matches[0]
    if max(s['y'] for s in headings.values()) - min(s['y'] for s in headings.values()) > 2:
        return None
    columns = {}
    for key, span in headings.items():
        under = [rule for rule in rules if rule[0] <= span['x'] < rule[1]
                 and 0 < span['y'] - rule[2] < 20]
        if not under: return None
        columns[key] = max(under, key=lambda rule: rule[2])
    if len({(round(l, 1), round(r, 1)) for l, r, _ in columns.values()}) != len(HEADERS):
        return None
    top = columns['date'][2]
    if any(abs(rule[2] - top) > .2 for rule in columns.values()): return None
    left, right, _ = columns['date']
    boundaries = sorted({round(y, 2) for l, r, y in rules if y <= top + .2
                         and abs(l - left) < .2 and abs(r - right) < .2}, reverse=True)
    if len(boundaries) < 2: return None
    cells = []
    for upper, lower in zip(boundaries, boundaries[1:]):
        # Every date row needs a matching rule beneath each of the other cells.
        if any(not any(abs(y - lower) < .2 and abs(l - c[0]) < .2 and abs(r - c[1]) < .2
                       for l, r, y in rules) for c in columns.values()): return None
        row = {}
        for key, (l, r, _) in columns.items():
            parts = [s for s in spans if l <= s['x'] < r and lower < s['y'] < upper]
            row[key] = ''.join(s['text'] for s in sorted(parts, key=lambda s: (-s['y'], s['x'])))
        if any(row.values()): cells.append(row)
    return cells, page_text


def _amount(value):
    value = compact(value).replace(',', '').replace('￥', '').replace('¥', '')
    if not value or value in ('-', '--'): return Decimal(0)
    if not re.fullmatch(r'[+-]?\d+(?:\.\d{1,2})?', value): raise ValueError('Invalid amount')
    number = Decimal(value)
    if abs(number) > Decimal('9000000000000'): raise ValueError('Amount out of range')
    return number


def parse_statement_pdf(data, source):
    """Return a complete table result, or None so other formats can use OCR."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if not 0 < len(reader.pages) <= 30: return None
        tables = [_page_table(page) for page in reader.pages]
    except Exception:
        return None
    # Never silently accept only the readable pages of a mixed-format PDF.
    if any(table is None for table in tables): return None
    rows, warnings, holders, counts = [], [], set(), set()
    for page_number, (cells, page_text) in enumerate(tables, 1):
        holder = re.search(r'用户所属公司\s*[:：]\s*(.*?)(?=\s*打印时间\s*[:：]|\n|$)', page_text)
        if holder and holder[1].strip(): holders.add(holder[1].strip())
        counts.update(int(n) for n in re.findall(r'记录数\s*[:：]\s*(\d+)', page_text))
        for row_number, cell in enumerate(cells, 1):
            label = f'第 {page_number} 页第 {row_number} 行'
            day = compact(cell['date'])
            if day in ('合计', '总计', '本页合计'): continue
            found = re.fullmatch(r'(20\d{2})[-/](\d{2})[-/](\d{2})(?:\d{2}:\d{2}:\d{2})?', day)
            try:
                if not found: raise ValueError('Missing date')
                day = date(*map(int, found.groups())).isoformat()
            except ValueError:
                day = ''
                warnings.append(label + '交易日期无法确认，请对照原件补充')
            value, unclear = '', False
            try:
                debit, credit = _amount(cell['debit']), _amount(cell['credit'])
                if bool(debit) == bool(credit) or credit < 0: raise ValueError('Ambiguous direction')
                value = format(-abs(debit) if debit else credit, '.2f')
            except ValueError:
                unclear = True
                warnings.append(label + '借贷金额缺失、冲突或格式无效，请对照原件确认金额及收付款方向')
            rows.append({'date': day, 'amount': value, 'counterparty': cell['counterparty'],
                         'note': '；'.join(value for value in (cell['note'], cell['type']) if value),
                         'source': source, 'needs_review': True, 'direction_needs_review': unclear})
    if len(counts) == 1 and len(rows) != next(iter(counts)):
        warnings.append(f'原件标注 {next(iter(counts))} 条记录，实际识别 {len(rows)} 条，请检查是否有遗漏')
    if len(holders) > 1: warnings.append('交易清单页识别到不同户名，请人工核对并填写')
    return {'transactions': rows, 'account_name': next(iter(holders)) if len(holders) == 1 else '',
            'warnings': warnings}
