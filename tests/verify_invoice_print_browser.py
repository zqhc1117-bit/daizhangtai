"""Exercise the company sidebar, real uploads and physical PDF pagination without a printer."""
import argparse
from contextlib import closing
import io
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import zipfile

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / 'runtime/python/Lib/site-packages'))
import db
import server
from test_invoice_print import phone_screenshot
from pypdf import PdfReader
import pypdfium2


def sample(number):
    image = Image.new('RGB', (1100, 655), 'white')
    draw = ImageDraw.Draw(image)
    fonts = Path(os.environ.get('WINDIR', '')) / 'Fonts'
    def label(x, y, text, size=20, fill='#24282b'):
        font = ImageFont.truetype(str(fonts / 'simsun.ttc'), size) if (fonts / 'simsun.ttc').exists() else ImageFont.load_default(size=size)
        draw.text((x, y), text, font=font, fill=fill)
    label(318, 18, '电子发票（普通发票）', 39)
    label(822, 26, f'发票号码  TEST-{number:04}')
    label(822, 59, '开票日期  2026-11-18')
    label(45, 28, '测试样票', 25)
    draw.ellipse((461, 7, 606, 77), outline='#aa403c', width=3)
    draw.rectangle((24, 105, 1075, 589), outline='#363a3c', width=2)
    for y in (188, 415, 473):
        draw.line((24, y, 1075, y), fill='#363a3c', width=2)
    draw.line((545, 105, 545, 188), fill='#363a3c', width=2)
    label(42, 132, '购买方名称　　打印验收公司')
    label(571, 132, '销售方名称　　合成供应商')
    label(43, 202, '项目名称　　　　　　单位　　　数量　　　　单价　　　　金额　　　　税额')
    label(43, 252, f'测试服务 {number}　　　　 项　　　　1　　　 100.00　　　100.00　　　0.00')
    label(42, 433, '价税合计（大写）　壹佰元整　　　　　　　　　（小写） ¥100.00')
    label(42, 500, '备注')
    label(48, 605, f'开票人　测试员 {number}')
    return image


