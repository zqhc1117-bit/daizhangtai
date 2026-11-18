import base64
import unittest

import test_http as http_suite


class VoucherPayrollHTTPTests(unittest.TestCase):
    setUp=http_suite.HTTPTests.setUp
    tearDown=http_suite.HTTPTests.tearDown
    setup=http_suite.HTTPTests.setup
    create_client=http_suite.HTTPTests.create_client

    def test_editor_files_sources_download_and_template_routes(self):
        self.setup();first=self.create_client('EDITOR-1','small2013');second=self.create_client('EDITOR-2','small2013')
        prefix=f'/api/books/{first["id"]}'
        content=b'Local synthetic attachment only.'
        status,file,_=self.client.call('/api/upload',{'name':'synthetic.txt','content_base64':base64.b64encode(content).decode()})
        self.assertEqual(200,status)
        payload={'date':'2026-11-17','summary':'合成凭证','attachment_count':1,'attachment_ids':[file['id']],'voucher_word':'付','lines':[{'account_code':'5602','debit_fen':10000},{'account_code':'1002','credit_fen':10000}]}
        status,voucher,_=self.client.call(prefix+'/vouchers',payload);self.assertEqual(200,status,voucher)
        path=f'{prefix}/vouchers/{voucher["id"]}/attachments/{file["id"]}'
        status,data,headers=self.client.call(path);self.assertEqual((200,content),(status,data));self.assertIn('attachment;',headers['Content-Disposition'])
        self.assertEqual(404,self.client.call(path.replace(prefix,f'/api/books/{second["id"]}'))[0])
        status,sources,_=self.client.call(f'{prefix}/vouchers/{voucher["id"]}/sources')
        self.assertEqual(200,status);self.assertTrue(sources['has_originals']);self.assertEqual('synthetic.txt',sources['manual_files'][0]['name'])
        status,template,_=self.client.call(prefix+'/voucher-templates',{**payload,'name':'合成模板'});self.assertEqual(200,status,template)
        status,templates,_=self.client.call(prefix+'/voucher-templates');self.assertEqual(200,status);self.assertEqual('付',templates['templates'][0]['voucher_word'])
        self.assertEqual(200,self.client.call(f'{prefix}/voucher-templates/{template["id"]}',{'version':1},'DELETE')[0])

    def test_payroll_edit_carry_reload_delete_routes(self):
        self.setup();client=self.create_client('PAYROLL','small2013');prefix=f'/api/books/{client["id"]}'
        payload={'period':'2026-12','employee_name':'HTTP合成员工','employee_code':'HTTP-E01','department':'行政','position':'助理','note':'测试','gross_fen':100000,'withheld_tax_fen':1000,'social_fen':2000,'pension_fen':1200,'medical_fen':600,'unemployment_fen':200,'housing_fund_fen':300,'other_deduction_fen':400,'expense_account':'5602'}
        status,person,_=self.client.call(prefix+'/payroll',payload);self.assertEqual(200,status,person)
        status,carry,_=self.client.call(prefix+'/payroll/carry-forward',{'period':'2027-01'});self.assertEqual(200,status,carry);self.assertEqual(1,carry['copied'])
        status,rows,_=self.client.call(prefix+'/payroll?period=2027-01');self.assertEqual(200,status);next_row=rows['rows'][0]
        for key in ('employee_name','employee_code','department','position','note','gross_fen','pension_fen','medical_fen','unemployment_fen','social_fen','housing_fund_fen','other_deduction_fen','expense_account'):self.assertEqual(payload[key],next_row[key])
        self.assertEqual((0,97300),(next_row['withheld_tax_fen'],next_row['net_fen']))
        status,changed,_=self.client.call(f'{prefix}/payroll/{next_row["id"]}',{'version':1,'gross_fen':110000,'department':'运营'},'PATCH');self.assertEqual(200,status,changed);self.assertEqual(107300,changed['net_fen'])
        status,book,_=self.client.call(prefix+'?period=2027-01');self.assertEqual(200,status);self.assertEqual('运营',book['payroll'][0]['department'])
        self.assertEqual(200,self.client.call(f'{prefix}/payroll/{next_row["id"]}',{'version':2},'DELETE')[0])
        self.assertEqual([],self.client.call(prefix+'/payroll?period=2027-01')[1]['rows'])
        self.assertEqual(person,self.client.call(prefix+'/payroll?period=2026-12')[1]['rows'][0])


if __name__=='__main__':unittest.main()
