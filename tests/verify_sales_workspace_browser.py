"""Exercise the sales flow in a temporary synthetic account, including real OCR."""
from contextlib import closing
from pathlib import Path
import base64
import json
import subprocess
import sys
import tempfile
import threading
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import business
import db
import server
from playwright.sync_api import sync_playwright, expect

OUT = ROOT / 'tmp' / 'sales-workspace-verification'
OUT.mkdir(parents=True, exist_ok=True)


def main():
    with tempfile.TemporaryDirectory(prefix='sales-workspace-test-') as folder:
        app = server.Application(folder, demo=True)
        with closing(db.connect(app.db_path)) as conn, conn:
            user = dict(conn.execute('SELECT id,username,name FROM users WHERE id=1').fetchone())
            client = business.handle(conn, 'POST', '/api/clients', dict(name='销售验收历史公司', code='SALES-1', taxpayer_type='small', standard='small2013', start_period='2026-01', accountant_id=1), user, {})
            token, csrf = app.new_session(conn, user)
            before = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ['clients', 'vouchers', 'files']}
        http = server.make_server('127.0.0.1', 0, app)
        http.RequestHandlerClass.log_message = lambda *args: None
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{http.server_address[1]}'
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel='chrome', headless=True)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN', timezone_id='Asia/Shanghai')
                context.add_cookies([{'name': 'ydz_session', 'value': token, 'url': base}])
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.set_default_timeout(15000)
                page.goto(base)
                expect(page.locator('.home-overview')).to_be_visible()
                expect(page.locator('[data-sub="工作总览"]')).to_have_text('财务工作台')
                page.locator('[data-sub="销售辅助台"]').click()
                expect(page.locator('.sales-card')).to_have_count(2)
                page.screenshot(path=str(OUT / '01-sales-home.png'), full_page=True)
                page.locator('[data-sales-open="ledger"]').click()
                page.locator('[name="companyName"]').fill('销售验收新客户')
                page.locator('[name="total"]').fill('12345.67')
                page.locator('[name="keywords"]').fill('办公桌、办公椅')
                page.locator('[data-ld-form] [type="submit"]').click()
                expect(page.locator('[data-ld-validation]')).to_contain_text('已配平')
                with page.expect_download() as download:
                    page.locator('[data-ld-action="export"]').click()
                download.value.save_as(str(OUT / 'sales-ledger.xlsx'))
                page.locator('[data-sales-back]').click()
                page.locator('[data-sales-open="tax"]').click()
                expect(page.locator('[data-sales-generate]')).to_be_disabled()
                page.screenshot(path=str(OUT / '02-tax-upload.png'), full_page=True)

                # Native xlsx, legacy xls and both CSV encodings exercise the full UI.
                for format in ['xlsx', 'xls', 'csv']:
                    data = page.evaluate('''async type => {
                        const X=await import('/modules/tax-liquidation/vendor/xlsx.js');
                        const rows=[['资产负债表'],['编制单位：销售报告验收公司'],['2026年11月30日','单位：万元'],['项目','行次','期末余额','年初余额','项目','行次','期末余额','年初余额'],['货币资金',1,8,900,'应付账款',1,2,800],['固定资产净值',2,1.8,800,'实收资本',2,5,300],['资产总计',3,9.8,1700,'未分配利润',3,2.8,600]];
                        const b=X.utils.book_new();X.utils.book_append_sheet(b,X.utils.aoa_to_sheet(rows),'资产负债表');return Array.from(new Uint8Array(X.write(b,{type:'array',bookType:type==='xls'?'biff8':type})));
                    }''', format)
                    page.locator('[data-sales-file]').set_input_files({'name': f'报表.{format}', 'mimeType': 'application/octet-stream', 'buffer': bytes(data)})
                    expect(page.locator('[data-sales-status]')).to_contain_text('已识别')
                    page.locator('[data-sales-generate]').click()
                    expect(page.locator('[data-sales-report]')).to_contain_text('80,000.00')
                    expect(page.locator('[data-sales-report]')).to_contain_text('小规模纳税人')
                page.locator('[name="sales-taxpayer"][value="general"]').check()
                expect(page.locator('[data-sales-result]')).to_be_hidden()
                page.locator('[data-sales-generate]').click()
                expect(page.locator('[data-sales-report]')).to_contain_text('一般纳税人')
                page.locator('[data-sales-recognition] summary').click()
                page.locator('[data-sales-company]').fill('销售报告更名验收公司')
                page.locator('[data-sales-amount="0"]').fill('81000.00')
                page.locator('[data-sales-amount="0"]').press('Tab')
                page.locator('[data-sales-generate]').click()
                expect(page.locator('[data-sales-report]')).to_contain_text('81,000.00')
                expect(page.locator('[data-sales-report]')).to_contain_text('销售报告更名验收公司')
                with page.expect_download() as download:
                    page.locator('[data-sales-word]').click()
                download.value.save_as(str(OUT / 'sales-report.docx'))
                with zipfile.ZipFile(OUT / 'sales-report.docx') as archive:
                    word = archive.read('word/document.xml').decode('utf-8')
                assert '一般纳税人' in word and '81,000.00' in word and '销售报告更名验收公司' in word
                page.screenshot(path=str(OUT / '03-tax-report.png'), full_page=True)
                page.pdf(path=str(OUT / 'sales-report.pdf'), prefer_css_page_size=True, print_background=True)
                page.reload()
                page.locator('[data-sub="销售辅助台"]').click()
                page.locator('[data-sales-open="tax"]').click()
                expect(page.locator('[data-sales-report]')).to_contain_text('81,000.00')
                expect(page.locator('[name="sales-taxpayer"][value="general"]')).to_be_checked()

                trial='科目余额表\n编制单位：余额表验收公司\n单位：元\n科目编码,科目名称,期初余额,,本期发生额,,期末余额,\n,,借方,贷方,借方,贷方,借方,贷方\n1002,银行存款,999,0,999,0,10000,0\n100201,银行存款_甲银行,999,0,999,0,10000,0\n1601,固定资产,999,0,999,0,10000,0\n1602,累计折旧,0,999,0,999,0,2000\n2202,应付账款,0,999,0,999,0,3000\n3001,实收资本,0,999,0,999,0,15000'
                page.locator('[data-sales-file]').set_input_files({'name':'余额.csv','mimeType':'text/csv','buffer':trial.encode('gb18030')})
                expect(page.locator('[data-sales-status]')).to_contain_text('科目余额表')
                page.locator('[data-sales-generate]').click()
                expect(page.locator('[data-sales-report]')).to_contain_text('8,000.00')
                expect(page.locator('[data-sales-report]')).to_contain_text('10,000.00')
                assert '99,900.00' not in page.locator('[data-sales-report]').inner_text()

                # Generate native PDF and image-only PDF from the same synthetic table.
                fixture = context.new_page()
                fixture.set_content('''<!doctype html><meta charset="utf-8"><style>body{font:20px "Microsoft YaHei";padding:30px}h1{text-align:center;font-size:28px}table{border-collapse:collapse;width:900px}th,td{border:1px solid #555;padding:12px 15px;text-align:right}th:first-child,td:first-child{text-align:left}</style><h1>资产负债表</h1><p>编制单位：PDF验收公司</p><p>2026年11月30日　　单位：元</p><table><tr><th>项目</th><th>行次</th><th>期末余额</th><th>年初余额</th></tr><tr><td>货币资金</td><td>1</td><td>80,000.00</td><td>900,000.00</td></tr><tr><td>固定资产净值</td><td>2</td><td>18,000.00</td><td>800,000.00</td></tr><tr><td>应付账款</td><td>3</td><td>20,000.00</td><td>800,000.00</td></tr><tr><td>实收资本</td><td>4</td><td>50,000.00</td><td>300,000.00</td></tr><tr><td>未分配利润</td><td>5</td><td>28,000.00</td><td>600,000.00</td></tr><tr><td>资产总计</td><td>6</td><td>98,000.00</td><td>1,700,000.00</td></tr></table>''')
                fixture.pdf(path=str(OUT/'native-statement.pdf'),format='A4',landscape=True,print_background=True)
                fixture.screenshot(path=str(OUT/'statement.png'), full_page=True)
                from PIL import Image
                with Image.open(OUT/'statement.png') as img:
                    img.convert('RGB').save(OUT/'scanned-statement.pdf',resolution=140)
                fixture.set_content('''<!doctype html><meta charset="utf-8"><style>body{font:18px "Microsoft YaHei";padding:24px}table{border-collapse:collapse;width:1000px}td,th{border:1px solid #555;padding:12px 8px;text-align:right}td:nth-child(1),td:nth-child(5){text-align:left}</style><h1>资产负债表</h1><p>编制单位：并排PDF验收公司</p><p>2026年11月30日　　单位：元</p><table><tr><th>资产</th><th>行次</th><th>期末余额</th><th>年初余额</th><th>负债和所有者权益</th><th>行次</th><th>期末余额</th><th>年初余额</th></tr><tr><td>货币资金</td><td>1</td><td>80,000.00</td><td>900,000.00</td><td>应付账款</td><td>1</td><td>20,000.00</td><td>800,000.00</td></tr><tr><td>固定资产净值</td><td>2</td><td>18,000.00</td><td>800,000.00</td><td>实收资本</td><td>2</td><td>50,000.00</td><td>300,000.00</td></tr><tr><td>资产总计</td><td>3</td><td>98,000.00</td><td>1,700,000.00</td><td>未分配利润</td><td>3</td><td>28,000.00</td><td>600,000.00</td></tr></table>''')
                fixture.pdf(path=str(OUT/'side-by-side.pdf'),format='A4',landscape=True,print_background=True)
                fixture.set_content('''<!doctype html><meta charset="utf-8"><style>body{font:18px "Microsoft YaHei";padding:24px}table{border-collapse:collapse;width:1000px}td,th{border:1px solid #555;padding:12px 8px;text-align:center}</style><h1>科目余额表</h1><p>编制单位：PDF余额表验收公司</p><p>2026年11月30日　　单位：元</p><table><tr><th rowspan="2">科目编码</th><th rowspan="2">科目名称</th><th colspan="2">期初余额</th><th colspan="2">本期发生额</th><th colspan="2">期末余额</th></tr><tr><th>借方</th><th>贷方</th><th>借方</th><th>贷方</th><th>借方</th><th>贷方</th></tr><tr><td>1002</td><td>银行存款</td><td>999</td><td>0</td><td>999</td><td>0</td><td>10,000.00</td><td>0</td></tr><tr><td>1601</td><td>固定资产</td><td>999</td><td>0</td><td>999</td><td>0</td><td>10,000.00</td><td>0</td></tr><tr><td>1602</td><td>累计折旧</td><td>0</td><td>999</td><td>0</td><td>999</td><td>0</td><td>2,000.00</td></tr><tr><td>2202</td><td>应付账款</td><td>0</td><td>999</td><td>0</td><td>999</td><td>0</td><td>3,000.00</td></tr><tr><td>3001</td><td>实收资本</td><td>0</td><td>999</td><td>0</td><td>999</td><td>0</td><td>15,000.00</td></tr></table>''')
                fixture.pdf(path=str(OUT/'trial-statement.pdf'),format='A4',landscape=True,print_background=True)
                fixture.screenshot(path=str(OUT/'trial-statement.png'),full_page=True)
                fixture.close()
                for name in ['native-statement.pdf','statement.png','scanned-statement.pdf','side-by-side.pdf','trial-statement.pdf','trial-statement.png']:
                    print('Uploading', name, flush=True)
                    page.locator('[data-sales-file]').set_input_files(str(OUT/name))
                    expect(page.locator('[data-sales-status]')).to_contain_text('已识别',timeout=230000)
                    page.locator('[data-sales-generate]').click()
                    expect(page.locator('[data-sales-report]')).to_contain_text('10,000.00' if name.startswith('trial') else '80,000.00')
                    expect(page.locator('[data-sales-report]')).to_contain_text('8,000.00' if name.startswith('trial') else '18,000.00')
                    assert '900,000.00' not in page.locator('[data-sales-report]').inner_text()
                    print('Verified', name, flush=True)

                # A failed replacement cannot export the previous company's report.
                page.locator('[data-sales-file]').set_input_files({'name':'错误.pdf','mimeType':'application/pdf','buffer':b'not a pdf'})
                expect(page.locator('[data-sales-status]')).to_contain_text('不一致')
                expect(page.locator('[data-sales-generate]')).to_be_disabled()
                expect(page.locator('[data-sales-result]')).to_be_hidden()
                page.locator('[data-sales-file]').set_input_files(str(OUT/'native-statement.pdf'))
                expect(page.locator('[data-sales-status]')).to_contain_text('已识别',timeout=60000)
                pending=[]
                page.route('**/api/sales/statement-text',lambda route:pending.append(route))
                page.locator('[data-sales-file]').set_input_files(str(OUT/'native-statement.pdf'))
                expect(page.locator('[data-sales-generate]')).to_have_text('正在识别报表…')
                page.locator('[data-sales-back]').click()
                page.locator('[data-sales-open="tax"]').click()
                expect(page.locator('[data-sales-generate]')).to_be_disabled()
                assert pending
                pending[0].fulfill(status=200,content_type='application/json',body=json.dumps({'rows':[['编制单位：PDF验收公司'],['项目','期末余额'],['货币资金','80000.00'],['实收资本','80000.00']],'ocr':False,'warnings':[]},ensure_ascii=False))
                expect(page.locator('[data-sales-status]')).to_contain_text('已识别')
                expect(page.locator('[data-sales-generate]')).to_be_enabled()
                page.unroute('**/api/sales/statement-text')
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(OUT/'04-mobile-upload.png'),full_page=True)
                assert page.locator('[data-sales-generate]').is_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Sales layout overflows narrow screen'
                page.set_viewport_size({'width':1440,'height':1000})

                # The prompt by the report leads to the existing company draft and the requested fields.
                page.locator('[data-sales-generate]').click()
                page.locator('[data-sales-result] [data-sales-fill]').click()
                expect(page.locator('.sales-history')).to_have_attribute('open', '')
                page.locator('[data-sales-client]').select_option(str(client['id']))
                page.locator('[data-sales-open="tax-manual"]').click()
                expect(page.locator('[data-tliq-path="company.name"]')).to_have_value('销售验收历史公司')
                page.locator('[data-tliq-action="mode"][data-tliq-mode="full"]').click()
                page.locator('[data-tliq-steps] [data-tliq-step="2"]').click()
                page.locator('[data-tliq-path="full.balance.fixedAssets"]').fill('1000.00')
                page.locator('[data-tliq-path="full.balance.payables"]').fill('250.00')
                page.locator('[data-tliq-steps] [data-tliq-step="3"]').click()
                expect(page.locator('[data-tliq-path="full.assets.fixedAssets.realized"]')).to_be_visible()
                expect(page.locator('[data-tliq-path="full.debts.payables.method"]')).to_be_visible()
                page.locator('[data-tliq-steps] [data-tliq-step="4"]').click()
                expect(page.locator('[data-tliq-path="settlement.taxes.income"]')).to_be_visible()
                page.locator('[data-sales-back]').click()
                expect(page.locator('[data-sales-result]')).to_be_visible()
                page.locator('[data-sub="工作总览"]').click()
                page.locator(f'[data-action="company-home"][data-id="{client["id"]}"]').click()
                expect(page.locator('.cw-shell')).to_be_visible()
                expect(page.locator('.cw-side-nav [data-cw-view="tax-liquidation"]')).to_have_count(0)
                expect(page.locator('.cw-side-nav [data-cw-view="ledger-draft"]')).to_have_count(0)
                assert not errors, errors
                with closing(db.connect(app.db_path)) as conn:
                    after = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in before}
                assert before == after, (before,after)

                # Native statement and receipts share one transaction. Wages and housing have a single source each.
                fixture = context.new_page()
                bank_statement = '''借/贷
账号：12345678901234
本方账号户名：销售验收历史公司
20260901 12:00:01
12345678901234张三 1234567890 对方开户行 123456789借3200.50发放工资10000.00
20260902 12:00:02
12345678901234国家金库北京分库 1234567890 清算行 123456789借977.32税务扣缴9000.00
20260904 12:00:04
12345678901234国家金库北京分库 1234567890 清算行 123456789借150.00增值税8850.00'''
                receipts = '''银行业务回单（付款）
回单编号：QA002
日期：2026-09-02
小写：977.32
付款人户名：销售验收历史公司
收款人户名：国家金库北京分库
摘要：税务扣款
用途：缴纳社会保险费

银行业务回单（付款）
回单编号：QA003
日期：2026-09-03
小写：450.10
付款人户名：销售验收历史公司
收款人户名：住房公积金管理中心
摘要：缴存'''
                uploads = []
                for filename, body in [('合成流水.pdf', bank_statement), ('合成回单.pdf', receipts)]:
                    fixture.set_content('<meta charset="utf-8"><pre style="font:18px Microsoft YaHei">' + body + '</pre>')
                    uploads.append({'name':filename, 'mimeType':'application/pdf', 'buffer':fixture.pdf(format='A4')})
                fixture.close()
                page.locator('.cw-header [data-cw-action="back"]').click()
                page.locator('[data-sub="销售辅助台"]').click()
                page.locator('[data-sales-payroll-jump]').click()
                page.locator('[data-payroll-client]').select_option(str(client['id']))
                expect(page.locator('[data-payroll-bank]')).to_have_value('1002')
                page.locator('[data-payroll-files]').set_input_files(uploads)
                page.locator('[data-payroll-parse]').click()
                expect(page.locator('[data-payroll-status]')).to_contain_text('待确认入账 3 笔',timeout=65000)
                expect(page.locator('[data-payroll-row]')).to_have_count(3)
                expect(page.locator('[data-payroll-totals] dd')).to_have_text(['¥ 3,200.50','¥ 977.32','¥ 450.10'])
                with closing(db.connect(app.db_path)) as conn:
                    assert conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0] == before['vouchers']
                page.screenshot(path=str(OUT/'05-payroll-preview.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Payroll results overflow narrow screen'
                page.set_viewport_size({'width':1440,'height':1000})
                page.locator('[data-payroll-post]').click()
                expect(page.locator('[data-payroll-status]')).to_contain_text('已生成凭证 3 笔')
                page.locator('[data-payroll-preview]').click()
                expect(page.locator('[data-payroll-status]')).to_contain_text('已有凭证 3 笔')
                expect(page.locator('[data-payroll-post]')).to_be_hidden()
                page.locator('[data-payroll-voucher]').first.click()
                expect(page.locator('.cv-view-target')).to_contain_text('工资')
                with closing(db.connect(app.db_path)) as conn:
                    vouchers = conn.execute('SELECT source_type,attachment_count FROM vouchers WHERE client_id=? ORDER BY id',(client['id'],)).fetchall()
                    assert [tuple(row) for row in vouchers] == [('bank_statement',1),('bank_receipt',2),('bank_receipt',1)], vouchers
                assert not errors, errors
                (OUT/'result.json').write_text(json.dumps({'ok':True,'browser_errors':errors,'report_business_data_unchanged':before==after,'payroll_vouchers':len(vouchers),'formats':['xlsx','xls','csv','gb18030 csv','PDF','PNG OCR','scanned PDF']},ensure_ascii=False,indent=2),encoding='utf-8')
                browser.close()
                print('Sales workspace browser checks passed.',flush=True)
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
