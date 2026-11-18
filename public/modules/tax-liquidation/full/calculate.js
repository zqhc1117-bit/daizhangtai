import {ACCOUNTS,ASSETS,LIABILITIES,EQUITY,DISPOSABLE_ASSETS,EXTERNAL_DEBTS,DEBT_METHODS} from '../accounts.config.js';
import {amount,sum} from '../money.js';
export function calculateFull(full) {
  const balance = Object.fromEntries(ACCOUNTS.map(row => [row.key,amount(full.balance[row.key])]));
  const pending = [];
  const assetRows = DISPOSABLE_ASSETS.filter(row => balance[row.key] > 0).map(row => {
    const treatment = full.assets[row.key];
    if (!String(treatment.realized).trim()) pending.push(`${row.label}尚未填写实际变现额`);
    const realized = amount(treatment.realized);
    return {...row,book:balance[row.key],realized,gain:realized-balance[row.key],note:treatment.note};
  });
  const debtRows = EXTERNAL_DEBTS.filter(row => balance[row.key] > 0).map(row => {
    const treatment = full.debts[row.key], book = balance[row.key];
    if (!Object.hasOwn(DEBT_METHODS,treatment.method)) pending.push(`${row.label}请选择有效处理方式`);
    if (treatment.method === 'partial' && (!String(treatment.paid).trim() || amount(treatment.paid) > book)) pending.push(`${row.label}的部分偿还金额应在0至账面金额之间`);
    if (['waived','partial'].includes(treatment.method) && !treatment.note.trim()) pending.push(`${row.label}请补充书面豁免依据`);
    if (treatment.method === 'assumed' && (!treatment.assignee.trim() || !treatment.consent)) pending.push(`${row.label}的股东承接尚待承接人及债权人书面同意`);
    const paid = treatment.method === 'paid' ? book : treatment.method === 'partial' ? Math.min(book,Math.max(0,amount(treatment.paid))) : 0;
    const income = ['waived','partial'].includes(treatment.method) ? book-paid : 0;
    return {...row,...treatment,book,paid,income,assumed:treatment.method === 'assumed' ? book : 0};
  });
  return {balance,assetsTotal:sum(ASSETS.map(row => balance[row.key])),liabilitiesTotal:sum(LIABILITIES.map(row => balance[row.key])),equityTotal:sum(EQUITY.map(row => balance[row.key])),assetRows,debtRows,pending,
    realizedTotal:sum(assetRows.map(row => row.realized)),disposalGain:sum(assetRows.map(row => row.gain)),debtIncome:sum(debtRows.map(row => row.income)),externalPaid:sum(debtRows.map(row => row.paid)),assumedTotal:sum(debtRows.map(row => row.assumed))};
}
