"""Synthetic regression cases for official-source alignment; no client data."""
import sqlite3
import unittest

import db
import finance
import reconciliation
from tests import test_finance as fixtures


class OfficialAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FinanceTest('runTest'); self.fixture.setUp()
        self.conn=self.fixture.conn

    def tearDown(self):
        self.fixture.tearDown()

    def test_tax_recommendation_is_standard_and_semantics_bound(self):
        transaction={'date':'2026-01-17','amount':'-100.00','note':'缴纳印花税','counterparty':'税务机关'}
        for cid,code in ((1,'5403'),(2,'6403')):
            for name in ('税金及附加','营业税金及附加'):
                self.conn.execute('UPDATE accounts SET name=? WHERE client_id=? AND code=?',(name,cid,code))
                account,_=reconciliation._recommended_business_account(self.conn,cid,transaction)
                self.assertEqual((account['code'],account['category'],account['report_key']),(code,'expense','taxes'))
        self.conn.execute("UPDATE accounts SET name='自定义费用' WHERE client_id=2 AND code='6403'")
        with self.assertRaises(db.ApiError): reconciliation._recommended_business_account(self.conn,2,transaction)
        self.assertFalse(finance.account_name_matches(self.conn,2,'5403','机械作业','税金及附加'))

    def test_aliases_do_not_strip_parenthetical_company_names(self):
        self.assertFalse(finance.account_name_matches(self.conn,1,'3001','甲','实收资本（甲）'))
        self.conn.execute("INSERT INTO account_name_aliases VALUES(1,'3001','实收资本（甲）','甲','synthetic')")
        self.assertTrue(finance.account_name_matches(self.conn,1,'3001','甲','实收资本（甲）'))
        self.assertFalse(finance.account_name_matches(self.conn,2,'3001','甲','实收资本（甲）'))
        self.assertFalse(finance.account_name_matches(self.conn,1,'3001','乙','实收资本（甲）'))

    def test_red_voucher_round_trip_gross_amounts_and_net(self):
        self.fixture.voucher(10000)
        red=self.fixture.voucher(-2500)
        self.assertEqual((red['lines'][0]['debit_fen'],red['lines'][0]['credit_fen']),(-2500,0))
        stored=self.conn.execute('SELECT * FROM voucher_lines WHERE id=?',(red['lines'][0]['id'],)).fetchone()
        self.assertEqual((stored['debit_fen'],stored['credit_fen']),(0,2500))
        actual=finance.balance_bundle(self.conn,1,'2026-01','2026-01')
        bank=next(r for r in actual['posted_trial_balance_rollup'] if r['account_code']=='1002')
        self.assertEqual((bank['debit_fen'],bank['credit_fen'],bank['closing_debit_fen']),(7500,0,7500))
        self.assertEqual(sum(r['debit_fen'] for r in actual['balance_details'] if r['account_code']=='1002'),7500)
        self.assertEqual(self.fixture.call(method='GET')['reports']['profit_loss']['profit_fen'],7500)
        exported=finance.export_rows(self.conn,1,'vouchers','2026-01')[1]
        self.assertTrue(any('-25.00' in str(value) for row in exported for value in row))
        changed=self.fixture.call('vouchers/'+str(red['id']),{'version':red['version'],'date':red['date'],'summary':'红字更正','lines':red['lines']},method='PATCH')
        self.assertEqual(changed['lines'][0]['debit_fen'],-2500)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute('UPDATE voucher_lines SET original_debit_fen=-999 WHERE id=?',(changed['lines'][0]['id'],))
        self.assertEqual(list(self.conn.execute('PRAGMA foreign_key_check')),[])

    def test_red_closing_matches_standard_projection_without_negative_pool(self):
        self.fixture.voucher(-10000)
        finance._insert_voucher(self.conn,1,'2026-01-31','合成红字损益结转',[
            finance._entry('5001',debit=-10000),finance._entry('3103',credit=-10000)],None,source_type='closing')
        report=finance.standard_balance_bundle(self.conn,1,'2026-01','2026-01')
        actual=finance.balance_bundle(self.conn,1,'2026-01','2026-01')
        self.assertEqual(sum(r['closing_debit_fen']-r['closing_credit_fen'] for r in report['trial_balance']),0)
        for code in ('1002','3103','5001'):
            a=next(r for r in actual['trial_balance'] if r['account_code']==code)
            b=next(r for r in report['trial_balance'] if r['account_code']==code)
            self.assertEqual(a['closing_debit_fen']-a['closing_credit_fen'],b['closing_debit_fen']-b['closing_credit_fen'])

    def test_opening_snapshot_does_not_backdate_or_allow_double_count(self):
        self.conn.execute("UPDATE clients SET start_period='2024-01' WHERE id=1")
        balances=[finance._entry('1002',10000),finance._entry('3001',credit=10000)]
        result=finance.import_opening_snapshot(self.conn,1,'2026-01',balances,{'sha256':'synthetic'})
        self.assertEqual(result['opening_period'],'2026-01')
        self.assertEqual(self.conn.execute('SELECT start_period FROM clients WHERE id=1').fetchone()[0],'2024-01')
        self.assertEqual(finance._client(self.conn,1)['opening_period'],'2026-01')
        for month in ('2024-01','2025-12'):
            with self.assertRaises(db.ApiError): finance.balance_bundle(self.conn,1,month,month)
            with self.assertRaises(db.ApiError): finance._open(self.conn,1,month)
        report=finance.balance_bundle(self.conn,1,'2026-01','2026-01')
        self.assertEqual(report['book_start_period'],'2026-01')
        self.assertTrue(all(r['date']=='2026-01-01' for r in report['balance_details']))
        bank=next(r for r in report['trial_balance'] if r['account_code']=='1002')
        self.assertEqual(bank['opening_debit_fen'],10000)
        with self.assertRaises(db.ApiError): finance.import_opening_snapshot(self.conn,1,'2026-01',balances,{'sha256':'synthetic'})


if __name__=='__main__': unittest.main()
