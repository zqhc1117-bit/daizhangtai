"""Read-only invoice progress across the invoice ledger and saved source documents."""

import datetime as dt
import decimal
import json
import re
import unicodedata

from db import ApiError


def _object(value):
    try:
        parsed = json.loads(value or '{}')
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _number(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).upper()


def _period(date):
    try:
        return dt.date.fromisoformat(str(date)).strftime('%Y-%m')
    except ValueError:
        return ''


def _amount(value):
    try:
        number = decimal.Decimal(str(value).replace(',', ''))
        return int((number * 100).quantize(decimal.Decimal('1'))) if number.is_finite() else None
    except (decimal.InvalidOperation, ValueError, TypeError):
        return None


def ticket_progress(conn, period):
    """Deduplicate by company/invoice number and verify posting against active vouchers.

    Recognition alone is pending work, not evidence of posting. Bank statements,
    receipts, request drafts and void vouchers cannot complete invoice progress.
    Explicit invoice-ledger periods take precedence over source-document dates.
    """
    if not isinstance(period, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
        raise ApiError(400, '票据进度期间无效')
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    clients = {r[0] for r in conn.execute('SELECT id FROM clients WHERE deleted=0')}
    active = {r['id']: dict(r) for r in conn.execute(
        "SELECT id,client_id,period,source_type,source_id FROM vouchers WHERE status='active'")}
    direct = {(r['client_id'], r['source_id']): r['id'] for r in active.values()
              if r['source_type'] == 'reconciliation'}
    combined = {}

    def add(cid, invoice, origin, ident, priority, voucher_id=None, explicit_period=None):
        if cid not in clients or not isinstance(invoice, dict) or not invoice:
            return
        number = str(invoice.get('number') or '')
        key = (cid, _number(number) or f'{origin}:{ident}')
        posted = voucher_id in active and active[voucher_id]['client_id'] == cid
        record = dict(client_id=cid, period=explicit_period or _period(invoice.get('date')),
                      date=str(invoice.get('date') or ''), number=number,
                      summary=str(invoice.get('summary') or invoice.get('item_name') or ''),
                      total_fen=invoice.get('total_fen') if 'total_fen' in invoice else _amount(invoice.get('amount')),
                      origin=origin, id=ident, status='posted' if posted else 'unprocessed',
                      voucher_id=voucher_id if posted else None,
                      voucher_period=active[voucher_id]['period'] if posted else None, _priority=priority)
        previous = combined.get(key)
        if previous:
            if priority < previous['_priority']:
                record, previous = previous, record
            if record['status'] != 'posted' and previous['status'] == 'posted':
                record.update(status='posted', voucher_id=previous['voucher_id'], voucher_period=previous['voucher_period'])
        combined[key] = record

    # A finalized single-invoice source replaces its raw OCR snapshot, including
    # corrected invoice numbers/dates. Repeated uploads still deduplicate by number.
    replacements = {}
    linked_invoices = []
    source_counts = {}
    if 'source_archive_links' in tables:
        for row in conn.execute('SELECT * FROM source_archive_links ORDER BY id'):
            invoice = _object(row['final_json']).get('invoice')
            if not isinstance(invoice, dict) or not invoice:
                continue
            linked_invoices.append((invoice, dict(row)))
            key = (row['client_id'], row['archive_id'], invoice.get('source'))
            previous = replacements.get(key)
            posted = row['voucher_id'] in active and active[row['voucher_id']]['client_id'] == row['client_id']
            if not previous or posted or not previous[2]:
                replacements[key] = (invoice, dict(row), posted)
    if 'source_archives' in tables:
        for row in conn.execute('SELECT id,client_id,recognized_json FROM source_archives ORDER BY id'):
            invoices = _object(row['recognized_json']).get('invoices', [])
            if not isinstance(invoices, list):
                continue
            for index, invoice in enumerate(invoices):
                if not isinstance(invoice, dict):
                    continue
                source = invoice.get('source')
                replacement = replacements.get((row['client_id'], row['id'], source)) if source else None
                same_source = sum(isinstance(i, dict) and i.get('source') == source for i in invoices)
                source_counts[(row['client_id'], row['id'], source)] = same_source
                if replacement and same_source == 1:
                    invoice, link, _ = replacement
                    add(row['client_id'], invoice, 'archive', f"{row['id']}:{index}", 2, link['voucher_id'])
                else:
                    add(row['client_id'], invoice, 'archive', f"{row['id']}:{index}", 1)
    for invoice, link in linked_invoices:
        key = (link['client_id'], link['archive_id'], invoice.get('source'))
        if source_counts.get(key) != 1 or not invoice.get('source'):
            add(link['client_id'], invoice, 'archive', f"link:{link['id']}", 2, link['voucher_id'])
    if 'reconciliation_records' in tables:
        for row in conn.execute('SELECT id,client_id,invoice_json,batch_metadata_json FROM reconciliation_records ORDER BY id'):
            cid = row['client_id']
            linked = _object(row['batch_metadata_json']).get('linked_voucher_id')
            try:
                linked = int(linked) if linked is not None else None
            except (ValueError, TypeError):
                linked = None
            add(cid, _object(row['invoice_json']), 'reconciliation', row['id'], 3,
                direct.get((cid, str(row['id']))) or linked)
    for row in conn.execute('SELECT * FROM invoices ORDER BY id'):
        add(row['client_id'], dict(row), 'invoice', row['id'], 4, row['voucher_id'], row['period'])
    result = []
    for record in combined.values():
        if record['period'] == period:
            record.pop('_priority')
            result.append(record)
    return sorted(result, key=lambda r: (r['client_id'], r['date'], r['number']))
