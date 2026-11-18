"""Bank evidence for wage, social insurance and housing fund expense vouchers."""
import re
import unicodedata
from collections import Counter

import finance
import reconciliation
import reconciliation_batch
import source_archive
from db import ApiError, audit


DETAILS = {
    '工资': ('工资', '职工工资', '工资薪酬', '职工薪酬', '工资薪金', '工资奖金', '工资及奖金', '工资薪金及奖金'),
    '社保': ('社保', '社保费', '社会保险', '社会保险费', '社会保障费', '社会保险(支出)'),
    '公积金': ('公积金', '公积金(支出)', '住房公积金', '住房公积金(支出)'),
}
SOCIAL = re.compile(r'社保|社会保险|养老保险|医疗保险|失业保险|工伤保险|生育保险|五险')
TREASURY = re.compile(r'国库|国家金库')
OTHER_PAYMENT = re.compile(r'个人所得税|代[扣缴]个税|工资薪金个税|代发手续费|工资手续费')


def _compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', reconciliation.normalize(text)))


def _memo(row):
    return '；'.join(dict.fromkeys(text for key in ('note', 'purpose')
                                 if (text := reconciliation.normalize(row.get(key)))))


def _classification(transaction, receipt):
    memos = '；'.join(_compact(_memo(row)) for row in (transaction, receipt or {}))
    parties = '；'.join(_compact(row.get('counterparty')) for row in (transaction, receipt or {}))
    evidence = memos + '；' + parties
    social = bool(SOCIAL.search(evidence))
    housing = '公积金' in evidence
    wage = '工资' in memos or '薪酬' in memos
    # A generic 职工薪酬 heading can describe either of the two benefits.
    if sum((social, housing, '工资' in memos)) > 1:
        return '', '摘要或回单涉及多类职工款项，请核实各项实际金额'
    category = '社保' if social else '公积金' if housing else '工资' if wage else ''
    if category and OTHER_PAYMENT.search(memos):
        return category, '同时识别到个税或手续费字样，请核实实际付款用途'
    if social and not TREASURY.search(evidence):
        return category, '已识别社保字样，请补充能确认款项缴入国库的回单或流水'
    return category, ''


def _read_archive(conn, cid, archive_id):
    archive = source_archive.validate(conn, cid, archive_id)
    if not archive:
        raise ApiError(400, '请先上传并识别银行回单或流水')
    parsed = reconciliation._history_object(archive['recognized_json'])
    txs, receipts = parsed.get('transactions', []), parsed.get('receipts', [])
    if any(not isinstance(rows, list) or len(rows) > 500 or
           any(not isinstance(row, dict) for row in rows) for rows in (txs, receipts)):
        raise ApiError(400, '每批最多核对 500 笔流水和 500 张回单，请拆分后重新上传')
    return parsed, txs, receipts


def _source_rows(parsed, txs, receipts, client):
    if parsed.get('transactions_from_receipts'):
        txs = []
    allowed = {_compact(client['name']), _compact(client.get('enterprise_name'))} - {''}
    holder_names = {_compact(name) for name in parsed.get('account_names', [parsed.get('account_name')])} - {''}
    statement_issue = ('识别到的流水户名与所选账套不一致，请核对原件及账套'
                       if holder_names - allowed else '')
    if len(holder_names) > 1 or any('不同户名' in warning for warning in parsed.get('warnings', [])):
        statement_issue = '本批流水识别到多个户名，请按公司分别上传'

    pairs = reconciliation.match_receipts(txs, receipts)['matches']
    tx_counts = Counter(reconciliation._transaction_key(transaction) for transaction in txs)
    receipt_counts = Counter(reconciliation_batch.normalize_receipt_source(receipt, receipt) for receipt in receipts)
    matched, conflicts = set(), set()
    for pair in pairs:
        index = pair['transaction_index']
        transaction = txs[index]
        receipt = None
        issue = statement_issue
        if pair['status'] == 'matched':
            receipt_index = pair['receipt_index']
            receipt = receipts[receipt_index]
            matched.add(receipt_index)
            if receipt_counts[reconciliation_batch.normalize_receipt_source(receipt, receipt)] > 1:
                issue = '本批有重复的回单编号或回单记录，请核对'
        elif pair['status'] == 'review':
            conflicts.update(pair['candidate_indices'])
            issue = '同日同金额的流水和回单无法唯一匹配，请核对'
        if tx_counts[reconciliation._transaction_key(transaction)] > 1:
            issue = '本批有日期、金额、对方和摘要相同的流水，请核对重复记录'
        if receipt:
            owner = _compact(receipt.get('own_name'))
            if owner not in allowed and (owner or not holder_names & allowed):
                issue = '配对回单的本方户名缺失或与所选账套不一致，请核对'
        yield f'statement:{index}', transaction, receipt, issue

    for index, receipt in enumerate(receipts):
        if index in matched:
            continue
        issue = ''
        if index in conflicts:
            issue = '同日同金额的流水和回单无法唯一匹配，请核对'
        elif receipt_counts[reconciliation_batch.normalize_receipt_source(receipt, receipt)] > 1:
            issue = '本批有重复的回单编号或回单记录，请核对'
        elif _compact(receipt.get('own_name')) not in allowed:
            issue = '回单的本方户名缺失或与所选账套不一致，请核对'
        yield f'receipt:{index}', receipt, receipt, issue


