import {draftAccounts,draftKey,generateRows,summarize,balanceLastRow,rowAmount,yuan,MAX_ROWS} from './ledger-draft-model.js';
import {createLedgerDraftXlsx,draftFilename} from './ledger-draft-xlsx.js';
import {ledgerPresentation,isExpense} from './ledger-draft-expenses.js';

const h = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const states = new Map();
const money = fen => yuan(fen).replace(/\B(?=(\d{3})+(?!\d))/g,',');
function stateFor(book,ctx) {
  const key = draftKey(book,ctx);
  if (!states.has(key)) {
    const state = {companyName:book.client.name||'',total:'',keywords:'',accountName:'库存商品',rows:[],reviewed:false,storageError:'',undo:null};
    try {
      const saved = JSON.parse(globalThis.localStorage?.getItem(key) || 'null');
      if (saved?.version === 1 && Array.isArray(saved.rows) && saved.rows.length <= MAX_ROWS && saved.rows.every(r => r && typeof r === 'object')) {
        for (const field of ['total','keywords','accountName','companyName']) if (typeof saved[field] === 'string') state[field] = saved[field];
        state.rows = saved.rows.map(r => Object.fromEntries(['category','name','spec','unit','quantity','price'].map(field=>[field,String(r[field] ?? '')])));
        state.reviewed = saved.reviewed === true;
      }
    } catch {state.storageError='浏览器草稿读取失败，当前内容将在本次页面内保留。';}
    states.set(key,state);
  }
  return states.get(key);
}
function saveState(book,ctx,state) {
  try {
    if (!globalThis.localStorage) throw new Error('Storage unavailable');
    const {total,keywords,accountName,companyName,rows,reviewed} = state;
    localStorage.setItem(draftKey(book,ctx),JSON.stringify({version:1,total,keywords,accountName,companyName,rows,reviewed}));
    state.storageError='';
  } catch {state.storageError='浏览器未能保存草稿，请先导出；关闭或刷新页面可能丢失本次修改。';}
}
const accountFor = (book,state) => draftAccounts(book).find(a=>a.name===state.accountName);
function inputCell(row,key,index,label) {
  const numeric=['quantity','price'].includes(key);
  return `<td><input data-ld-row="${index}" data-ld-field="${key}" aria-label="第 ${index+1} 行${label}" value="${h(row[key])}" ${numeric?'inputmode="decimal" maxlength="18"':'maxlength="160"'}></td>`;
}
function rowsHtml(state,account) {
  const copy=ledgerPresentation(state.accountName);
  return state.rows.map((r,i)=>`<tr><td>${i+1}</td><td class="ld-code">${h(account?.code||'—')}${String(i+1).padStart(2,'0')}</td>${['category','name','spec','unit','quantity','price'].map((key,j)=>inputCell(r,key,i,copy.fields[j])).join('')}<td class="ld-number" data-ld-amount="${i}">—</td><td><button type="button" class="ld-delete" data-ld-delete="${i}" aria-label="删除第 ${i+1} 行">删除</button></td></tr>`).join('') || `<tr><td colspan="10" class="ld-empty">${copy.empty}</td></tr>`;
}
function tables(book,state,account) {
  const copy=ledgerPresentation(state.accountName);
  return `<section class="ld-sheet"><div class="ld-sheet-head"><h3>${h(book.client.name)} - ${h(copy.detailTitle)}</h3><span data-ld-review-status></span></div><p>会计科目：${h(account?.code||'未配置')} ${h(state.accountName)} ｜ 金额单位：人民币元${copy.expense?' ｜ 1 项为费用项目，非员工人数':''}</p><div class="ld-table-scroll"><table class="ld-details"><thead><tr>${[...copy.columns,'操作'].map(v=>`<th>${v}</th>`).join('')}</tr></thead><tbody>${rowsHtml(state,account)}</tbody><tfoot><tr><th colspan="6">${h(copy.subtotal)} <small data-ld-unit></small></th><td class="ld-number" data-ld-quantity></td><td>—</td><td class="ld-number" data-ld-total></td><td></td></tr></tfoot></table></div><div class="ld-row-actions"><button type="button" class="btn" data-ld-action="add" ${state.rows.length>=MAX_ROWS?'disabled':''}>＋ 添加明细</button><button type="button" class="btn" data-ld-action="balance" ${!state.rows.length?'disabled':''}>配平末行</button><span>${copy.balanceHint}</span></div></section>
  <section class="ld-sheet"><div class="ld-sheet-head"><h3>${h(book.client.name)} - ${h(copy.generalTitle)}</h3><span>随明细自动联动</span></div><p>会计科目：${h(account?.code||'未配置')} ${h(state.accountName)} ｜ 金额单位：人民币元${copy.expense?' ｜ 费用发生额拟稿，非已记账期末余额':''}</p><div class="ld-table-scroll"><table><thead><tr>${['科目代码','科目名称','摘要',copy.quantityLabel,'方向',copy.amountLabel].map(v=>`<th>${v}</th>`).join('')}</tr></thead><tbody><tr class="ld-general"><td>${h(account?.code||'—')}</td><td>${h(state.accountName)}</td><td data-ld-summary></td><td class="ld-number" data-ld-general-quantity></td><td>${account?.direction==='credit'?'贷':'借'}</td><td class="ld-number" data-ld-general-total></td></tr></tbody></table></div></section>`;
}

