import assert from 'node:assert/strict';
import test from 'node:test';
import { buildOverviewRows, overviewCounts, overviewSummaries, filterOverviewRows } from '../public/home-overview.js';

const period = '2026-11';
const client = (id = 1, extra = {}) => ({ id, name: `合成公司${id}`, start_period: '2026-01', enabled: 1, ...extra });
const contract = (id = 1, extra = {}) => ({ id, client_id: 1, start_period: '2026-01', end_period: period, status: 'active', debt_fen: 0, ...extra });
const row = (data = {}, company = client(), month = period) => buildOverviewRows(data, [company], month)[0];

test('home and agency share balance data while retaining all companies and independent filing status', () => {
  const data = {
    balance_progress: [{ client_id: 1, period, has_data: true, account_count: 2 }],
    book_periods: [{ client_id: 2, period, voucher_count: 0, closed: 1 }],
    tax_records: [{ client_id: 2, period, tax_type: '增值税', status: 'submitted' }],
  };
  const rows = buildOverviewRows(data, [client(1), client(2)], period);
  assert.deepEqual(rows.map(row => row.progress.states.slice(2, 4)), [[1, 0], [0, 2]]);
  assert.equal(rows[0].flags.book, false);
  assert.equal(rows[1].flags.book, true);
  assert.equal(row(data).progress.states[2], 1, 'home uses the same balance rule as agency');
  assert.deepEqual(overviewSummaries(rows).find(s => s.key === 'book').counts, [1, 1]);
  assert.deepEqual(filterOverviewRows(rows, 'book:1').map(r => r.client.id), [1]);
});

test('service expiration and actual debt are independent, and renewals clear the old expiration', () => {
  const data = { contracts: [contract()] };
  assert.equal(row(data).flags.expiry, true);
  assert.equal(row(data).flags.debt, false, 'a fully paid contract can still expire');
  data.contracts.push(contract(2, { start_period: '2026-12', end_period: '2027-11' }));
  assert.equal(row(data).flags.expiry, false);
  data.contracts[0].debt_fen = 90000;
  assert.equal(row(data).flags.debt, true, 'renewal does not clear an outstanding balance');
  data.contracts.push(contract(3, { client_id: 2, debt_fen: 900000 }));
  data.contracts.push(contract(4, { start_period: '2027-01', end_period: '2027-12', debt_fen: 500000 }));
  data.contracts.push(contract(5, { deleted: 1, debt_fen: 500000 }));
  assert.equal(row(data).debt, 90000, 'other companies, future service and removed contracts do not inflate debt');
});

test('current unbooked and three months without vouchers remain separate, including year boundaries', () => {
  const data = { balance_progress: [{ client_id: 1, period, has_data: true }], book_periods: [{ client_id: 1, period: '2026-10', voucher_count: 1 }] };
  assert.equal(row(data).flags.book, false);
  assert.equal(row(data).flags.inactive, false);
  data.book_periods[0].voucher_count = 0;
  assert.equal(row(data).flags.inactive, true);
  assert.equal(row(data).flags.book, false, 'carried balances and recent voucher activity are independent');
  assert.equal(row(data, client(1, { start_period: '2026-10' })).flags.inactive, false, 'newly established books do not satisfy the three month condition');
  assert.equal(row({}, client(), '2027-01').flags.inactive, true);
  const january = { book_periods: [{ client_id: 1, period: '2026-11', voucher_count: 1 }] };
  assert.equal(row(january, client(), '2027-01').flags.inactive, false);
  assert.equal(row(january, client(), '2027-02').flags.inactive, true);
});

test('persisted progress remains authoritative, with ordinary tax and IIT evaluated separately', () => {
  const data = {
    invoices: [{ client_id: 1, period, status: 'unprocessed' }],
    book_periods: [{ client_id: 1, period, voucher_count: 1 }],
    balance_progress: [{ client_id: 1, period, has_data: true }],
    tax_records: [
      { client_id: 1, period, tax_type: '增值税', status: 'submitted', payment_status: 'paid' },
      { client_id: 1, period, tax_type: '个人所得税', status: 'preparing', payment_status: 'unpaid' },
    ],
  };
  const actual = row(data);
  assert.equal(actual.flags.tickets, true);
  assert.equal(actual.flags.book, false);
  assert.equal(actual.flags.filing, false, 'unfinished IIT does not undo ordinary tax filing');
  assert.equal(actual.flags.payment, true, 'IIT payment still participates in payment progress');
  data.tax_records[1].payment_status = 'paid';
  assert.equal(row(data).flags.payment, false);
  assert.equal(row({}).flags.payment, false, 'missing records are not evidence of unpaid tax');
  data.book_periods[0] = { client_id: 1, period, voucher_count: 0, closed: 1 };
  data.balance_progress[0].has_data = false;
  assert.equal(row(data).flags.book, true, 'closing an empty balance does not count as booked');
  data.book_periods[0].closed = 0;
  assert.equal(row(data).flags.book, true, 'reopening still uses balance data');
});

test('future and disabled books do not create monthly work reminders; historic fees remain visible', () => {
  for (const company of [client(1, { start_period: '2027-01' }), client(1, { enabled: 0 }), client(1, { enabled: false })]) {
    const actual = row({ contracts: [contract(1, { debt_fen: 10000 })] }, company);
    for (const key of ['tickets', 'book', 'filing', 'payment', 'inactive']) assert.equal(actual.flags[key], false);
    assert.equal(actual.flags.expiry, true);
    assert.equal(actual.flags.debt, true);
  }
  assert.deepEqual(buildOverviewRows({}, [client(1, { deleted: 1 })], period), []);
});

