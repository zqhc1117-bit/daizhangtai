"""Automatic summaries must describe the subject without changing accounting."""
import unittest
import test_reconciliation_batch as fixtures
import finance
import reconciliation_batch as batch
import repair_voucher_summaries as repair


class VoucherSummaryTests(unittest.TestCase):
    setUp=fixtures.BatchTests.setUp
    tearDown=fixtures.BatchTests.tearDown
    item=fixtures.BatchTests.item
    payload=fixtures.BatchTests.payload

    def test_expense_preview_and_saved_summaries_use_subject_categories(self):
        payload=self.payload()
        payload['transactions'][0]['note']='IBPS对公提回借记'
        draft=batch.preview(self.c,payload)['rows'][0]['voucher']
        self.assertEqual(['管理费用','银行存款'],[l['summary'] for l in draft['lines']])
        posted=batch.auto_post(self.c,payload,self.user)['rows'][0]
        saved=finance._voucher(self.c,1,posted['voucher_id'])
        self.assertEqual(['管理费用','银行存款'],[l['summary'] for l in saved['lines']])
        self.assertIn('IBPS',saved['summary'],'Original transaction remains available for search and audit.')
        self.assertEqual(10000,sum(l['debit_fen'] for l in saved['lines']))

    def test_income_tax_and_counterparty_detail_use_own_book_categories(self):
        payload=self.payload();payload['transactions'][0].update(amount='7500.00',note='IBPS对公提回贷记')
        payload['invoices'][0].update(amount='7500.00',net_amount='7425.74',tax_amount='74.26',seller='本方公司',buyer='供应商')
        payload['evidence'][0]['business_account_code']='5001'
        saved=batch.auto_post(self.c,payload,self.user)['rows'][0]
        self.assertEqual(['银行存款','主营业务收入','应交税费'],[l['summary'] for l in saved['posted_lines']])
        self.assertEqual([750000,742574,7426],[l['debit_fen'] or l['credit_fen'] for l in saved['posted_lines']])

    def test_confirmed_manual_line_summaries_are_preserved(self):
        payload=self.payload();preview=batch.preview(self.c,payload)
        draft=preview['rows'][0]['voucher'];draft['lines'][0]['summary']='人工确认的办公支出'
        out=batch.save_batch(self.c,{**payload,'confirmed':True,'preview_token':preview['preview_token'],'selected_indices':[0],'voucher':draft},self.user)
        self.assertEqual('人工确认的办公支出',out['results'][0]['posted_lines'][0]['summary'])

    def test_deferred_details_and_bank_subaccounts_resolve_to_parent_category(self):
        with self.c:
            bank=finance._create_account(self.c,1,{'parent_code':'1002','name':'某银行账户'},self.user)
        self.assertEqual('银行存款',finance.account_category_summary(self.c,1,{'account_code':bank['code']}))
        self.assertEqual('应付账款',finance.account_category_summary(self.c,1,{'counterparty_parent_code':'2202'}))
        self.assertEqual('管理费用',finance.account_category_summary(self.c,1,{'business_parent_code':'5602','business_sub_account':'某供应商'}))

    def test_repair_proves_original_summaries_and_is_idempotent(self):
        from unittest.mock import patch
        payload=self.payload()
        note=payload['transactions'][0]['note']
        # Recreate the prior generator, including its original creation audit.
        with patch.object(finance,'with_account_summaries',side_effect=lambda c,cid,lines:[dict(l,summary=note) for l in lines]):
            posted=batch.auto_post(self.c,payload,self.user)['rows'][0]
        before=finance._voucher(self.c,1,posted['voucher_id'])
        planned=repair.plan_repairs(self.c);self.assertEqual(1,len(planned))
        with self.c:repair.apply_repairs(self.c,planned)
        after=finance._voucher(self.c,1,posted['voucher_id'])
        self.assertEqual(['管理费用','银行存款'],[l['summary'] for l in after['lines']])
        self.assertEqual([(l['debit_fen'],l['credit_fen']) for l in before['lines']],[(l['debit_fen'],l['credit_fen']) for l in after['lines']])
        self.assertEqual([],repair.plan_repairs(self.c))

    def test_repair_does_not_change_manual_reviewed_or_closed_vouchers(self):
        from unittest.mock import patch
        payload=self.payload();note=payload['transactions'][0]['note']
        with patch.object(finance,'with_account_summaries',side_effect=lambda c,cid,lines:[dict(l,summary=note) for l in lines]):
            posted=batch.auto_post(self.c,payload,self.user)['rows'][0]
        vid=posted['voucher_id']
        with self.c:self.c.execute('UPDATE vouchers SET reviewed=1 WHERE id=?',(vid,))
        self.assertEqual([],repair.plan_repairs(self.c))
        with self.c:
            self.c.execute('UPDATE vouchers SET reviewed=0 WHERE id=?',(vid,))
            self.c.execute('UPDATE book_periods SET closed=1 WHERE client_id=1')
        self.assertEqual([],repair.plan_repairs(self.c))
        with self.c:
            self.c.execute('UPDATE book_periods SET closed=0 WHERE client_id=1')
            self.c.execute("UPDATE voucher_lines SET summary='人工填写' WHERE voucher_id=?",(vid,))
        self.assertEqual([],repair.plan_repairs(self.c))


if __name__=='__main__':unittest.main()
