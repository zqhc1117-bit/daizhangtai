import {ACCOUNTS} from './accounts.config.js';
import {toFen, scaled, shareUnits, SHARE_SCALE, sum} from './money.js';
const nonnegative = (issues, value, label, signed = false) => {
  try { if (!signed && toFen(value) < 0) issues.push(`${label}不能为负数`); }
  catch (error) { issues.push(`${label}：${error.message}`); }
};
export function validateNumbers(draft) {
  const issues = [];
  draft.shareholders.forEach((row,i) => {
    nonnegative(issues,row.paid,`股东${i+1}实缴出资`); nonnegative(issues,row.subscribed,`股东${i+1}认缴出资`);
    try {const n = scaled(row.ratio,6);if (n < 0 || n > 100*SHARE_SCALE) issues.push(`股东${i+1}持股比例须在0%至100%之间`);} catch {issues.push(`股东${i+1}持股比例格式不正确`);}
  });
  if (draft.mode === 'quick') {
    for (const [key,label] of [['cash','货币资金'],['paidCapital','实收资本'],['retainedEarnings','未分配利润'],...(draft.quick.hasTax ? [['taxPayable','基准日欠税']] : [])]) nonnegative(issues,draft.quick[key],label,key === 'retainedEarnings');
  } else {
    for (const row of ACCOUNTS) nonnegative(issues,draft.full.balance[row.key],row.label,['retainedEarnings','capitalReserve'].includes(row.key));
    for (const [key,row] of Object.entries(draft.full.assets)) if (toSafe(draft.full.balance[key]) > 0) nonnegative(issues,row.realized,'资产实际变现金额');
    for (const [key,row] of Object.entries(draft.full.debts)) if (toSafe(draft.full.balance[key]) > 0 && row.method === 'partial') nonnegative(issues,row.paid,'债务实际支付金额');
  }
  for (const [key,label] of [['agencyFee','注销代办/审计评估费'],['miscFee','公告及办公杂费'],['employeeExtra','新增职工工资社保补偿'],['vat','增值税及附加'],['otherTax','印花税及其他税费']]) nonnegative(issues,draft.settlement[key],label);
  if (draft.settlement.taxRate === 'manual') nonnegative(issues,draft.settlement.manualTax,'手动所得税额');
  else if (!['0','5','25'].includes(draft.settlement.taxRate)) issues.push('请选择有效的所得税计算方式');
  return issues;
}
function toSafe(value) {try{return toFen(value);}catch{return 0;}}
export function shareTotal(draft) {return sum(draft.shareholders.map(row => shareUnits(row.ratio)));}
export function validateCompany(draft) {
  const c = draft.company, issues = [];
  for (const [key,label] of [['name','企业名称'],['creditCode','统一社会信用代码'],['legalPerson','法定代表人'],['established','成立日期'],['registeredCapital','注册资本'],['address','注册地址'],['baseDate','清算基准日'],['endDate','清算结束日'],['leader','清算组负责人'],['members','清算组成员']]) if (!c[key].trim()) issues.push(`请填写${label}`);
  if (c.creditCode && !/^[0-9A-HJ-NPQRTUWXY]{18}$/i.test(c.creditCode)) issues.push('统一社会信用代码应为18位有效字符');
  if (c.reason === '其他' && !c.reasonOther.trim()) issues.push('请说明其他解散事由');
  const validDate = text => /^\d{4}-\d{2}-\d{2}$/.test(text) && Number.isFinite(Date.parse(text)) && new Date(text).toISOString().slice(0,10) === text;
  for (const key of ['established','baseDate','endDate']) if (c[key] && !validDate(c[key])) issues.push('日期格式不正确，请重新选择');
  if (c.baseDate && c.endDate && c.endDate < c.baseDate) issues.push('清算结束日不能早于基准日');
  if (c.established && c.baseDate && c.baseDate < c.established) issues.push('清算基准日不能早于成立日期');
  if (c.registeredCapital.trim()) nonnegative(issues,c.registeredCapital,'注册资本');
  draft.shareholders.forEach((row,i) => {
    if (!row.name.trim()) issues.push(`请填写第${i+1}位股东名称`);
    if (!['natural','corporate'].includes(row.type)) issues.push(`第${i+1}位股东性质不正确`);
  });
  return issues;
}
