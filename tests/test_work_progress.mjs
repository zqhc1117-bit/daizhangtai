import assert from 'node:assert/strict';
import test from 'node:test';
import {
  PROGRESS, PROGRESS_KEYS, getWorkProgress, getBalanceWorkProgress, progressStatus,
  progressStatusButton, progressDialogTitle, progressEvidence, renderProgressDetails,
} from '../public/work-progress.js';

const period = '2026-10';
const client = { id: 7, name: '合成客户' };
const tax = (id, overrides = {}) => ({
  id, client_id: client.id, period, tax_type: '增值税', status: 'pending',
  confirmation_status: 'pending', payment_status: 'unpaid',
  amount_fen: 12300, paid_fen: 0, reference: '', ...overrides,
});
const invoice = (id, overrides = {}) => ({
  id, client_id: client.id, period, date: `${period}-02`, number: `INV-${id}`,
  summary: '合成票据', total_fen: 12300, status: 'unprocessed', ...overrides,
});
const work = data => getWorkProgress(data, client.id, period);
const render = (data, key, target = client, targetPeriod = period) =>
  renderProgressDetails(target, targetPeriod, data, key, { money: value => (Number(value || 0) / 100).toFixed(2) });

test('agency bookkeeping follows monthly balance data even without vouchers or after closing', () => {
  const data = {
    book_periods: [{ client_id: client.id, period, voucher_count: 0, closed: 1 }],
    balance_progress: [{ client_id: client.id, period, has_data: true, account_count: 2 }],
  };
  const snapshot = structuredClone(data);
  const state = getBalanceWorkProgress(data, client.id, period);
  assert.equal(progressStatus(state, 'bookkeeping').label, '已记账');
  const html = render(state, 'bookkeeping');
  assert.match(html, /余额表.*有数据/);
  assert.match(html, /data-action="work-progress-balance"/);
  assert.doesNotMatch(html, /至少一张有效凭证/);
  assert.deepEqual(data, snapshot);
  data.balance_progress[0].has_data = false;
  data.book_periods[0].voucher_count = 10;
  assert.equal(progressStatus(getBalanceWorkProgress(data, client.id, period), 'bookkeeping').label, '未记账');
  assert.equal(getBalanceWorkProgress(snapshot, 99, period).states[2], 0);
  assert.equal(getBalanceWorkProgress(snapshot, client.id, '2026-09').states[2], 0);
  assert.equal(getBalanceWorkProgress({ balance_progress: null }, client.id, period).states[2], 0);
});

test('five legacy card states remain separate from six home table keys', () => {
  assert.equal(PROGRESS.length, 5);
  assert.deepEqual(PROGRESS_KEYS, ['confirmation', 'tickets', 'bookkeeping', 'filing', 'payment', 'individual']);
  const state = work({});
  assert.deepEqual(state.states, [0, 0, 0, 0, 0]);
  assert.deepEqual(PROGRESS_KEYS.map(key => progressStatus(state, key).label),
    ['未登记', '未登记', '未记账', '未登记', '未登记', '未登记']);
  assert.throws(() => progressStatus(state, 'unknown'), /未知/);
  assert.throws(() => progressDialogTitle('unknown'), /未知/);
});

test('non-tax arrays and unrelated records leave progress unregistered', () => {
  const state = work({ tax_records: null, invoices: 'invalid', book_periods: {} });
  assert.deepEqual(state.states, [0, 0, 0, 0, 0]);
  assert.deepEqual(state.tax, []);
  assert.deepEqual(state.invoice, []);
  assert.equal(state.book, undefined);
});

test('mixed ordinary taxes and IIT have independent filing but shared confirmation and payment', () => {
  const data = {
    tax_records: [
      tax(11, { status: 'submitted', confirmation_status: 'confirmed', payment_status: 'paid', paid_fen: 12300 }),
      tax(12, { tax_type: '企业所得税', status: 'submitted', confirmation_status: 'confirmed', payment_status: 'paid', paid_fen: 12300 }),
      tax(13, { tax_type: '个人所得税', status: 'preparing' }),
    ],
  };
  const state = work(data);
  assert.deepEqual(state.states, [1, 0, 0, 2, 1]);
  assert.equal(progressStatus(state, 'filing').label, '已登记申报');
  assert.equal(progressStatus(state, 'individual').label, '已登记·准备中');
  assert.equal(progressStatus(state, 'confirmation').label, '待确认');
  assert.equal(progressStatus(state, 'payment').label, '待缴款');
  assert.match(render(state, 'filing'), /data-record-id="11"/);
  assert.match(render(state, 'filing'), /data-record-id="12"/);
  assert.doesNotMatch(render(state, 'filing'), /data-record-id="13"/);
  assert.match(render(state, 'individual'), /data-record-id="13"/);
  assert.doesNotMatch(render(state, 'individual'), /data-record-id="11"/);
  assert.match(render(state, 'confirmation'), /data-record-id="13"/);
  assert.match(render(state, 'payment'), /data-record-id="13"/);

  data.tax_records[2] = tax(13, { tax_type: '个人所得税', status: 'submitted', confirmation_status: 'confirmed', payment_status: 'paid', paid_fen: 12300 });
  const completed = work(data);
  assert.deepEqual(completed.states, [2, 0, 0, 2, 2]);
  assert.deepEqual(progressStatus(completed, 'payment'), { label: '已登记缴款', className: 'good' });
});

