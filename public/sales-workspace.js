import {renderLedgerDraft,bindLedgerDraft} from './ledger-draft.js';
import {renderTaxLiquidation,bindTaxLiquidation} from './modules/tax-liquidation/entry.js';
import {renderUploadReport,bindUploadReport} from './modules/tax-liquidation/upload-report.js';
import {createPayrollPosting,renderPayrollPosting,bindPayrollPosting} from './modules/tax-liquidation/payroll-posting.js';
const h=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const states=new Map();
const stateFor=ctx=>{const key=`${ctx.demo?'demo':'local'}:${ctx.user?.id}`;if(!states.has(key))states.set(key,{view:'',clientId:'',period:ctx.period});return states.get(key);};
const icons={ledger:'<path d="M5 3h14v18H5zM9 7h6M9 11h6M9 15h6"/>',tax:'<path d="M6 3h8l4 4v14H6zM14 3v5h5M9 12h6M9 16h6"/>'};
export function salesBook(ctx,state=stateFor(ctx)) {
  const client=ctx.clients?.find(c=>String(c.id)===state.clientId);
  if(client)return {client,period:state.period};
  return {client:{id:`sales-${ctx.demo?'demo':'local'}-${ctx.user?.id||'local'}`,code:'sales',name:'',standard:'small2013'},period:state.period,
    accounts:['其他应收款','其他应付款','库存商品','固定资产','管理费用'].map((name,i)=>({name,code:['1221','2241','1405','1601','5602'][i],direction:i===1?'credit':'debit'}))};
}
export function renderSalesWorkspace(ctx) {
  const s=stateFor(ctx);
  return `<section class="sales-workspace" data-sales-workspace><header class="sales-heading"><div><p>首页 / 销售辅助台</p><h1>销售辅助台</h1><span>明细账拟稿与企业清算报告，直接在这里办理</span></div><button class="btn" data-sales-back ${s.view?'':'hidden'}>返回销售辅助台</button></header><div data-sales-content></div></section>`;
}
export async function bindSalesWorkspace(container,ctx) {
  const root=container.querySelector('[data-sales-workspace]');if(!root||root.dataset.bound)return;root.dataset.bound='1';
  const s=stateFor(ctx),content=root.querySelector('[data-sales-content]');let sequence=0;
  const companyPicker=()=>`<label>选择已有公司（可选）<select data-sales-client><option value="">新客户 / 直接填写企业名称</option>${(ctx.clients||[]).map(c=>`<option value="${h(c.id)}" ${String(c.id)===s.clientId?'selected':''}>${h(c.name)}</option>`).join('')}</select></label>`;
  const show=async()=>{
    const current=++sequence,back=root.querySelector('[data-sales-back]');
    back.hidden=!s.view;
    back.textContent=s.view==='tax-manual'?'返回上传报告':'返回销售辅助台';
    root.querySelector('.sales-heading h1').textContent=s.view==='ledger'?'明细账生成':s.view==='tax'||s.view==='tax-manual'?'税务清算报告':'销售辅助台';
    if(!s.view){content.innerHTML=`<div class="sales-cards">${[['ledger','明细账生成','填写金额与商品、业务信息，生成可编辑明细及总账，导出 Excel。'],['tax','税务清算报告','选择纳税人类型，上传资产负债表或科目余额表，一键生成清算清偿报告。']].map(([key,title,description])=>`<button class="sales-card" data-sales-open="${key}"><span class="sales-card-icon"><svg viewBox="0 0 24 24" aria-hidden="true">${icons[key]}</svg></span><strong>${title}</strong><span>${description}</span><b>进入模块 →</b></button>`).join('')}</div>`;return;}
    if(s.view==='tax'){
      const book=salesBook(ctx,{clientId:'',period:ctx.period});
      s.payroll ||= createPayrollPosting();
      content.innerHTML=`<nav class="sales-tax-tools" aria-label="清算报告操作">
        <button type="button" class="btn" data-sales-fill>填写实际变现、清偿和完税</button>
        <button type="button" class="btn" data-sales-payroll-jump>管理费用入账</button>
      </nav>`+renderUploadReport(book,ctx)+`<details class="sales-history"><summary>手工填报与历史公司草稿</summary>
        <p>在完整模式第 3 步填写实际变现和债务清偿，第 4 步核实完税情况。填写后可从填报工具导出报告。</p>
        <div class="sales-options">${companyPicker()}<button type="button" class="btn" data-sales-open="tax-manual">打开清算填报工具</button></div>
      </details>`+renderPayrollPosting(ctx,s.payroll);
      bindUploadReport(content,book,ctx);
      bindPayrollPosting(content,ctx,s.payroll);
      return;
    }
    content.innerHTML=`<div class="sales-options">${companyPicker()}${s.view==='ledger'?`<label>账期<input type="month" value="${h(s.period)}" data-sales-period required></label>`:''}</div><div data-sales-tool role="status">正在读取工具…</div>`;
    try{
      let book=salesBook(ctx);
      if(s.clientId)book=await ctx.api(`/api/books/${Number(s.clientId)}?period=${encodeURIComponent(s.period)}`);
      if(!root.isConnected||current!==sequence)return;
      const host=content.querySelector('[data-sales-tool]'),toolCtx={...ctx,sales:!s.clientId};
      if(s.view==='ledger'){host.innerHTML=renderLedgerDraft(book,toolCtx);bindLedgerDraft(host,book,toolCtx);}
      else {host.innerHTML=renderTaxLiquidation(book,toolCtx);await bindTaxLiquidation(host,book,toolCtx);}
    }catch(error){if(root.isConnected&&current===sequence)content.querySelector('[data-sales-tool]').textContent=error.message;}
  };
  root.addEventListener('click',event=>{
    if(event.target.closest('[data-sales-fill]')){
      const history=content.querySelector('.sales-history');
      history.open=true;
      history.querySelector('[data-sales-client]').focus({preventScroll:true});
      history.scrollIntoView({behavior:'smooth',block:'start'});
      return;
    }
    if(event.target.closest('[data-sales-payroll-jump]')){
      const payroll=content.querySelector('[data-sales-payroll]');
      payroll.querySelector('[data-payroll-client]').focus({preventScroll:true});
      payroll.scrollIntoView({behavior:'smooth',block:'start'});
      return;
    }
    const button=event.target.closest('[data-sales-open],[data-sales-back]');
    if(!button)return;
    s.view=button.dataset.salesOpen||(s.view==='tax-manual'?'tax':'');
    show();
  });
  root.addEventListener('change',event=>{if(event.target.matches('[data-sales-client]')){s.clientId=event.target.value;if(s.view!=='tax')show();}else if(event.target.matches('[data-sales-period]')&&/^\d{4}-\d{2}$/.test(event.target.value)){s.period=event.target.value;show();}});
  await show();
}
