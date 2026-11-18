import {escapeHtml as h,accountPickerField,bindAccountPicker} from './account-picker.js';
import {parseVoucherMoney} from './voucher-editor.js';
import {payrollReport,legacySocialNote} from './payroll-report.js';
import {createPayrollXlsx,payrollFilename} from './payroll-xlsx.js';
import {exportPayrollMaster} from './payroll-master.js';
const keys=['gross_fen','social_fen','housing_fund_fen','other_deduction_fen','withheld_tax_fen','net_fen'];
const captions=['工资总额','个人社保','个人公积金','其他扣款','代扣个税','实发工资'];
const socialFields=['pension','medical','unemployment'];
const money=n=>(Number(n||0)/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const plain=n=>(Number(n||0)/100).toFixed(2);
export function adjacentPayrollPeriod(period,delta){const [y,m]=period.split('-').map(Number),d=new Date(y,m-1+delta,1);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;}
const isClosed=book=>(book.periods||[]).some(p=>p.period>=book.period&&p.closed);
const isPosted=book=>(book.payroll||[]).some(p=>p.voucher_id||p.payment_voucher_id);
const act=(label,action,attrs='')=>`<button type="button" class="btn" data-book-action="${action}" ${attrs}>${label}</button>`;
export function renderPayrollPage(book){
  const rows=book.payroll||[],locked=isClosed(book)||isPosted(book),prev=adjacentPayrollPeriod(book.period,-1),total=key=>rows.reduce((n,p)=>n+Number(p[key]||0),0);
  const personnel=rows.map(p=>`<tr><td><strong>${h(p.employee_name)}</strong><small>${h(p.employee_code||'未填员工编号')}</small></td><td>${h(p.department||'—')}<small>${h(p.position||'')}</small></td>${keys.map(key=>`<td class="number ${key==='net_fen'?'payroll-net-amount':''}">${money(p[key])}</td>`).join('')}<td><span class="payroll-status ${p.voucher_id?'posted':''}">${p.voucher_id?'已计提':'未计提'}</span><small>${p.payment_voucher_id?'已登记发放':'未登记发放'}</small></td><td class="payroll-row-actions">${act('查看工资条','view-payslip',`data-id="${p.id}"`)}${act('修改','edit-payroll',`data-id="${p.id}" ${locked?'disabled':''}`)}${act('删除','delete-payroll',`data-id="${p.id}" ${locked?'disabled':''}`)}</td></tr>`).join('');
  return `<section class="payroll-page" aria-label="工资管理">
    <header class="payroll-heading"><div><h1>工资管理</h1><p>保存本公司人员工资，按所属月份生成工资表和工资条</p></div><div class="payroll-month"><button type="button" data-py-period="${prev}" aria-label="上月工资" ${prev<book.client.start_period?'disabled':''}>‹</button><label><input type="month" data-py-month value="${h(book.period)}" min="${h(book.client.start_period)}" aria-label="工资所属月份"></label><button type="button" data-py-period="${adjacentPayrollPeriod(book.period,1)}" aria-label="下月工资">›</button></div></header>
    <div class="payroll-metrics"><article><span>本月人员</span><strong>${rows.length}<small> 人</small></strong></article><article><span>工资总额</span><strong>¥${money(total('gross_fen'))}</strong></article><article><span>扣款合计</span><strong>¥${money(total('gross_fen')-total('net_fen'))}</strong></article><article class="payroll-net"><span>实发工资</span><strong>¥${money(total('net_fen'))}</strong></article></div>
    <div class="payroll-actions print-hide">${act('＋ 添加人员 / 工资','new-payroll',locked?'disabled':'')}${act('带入上月人员','carry-payroll',locked||prev<book.client.start_period?'disabled':'')}${act('生成全部工资条','payroll-slips',rows.length?'':'disabled')}${act('导出工资表（Excel）','export-payroll',rows.length?'':'disabled')}<span class="payroll-action-spacer"></span>${act('生成计提凭证','post-payroll',isClosed(book)||!rows.length?'disabled':'')}${act('登记工资发放','pay-payroll',isClosed(book)||!rows.length?'disabled':'')}</div>
    <p class="payroll-carry-hint">${locked?'本月已计提、发放或结账。可查看、生成工资条；更正前请先处理关联凭证。':'下个月点击“带入上月人员”，即可沿用本月人员、工资和扣款（个税除外）；已有人员会自动跳过。'} 导出工资表会按公司名称写入工资大表，月份之间沿用原有空行间隔；重复导出更新该月份。</p>
    <div class="table-wrap book-table"><table class="payroll-table"><thead><tr><th>员工信息</th><th>部门 / 岗位</th>${captions.map(t=>`<th>${t}（元）</th>`).join('')}<th>计提 / 发放</th><th>操作</th></tr></thead><tbody>${personnel||`<tr><td colspan="${keys.length+4}" class="book-empty">本月还没有人员工资。点击“添加人员 / 工资”，或带入上月人员。</td></tr>`}</tbody></table></div>
    <p class="payroll-footnote">应发工资 = 工资总额 − 个人社保 − 个人公积金 − 其他扣款；实发工资 = 应发工资 − 代扣个税。个税按确认金额录入，工资按公司和月份保存。</p></section>`;
}
export function bindPayrollPage(container,book,ctx){
  const root=container.querySelector('.payroll-page');if(!root)return;
  const go=async period=>{if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(period)||period<book.client.start_period)throw new Error('请选择建账月份之后的有效期间');ctx.setBook(book.client.id,period);await ctx.reload();};
  root.addEventListener('click',e=>{const b=e.target.closest('[data-py-period]');if(b&&!b.disabled)go(b.dataset.pyPeriod).catch(e=>ctx.toast(e.message,'error'));});
  root.querySelector('[data-py-month]').addEventListener('change',e=>go(e.target.value).catch(e=>ctx.toast(e.message,'error')));
}
export function payrollFormPayload(form,accounts){
  const p={};for(const name of ['period','employee_name','employee_code','department','position','note'])p[name]=form.elements[name].value.trim();
  for(const name of ['gross','withheld_tax','social','other_deduction',...socialFields,'housing_fund'])p[`${name}_fen`]=parseVoucherMoney(form.elements[name].value);
  const social=socialFields.reduce((n,key)=>n+p[`${key}_fen`],0);
  if(social>0)p.social_fen=social;
  if(!p.employee_name)throw new Error('请填写员工姓名');
  if(p.gross_fen<=0)throw new Error('工资总额须大于零');
  if(p.withheld_tax_fen+p.social_fen+p.housing_fund_fen+p.other_deduction_fen>p.gross_fen)throw new Error('扣款合计不能超过工资总额');
  p.expense_account=form.querySelector('[data-account-value]').value;
  if(!accounts.some(a=>a.code===p.expense_account&&['expense','cost'].includes(a.category)))throw new Error('请选择工资成本费用科目');
  return p;
}
export function openPayrollForm(ctx,book,record=null){
  if(isClosed(book)||isPosted(book))throw new Error('请先撤回本期发放和计提凭证，再修改人员工资');
  const accounts=book.accounts.filter(a=>['expense','cost'].includes(a.category));
  const eligible=accounts.filter(a=>!book.accounts.some(other=>other.code!==a.code&&other.code.startsWith(a.code)));
  const a=eligible.find(a=>a.name==='管理费用')||eligible.find(a=>a.report_key==='administration')||eligible[0];
  const accountCode=record?.expense_account||a?.code||'';
  const field=(name,label,value='',attrs='')=>`<label class="field"><span>${label}</span><input name="${name}" value="${h(value)}" ${attrs}></label>`;
  const pending=ctx.modal(record?'修改人员及工资':'添加人员及工资',`<div class="form-grid">
    ${field('period','所属期间',record?.period||book.period,`type="month" required min="${h(book.client.start_period)}" ${record?'readonly':''}`)}${field('employee_name','员工姓名',record?.employee_name||'','required maxlength="80"')}${field('employee_code','员工编号（可选）',record?.employee_code||'','maxlength="40"')}${field('department','部门（可选）',record?.department||'','maxlength="80"')}${field('position','岗位（可选）',record?.position||'','maxlength="80"')}${field('gross','工资总额（元）',record?plain(record.gross_fen):'','required inputmode="decimal"')}
    <fieldset class="payroll-deductions wide"><legend>扣社保自付部分（元）</legend><div class="payroll-social-fields">${socialFields.map((name,index)=>field(name,['养老','医保','失业'][index],plain(record?.[`${name}_fen`]),'inputmode="decimal"')).join('')}${field('social','个人社保合计',plain(record?.social_fen),'inputmode="decimal"')}</div><p>填写养老、医保、失业后自动汇总。历史记录只有社保合计时，可继续使用合计，明细待补填。</p></fieldset>
    ${field('housing_fund','扣公积金自付部分（元）',plain(record?.housing_fund_fen),'inputmode="decimal"')}${field('withheld_tax','扣个税（元）',plain(record?.withheld_tax_fen),'inputmode="decimal"')}${field('other_deduction','其他扣款（元）',plain(record?.other_deduction_fen),'inputmode="decimal"')}
    <label class="field"><span>工资费用科目</span>${accountPickerField(accounts,accountCode,'aria-label="搜索工资费用科目"')}</label><label class="field wide"><span>备注（可选）</span><textarea name="note" maxlength="500" rows="2">${h(record?.note||'')}</textarea></label></div><div class="payroll-form-net">预计实发 <strong data-py-net>¥0.00</strong></div><p class="muted">应发工资 = 工资总额 − 个人社保 − 个人公积金 − 其他扣款；实发工资 = 应发工资 − 个税。${record?'仅修改本月记录，其他月份保留。':'保存后即可生成与所选月份对应的工资表。'}</p>`,async form=>{
    const payload=payrollFormPayload(form,accounts),clientId=book.client.id;
    await ctx.api(`/api/books/${clientId}/payroll${record?`/${record.id}`:''}`,{method:record?'PATCH':'POST',body:{...payload,...(record?{version:record.version}:{})}});
    ctx.setBook(clientId,payload.period);ctx.toast(record?'人员工资已修改':'人员工资已保存');
    try{await ctx.reload();}catch(e){ctx.toast(`工资已保存，请刷新页面重新读取：${e.message}`,'error');}
  });
  queueMicrotask(()=>{
    const form=document.querySelector('#app-modal-form');if(!form)return;
    bindAccountPicker(form,accounts);
    let detailed=socialFields.some(key=>Number(record?.[`${key}_fen`]||0)>0);
    const update=()=>{try{
      const social=socialFields.reduce((n,key)=>n+parseVoucherMoney(form.elements[key].value),0);
      if(social>0||detailed)form.elements.social.value=plain(social);
      detailed=social>0;form.elements.social.readOnly=detailed;
      const net=parseVoucherMoney(form.elements.gross.value)-['withheld_tax','social','housing_fund','other_deduction'].reduce((n,k)=>n+parseVoucherMoney(form.elements[k].value),0);
      form.querySelector('[data-py-net]').textContent=`¥${money(net)}`;form.querySelector('[data-py-net]').classList.toggle('negative',net<0);
    }catch{form.querySelector('[data-py-net]').textContent='请检查金额';}};
    form.addEventListener('input',update);update();
  });return pending;
}
export async function carryPayroll(ctx,book){
  const source=adjacentPayrollPeriod(book.period,-1),prefix=`/api/books/${book.client.id}`;
  const {rows}=await ctx.api(`${prefix}/payroll?period=${source}`);
  if(!rows.length)return ctx.modal('带入上月人员',`<p>${h(source)} 没有可带入的人员。请先在本月添加人员及工资，下个月即可带入。</p>`);
  return ctx.modal('带入上月人员',`<p><strong>${h(book.client.name)}</strong></p><p>从 ${h(source)} 带入 ${h(book.period)}，上月共 <strong>${rows.length}</strong> 人。</p><div class="payroll-carry-list">${rows.map(p=>`<div><span>${h(p.employee_name)} ${p.employee_code?`（${h(p.employee_code)}）`:''}</span><span>工资总额 ¥${money(p.gross_fen)}</span></div>`).join('')}</div><p class="payroll-carry-option">基本工资（工资总额）、社保、公积金和其他扣款全部沿用上月，个税清零后按本月实际金额填写。</p><p class="muted">按姓名或员工编号跳过本月已有人员，不覆盖已录入的工资，不带入计提和发放状态。</p>`,async()=>{
    const result=await ctx.api(`${prefix}/payroll/carry-forward`,{method:'POST',body:{period:book.period,source_period:source}});
    ctx.toast(`已带入 ${result.copied} 人${result.skipped.length?`，跳过本月已有 ${result.skipped.length} 人`:''}`);
    await ctx.reload();
  },{submitLabel:'带入人员'});
}
export function payrollSlipMarkup(rows,book){
  const report=payrollReport(book,rows),{columns}=report,totalWidth=columns.reduce((sum,c)=>sum+c.width,0);
  const header=columns.map((c,index)=>index===3?'<th colspan="4" scope="colgroup">扣社保自付部分</th>':index>3&&index<7?'':`<th rowspan="2" scope="col">${h(c.label).replace('\n','<br>')}</th>`).join('');
  const cells=(values,total=false)=>columns.map((c,index)=>{
    if(total&&index===0)return '<td colspan="2">合计</td>';
    if(total&&index===1)return '';
    const value=values[index],content=value===null?'':c.money?(c.key==='withheld_tax_fen'&&value===0?(total?'－':''):money(value)):h(value);
    return `<td class="${c.money?'payroll-report-money':''} ${c.key==='housing_fund_fen'||c.key==='withheld_tax_fen'?'payroll-report-emphasis':''}">${content}</td>`;
  }).join('');
  const notes=report.rows.filter(r=>r.person.note).map(r=>`<p class="payroll-slip-note">${h(r.person.employee_name)}：${h(r.person.note)}</p>`).join('');
  return `<div class="payroll-slips"><article class="payroll-slip payroll-report ${report.hasOther?'payroll-report-extra':''}"><header><h2>${h(report.title)}</h2><p class="payroll-report-period">${h(report.periodLabel)}</p></header><p class="payroll-report-unit">单位：元</p><div class="payroll-report-scroll"><table><colgroup>${columns.map(c=>`<col style="width:${(c.width/totalWidth*100).toFixed(3)}%">`).join('')}</colgroup><thead><tr>${header}</tr><tr>${columns.slice(3,7).map(c=>`<th scope="col">${h(c.label)}</th>`).join('')}</tr></thead><tbody>${report.rows.map(r=>`<tr>${cells(r.values)}</tr>`).join('')}<tr class="payroll-report-total">${cells(report.totals,true)}</tr></tbody></table></div>${report.hasLegacySocial?`<p class="payroll-slip-note">${h(legacySocialNote)}</p>`:''}${notes}</article></div>`;
}
export function printPayrollSlips(rows,book){
  const title=document.title;document.title=`${book.client.name}_${book.period}_工资表`;
  const root=document.createElement('div');root.className='payroll-print-root';root.innerHTML=payrollSlipMarkup(rows,book);document.body.append(root);document.body.classList.add('printing-payroll');
  const cleanup=()=>{root.remove();document.body.classList.remove('printing-payroll');document.title=title;};
  window.addEventListener('afterprint',cleanup,{once:true});
  try{window.print();}catch(e){cleanup();throw e;}
}
export function showPayrollSlips(ctx,book,id=null){
  const rows=payrollReport(book).rows.map(r=>r.person).filter(p=>!id||p.id===id);
  if(!rows.length)throw new Error('没有可生成工资条的人员记录，请先保存工资');
  const pending=ctx.modal(id?'查看工资条':`本月工资表（${rows.length} 人）`,`<div class="payroll-slip-tools"><span>${h(book.period)} · ${rows.length} 人</span><button type="button" class="btn btn-primary" data-py-print>打印 / 另存为 PDF</button>${id?'':'<button type="button" class="btn" data-py-master>导出到工资大表</button>'}<button type="button" class="btn" data-py-export>${id?'导出当前工资条':'导出本月单表'}（Excel）</button></div>${payrollSlipMarkup(rows,book)}`,null,{wide:true});
  queueMicrotask(()=>{const form=document.querySelector('#app-modal-form');form?.querySelector('[data-py-print]')?.addEventListener('click',()=>printPayrollSlips(rows,book));form?.querySelector('[data-py-export]')?.addEventListener('click',()=>exportPayroll(book,rows));form?.querySelector('[data-py-master]')?.addEventListener('click',()=>exportPayrollMaster(ctx,book).catch(e=>ctx.toast(e.message,'error')));});return pending;
}
export function payrollExportRows(book,records){
  const report=payrollReport(book,records),format=values=>values.map((value,index)=>value===null?'':report.columns[index].money?plain(value):value);
  return [[report.title],[report.periodLabel],['单位：元'],report.columns.map(c=>c.label.replace('\n','')), ...report.rows.map(r=>format(r.values)),format(report.totals)];
}
export function exportPayroll(book,records){
  const blob=new Blob([createPayrollXlsx(book,records)],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=payrollFilename(book);a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);
}
