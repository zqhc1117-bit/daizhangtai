"""Parse labelled receipt blocks without inferring direction from an amount."""
import re
import unicodedata
from datetime import date
from decimal import Decimal


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text)))


def uppercase_amount(text):
    """A limited financial-numeral cross-check; unfamiliar text stays unknown."""
    text = compact(text).removeprefix('人民币').replace('圆', '元')
    digits = dict(zip('零壹贰叁肆伍陆柒捌玖', range(10)))
    units = {'拾': 10, '佰': 100, '仟': 1000}
    match = re.fullmatch(r'(?:(?P<yuan>[零壹贰叁肆伍陆柒捌玖拾佰仟万亿]+)元)?(?P<tail>[零壹贰叁肆伍陆柒捌玖角分整正]+)', text)
    if not match:
        return None
    total = section = digit = 0
    previous = 10000
    for char in match['yuan'] or '':
        if char in digits:
            if digit: return None
            digit = digits[char]
        elif char in units:
            if not digit or units[char] >= previous: return None
            section += digit * units[char]
            digit = 0
            previous = units[char]
        elif char in ('万', '亿'):
            section += digit
            if not section: return None
            if char == '万': total += section * 10000
            else: total = (total + section) * 100000000
            section = digit = 0
            previous = 10000
    yuan = total + section + digit
    tail = match['tail']
    if tail in ('整', '正'):
        if not match['yuan']: return None
        cents = 0
    else:
        fraction = re.fullmatch(r'零?(?:([壹贰叁肆伍陆柒捌玖])角)?(?:零?([壹贰叁肆伍陆柒捌玖])分|整|正)?', tail)
        if not fraction or not any(fraction.groups()): return None
        cents = digits.get(fraction[1], 0) * 10 + digits.get(fraction[2], 0)
    return Decimal(yuan) + Decimal(cents) / 100


def parse_receipts(items):
    """One record per explicit heading; fields never cross receipt boundaries."""
    headings = []
    for item in items:
        text = compact(item['text'])
        heading = re.fullmatch(r'(入账|出账|收款|付款)回单', text)
        labelled = re.fullmatch(r'.*回单\((收款|付款)\)', text)
        if heading or labelled:
            direction = (heading or labelled)[1]
            headings.append((item, 1 if direction in ('入账', '收款') else -1))
    headings.sort(key=lambda pair: pair[0]['y'])
    if any(b[0]['y'] - a[0]['y'] < 24 for a, b in zip(headings, headings[1:])):
        return []  # Side-by-side/overlapping blocks need a different layout parser.
    receipts = []
    for index, (heading, sign) in enumerate(headings):
        bottom = headings[index + 1][0]['y'] if index + 1 < len(headings) else float('inf')
        block = [compact(x['text']) for x in items if heading['y'] < x['y'] < bottom]

        def field(label):
            values = [m[1] for text in block if (m := re.fullmatch('(?:' + label + r'):(.*)', text))]
            return values[0] if len(values) == 1 else ''

        raw_date = field(r'(?:交易)?日期')
        day = re.fullmatch(r'(20\d{2})(?:年|[-/])?(\d{1,2})(?:月|[-/])?(\d{1,2})日?', raw_date)
        try:
            day = date(*map(int, day.groups())).isoformat() if day else ''
        except ValueError:
            day = ''
        small = field(r'(?:交易金额)?\(小写\)|小写')
        large = uppercase_amount(field(r'(?:交易金额)?\(大写\)|大写'))
        amount_pattern = r'(?:CNY|RMB|[¥￥])?([\d,]+\.\d{2})'
        number = re.fullmatch(amount_pattern, small, re.I)
        corrected = False
        if not number and large is not None:
            # Accept O/0 and Y/¥ corrections only when the uppercase amount agrees.
            repaired = small.replace('O', '0').replace('o', '0')
            repaired = re.sub(r'^Y(?=\d)', '¥', repaired, flags=re.I)
            number = re.fullmatch(amount_pattern, repaired, re.I)
            corrected = bool(number)
        value = Decimal(number[1].replace(',', '')) if number else None
        warnings = []
        if value is not None and large is not None and value != large:
            value = None
            warnings.append('回单大小写金额不一致，请核对原件')
        elif corrected:
            warnings.append('回单小写金额含易混字符，已与大写金额交叉核对，请人工确认')
        payer = field(r'付款人(?:户名)?')
        payee = field(r'收款人(?:户名)?')
        record = {'number': field('回单编号'), 'date': day,
                  'amount': format(value * sign, '.2f') if value is not None else '',
                  'counterparty': payer if sign > 0 else payee,
                  'own_name': payee if sign > 0 else payer,
                  'note': ' '.join(filter(None, (field('业务类型'), field(r'交易摘要|摘要')))),
                  'needs_review': True, 'direction_needs_review': False,
                  'warnings': warnings}
        purpose = '；'.join(filter(None, (field(r'(?:交易|资金|款项)?用途'), field(r'(?:交易)?附言'), field(r'(?:交易)?备注'))))
        if purpose:
            record['purpose'] = purpose
        missing = [label for key, label in (('date', '交易日期'), ('amount', '金额'), ('own_name', '本方户名')) if not record[key]]
        if missing:
            warnings.append('回单缺少' + '、'.join(missing) + '，请核对原件并补充')
        receipts.append(record)
    return receipts
