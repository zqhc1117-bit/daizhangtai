import { fileBase64 } from '../../reconcile.js';
import { money } from './money.js';
import { h } from './shared/fields.js';

const categories = ['工资', '社保', '公积金'];
const allowedFile = /\.(pdf|png|jpe?g|zip)$/i;
const maxUpload = 10 * 1024 * 1024;

export function createPayrollPosting() {
  return {clientId:'', bankCode:'', banks:[], files:[], archiveId:'', result:null,
    selected:new Set(), busy:false, message:'', error:''};
}

export function renderPayrollPosting(ctx, state) {
  const clients = (ctx.clients || []).filter(client => client.enabled !== 0 && client.enabled !== false);
  const options = clients.map(client => `<option value="${h(client.id)}" ${String(client.id) === state.clientId ? 'selected' : ''}>${h(client.name)}</option>`).join('');
  return `<section id="sales-payroll" class="sales-payroll sales-upload" data-sales-payroll aria-labelledby="sales-payroll-heading">
    <h2 id="sales-payroll-heading">管理费用入账</h2>
    <p>银行回单或流水中的工资、薪酬归入工资，缴入国库的社保归入社保，住房公积金归入公积金。</p>
    <dl class="sales-payroll-totals" data-payroll-totals aria-label="本批管理费用金额"></dl>
    <div class="sales-import-fields">
      <label>本方账套<select data-payroll-client><option value="">请选择已有公司</option>${options}</select></label>
      <label>付款银行科目<select data-payroll-bank></select></label>
    </div>
    <label class="sales-payroll-files">银行回单或流水 <small>PDF、PNG、JPG 或 ZIP，可多选，合计最多 10 MB</small>
      <input type="file" data-payroll-files multiple accept=".pdf,.png,.jpg,.jpeg,.zip">
    </label>
    <div class="sales-payroll-actions">
      <button type="button" class="btn btn-primary" data-payroll-parse>识别并预览凭证</button>
      <button type="button" class="btn" data-payroll-preview hidden>重新核对</button>
    </div>
    <p class="sales-file-status" data-payroll-status role="status" aria-live="polite"></p>
    <div data-payroll-results></div>
    <button type="button" class="btn btn-primary" data-payroll-post hidden>确认入账</button>
  </section>`;
}

function totalsMarkup(result) {
  return categories.map(category => {
    const total = (result?.rows || []).filter(row => row.category === category &&
      ['ready','posted'].includes(row.status) && row.expense_fen === -row.amount_fen)
      .reduce((sum, row) => sum + BigInt(row.expense_fen), 0n);
    const display = `${(total / 100n).toLocaleString('zh-CN')}.${String(total % 100n).padStart(2,'0')}`;
    return `<div><dt>${category}</dt><dd>${result ? `¥ ${display}` : '—'}</dd></div>`;
  }).join('');
}

function resultsMarkup(result, selected) {
  if (!result) return '';
  const warnings = (result.warnings || []).map(warning => `<li>${h(warning)}</li>`).join('');
  const rows = result.rows.map(row => `<tr>
    <td>${row.status === 'ready' ? `<input type="checkbox" data-payroll-row="${h(row.id)}" aria-label="选择 ${h(row.date)} ${h(row.category)} ${h(row.counterparty)}" ${selected.has(row.id) ? 'checked' : ''}>` : '—'}</td>
    <td>${h(row.date || '日期待核实')}<small>${h(row.sources.join('、'))}</small></td>
    <td>${h(row.counterparty || '—')}${row.statement_note ? `<small>流水 · ${h(row.statement_note)}</small>` : ''}${row.receipt_note ? `<small>回单 · ${h(row.receipt_note)}</small>` : ''}</td>
    <td class="sales-payroll-number">${row.amount_fen == null ? '待核实' : h(money(row.amount_fen))}</td>
    <td>${row.category ? `管理费用 / ${h(row.category)}` : '待核实'}</td>
    <td>${row.status === 'posted'
      ? `<button type="button" class="btn sales-payroll-voucher" data-payroll-voucher="${h(row.id)}">${h(row.voucher_ref)}</button><small>${h(row.reason || '已入账')}</small>`
      : row.status === 'ready' ? '待确认入账' : `<small class="sales-payroll-review">${h(row.reason)}</small>`}</td>
  </tr>`).join('');
  return `${warnings ? `<ul class="sales-import-warnings">${warnings}</ul>` : ''}
    ${rows ? `<div class="sales-import-table"><table aria-label="工资、社保和公积金入账预览"><thead>
      <tr><th scope="col">选择</th><th scope="col">交易日期 / 原件</th><th scope="col">交易对方 / 摘要</th><th scope="col">金额（元）</th><th scope="col">明细科目</th><th scope="col">处理结果</th></tr>
    </thead><tbody>${rows}</tbody></table></div>` : ''}`;
}