def assert_sheets(content, counts):
    reader = PdfReader(io.BytesIO(content))
    assert len(reader.pages) == len(counts)
    with pypdfium2.PdfDocument(content) as doc:
        for page, occupied, i in zip(reader.pages, counts, range(len(counts))):
            assert abs(float(page.mediabox.width) - 595.28) < 1
            assert abs(float(page.mediabox.height) - 841.89) < 1
            assert not page.extract_text().strip(), 'Unexpected UI text, browser title, URL or date'
            with closing(doc[i]) as rendered, closing(rendered.render(scale=1)) as bitmap:
                mask = bitmap.to_pil().convert('L').point(lambda p: 255 if p < 210 else 0)
                for half in range(2):
                    slot = mask.crop((0, half * mask.height // 2, mask.width, (half + 1) * mask.height // 2))
                    box = slot.getbbox()
                    if half >= occupied:
                        assert box is None, 'Odd page must have a blank lower half'
                        continue
                    assert box is not None
                    l, t, r, b = box
                    assert .81 < (r - l) / slot.width < .88
                    assert abs((l + r) / 2 - slot.width / 2) < 5
                    assert abs((t + b) / 2 - slot.height / 2) < 6


def print_from_workbench(page, button):
    page.evaluate("delete document.body.dataset.testPrinted")
    button.click()
    expect(page.locator('body')).to_have_attribute('data-test-printed', 'yes', timeout=20000)
    return page.pdf(prefer_css_page_size=True, display_header_footer=True, print_background=True)


def verify(folder, output):
    app = server.Application(folder / 'db', demo=True)
    with closing(db.connect(app.db_path)) as conn, conn:
        for n in (1, 2):
            conn.execute("INSERT INTO clients(id,name,code,taxpayer_type,standard,start_period,created_at,updated_at,accountant_id) VALUES(?,?,?,'small','small2013','2026-01',?,?,1)",
                         (n, f'打印验收公司{n}', f'PRINT-{n}', db.now(), db.now()))
        token, _ = app.new_session(conn, {'id': 1})
    # A common invoice PDF has the ticket at the top of an otherwise white A4 page.
    tall = Image.new('RGB', (1240, 1754), 'white')
    tall.paste(sample(1), (70, 50))
    pdf = io.BytesIO()
    tall.save(pdf, 'PDF', save_all=True, append_images=[sample(2)], resolution=150)
    archive = folder / '本月票据.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('采购/发票.pdf', pdf.getvalue())
        z.writestr('采购/说明.xml', b'<description/>')
    photo = folder / '发票手机截图.png'
    phone_screenshot(sample(3)).save(photo)
    broken = folder / '损坏发票.pdf'
    broken.write_bytes(b'not a PDF')
    standalone = folder / '单独两页.pdf'
    standalone.write_bytes(pdf.getvalue())
    http = server.make_server('127.0.0.1', 0, app)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{http.server_port}'
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(viewport={'width': 1366, 'height': 900}, locale='zh-CN')
            context.add_cookies([{'name': 'ydz_session', 'value': token, 'url': base}])
            context.add_init_script("window.print=()=>{document.body.dataset.testPrinted='yes'}")
            page = context.new_page()
            errors, asset_requests = [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda request: asset_requests.append(request.url)
                    if '/invoice-print/page?' in request.url else None)
            page.goto(base)
            page.locator('[data-action="company-home"]').first.click()
            entry = page.locator('.cw-side-nav [data-cw-view="invoice-print"]')
            entry.click()
            picker = page.locator('[data-ip-input]')
            rows = page.locator('[data-ip-index]')
            button = lambda key: page.locator(f'[data-ip-action="{key}"]')
            expect(button('print')).to_be_disabled()
            picker.set_input_files(str(archive))
            expect(rows).to_have_count(2, timeout=20000)
            expect(page.locator('[data-ip-skipped]')).to_contain_text('说明.xml')
            expect(rows.nth(0)).to_contain_text('第 1/2 页')
            expect(rows.nth(1)).to_contain_text('第 2/2 页')
            first_preview = page.locator('.ip-current img').first
            expect(first_preview).to_have_attribute('src', re.compile(r'&size=preview$'))
            first_url = base + first_preview.get_attribute('src')
            assert context.request.get(first_url).ok
            unauthorized = browser.new_context()
            assert unauthorized.request.get(first_url).status == 401
            unauthorized.close()
            assert context.request.get(first_url.replace('/books/1/', '/books/2/')).status == 404
            assert all('size=preview' in url for url in asset_requests)
            with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/invoice-print')) as upload_request:
                picker.set_input_files([str(photo), str(broken)])
            assert 'multipart/form-data' in upload_request.value.headers['content-type']
            expect(rows).to_have_count(3, timeout=20000)
            expect(page.locator('[data-ip-skipped]')).to_contain_text('损坏发票.pdf')
            button('next').click()
            expect(page.locator('[data-ip-pager]')).to_have_text('第 2 / 2 页')
            button('rotate').last.click()
            assert 'rotate(90deg)' in page.locator('.ip-current img').get_attribute('style')
            for _ in range(3):
                button('rotate').last.click()
            button('prev').click()
            if output:
                page.screenshot(path=str(output / 'workspace.png'))
            content = print_from_workbench(page, button('print'))
            assert sum('size=print' in url for url in asset_requests) == 2
            assert_sheets(content, [2, 1])
            if output:
                (output / 'invoice-print-check.pdf').write_bytes(content)
            button('remove').first.click()
            expect(rows).to_have_count(2)
            mixed = page.pdf(prefer_css_page_size=True)
            assert_sheets(mixed, [2])
            if output:
                (output / 'invoice-print-phone-check.pdf').write_bytes(mixed)
            # Dropping a standalone multi-page PDF appends to the current list.
            upload = page.evaluate_handle('''bytes => {
                const transfer = new DataTransfer();
                transfer.items.add(new File([new Uint8Array(bytes)], '单独上传.pdf', {type:'application/pdf'}));
                return transfer;
            }''', list(pdf.getvalue()))
            page.locator('.ip-drop').dispatch_event('drop', {'dataTransfer': upload})
            upload.dispose()
            expect(rows).to_have_count(4, timeout=20000)
            entry.click()
            expect(rows).to_have_count(4)
            assert_sheets(page.pdf(prefer_css_page_size=True), [2, 2])

            # Let the first group become visible while the archive is still uploading.
            button('clear').click()
            delayed = []
            def single_original(url):
                return '/invoice-print?name=' in url
            def hold_archive(route):
                delayed.append(route)
            page.route(single_original, hold_archive)
            with page.expect_request(lambda request: single_original(request.url)):
                picker.set_input_files([str(photo), str(standalone), str(archive)])
            expect(rows).to_have_count(3, timeout=20000)
            expect(rows.nth(0)).to_contain_text('发票手机截图.png')
            expect(rows.nth(1)).to_contain_text('单独两页.pdf')
            expect(page.locator('[data-ip-progress]')).to_contain_text('已处理 2 / 3')
            expect(button('print')).to_be_disabled()
            delayed[0].continue_()
            expect(rows).to_have_count(5, timeout=20000)
            page.unroute(single_original)
            assert_sheets(print_from_workbench(page, button('print')), [2, 2, 1])
            page.locator('[data-cw-action="companies"]').click()
            page.locator('[data-cw-company]:not(.selected)').click()
            expect(page.locator('.cw-shell')).to_have_attribute('data-cw-page', 'home')
            entry.click()
            expect(rows).to_have_count(0)
            expect(button('print')).to_be_disabled()
            assert not errors, errors
            browser.close()
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='invoice-print-check-') as temp:
        verify(Path(temp), args.output)
    print('Invoice print sidebar, mixed imports, original margins, two-up and odd/even pagination passed.')