test('all ordinary tax records must be submitted; a single incomplete tax blocks that aggregate', () => {
  const state = work({ tax_records: [tax(1, { status: 'submitted' }), tax(2, { tax_type: '企业所得税' })] });
  assert.equal(state.states[3], 1);
  assert.equal(state.states[4], 0);
  assert.equal(progressStatus(state, 'filing').label, '已登记·准备中');
});

test('IIT aliases match precisely and individual-only records do not complete ordinary filing', () => {
  const state = work({ tax_records: [
    tax(1, { tax_type: '个税', status: 'submitted' }),
    tax(2, { tax_type: '个人所得税', status: 'submitted' }),
    tax(3, { tax_type: 'iit', status: 'submitted' }),
  ] });
  assert.deepEqual(state.individual.map(record => record.id), [1, 2, 3]);
  assert.deepEqual(state.states.slice(3), [0, 2]);
  const other = work({ tax_records: [tax(4, { tax_type: 'IIT附加说明' })] });
  assert.deepEqual(other.individual, []);
  assert.deepEqual(other.states.slice(3), [1, 0]);
});

test('partial ticket posting stays pending and only all posted completes ticket progress', () => {
  const data = { invoices: [invoice(31, { status: 'posted' }), invoice(32)] };
  assert.equal(work(data).states[1], 1);
  const html = render(work(data), 'tickets');
  assert.match(html, /data-action="work-progress-post-invoice"[^>]*data-record-id="32"/);
  assert.doesNotMatch(html, /data-action="work-progress-post-invoice"[^>]*data-record-id="31"/);
  data.invoices[1].status = 'posted';
  assert.equal(work(data).states[1], 2);
  data.invoices[0].status = 'unprocessed';
  assert.equal(work(data).states[1], 1, 'withdrawn source returns ticket aggregate to pending');
});

test('audited ticket sources replace the ledger-only list and expose their evidence and correct actions', () => {
  const data = {
    invoices: [invoice(1)],
    ticket_progress: [
      invoice('raw:1', { origin: 'archive', number: '<raw>', total_fen: null }),
      invoice(2, { origin: 'reconciliation', status: 'posted', voucher_period: '2026-09' }),
      invoice(3, { origin: 'invoice' }),
      invoice(4, { client_id: 8 }), invoice(5, { period: '2026-09' }),
    ],
  };
  const state = work(data);
  assert.equal(state.invoice.length, 3);
  assert.equal(state.states[1], 1);
  assert.equal(progressEvidence(state, 'tickets'), '本月票据 3 张，已制证 1 张');
  const html = render(state, 'tickets');
  assert.match(html, /原件归档/);
  assert.match(html, /流水发票核对/);
  assert.match(html, /&lt;raw&gt;/);
  assert.match(html, /data-voucher-period="2026-09"/);
  assert.match(html, /data-action="work-progress-reconcile"/);
  assert.doesNotMatch(html, /data-record-id="(?:NaN|2)"/);
  assert.match(html, /data-action="work-progress-post-invoice"[^>]*data-record-id="3"/);
  assert.equal(work({ invoices: [invoice(1)], ticket_progress: [] }).states[1], 0, 'audited empty list is authoritative');
});

test('tax evidence excludes deleted records and distinguishes absence, preparation and completed steps', () => {
  const data = { tax_records: [tax(1, { deleted: 1, status: 'submitted' }), tax(2), tax(3, { tax_type: '个税', status: 'submitted', payment_status: 'paid' })] };
  const state = work(data);
  assert.equal(progressEvidence(state, 'filing'), '本月非个税申报记录 1 条，已登记申报 0 条');
  assert.equal(progressEvidence(state, 'individual'), '本月个税记录 1 条，已登记申报 1 条');
  assert.equal(progressEvidence(state, 'payment'), '本月税务记录 2 条，已缴款 1 条');
  assert.equal(progressEvidence(state, 'confirmation'), '本月税务记录 2 条，人工已确认 0 条');
  assert.equal(progressEvidence(work({}), 'payment'), '本月未登记税务记录');
  assert.deepEqual(state.states.slice(3), [1, 2]);
});

