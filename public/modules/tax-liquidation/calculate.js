import {calculateQuick} from './quick/calculate.js';
import {calculateFull} from './full/calculate.js';
import {amount,allocate,sum,taxAt,SHARE_SCALE} from './money.js';
import {validateNumbers,validateCompany,shareTotal} from './validation.js';
export const INSOLVENCY_WARNING = '预警：现有资金不足以全额清偿债务（资不抵债），不符合常规注销条件，依法应走破产清算程序！';
export function calculate(draft) {
  const mode = draft.mode === 'full' ? calculateFull(draft.full) : calculateQuick(draft.quick);
  const s = draft.settlement;
  const expense = amount(s.agencyFee)+amount(s.miscFee), employeeExtra = amount(s.employeeExtra);
  const deductibleCosts = expense+employeeExtra, relatedTaxes = amount(s.vat)+amount(s.otherTax);
  const taxableIncome = mode.disposalGain+mode.debtIncome-deductibleCosts-relatedTaxes;
  const incomeTax = s.taxRate === 'manual' ? amount(s.manualTax) : taxAt(taxableIncome,['0','5','25'].includes(s.taxRate) ? Number(s.taxRate) : 25);
  const cashPool = mode.balance.cash+mode.realizedTotal;
  const taxTotal = (mode.balance.taxPayable || 0)+relatedTaxes+incomeTax;
  const priorityDues = [expense,(mode.balance.employeePayable || 0)+employeeExtra,taxTotal,mode.externalPaid];
  let cashLeft = Math.max(0,cashPool);
  const priorities = ['清算费用','职工工资、社保及法定补偿','全部税款','外部债务实际清偿'].map((label,i) => {
    const due = Math.max(0,priorityDues[i]), covered = Math.min(cashLeft,due);
    cashLeft -= covered;
    return {label,due,covered,shortfall:due-covered,left:cashLeft};
  });
  const insolvent = priorities.some(row => row.shortfall > 0);
  const balanceDifference = mode.assetsTotal-mode.liabilitiesTotal-mode.equityTotal;
  const numberIssues = validateNumbers(draft), companyIssues = validateCompany(draft);
  const sharesTotal = shareTotal(draft), sharesValid = sharesTotal === 100*SHARE_SCALE && !numberIssues.some(text => text.includes('持股比例'));
  const pending = [...mode.pending];
  if (balanceDifference !== 0) pending.push('清算基准日资产负债尚未平衡');
  if (!sharesValid) pending.push('股东持股比例合计须为100%');
  if (numberIssues.length) pending.push(...numberIssues);
  const distributable = insolvent || pending.length ? 0 : cashLeft;
  const portions = allocate(distributable,draft.shareholders);
  const distributions = draft.shareholders.map((row,i) => {
    const received = portions[i], cost = amount(row.paid), gain = received-cost;
    return {...row,received,cost,gain,personalTaxHint:row.type === 'natural' && gain > 0,estimatedPersonalTax:row.type === 'natural' && gain > 0 ? taxAt(gain,20) : 0};
  });
  const taxIssues = [];
  if (taxableIncome > 0 && s.taxRate === '5') taxIssues.push('当前采用5%测算，清算所得税适用口径尚需核定');
  if (taxableIncome > 0 && s.taxRate === '0' && !s.exemptionBasis.trim()) taxIssues.push('正数清算所得选择0%，请补充免税依据或改用核定税额');
  const unpaidTaxKinds = Object.entries({vat:'增值税',income:'企业所得税',personal:'个人所得税',other:'印花税及其他地方税费'}).filter(([key]) => !s.taxes[key]).map(([,label]) => label);
  const reviewIssues = [...pending,...companyIssues,...taxIssues,...unpaidTaxKinds.map(text => `${text}尚未勾选结清`)];
  if (draft.sample) reviewIssues.unshift('当前使用示例数据，请核实后替换');
  const ready = !insolvent && !reviewIssues.length;
  return {...mode,expense,employeeExtra,deductibleCosts,relatedTaxes,taxableIncome,incomeTax,taxTotal,cashPool,priorities,insolvent,shortfall:sum(priorities.map(row => row.shortfall)),balanceDifference,sharesTotal,sharesValid,numberIssues,companyIssues,pending,distributable,remaining:cashLeft,distributions,paidTotal:sum(draft.shareholders.map(row => amount(row.paid))),reviewIssues,unpaidTaxKinds,ready};
}
