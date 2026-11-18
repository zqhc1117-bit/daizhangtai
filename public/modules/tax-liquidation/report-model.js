import {calculate,INSOLVENCY_WARNING} from './calculate.js';
import {ASSETS,LIABILITIES,EQUITY,DEBT_METHODS} from './accounts.config.js';
import {MODE_LABELS} from './modes.config.js';
import {money,amount} from './money.js';
const p = text => ({type:'paragraph',text});
const table = (headers,rows,numeric=[]) => ({type:'table',headers,rows,numeric});
const val = value => value?.trim() || '【待填写】';
export function buildReport(draft) {
  const totals = calculate(draft), c = draft.company;
  const balanceRows = (draft.mode === 'full' ? [...ASSETS,...LIABILITIES,...EQUITY] : [ASSETS[0],LIABILITIES.find(row => row.key==='taxPayable'),EQUITY[0],EQUITY.at(-1)]).map(row => [row.label,money(totals.balance[row.key] || 0)]);
  balanceRows.push(['资产总计',money(totals.assetsTotal)],['负债合计',money(totals.liabilitiesTotal)],['所有者权益合计',money(totals.equityTotal)],['资产－负债－权益（差额）',money(totals.balanceDifference)]);
  const taxState = key => draft.settlement.taxes[key] ? '已勾选结清，待凭证核实' : '尚未确认结清';
  const sections = [
    {title:'一、企业基本情况',blocks:[
      p(`${val(c.name)}，统一社会信用代码：${val(c.creditCode)}，法定代表人：${val(c.legalPerson)}，成立日期：${val(c.established)}，注册资本：${c.registeredCapital?money(amount(c.registeredCapital))+'元':'【待填写】'}，注册地址：${val(c.address)}。`),
      p(`本公司因${c.reason==='其他'?val(c.reasonOther):c.reason}开展清算，清算基准日为${val(c.baseDate)}，清算结束日为${val(c.endDate)}。清算组负责人：${val(c.leader)}；清算组成员：${val(c.members)}。`),
      table(['股东名称','股东性质','持股比例','认缴出资（元）','实缴出资（元）'],draft.shareholders.map(row => [val(row.name),row.type==='corporate'?'法人企业':'自然人',`${row.ratio || '0'}%`,money(amount(row.subscribed)),money(amount(row.paid))]),[2,3,4]),
    ]},
    {title:'二、清算基准日资产负债情况对照表',blocks:[
      p(`截至${val(c.baseDate)}，本公司按${MODE_LABELS[draft.mode]}录入的资产、负债和所有者权益如下，金额单位：人民币元。`),table(['项目','基准日账面金额（元）'],balanceRows,[1]),
      p(totals.balanceDifference===0 && !totals.numberIssues.length?'上述录入数据的资产、负债及所有者权益平衡。':`上述录入数据尚待核对，平账差额为${money(totals.balanceDifference)}元；不能据此认定基准日财务数据已经核实。`),
    ]},
    {title:'三、资产清理与变现处置情况',blocks:[
      ...(totals.assetRows.length ? [table(['资产项目','账面价值（元）','实际变现（元）','处置损益（元）','处置说明'],totals.assetRows.map(row => [row.label,money(row.book),draft.full.assets[row.key].realized.trim()?money(row.realized):'待填写',money(row.gain),row.note || '待补充']),[1,2,3])] : [p(draft.mode==='quick'?'本报告采用极速模式，假设除货币资金外无其他待处置资产；如存在应收款、存货、固定资产等，应改用完整模式。':'基准日未录入金额大于零的非货币资产，无资产变现卡片。')]),
      p(`基准日货币资金${money(totals.balance.cash)}元，非货币资产实际变现合计${money(totals.realizedTotal)}元，资产处置净损益${money(totals.disposalGain)}元，可用现金池合计${money(totals.cashPool)}元。`),
    ]},
    {title:'四、税款结清及债权债务清偿情况',blocks:[
      table(['税款项目','金额（元）','核实状态'],[['基准日未交税费',money(totals.balance.taxPayable || 0),'纳入本次优先清偿'],['清算期增值税及附加',money(amount(draft.settlement.vat)),taxState('vat')],['清算所得税',money(totals.incomeTax),taxState('income')],['印花税及其他税费',money(amount(draft.settlement.otherTax)),taxState('other')]], [1]),
      p(`个人所得税完税状态：${taxState('personal')}。完税勾选为清算组自行核实记录，实际结清情况应与申报表、缴款凭证及清税证明一致。`),
      ...(totals.debtRows.length ? [table(['债务项目','账面金额（元）','处理方式','实际支付（元）','豁免所得（元）'],totals.debtRows.map(row => [row.label,money(row.book),DEBT_METHODS[row.method] || '待选择',money(row.paid),money(row.income)]),[1,3,4]),...totals.debtRows.filter(row => row.note || row.method==='assumed').map(row => p(row.method==='assumed'?`${row.label}${money(row.book)}元，拟由股东${val(row.assignee)}个人承诺兜底承接，公司实际支付0.00元；${row.consent?'已勾选取得债权人书面同意，债务转移以书面协议为准':'尚未确认取得债权人书面同意，公司债务仍待处理'}。${row.note}`:`${row.label}清偿说明：${row.note}`))] : [p(draft.mode==='quick'?'极速模式未录入外部债务；假设不存在除基准日欠税外的其他待偿债务。':'基准日未录入应付账款、其他应付款等外部债务。')]),
      ...(totals.debtIncome>0?[p(`书面豁免产生的债务清偿所得合计${money(totals.debtIncome)}元，已并入本报告的清算所得测算。`)]:[]),
      table(['清偿顺序','应付金额（元）','现金可覆盖（元）','缺口（元）'],totals.priorities.map((row,i) => [`${i+1}. ${row.label}`,money(row.due),money(row.covered),money(row.shortfall)]),[1,2,3]),
    ]},
    {title:'五、清算费用及清算所得计算',blocks:[
      table(['计算项目','金额（元）'],[['注销代办/审计评估费',money(amount(draft.settlement.agencyFee))],['公告及办公杂费',money(amount(draft.settlement.miscFee))],['本期新增职工工资社保及法定补偿',money(totals.employeeExtra)],['资产处置净损益',money(totals.disposalGain)],['加：债务清偿净所得',money(totals.debtIncome)],['减：清算费用（含本期新增职工支出）',money(totals.deductibleCosts)],['减：相关税金及附加',money(totals.relatedTaxes)],['清算所得（测算）',money(totals.taxableIncome)],['清算所得税',money(totals.incomeTax)]],[1]),
      p(`清算所得 = 资产处置净损益 + 债务清偿净所得 − 清算费用 − 相关税金及附加。本报告所得税采用${draft.settlement.taxRate==='manual'?'手动输入的申报/核定税额':draft.settlement.taxRate+'%税率测算（负数清算所得不计税）'}。${draft.settlement.exemptionBasis?'免税依据：'+draft.settlement.exemptionBasis+'。':''}账面值暂按计税基础测算；税会差异、可弥补亏损等以实际清算申报为准。`),
    ]},
    {title:'六、剩余财产分配方案及清算结论',blocks:[
      p(`可用现金池${money(totals.cashPool)}元，按清偿顺序扣减后预计余额${money(totals.remaining)}元，当前可列入股东分配方案的金额为${money(totals.distributable)}元。${totals.pending.length?'因存在待处理事项，当前暂停分配，待核实后重新计算。':''}`),
      table(['股东名称','持股比例','应分得财产（元）','实缴成本（元）','投资净损益（元）'],totals.distributions.map(row => [val(row.name),`${row.ratio || '0'}%`,money(row.received),money(row.cost),money(row.gain)]),[1,2,3,4]),
      ...totals.distributions.filter(row => row.personalTaxHint).map(row => p(`自然人股东${val(row.name)}分配金额超过实缴出资${money(row.gain)}元，超出部分需按财产转让所得缴纳20%个税（提示测算${money(row.estimatedPersonalTax)}元）。涉及累计未分配利润、盈余公积的，应区分股息与投资转让所得并按实际适用口径核实；本表列示税前分配方案，未从公司现金池再次扣减股东个人税款。`)),
      p(totals.insolvent?`${INSOLVENCY_WARNING}按当前录入数据，现金缺口合计${money(totals.shortfall)}元，禁止向股东分配财产。`:totals.ready?'清算结论：按本报告录入数据，资产负债平衡，各顺序清偿金额均有现金覆盖，税款已勾选结清。清算组应据实核对凭证、履行债权人通知及公告等程序，将清算方案报股东会或人民法院确认后依法办理后续手续。':'清算结论：本报告为待核实草稿，尚不能确认税务清算及债务清偿已完成。'),
      ...(!totals.ready ? [p(`待核实事项：${totals.reviewIssues.slice(0,30).join('；') || '资金不足，需依法处理清偿程序'}。`)] : []),
    ]},
  ];
  return {schemaVersion:1,title:`关于 ${val(c.name)} 税务清算及财产清偿报告`,companyName:c.name || '企业',mode:draft.mode,sample:draft.sample,status:totals.insolvent?'资金不足 · 暂停分配':totals.ready?'待签署报告':'待核实草稿',sections,totals,
    signatures:[`清算组负责人（签字）：${c.leader || '________________'}`,`清算组成员（签字）：${c.members || '________________'}`,`${c.name || '企业名称'}（公章）`,`落款日期：${c.endDate || '____年____月____日'}`]};
}