export function renderLedgerDraft(book,ctx) {
  const state=stateFor(book,ctx);if(ctx.sales)book.client.name=state.companyName;const accounts=draftAccounts(book), account=accountFor(book,state), copy=ledgerPresentation(state.accountName);
  return `<div class="ld-page" data-ledger-draft><header class="ld-heading"><div><h2>${ctx.sales?'明细账生成':'明细及总账'}</h2><p data-ld-copy="header">${copy.header}</p></div><span class="ld-badge">工作拟稿</span></header><div class="ld-notice" data-ld-copy="notice">${copy.notice}</div>
  <form class="ld-form" data-ld-form><label>${ctx.sales?'企业名称':'当前公司'}<input name="companyName" value="${h(ctx.sales?state.companyName:book.client.name)}" ${ctx.sales?'required maxlength="180" placeholder="填写企业名称，无需先建账"':'readonly'} aria-label="${ctx.sales?'企业名称':'当前公司'}"></label><label>总金额（元）<input name="total" inputmode="decimal" maxlength="18" required placeholder="例如 12345.67" value="${h(state.total)}"></label><label>会计科目<select name="accountName" aria-label="会计科目">${accounts.map(a=>`<option value="${h(a.name)}" ${a.name===state.accountName?'selected':''} ${a.code?'':'disabled'}>${h(a.code ? `${a.code} ${a.name}` : `${a.name}（账套未配置）`)}</option>`).join('')}</select></label><label class="ld-keywords"><span data-ld-copy="keywordLabel">${copy.keywordLabel}</span><textarea name="keywords" rows="2" maxlength="1700" required placeholder="${h(copy.placeholder)}">${h(state.keywords)}</textarea></label><div class="ld-form-actions"><button type="submit" class="btn btn-primary">生成拟稿</button><button type="button" class="btn" data-ld-action="undo" ${state.undo?'':'disabled'}>撤销上次生成</button><small data-ld-copy="hint">${copy.hint}</small></div></form>
  <p class="ld-notice" data-ld-generation-notes hidden></p><div class="ld-validation" data-ld-validation role="status" aria-live="polite"></div><div data-ld-tables>${tables(book,state,account)}</div>
  <footer class="ld-footer"><label><input type="checkbox" data-ld-reviewed ${state.reviewed?'checked':''}> <span data-ld-copy="review">${copy.review}</span></label><button type="button" class="btn btn-primary" data-ld-action="export">导出 Excel（双表）</button><p data-ld-saved>草稿按当前操作员、公司和账期保存在本浏览器；账期 ${h(book.period)}。下载位置由浏览器设置决定。</p></footer></div>`;
}

