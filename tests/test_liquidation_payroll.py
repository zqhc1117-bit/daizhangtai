import base64
import tempfile
import unittest
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest.mock import patch

import db
import finance
import liquidation_payroll as payroll
import source_archive


class LiquidationPayrollTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        self.app = SimpleNamespace(db_path=directory / 'test.sqlite3', files_dir=directory / 'files', lock=Lock())
        self.app.files_dir.mkdir()
        db.initialize(self.app.db_path)
        self.conn = db.connect(self.app.db_path)
        self.addCleanup(self.conn.close)
        self.user = {'id': 1, 'username': 'tester', 'name': '测试员'}
        with self.conn:
            self.conn.execute("INSERT INTO users(id,username,name,password_hash) VALUES(1,'tester','测试员','unused')")
            for cid, name, standard in ((1, '本方公司', 'small2013'), (2, '第二账套', 'enterprise2007')):
                self.conn.execute('''INSERT INTO clients(id,name,code,taxpayer_type,standard,start_period,created_at,updated_at)
                    VALUES(?,?,?,'small',?,'2026-08',?,?)''', (cid, name, str(cid), standard, db.now(), db.now()))
                finance.ensure_book(self.conn, dict(self.conn.execute('SELECT * FROM clients WHERE id=?', (cid,)).fetchone()))

    def archive(self, transactions, receipts, *, cid=1, name='本方公司', receipt_only=False):
        items = sorted({row['source'] for row in transactions + receipts})
        files = [{'name': filename, 'content_base64': base64.b64encode(filename.encode()).decode()} for filename in items]
        parsed = {'transactions': transactions, 'receipts': receipts, 'warnings': [], 'account_names': [name],
                  'account_name': name, 'transactions_from_receipts': receipt_only}
        result = source_archive.capture(self.app, {'client_id': cid, 'statements': files}, parsed, self.user)
        return {'client_id': cid, 'archive_id': result['archive_id'], 'bank_account_code': '1002'}

    def transaction(self, day, amount, party, note, **fields):
        return {'date': f'2026-09-{day:02d}', 'amount': amount, 'counterparty': party, 'note': note,
                'source': '流水.pdf'} | fields

    def receipt(self, transaction, number, **fields):
        return dict(transaction, number=number, source='回单.pdf', own_name='本方公司') | fields

    def preview(self, payload):
        return {row['id']: row for row in payroll.process(self.conn, payload, self.user)['rows']}

    def commit(self, payload, *keys):
        return payroll.process(self.conn, dict(payload, confirmed=True, row_ids=list(keys)), self.user, commit=True)['rows']

    def test_mixed_sources_post_each_actual_payment_once(self):
        wage = self.transaction(1, '-3200.50', '张三', '代发工 资')
        social = self.transaction(2, '-977.32', '国家金库某市支库', '税务扣款')
        housing = self.transaction(3, '-450.10', '住房公积金管理中心', '委托收款')
        tax = self.transaction(4, '-150', '国家金库某市支库', '增值税')
        receipt = self.receipt(social, 'R1', note='电子缴款', purpose='缴纳社会保险费')
        payload = self.archive([wage, social, tax], [receipt, self.receipt(housing, 'R2')])
        preview = self.preview(payload)
        self.assertEqual([(row['category'], row['expense_fen']) for row in preview.values()],
                         [('工资', 320050), ('社保', 97732), ('公积金', 45010)])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], 0)
        self.assertFalse(self.conn.execute("SELECT 1 FROM accounts WHERE code LIKE '5602_%'").fetchone())
        with self.assertRaises(db.ApiError):
            payroll.process(self.conn, dict(payload, row_ids=list(preview)), self.user, commit=True)
        self.assertEqual(self.commit(payload, 'statement:2'), [])  # unrelated tax cannot be forced through the API
        posted = self.commit(payload, *preview)
        self.assertEqual([row['status'] for row in posted], ['posted'] * 3)
        for row in posted:
            expense, bank = row['posted_lines']
            self.assertEqual((expense['account_name'], expense['debit_fen']), (row['category'], -row['amount_fen']))
            self.assertEqual((bank['account_code'], bank['credit_fen']), ('1002', -row['amount_fen']))
            self.assertTrue(expense['account_code'].startswith('5602'))
        retry = self.commit(payload, *preview)
        self.assertEqual([row['voucher_id'] for row in retry], [row['voucher_id'] for row in posted])
        self.assertTrue(all(row['already_posted'] for row in retry))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM source_archive_links').fetchone()[0], 3)
        self.assertEqual(self.conn.execute('SELECT attachment_count FROM vouchers WHERE id=?',
                                           (posted[1]['voucher_id'],)).fetchone()[0], 2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], 3)

    def test_numbered_receipts_keep_distinct_equal_amount_payments(self):
        payment = self.transaction(1, '-3200', '付款清算账户', '职工薪酬')
        receipts = [self.receipt(payment, number) for number in ('001', '002')]
        payload = self.archive([], receipts, receipt_only=True)
        posted = self.commit(payload, *self.preview(payload))
        self.assertEqual([row['status'] for row in posted], ['posted', 'posted'])
        self.assertNotEqual(posted[0]['voucher_id'], posted[1]['voucher_id'])
        reimported = self.archive([], [receipts[1]], receipt_only=True)
        self.assertEqual(self.preview(reimported)['receipt:0']['voucher_id'], posted[1]['voucher_id'])

    def test_reviews_treasury_evidence_mixed_amounts_refunds_and_direction(self):
        rows = [
            self.transaction(1, '-300', '社会保险中心', '社保费'),
            self.transaction(2, '-400', '国家金库某市支库', '社保和公积金'),
            self.transaction(3, '50', '员工', '工资退回'),
            self.transaction(4, '-440', '收款单位', '工资', direction_needs_review=True),
            self.transaction(5, '-60', '国家金库某市支库', '工资薪金个人所得税'),
        ]
        payload = self.archive(rows, [])
        preview = self.preview(payload)
        self.assertEqual([row['status'] for row in preview.values()], ['review'] * len(rows))
        for row, reason in zip(preview.values(), ('国库', '多类', '退款', '方向', '个税')):
            self.assertIn(reason, row['reason'])
        self.assertEqual([row['status'] for row in self.commit(payload, *preview)], ['review'] * len(rows))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], 0)

    def test_uses_existing_detail_and_bank_in_enterprise_chart(self):
        with self.conn:
            detail = finance._create_account(self.conn, 2, {'parent_code': '6602', 'name': '住房公积金（支出）'}, self.user)
            bank = finance._create_account(self.conn, 2, {'parent_code': '1002', 'name': '付款银行'}, self.user)
        payload = self.archive([self.transaction(3, '-540', '住房公积金中心', '缴存')], [], cid=2, name='第二账套')
        self.assertEqual(payroll.bank_accounts(self.conn, 2)['accounts'], [{'code': bank['code'], 'name': '付款银行'}])
        self.assertEqual(self.preview(payload)['statement:0']['status'], 'review')
        payload['bank_account_code'] = bank['code']
        self.assertEqual(self.preview(payload)['statement:0']['expense_line']['account_code'], detail['code'])
        posted, = self.commit(payload, 'statement:0')
        self.assertEqual([line['account_code'] for line in posted['posted_lines']], [detail['code'], bank['code']])
        with self.assertRaises(db.ApiError):
            self.preview(dict(payload, client_id=1))

    def test_reviews_possible_duplicates_and_unrelated_holder(self):
        wage = self.transaction(1, '-3200', '张三', '代发工资')
        first, = self.commit(self.archive([wage], []), 'statement:0')
        self.assertEqual(first['status'], 'posted')
        receipt = self.receipt(wage, '001', note='职工薪酬')
        review = self.preview(self.archive([], [receipt], receipt_only=True))['receipt:0']
        self.assertEqual(review['status'], 'review')
        self.assertIn('同日同金额', review['reason'])
        payment = self.transaction(2, '-3800', '李四', '代发工资')
        review = self.preview(self.archive([payment], [], name='另一公司'))['statement:0']
        self.assertIn('户名', review['reason'])
        ambiguous = self.preview(self.archive([payment, payment], []))
        self.assertTrue(all(row['status'] == 'review' for row in ambiguous.values()))

    def test_failed_attachment_rolls_back_expense_account_and_voucher(self):
        payload = self.archive([self.transaction(4, '-100', '张三', '工资')], [])
        with patch.object(source_archive, 'attach', side_effect=db.ApiError(409, '归档错误')):
            row, = self.commit(payload, 'statement:0')
        self.assertEqual(row['status'], 'review')
        self.assertFalse(self.conn.execute("SELECT 1 FROM accounts WHERE code LIKE '5602_%'").fetchone())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
