"""Recover one native invoice from spatial text when drawing order is split."""
import io
import re
import unicodedata
from datetime import date
from decimal import Decimal

from invoice_details import extract_details


def parse_layout_invoice(layout, plain, source):
    text = unicodedata.normalize('NFKC', layout)
    flat = re.sub(r'\s+', '', unicodedata.normalize('NFKC', plain))
    # This fallback supports the explicit left buyer / right seller layout.
    # An unfamiliar or ambiguous layout must go through the existing OCR path.
    if flat.count('购买方') != 1 or flat.count('销售方') != 1:
        return None
    numbers = re.findall(r'发票号码\s*:\s*(\d{8,24})(?!\d)', text)
    dates = re.findall(r'开票日期\s*:\s*(20\d{2})年(\d{1,2})月(\d{1,2})日', text)
    totals = re.findall(r'\(小写\)\s*:?\s*[¥￥]?\s*(-?[\d,]+\.\d{2})(?!\d)', text)
    names = []
    for line in text.splitlines():
        matches = list(re.finditer(r'名称\s*:\s*(\S[^\r\n]*?)(?= {2,}|$)', line))
        if len(matches) == 2:
            left, right = matches
            if '购' in line[:left.start()] and '销' in line[left.end():right.start()]:
                names.append((left[1].strip(), right[1].strip()))
    if any(len(values) != 1 for values in (numbers, dates, totals, names)):
        return None
    buyer, seller = names[0]
    if any(not name or len(name) > 100 or ':' in name for name in (buyer, seller)):
        return None
    try:
        day = date(*map(int, dates[0])).isoformat()
        total = Decimal(totals[0].replace(',', ''))
    except (ValueError, ArithmeticError):
        return None
    # Use original drawing-order text for item names: layout mode can merge a
    # table heading into the item when fonts share a baseline.
    details = extract_details(plain)
    sums = []
    headers = []
    for line in text.splitlines():
        amount_head = list(re.finditer(r'金[ \t]*额', line))
        tax_head = list(re.finditer(r'税[ \t]*额', line))
        if len(amount_head) == len(tax_head) == 1 and amount_head[0].end() < tax_head[0].start():
            headers.append(((amount_head[0].start()+amount_head[0].end())/2,
                            (tax_head[0].start()+tax_head[0].end())/2))
    if len(headers) == 1:
        amount_x, tax_x = headers[0]
        tolerance = (tax_x - amount_x) / 2
        for line in text.splitlines():
            if re.match(r'^\s*合\s*计\s+', line):
                values = list(re.finditer(r'[¥￥]\s*(-?[\d,]+\.\d{2})(?!\d)', line))
                if (len(values) == 2
                        and all(abs((value.start()+value.end())/2-x) < tolerance for value,x in zip(values,(amount_x,tax_x)))
                        and sum(Decimal(value[1].replace(',', '')) for value in values) == total):
                    sums.append(tuple(value[1].replace(',', '') for value in values))
    if len(sums) == 1:
        details['net_amount'], details['tax_amount'] = sums[0]
    return {'number': numbers[0], 'date': day, 'amount': format(total, '.2f'),
            'buyer': buyer, 'seller': seller, 'source': source,
            'needs_review': True, **details}


def parse_invoice_pdf(data, source):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) != 1:
            return None
        page = reader.pages[0]
        plain = page.extract_text() or ''
        if not plain.strip():
            return None
        return parse_layout_invoice(page.extract_text(extraction_mode='layout') or '', plain, source)
    except Exception:
        # Preserve the original text parser and OCR fallback on unsupported PDFs.
        return None
