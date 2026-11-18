"""Agency bookkeeping progress uses actual monthly balance data, without writes."""

from pathlib import Path
import tempfile
import unittest

import business
import db
import finance
from balance_progress import balance_progress


class BalanceProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'progress.sqlite3'
        db.initialize(self.path)
        self.conn = db.connect(self.path)
        self.user = {'id': 1, 'username': 'test', 'name': '测试员'}
        with self.conn:
            self.conn.execute("INSERT INTO users(id,username,name,password_hash) VALUES(1,'test','测试员','unused')")
            for cid in (1, 2):
                business.handle(self.conn, 'POST', '/api/clients', {
                    'name': f'余额进度测试{cid}', 'code': f'BP{cid}', 'taxpayer_type': 'small',
                    'standard': 'small2013', 'start_period': '2026-01',
                }, self.user, {})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def progress(self, period='2026-11', cid=1):
        return next(row for row in balance_progress(self.conn, period) if row['client_id'] == cid)

    def post(self, date='2026-11-05', debit='1002', credit='2241', amount=10000, cid=1):
        with self.conn:
            return finance._insert_voucher(self.conn, cid, date, '余额进度测试', [
                finance._entry(debit, amount), finance._entry(credit, credit=amount),
            ], self.user)

    def assert_matches_balance(self, period, cid=1):
        report = finance.balance_bundle(self.conn, cid, period, period)
        rows = report['posted_trial_balance_rollup']
        keys = finance._BALANCE_AMOUNT_KEYS[:6]
        has_amount = any(row[key] != 0 for row in rows for key in keys)
        has_current_source = any(
            row['source_type'] == 'manual' and row['period_bucket'] == 'current'
            and (row['debit_fen'] != 0 or row['credit_fen'] != 0)
            for row in report['balance_details'])
        self.assertEqual(self.progress(period, cid)['has_data'], has_amount or has_current_source)

    def test_empty_accounts_zero_opening_and_closed_empty_month_stay_empty(self):
        with self.conn:
            self.conn.execute("INSERT INTO opening_balances(client_id,account_code) VALUES(1,'1002')")
            self.conn.execute("INSERT INTO book_periods(client_id,period,closed) VALUES(1,'2026-11',1)")
        self.assertFalse(self.progress()['has_data'])
        self.assertEqual(self.progress()['account_count'], 0)
        self.assert_matches_balance('2026-11')

    def test_opening_only_counts_from_its_effective_month_and_carries_forward(self):
        with self.conn:
            finance._opening(self.conn, 1, {'version': 1, 'balances': [
                finance._entry('1002', 10000), finance._entry('3001', credit=10000),
            ]}, self.user)
            self.conn.execute("UPDATE book_settings SET opening_period='2026-10' WHERE client_id=1")
        self.assertFalse(self.progress('2026-09')['has_data'])
        for period in ('2026-10', '2026-11', '2027-01'):
            self.assertTrue(self.progress(period)['has_data'])
            self.assertEqual(self.progress(period)['account_count'], 2)
            self.assert_matches_balance(period)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], 0)
        self.assertFalse(self.progress(cid=2)['has_data'])

    def test_previous_balance_counts_without_current_vouchers_but_future_records_do_not(self):
        self.post('2026-10-05')
        self.post('2026-12-05', cid=2)
        self.assertFalse(self.progress('2026-09')['has_data'])
        self.assertTrue(self.progress()['has_data'])
        self.assertTrue(self.progress('2027-01')['has_data'])
        self.assertFalse(self.progress(cid=2)['has_data'])
        for period in ('2026-09', '2026-10', '2026-11', '2027-01'):
            self.assert_matches_balance(period)

    def test_zero_closing_with_current_activity_counts_then_empty_later_month_does_not(self):
        self.post('2026-10-05')
        self.post('2026-10-06', debit='2241', credit='1002')
        self.assertTrue(self.progress('2026-10')['has_data'])
        self.assertFalse(self.progress('2026-11')['has_data'])
        self.assert_matches_balance('2026-10')
        self.assert_matches_balance('2026-11')

    def test_red_letter_cancellation_is_still_visible_current_activity(self):
        self.post(amount=10000)
        self.post(amount=-10000)
        self.assertTrue(self.progress()['has_data'])
        self.assertFalse(self.progress('2026-12')['has_data'])
        self.assert_matches_balance('2026-11')
        self.assert_matches_balance('2026-12')

    def test_saving_and_voiding_last_voucher_recalculates_without_stored_progress(self):
        self.assertFalse(self.progress()['has_data'])
        vid = self.post()
        self.assertTrue(self.progress()['has_data'])
        with self.conn:
            self.conn.execute("UPDATE vouchers SET status='void' WHERE id=?", (vid,))
        self.assertFalse(self.progress()['has_data'])
        self.assert_matches_balance('2026-11')

    def test_bootstrap_has_all_company_results_and_does_not_change_accounting_data(self):
        self.post(cid=1)
        with self.conn:
            self.conn.execute('UPDATE clients SET enabled=0 WHERE id=1')
        before = self.conn.total_changes
        data = business.bootstrap(self.conn, '2026-11', False)
        self.assertEqual(data['balance_progress'], [
            {'client_id': 1, 'period': '2026-11', 'has_data': True, 'account_count': 2},
            {'client_id': 2, 'period': '2026-11', 'has_data': False, 'account_count': 0},
        ])
        self.assertEqual(self.conn.total_changes, before)
        with self.conn:
            self.conn.execute('UPDATE clients SET deleted=1 WHERE id=1')
        self.assertEqual([row['client_id'] for row in balance_progress(self.conn, '2026-11')], [2])

    def test_invalid_period_is_rejected(self):
        for period in ('2026-13', '2026-00', '', None):
            with self.assertRaises(db.ApiError):
                balance_progress(self.conn, period)


if __name__ == '__main__':
    unittest.main()