def _bank_accounts(conn, cid):
    return [account for account in reconciliation._cash_leaves(conn, cid)
            if account['code'].startswith('1002')]


def bank_accounts(conn, client_id):
    cid = source_archive.client_id(conn, client_id)
    finance._client(conn, cid)
    return {'accounts': [{'code': a['code'], 'name': a['name']} for a in _bank_accounts(conn, cid)]}


def _expense_line(conn, cid, category, value):
    root = reconciliation._suggested_business_root(conn, cid, {'account_code': '5602', 'account_name': '管理费用'})
    if not root:
        raise ApiError(409, '账套缺少可用的管理费用科目，请核对科目设置')
    code = root['code']
    matches = [row for row in conn.execute('''SELECT code,name FROM accounts
        WHERE client_id=? AND code LIKE ? AND code<>? AND report_key='administration'
          AND direction='debit' AND category='expense' ORDER BY code''', (cid, code + '%', code))
        if _compact(row['name']) in DETAILS[category]]
    leaves = [row for row in matches if not reconciliation._has_children(conn, cid, row['code'])]
    if len(leaves) == 1:
        return finance._entry(leaves[0]['code'], debit=value)
    if matches:
        raise ApiError(409, f'管理费用下无法确定唯一的「{category}」末级科目，请核对科目设置')
    _, planned = reconciliation_batch._target_line(conn, cid, code, sub=category)
    if planned and planned.get('business_parent_code'):
        return dict(planned, debit_fen=value)
    raise ApiError(409, f'当前账套的管理费用不能新增「{category}」明细，请核对科目设置')


def _existing(conn, cid, transaction, receipt):
    numbered = bool(reconciliation.normalize((receipt or {}).get('number')))
    statement = reconciliation._existing_statement_posting(conn, cid, transaction, include_receipts=not numbered)
    bank_receipt = (reconciliation_batch._receipt_posting(conn, cid, transaction, receipt, use_transaction_key=not numbered)
                    if receipt else None)
    if statement and bank_receipt and statement['voucher_id'] != bank_receipt['voucher_id']:
        raise ApiError(409, '流水和回单已分别关联不同凭证，请核对原凭证')
    return statement or bank_receipt


def _possible_duplicate(conn, cid, bank, transaction, receipt, date, value):
    rows = conn.execute('''SELECT DISTINCT v.counterparty,v.source_type,v.source_id
        FROM vouchers v JOIN accounting_voucher_lines l ON l.voucher_id=v.id
        WHERE v.client_id=? AND v.status='active' AND v.date=? AND l.account_code=?
          AND l.credit_fen-l.debit_fen=?''', (cid, date, bank['code'], value))
    party = _compact(transaction.get('counterparty'))
    receipt_number = reconciliation.normalize((receipt or {}).get('number'))
    for row in rows:
        other = _compact(row['counterparty'])
        if party and other and party != other:
            continue
        if receipt_number and row['source_type'] == 'bank_receipt':
            previous = (row['source_id'] or '').removeprefix(date + ':')
            if previous and previous != receipt_number and not re.fullmatch('[0-9a-f]{64}', previous):
                continue
        return True
    return False


