import {ASSETS,LIABILITIES,EQUITY,DISPOSABLE_ASSETS,EXTERNAL_DEBTS,DEBT_METHODS} from '../accounts.config.js';
import {amount,money} from '../money.js';
import {field,select,textarea,checkbox,section,h} from '../shared/fields.js';
export function renderFullBalanceForm(draft) {
  const upload = section('从资产负债表带入','读取 Excel 的期末余额，支持常见左右并排报表。仅在本浏览器解析，不上传文件。',`<label class="tliq-upload"><span class="tliq-upload-icon">⇧</span><strong>选择资产负债表 Excel</strong><span>.xlsx / .xls · 最大10MB</span><input type="file" data-tliq-upload accept=".xlsx,.xls" aria-label="上传资产负债表 Excel"></label><div class="tliq-import-message" data-tliq-import-message role="status"></div><p class="tliq-note">固定资产、无形资产请使用净值。识别结果请对照原表核实；不覆盖未匹配的科目。</p>`);
  const groups = [[ASSETS,'资产','公司拥有什么'],[LIABILITIES,'负债','公司还欠什么'],[EQUITY,'所有者权益','股东投入与历年积累']].map(([accounts,title,hint]) => section(`${title} · ${hint}`,'金额统一按元填写，可手动调整。',`<div class="tliq-fields">${accounts.map(row => field(`full.balance.${row.key}`,row.label,draft.full.balance[row.key],{hint:row.hint,money:true})).join('')}</div>`)).join('');
  return upload+groups+`<div data-tliq-live="balance"></div>`;
}
export function renderFullTreatmentForm(draft) {
  const assets = DISPOSABLE_ASSETS.filter(row => amount(draft.full.balance[row.key]) > 0);
  const debts = EXTERNAL_DEBTS.filter(row => amount(draft.full.balance[row.key]) > 0);
  const assetCards = assets.map(row => {
    const asset = draft.full.assets[row.key];
    return `<div class="tliq-treatment"><div class="tliq-treatment-head"><strong>${h(row.label)}</strong><span>账面价值 <b>¥ ${money(amount(draft.full.balance[row.key]))}</b></span></div><div class="tliq-fields">${field(`full.assets.${row.key}.realized`,'实际变现/收回金额',asset.realized,{hint:'确实收到的钱；全部损失或无偿处置填0',money:true,full:true})}${textarea(`full.assets.${row.key}.note`,'处置说明',asset.note,'例如：收回客户欠款、设备转让、无回收价值等')}</div><div class="tliq-treatment-result" data-tliq-live="gain-${row.key}"></div></div>`;
  }).join('');
  const debtCards = debts.map(row => {
    const debt = draft.full.debts[row.key];
    return `<div class="tliq-treatment"><div class="tliq-treatment-head"><strong>${h(row.label)}</strong><span>账面债务 <b>¥ ${money(amount(draft.full.balance[row.key]))}</b></span></div><div class="tliq-fields">
      ${select(`full.debts.${row.key}.method`,'如何处理这笔债务',debt.method,Object.entries(DEBT_METHODS))}
      ${debt.method==='partial'?field(`full.debts.${row.key}.paid`,'实际支付金额',debt.paid,{hint:'不能超过账面债务；免除差额计入清算所得',money:true}):''}
      ${debt.method==='assumed'?field(`full.debts.${row.key}.assignee`,'承接股东姓名/名称',debt.assignee,{full:true}):''}
      ${textarea(`full.debts.${row.key}.note`,['waived','partial'].includes(debt.method)?'书面豁免依据及说明':'清偿/承接说明',debt.note)}
      </div>${['waived','partial'].includes(debt.method)?'<p class="tliq-blue-note">豁免债务依法需并入清算所得计算企业所得税。</p>':''}
      ${debt.method==='assumed'?`${checkbox(`full.debts.${row.key}.consent`,'已取得债权人书面同意，明确公司债务转由股东承接',debt.consent)}<p class="tliq-note">股东单方承诺不等于公司债务已经解除；未取得债权人同意时，报告保留待处理状态并暂停股东分配。</p>`:''}
      <div class="tliq-treatment-result" data-tliq-live="debt-${row.key}"></div></div>`;
  }).join('');
  return section('把非现金家底处理掉','自动列出基准日金额大于0的非货币资产。变现收入用于后续还债。',assetCards || '<p class="tliq-empty">没有待处理的非货币资产。</p>')
    +section('把外部欠款处理清楚','职工欠薪和基准日欠税将在第4步按法定顺序单独清偿。',debtCards || '<p class="tliq-empty">没有待处理的外部债务。</p>');
}
