"""Exercise upload, exact matching, persistent exports and month changes in Chrome."""
from contextlib import closing
import io
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import db
import finance
import payroll_master as pm
import server
from test_payroll_master import COMPANY, PERSON, fixture
from openpyxl import load_workbook
from playwright.sync_api import sync_playwright, expect


def main():
    output = ROOT/'tmp'/'payroll-master-verification'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='payroll-master-browser-') as temp:
        app = server.Application(temp, demo=True)
        with closing(db.connect(app.db_path)) as conn, conn:
            conn.execute('INSERT INTO clients(id,name,code,taxpayer_type,standard,start_period,created_at,updated_at,accountant_id) VALUES(1,?,?,?,?,?,?,?,1)', (COMPANY,'MASTER-UI','small','small2013','2026-01',db.now(),db.now()))
            finance.ensure_book(conn,dict(conn.execute('SELECT * FROM clients WHERE id=1').fetchone()))
            for period in ('2026-11','2026-12','2027-01'):
                finance.handle(conn,'POST','/api/books/1/payroll',{**PERSON,'period':period,'expense_account':'5602'},{'id':1,'username':'demo1'},{})
            token,_ = app.new_session(conn,{'id':1})
        http=server.make_server('127.0.0.1',0,app)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{http.server_address[1]}'
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True)
                context=browser.new_context(viewport={'width':1366,'height':768},locale='zh-CN',accept_downloads=True)
                context.add_cookies([{'name':'ydz_session','value':token,'url':base}])
                page=context.new_page();errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_default_timeout(15000)
                try:
                    page.goto(base)
                    page.locator('[data-action="company-home"]').first.click()
                    page.locator('.cw-side-nav [data-cw-view="payroll"]').click()
                    page.locator('[data-py-month]').fill('2026-11');page.locator('[data-py-month]').dispatch_event('change')
                    expect(page.locator('[data-book-action="view-payslip"]')).to_have_count(1)
                    page.locator('[data-book-action="export-payroll"]').click()
                    form=page.locator('#app-modal-form')
                    expect(form.locator('[type="submit"]')).to_be_disabled()
                    form.locator('[name="master_file"]').set_input_files({'name':'合成工资大表.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':fixture()})
                    expect(form.locator('[name="master_sheet"]')).to_have_value('工资表')
                    expect(form.locator('[data-master-status]')).to_contain_text('已按公司全称匹配')
                    expect(form.locator('[type="submit"]')).to_be_enabled()
                    page.locator('.modal').screenshot(path=str(output/'工资大表导出窗口.png'))
                    with page.expect_download() as event: form.locator('[type="submit"]').click()
                    destination=output/'synthetic-master.xlsx';event.value.save_as(destination)
                    expect(page.locator('.modal')).to_have_count(0)
                    assert event.value.suggested_filename=='记账户工资大表.xlsx'
                    book=load_workbook(destination,data_only=True)
                    assert book.active['K15'].value==5905.64
                    page.locator('[data-book-action="export-payroll"]').click()
                    expect(form.locator('[data-master-status]')).to_contain_text('更新该月份原有工资')
                    with page.expect_download() as event: form.locator('[type="submit"]').click()
                    event.value.save_as(destination)
                    assert len(pm.MasterWorkbook(destination.read_bytes()).sheets[0]['blocks'])==2
                    for period in ('2026-12','2027-01'):
                        page.locator('[data-py-month]').fill(period);page.locator('[data-py-month]').dispatch_event('change')
                        expect(page.locator('[data-py-month]')).to_have_value(period)
                        # Wait for the selected period's book request before exporting.
                        expect(page.locator('[data-py-period]')).to_have_count(2)
                        page.locator('[data-book-action="export-payroll"]').click()
                        expect(form).to_contain_text(period)
                        with page.expect_download() as event: form.locator('[type="submit"]').click()
                        event.value.save_as(destination)
                    assert [b['period'] for b in pm.MasterWorkbook(destination.read_bytes()).sheets[0]['blocks']]==['2026-10','2026-11','2026-12','2027-01']
                    page.locator('[data-book-action="export-payroll"]').click()
                    form.locator('[name="master_file"]').set_input_files({'name':'broken.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':b'bad file'})
                    expect(form.locator('[data-master-error]')).to_contain_text('无法读取')
                    expect(form.locator('[type="submit"]')).to_be_disabled()
                    form.locator('[name="master_file"]').set_input_files([])
                    expect(form.locator('[type="submit"]')).to_be_enabled()
                    assert not errors,errors
                    result={'passed':True,'checks':['upload and exact company match','1366 layout','full workbook download','persisted master reused','same month replaces once','December and next January retained','invalid upload blocks export','original master restored after clearing file'],'browser_errors':errors}
                    (output/'browser-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                    print(json.dumps(result,ensure_ascii=False))
                except Exception:
                    page.screenshot(path=str(output/'browser-failure.png'));raise
                finally:
                    browser.close()
        finally:
            http.shutdown();http.server_close();thread.join(timeout=3)


if __name__=='__main__':main()