def _prepare(conn, cid, bank, source):
    ident, t, receipt, issue = source
    category, classification_issue = _classification(t, receipt)
    if not category and not classification_issue:
        return None
    amount = reconciliation.fen(t.get('amount'))
    row = {'id': ident, 'category': category, 'date': reconciliation.normalize(t.get('date')),
           'counterparty': reconciliation.normalize(t.get('counterparty')), 'amount_fen': amount,
           'statement_note': _memo(t) if ident.startswith('statement:') else '',
           'receipt_note': _memo(receipt) if receipt else '',
           'sources': list(dict.fromkeys(r['source'] for r in (t, receipt or {}) if r.get('source')))}
    try:
        if issue:
            raise ApiError(409, issue)
        if not amount or t.get('direction_needs_review') or (receipt and receipt.get('direction_needs_review')):
            raise ApiError(409, '金额或收付方向待核实，请对照原件')
        date = finance._date(row['date'])
        if amount > 0:
            raise ApiError(409, '识别为银行收款，请核实退款或冲回的原凭证')
        existing = _existing(conn, cid, t, receipt)
        if existing:
            classified = sum(line['debit_fen'] - line['credit_fen'] for line in existing['posted_lines']
                             if line['report_key'] == 'administration' and
                             _compact(line['account_name']) in DETAILS.get(category, ()))
            return dict(row, **existing, status='posted', expense_fen=classified,
                        reason='已有关联凭证' if classified == -amount else '已有关联凭证，请查看原分录')
        if classification_issue:
            raise ApiError(409, classification_issue)
        if not bank:
            raise ApiError(400, '请选择本批款项实际支付的银行存款科目')
        finance._open(conn, cid, date[:7])
        if _possible_duplicate(conn, cid, bank, t, receipt, date, -amount):
            raise ApiError(409, '同一资金科目已发现同日同金额的支出凭证，请核对是否重复')
        expense = _expense_line(conn, cid, category, -amount)
    except ApiError as error:
        return dict(row, status='review', reason=error.message)
    return dict(row, status='ready', reason='', expense_fen=-amount, expense_line=expense)


def _post(conn, cid, bank, source, row, archive_id, user, account_name):
    ident, t, receipt, _ = source
    bank_line = finance._entry(bank['code'], credit=-row['amount_fen'], category='operating')
    lines = reconciliation._resolve_voucher_lines(conn, cid, [row['expense_line'], bank_line], t, user)
    lines = finance.with_account_summaries(conn, cid, lines)
    summary = ' '.join(part for part in (row['category'], row['counterparty'],
                                        row['statement_note'] or row['receipt_note']) if part)
    key = reconciliation._transaction_key(t)
    source_type = 'bank_receipt' if receipt else 'bank_statement'
    source_id = reconciliation_batch.normalize_receipt_source(t, receipt) if receipt else key
    attachment_count = len(row['sources'])
    vid = finance._insert_voucher(conn, cid, row['date'], summary[:200], lines, user, source_type, source_id,
                                  attachment_count=attachment_count, counterparty=row['counterparty'])
    if receipt and ident.startswith('statement:'):
        matching = '流水与回单的交易日期、收付款金额和交易对方相符'
    else:
        matching = '按银行回单的交易信息入账' if receipt else '按银行流水的交易信息入账'
    basis = {'mode': 'liquidation_payroll', 'account_name': account_name, 'category': row['category'],
             'business_account_code': lines[0]['account_code'], 'bank_account_code': bank['code'],
             'matching_basis': matching, 'classification': f'管理费用 / {row["category"]}'}
    source_archive.attach(conn, cid, archive_id, user, t, receipt=receipt, voucher_id=vid, basis=basis)
    audit(conn, user, 'reconcile_receipt_post' if receipt else 'reconcile_statement_post', 'vouchers', vid,
          dict(basis, transaction_key=key, transaction=t, receipt=(receipt or {}).get('number'),
               matched_receipt=receipt))
    return dict(row, **reconciliation._posting_result(conn, cid, vid, False, t), status='posted', reason='')


def process(conn, payload, user, *, commit=False):
    cid = source_archive.client_id(conn, payload.get('client_id'))
    client = finance._client(conn, cid)
    archive_id = payload.get('archive_id')
    parsed, txs, receipts = _read_archive(conn, cid, archive_id)
    bank_code = payload.get('bank_account_code')
    bank = next((account for account in _bank_accounts(conn, cid) if account['code'] == bank_code), None)
    sources = {source[0]: source for source in _source_rows(parsed, txs, receipts, client)}
    if commit:
        selected = payload.get('row_ids')
        if (payload.get('confirmed') is not True or not isinstance(selected, list) or not selected or
                len(selected) > 1000 or any(not isinstance(key, str) or key not in sources for key in selected)):
            raise ApiError(400, '请从预览中选择并确认需要入账的款项')
        if not bank:
            raise ApiError(400, '请选择本批款项实际支付的银行存款科目')
        sources = {key: sources[key] for key in dict.fromkeys(selected)}

    rows = []
    for source in sources.values():
        if commit:
            row = None
            try:
                with finance._write(conn):
                    row = _prepare(conn, cid, bank, source)
                    if row and row['status'] == 'ready':
                        row = _post(conn, cid, bank, source, row, archive_id, user, client['name'])
            except ApiError as error:
                if not row:
                    raise
                row = dict(row, status='review', reason=error.message)
        else:
            row = _prepare(conn, cid, bank, source)
        if row:
            rows.append(row)
    return {'client_id': cid, 'rows': rows,
            'recognized_account': parsed.get('account_name', ''), 'warnings': parsed.get('warnings', [])}
