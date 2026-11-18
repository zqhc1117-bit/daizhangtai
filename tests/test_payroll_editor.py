"""Personnel carry-forward and voucher editor persistence, in temporary books only."""
import os
import tempfile
import unittest

import db
import finance


class PayrollEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.temp.name,'app.sqlite3');db.initialize(self.path)
        self.conn=db.connect(self.path);self.user={'id':1,'username':'tester'}
        with self.conn:
            self.conn.execute("INSERT INTO users(id,username,name,password_hash) VALUES(1,'tester','测试员','unused')")
            for cid in (1,2):
                self.conn.execute('INSERT INTO clients(id,name,code,taxpayer_type,standard,start_period,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(cid,f'合成账套{cid}',f'S{cid}','small','small2013','2026-01',db.now(),db.now()))
                finance.ensure_book(self.conn,dict(self.conn.execute('SELECT * FROM clients WHERE id=?',(cid,)).fetchone()))

    def tearDown(self):
        self.conn.close();self.temp.cleanup()

    def call(self,route='',payload=None,method='POST',cid=1,period='2026-11'):
        return finance.handle(self.conn,method,f'/api/books/{cid}'+('/'+route if route else ''),payload or {},self.user,{'period':period})

    def person(self,**changes):
        return self.call('payroll',{'period':'2026-11','employee_name':'合成员工甲','employee_code':'E001','department':'行政','position':'助理','note':'合成备注','gross_fen':1000000,'withheld_tax_fen':20000,'social_fen':80000,'other_deduction_fen':1500,'expense_account':'5602',**changes})

    def error(self,status,route,payload,method='POST',cid=1):
        with self.assertRaises(db.ApiError) as got:self.call(route,payload,method,cid)
        self.assertEqual(status,got.exception.status,got.exception.message)

    def test_carry_keeps_source_and_deduplicates_current_month(self):
        source=self.person();self.person(employee_name='合成员工乙',employee_code='E002',gross_fen=700000)
        result=self.call('payroll/carry-forward',{'period':'2026-12','reset_deductions':False})
        self.assertEqual(2,result['copied']);self.assertEqual('2026-11',result['source_period'])
        rows=self.call('payroll',method='GET',period='2026-12')['rows']
        self.assertEqual((2,2), (len(rows),self.call('payroll/carry-forward',{'period':'2026-12'})['skipped'].__len__()))
        first=next(p for p in rows if p['employee_name']==source['employee_name'])
        for k in ('employee_name','employee_code','department','position','note','gross_fen','social_fen','other_deduction_fen','expense_account'):
            self.assertEqual(source[k],first[k])
        self.assertEqual(0,first['withheld_tax_fen'])
        self.assertEqual(918500,first['net_fen'])
        self.assertEqual((None,None,1),(first['voucher_id'],first['payment_voucher_id'],first['version']))
        changed=self.call(f'payroll/{first["id"]}',{'version':1,'gross_fen':1200000,'department':'运营'},'PATCH')
        self.assertEqual(1118500,changed['net_fen']);self.assertEqual(2,changed['version'])
        old=next(p for p in self.call('payroll',method='GET')['rows'] if p['id']==source['id'])
        self.assertEqual(source,old,'Editing December must not change November.')
        self.call(f'payroll/{first["id"]}',{'version':2},'DELETE')
        self.assertEqual(1,len(self.call('payroll',method='GET',period='2026-12')['rows']))
        self.assertEqual(2,len(self.call('payroll',method='GET')['rows']))

    def test_year_boundary_legacy_reset_option_and_employee_code_identity(self):
        self.person(period='2026-12')
        current=self.person(period='2027-01',employee_name='合成员工改名',gross_fen=999999)
        skipped=self.call('payroll/carry-forward',{'period':'2027-01'})
        self.assertEqual(0,skipped['copied']);self.assertEqual(['合成员工甲'],skipped['skipped'])
        self.assertEqual(current,self.call('payroll',method='GET',period='2027-01')['rows'][0])
        self.person(period='2026-12',employee_name='合成员工乙',employee_code='E002')
        result=self.call('payroll/carry-forward',{'period':'2027-01','reset_deductions':True})
        self.assertEqual(1,result['copied'])
        row=next(r for r in self.call('payroll',method='GET',period='2027-01')['rows'] if r['employee_code']=='E002')
        self.assertEqual((0,80000,1500,918500),(row['withheld_tax_fen'],row['social_fen'],row['other_deduction_fen'],row['net_fen']))

    def test_posted_source_can_carry_without_voucher_links_but_target_is_locked(self):
        source=self.person();self.call('payroll/post',{'period':'2026-11'})
        self.call('payroll/pay',{'period':'2026-11','date':'2026-11-30'})
        self.assertEqual(1,self.call('payroll/carry-forward',{'period':'2026-12'})['copied'])
        row=self.call('payroll',method='GET',period='2026-12')['rows'][0]
        self.assertIsNone(row['voucher_id']);self.assertIsNone(row['payment_voucher_id'])
        self.assertEqual(0,row['withheld_tax_fen']);self.assertEqual(918500,row['net_fen'])
        self.call('payroll/post',{'period':'2026-12'})
        row=self.call('payroll',method='GET',period='2026-12')['rows'][0]
        self.error(409,f'payroll/{row["id"]}',{'version':row['version'],'gross_fen':900000},'PATCH')
        self.error(409,'payroll/carry-forward',{'period':'2026-12'})
        self.error(409,f'payroll/{row["id"]}',{'version':row['version']},'DELETE')

    def test_versions_duplicates_scope_and_invalid_values(self):
        row=self.person()
        self.error(409,'payroll',dict(row,id=None,employee_name='另一个姓名'))
        changed=self.call(f'payroll/{row["id"]}',{'version':1,'note':'修改'},'PATCH')
        self.error(409,f'payroll/{row["id"]}',{'version':1,'gross_fen':99},'PATCH')
        self.error(404,f'payroll/{row["id"]}',{'version':2,'gross_fen':99},'PATCH',cid=2)
        self.error(400,f'payroll/{row["id"]}',{'version':2,'period':'2026-12'},'PATCH')
        self.error(400,f'payroll/{row["id"]}',{'version':2,'gross_fen':1},'PATCH')
        self.error(400,'payroll/carry-forward',{'period':'2026-12','source_period':'2026-10'})
        self.assertEqual(changed,self.call('payroll',method='GET')['rows'][0])

    def test_carry_is_atomic_and_company_scoped(self):
        self.person();self.person(employee_name='合成员工乙',employee_code='E002')
        with self.conn:self.conn.execute("UPDATE payroll SET expense_account='9999' WHERE employee_code='E002'")
        self.error(400,'payroll/carry-forward',{'period':'2026-12'})
        self.assertEqual([],self.call('payroll',method='GET',period='2026-12')['rows'])
        self.error(400,'payroll/carry-forward',{'period':'2026-12'},cid=2)
        with self.conn:self.conn.execute("INSERT INTO book_periods(client_id,period,closed) VALUES(1,'2026-12',1)")
        self.error(409,'payroll/carry-forward',{'period':'2026-12'})

    def detailed_person(self,**changes):
        return self.person(gross_fen=700000,social_fen=76635,pension_fen=58160,medical_fen=14840,unemployment_fen=3635,housing_fund_fen=30000,withheld_tax_fen=2801,other_deduction_fen=0,**changes)

    def test_social_details_housing_net_and_payment_reconcile(self):
        row=self.detailed_person()
        self.assertEqual(590564,row['net_fen'])
        self.call('payroll/post',{'period':'2026-11'})
        payment=self.call('payroll/pay',{'period':'2026-11','date':'2026-11-30'})
        voucher=finance._voucher(self.conn,1,payment['voucher_id'])
        credits={line['account_code']:line['credit_fen'] for line in voucher['lines'] if line['credit_fen']}
        self.assertEqual({'1002':590564,'2221':2801,'2241':106635},credits)
        self.assertEqual(700000,sum(credits.values()))

    def test_details_carry_resets_only_tax_and_preserves_history(self):
        original=self.detailed_person()
        self.assertEqual(1,self.call('payroll/carry-forward',{'period':'2026-12'})['copied'])
        december=self.call('payroll',method='GET',period='2026-12')['rows'][0]
        for key in ('pension_fen','medical_fen','unemployment_fen','social_fen','housing_fund_fen','gross_fen','other_deduction_fen'):
            self.assertEqual(original[key],december[key])
        self.assertEqual(0,december['withheld_tax_fen'])
        self.assertEqual(593365,december['net_fen'])
        updated=self.call(f'payroll/{december["id"]}',{'version':1,'pension_fen':60000,'housing_fund_fen':35000,'withheld_tax_fen':7890},'PATCH')
        self.assertEqual((78475,578635),(updated['social_fen'],updated['net_fen']))
        self.assertEqual(original,self.call('payroll',method='GET')['rows'][0])
        self.assertEqual([],self.call('payroll',method='GET',cid=2,period='2026-12')['rows'])
        self.call('payroll/carry-forward',{'period':'2027-01','reset_deductions':True})
        january=self.call('payroll',method='GET',period='2027-01')['rows'][0]
        for key in ('pension_fen','medical_fen','unemployment_fen','social_fen','housing_fund_fen','other_deduction_fen','gross_fen'):
            self.assertEqual(updated[key],january[key])
        self.assertEqual(0,january['withheld_tax_fen'])
        self.assertEqual(586525,january['net_fen'])
        self.assertEqual(updated,self.call('payroll',method='GET',period='2026-12')['rows'][0])
        finance.init_schema(self.conn);finance.init_schema(self.conn)
        self.assertEqual(original,self.call('payroll',method='GET')['rows'][0])

    def test_details_validate_before_writing_and_legacy_total_stays_available(self):
        row=self.person()
        for payload in ({'pension_fen':100,'social_fen':1},{'pension_fen':-1},{'housing_fund_fen':True},{'medical_fen':1.5},{'housing_fund_fen':1000000}):
            self.error(400,f'payroll/{row["id"]}',{'version':1,**payload},'PATCH')
        self.assertEqual(row,self.call('payroll',method='GET')['rows'][0])
        self.assertEqual((0,0,0,0),tuple(row[key] for key in ('pension_fen','medical_fen','unemployment_fen','housing_fund_fen')))
        self.assertEqual(898500,row['net_fen'])
        payload={key:value for key,value in row.items() if key not in ('id','social_fen')}
        payload.update(employee_name='自动汇总',employee_code='E002',pension_fen=10000,medical_fen=2000,unemployment_fen=300,housing_fund_fen=5000)
        created=self.call('payroll',payload)
        self.assertEqual(12300,created['social_fen'])
        self.assertEqual(961200,created['net_fen'])

    def voucher_payload(self,**changes):
        return {'date':'2026-11-17','summary':'合成办公费','voucher_word':'付','attachment_count':2,'lines':[{'account_code':'5602','debit_fen':12345},{'account_code':'1002','credit_fen':12345,'cashflow_category':'operating'}],**changes}

    def test_voucher_templates_validate_and_are_scoped(self):
        payload=self.voucher_payload(name='月度费用')
        created=self.call('voucher-templates',payload)
        stored=self.call('voucher-templates',method='GET')['templates'][0]
        self.assertEqual('付',stored['voucher_word']);self.assertEqual(12345,stored['lines'][0]['debit_fen'])
        self.assertEqual([],self.call('voucher-templates',method='GET',cid=2)['templates'])
        self.error(404,f'voucher-templates/{created["id"]}',{'version':1},'DELETE',cid=2)
        self.error(409,f'voucher-templates/{created["id"]}',{'version':2},'DELETE')
        self.call(f'voucher-templates/{created["id"]}',{'version':1},'DELETE')
        self.assertEqual([],self.call('voucher-templates',method='GET')['templates'])
        self.error(400,'voucher-templates',dict(payload,lines=[{'account_code':'5602','debit_fen':100},{'account_code':'1002','credit_fen':200}]))

    def test_voucher_attachments_preserve_edit_omission_and_reject_cross_book_atomically(self):
        with self.conn:
            for i in (1,2):self.conn.execute('INSERT INTO files(id,name,path,size,sha256,user_id,created_at) VALUES(?,?,?,?,?,?,?)',(i,f'合成附件{i}.txt',f'synthetic-{i}.txt',4,'unused',1,db.now()))
        row=self.call('vouchers',self.voucher_payload(attachment_ids=[1]))
        self.assertEqual('付',row['voucher_word']);self.assertEqual([1],[f['id'] for f in row['attachments']])
        self.error(403,'vouchers',self.voucher_payload(attachment_ids=[1]),cid=2)
        self.assertEqual(0,self.conn.execute('SELECT COUNT(*) FROM vouchers WHERE client_id=2').fetchone()[0])
        updated=self.call(f'vouchers/{row["id"]}',self.voucher_payload(version=1),'PATCH')
        self.assertEqual([1],[f['id'] for f in updated['attachments']])
        self.error(400,f'vouchers/{row["id"]}',self.voucher_payload(version=2,attachment_ids=[2,999]),'PATCH')
        self.assertEqual(2,finance._voucher(self.conn,1,row['id'])['version'])
        self.assertEqual([1],[f['id'] for f in finance._voucher(self.conn,1,row['id'])['attachments']])
        final=self.call(f'vouchers/{row["id"]}',self.voucher_payload(version=2,attachment_ids=[2]),'PATCH')
        self.assertEqual([2],[f['id'] for f in final['attachments']])
        finance.init_schema(self.conn);finance.init_schema(self.conn)
        self.assertEqual(final,finance._voucher(self.conn,1,row['id']))


if __name__=='__main__':unittest.main()
