import { PROGRESS, getBalanceWorkProgress } from './work-progress.js';
import { getAgencyExpiry } from './contract-progress.js';

export const OVERVIEW_TODOS = [
  { key: 'tickets', label: '票据待制证', hint: '所选月份的票据台账、流水发票核对及已归档发票，去重后尚未全部制证', tone: 'info', states: PROGRESS[1].labels },
  { key: 'book', label: '本月未记账', hint: '已启用且已到建账月份，所选月份余额表没有金额数据；期初余额、当期发生额及结转余额均计入已记账', tone: 'warn', states: ['未记账', '已记账'] },
  { key: 'filing', label: '申报待登记', hint: '所选月份非个税申报尚未登记完成，含未登记和准备中', tone: 'info', states: PROGRESS[3].labels },
  { key: 'payment', label: '待缴款', hint: '所选月份已有税务记录，但尚未全部登记缴款（含个税）', tone: 'warn', states: ['未登记', '待缴款', '已登记缴款'] },
  { key: 'expiry', label: '服务到期', hint: '按所选月份统计本月到期和已到期、尚未续约的服务合同', tone: 'warn', states: ['无有效合同', '到期待续约', '服务未到期'] },
  { key: 'debt', label: '存在欠费', hint: '截至所选月份，服务合同累计应收减实收后仍有欠费', tone: 'bad', states: ['无有效合同', '存在欠费', '无欠费'] },
  { key: 'inactive', label: '近三月无凭证', hint: '启用至少三个月，含所选月份在内的连续三个月没有有效凭证', tone: 'warn', states: ['三月无凭证', '统计条件不足', '三月内有凭证'] },
];

function recentMonths(period) {
  const [year, month] = period.split('-').map(Number);
  return [2, 1, 0].map(offset => {
    const value = year * 12 + month - 1 - offset;
    return `${Math.floor(value / 12)}-${String(value % 12 + 1).padStart(2, '0')}`;
  });
}

// Use the same persisted progress and contract calculations as the existing pages.
// debt_fen is the server's receivables rollup for the selected bootstrap month.
export function buildOverviewRows(data, clients, period) {
  const contracts = (Array.isArray(data.contracts) ? data.contracts : []).filter(record => !record.deleted);
  const periods = Array.isArray(data.book_periods) ? data.book_periods : [];
  const months = recentMonths(period);
  return clients.filter(client => !client.deleted).map(client => {
    const progress = getBalanceWorkProgress(data, client.id, period);
    const expiry = getAgencyExpiry(contracts, client.id, period);
    const debt = contracts.filter(record => Number(record.client_id) === Number(client.id) && record.start_period <= period)
      .reduce((sum, record) => sum + Math.max(0, Number(record.debt_fen) || 0), 0);
    const started = !client.start_period || client.start_period <= period;
    const enabled = client.enabled !== false && client.enabled !== 0;
    const actionable = started && enabled;
    const flags = {
      tickets: actionable && progress.states[1] === 1,
      book: actionable && progress.states[2] === 0,
      filing: actionable && progress.states[3] !== 2,
      payment: actionable && progress.tax.length > 0 && progress.tax.some(record => record.payment_status !== 'paid'),
      expiry: expiry.state === 'due' || expiry.state === 'overdue',
      debt: debt > 0,
      inactive: actionable && Boolean(client.start_period) && client.start_period <= months[0]
        && !periods.some(record => Number(record.client_id) === Number(client.id) && months.includes(record.period) && Number(record.voucher_count) > 0),
    };
    const hasActiveContract = expiry.state !== 'missing' && expiry.state !== 'terminated';
    const summaryStates = {
      tickets: actionable ? progress.states[1] : -1,
      book: actionable ? progress.states[2] : -1,
      filing: actionable ? progress.states[3] : -1,
      payment: actionable ? (!progress.tax.length ? 0 : flags.payment ? 1 : 2) : -1,
      expiry: flags.expiry ? 1 : hasActiveContract ? 2 : 0,
      debt: flags.debt ? 1 : hasActiveContract ? 2 : 0,
      inactive: actionable ? (flags.inactive ? 0 : !client.start_period || client.start_period > months[0] ? 1 : 2) : -1,
    };
    return { client, progress, expiry, debt, started, enabled, flags, summaryStates };
  });
}

export function overviewCounts(rows) {
  return Object.fromEntries(OVERVIEW_TODOS.map(todo => [todo.key, rows.filter(row => row.flags[todo.key]).length]));
}

export function overviewSummaries(rows) {
  const pending = overviewCounts(rows);
  return OVERVIEW_TODOS.map(todo => {
    const counts = todo.states.map(() => 0);
    rows.forEach(row => { const state = row.summaryStates[todo.key]; if (state >= 0 && state < counts.length) counts[state]++; });
    return { ...todo, counts, total: counts.reduce((sum, count) => sum + count, 0), pending: pending[todo.key] };
  });
}

export function filterOverviewRows(rows, todo = '', progress = '') {
  const [key, state] = todo.split(':');
  if (OVERVIEW_TODOS.some(item => item.key === key)) {
    if (state === undefined) rows = rows.filter(row => row.flags[key]);
    else if (/^[0-2]$/.test(state)) rows = rows.filter(row => row.summaryStates[key] === Number(state));
  }
  if (/^[0-4]:[0-2]$/.test(progress)) {
    const [index, state] = progress.split(':').map(Number);
    rows = rows.filter(row => row.progress.states[index] === state);
  }
  return rows;
}
