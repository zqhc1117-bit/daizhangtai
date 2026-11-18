"""Exercise the reference payroll format in Chromium with temporary synthetic companies."""
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import db
import finance
import server
from openpyxl import load_workbook
from playwright.sync_api import sync_playwright,expect


def main():
    output=ROOT/'tmp'/'payroll-template-verification';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='payroll-template-ui-') as folder:
        app=server.Application(folder,demo=True)
        with closing(db.connect(app.db_path)) as conn,conn:
            for cid,name in ((1,'工资格式验收有限公司'),(2,'隔离人员验收有限公司')):
                conn.execute('INSERT INTO clients(id,name,code,taxpayer_type,standard,start_period,created_at,updated_at,accountant_id) VALUES(?,?,?,?,?,?,?,?,1)',(cid,name,f'FORMAT-{cid}','small','small2013','2026-01',db.now(),db.now()))
                finance.ensure_book(conn,dict(conn.execute('SELECT * FROM clients WHERE id=?',(cid,)).fetchone()))
            for index in range(1,4):
                finance.handle(conn,'POST','/api/books/1/payroll',{'period':'2026-11','employee_name':f'样例人员{index}','gross_fen':500000,'pension_fen':58160,'medical_fen':14840,'unemployment_fen':3635,'housing_fund_fen':15000,'expense_account':'5602'},{'id':1,'username':'admin'},{})
            token,_=app.new_session(conn,{'id':1})
        http=server.make_server('127.0.0.1',0,app);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{http.server_address[1]}'
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True)
                context=browser.new_context(viewport={'width':1920,'height':1080},locale='zh-CN',timezone_id='Asia/Shanghai',accept_downloads=True)
                context.add_cookies([{'name':'ydz_session','value':token,'url':base}])
                context.add_init_script("window.__printText='';window.print=()=>{window.__printText=document.querySelector('.payroll-print-root').innerText;window.dispatchEvent(new Event('afterprint'));};")
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)));page.set_default_timeout(10000)
                try:
                    page.goto(base);page.locator('[data-action="company-home"]').first.click()
                    page.locator('.cw-side-nav [data-cw-view="payroll"]').click()
                    page.locator('[data-py-month]').fill('2026-11');page.locator('[data-py-month]').dispatch_event('change')
                    expect(page.locator('[data-book-action="edit-payroll"]')).to_have_count(3)
                    page.locator('[data-book-action="new-payroll"]').click()
                    form=page.locator('#app-modal-form')
                    for key,value in {'employee_name':'样例人员4','gross':'7000','pension':'581.60','medical':'148.40','unemployment':'36.35','housing_fund':'300','withheld_tax':'28.01'}.items():form.locator(f'[name="{key}"]').fill(value)
                    expect(form.locator('[name="social"]')).to_have_value('766.35')
                    expect(form.locator('[data-py-net]')).to_have_text('¥5,905.64')
                    form.screenshot(path=str(output/'添加人员及明细.png'))
                    form.locator('[type="submit"]').click()
                    expect(page.locator('[data-book-action="view-payslip"]')).to_have_count(4)
                    page.locator('[data-book-action="payroll-slips"]').click()
                    report=page.locator('.payroll-report')
                    expect(report.locator('h2')).to_have_text('工资格式验收有限公司工资表')
                    expect(report.locator('.payroll-report-period')).to_have_text('2026年11月')
                    expect(report.locator('thead tr')).to_have_count(2)
                    expect(report.locator('tbody tr')).to_have_count(5)
                    expect(report.locator('.payroll-report-total')).to_contain_text('18,156.59')
                    report.screenshot(path=str(output/'工资表-2026-11.png'))
                    with page.expect_download() as downloaded:page.locator('[data-py-export]').click()
                    november=output/'工资表-2026-11.xlsx';downloaded.value.save_as(november)
                    assert downloaded.value.suggested_filename=='工资格式验收有限公司_2026-11_工资表.xlsx'
                    with closing(load_workbook(november,data_only=True)) as exported:
                        assert exported.active['A2'].value=='2026年11月'
                        assert exported.active['K10'].value==18156.59
                    page.locator('[data-py-print]').click()
                    assert '2026年11月' in page.evaluate('window.__printText')
                    assert page.locator('.payroll-print-root').count()==0
                    page.set_viewport_size({'width':1366,'height':768})
                    report.screenshot(path=str(output/'工资表-1366.png'))
                    assert report.locator('.payroll-report-scroll').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
                    page.locator('.modal-footer [data-close]').click()
                    page.locator('[data-py-period="2026-12"]').click()
                    expect(page.locator('[data-book-action="view-payslip"]')).to_have_count(0)
                    expect(page.locator('[data-book-action="export-payroll"]')).to_be_disabled()
                    page.locator('[data-book-action="carry-payroll"]').click()
                    expect(form.locator('input[type="checkbox"]')).to_have_count(0)
                    expect(form.locator('.payroll-carry-option')).to_have_text('基本工资（工资总额）、社保、公积金和其他扣款全部沿用上月，个税清零后按本月实际金额填写。')
                    form.screenshot(path=str(output/'带入上月人员.png'))
                    form.locator('[type="submit"]').click()
                    expect(page.locator('[data-book-action="view-payslip"]')).to_have_count(4)
                    page.locator('[data-book-action="payroll-slips"]').click()
                    expect(report.locator('.payroll-report-period')).to_have_text('2026年12月')
                    report.screenshot(path=str(output/'工资表-2026-12.png'))
                    with page.expect_download() as downloaded:page.locator('[data-py-export]').click()
                    december=output/'工资表-2026-12.xlsx';downloaded.value.save_as(december)
                    assert downloaded.value.suggested_filename=='工资格式验收有限公司_2026-12_工资表.xlsx'
                    with closing(load_workbook(december,data_only=True)) as exported:
                        assert exported.active['A2'].value=='2026年12月'
                        assert all(exported.active.cell(row,10).value is None for row in range(6,10))
                        assert exported.active['J10'].value=='－'
                        assert exported.active['K10'].value==18184.6
                    page.locator('.modal-footer [data-close]').click()
                    page.locator('tr').filter(has_text='样例人员4').locator('[data-book-action="edit-payroll"]').click()
                    for key,value in {'gross':'7000.00','pension':'581.60','medical':'148.40','unemployment':'36.35','social':'766.35','housing_fund':'300.00','withheld_tax':'0.00','other_deduction':'0.00'}.items():expect(form.locator(f'[name="{key}"]')).to_have_value(value)
                    form.locator('[name="gross"]').fill('7500');form.locator('[type="submit"]').click()
                    expect(page.locator('.payroll-page')).to_contain_text('6,433.65')
                    page.locator('[data-py-period="2026-11"]').click()
                    expect(page.locator('.payroll-page')).to_contain_text('5,905.64')
                    page.locator('[data-cw-action="companies"]').click();page.locator('[data-cw-company="2"]').click()
                    page.locator('.cw-side-nav [data-cw-view="payroll"]').click()
                    expect(page.locator('[data-book-action="view-payslip"]')).to_have_count(0)
                    expect(page.locator('.payroll-page')).not_to_contain_text('样例人员')
                    assert not errors,errors
                    result={'passed':True,'browser_errors':errors,'checks':['company-scoped personnel','add social details and housing fund','live net salary','two-row template header','all four personnel totals','XLSX download and independent reader','November to December period and filename','print date','1366 layout','carry dialog has no reset option','carry details forward and reset only tax','recalculate carried net salary and export','December edit preserves November'],'output':str(output)}
                    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
                except Exception:
                    page.screenshot(path=str(output/'failure.png'));raise
                finally:browser.close()
        finally:http.shutdown();http.server_close();thread.join(timeout=3)


if __name__=='__main__':main()
