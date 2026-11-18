"""Corporate receipt and sales-invoice matching within one book."""
from collections import defaultdict
import re
import unicodedata

import finance
import reconciliation
from db import ApiError


ACCOUNT_NAMES = {
    'advance': ('预收账款', '预收款项', '预收款'),
    'prepaid': ('预付账款', '预付款项', '预付款'),
}
POSTING_ACTIONS = {
    'bank_statement': 'reconcile_statement_post',
    'bank_receipt': 'reconcile_receipt_post',
    'reconciliation_invoice': 'reconcile_invoice_post',
}


def _name_key(value):
    name = unicodedata.normalize('NFKC', reconciliation.normalize(value))
    return re.sub(r'\s+', '', name)


def company_name(value):
    return bool(re.fullmatch(r'.+公司', _name_key(value)))


def _own_names(client):
    return {reconciliation.normalize(client.get(key)) for key in ('name', 'enterprise_name')} - {''}


def _other_company(value, client):
    return (client['enterprise_form'] == '公司' and company_name(value)
            and _name_key(value) not in {_name_key(name) for name in _own_names(client)})


def company_receipt(transaction, client):
    return ((reconciliation.fen(transaction.get('amount')) or 0) > 0
            and _other_company(transaction.get('counterparty'), client))


def company_invoice(invoice, client):
    return (reconciliation.normalize(invoice.get('seller')) in _own_names(client)
            and _other_company(invoice.get('buyer'), client))


def account_root(conn, cid, key):
    names = ACCOUNT_NAMES[key]
    rows = conn.execute('''SELECT code,name FROM accounts a WHERE a.client_id=? AND a.report_key=?
        AND NOT EXISTS (SELECT 1 FROM accounts p WHERE p.client_id=a.client_id
                        AND length(p.code)<length(a.code) AND a.code LIKE p.code||'%')
        ORDER BY a.code''', (cid, key)).fetchall()
    named = [row for row in rows if row['name'] in names]
    candidates = named or rows
    if len(candidates) != 1:
        raise ApiError(409, f'本账套未能确定唯一的{names[0]}科目，请核对科目设置')
    return candidates[0]


def _recorded_offset(conn, items, row, amount):
    action = POSTING_ACTIONS.get(row['source_type'])
    if not action:
        return False
    logged = conn.execute("SELECT detail FROM audit_logs WHERE action=? AND entity='vouchers' AND entity_id=? ORDER BY id LIMIT 1",
                          (action, str(row['voucher_id']))).fetchone()
    if not logged:
        return False
    detail = reconciliation._history_object(logged['detail'])
    settlement = (detail.get('automatic_posting') or {}).get('settlement') or {}
    if settlement.get('account_code') != row['account_code'] or settlement.get('remaining') != amount:
        return False
    for item in items:
        if item['voucher_id'] == settlement.get('voucher_id') and item['remaining'] == amount:
            items.remove(item)
            return True
    return False


def _apply_offset(items, amount):
    """Reduce open amounts. Keep uncertain allocations out of automatic matching."""
    exact = [item for item in items if item['remaining'] == amount]
    if exact:
        for item in exact[1:]:
            item['uncertain'] = True
        items.remove(exact[0])
        return 0
    if len(items) > 1 and amount < sum(item['remaining'] for item in items):
        for item in items:
            item['uncertain'] = True
    for item in items[:]:
        cleared = min(amount, item['remaining'])
        item['remaining'] -= cleared
        amount -= cleared
        if not item['remaining']:
            items.remove(item)
        if not amount:
            break
    return amount


def open_items(conn, client, party, key):
    """Read active entries, including legacy vouchers and named opening advances."""
    cid = client['id']
    opening_period = client['opening_period']
    sign = -1 if key == 'advance' else 1
    openings = conn.execute('''SELECT a.code account_code, o.debit_fen-o.credit_fen movement
        FROM opening_balances o JOIN accounts a ON a.client_id=o.client_id AND a.code=o.account_code
        WHERE o.client_id=? AND a.report_key=? AND trim(a.name)=?''', (cid, key, party))
    rows = [{**dict(row), 'date': opening_period+'-01', 'voucher_id': None,
             'reference': opening_period+' 期初余额', 'eligible': key == 'advance'} for row in openings]
    movements = conn.execute('''SELECT v.id voucher_id,v.date,v.period,v.number,v.source_type,v.counterparty,
        a.code account_code,a.name account_name,SUM(l.debit_fen-l.credit_fen) movement,
        (SELECT COALESCE(SUM(c.debit_fen-c.credit_fen),0) FROM accounting_voucher_lines c
         JOIN accounts account ON account.client_id=c.client_id AND account.code=c.account_code
         WHERE c.voucher_id=v.id AND account.report_key='cash') cash_net,
        EXISTS(SELECT 1 FROM accounting_voucher_lines r
         JOIN accounts account ON account.client_id=r.client_id AND account.code=r.account_code
         WHERE r.voucher_id=v.id AND account.category='income' AND r.credit_fen>r.debit_fen) has_income
        FROM vouchers v JOIN accounting_voucher_lines l ON l.voucher_id=v.id
        JOIN accounts a ON a.client_id=l.client_id AND a.code=l.account_code
        WHERE v.client_id=? AND v.status='active' AND v.period>=? AND a.report_key=?
          AND (trim(a.name)=? OR trim(v.counterparty)=?)
        GROUP BY v.id,a.code HAVING movement<>0 ORDER BY v.date,v.id,a.code''',
        (cid, opening_period, key, party, party))
    for row in movements:
        name = reconciliation.normalize(row['account_name'])
        tagged_party = reconciliation.normalize(row['counterparty'])
        if name != party and company_name(name):
            continue
        eligible = (row['cash_net'] > 0 if key == 'advance' else
                    row['cash_net'] == 0 and (row['has_income'] or row['source_type'] == 'reconciliation_invoice'))
        # All movements in a named detail affect its balance. A conflicting
        # voucher counterparty prevents using that movement as payment evidence.
        rows.append({**dict(row), 'eligible': bool(eligible and tagged_party in ('', party)),
                     'reference': '记 %s-%04d' % (row['period'], row['number'])})

    pending = defaultdict(list)
    overdrawn = defaultdict(int)
    for row in rows:
        code = row['account_code']
        amount = sign * row['movement']
        if amount < 0:
            if len(pending[code]) > 1 and row['voucher_id'] and _recorded_offset(conn, pending[code], row, -amount):
                continue
            overdrawn[code] += _apply_offset(pending[code], -amount)
        elif amount:
            absorbed = min(amount, overdrawn[code])
            overdrawn[code] -= absorbed
            if amount > absorbed:
                pending[code].append({**row, 'remaining': amount-absorbed, 'uncertain': False})
    return [item for items in pending.values() for item in items if item['eligible']]


def match_open(items, key, date, amount, voucher_id=None):
    possible = [item for item in items if item['date'] <= date and
                (voucher_id is None or item['voucher_id'] == voucher_id)]
    exact = [item for item in possible if item['remaining'] == amount]
    if len(exact) > 1 or any(item['uncertain'] for item in exact):
        raise ApiError(409, f'{ACCOUNT_NAMES[key][0]}下有无法唯一对应的未冲销往来，请核对历史凭证')
    if exact:
        matched = exact[0]
        return {key: matched[key] for key in ('account_code', 'voucher_id', 'date', 'reference', 'remaining')}
    return None
