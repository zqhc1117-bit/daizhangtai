// Confirm actual bookkeeping entries before a reconciliation is posted.
import { accountPickerField, bindAccountPicker, escapeHtml as esc } from './account-picker.js';

const amountToFen = value => {
  const text = String(value || '').trim();
  if (!text) return 0;
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) throw new Error('金额请填写非负数字，最多两位小数。');
  const [whole, fraction = ''] = text.split('.');
  const n = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(n)) throw new Error('金额超出允许范围。');
  return n;
};
const leafAccounts = accounts => accounts.filter(a => !accounts.some(b => b.code !== a.code && String(b.code).startsWith(String(a.code))));

// onSubmit(voucher) must post the reconciliation and return the server response.
// Resolves with that response on success, or null if the operator cancels.
export async function confirmReconcileVoucher({api, clientId, clientName, transaction, invoice, draft, basis, onSubmit}) {
  const period = String(draft.date || '').slice(0, 7);
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(period)) throw new Error('请先填写有效的交易日期。');
  const book = await api(`/api/books/${encodeURIComponent(clientId)}?period=${encodeURIComponent(period)}`);
  const accounts = book.accounts || [];
  const allowed = new Set(leafAccounts(accounts).filter(account => account.report_key !== 'prior_adjustment').map(account => account.code));
  if (!allowed.size) throw new Error('该账套没有可用的末级科目，请先设置科目。');
  const deferred = new Map();
  const choices = [...accounts];
  const accountValue = line => line.business_parent_code ? `draft:${line.business_parent_code}:${line.business_sub_account}` : String(line.account_code || '');
  for (const line of draft.lines) {
    if (!line.business_parent_code) continue;
    const code = accountValue(line);
    if (deferred.has(code)) continue;
    const parent = accounts.find(account => account.code === line.business_parent_code);
    deferred.set(code, {business_parent_code:line.business_parent_code,business_sub_account:line.business_sub_account});
    choices.push({code,displayCode:line.business_parent_code,name:line.business_sub_account,category:parent?.category,note:parent?.name||'确认时建立明细'});
    allowed.add(code);
  }
  const details = invoice
    ? `购买方 ${esc(invoice.buyer || '—')}　发票金额 ¥ ${esc(invoice.amount)}`
    : `交易对方 ${esc(transaction?.counterparty || '—')}　流水金额 ¥ ${esc(transaction?.amount)}`;
  const priorFocus = document.activeElement, root = document.createElement('div');
  root.innerHTML = `<div class="modal-backdrop"><section class="modal wide-modal" role="dialog" aria-modal="true" aria-labelledby="rc-voucher-title"><div class="modal-header"><h2 id="rc-voucher-title">编辑待入账凭证</h2><button type="button" data-rv-cancel aria-label="关闭">×</button></div><form><div class="modal-body"><p><b>${esc(clientName || book.client?.name || clientId)}</b>　${esc(draft.date)}　${details}</p>${basis?.length ? `<p>${esc(basis.join(' · '))}</p>` : ''}<label class="field"><span>凭证摘要 *</span><input name="summary" value="${esc(draft.summary)}" maxlength="200" required></label><div class="rc-scroll"><table class="rc-edit rc-voucher-lines"><thead><tr><th>分录摘要</th><th>科目</th><th>借方金额（元）</th><th>贷方金额（元）</th><th></th></tr></thead><tbody data-rv-lines></tbody></table></div><button type="button" class="btn" data-rv-add>＋ 添加分录</button><div data-rv-duplicate hidden><p data-rv-duplicate-message></p><label class="field"><span>核对说明 *</span><textarea name="duplicate_review_note" maxlength="500" placeholder="填写重复凭证核验依据"></textarea></label></div></div><div class="modal-error" data-rv-error role="alert"></div><div class="modal-footer"><button type="button" class="btn" data-rv-cancel>取消</button><button type="submit" class="btn btn-primary">确认入账</button></div></form></section></div>`;
  document.body.append(root);
  const form = root.querySelector('form'), rows = root.querySelector('[data-rv-lines]');
  const inputAmount = fen => fen ? (fen / 100).toFixed(2) : '';
  const addLine = (line={}) => {
    const tr=document.createElement('tr');
    tr.dataset.originalSummary=line.summary||'';
    const value=accountValue(line);
    const field=accountPickerField(choices,allowed.has(value)?value:'','data-rv-account aria-label="科目"');
    tr.innerHTML=`<td><input data-rv-summary aria-label="分录摘要" maxlength="200" value="${esc(line.summary||'')}" placeholder="同凭证摘要"></td><td>${field}</td><td><input data-rv-debit inputmode="decimal" aria-label="借方金额" value="${inputAmount(line.debit_fen)}"></td><td><input data-rv-credit inputmode="decimal" aria-label="贷方金额" value="${inputAmount(line.credit_fen)}"></td><td><button type="button" class="rc-link" data-rv-remove>删除</button></td>`;
    rows.append(tr);
  };
  draft.lines.forEach(addLine);
  const picker = bindAccountPicker(form, choices, {autoMatch:true,include:account=>allowed.has(account.code)});
  return new Promise(resolve => {
    let busy = false;
    const close = result => { if (busy) return; picker.close(); document.removeEventListener('keydown', keyHandler); root.remove(); priorFocus?.focus?.(); resolve(result); };
    const keyHandler = event => { if(event.key==='Escape' && !event.defaultPrevented && !event.isComposing && event.keyCode!==229) close(null); };
    document.addEventListener('keydown', keyHandler);
    root.querySelectorAll('[data-rv-cancel]').forEach(button => button.addEventListener('click', () => close(null)));
    root.querySelector('[data-rv-add]').addEventListener('click', () => addLine());
    rows.addEventListener('click', event => { const button=event.target.closest('[data-rv-remove]'); if(button) button.closest('tr').remove(); });
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (busy) return;
      const error = root.querySelector('[data-rv-error]'); error.textContent = '';
      try {
        const summary = form.querySelector('[name="summary"]').value.trim();
        if (!summary) throw new Error('请填写凭证摘要。');
        const lines = [...rows.querySelectorAll('tr')].map((row,index) => {
          const selected=row.querySelector('[data-account-value]').value;
          if(!allowed.has(selected))throw new Error(`请选择第 ${index+1} 行分录的科目。`);
          const lineSummary=row.querySelector('[data-rv-summary]').value.trim();
          return {...(deferred.get(selected)||{account_code:selected}),
            summary:summary!==draft.summary&&lineSummary===row.dataset.originalSummary?'':lineSummary,
            debit_fen:amountToFen(row.querySelector('[data-rv-debit]').value),
            credit_fen:amountToFen(row.querySelector('[data-rv-credit]').value)};
        });
        if (lines.length < 2 || lines.some(line=>(line.debit_fen > 0) === (line.credit_fen > 0))) throw new Error('请至少填写两条分录，每条只填写一方金额。');
        const debit=lines.reduce((sum,line)=>sum+line.debit_fen,0),credit=lines.reduce((sum,line)=>sum+line.credit_fen,0);
        if (!Number.isSafeInteger(debit) || !Number.isSafeInteger(credit) || debit!==credit) throw new Error('借贷金额合计必须相等。');
        const duplicateNote=form.querySelector('[name="duplicate_review_note"]').value.trim();
        if (!root.querySelector('[data-rv-duplicate]').hidden && !duplicateNote) throw new Error('请填写重复凭证核对说明。');
        const voucher={date:draft.date,summary,lines,...(duplicateNote?{duplicate_review_note:duplicateNote}:{})};
        busy=true; picker.close(); root.querySelectorAll('button,input,select,textarea').forEach(el=>el.disabled=true);
        try { const result=await onSubmit(voucher); busy=false; close(result); }
        catch (err) {
          busy=false; root.querySelectorAll('button,input,select,textarea').forEach(el=>el.disabled=false);
          if (err.details?.code==='possible_duplicate') {
            const list=err.details.candidates || [];
            root.querySelector('[data-rv-duplicate-message]').textContent=`${err.message}：${list.map(v=>`${v.date} ${v.number} ${v.summary || ''}`).join('；')}`;
            root.querySelector('[data-rv-duplicate]').hidden=false;
            form.querySelector('[name="duplicate_review_note"]').focus();
          }
          error.textContent=err.message || '保存失败，请重试。';
        }
      } catch (err) { error.textContent=err.message; }
    });
    form.querySelector('[name="summary"]').focus();
  });
}
export function statementPostPreview({transaction, businessAccount, bankAccount}) {
  const text = String(transaction?.amount || '').trim().replace(/[,¥￥]/g, '');
  if (!/^[+-]?\d+(?:\.\d{1,2})?$/.test(text)) throw new Error('金额无效，请填写非零金额后再入账。');
  const incoming = !text.startsWith('-');
  const fen = amountToFen(text.replace(/^[+-]/, ''));
  if (!fen) throw new Error('流水金额为零，不能入账。');
  const business = {account_code: String(businessAccount.code), account_name: businessAccount.name || '', debit_fen: incoming ? 0 : fen, credit_fen: incoming ? fen : 0};
  const bank = {account_code: String(bankAccount.code), account_name: bankAccount.name || '', debit_fen: incoming ? fen : 0, credit_fen: incoming ? 0 : fen};
  return {direction: incoming ? '收款' : '付款', amount: (fen / 100).toFixed(2), lines: incoming ? [bank, business] : [business, bank]};
}