export function bindLedgerDraft(container,book,ctx) {
  const root=container.querySelector('[data-ledger-draft]');
  if (!root || root.dataset.bound) return;
  root.dataset.bound='1';
  const state=stateFor(book,ctx);
  const sync = () => {
    const account=accountFor(book,state), copy=ledgerPresentation(state.accountName), totals=summarize(state,account);
    root.querySelectorAll('[data-ld-copy]').forEach(node=>{node.textContent=copy[node.dataset.ldCopy];});
    root.querySelector('[name="keywords"]').placeholder=copy.placeholder;
    root.querySelectorAll('[data-ld-amount]').forEach(node => {try {node.textContent=money(rowAmount(state.rows[Number(node.dataset.ldAmount)]));} catch {node.textContent='待修正';}});
    const set = (selector,value) => {const node=root.querySelector(selector);if(node)node.textContent=value;};
    set('[data-ld-quantity]',String(totals.count));set('[data-ld-total]',money(totals.total));
    set('[data-ld-unit]',`（${totals.unit}${copy.expense?'，数量或期数合计，非人数':''}）`);
    set('[data-ld-general-quantity]',`${totals.count}（${totals.unit}）`);set('[data-ld-general-total]',money(totals.total));
    set('[data-ld-summary]',`${copy.summary(state.keywords)}，${state.rows.length}项合计`);
    const unknown=[...new Set(state.rows.filter(row=>row.spec.includes('未识别')).map(row=>row.category))];
    set('[data-ld-generation-notes]',unknown.length?`未识别${copy.expense?'费用':''}关键词：${unknown.join('、')}。${copy.unknownHint}`:'');
    root.querySelector('[data-ld-generation-notes]').hidden=!unknown.length;
    const issues=[...totals.errors];if(!account?.code)issues.unshift('当前账套缺少所选科目，请先维护科目表');
    const valid=totals.balanced && account?.code;
    const validation=root.querySelector('[data-ld-validation]');
    validation.classList.toggle('ld-valid',Boolean(valid));
    validation.textContent=issues.length ? issues.join('；') : valid ? `已配平：${copy.expense?'费用清单与汇总金额':'明细与总账'}均为 ${money(totals.total)} 元，共 ${state.rows.length} 项。` : `待配平：目标 ${money(totals.target)} 元，明细 ${money(totals.total)} 元，差额 ${money(totals.difference)} 元。`;
    root.querySelector('[data-ld-action="export"]').disabled=!valid;
    root.querySelector('[data-ld-reviewed]').disabled=!valid;
    root.querySelector('[data-ld-reviewed]').checked=state.reviewed;
    set('[data-ld-review-status]',state.reviewed?'已人工核对拟稿':'待核对拟稿');
    set('[data-ld-saved]',state.storageError || `草稿按当前操作员、公司和账期保存在本浏览器；账期 ${book.period}。下载位置由浏览器设置决定。`);
    root.querySelector('[data-ld-action="undo"]').disabled=!state.undo;
  };
  const persist = () => {saveState(book,ctx,state);sync();};
  const redraw = () => {root.querySelector('[data-ld-tables]').innerHTML=tables(book,state,accountFor(book,state));persist();};
  root.addEventListener('input',event => {
    const el=event.target;
    if (el.dataset.ldField) {state.rows[Number(el.dataset.ldRow)][el.dataset.ldField]=el.value;state.reviewed=false;persist();}
    else if (ctx.sales && el.name==='companyName') {state.companyName=el.value;book.client.name=el.value;state.reviewed=false;redraw();}
    else if (['total','keywords'].includes(el.name)) {state[el.name]=el.value;state.reviewed=false;persist();}
  });
  root.addEventListener('change',event => {
    if(event.target.name==='accountName') {state.accountName=event.target.value;state.reviewed=false;redraw();}
    if(event.target.matches('[data-ld-reviewed]')) {state.reviewed=event.target.checked;persist();}
  });
  root.querySelector('[data-ld-form]').addEventListener('submit',event => {
    event.preventDefault();
    try {
      if(ctx.sales&&!state.companyName.trim())throw new Error('请填写企业名称');
      const next=generateRows(state.total,state.keywords,accountFor(book,state));
      state.undo=state.rows.map(row=>({...row}));state.rows=next;state.reviewed=false;redraw();
      ctx.toast('已生成待核对拟稿，可逐项修改');
    } catch(error) {ctx.toast(error.message,'error');}
  });
  root.addEventListener('click',event => {
    const button=event.target.closest('[data-ld-action],[data-ld-delete]');
    if(!button || button.disabled)return;
    try {
      if(button.hasAttribute('data-ld-delete')) {state.rows.splice(Number(button.dataset.ldDelete),1);state.reviewed=false;redraw();return;}
      switch(button.dataset.ldAction) {
        case 'add':if(state.rows.length>=MAX_ROWS)return;state.rows.push({category:'',name:'',spec:isExpense(state.accountName)?'用途、归属及所属期间待核对':'待核对',unit:isExpense(state.accountName)?'项':'件',quantity:'1',price:'0.00'});state.reviewed=false;redraw();root.querySelector(`[data-ld-row="${state.rows.length-1}"]`)?.focus();break;
        case 'balance':state.rows=balanceLastRow(state);state.reviewed=false;redraw();break;
        case 'undo':if(state.undo){state.rows=state.undo;state.undo=null;state.reviewed=false;redraw();}break;
        case 'export':{
          if(ctx.sales&&!state.companyName.trim())throw new Error('请填写企业名称');
          const account=accountFor(book,state), bytes=createLedgerDraftXlsx({companyName:book.client.name,period:book.period,account,draft:state});
          const url=URL.createObjectURL(new Blob([bytes],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));
          const anchor=document.createElement('a');anchor.href=url;anchor.download=draftFilename(book.client.name,account,state.total);document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
          ctx.toast('已导出明细及总账 Excel');break;
        }
      }
    } catch(error) {ctx.toast(error.message,'error');}
  });
  sync();
}

export const ledgerDraftTest={stateFor,saveState,states};
