import {field,checkbox,section} from './fields.js';
export function renderSettlementForm(draft) {
  const s = draft.settlement;
  const costs = section('关门善后，要花多少钱？','基准日已有的应付职工薪酬会另外带入；这里仅填清算期间新增的支出，避免重复扣减。',`<div class="tliq-fields">${field('settlement.agencyFee','注销代办/审计评估费',s.agencyFee,{money:true})}${field('settlement.miscFee','公告及办公杂费',s.miscFee,{money:true})}${field('settlement.employeeExtra','辞退员工经济补偿金及补发工资社保',s.employeeExtra,{money:true,full:true,hint:'清算期间新增、且尚未计入第2步职工欠款的金额'})}</div>`);
  const tax = section('清算期间，还要交哪些税？','基准日欠税自动带入清偿；下方只填本次新增或补缴税额。',`<div class="tliq-fields">${field('settlement.vat','清算期新增/补缴增值税及附加',s.vat,{money:true})}${field('settlement.otherTax','印花税及其他税费',s.otherTax,{money:true})}</div>
    <div class="tliq-tax-calculator"><h4>清算所得税计算器</h4><p>清算所得 = 资产处置净损益 + 债务清偿净所得 − 清算费用 − 相关税金及附加</p><div data-tliq-live="tax-formula"></div><div class="tliq-rate-options" role="group" aria-label="清算所得税计算方式">${[['0','免税/亏损 0%'],['5','小微优惠 5%'],['25','一般税率 25%'],['manual','手动输入税额']].map(([rate,label]) => `<button type="button" class="tliq-rate ${s.taxRate===rate?'active':''}" data-tliq-action="tax-rate" data-tliq-rate="${rate}" aria-pressed="${s.taxRate===rate}">${label}</button>`).join('')}</div>
    ${s.taxRate==='manual'?field('settlement.manualTax','核定/申报的清算所得税额',s.manualTax,{money:true,hint:'以实际申报或主管税务机关确认金额为准'}):''}
    ${s.taxRate==='0'?field('settlement.exemptionBasis','免税依据（所得为正时填写）',s.exemptionBasis,{full:true,hint:'亏损时自动按0计算；正数所得免税应说明依据'}):''}
    <p class="tliq-note">清算所得通常按25%计算；5%保留为试算选项，不自动认定适用小微优惠。当前按账面值测算，税会差异及可弥补亏损请通过手动税额按申报结果校准。清算费用扣除含本期新增职工支出。</p><div data-tliq-live="income-tax"></div></div>
    <div class="tliq-tax-checks"><h4>完税情况（请按实际凭证核实）</h4>${[['vat','增值税已结清'],['income','企业所得税已结清'],['personal','个人所得税已结清'],['other','印花税及其他地方税费已结清']].map(([key,label]) => checkbox(`settlement.taxes.${key}`,label,s.taxes[key])).join('')}</div>`);
  return costs+tax+section('先付该付的钱，再给股东分钱','按清算费用 → 职工工资社保补偿 → 全部税款 → 外部债务顺序逐项检查。',`<div data-tliq-live="waterfall"></div>`)
    +section('股东剩余财产分配','按持股比例计算到分；尾差按最大余数分配，合计与可分配金额一致。',`<div data-tliq-live="distributions"></div>`);
}
