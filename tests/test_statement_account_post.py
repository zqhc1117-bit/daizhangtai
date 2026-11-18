"""Confirmed posting for unmatched statement lines, isolated from production data."""
import os, sys, tempfile, threading, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import db, finance, reconciliation, server
from test_http import Client


class StatementAccountPostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'book.sqlite3')
        db.initialize(self.path)
        self.conn = db.connect(self.path)
        self.user = {'id': 1, 'username': 'tester', 'name': '测试员'}
        with self.conn:
            self.conn.execute("INSERT INTO users(id,username,name,password_hash) VALUES(1,'tester','测试员','unused')")
            self.conn.execute("INSERT INTO clients(id,name,code,taxpayer_type,standard,start_period,created_at,updated_at) VALUES(1,'合成企业','T1','small','small2013','2026-01',?,?)", (db.now(), db.now()))
            self.conn.execute("INSERT INTO clients(id,name,code,taxpayer_type,standard,start_period,created_at,updated_at) VALUES(2,'企业准则公司','T2','general','enterprise2007','2026-01',?,?)", (db.now(), db.now()))
            for cid in (1, 2):
                finance.ensure_book(self.conn, dict(self.conn.execute('SELECT * FROM clients WHERE id=?', (cid,)).fetchone()))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def interest(self, **changes):
        row = {'date': '2026-09-21', 'amount': '0.04', 'counterparty': '应付利息-单位活期存款利息-自动计提', 'note': '应付利息-单位活期存款利息-自动计提', 'source': '流水'}
        row.update(changes)
        return row

    def post(self, **changes):
        payload = {'client_id': 1, 'account_name': '合成企业', 'confirmed': True, 'mode': 'recommended',
                   'business_account_code': '5603', 'bank_account_code': '1002', 'transaction': self.interest()}
        payload.update(changes)
        return reconciliation.post_unmatched_statement(self.conn, payload, self.user)

    def lines(self, voucher_id):
        rows = self.conn.execute('SELECT account_code,debit_fen,credit_fen FROM voucher_lines WHERE voucher_id=? ORDER BY id', (voucher_id,)).fetchall()
        return [(row['account_code'], row['debit_fen'], row['credit_fen']) for row in rows]

    def test_recommendation_posts_only_after_confirmation_and_updates_balance(self):
        with self.assertRaises(db.ApiError) as rejected:
            self.post(confirmed=False)
        self.assertIn('不会自动写入', rejected.exception.message)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], 0)
        result = self.post()
        self.assertEqual(result['status'], 'posted')
        self.assertFalse(result['already_posted'])
        self.assertEqual(self.lines(result['voucher_id']), [('1002', 4, 0), ('5603', 0, 4)])
        trial = {row['account_code']: row for row in finance.balance_bundle(self.conn, 1, '2026-09', '2026-09')['trial_balance']}
        self.assertEqual(trial['1002']['debit_fen'], 4)
        self.assertEqual(trial['5603']['credit_fen'], 4)
        history = reconciliation.records(self.conn, 1)
        self.assertEqual(history['postings'][0]['source_type'], 'bank_statement')
        self.assertEqual(history['postings'][0]['transaction']['amount'], '0.04')
        self.assertEqual(history['postings'][0]['transaction_source'], 'audit')

    def test_manual_account_is_not_replaced_by_recommendation(self):
        result = self.post(mode='manual', business_account_code='5602')
        self.assertEqual(self.lines(result['voucher_id']), [('1002', 4, 0), ('5602', 0, 4)])
        trial = {row['account_code']: row for row in finance.balance_bundle(self.conn, 1, '2026-09', '2026-09')['trial_balance']}
        self.assertEqual(trial['5602']['credit_fen'], 4)
        self.assertEqual(trial['5603']['credit_fen'], 0)
        again = self.post(mode='manual', business_account_code='5711')
        self.assertTrue(again['already_posted'])
        self.assertEqual(again['voucher_id'], result['voucher_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], 1)
        self.assertEqual(trial['5602']['credit_fen'], 4)

    def test_wrong_recommendation_parent_direction_period_and_duplicate_are_rejected(self):
        with self.assertRaises(db.ApiError) as wrong:
            self.post(business_account_code='5602')
        self.assertIn('5603', wrong.exception.message)
        with finance._write(self.conn):
            finance._create_account(self.conn, 1, {'parent_code': '5603', 'name': '利息收入'}, self.user)
        with self.assertRaises(db.ApiError) as parent:
            self.post()
        self.assertIn('末级科目', parent.exception.message)
        with self.assertRaises(db.ApiError):
            self.post(mode='manual', business_account_code='5603')
        child = self.conn.execute("SELECT code FROM accounts WHERE client_id=1 AND name='利息收入'").fetchone()['code']
        posted = self.post(mode='manual', business_account_code=child, transaction=self.interest(note='另一笔利息', amount='0.05'))
        self.assertEqual(self.lines(posted['voucher_id'])[1][0], child)
        before = self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0]
        with self.assertRaises(db.ApiError) as duplicate:
            self.post(mode='manual', business_account_code=child, transaction=self.interest(note='同额另一笔利息', amount='0.05'))
        self.assertEqual(duplicate.exception.details['code'], 'possible_duplicate')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], before)
        noted = self.post(mode='manual', business_account_code=child, transaction=self.interest(note='同额另一笔利息', amount='0.05'), duplicate_review_note='已核对本金不同')
        self.assertFalse(noted['already_posted'])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM vouchers WHERE status='active'").fetchone()[0], before + 1)
        with self.assertRaises(db.ApiError) as direction:
            self.post(transaction=self.interest(direction_needs_review=True, note='方向待定利息'))
        self.assertIn('收付款方向', direction.exception.message)
        with self.conn:
            self.conn.execute("INSERT INTO book_periods(client_id,period,closed) VALUES(1,'2026-10',1)")
        with self.assertRaises(db.ApiError) as closed:
            self.post(transaction=self.interest(date='2026-10-02', note='结账后利息'))
        self.assertIn('已结账', closed.exception.message)

    def test_funding_account_must_be_explicit_when_several_exist(self):
        with self.assertRaises(db.ApiError) as missing:
            self.post(bank_account_code='')
        self.assertIn('多个资金科目', missing.exception.message)
        result = self.post(bank_account_code='1001')
        self.assertEqual(self.lines(result['voucher_id'])[0][0], '1001')
        with self.assertRaises(db.ApiError):
            self.post(mode='manual', business_account_code='1002', transaction=self.interest(note='不能用资金科目'))

    def test_enterprise_recommendation_uses_the_book_name_not_the_small_code(self):
        with self.assertRaises(db.ApiError) as rejected:
            self.post(client_id=2, account_name='企业准则公司', business_account_code='5603')
        self.assertIn('6603', rejected.exception.message)
        result = self.post(client_id=2, account_name='企业准则公司', business_account_code='6603')
        self.assertEqual(self.lines(result['voucher_id']), [('1002', 4, 0), ('6603', 0, 4)])

    def test_matched_invoice_or_receipt_is_not_posted_again(self):
        transaction = {'date': '2026-09-21', 'amount': '-100.00', 'counterparty': '合成供应商', 'note': '服务费', 'source': '流水'}
        invoice = {'date': '2026-09-21', 'amount': '100.00', 'seller': '合成供应商', 'buyer': '合成企业', 'number': 'F1', 'source': '发票'}
        with self.assertRaises(db.ApiError) as matched:
            self.post(mode='manual', business_account_code='5602', transaction=transaction, transactions=[transaction], invoices=[invoice], transaction_index=0)
        self.assertIn('发票', matched.exception.message)
        receipt = dict(transaction, number='R1', own_name='合成企业')
        with self.assertRaises(db.ApiError) as receipt_match:
            self.post(mode='manual', business_account_code='5602', transaction=transaction, transactions=[transaction], invoices=[], receipts=[receipt], transaction_index=0)
        self.assertIn('回单', receipt_match.exception.message)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0], 0)

    def trial(self):
        return {row['account_code']: row for row in finance.balance_bundle(self.conn, 1, '2026-09', '2026-09')['trial_balance']}

    def test_statement_withdraw_clears_balance_and_allows_repost(self):
        posted = self.post(mode='manual', business_account_code='5602')
        vid = posted['voucher_id']
        voucher = finance._voucher(self.conn, 1, vid)
        self.assertEqual(voucher['source_type'], 'bank_statement')
        self.assertEqual(self.trial()['5602']['credit_fen'], 4)
        with self.assertRaises(db.ApiError) as stale:
            finance.withdraw_source(self.conn, 1, vid, self.user, '版本过期', voucher['version'] + 1)
        self.assertIn('请刷新', stale.exception.message)
        with self.conn:
            self.conn.execute('UPDATE vouchers SET reviewed=1 WHERE id=?', (vid,))
        with self.assertRaises(db.ApiError) as reviewed:
            finance.withdraw_source(self.conn, 1, vid, self.user, '已审核不能撤回', voucher['version'])
        self.assertIn('取消凭证审核', reviewed.exception.message)
        with self.conn:
            self.conn.execute('UPDATE vouchers SET reviewed=0 WHERE id=?', (vid,))
            self.conn.execute("UPDATE book_periods SET closed=1 WHERE client_id=1 AND period='2026-09'")
        with self.assertRaises(db.ApiError) as closed:
            finance.withdraw_source(self.conn, 1, vid, self.user, '已结账不能撤回', voucher['version'])
        self.assertIn('已结账', closed.exception.message)
        with self.assertRaises(db.ApiError) as other_book:
            finance.withdraw_source(self.conn, 2, vid, self.user, '跨账套', voucher['version'])
        self.assertEqual(other_book.exception.status, 404)
        with self.conn:
            self.conn.execute("UPDATE book_periods SET closed=0 WHERE client_id=1 AND period='2026-09'")
            removed = finance.delete_balance_entry(self.conn, 1, {'voucher_id': vid, 'reason': '科目选错'}, self.user)
        self.assertEqual(removed['status'], 'void')
        self.assertEqual(self.conn.execute('SELECT status FROM vouchers WHERE id=?', (vid,)).fetchone()[0], 'void')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM voucher_lines WHERE voucher_id=?', (vid,)).fetchone()[0], 2)
        self.assertEqual(self.trial()['5602']['credit_fen'], 0)
        self.assertEqual(self.trial()['1002']['debit_fen'], 0)
        history = reconciliation.records(self.conn, 1)
        self.assertFalse(any(item['voucher_id'] == vid for item in history['postings']))
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM audit_logs WHERE action='withdraw_source' AND entity='voucher' AND entity_id=?", (str(vid),)).fetchone())
        again = self.post(mode='manual', business_account_code='5603')
        self.assertFalse(again['already_posted'])
        self.assertNotEqual(again['voucher_id'], vid)
        self.assertEqual(self.lines(again['voucher_id']), [('1002', 4, 0), ('5603', 0, 4)])
        repeat = self.post(mode='manual', business_account_code='5602')
        self.assertTrue(repeat['already_posted'])
        self.assertEqual(repeat['voucher_id'], again['voucher_id'])
        self.assertEqual(self.lines(again['voucher_id'])[1][0], '5603')
        current = finance._voucher(self.conn, 1, again['voucher_id'])
        token = finance._balance_delete_token('voucher', current)
        with self.conn:
            batch = finance.delete_balance_entries(self.conn, 1, {'reason': '批量撤回流水', 'entries': [{'voucher_id': again['voucher_id'], 'delete_token': token}]}, self.user)
        self.assertEqual(batch['deleted_count'], 1)
        self.assertEqual(self.conn.execute('SELECT status FROM vouchers WHERE id=?', (again['voucher_id'],)).fetchone()[0], 'void')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM vouchers WHERE source_type='bank_statement' AND status='active'").fetchone()[0], 0)
        self.assertEqual(self.trial()['5603']['credit_fen'], 0)


class StatementAccountHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ydz-statement-post-')
        self.app = server.Application(self.temp.name)
        self.http = server.make_server('127.0.0.1', 0, self.app)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.client = Client(f'http://127.0.0.1:{self.http.server_address[1]}')
        users = [{'username': f'statement{i}', 'name': f'流水测试员{i}', 'password': f'StatementOnly-{i}-Strong!'} for i in range(1, 4)]
        code, body, _ = self.client.call('/api/setup', {'company_name': '合成测试机构', 'users': users}, headers={'X-Setup-Token': self.app.setup_token})
        self.assertEqual(code, 200, body)
        code, body, _ = self.client.call('/api/clients', {'name': '合成客户公司', 'code': 'STMT-1', 'taxpayer_type': 'general', 'standard': 'small2013', 'start_period': '2026-01', 'industry': '商务服务', 'region': '本地', 'accountant_id': 1, 'enabled': True})
        self.assertEqual(code, 200, body)
        self.cid = body['id']

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_http_confirmation_posts_once_and_rejects_missing_csrf(self):
        payload = {'client_id': self.cid, 'account_name': '合成客户公司', 'confirmed': True, 'mode': 'recommended',
                   'business_account_code': '5603', 'bank_account_code': '1002',
                   'transaction': {'date': '2026-09-21', 'amount': '0.04', 'counterparty': '应付利息', 'note': '单位活期存款利息', 'source': '流水'}}
        self.assertEqual(self.client.call('/api/reconcile/statement-post', payload, csrf=False)[0], 403)
        code, posted, _ = self.client.call('/api/reconcile/statement-post', payload)
        self.assertEqual(code, 200, posted)
        self.assertGreater(posted['voucher_id'], 0)
        self.assertEqual([(line['account_code'], line['debit_fen'], line['credit_fen']) for line in posted['posted_lines']], [('1002', 4, 0), ('5603', 0, 4)])
        code, again, _ = self.client.call('/api/reconcile/statement-post', dict(payload, mode='manual', business_account_code='5602'))
        self.assertEqual(code, 200, again)
        self.assertTrue(again['already_posted'])
        self.assertEqual(again['voucher_id'], posted['voucher_id'])
        code, balance, _ = self.client.call(f'/api/books/{self.cid}?period=2026-09')
        self.assertEqual(code, 200, balance)
        trial = {row['account_code']: row for row in balance['trial_balance']}
        self.assertEqual(trial['5603']['credit_fen'], 4)
        self.assertEqual(trial['1002']['debit_fen'], 4)


if __name__ == '__main__':
    unittest.main()