test('sources are isolated by company and month and reading the overview never mutates them', () => {
  const data = {
    invoices: [{ client_id: 2, period, status: 'unprocessed' }, { client_id: 1, period: '2026-10', status: 'unprocessed' }],
    tax_records: [{ client_id: 2, period, tax_type: '增值税', status: 'submitted', payment_status: 'unpaid' }],
    contracts: [contract(1, { client_id: 2, debt_fen: 10000 })],
    book_periods: [{ client_id: 2, period, voucher_count: 10 }],
    balance_progress: [{ client_id: 2, period, has_data: true }, { client_id: 1, period: '2026-10', has_data: true }],
  };
  const snapshot = structuredClone(data);
  const actual = row(data);
  assert.equal(actual.flags.tickets, false);
  assert.equal(actual.flags.payment, false);
  assert.equal(actual.flags.expiry, false);
  assert.equal(actual.flags.debt, false);
  assert.equal(actual.flags.book, true);
  assert.deepEqual(data, snapshot);
});

test('cards count customers once and filter only the supplied global scope', () => {
  const data = { contracts: [contract(), contract(2, { end_period: '2026-10', debt_fen: 10000 })] };
  const rows = buildOverviewRows(data, [client(), client(2)], period);
  const counts = overviewCounts(rows);
  assert.equal(counts.expiry, 1, 'multiple contracts for the same client count as one customer');
  assert.deepEqual(filterOverviewRows(rows, 'expiry').map(r => r.client.id), [1]);
  assert.deepEqual(filterOverviewRows(rows, 'debt').map(r => r.client.id), [1]);
  assert.equal(filterOverviewRows(rows, '').length, 2);
  assert.equal(filterOverviewRows(rows, 'unknown').length, 2);
  assert.equal(overviewCounts(buildOverviewRows(data, [client(2)], period)).debt, 0);
  assert.equal(filterOverviewRows(rows, '', '2:0').length, 2, 'the existing chart filters continue to work');
  assert.equal(filterOverviewRows(rows, '', '9:9').length, 2);
  assert.equal(filterOverviewRows([], 'expiry').length, 0);
});

test('seven ring summaries keep monthly eligibility, unpaid records and historic contract debt separate', () => {
  const data = {
    invoices: [{ client_id: 1, period, status: 'unprocessed' }, { client_id: 5, period, status: 'posted' }],
    book_periods: [{ client_id: 5, period, voucher_count: 1 }],
    balance_progress: [{ client_id: 5, period, has_data: true }],
    tax_records: [
      { client_id: 1, period, tax_type: '增值税', status: 'submitted', payment_status: 'paid' },
      { client_id: 1, period, tax_type: '个人所得税', status: 'preparing', payment_status: 'unpaid' },
      { client_id: 5, period, tax_type: '增值税', status: 'submitted', payment_status: 'paid' },
    ],
    contracts: [contract(), contract(2, { client_id: 2, end_period: '2026-10', debt_fen: 90000 }), contract(3, { client_id: 5, end_period: '2027-04' })],
  };
  const rows = buildOverviewRows(data, [client(), client(2, { enabled: 0 }), client(3, { start_period: '2027-01' }), client(4, { start_period: '2026-10' }), client(5)], period);
  const summaries = Object.fromEntries(overviewSummaries(rows).map(summary => [summary.key, summary]));
  assert.deepEqual(Object.keys(summaries), ['tickets', 'book', 'filing', 'payment', 'expiry', 'debt', 'inactive']);
  assert.deepEqual(summaries.tickets.counts, [1, 1, 1]);
  assert.deepEqual(summaries.book.counts, [2, 1]);
  assert.deepEqual(summaries.filing.counts, [1, 0, 2]);
  assert.deepEqual(summaries.payment.counts, [1, 1, 1], 'unregistered payment is distinct from an unpaid record, including IIT');
  assert.deepEqual(summaries.expiry.counts, [2, 2, 1]);
  assert.deepEqual(summaries.debt.counts, [2, 1, 2], 'disabled clients retain historic debt, and a fully paid contract can expire');
  assert.deepEqual(summaries.inactive.counts, [1, 1, 1], 'a newly opened book is not evidence of three months without vouchers');
  assert.deepEqual(Object.fromEntries(Object.entries(summaries).map(([key, summary]) => [key, summary.pending])), {
    tickets: 1, book: 2, filing: 1, payment: 1, expiry: 2, debt: 1, inactive: 1,
  });
  assert.deepEqual(filterOverviewRows(rows, 'book:0').map(r => r.client.id), [1, 4]);
  assert.deepEqual(filterOverviewRows(rows, 'payment:0').map(r => r.client.id), [4]);
  assert.deepEqual(filterOverviewRows(rows, 'payment:1').map(r => r.client.id), [1]);
  assert.deepEqual(filterOverviewRows(rows, 'payment:2').map(r => r.client.id), [5]);
  assert.deepEqual(filterOverviewRows(rows, 'expiry:1').map(r => r.client.id), [1, 2]);
  assert.deepEqual(filterOverviewRows(rows, 'debt:1').map(r => r.client.id), [2]);
  assert.deepEqual(filterOverviewRows(rows, 'inactive:1').map(r => r.client.id), [4]);
});
