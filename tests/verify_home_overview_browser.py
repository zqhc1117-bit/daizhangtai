"""Verify the merged home in Chromium using only a temporary synthetic database."""
from contextlib import closing
from pathlib import Path
import json
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import business
import db
import finance
import server
from playwright.sync_api import sync_playwright, expect


def seed(app):
    with closing(db.connect(app.db_path)) as conn, conn:
        user = dict(conn.execute('SELECT id,username,name FROM users WHERE id=1').fetchone())

        def call(path, payload):
            return business.handle(conn, 'POST', path, payload, user, {})

        names = ['合并验收甲公司', '合并验收乙公司', '合并验收丙公司', '未来建账公司', '停用验收公司', '回收验收公司']
        for index, name in enumerate(names, 1):
            call('/api/clients', dict(name=name, code=f'OVERVIEW{index}', taxpayer_type='general' if index == 2 else 'small',
                                     standard='small2013', start_period='2027-01' if index == 4 else '2026-01',
                                     accountant_id=2 if index == 2 else 1,
                                     note='<img data-home-xss src=x onerror="alert(1)"> 随手记' if index == 1 else '',
                                     labels=['重点客户'] if index == 1 else []))
        conn.execute('UPDATE clients SET enabled=0 WHERE id=5')
        conn.execute('UPDATE clients SET deleted=1 WHERE id=6')
        finance._opening(conn, 2, {'version': 1, 'balances': [
            finance._entry('1002', 10000), finance._entry('3001', credit=10000),
        ]}, user)
        for cid, month in [(1, '2026-11'), (3, '2026-10')]:
            finance.handle(conn, 'POST', f'/api/books/{cid}/vouchers', {
                'date': f'{month}-05', 'summary': '合成首页进度凭证',
                'lines': [finance._entry('1002', 10000, category='operating'), finance._entry('5001', credit=10000)],
            }, user, {'period': month})
        call('/api/invoices', dict(client_id=1, period='2026-11', kind='input', number='OVERVIEW-INV-1', date='2026-11-03',
                                   amount_fen=10000, tax_fen=0, total_fen=10000))
        invoice = dict(number='OVERVIEW-RECON', date='2026-11-04', amount='100.00', source='synthetic.pdf', item_name='合成核对票据')
        conn.execute('''INSERT INTO reconciliation_records(client_id,invoice_number,transaction_json,invoice_json,
            account_name,status,review_note,user_id,created_at,transaction_key)
            VALUES(1,?,'{}',?,'合并验收甲公司','matched','',1,'2026-11-17','overview-recon')''',
            (invoice['number'], json.dumps(invoice)))
        conn.execute('INSERT INTO source_archives VALUES(?,1,?,?,1,?)',
            ('overview-archive', 'overview-archive', json.dumps({'invoices': [invoice]}), '2026-11-17'))
        for cid, end, fee in [(1, '2026-11', 10000), (2, '2026-10', 10000), (3, '2027-06', 0)]:
            call('/api/contracts', dict(client_id=cid, service='代理记账', start_period='2026-01', end_period=end, monthly_fee_fen=fee))
        call('/api/receipts', dict(contract_id=1, date='2026-11-01', amount_fen=110000, method='银行'))
        call('/api/tax-records', dict(client_id=1, period='2026-11', tax_type='增值税', status='submitted', payment_status='paid',
                                     confirmation_status='confirmed', amount_fen=10000, paid_fen=10000, reference='SYNTHETIC-VAT-1'))
        iit = call('/api/tax-records', dict(client_id=1, period='2026-11', tax_type='个人所得税', status='preparing',
                                           payment_status='unpaid', amount_fen=1000, paid_fen=0))
        call('/api/tax-records', dict(client_id=3, period='2026-11', tax_type='增值税', status='pending', payment_status='unpaid',
                                     amount_fen=0, paid_fen=0))
        token, _ = app.new_session(conn, {'id': 1})
        return token, iit['id']


