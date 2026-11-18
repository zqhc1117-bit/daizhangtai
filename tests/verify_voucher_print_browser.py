"""Exercise print controls against an isolated synthetic book, never a real printer."""
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
    output=ROOT/'tmp'/'voucher-print-verification';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='voucher-print-ui-') as folder:
        app=server.Application(folder,demo=True)
        with closing(db.connect(app.db_path)) as conn,conn:
            conn.execute("INSERT INTO clients(id,name,code,taxpayer_type,standard,start_period,created_at,updated_at,accountant_id) VALUES(1,'凭证打印功能验收公司','PRINT-CHECK','small','small2013','2026-01',?,?,1)",(db.now(),db.now()))
            finance.ensure_book(conn,dict(conn.execute('SELECT * FROM clients WHERE id=1').fetchone()))
            user={'id':1,'username':'admin','name':'合成会计'}
            for i in range(3):
                finance._insert_voucher(conn,1,'2026-11-17','合成打印凭证',finance.with_account_summaries(conn,1,[finance._entry('5602',750000+i),finance._entry('1002',credit=750000+i)]),user)
            long=[dict(finance._entry('5602',10000),summary=f'管理费用第{i+1}行') for i in range(12)]+[dict(finance._entry('1002',credit=120000),summary='银行存款最后一行')]
            finance._insert_voucher(conn,1,'2026-11-17','长凭证',long,user)
            token,_=app.new_session(conn,{'id':1})
        http=server.make_server('127.0.0.1',0,app);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{http.server_address[1]}'
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True)
                context=browser.new_context(viewport={'width':1366,'height':900},locale='zh-CN',timezone_id='Asia/Shanghai')
                context.add_cookies([{'name':'ydz_session','value':token,'url':base}])
                context.add_init_script('window.__prints=0;window.print=()=>{window.__prints++};')
                page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_default_timeout(12000)
                page.goto(base);page.locator('[data-action="company-home"]').first.click()
                expect(page.locator('.cw-shell')).to_be_visible()
                page.locator('[data-cw-vouchers="2026-11"]').first.click()
                expect(page.locator('[data-cv-voucher]')).to_have_count(4)
                title=page.title()
                def settings(ids):
                    page.locator('.cv-selection-bar [data-cv-do="select-none"]').click() if page.locator('.cv-selection-bar').count() else None
                    for ident in ids:page.locator(f'[data-cv-row="{ident}"]').check()
                    page.locator('[data-cv-do="print"]').click()
                    expect(page.locator('#modal-title')).to_have_text('凭证打印设置')
                    return page.locator('#app-modal-form')
                def capture(form,name,pages,slips):
                    form.locator('[type="submit"]').click()
                    expect(page.locator('#modal-root .modal')).to_have_count(0)
                    expect(page.locator('#cv-print-root .vp-sheet')).to_have_count(pages)
                    expect(page.locator('#cv-print-root .cv-slip')).to_have_count(slips)
                    assert page.title()==''
                    page.emulate_media(media='print')
                    bounds=page.locator('#cv-print-root .vp-slot').evaluate_all('els=>els.map(el=>{const a=el.getBoundingClientRect(),b=el.firstElementChild.getBoundingClientRect();return {slot:a.height,content:b.height,bottom:b.bottom,limit:a.bottom}})')
                    assert all(r['content']<=r['slot']+1 and r['bottom']<=r['limit']+1 for r in bounds),bounds
                    if name=='a4-two':page.locator('#cv-print-root .vp-sheet').first.screenshot(path=str(output/'a4-two.png'))
                    page.pdf(path=str(output/(name+'.pdf')),prefer_css_page_size=True,display_header_footer=True,print_background=True)
                    page.evaluate("window.dispatchEvent(new Event('afterprint'))")
                    page.emulate_media(media='screen')
                    assert page.title()==title
                    expect(page.locator('#cv-print-root')).to_have_count(0)
                form=settings([1,2]);expect(form.locator('[name="layout"][value="a4-two"]')).to_be_checked()
                expect(form.locator('[data-vp-count]')).to_contain_text('2 张凭证 · 2 个凭证版面 · 1 张纸')
                page.screenshot(path=str(output/'print-settings.png'))
                capture(form,'a4-two',1,2)
                form=settings([1,2,3]);capture(form,'a4-odd',2,3)
                form=settings([1,2,3]);form.locator('[name="layout"][value="a4-three"]').check();capture(form,'a4-three',1,3)
                form=settings([1,2]);expect(form.locator('[name="layout"][value="a4-three"]')).to_be_checked()
                form.locator('[name="layout"][value="a4-one"]').check();form.locator('[name="hideCode"]').check();form.locator('[name="preparer"]').uncheck();capture(form,'a4-one-hidden',2,2)
                form=settings([4]);form.locator('[name="layout"][value="a4-two"]').check();capture(form,'long-voucher',2,3)
                form=settings([1]);form.locator('[name="layout"][value="voucher-24x14-digits"]').check();capture(form,'custom-digits',1,1)
                form=settings([1]);form.locator('[name="layout"][value="a4-three"]').check();form.locator('[name="top"]').fill('30');form.locator('[name="bottom"]').fill('30')
                expect(form.locator('[data-vp-count]')).to_contain_text('边距过大');form.locator('[type="submit"]').click();expect(page.locator('.modal-error')).to_contain_text('边距过大')
                form.locator('[name="top"]').fill('8');form.locator('[name="bottom"]').fill('8');form.locator('[name="layout"][value="a4-two"]').check()
                form.locator('[name="range"][value="all"]').check();capture(form,'all-vouchers',3,6)
                page.locator('[data-cv-voucher="2"] .cv-meta').hover()
                page.locator('[data-cv-do="print-one"][data-id="2"]').click();expect(page.locator('[name="range"][value="selected"]')).to_be_checked()
                expect(page.locator('[data-vp-count]')).to_contain_text('1 张凭证');page.locator('.modal-footer [data-close]').click()
                # Extremely long summaries are kept in full; an undersized layout
                # is rejected before any text can be cut off by the paper edge.
                page.evaluate('''async()=>{
                    const {printVoucherSheets}=await import('/voucher-print.js');
                    const v={id:99,number:99,date:'2026-11-17',summary:'长摘要',lines:Array.from({length:5},(_,i)=>({account_code:'5602',account_name:'管理费用',summary:'费用'.repeat(100),debit_fen:i?0:10000,credit_fen:i===1?10000:0}))};
                    window.__verbose={v,book:{client:{name:'长摘要打印测试'}}};
                    printVoucherSheets([v],window.__verbose.book,{layout:'a4-two'});
                }''')
                expect(page.locator('#cv-print-root .cv-slip')).to_have_count(5)
                page.evaluate("window.dispatchEvent(new Event('afterprint'))")
                result=page.evaluate('''async()=>{try{const {printVoucherSheets}=await import('/voucher-print.js');printVoucherSheets([window.__verbose.v],window.__verbose.book,{layout:'a4-three'});return '';}catch(e){return e.message;}}''')
                assert '完整容纳' in result,result
                assert page.title()==title
                expect(page.locator('#cv-print-root')).to_have_count(0)
                assert not errors,errors
                print(json.dumps({'result':'passed','print_calls':page.evaluate('window.__prints'),'outputs':str(output)},ensure_ascii=False))
                browser.close()
        finally:http.shutdown();http.server_close();thread.join(timeout=5)


if __name__=='__main__':main()