function statementLineText(line) {
  const amount = ((line.debit_fen || line.credit_fen) / 100).toFixed(2);
  return `${line.debit_fen ? '借' : '贷'}：${line.account_code} ${line.account_name} ¥ ${amount}`;
}

// Shows the date, amount, direction and both accounts, then posts only after confirmation.
// onSubmit receives the confirmed funding account; business account stays the one already chosen.
export async function confirmStatementPost({transaction, businessAccount, bankAccount, bankAccounts, mode, recommendation, clientName, onSubmit}) {
  if (!businessAccount?.code) throw new Error('请先确定业务科目。');
  if (!bankAccount?.code) throw new Error('请先选择资金科目。');
  const banks = bankAccounts?.length ? bankAccounts : [bankAccount];
  const priorFocus = document.activeElement;
  const root = document.createElement('div');
  const multiple = banks.length > 1;
  const bankOptions = banks.map(account => `<option value="${esc(account.code)}">${esc(account.code)} ${esc(account.name)}</option>`).join('');
  const recommendationText = recommendation?.account_code ? `${recommendation.account_code} ${recommendation.account_name || ''}` : '无';
  const modeText = mode === 'manual'
    ? '本次按手选科目入账，不会改用推荐科目。'
    : '本次采用推荐科目。取消则不入账，推荐本身不会写入账套。';
  const defaultSummary = (`${Number(String(transaction.amount).replace(/[,¥￥]/g,''))>0?'收款':'付款'} ${transaction.counterparty||''} ${transaction.note||''}`).trim().slice(0,200);
  root.innerHTML = `<div class="modal-backdrop"><section class="modal wide-modal" role="dialog" aria-modal="true" aria-labelledby="rc-statement-title"><div class="modal-header"><h2 id="rc-statement-title">确认入账分录</h2><button type="button" data-sp-cancel aria-label="关闭">×</button></div><form><div class="modal-body"><p>请核对日期、金额、收付方向、业务科目和资金科目。确认后才生成凭证并更新余额。</p><p><b>本方：${esc(clientName || '')}</b>　日期：${esc(transaction.date)}　金额：¥ ${esc(transaction.amount)}　方向：<span data-sp-direction></span></p><p>交易对方：${esc(transaction.counterparty || '未填写')}</p><label class="field"><span>凭证摘要 *</span><input name="summary" value="${esc(defaultSummary)}" required maxlength="200"></label><p>业务科目：<b data-sp-business>${esc(businessAccount.code)} ${esc(businessAccount.name || '')}</b></p><p>${esc(modeText)} 当前推荐：${esc(recommendationText)}。${esc(recommendation?.basis || '')}</p>${multiple ? `<label class="field"><span>资金科目 *</span><select data-sp-bank aria-label="资金科目">${bankOptions}</select></label>` : `<p>资金科目：<b>${esc(bankAccount.code)} ${esc(bankAccount.name || '')}</b></p>`}<div class="rc-statement-lines" data-sp-lines></div><div data-sp-duplicate hidden><p data-sp-duplicate-message></p><label class="field"><span>核对说明 *</span><textarea name="duplicate_review_note" maxlength="500" placeholder="说明已核实不是重复凭证的依据"></textarea></label></div></div><div class="modal-error" data-sp-error role="alert"></div><div class="modal-footer"><button type="button" class="btn" data-sp-cancel>取消</button><button type="submit" class="btn btn-primary">确认入账</button></div></form></section></div>`;
  document.body.append(root);
  const form = root.querySelector('form');
  const bankSelect = root.querySelector('[data-sp-bank]');
  const duplicate = root.querySelector('[data-sp-duplicate]');
  duplicate.hidden = true;
  if (bankSelect) bankSelect.value = String(bankAccount.code);
  const selectedBank = () => banks.find(account => String(account.code) === String(bankSelect ? bankSelect.value : bankAccount.code));
  const refresh = () => {
    const bank = selectedBank();
    if (!bank) { root.querySelector('[data-sp-lines]').textContent = '请选择资金科目。'; root.querySelector('[data-sp-direction]').textContent = ''; return; }
    const preview = statementPostPreview({transaction, businessAccount, bankAccount: bank});
    root.querySelector('[data-sp-direction]').textContent = preview.direction;
    root.querySelector('[data-sp-lines]').textContent = preview.lines.map(statementLineText).join('\n');
  };
  refresh();
  if (bankSelect) bankSelect.addEventListener('change', refresh);
  return new Promise(resolve => {
    let busy = false;
    const close = result => { if (busy) return; document.removeEventListener('keydown', keyHandler); root.remove(); priorFocus?.focus?.(); resolve(result); };
    const keyHandler = event => { if (event.key === 'Escape') close(null); };
    document.addEventListener('keydown', keyHandler);
    root.querySelectorAll('[data-sp-cancel]').forEach(button => button.addEventListener('click', () => close(null)));
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (busy) return;
      const error = root.querySelector('[data-sp-error]'); error.textContent = '';
      try {
        const bank = selectedBank();
        if (!bank) throw new Error('请选择本账套的银行存款或现金末级科目。');
        statementPostPreview({transaction, businessAccount, bankAccount: bank});
        const summary = form.querySelector('[name="summary"]').value.trim();
        if(!summary)throw new Error('请填写凭证摘要。');
        const duplicateNote = form.querySelector('[name="duplicate_review_note"]').value.trim();
        if (!duplicate.hidden && !duplicateNote) throw new Error('请填写重复凭证核对说明。');
        busy = true; root.querySelectorAll('button,input,select,textarea').forEach(el => { el.disabled = true; });
        try {
          const result = await onSubmit({bank_account_code: bank.code, ...(summary===defaultSummary?{}:{summary}), ...(duplicateNote ? {duplicate_review_note: duplicateNote} : {})});
          busy = false; close(result);
        } catch (err) {
          busy = false; root.querySelectorAll('button,input,select,textarea').forEach(el => { el.disabled = false; });
          if (err.details?.code === 'possible_duplicate') {
            const list = err.details.candidates || [];
            root.querySelector('[data-sp-duplicate-message]').textContent = `${err.message}：${list.map(item => `${item.date} ${item.number} ${item.summary || ''}`).join('；')}`;
            duplicate.hidden = false;
            form.querySelector('[name="duplicate_review_note"]').focus();
          }
          error.textContent = err.message || '保存失败，请重试。';
        }
      } catch (err) { error.textContent = err.message; }
    });
    (bankSelect || form.querySelector('[type="submit"]')).focus();
  });
}

export const reconcileVoucherTest = {amountToFen, leafAccounts, statementPostPreview};
