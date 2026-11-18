from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import db
import finance
import reconciliation
import reconciliation_batch as batch


BUYER = '合成采购有限公司'
SELLER = '合成销售有限公司'


class CorporateAdvanceTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.path = Path(self.folder.name) / 'test.sqlite3'
        db.initialize(self.path)
        self.conn = db.connect(self.path)
        self.user = {'id': 1, 'username': 'fixture', 'name': '测试员'}
        with self.conn:
            self.conn.execute("INSERT INTO users(id,username,name,password_hash) VALUES(1,'fixture','测试员','unused')")
            for cid, standard in ((1, 'small2013'), (2, 'enterprise2007')):
                self.conn.execute('''INSERT INTO clients(id,name,code,taxpayer_type,standard,start_period,created_at,updated_at)
                    VALUES(?,?,?,'general',?,'2026-01',?,?)''', (cid, SELLER, f'T{cid}', standard, db.now(), db.now()))
                finance.ensure_book(self.conn, self.conn.execute('SELECT * FROM clients WHERE id=?', (cid,)).fetchone())

    def tearDown(self):
        self.conn.close()
        self.folder.cleanup()

    def payment(self, date, buyer=BUYER, amount='2000.00'):
        return {'date': date, 'amount': amount, 'counterparty': buyer, 'note': 'IBPS对公提回借记'}

    def invoice(self, date, number='INV1', buyer=BUYER):
        return {'date': date, 'number': number, 'seller': SELLER, 'buyer': buyer,
                'amount': '2000.00', 'net_amount': '1900.00', 'tax_amount': '100.00'}

    def payload(self, payments=(), invoices=(), receipts=(), cid=1):
        return {'client_id': cid, 'account_name': SELLER, 'transactions': list(payments),
                'invoices': list(invoices), 'receipts': list(receipts)}

    def save(self, payload, invoice=False, index=0):
        preview = batch.preview(self.conn, payload)
        row = preview['invoice_rows' if invoice else 'rows'][index]
        self.assertEqual(row['status'], 'ready', row)
        selection = {'selected_invoice_index': index} if invoice else {'selected_indices': [index]}
        return batch.save_batch(self.conn, {**payload, **selection, 'confirmed': True,
                                'preview_token': preview['preview_token'], 'voucher': row['voucher']}, self.user)['results'][0]

    def test_receipt_first_uses_company_advance_then_invoice_offsets_it(self):
        payment = self.payment('2026-09-15')
        receipt = dict(payment, number='R1', own_name=SELLER)
        payload = self.payload([payment], receipts=[receipt])
        draft = batch.preview(self.conn, payload)['rows'][0]['voucher']
        self.assertEqual([line['summary'] for line in draft['lines']], ['银行存款', '预收账款'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], 0)
        receipt_post = self.save(payload)
        business = receipt_post['posted_lines'][1]
        self.assertEqual((business['account_name'], business['credit_fen']), (BUYER, 200000))
        self.assertEqual(batch.post_receipts(self.conn, payload, self.user)['rows'][0]['voucher_id'], receipt_post['voucher_id'])

        issued = self.payload(invoices=[self.invoice('2026-11-05')])
        invoice_post = self.save(issued, invoice=True)
        self.assertEqual(invoice_post['automatic_posting']['settlement']['voucher_id'], receipt_post['voucher_id'])
        self.assertEqual([(line['account_code'], line['debit_fen'], line['credit_fen']) for line in invoice_post['posted_lines']],
                         [(business['account_code'], 200000, 0), ('5001', 0, 190000), ('2221', 0, 10000)])
        self.assertEqual(batch.preview(self.conn, issued)['invoice_rows'][0]['voucher_id'], invoice_post['voucher_id'])
        history = reconciliation.records(self.conn, 1)['postings']
        self.assertEqual(history[0]['invoice']['number'], 'INV1')
        self.assertIsNone(history[0]['transaction'])
        next_invoice = self.payload(invoices=[self.invoice('2026-11-10', 'INV2')])
        self.assertEqual(batch.preview(self.conn, next_invoice)['invoice_rows'][0]['voucher']['lines'][0]['summary'], '预付账款')

    def test_invoice_first_offsets_prepayment_for_the_same_book_and_company(self):
        posted_invoice = self.save(self.payload(invoices=[self.invoice('2026-08-05')], cid=2), invoice=True)
        prepaid = posted_invoice['posted_lines'][0]
        self.assertEqual((prepaid['summary'], prepaid['account_name'], prepaid['debit_fen']), ('预付账款', BUYER, 200000))
        self.assertEqual(posted_invoice['posted_lines'][1]['account_code'], '6001')
        for payload in (self.payload([self.payment('2026-10-05')]),
                        self.payload([self.payment('2026-10-05', buyer='另一采购公司')], cid=2)):
            self.assertEqual(batch.preview(self.conn, payload)['rows'][0]['voucher']['lines'][1]['summary'], '预收账款')
        actual_payment = self.payload([self.payment('2026-10-05')], cid=2)
        posted_payment = self.save(actual_payment)
        self.assertEqual(posted_payment['automatic_posting']['settlement']['voucher_id'], posted_invoice['voucher_id'])
        self.assertEqual((posted_payment['posted_lines'][1]['account_code'], posted_payment['posted_lines'][1]['credit_fen']),
                         (prepaid['account_code'], 200000))
        self.assertEqual(batch.preview(self.conn, actual_payment)['rows'][0]['voucher_id'], posted_payment['voucher_id'])

    def test_both_documents_use_their_own_dates_and_post_earlier_document_first(self):
        for index, (paid, billed) in enumerate((('2026-06-05','2026-07-12'), ('2026-10-05','2026-09-12'))):
            buyer = f'合成测试{index}公司'
            payload = self.payload([self.payment(paid, buyer)], [self.invoice(billed, f'PAIR{index}', buyer)])
            draft = batch.preview(self.conn, payload)
            self.assertEqual((draft['rows'][0]['status'], draft['invoice_rows'][0]['status']),
                             ('ready', 'review') if paid<billed else ('review', 'ready'))
            first = self.save(payload, invoice=billed<paid)
            second = self.save(payload, invoice=paid<billed)
            self.assertEqual((first['voucher_date'], second['voucher_date']), tuple(sorted((paid, billed))))
            self.assertEqual(second['automatic_posting']['settlement']['voucher_id'], first['voucher_id'])

    def test_ambiguous_history_and_stale_confirmation_leave_ledger_intact(self):
        self.save(self.payload([self.payment('2026-08-05')]))
        issued = self.payload(invoices=[self.invoice('2026-10-05')])
        draft = batch.preview(self.conn, issued)
        self.save(self.payload([self.payment('2026-09-05')]))
        self.assertIn('无法唯一', batch.preview(self.conn, issued)['invoice_rows'][0]['reason'])
        with self.assertRaisesRegex(db.ApiError, '重新核对'):
            batch.save_batch(self.conn, {**issued, 'selected_invoice_index': 0, 'confirmed': True,
                             'preview_token': draft['preview_token'], 'voucher': draft['invoice_rows'][0]['voucher']}, self.user)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], 2)

    def test_confirmed_bulk_pair_records_each_source_once(self):
        payload = self.payload([self.payment('2026-11-05')], [self.invoice('2026-10-05')])
        outcome = batch.auto_post(self.conn, payload, self.user)
        row = outcome['rows'][0]
        self.assertEqual(row['status'], 'posted', row)
        self.assertEqual(outcome['periods'], ['2026-10','2026-11'])
        self.assertEqual(row['automatic_posting']['settlement']['voucher_id'], row['invoice_posting']['voucher_id'])
        again = batch.auto_post(self.conn, payload, self.user)['rows'][0]
        self.assertEqual(again['voucher_id'], row['voucher_id'])
        self.assertEqual(again['invoice_posting']['voucher_id'], row['invoice_posting']['voucher_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], 2)

    def test_supports_legacy_opening_and_excludes_supplier_prepayments_and_personal_payments(self):
        with finance._write(self.conn):
            advance = finance._create_account(self.conn, 1, {'parent_code':'2203','name':BUYER}, self.user)['code']
            finance._opening(self.conn, 1, {'version':1,'balances':[finance._entry('1002', 200000),
                            finance._entry(advance, credit=200000)]}, self.user)
            finance._insert_voucher(self.conn, 1, '2026-08-05', '预付供应商货款',
                                    [finance._entry('1123', 200000),finance._entry('1002', credit=200000)],
                                    self.user, counterparty=BUYER)
        issued = self.payload(invoices=[self.invoice('2026-09-05')])
        invoice_draft = batch.preview(self.conn, issued)['invoice_rows'][0]
        self.assertEqual(invoice_draft['voucher']['lines'][0]['account_code'], advance)
        self.assertIn('期初余额', invoice_draft['basis'][0])
        bank_draft = batch.preview(self.conn, self.payload([self.payment('2026-09-05')]))['rows'][0]
        self.assertEqual(bank_draft['voucher']['lines'][1]['summary'], '预收账款')

        person = self.invoice('2026-10-05', buyer='张三')
        purchase = dict(self.invoice('2026-10-05', 'P1'), seller=BUYER, buyer=SELLER)
        preview = batch.preview(self.conn, self.payload([self.payment('2026-10-05','张三'), self.payment('2026-10-05',SELLER)],
                                                       [person,purchase]))
        self.assertEqual(preview['invoice_rows'], [])
        self.assertTrue(all(row.get('document')!='company_statement' for row in preview['rows']))
        self.assertTrue(preview['rows'][0]['matched'])


if __name__ == '__main__':
    unittest.main()
