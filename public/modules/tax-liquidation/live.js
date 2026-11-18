import {money,SHARE_SCALE,sum} from './money.js';
import {h} from './shared/fields.js';
import {INSOLVENCY_WARNING} from './calculate.js';
export function liveViews(draft,t) {
  const balanced = t.balanceDifference===0 && !t.numberIssues.length;
  const views = {
    'capital-link':draft.quick.capitalLinked?'正在跟随股东实缴合计':'已改为手动金额',
    'share-total':`<span class="tliq-status ${t.sharesValid?'tliq-good':'tliq-bad'}">${t.sharesValid?'●':'!'} 持股合计 ${(t.sharesTotal/SHARE_SCALE).toFixed(6).replace(/\.?0+$/,'')}% ${t.sharesValid?'':'（应为100%）'}</span><span class="tliq-small">实缴合计 ¥ ${money(t.paidTotal)}</span>`,
    balance:`<div class="tliq-balance ${balanced?'tliq-good':'tliq-bad'}" role="status"><strong>${balanced?'● 已平账':'● 尚未平账'}</strong><span>${draft.mode==='quick'?'货币资金 = 实收资本 + 未分配利润 + 未交税费':'资产总计 = 负债合计 + 所有者权益合计'}</span><b>差额 ¥ ${money(t.balanceDifference)}</b>${t.numberIssues.length?'<span>请先修正金额/比例格式</span>':''}</div>`,
    'tax-formula':`<div class="tliq-equation">${money(t.disposalGain)} + ${money(t.debtIncome)} − ${money(t.deductibleCosts)} − ${money(t.relatedTaxes)}<strong>= ¥ ${money(t.taxableIncome)}</strong></div>`,
    'income-tax':`<div class="tliq-calculated">本次清算所得税 <strong>¥ ${money(t.incomeTax)}</strong></div>`,
    waterfall:`<div class="tliq-cash-pool"><span>可用现金池<small>基准日货币资金 + 资产实际变现总额</small></span><strong>¥ ${money(t.cashPool)}</strong></div><ol class="tliq-waterfall">${t.priorities.map(row => `<li class="${row.shortfall?'tliq-shortfall':''}"><span>${h(row.label)}<small>支付后可用 ¥ ${money(row.left)}</small></span><b>− ¥ ${money(row.due)}</b>${row.shortfall?`<em>缺口 ¥ ${money(row.shortfall)}</em>`:'<i>✓ 可覆盖</i>'}</li>`).join('')}</ol><div class="tliq-calculated">${t.insolvent?'当前资金缺口':'清偿后预计余额'}<strong>¥ ${money(t.insolvent?t.shortfall:t.remaining)}</strong></div>`,
    distributions:`${t.pending.length||t.insolvent?'<p class="tliq-inline-warning">存在未平账、待处理债务、数据错误或资金不足，当前暂停股东分配。</p>':''}<div class="tliq-table-scroll"><table class="tliq-result-table"><thead><tr><th>股东</th><th>比例</th><th>应分得财产</th><th>实缴出资成本</th><th>投资净损益</th></tr></thead><tbody>${t.distributions.map(row => `<tr><td>${h(row.name||'待填写')}</td><td>${h(row.ratio||'0')}%</td><td class="tliq-number">${money(row.received)}</td><td class="tliq-number">${money(row.cost)}</td><td class="tliq-number">${money(row.gain)}</td></tr>`).join('')}<tr class="tliq-total-row"><td colspan="2">合计</td><td class="tliq-number">${money(sum(t.distributions.map(row=>row.received)))}</td><td class="tliq-number">${money(t.paidTotal)}</td><td class="tliq-number">${money(t.distributable-t.paidTotal)}</td></tr></tbody></table></div>${t.distributions.filter(row=>row.personalTaxHint).map(row=>`<p class="tliq-blue-note">${h(row.name||'自然人股东')}：超出实缴出资 ¥ ${money(row.gain)}，超出部分需按财产转让所得缴纳20%个税（测算 ¥ ${money(row.estimatedPersonalTax)}）。涉及股息部分请核实分类，本表为税前分配。</p>`).join('')}`,
  };
  for (const row of t.assetRows) views[`gain-${row.key}`]=`资产处置损益 <strong class="${row.gain<0?'tliq-bad':'tliq-good'}">¥ ${money(row.gain)}</strong>`;
  for (const row of t.debtRows) views[`debt-${row.key}`]=`公司实际支付 <strong>¥ ${money(row.paid)}</strong>　债务清偿所得 <strong>¥ ${money(row.income)}</strong>`;
  return views;
}
export function renderSummary(t) {
  return [['基准日资产',t.assetsTotal,'公司关门时的账面家底'],['可用现金池',t.cashPool,'现金余额 + 实际变现'],['应清偿税款',t.taxTotal,'基准日欠税 + 清算期税'],['可分配财产',t.distributable,'先清偿，再按比例分配']].map(([label,value,hint])=>`<div class="tliq-metric"><span>${label}</span><strong>¥ ${money(value)}</strong><small>${hint}</small></div>`).join('');
}
export function renderWarnings(t) {
  return `${t.insolvent?`<div class="tliq-danger-alert" role="alert"><strong>${INSOLVENCY_WARNING}</strong><span>当前现金缺口 ¥ ${money(t.shortfall)}，已暂停股东分配。请结合全部财产及法定破产条件核实处理。</span></div>`:''}${t.numberIssues.length?`<div class="tliq-inline-warning" role="alert">${t.numberIssues.map(h).join('；')}</div>`:''}`;
}
