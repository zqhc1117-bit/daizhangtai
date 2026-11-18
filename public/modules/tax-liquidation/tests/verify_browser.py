"""Exercise the real company entry and local-only feature with synthetic, temporary books.
Run from project root: python -X utf8 -B public/modules/tax-liquidation/tests/verify_browser.py
Requires developer-only playwright, Chrome and pypdf. Never opens the formal data directory.
"""
from contextlib import closing, suppress
from pathlib import Path
from zipfile import ZipFile
import io
import json
import sys
import tempfile
import threading
import traceback
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tmp' / 'tax-liquidation-dev-deps'))
import db
import finance
import server
server.Handler.log_message = lambda *args: None
from playwright.sync_api import sync_playwright, expect
from pypdf import PdfReader


def main():
    output = ROOT / 'tmp' / 'tax-liquidation-verification'
    output.mkdir(parents=True, exist_ok=True)
    checks = []
    with tempfile.TemporaryDirectory(prefix='tax-liquidation-ui-') as folder:
        app = server.Application(folder, demo=True)
        with closing(db.connect(app.db_path)) as conn, conn:
            for cid, name in [(1, '税务清算验收甲公司'), (2, '税务清算验收乙公司')]:
                conn.execute("INSERT INTO clients(id,name,code,taxpayer_type,standard,start_period,created_at,updated_at,accountant_id) VALUES(?,?,?,'small','small2013','2026-01',?,?,1)", (cid, name, f'LIQ-{cid}', db.now(), db.now()))
                finance.ensure_book(conn, dict(conn.execute('SELECT * FROM clients WHERE id=?', (cid,)).fetchone()))
            token, _ = app.new_session(conn, {'id': 1})
        http = server.make_server('127.0.0.1', 0, app)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{http.server_address[1]}'
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel='chrome', headless=True)
                context = browser.new_context(viewport={'width': 1920, 'height': 1080}, locale='zh-CN', timezone_id='Asia/Shanghai', accept_downloads=True)
                context.add_cookies([{'name': 'ydz_session', 'value': token, 'url': base}])
                context.add_init_script('window.__tlPrints=0;window.print=()=>{window.__tlPrints++};')
                page = context.new_page()
                errors = []
                mutations = []
                external = []
                network = {}
                console_errors = []
                page.on('request', lambda request: network.update({request.url: 'pending'}))
                page.on('requestfinished', lambda request: network.update({request.url: 'finished'}))
                page.on('requestfailed', lambda request: network.update({request.url: request.failure}))
                page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: mutations.append(request.url) if request.method in ['POST', 'PUT', 'DELETE', 'PATCH'] else None)
                page.on('request', lambda request: external.append(request.url) if request.url.startswith('http') and not request.url.startswith(base) else None)
                page.set_default_timeout(15000)
                try:
                    page.goto(base)
                    page.locator('[data-action="company-home"][data-id="1"]').click()
                    page.locator('.cw-side-nav [data-cw-view="tax-liquidation"]').click()
                    expect(page.locator('.tliq-workspace')).to_be_visible()
                    expect(page.locator('[data-tliq-action="mode"]')).to_have_count(2)
                    expect(page.locator('.tliq-report-section')).to_have_count(6)
                    page.locator('[data-tliq-path="paste"]').fill('企业名称：智能提取科技有限公司\n统一社会信用代码：91310000MA1K12345X\n法定代表人：李明\n成立日期：2020年6月8日\n注册资本：12.3456万元人民币')
                    page.locator('[data-tliq-action="extract"]').click()
                    expect(page.locator('[data-tliq-path="company.registeredCapital"]')).to_have_value('123456.00')
                    expect(page.locator('[data-tliq-path="company.established"]')).to_have_value('2020-06-08')
                    page.locator('[data-tliq-path="shareholders.0.paid"]').fill('123.45')
                    page.locator('[data-tliq-step="2"]').first.click()
                    expect(page.locator('[data-tliq-path="quick.paidCapital"]')).to_have_value('123.45')
                    page.locator('[data-tliq-path="quick.cash"]').fill('123.45')
                    expect(page.locator('[data-tliq-live="balance"]')).to_contain_text('已平账')
                    page.locator('[data-tliq-path="quick.paidCapital"]').fill('100.00')
                    page.locator('[data-tliq-path="quick.retainedEarnings"]').fill('23.45')
                    expect(page.locator('[data-tliq-live="balance"]')).to_contain_text('已平账')
                    page.locator('[data-tliq-bool="true"]').check()
                    expect(page.locator('[data-tliq-path="quick.taxPayable"]')).to_be_visible()
                    page.locator('[data-tliq-bool="false"]').check()
                    expect(page.locator('[data-tliq-path="quick.taxPayable"]')).to_have_count(0)
                    checks.append('工商提取、实缴联动、精确平账及欠税条件输入')

                    def sample(mode):
                        page.locator('.tliq-sample-menu summary').click()
                        page.locator(f'[data-tliq-action="sample"][data-tliq-mode="{mode}"]').click()

                    sample('quick')
                    page.locator('[data-tliq-step="4"]').first.click()
                    expect(page.locator('[data-tliq-step="3"]')).to_have_count(0)
                    expect(page.locator('[data-tliq-live="distributions"]')).to_contain_text('27,950.00')
                    page.screenshot(path=str(output / 'quick-desktop.png'))
                    layout = page.evaluate("""() => Object.fromEntries(['.tliq-toolbar','.tliq-title-group','.tliq-local-bar','.tliq-paper h1','.tliq-report th','.tliq-workspace','.cw-content'].map(selector => {const e=document.querySelector(selector),c=getComputedStyle(e),r=e.getBoundingClientRect();return [selector,{whiteSpace:c.whiteSpace,display:c.display,position:c.position,height:r.height,top:r.top,width:r.width,scrollWidth:e.scrollWidth,color:c.color,flexShrink:c.flexShrink}]}))""")
                    (output / 'layout.json').write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding='utf-8')
                    with page.expect_download() as download:
                        page.locator('[data-tliq-action="word"]').click()
                    quick_word = output / 'quick.docx'
                    download.value.save_as(str(quick_word))
                    with ZipFile(quick_word) as archive:
                        xml = archive.read('word/document.xml')
                        doc = ET.fromstring(xml)
                        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                        assert len(doc.findall('.//w:tbl', ns)) >= 5
                        assert doc.find('.//w:cantSplit', ns) is not None
                        assert doc.find('.//w:tblHeader', ns) is not None
                        text = ''.join(doc.itertext())
                        assert '27,950.00' in text and '清算组成员（签字）' in text
                    checks.append('极速模式自动跳步、分配尾差及可编辑Word边框表格/落款')
                    page.locator('[data-tliq-action="print"]').click()
                    assert page.evaluate('window.__tlPrints') == 1
                    page.emulate_media(media='print')
                    expect(page.locator('.tliq-editor')).to_be_hidden()
                    expect(page.locator('.cw-sidebar')).to_be_hidden()
                    expect(page.locator('.tliq-toolbar')).to_be_hidden()
                    expect(page.locator('.tliq-report')).to_be_visible()
                    pdf = page.pdf(path=str(output / 'quick-report.pdf'), prefer_css_page_size=True, print_background=True)
                    pdf_doc = PdfReader(io.BytesIO(pdf))
                    assert all(abs(float(p.mediabox.width) - 595.28) < 2 and abs(float(p.mediabox.height) - 841.89) < 2 for p in pdf_doc.pages)
                    pdf_text = ''.join(''.join(p.extract_text().split()) for p in pdf_doc.pages)
                    assert '剩余财产分配方案' in pdf_text and '清算组成员' in pdf_text
                    assert '税务清算及财产清偿报告生成器' not in pdf_text
                    assert '填充示例数据' not in pdf_text and '工作总览' not in pdf_text
                    page.emulate_media(media='screen')
                    checks.append(f'仅报告打印、A4纵向PDF（{len(pdf_doc.pages)}页）及导航/表单隐藏')

                    sample('full')
                    page.locator('[data-tliq-step="3"]').first.click()
                    expect(page.locator('[data-tliq-path="full.assets.inventory.realized"]')).to_have_value('42000.00')
                    expect(page.locator('[data-tliq-live="gain-inventory"]')).to_contain_text('12,000.00')
                    page.locator('[data-tliq-path="full.debts.otherPayables.method"]').select_option('assumed')
                    page.locator('[data-tliq-path="full.debts.otherPayables.assignee"]').fill('张明')
                    expect(page.locator('.tliq-report')).to_contain_text('尚未确认取得债权人书面同意')
                    page.locator('[data-tliq-path="full.debts.otherPayables.consent"]').check()
                    expect(page.locator('.tliq-report')).to_contain_text('已勾选取得债权人书面同意')
                    page.locator('[data-tliq-path="full.debts.otherPayables.method"]').select_option('partial')
                    page.locator('[data-tliq-step="4"]').first.click()
                    expect(page.locator('[data-tliq-live="income-tax"]')).to_contain_text('4,525.00')
                    page.locator('[data-tliq-action="tax-rate"][data-tliq-rate="manual"]').click()
                    page.locator('[data-tliq-path="settlement.manualTax"]').fill('100.01')
                    expect(page.locator('[data-tliq-live="income-tax"]')).to_contain_text('100.01')
                    page.locator('[data-tliq-action="tax-rate"][data-tliq-rate="25"]').click()
                    page.locator('[data-tliq-path="settlement.taxes.personal"]').uncheck()
                    expect(page.locator('.tliq-report')).to_contain_text('个人所得税尚未勾选结清')
                    page.locator('[data-tliq-path="settlement.taxes.personal"]').check()
                    page.locator('[data-tliq-path="settlement.agencyFee"]').fill('999999')
                    expect(page.locator('.tliq-danger-alert')).to_be_visible()
                    expect(page.locator('[data-tliq-live="distributions"]')).to_contain_text('暂停股东分配')
                    expect(page.locator('.tliq-report')).to_contain_text('禁止向股东分配财产')
                    page.locator('[data-tliq-path="settlement.agencyFee"]').fill('1500.00')
                    expect(page.locator('.tliq-danger-alert')).to_have_count(0)
                    page.locator('.cw-content').evaluate('el=>el.scrollTo(0,0)')
                    checks.append('完整模式变现损益、股东债务承接、税额切换与资不抵债暂停分配')
                    page.screenshot(path=str(output / 'full-desktop.png'))
                    page.set_viewport_size({'width': 1366, 'height': 768})
                    page.screenshot(path=str(output / 'full-1366.png'))
                    rects = page.evaluate("""() => {const a=document.querySelector('.tliq-editor').getBoundingClientRect(),b=document.querySelector('.tliq-preview').getBoundingClientRect();return {ratio:a.width/(a.width+b.width),left:a.left,right:b.left};}""")
                    assert abs(rects['ratio'] - 0.55) < 0.01 and rects['right'] > rects['left']
                    page.set_viewport_size({'width': 800, 'height': 1000})
                    assert page.locator('.tliq-columns').evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ').length") == 1
                    page.screenshot(path=str(output / 'stacked-800.png'))
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.locator('.cw-content').evaluate('el=>el.scrollTo(0,0)')
                    page.screenshot(path=str(output / 'mobile-390.png'))
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.set_viewport_size({'width': 1920, 'height': 1080})
                    checks.append('桌面55/45双栏、800px堆叠与390px页面无横向溢出')

                    page.locator('[data-tliq-step="2"]').first.click()
                    excel = page.evaluate("""async () => {const x=await import('/modules/tax-liquidation/vendor/xlsx.js');const w=x.utils.book_new();x.utils.book_append_sheet(w,x.utils.aoa_to_sheet([['单位：万元'],['项目','行次','期末余额','年初余额','项目','行次','期末数','年初数'],['货币资金',1,8,900,'应付账款',1,2,800],['固定资产原价',2,99,88,'实收资本',2,5,300],['固定资产净值',3,1.8,800,'未分配利润',3,5.2,800]]),'资产负债表');return Array.from(new Uint8Array(x.write(w,{type:'array',bookType:'xlsx'})));}""")
                    assert len(excel) > 1000
                    page.locator('[data-tliq-upload]').set_input_files({'name': '期末余额验收.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'buffer': bytes(excel)})
                    expect(page.locator('[data-tliq-import-message]')).to_contain_text('匹配5项')
                    expect(page.locator('[data-tliq-path="full.balance.cash"]')).to_have_value('80000.00')
                    expect(page.locator('[data-tliq-path="full.balance.fixedAssets"]')).to_have_value('18000.00')
                    checks.append('真实Excel文件上传、并排报表期末列、净值匹配与万元换算')
                    with page.expect_download() as download:
                        page.locator('[data-tliq-action="word"]').click()
                    download.value.save_as(str(output / 'full.docx'))
                    page.emulate_media(media='print')
                    full_pdf = page.pdf(path=str(output / 'full-report.pdf'), prefer_css_page_size=True)
                    full_reader = PdfReader(io.BytesIO(full_pdf))
                    assert all(float(p.mediabox.width) < float(p.mediabox.height) for p in full_reader.pages)
                    page.emulate_media(media='screen')

                    # Reload returns to home; reopening the module restores its company-specific state.
                    page.reload()
                    page.locator('[data-action="company-home"][data-id="1"]').click()
                    page.locator('.cw-side-nav [data-cw-view="tax-liquidation"]').click()
                    expect(page.locator('[data-tliq-path="full.balance.cash"]')).to_have_value('80000.00')
                    page.locator('.cw-company-button').click()
                    page.locator('.cw-company-option[data-cw-company="2"]').click()
                    page.locator('.cw-side-nav [data-cw-view="tax-liquidation"]').click()
                    expect(page.locator('[data-tliq-path="company.name"]')).to_have_value('税务清算验收乙公司')
                    page.locator('[data-tliq-step="2"]').first.click()
                    expect(page.locator('[data-tliq-path="quick.cash"]')).to_have_value('')
                    page.locator('[data-tliq-path="quick.cash"]').fill('88.88')
                    page.locator('[data-tliq-action="reset"]').click()
                    page.locator('[data-tliq-action="confirm-reset"]').click()
                    page.locator('[data-tliq-action="undo"]').click()
                    expect(page.locator('[data-tliq-path="quick.cash"]')).to_have_value('88.88')
                    saved = page.evaluate("Object.fromEntries(Object.keys(localStorage).filter(k=>k.includes('tax-liquidation')).map(k=>[k,JSON.parse(localStorage[k])]))")
                    assert len(saved) == 2
                    assert any(v['full']['balance']['cash'] == '80000.00' for v in saved.values())
                    checks.append('刷新恢复、跨公司独立草稿、清空及撤销')
                    assert not errors, errors
                    assert not mutations, mutations
                    assert not external, external
                    checks.append('浏览器零脚本错误、零后端写入、零外网请求')

                    config = (MODULE / 'modes.config.js').read_text(encoding='utf-8')
                    for only in ['quick', 'full']:
                        locked = browser.new_context(viewport={'width': 1366, 'height': 900})
                        locked.add_cookies([{'name': 'ydz_session', 'value': token, 'url': base}])
                        locked.route('**/modules/tax-liquidation/modes.config.js', lambda route: route.fulfill(status=200, content_type='text/javascript; charset=utf-8', body=config.replace("['quick', 'full']", f"['{only}']", 1)))
                        lp = locked.new_page()
                        lp.goto(base)
                        lp.locator('[data-action="company-home"][data-id="1"]').click()
                        lp.locator('.cw-side-nav [data-cw-view="tax-liquidation"]').click()
                        expect(lp.locator('.tliq-workspace')).to_be_visible()
                        expect(lp.locator('[data-tliq-action="mode"]')).to_have_count(0)
                        expect(lp.locator('[data-tliq-action="sample"]')).to_have_count(1)
                        lp.locator('[data-tliq-step="2"]').first.click()
                        expect(lp.locator('[data-tliq-upload]' if only == 'full' else '[data-tliq-path="quick.cash"]')).to_be_visible()
                        locked.close()
                    checks.append('仅启用极速/仅启用完整：切换器隐藏、表单锁定及案例联动')
                    (output / 'failure.txt').unlink(missing_ok=True)
                    (output / 'failure.png').unlink(missing_ok=True)
                    (output / 'result.json').write_text(json.dumps({'passed': checks, 'errors': errors, 'external_requests': external, 'mutations': mutations}, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(json.dumps({'passed': checks, 'output': str(output)}, ensure_ascii=False, indent=2))
                except BaseException:
                    (output / 'failure.txt').write_text(traceback.format_exc()+'\nBrowser errors: '+repr(errors)+'\nConsole errors: '+repr(console_errors)+'\nNetwork: '+json.dumps(network,ensure_ascii=False), encoding='utf-8')
                    with suppress(Exception):
                        page.screenshot(path=str(output / 'failure.png'))
                    raise
                finally:
                    with suppress(Exception):
                        browser.close()
        finally:
            http.shutdown()
            http.server_close()


if __name__ == '__main__':
    main()