test('every source isolates client and month; invoice dates are accepted only without explicit period', () => {
  const data = {
    tax_records: [tax(1, { client_id: 8, status: 'submitted' }), tax(2, { period: '2026-09', status: 'submitted' }), tax(3)],
    invoices: [
      invoice(1, { client_id: 8, status: 'posted' }), invoice(2, { period: '2026-09', status: 'posted' }),
      invoice(3, { period: undefined }), invoice(4, { period: '2026-09', date: '2026-10-05', status: 'posted' }),
    ],
    book_periods: [
      { client_id: 8, period, closed: 1 }, { client_id: 7, period: '2026-09', closed: 1 },
      { client_id: '7', period, voucher_count: 1 },
    ],
  };
  const state = getWorkProgress(data, '7', period);
  assert.deepEqual(state.tax.map(record => record.id), [3]);
  assert.deepEqual(state.invoice.map(record => record.id), [3]);
  assert.deepEqual(state.states, [1, 1, 1, 1, 0]);
  assert.deepEqual(getWorkProgress(data, 99, period).states, [0, 0, 0, 0, 0]);
});

test('ledger follows active-voucher server rollup, not raw vouchers, attachments, tickets or unreviewed count', () => {
  const data = {
    vouchers: [{ client_id: client.id, period, status: 'void' }],
    invoices: [invoice(1, { status: 'posted', voucher_id: 123 })],
    book_periods: [{ client_id: client.id, period, voucher_count: 0, unreviewed_count: 3, attachment_count: 10 }],
  };
  assert.equal(work(data).states[2], 0, 'server count remains authoritative after a voucher is voided');
  data.book_periods[0].voucher_count = '1';
  assert.equal(work(data).states[2], 1, 'an active but unreviewed voucher is already booked');
  data.book_periods[0].voucher_count = 0;
  assert.equal(work(data).states[2], 0, 'voiding the last active voucher clears booked status');
});

test('closed status has precedence even with no vouchers and reopen returns to actual rollup', () => {
  const data = { book_periods: [{ client_id: client.id, period, closed: 1, voucher_count: 0 }] };
  assert.equal(work(data).states[2], 2);
  data.book_periods[0] = { client_id: client.id, period, status: 'closed', voucher_count: 0 };
  assert.equal(work(data).states[2], 2);
  data.book_periods[0].status = 'open';
  assert.equal(work(data).states[2], 0);
});

test('tax and ticket HTML escapes record values and keeps target client, period, key and record identifiers', () => {
  const unsafe = '<img src=x onerror="alert(1)"> & \'quoted\'';
  const target = { id: client.id, name: unsafe };
  const targetPeriod = '2026-10" onfocus="bad';
  const state = work({
    tax_records: [tax(44, { tax_type: unsafe, reference: unsafe })],
    invoices: [invoice(55, { number: unsafe, summary: unsafe })],
  });
  for (const key of PROGRESS_KEYS) {
    const button = progressStatusButton(state, key, target, targetPeriod);
    const html = render(state, key, target, targetPeriod);
    for (const output of [button, html]) {
      assert.doesNotMatch(output, /<img/);
      assert.match(output, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt; &amp; &#39;quoted&#39;/);
      assert.match(output, /data-id="7"/);
      assert.match(output, /data-period="2026-10&quot; onfocus=&quot;bad"/);
      assert.match(output, new RegExp(`data-progress-key="${key}"`));
    }
    assert.match(button, /type="button"/);
    assert.match(button, /aria-label="/);
    assert.match(button, /data-action="work-progress"/);
  }
  assert.match(render(state, 'filing'), /data-action="work-progress-edit-tax"[^>]*data-record-id="44"/);
  assert.match(render(state, 'tickets'), /data-action="work-progress-edit-invoice"[^>]*data-record-id="55"/);
});

test('bookkeeping actions select the intended voucher or close view without a completion flag', () => {
  const html = render(work({}), 'bookkeeping');
  assert.match(html, /data-action="work-progress-book"[^>]*data-id="7"[^>]*data-period="2026-10"[^>]*data-view="vouchers"/);
  assert.match(html, /data-action="work-progress-book"[^>]*data-id="7"[^>]*data-period="2026-10"[^>]*data-view="period"/);
  assert.doesNotMatch(html, /data-action="[^\"]*(?:complete|override)/);
});

test('detail tables list relevant records with a clear empty state and escape money formatter output', () => {
  for (const key of ['confirmation', 'filing', 'payment', 'individual', 'tickets']) {
    const html = render(work({}), key);
    assert.match(html, /本月尚/);
    assert.match(html, /data-action="work-progress-(?:new-tax|new-invoice)"/);
  }
  const state = work({ tax_records: [tax(1)] });
  const html = renderProgressDetails(client, period, state, 'filing', { money: () => '<b>bad money</b>' });
  assert.match(html, /&lt;b&gt;bad money&lt;\/b&gt;/);
  assert.doesNotMatch(html, /<b>bad money<\/b>/);
});
