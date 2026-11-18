"""Progress audits use persisted evidence without altering business records."""
from pathlib import Path
from contextlib import closing
import json
import tempfile
import unittest

import business
import db
import finance
import source_archive
from ticket_progress import ticket_progress


class TicketProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'tickets.sqlite3'
        db.initialize(self.path)
        self.conn = db.connect(self.path)
        source_archive.init_schema(self.conn)
        self.user = {'id': 1, 'username': 'test', 'name': '测试员'}
        self.conn.execute("INSERT INTO users(id,username,name,password_hash) VALUES(1,'test','测试员','unused')")
        for cid in (1, 2):
            business.handle(self.conn, 'POST', '/api/clients', dict(name=f'票据测试{cid}', code=f'T{cid}',
                taxpayer_type='small', standard='small2013', start_period='2026-01'), self.user, {})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def invoice(self, number='TEST-1', **extra):
        return dict(date='2026-11-03', number=number, amount='100.00', buyer='测试公司',
                    seller='供货商', source='invoice.pdf', item_name='合成发票', **extra)

    def ledger(self, cid=1, number='TEST-1', period='2026-11'):
        return business.handle(self.conn, 'POST', '/api/invoices', dict(client_id=cid, period=period,
            kind='input', number=number, date=period+'-03', amount_fen=10000, tax_fen=0, total_fen=10000), self.user, {})['id']

    def saved(self, cid=1, number='TEST-1', **extra):
        invoice = self.invoice(number);invoice.update(extra)
        return self.conn.execute('''INSERT INTO reconciliation_records(client_id,invoice_number,transaction_json,
            invoice_json,account_name,status,review_note,user_id,created_at,transaction_key,batch_metadata_json)
            VALUES(?,?,'{}',?,'test','matched','',1,'2026-11-17',?,'{}')''',
            (cid, number, json.dumps(invoice), number)).lastrowid

    def archive(self, ident, cid=1, invoices=None):
        self.conn.execute('INSERT INTO source_archives VALUES(?,?,?,?,1,?)',
            (ident, cid, ident, json.dumps({'invoices': invoices or [], 'receipts': [self.invoice('RECEIPT')]}), '2026-11-17'))

    def post(self, cid=1, source='manual', source_id=None, date='2026-11-05'):
        return finance._insert_voucher(self.conn, cid, date, '合成进度凭证',
            [finance._entry('1002', 10000), finance._entry('3001', credit=10000)], self.user,
            source_type=source, source_id=str(source_id) if source_id is not None else None)

    def link(self, archive, invoice, vid, cid=1):
        self.conn.execute('''INSERT INTO source_archive_links(archive_id,client_id,voucher_id,source_key,
            final_json,basis_json,created_at,updated_at) VALUES(?,?,?,?,?,'{}','now','now')''',
            (archive, cid, vid, 'v:'+str(vid), json.dumps({'invoice': invoice})))

    def test_ledger_posting_requires_an_active_voucher_in_same_company(self):
        ident = self.ledger();vid = self.post()
        self.conn.execute("UPDATE invoices SET status='posted',voucher_id=? WHERE id=?", (vid, ident))
        self.assertEqual(ticket_progress(self.conn, '2026-11')[0]['status'], 'posted')
        self.conn.execute("UPDATE vouchers SET status='void' WHERE id=?", (vid,))
        self.assertEqual(ticket_progress(self.conn, '2026-11')[0]['status'], 'unprocessed')
        foreign = self.post(2)
        self.conn.execute('UPDATE invoices SET voucher_id=? WHERE id=?', (foreign, ident))
        self.assertEqual(ticket_progress(self.conn, '2026-11')[0]['status'], 'unprocessed')

    def test_saved_reconciliation_and_voiding_use_invoice_month(self):
        rid = self.saved()
        self.assertEqual(ticket_progress(self.conn, '2026-11')[0]['status'], 'unprocessed')
        vid = self.post(source='reconciliation', source_id=rid, date='2026-10-30')
        row = ticket_progress(self.conn, '2026-11')[0]
        self.assertEqual((row['status'], row['origin'], row['voucher_period']), ('posted', 'reconciliation', '2026-10'))
        self.assertEqual(ticket_progress(self.conn, '2026-10'), [])
        self.conn.execute("UPDATE vouchers SET status='void' WHERE id=?", (vid,))
        self.assertEqual(ticket_progress(self.conn, '2026-11')[0]['status'], 'unprocessed')

    def test_metadata_links_are_validated(self):
        rid = self.saved();vid = self.post(source='bank_receipt')
        for linked, expected in [(vid, 'posted'), (self.post(2), 'unprocessed'), ('invalid', 'unprocessed')]:
            self.conn.execute('UPDATE reconciliation_records SET batch_metadata_json=? WHERE id=?',
                (json.dumps({'linked_voucher_id': linked}), rid))
            self.assertEqual(ticket_progress(self.conn, '2026-11')[0]['status'], expected)

    def test_same_invoice_across_sources_and_uploads_counts_once(self):
        self.archive('a', invoices=[self.invoice()]);self.archive('b', invoices=[self.invoice()])
        rid = self.saved();self.post(source='reconciliation', source_id=rid)
        self.ledger();self.ledger(2)
        rows = ticket_progress(self.conn, '2026-11')
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]['origin'], rows[0]['status']), ('invoice', 'posted'))
        self.assertEqual(rows[1]['status'], 'unprocessed')
        self.conn.execute("UPDATE invoices SET period='2026-10' WHERE client_id=1")
        self.assertEqual(len(ticket_progress(self.conn, '2026-11')), 1, 'explicit ledger month wins over raw recognition')

    def test_archives_are_pending_and_receipts_are_not_invoices(self):
        self.archive('receipt-only')
        self.assertEqual(ticket_progress(self.conn, '2026-11'), [])
        self.archive('raw', invoices=[self.invoice(), self.invoice('TEST-2')])
        self.archive('duplicate', invoices=[self.invoice()])
        rows = ticket_progress(self.conn, '2026-11')
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['status'] == 'unprocessed' and r['origin'] == 'archive' for r in rows))
        self.link('raw', self.invoice(), self.post())
        rows = ticket_progress(self.conn, '2026-11')
        self.assertEqual([r['status'] for r in rows], ['posted', 'unprocessed'], 'multi-invoice files retain individual posting state')

    def test_finalized_invoice_replaces_ocr_number_and_date(self):
        raw = self.invoice('OCR-OLD');raw['date'] = '2026-10-30'
        self.archive('raw', invoices=[raw])
        final = self.invoice('CORRECTED');vid = self.post()
        self.link('raw', final, vid)
        self.assertEqual(ticket_progress(self.conn, '2026-10'), [])
        row = ticket_progress(self.conn, '2026-11')[0]
        self.assertEqual((row['number'], row['status']), ('CORRECTED', 'posted'))
        self.conn.execute("UPDATE vouchers SET status='void' WHERE id=?", (vid,))
        self.assertEqual(ticket_progress(self.conn, '2026-11')[0]['status'], 'unprocessed')

    def test_bootstrap_is_readonly_excludes_recycled_but_keeps_disabled(self):
        self.saved();self.saved(2)
        self.conn.execute('UPDATE clients SET enabled=0 WHERE id=1')
        self.conn.execute('UPDATE clients SET deleted=1 WHERE id=2')
        self.conn.commit();writes = self.conn.total_changes
        with closing(db.connect(self.path)) as check:
            check.execute('PRAGMA query_only=ON')
            rows = business.bootstrap(check, '2026-11', False)['ticket_progress']
        self.assertEqual([r['client_id'] for r in rows], [1])
        self.assertEqual(self.conn.total_changes, writes)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM invoices').fetchone()[0], 0)

    def test_invalid_or_undated_sources_do_not_get_assigned_to_current_month(self):
        self.archive('invalid', invoices=[{'number': 'NO-DATE', 'amount': 'bad'}, 'bad'])
        self.assertEqual(ticket_progress(self.conn, '2026-11'), [])
        with self.assertRaises(db.ApiError):ticket_progress(self.conn, '2026-99')


if __name__ == '__main__':
    unittest.main()