def main():
    output = ROOT / 'tmp' / 'home-overview-verification'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='home-overview-ui-') as folder:
        app = server.Application(folder, demo=True)
        token, iit_id = seed(app)
        http = server.make_server('127.0.0.1', 0, app)
        http.RequestHandlerClass.log_message = lambda *args: None
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{http.server_address[1]}'
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel='chrome', headless=True)
                context = browser.new_context(viewport={'width': 1920, 'height': 1080}, locale='zh-CN', timezone_id='Asia/Shanghai')
                context.add_cookies([{'name': 'ydz_session', 'value': token, 'url': base}])
                page = context.new_page()
                errors = []
                network_errors = []
                console_errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('requestfailed', lambda request: network_errors.append({'url': request.url, 'failure': request.failure}))
                page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
                page.set_default_timeout(10000)

                def main_rows():
                    return page.locator('.home-progress-table tbody > tr:not(.home-client-details)')

                def company_ids():
                    return page.locator('.home-progress-table [data-action="company-home"]').evaluate_all('(nodes) => nodes.map(node => Number(node.dataset.id))')

                def close_modal():
                    page.locator('.modal-footer [data-close]').click()
                    expect(page.locator('.modal-backdrop')).to_have_count(0)

                try:
                    page.goto(base)
                    expect(page.locator('.home-overview h1')).to_have_text('工作总览')
                    page.locator('[data-filter="period"]').fill('2026-11')
                    page.locator('[data-filter="period"]').dispatch_event('change')
                    expect(page.locator('.sidebar [data-sub="工作总览"]')).to_be_visible()
                    expect(page.locator('.sidebar [data-sub="代账进展"]')).to_have_text('代账进展')
                    expect(page.locator('.topnav button')).to_have_count(6)
                    expect(page.locator('#home-more-filters')).to_be_hidden()
                    expect(page.locator('.home-stats-content')).to_be_visible()
                    expect(page.locator('.home-summary .progress-card')).to_have_count(7)
                    expect(page.locator('.home-summary .donut')).to_have_count(7)
                    expect(page.locator('.home-summary-label')).to_have_text(['票据待制证', '本月未记账', '申报待登记', '待缴款', '服务到期', '存在欠费', '近三月无凭证'])
                    expect(page.locator('.home-summary .home-todo, .home-summary .progress-title, .home-summary .progress-track')).to_have_count(0)
                    expect(page.locator('[data-action="home-statistics"], .home-tools')).to_have_count(0)
                    expect(page.locator('.home-summary .detection-grid, .home-summary table')).to_have_count(0)
                    expect(main_rows()).to_have_count(5)
                    assert company_ids() == [1, 2, 3, 4, 5]
                    assert main_rows().first.locator('td').count() == 12
                    assert page.locator('.home-progress-table th').count() == 12
                    for key, count in dict(tickets=1, book=0, filing=2, payment=2, expiry=2, debt=1, inactive=1).items():
                        expect(page.locator(f'[data-summary="{key}"] .donut strong')).to_have_text(str(count))
                    expect(page.locator('[data-action="home-filter"][data-value="book:0"] b')).to_have_text('0')
                    expect(page.locator('[data-action="home-filter"][data-value="book:1"] b')).to_have_text('3')
                    expect(page.locator('[data-summary="book"] .card-status button')).to_have_count(2)
                    for cid in [1, 2, 3]:
                        expect(main_rows().locator(f'[data-id="{cid}"][data-progress-key="bookkeeping"]')).to_have_text('已记账')
                    expect(main_rows().locator('[data-id="2"][data-progress-key="bookkeeping"]')).to_have_attribute('title', '余额表有数据（2 个科目），本月有效凭证 0 张；点击查看明细与处理')
                    page.screenshot(path=str(output / 'overview-1920.png'), full_page=True)
                    page.set_viewport_size({'width': 1366, 'height': 768})
                    page.screenshot(path=str(output / 'overview-1366.png'), full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Only the table may scroll horizontally.'
                    assert page.locator('.home-summary .card-main').evaluate_all('''cards => cards.every(card => {
                        const ring = card.querySelector('.donut').getBoundingClientRect();
                        const text = card.querySelector('.card-status').getBoundingClientRect();
                        return ring.right <= text.left && ring.top < text.bottom && text.top < ring.bottom;
                    })'''), 'Each ring must sit to the left of its status rows.'
                    page.set_viewport_size({'width': 1920, 'height': 1080})

                    page.locator('[data-company-select="clients"]').first.check()
                    page.locator('[data-action="home-filter"][data-value="book:1"]').click()
                    expect(main_rows()).to_have_count(3)
                    assert company_ids() == [1, 2, 3]
                    expect(page.locator('[data-company-select="clients"]:checked')).to_have_count(0)
                    expect(page.locator('[data-action="home-filter"][data-value="book:1"]')).to_have_attribute('aria-pressed', 'true')
                    page.locator('[data-action="home-clear-filter"]').click()
                    expect(main_rows()).to_have_count(5)

                    expected_ids = dict(tickets=[1], book=[], filing=[2, 3], payment=[1, 3], expiry=[1, 2], debt=[2], inactive=[2])
                    for key, ids in expected_ids.items():
                        page.locator(f'.home-summary-label[data-value="{key}"]').click()
                        expect(page.locator('.home-progress-table [data-action="company-home"]')).to_have_count(len(ids))
                        assert company_ids() == ids
                        page.locator('[data-action="home-clear-filter"]').click()

                    page.locator('[data-action="home-details"][data-id="1"]').click()
                    detail = page.locator('[data-home-details="1"]')
                    expect(detail).to_be_visible()
                    expect(detail).to_contain_text('<img data-home-xss')
                    expect(detail).to_contain_text('重点客户')
                    expect(page.locator('img[data-home-xss]')).to_have_count(0)
                    for key in ['tickets', 'bookkeeping', 'filing', 'individual', 'payment', 'confirmation']:
                        page.locator(f'[data-action="work-progress"][data-id="1"][data-progress-key="{key}"]').click()
                        expect(page.locator('.modal-body')).to_contain_text('合并验收甲公司 · 2026-11')
                        if key == 'bookkeeping':
                            expect(page.locator('.modal-body')).to_contain_text('余额表：有数据')
                        if key == 'tickets':
                            expect(page.locator('.modal-body')).to_contain_text('本月票据 2 张，已制证 0 张')
                            expect(page.locator('.progress-details-table')).to_contain_text('流水发票核对')
                            expect(page.locator('.progress-details-table [data-action="work-progress-reconcile"]')).to_have_count(1)
                        close_modal()
                    expect(page.locator('[data-home-details="1"]')).to_be_visible()
                    page.screenshot(path=str(output / 'overview-details.png'), full_page=True)

                    page.locator('[data-action="home-more-filters"]').click()
                    page.locator('[data-filter="taxpayer"]').select_option('general')
                    expect(main_rows()).to_have_count(1)
                    assert company_ids() == [2]
                    page.locator('[data-action="reset-filters"]').first.click()
                    page.locator('[data-filter="label"]').select_option('重点客户')
                    expect(main_rows()).to_have_count(1)
                    assert company_ids() == [1]
                    page.locator('[data-action="reset-filters"]').first.click()
                    page.locator('[data-filter="accountant"]').select_option('2')
                    expect(main_rows()).to_have_count(1)
                    expect(page.locator('[data-summary="debt"] .donut strong')).to_have_text('1')
                    page.locator('[data-action="reset-filters"]').first.click()

                    expect(page.locator('.home-stats-content')).to_be_visible()
                    expect(page.locator('.progress-card')).to_have_count(7)
                    page.locator('[data-action="home-filter"][data-value="book:0"]').click()
                    expect(page.locator('.home-progress-table [data-action="company-home"]')).to_have_count(0)
                    assert company_ids() == []
                    expect(page.locator('[data-action="home-filter"][data-value="book:1"] b')).to_have_text('3')
                    page.locator('[data-action="home-filter"][data-value="book:1"]').click()
                    expect(main_rows()).to_have_count(3)
                    assert company_ids() == [1, 2, 3]
                    page.locator('[data-action="home-clear-filter"]').click()

                    page.locator('[data-filter="period"]').fill('2026-10')
                    page.locator('[data-filter="period"]').dispatch_event('change')
                    expect(page.locator('[data-summary="book"] .donut strong')).to_have_text('1')
                    expect(page.locator('[data-summary="tickets"] .donut strong')).to_have_text('0')
                    expect(page.locator('.home-progress-table .agency-expiry-due, .home-progress-table .agency-expiry-overdue')).to_have_count(1)
                    expect(page.locator('.home-todo-tags .status').filter(has_text='欠费')).to_have_count(2)
                    page.locator('[data-action="period-next"]').click()
                    expect(page.locator('[data-filter="period"]')).to_have_value('2026-11')
                    expect(page.locator('.home-todo-tags .status').filter(has_text='欠费')).to_have_count(1)

                    page.locator('[data-action="home-details"][data-id="2"]').click()
                    page.locator('[data-home-details="2"] [data-action="home-fees"]').click()
                    expect(page.locator('.modal-body')).to_contain_text('服务到期月份与实际欠费分别计算')
                    page.locator('.modal-body [data-action="receipt-new"]').click()
                    form = page.locator('#app-modal-form')
                    form.locator('[name="date"]').fill('2026-11-17')
                    form.locator('[name="amount"]').fill('1000.00')
                    form.locator('[name="method"]').select_option('bank')
                    form.locator('[type="submit"]').click()
                    expect(page.locator('.modal-backdrop')).to_have_count(0)
                    expect(page.locator('.home-todo-tags .status').filter(has_text='欠费')).to_have_count(0)
                    expect(page.locator('.home-progress-table .agency-expiry-due, .home-progress-table .agency-expiry-overdue')).to_have_count(2)
                    expect(main_rows()).to_have_count(5)

                    page.locator('.home-progress-table [data-action="agency-contract"][data-client="2"]').click()
                    expect(page.locator('#modal-title')).to_have_text('编辑服务合同')
                    expect(page.locator('#app-modal-form [name="end_period"]')).to_be_disabled()
                    page.locator('[data-action="contract-renew"][data-id="2"]').click()
                    expect(page.locator('#modal-title')).to_have_text('新建服务合同')
                    page.locator('#app-modal-form [name="end_period"]').fill('2027-10')
                    page.locator('#app-modal-form [type="submit"]').click()
                    expect(page.locator('.modal-backdrop')).to_have_count(0)
                    expect(page.locator('.home-progress-table .agency-expiry-due, .home-progress-table .agency-expiry-overdue')).to_have_count(1)

                    page.locator('[data-action="work-progress"][data-id="1"][data-progress-key="individual"]').click()
                    page.locator(f'[data-action="work-progress-edit-tax"][data-record-id="{iit_id}"]').click()
                    form = page.locator('#app-modal-form')
                    form.locator('[name="status"]').select_option('submitted')
                    form.locator('[name="reference"]').fill('SYNTHETIC-IIT-1')
                    form.locator('[name="paid"]').fill('10.00')
                    form.locator('[name="payment_status"]').select_option('paid')
                    form.locator('[name="confirmation_status"]').select_option('confirmed')
                    form.locator('[type="submit"]').click()
                    expect(page.locator('.modal-backdrop')).to_have_count(0)
                    expect(page.locator('[data-id="1"][data-progress-key="payment"]')).to_have_text('已登记缴款')
                    expect(page.locator('[data-id="1"][data-progress-key="individual"]')).to_have_text('已登记申报')

                    page.locator('[data-filter="accountant"]').select_option('1')
                    page.locator('[data-company-search-input]').fill('合并验收乙')
                    expect(page.locator('[data-company-search-id="2"]')).to_be_visible()
                    page.locator('[data-company-search-id="2"]').click()
                    expect(page.locator('.cw-shell')).to_be_visible()
                    expect(page.locator('[data-cw-period]')).to_have_value('2026-11')
                    page.goto(base, wait_until='networkidle')
                    expect(main_rows()).to_have_count(5, timeout=15000)
                    for nav in ['客户', '票据', '记账', '报税', '设置', '首页']:
                        page.locator(f'.topnav [data-nav="{nav}"]').click()
                        expect(page.locator('.page-error')).to_have_count(0)
                    expect(page.locator('.home-overview')).to_be_visible()

                    # Both agency entries must show every company even after filtering home.
                    page.locator('[data-filter="accountant"]').select_option('2')
                    page.locator('[data-action="home-more-filters"]').click()
                    page.locator('[data-filter="taxpayer"]').select_option('general')
                    expect(main_rows()).to_have_count(1)
                    page.locator('[data-action="agency-progress"]').click()
                    expect(page.locator('.agency-progress h1')).to_have_text('代账进展')
                    expect(page.locator('.sidebar [data-sub="代账进展"]')).to_have_class('side-item active')
                    expect(main_rows()).to_have_count(5)
                    assert company_ids() == [1, 2, 3, 4, 5]
                    expect(page.locator('[data-filter="accountant"]')).to_have_value('')
                    expect(page.locator('[data-filter="taxpayer"]')).to_have_value('')
                    expect(page.locator('.home-progress-table th')).to_have_text([
                        '全选当前列表', '序号', '客户名称', '记账会计', '记账', '申报', '操作',
                    ])
                    assert main_rows().first.locator('td').count() == 7
                    for cid in [1, 2, 3]:
                        expect(main_rows().locator(f'[data-id="{cid}"][data-progress-key="bookkeeping"]')).to_have_text('已记账')
                    for cid in [4, 5]:
                        expect(main_rows().locator(f'[data-id="{cid}"][data-progress-key="bookkeeping"]')).to_have_text('未记账')
                    expect(main_rows().nth(3)).to_contain_text('尚未建账')
                    expect(main_rows().nth(4)).to_contain_text('已停用')
                    for key in ['bookkeeping', 'filing']:
                        main_rows().locator(f'[data-action="work-progress"][data-id="1"][data-progress-key="{key}"]').click()
                        expect(page.locator('.modal-body')).to_contain_text('合并验收甲公司 · 2026-11')
                        close_modal()
                    page.locator('[data-action="home-details"][data-id="1"]').click()
                    expect(page.locator('[data-home-details="1"]')).to_be_visible()
                    expect(page.locator('[data-home-details="1"] > td')).to_have_attribute('colspan', '7')
                    expect(page.locator('img[data-home-xss]')).to_have_count(0)
                    page.locator('[data-action="home-details"][data-id="1"]').click()

                    page.locator('[data-action="period-prev"]').click()
                    expect(page.locator('[data-filter="period"]')).to_have_value('2026-10')
                    expect(main_rows().locator('[data-id="1"][data-progress-key="bookkeeping"]')).to_have_text('未记账')
                    expect(main_rows().locator('[data-id="3"][data-progress-key="bookkeeping"]')).to_have_text('已记账')
                    page.locator('[data-action="period-next"]').click()
                    expect(main_rows().locator('[data-id="1"][data-progress-key="bookkeeping"]')).to_have_text('已记账')
                    expect(main_rows().locator('[data-id="3"][data-progress-key="bookkeeping"]')).to_have_text('已记账')
                    page.locator('[data-filter="accountant"]').select_option('2')
                    expect(main_rows()).to_have_count(1)
                    page.locator('.sidebar [data-sub="代账进展"]').click()
                    expect(main_rows()).to_have_count(5)
                    for query, expected in [('合并验收甲', [1]), ('没有这家公司', [])]:
                        page.locator('[data-company-search-input]').fill(query)
                        page.locator('[data-search-form] [type="submit"]').click()
                        expect(page.locator('.home-progress-table [data-action="company-home"]')).to_have_count(len(expected))
                        assert company_ids() == expected
                    expect(page.locator('.home-progress-table')).to_contain_text('没有符合当前筛选条件的公司')
                    page.locator('[data-action="reset-filters"]').first.click()
                    expect(main_rows()).to_have_count(5)
                    page.screenshot(path=str(output / 'agency-progress-1920.png'), full_page=True)
                    page.set_viewport_size({'width': 1366, 'height': 768})
                    page.screenshot(path=str(output / 'agency-progress-1366.png'), full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.set_viewport_size({'width': 1920, 'height': 1080})

                    main_rows().locator('[data-id="2"][data-progress-key="bookkeeping"]').click()
                    expect(page.locator('.modal-body')).to_contain_text('余额表：有数据')
                    expect(page.locator('.modal-body')).to_contain_text('本月有效凭证 0 张')
                    page.locator('[data-action="work-progress-balance"]').click()
                    expect(page.locator('.company-balance[data-cob-company="2"]')).to_be_visible()
                    expect(page.locator('.company-balance')).to_have_attribute('aria-busy', 'false')
                    assert page.evaluate('location.hash') == '#balance/2/2026-11/2026-11'
                    page.goto(base, wait_until='networkidle')
                    page.locator('.sidebar [data-sub="代账进展"]').click()

                    # Crossing the old 50-row page size must not hide any company.
                    with closing(db.connect(app.db_path)) as conn, conn:
                        user = dict(conn.execute('SELECT id,username,name FROM users WHERE id=1').fetchone())
                        for index in range(55):
                            business.handle(conn, 'POST', '/api/clients', dict(
                                name=f'代账完整列表验收{index + 1:02d}公司', code=f'AGENCY{index + 1:02d}',
                                taxpayer_type='small', standard='small2013', start_period='2026-01',
                                accountant_id=1), user, {})
                    page.locator('.sidebar [data-sub="代账进展"]').click()
                    expect(main_rows()).to_have_count(60)
                    assert set(company_ids()) == set(range(1, 62)) - {6}
                    expect(page.locator('.agency-progress .table-foot').last).to_contain_text('显示 60 / 60 家公司')
                    expect(page.locator('[data-page-size]')).to_have_count(0)
                    main_rows().locator('[data-action="company-home"][data-id="1"]').click()
                    expect(page.locator('.cw-shell')).to_be_visible()
                    expect(page.locator('[data-cw-period]')).to_have_value('2026-11')
                    assert not errors, errors
                    result = {'passed': True, 'browser_errors': errors, 'checks': [
                        'home and agency sidebar entries', 'seven named summaries with left rings and right status text', 'selection scope',
                        'record dialogs and escaped inline details', 'shared accountant/taxpayer/label filters',
                        'always visible seven circular statistics and matching filters', 'period-sensitive fees and expiration',
                        'real receipt save updates debt independently of expiration', 'contract renewal clears expiration and preserves paid contract',
                        'tax save updates progress and payment', 'global company search preserves month',
                        'main navigation', '1920 and 1366 layouts',
                        'both agency entries clear prior filters and show all companies',
                        'agency shows only bookkeeping and filing columns; progress dialogs, company navigation, search and month switching',
                        'home and agency share balance-based bookkeeping, binary summaries, filters and detail evidence',
                        'ticket ledger, saved reconciliation and archived invoice deduplication; month isolation and source actions',
                        'all 60 companies visible without pagination; recycled clients excluded',
                    ]}
                    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(json.dumps(result, ensure_ascii=False))
                except Exception:
                    page.screenshot(path=str(output / 'failure.png'), full_page=True)
                    (output / 'failure.html').write_text(page.content(), encoding='utf-8')
                    print('Browser errors:', errors)
                    print('Network errors:', network_errors)
                    print('Console errors:', console_errors)
                    print('Resource timing:', page.evaluate('performance.getEntriesByType("resource").map(item => ({name:item.name,size:item.decodedBodySize}))'))
                    raise
                finally:
                    browser.close()
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    main()
