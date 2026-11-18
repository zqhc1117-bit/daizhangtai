"""Real Chromium interaction against an isolated synthetic local server.

Run: python -X utf8 -B tests/verify_voucher_payroll_browser.py
Requires the developer's Playwright package and installed Chrome. No formal book is used.
"""
from contextlib import closing
from pathlib import Path
import json
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import db
import finance
import server
from playwright.sync_api import sync_playwright,expect


def main():
    output=ROOT/'tmp'/'voucher-payroll-verification';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='voucher-payroll-ui-') as folder:
        app=server.Application(folder,demo=True)
        with closing(db.connect(app.db_path)) as conn,conn:
            conn.execute("INSERT INTO clients(id,name,code,taxpayer_type,standard,start_period,created_at,updated_at,accountant_id) VALUES(1,'凭证工资功能验收公司','UI-CHECK','small','small2013','2026-01',?,?,1)",(db.now(),db.now()))
            finance.ensure_book(conn,dict(conn.execute('SELECT * FROM clients WHERE id=1').fetchone()))
            conn.executemany("INSERT INTO accounts(client_id,code,name,direction,category,report_key) VALUES(1,?,?,'debit','expense','administration')",[(f'5602{i:04d}',f'费用明细{i:03d}') for i in range(1,301)])
            token,_=app.new_session(conn,{'id':1})
        http=server.make_server('127.0.0.1',0,app);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{http.server_address[1]}'
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True)
                context=browser.new_context(viewport={'width':1920,'height':1080},locale='zh-CN',timezone_id='Asia/Shanghai',accept_downloads=True)
                context.add_cookies([{'name':'ydz_session','value':token,'url':base}])
                context.add_init_script("window.__prints=0;window.print=()=>{window.__prints++;window.dispatchEvent(new Event('afterprint'));};")
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.set_default_timeout(10000)
                try:
                    page.goto(base);page.locator('[data-action="company-home"]').first.click()
                    expect(page.locator('.cw-shell')).to_be_visible()
                    page.locator('.cw-side-nav [data-cw-view="reconcile"]').click()
                    expect(page.locator('[data-company-reconcile]')).to_be_visible()
                    page.locator('.cw-tabs [data-cw-view="home"]').first.click()
                    page.locator('[data-cw-new-voucher]').click()
                    expect(page.locator('.ve-page')).to_be_visible()
                    expect(page.locator('.modal-backdrop')).to_have_count(0)
                    expect(page.locator('.voucher-line')).to_have_count(5)
                    page.screenshot(path=str(output/'voucher-empty-1920.png'))
                    page.locator('.ve-form [name="date"]').fill('2026-11-17')
                    page.locator('.ve-form [name="summary"]').fill('本地界面验收办公费')
                    def select_account(row,query,code):
                        search=page.locator(f'[data-ve-row="{row}"] .account-picker-search');search.click();search.fill(query)
                        page.locator(f'[data-account-option="{code}"]').click()
                        expect(page.locator(f'[data-ve-row="{row}"] [data-account-value]')).to_have_value(code)
                    select_account(0,'费用明细300','56020300')
                    page.locator('[data-ve-row="0"] .voucher-debit').fill('1234.56')
                    page.locator('[data-ve-row="1"] .account-picker-search').fill('1002')
                    page.locator('[data-ve-row="1"] .account-picker-search').press('Enter')
                    expect(page.locator('[data-ve-row="1"] [data-account-value]')).to_have_value('1002')
                    page.locator('[data-ve-row="1"] .voucher-credit').fill('1200')
                    page.locator('[data-ve-action="save"]').click()
                    expect(page.locator('[data-ve-error]')).to_contain_text('不平衡')
                    page.locator('[data-ve-row="1"] .voucher-credit').fill('1234.56')
                    page.locator('[data-ve-row="1"] .voucher-cf').select_option('operating')
                    page.locator('[data-ve-row="2"] .account-picker-search').click();page.locator('[data-account-create]').click()
                    page.locator('#app-modal-form [name="parent_code"]').select_option('1122');page.locator('#app-modal-form [name="name"]').fill('验收新增科目');page.locator('#app-modal-form [type="submit"]').click()
                    expect(page.locator('.modal-backdrop')).to_have_count(0);expect(page.locator('.ve-form [name="summary"]')).to_have_value('本地界面验收办公费')
                    select_account(2,'验收新增科目','112201');page.locator('[data-ve-row="2"] [data-ve-row-action="remove"]').click()
                    page.locator('[data-ve-action="add"]').click();expect(page.locator('.voucher-line')).to_have_count(5)
                    page.locator('[data-ve-row="0"] [data-ve-row-action="copy"]').click();expect(page.locator('.voucher-line')).to_have_count(6)
                    page.locator('[data-ve-row="1"] [data-ve-row-action="remove"]').click();expect(page.locator('.voucher-line')).to_have_count(5)
                    page.locator('[data-ve-upload]').set_input_files({'name':'界面验收附件.txt','mimeType':'text/plain','buffer':b'Synthetic UI check attachment.'})
                    expect(page.locator('.ve-file-list')).to_contain_text('界面验收附件.txt')
                    page.locator('.ve-form [name="attachment_count"]').fill('1')
                    page.locator('.ve-template-menu summary').click();page.locator('[data-ve-action="save-template"]').click()
                    page.locator('#app-modal-form [name="name"]').fill('验收费用模板');page.locator('#app-modal-form [type="submit"]').click()
                    expect(page.locator('.modal-backdrop')).to_have_count(0)
                    page.locator('[data-ve-action="preview"]').click();expect(page.locator('.cv-slip')).to_contain_text('本地界面验收办公费');page.locator('.modal-footer [data-close]').click()
                    page.screenshot(path=str(output/'voucher-filled-1920.png'))
                    page.set_viewport_size({'width':1366,'height':768});page.screenshot(path=str(output/'voucher-filled-1366.png'));page.set_viewport_size({'width':1920,'height':1080})
                    page.locator('[data-ve-action="save-new"]').click();expect(page.locator('.ve-form [name="summary"]')).to_have_value('')
                    with closing(db.connect(app.db_path)) as conn:
                        assert conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0]==1
                        assert conn.execute('SELECT COUNT(*) FROM voucher_attachments').fetchone()[0]==1
                    page.locator('.ve-template-menu summary').click();page.locator('[data-ve-action="templates"]').click();page.locator('#app-modal-form [type="submit"]').click()
                    expect(page.locator('.ve-form [name="summary"]')).to_have_value('本地界面验收办公费')
                    # Save across a year boundary and print the committed voucher.
                    page.locator('.ve-form [name="date"]').fill('2027-01-05')
                    def reject_write(route):route.fulfill(status=409,content_type='application/json',body='{"error":"合成写入失败，请重试"}')
                    page.route('**/api/books/1/vouchers',reject_write)
                    page.locator('[data-ve-action="save"]').click();expect(page.locator('[data-ve-error]')).to_contain_text('合成写入失败')
                    expect(page.locator('.ve-form [name="summary"]')).to_have_value('本地界面验收办公费');page.unroute('**/api/books/1/vouchers',reject_write)
                    page.locator('[data-ve-action="save-print"]').click();expect(page.locator('.cv-root')).to_be_visible()
                    expect(page.locator('[data-cw-period]')).to_have_value('2027-01')
                    expect(page.locator('#modal-title')).to_have_text('凭证打印设置')
                    page.locator('#app-modal-form [type="submit"]').click()
                    expect(page.locator('.modal-backdrop')).to_have_count(0)
                    assert page.evaluate('window.__prints')==1
                    page.locator('[data-cw-period]').fill('2026-11');page.locator('[data-cw-period]').dispatch_event('change')
                    expect(page.locator('[data-cv-voucher]')).to_have_count(1)
                    page.locator('[data-book-action="edit-voucher"]').first.click();expect(page.locator('.ve-page')).to_be_visible()
                    expect(page.locator('.ve-file-list')).to_contain_text('界面验收附件.txt')
                    page.locator('.ve-form [name="summary"]').fill('修改后的办公费')
                    page.locator('[data-ve-action="save"]').click();expect(page.locator('.cv-root')).to_contain_text('修改后的办公费')
                    page.locator('[data-book-action="voucher-sources"]').click();expect(page.locator('.sa-files')).to_contain_text('界面验收附件.txt');page.locator('.modal-footer [data-close]').click()
                    if '--voucher-only' in sys.argv:
                        assert not errors,errors
                        print(json.dumps({'passed':True,'checks':['voucher creation','editing','attachments','templates','save and print settings','cross-year navigation'],'browser_errors':errors},ensure_ascii=False))
                        return
                    page.locator('.cw-side-nav [data-cw-view="payroll"]').click();expect(page.locator('.payroll-page')).to_be_visible()
                    page.locator('[data-book-action="new-payroll"]').click()
                    f=page.locator('#app-modal-form');f.locator('[name="employee_name"]').fill('验收人员甲');f.locator('[name="employee_code"]').fill('E001');f.locator('[name="department"]').fill('行政');f.locator('[name="position"]').fill('助理');f.locator('[name="gross"]').fill('8000');f.locator('[name="withheld_tax"]').fill('100');f.locator('[name="social"]').fill('800');f.locator('[name="other_deduction"]').fill('50')
                    f.locator('.account-picker-search').fill('费用明细300');page.locator('[data-account-option="56020300"]').click()
                    expect(f.locator('[data-py-net]')).to_have_text('¥7,050.00');f.locator('[type="submit"]').click()
                    expect(page.locator('.payroll-page')).to_contain_text('验收人员甲')
                    page.screenshot(path=str(output/'payroll-1920.png'))
                    page.locator('[data-book-action="edit-payroll"]').first.click();page.locator('#app-modal-form [name="gross"]').fill('8500');page.locator('#app-modal-form [name="note"]').fill('本月调整');page.locator('#app-modal-form [type="submit"]').click()
                    page.locator('[data-book-action="view-payslip"]').first.click();expect(page.locator('.payroll-slip')).to_contain_text('7,550.00')
                    page.screenshot(path=str(output/'payslip-1920.png'))
                    page.locator('[data-py-print]').click();assert page.evaluate('window.__prints')==2
                    with page.expect_download() as downloaded:page.locator('[data-py-export]').click()
                    assert downloaded.value.suggested_filename.endswith('_工资表.xlsx')
                    page.locator('.modal-footer [data-close]').click()
                    page.locator('[data-py-period="2026-12"]').click();expect(page.locator('.payroll-metrics')).to_contain_text('0 人')
                    page.locator('[data-book-action="carry-payroll"]').click();expect(page.locator('.payroll-carry-list')).to_contain_text('验收人员甲');page.locator('#app-modal-form [type="submit"]').click()
                    expect(page.locator('.payroll-page')).to_contain_text('验收人员甲');expect(page.locator('.payroll-page')).to_contain_text('7,550.00')
                    page.locator('[data-book-action="carry-payroll"]').click();page.locator('#app-modal-form [type="submit"]').click();expect(page.locator('[data-book-action="view-payslip"]')).to_have_count(1)
                    page.locator('[data-book-action="edit-payroll"]').first.click();page.locator('#app-modal-form [name="gross"]').fill('9000');page.locator('#app-modal-form [type="submit"]').click();expect(page.locator('.payroll-page')).to_contain_text('8,050.00')
                    page.set_viewport_size({'width':1366,'height':768});page.screenshot(path=str(output/'payroll-1366.png'))
                    page.locator('[data-book-action="delete-payroll"]').first.click();page.locator('#app-modal-form [type="submit"]').click();expect(page.locator('[data-book-action="view-payslip"]')).to_have_count(0)
                    page.locator('[data-py-period="2026-11"]').click();expect(page.locator('.payroll-page')).to_contain_text('7,550.00')
                    page.locator('[data-book-maintenance-filter]').fill('不存在的人员');expect(page.locator('[data-book-action="view-payslip"]')).to_have_count(0)
                    page.locator('[data-book-maintenance-filter]').fill('验收人员甲');expect(page.locator('[data-book-action="view-payslip"]')).to_have_count(1)
                    # Re-read saved records, independent of page-memory state.
                    with closing(db.connect(app.db_path)) as conn:
                        assert conn.execute("SELECT COUNT(*) FROM payroll WHERE period='2026-11'").fetchone()[0]==1
                        assert conn.execute("SELECT COUNT(*) FROM payroll WHERE period='2026-12'").fetchone()[0]==0
                        assert conn.execute('SELECT COUNT(*) FROM vouchers').fetchone()[0]==2
                    assert not errors,errors
                    result={'passed':True,'browser_errors':errors,'screenshots':str(output),'checks':['direct source navigation','full-page voucher','300-account search','balance validation','row operations','attachment persistence','templates','save and new','cross-year save and print','voucher editing','source attachments','payroll add/edit','payslip preview/print/export','carry-forward deduplication','current month deletion leaves history','1920 and 1366 layouts']}
                    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
                except Exception:
                    page.screenshot(path=str(output/'failure.png'));(output/'failure.html').write_text(page.content(),encoding='utf-8');print('Browser errors:',errors);raise
                finally:browser.close()
        finally:http.shutdown();http.server_close();thread.join(timeout=3)


if __name__=='__main__':main()