export function bindPayrollPosting(container, ctx, state) {
  const root = container.querySelector('[data-sales-payroll]');
  const client = root.querySelector('[data-payroll-client]');
  const bank = root.querySelector('[data-payroll-bank]');
  const files = root.querySelector('[data-payroll-files]');
  const parse = root.querySelector('[data-payroll-parse]');
  const preview = root.querySelector('[data-payroll-preview]');
  const post = root.querySelector('[data-payroll-post]');
  const status = root.querySelector('[data-payroll-status]');
  const results = root.querySelector('[data-payroll-results]');

  const updateStatus = () => {
    status.textContent = state.error || state.message;
    status.classList.toggle('error', Boolean(state.error));
  };
  const updatePost = () => {
    const ready = state.result?.rows.some(row => row.status === 'ready');
    post.hidden = !ready;
    post.disabled = state.busy || !state.bankCode || !state.selected.size;
    post.textContent = state.selected.size ? `确认入账 ${state.selected.size} 笔` : '确认入账';
  };
  const refresh = () => {
    if (!root.isConnected) return;
    client.disabled = state.busy;
    bank.innerHTML = '<option value="">请选择付款银行科目</option>' +
      state.banks.map(account => `<option value="${h(account.code)}">${h(account.code)} ${h(account.name)}</option>`).join('');
    bank.value = state.bankCode;
    bank.disabled = state.busy || !state.banks.length;
    files.disabled = state.busy || !state.clientId;
    if (!state.files.length) files.value = '';
    parse.disabled = state.busy || !state.clientId || !state.files.length;
    preview.hidden = !state.archiveId;
    preview.disabled = state.busy;
    root.querySelector('[data-payroll-totals]').innerHTML = totalsMarkup(state.result);
    results.innerHTML = resultsMarkup(state.result, state.selected);
    results.querySelectorAll('input,button').forEach(control => { control.disabled = state.busy; });
    updatePost();
    updateStatus();
  };
  const run = async (message, action) => {
    if (state.busy) return;
    state.busy = true;
    state.error = '';
    state.message = message;
    state.refresh();
    try {
      await action();
    } catch (error) {
      state.error = error.message || '处理失败，请重试。';
    } finally {
      state.busy = false;
      state.refresh();
    }
  };
  const payload = () => ({
    client_id:Number(state.clientId), archive_id:state.archiveId, bank_account_code:state.bankCode,
  });
  const clearResult = () => {
    state.result = null;
    state.selected.clear();
  };
  const readPreview = async () => {
    clearResult();
    const result = await ctx.api('/api/sales/payroll-preview', {method:'POST', body:payload()});
    state.result = result;
    state.selected = new Set(result.rows.filter(row => row.status === 'ready').map(row => row.id));
    const pending = result.rows.filter(row => row.status === 'review').length;
    const posted = result.rows.filter(row => row.status === 'posted').length;
    const holder = result.recognized_account ? ` 识别户名 ${result.recognized_account}。` : '';
    state.message = result.rows.length
      ? `待确认入账 ${state.selected.size} 笔，待核对 ${pending} 笔，已有凭证 ${posted} 笔。${holder}${state.selected.size ? ' 确认后将借记管理费用明细，贷记所选银行存款。' : ''}`
      : '本批原件中未识别到工资、社保或公积金。';
  };
  const parseFiles = async () => {
    clearResult();
    const started = await ctx.api('/api/reconcile/parse/start', {method:'POST', body:{
      client_id:Number(state.clientId), statements:await Promise.all(state.files.map(fileBase64)),
      include_bank_metadata:true,
    }});
    for (;;) {
      const job = await ctx.api(`/api/reconcile/parse/progress?job_id=${encodeURIComponent(started.job_id)}`);
      if (job.status === 'failed') throw new Error(job.error || '原件识别失败，请检查文件。');
      if (job.status === 'completed' && job.result?.archive_id) {
        state.archiveId = job.result.archive_id;
        state.files = [];
        await readPreview();
        return;
      }
      if (job.status !== 'running') throw new Error('未返回识别结果，请重试。');
      state.message = `正在识别银行回单或流水，已用 ${Math.floor(job.elapsed_seconds)} 秒…`;
      state.updateStatus();
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  };

  root.addEventListener('change', event => {
    const input = event.target;
    if (input === client) {
      state.clientId = client.value;
      state.banks = [];
      state.bankCode = '';
      state.archiveId = '';
      state.files = [];
      state.error = '';
      state.message = '';
      clearResult();
      if (!state.clientId) {
        refresh();
        return;
      }
      run('正在读取银行科目…', async () => {
        const result = await ctx.api(`/api/sales/payroll-accounts?client_id=${encodeURIComponent(state.clientId)}`);
        state.banks = result.accounts;
        state.bankCode = state.banks.length === 1 ? state.banks[0].code : '';
        state.message = state.banks.length ? '' : '该账套没有可入账的银行存款末级科目，请核对科目设置。';
      });
    } else if (input === bank) {
      state.bankCode = bank.value;
      if (state.archiveId) run('正在核对付款科目及凭证…', readPreview);
    } else if (input === files) {
      clearResult();
      state.archiveId = '';
      state.error = '';
      state.files = Array.from(files.files);
      if (state.files.length > 30 || state.files.reduce((sum, file) => sum + file.size, 0) > maxUpload) {
        state.error = '每批最多 30 份文件，合计最多 10 MB，请分批上传。';
      } else if (state.files.some(file => !file.size || !allowedFile.test(file.name))) {
        state.error = '请选择有效的 PDF、PNG、JPG 或 ZIP 文件。';
      }
      if (state.error) state.files = [];
      state.message = state.files.length ? `已选择 ${state.files.length} 份文件。` : '';
      refresh();
    } else if (input.matches('[data-payroll-row]')) {
      if (input.checked) state.selected.add(input.dataset.payrollRow);
      else state.selected.delete(input.dataset.payrollRow);
      updatePost();
    }
  });
  root.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || button.disabled || state.busy) return;
    if (button === parse) run('正在识别银行回单或流水…', parseFiles);
    else if (button === preview) run('正在核对付款科目及凭证…', readPreview);
    else if (button === post) run('正在生成凭证…', async () => {
      const result = await ctx.api('/api/sales/payroll-post', {method:'POST', body:{
        ...payload(), confirmed:true, row_ids:[...state.selected],
      }});
      const updated = new Map(result.rows.map(row => [row.id, row]));
      state.result.rows = state.result.rows.map(row => updated.get(row.id) || row);
      state.selected.clear();
      const created = result.rows.filter(row => row.status === 'posted' && !row.already_posted).length;
      const existing = result.rows.filter(row => row.already_posted).length;
      const pending = result.rows.filter(row => row.status === 'review').length;
      state.message = `已生成凭证 ${created} 笔，已有凭证 ${existing} 笔，待核对 ${pending} 笔。`;
    });
    else if (button.matches('[data-payroll-voucher]')) {
      const row = state.result?.rows.find(item => item.id === button.dataset.payrollVoucher);
      if (row) ctx.openReconciliationVoucher({clientId:Number(state.clientId), period:row.period, voucherId:row.voucher_id})
        .catch(error => ctx.toast(error.message, 'error'));
    }
  });
  state.refresh = refresh;
  state.updateStatus = () => { if (root.isConnected) updateStatus(); };
  refresh();
}
