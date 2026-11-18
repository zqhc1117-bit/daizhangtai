import {availableModes,MODE_LABELS,resolveMode} from './modes.config.js';
import {loadDraft,saveDraft} from './storage.js';
import {createDraft,shareholder} from './schema.js';
import {amount,sum,yuan} from './money.js';
import {renderCompanyForm} from './shared/company-form.js';
import {extractCompany} from './shared/extract-company.js';
import {renderQuickForm} from './quick/form.js';
import {renderFullBalanceForm,renderFullTreatmentForm} from './full/form.js';
import {renderSettlementForm} from './shared/settlement-form.js';
import {importBalance} from './full/excel.js';
import {sampleDraft} from './demo.js';
import {buildReport} from './report-model.js';
import {renderReport} from './report.js';
import {liveViews,renderSummary,renderWarnings} from './live.js';
import {downloadWord,printReport} from './export.js';
import {h} from './shared/fields.js';
const bound = new WeakSet();
const stepLabels = {1:['企业与股东','确认身份与出资'],2:['关门家底','核对账面底数'],3:['卖货与还债','处理资产与债务'],4:['善后与分钱','完税与分配结果']};
const stepsFor = mode => mode==='quick' ? [1,2,4] : [1,2,3,4];
function renderStep(draft) {
  if (draft.step===1) return renderCompanyForm(draft);
  if (draft.step===2) return draft.mode==='quick' ? renderQuickForm(draft) : renderFullBalanceForm(draft);
  if (draft.step===3 && draft.mode==='full') return renderFullTreatmentForm(draft);
  return renderSettlementForm(draft);
}
function stepNavigation(draft) {
  return stepsFor(draft.mode).map(step=>`<button type="button" class="tliq-step ${step===draft.step?'active':''}" data-tliq-action="step" data-tliq-step="${step}" aria-current="${step===draft.step?'step':'false'}"><i>${step}</i><span><strong>${stepLabels[step][0]}</strong><small>${stepLabels[step][1]}</small></span></button>`).join('');
}
function stepFooter(draft) {
  const steps=stepsFor(draft.mode), index=steps.indexOf(draft.step);
  return `<button type="button" class="tliq-button" data-tliq-action="step" data-tliq-step="${steps[index-1]||draft.step}" ${index===0?'disabled':''}>← 上一步</button><span>${index+1} / ${steps.length}${draft.mode==='quick'?' · 已跳过资产债务处理':''}</span><button type="button" class="tliq-button tliq-primary" data-tliq-action="step" data-tliq-step="${steps[index+1]||draft.step}" ${index===steps.length-1?'disabled':''}>下一步 →</button>`;
}
function fillLive(root,draft,session) {
  const report=buildReport(draft), totals=report.totals, views=liveViews(draft,totals);
  root.querySelectorAll('[data-tliq-live]').forEach(node=>{node.innerHTML=views[node.dataset.tliqLive] || '';});
  root.querySelector('[data-tliq-report]').innerHTML=renderReport(report);
  root.querySelector('[data-tliq-summary]').innerHTML=renderSummary(totals);
  root.querySelector('[data-tliq-warnings]').innerHTML=renderWarnings(totals);
  root.querySelector('[data-tliq-review]').innerHTML=totals.reviewIssues.length?`<details class="tliq-review-list"><summary>有 ${totals.reviewIssues.length} 项待核实 · 报告按草稿展示</summary><ul>${totals.reviewIssues.map(text=>`<li>${h(text)}</li>`).join('')}</ul></details>`:'<div class="tliq-review-clear">✓ 当前录入数据已通过检查，请核实凭证后签署。</div>';
  const status=root.querySelector('[data-tliq-status]');
  status.textContent=session.error || (session.savedAt?`● 已保存到此浏览器 ${session.savedAt}`:'● 本公司独立草稿 · 自动保存在此浏览器');
  status.classList.toggle('tliq-bad',!!session.error);
  root.querySelector('[data-tliq-report-status]').textContent=report.status;
}
export function renderTaxLiquidation(book,ctx={}) {
  const session=loadDraft(book.client,ctx), draft=session.draft, modes=availableModes();
  draft.mode=resolveMode(draft.mode);
  if (!stepsFor(draft.mode).includes(draft.step)) draft.step=2;
  return `<div class="tliq-workspace" data-tliq-company="${h(book.client.id)}"><header class="tliq-toolbar"><div class="tliq-title-group"><span class="tliq-eyebrow">企业清算工作台</span><h2>税务清算及财产清偿报告生成器</h2>${modes.length>1?`<div class="tliq-mode-switch" role="group" aria-label="选择清算模式">${modes.map(mode=>`<button type="button" class="${mode===draft.mode?'active':''}" data-tliq-action="mode" data-tliq-mode="${mode}" aria-pressed="${mode===draft.mode}">${MODE_LABELS[mode]}</button>`).join('')}</div>`:`<span class="tliq-mode-static">${MODE_LABELS[draft.mode]}</span>`}</div><div class="tliq-toolbar-actions"><details class="tliq-sample-menu"><summary class="tliq-button">填充示例数据 <span>⌄</span></summary><div>${modes.map(mode=>`<button type="button" data-tliq-action="sample" data-tliq-mode="${mode}">${mode==='quick'?'极速案例':'完整案例'}</button>`).join('')}</div></details><button type="button" class="tliq-button" data-tliq-action="reset">清空重置</button><button type="button" class="tliq-button tliq-word" data-tliq-action="word">导出 Word (.docx)</button><button type="button" class="tliq-button tliq-primary" data-tliq-action="print">打印 / 导出 PDF</button></div></header>
    <div class="tliq-local-bar"><span data-tliq-status></span><span>仅存于当前浏览器 · 按公司隔离</span></div><div data-tliq-sample-banner></div><div data-tliq-message role="status" class="tliq-message" hidden></div><div data-tliq-reset-confirm hidden></div><div data-tliq-warnings></div><div class="tliq-summary" data-tliq-summary></div>
    <div class="tliq-columns"><section class="tliq-editor" aria-label="清算信息录入向导"><nav class="tliq-steps" data-tliq-steps aria-label="录入步骤">${stepNavigation(draft)}</nav><div data-tliq-form>${renderStep(draft)}</div><footer class="tliq-step-footer" data-tliq-step-footer>${stepFooter(draft)}</footer><div data-tliq-review></div></section><aside class="tliq-preview" aria-label="报告实时预览"><header class="tliq-preview-header"><div><span class="tliq-preview-dot"></span><strong>A4 报告实时预览</strong></div><span data-tliq-report-status></span></header><div class="tliq-preview-scroll" data-tliq-report></div></aside></div></div>`;
}
export function bindTaxLiquidation(container,book,ctx={}) {
  const root=container.querySelector('.tliq-workspace');
  if (!root || bound.has(root)) return;
  bound.add(root);
  const session=loadDraft(book.client,ctx);
  let undo=null, importMessage='', busy=false;
  const showMessage=(text,error=false)=>{
    const node=root.querySelector('[data-tliq-message]');node.textContent=text;node.hidden=!text;node.classList.toggle('tliq-message-error',error);
  };
  const save=()=>saveDraft(book.client,ctx,session);
  const refresh=()=>{
    const draft=session.draft;
    root.querySelector('[data-tliq-steps]').innerHTML=stepNavigation(draft);
    root.querySelector('[data-tliq-form]').innerHTML=renderStep(draft);
    root.querySelector('[data-tliq-step-footer]').innerHTML=stepFooter(draft);
    root.querySelectorAll('[data-tliq-action="mode"]').forEach(node=>{node.classList.toggle('active',node.dataset.tliqMode===draft.mode);node.setAttribute('aria-pressed',String(node.dataset.tliqMode===draft.mode));});
    root.querySelector('[data-tliq-sample-banner]').innerHTML=draft.sample?'<div class="tliq-sample-banner"><span>正在使用示例数据，报告及导出均带示例标记。</span><button type="button" class="tliq-link" data-tliq-action="end-sample">已核实，转为正式草稿</button></div>':'';
    const importNode=root.querySelector('[data-tliq-import-message]');if(importNode)importNode.textContent=importMessage;
    fillLive(root,draft,session);
  };
  root.addEventListener('input',event=>{
    const input=event.target.closest('[data-tliq-path]');if(!input || input.type==='file')return;
    const parts=input.dataset.tliqPath.split('.');
    if(parts.some(part=>['__proto__','constructor','prototype'].includes(part)))return;
    let holder=session.draft;
    for(const part of parts.slice(0,-1)){if(!holder || !Object.hasOwn(holder,part))return;holder=holder[part];}
    const key=parts.at(-1);if(!holder || !Object.hasOwn(holder,key))return;
    const value=input.dataset.tliqBool!==undefined?input.dataset.tliqBool==='true':input.type==='checkbox'?input.checked:input.value;
    holder[key]=value;
    if(input.dataset.tliqPath==='quick.paidCapital')session.draft.quick.capitalLinked=false;
    if(/^shareholders\.\d+\.paid$/.test(input.dataset.tliqPath) && session.draft.quick.capitalLinked) session.draft.quick.paidCapital=yuan(sum(session.draft.shareholders.map(row=>amount(row.paid))));
    save();fillLive(root,session.draft,session);
  });
  root.addEventListener('change',async event=>{
    const input=event.target;
    if(input.matches('[data-tliq-upload]')) {
      const file=input.files?.[0];if(!file)return;
      input.disabled=true;showMessage('正在本地识别资产负债表…');
      try{
        const result=await importBalance(file);if(!root.isConnected)return;
        Object.assign(session.draft.full.balance,result.values);
        importMessage=`已读取「${result.sheetName}」：匹配${result.matches.length}项，单位${result.unit===10000?'万元':result.unit===100000000?'亿元':'元'}已换算为元。${result.matches.map(row=>row.label).join('、')}。${result.warnings.join('；')}`;
        save();refresh();showMessage('Excel 已带入，请核对期末金额和平账结果。');
      }catch(error){if(root.isConnected)showMessage(error.message,true);}finally{if(root.isConnected)input.disabled=false;}
    } else if(input.matches('select,[type="radio"],[type="checkbox"]') || input.dataset.tliqPath==='company.reason') refresh();
  });
  root.addEventListener('click',async event=>{
    const button=event.target.closest('[data-tliq-action]');if(!button || button.disabled)return;
    const action=button.dataset.tliqAction,draft=session.draft;
    try{
      if(action==='step') {
        const step=Number(button.dataset.tliqStep);if(stepsFor(draft.mode).includes(step))draft.step=step;
      } else if(action==='mode') {
        draft.mode=resolveMode(button.dataset.tliqMode);if(!stepsFor(draft.mode).includes(draft.step))draft.step=2;
      } else if(action==='sample') {
        const mode=resolveMode(button.dataset.tliqMode);undo=structuredClone(draft);session.draft=sampleDraft(mode,book.client);
        root.querySelector('.tliq-sample-menu').open=false;
        showMessage('示例数据已填充。可点击“撤销替换”恢复刚才的草稿。');
        root.querySelector('[data-tliq-reset-confirm]').innerHTML='<button type="button" class="tliq-link" data-tliq-action="undo">撤销替换</button>';root.querySelector('[data-tliq-reset-confirm]').hidden=false;
      } else if(action==='reset') {
        const node=root.querySelector('[data-tliq-reset-confirm]');node.hidden=false;node.innerHTML='<div class="tliq-inline-warning">确定清空本公司的清算草稿？<button type="button" class="tliq-button" data-tliq-action="confirm-reset">确认清空</button> <button type="button" class="tliq-button" data-tliq-action="cancel-reset">取消</button></div>';return;
      } else if(action==='cancel-reset') {root.querySelector('[data-tliq-reset-confirm]').hidden=true;return;
      } else if(action==='confirm-reset') {
        undo=structuredClone(draft);session.draft=createDraft(book.client);session.draft.mode=resolveMode(draft.mode);importMessage='';
        root.querySelector('[data-tliq-reset-confirm]').innerHTML='<div class="tliq-note">本公司草稿已清空。<button type="button" class="tliq-link" data-tliq-action="undo">撤销清空</button></div>';
        showMessage('本公司清算草稿已重置。');
      } else if(action==='undo') {
        if(undo){session.draft=undo;undo=null;importMessage='';}root.querySelector('[data-tliq-reset-confirm]').hidden=true;showMessage('已恢复上一次草稿。');
      } else if(action==='add-shareholder') {
        if(draft.shareholders.length<80)draft.shareholders.push({...shareholder(),ratio:''});
      } else if(action==='remove-shareholder') {
        if(draft.shareholders.length>1)draft.shareholders.splice(Number(button.dataset.tliqIndex),1);
        if(draft.quick.capitalLinked)draft.quick.paidCapital=yuan(sum(draft.shareholders.map(row=>amount(row.paid))));
      } else if(action==='link-capital') {
        draft.quick.capitalLinked=true;draft.quick.paidCapital=yuan(sum(draft.shareholders.map(row=>amount(row.paid))));
      } else if(action==='extract') {
        const fields=extractCompany(draft.paste);Object.assign(draft.company,fields);
        showMessage(Object.keys(fields).length?`已提取${Object.keys(fields).length}项企业信息，请核对。`:'未识别到企业字段，请粘贴带字段名称的工商信息。',!Object.keys(fields).length);
      } else if(action==='tax-rate') {draft.settlement.taxRate=button.dataset.tliqRate;
      } else if(action==='end-sample') {draft.sample=false;showMessage('示例标记已移除，请继续核实企业资料及清偿凭证。');
      } else if(action==='word') {
        if(busy)return;busy=true;button.disabled=true;button.textContent='正在生成 Word…';
        try{await downloadWord(buildReport(structuredClone(draft)));showMessage('Word 文档已生成，可在浏览器下载列表中查看。');}finally{busy=false;button.disabled=false;button.textContent='导出 Word (.docx)';}return;
      } else if(action==='print') {printReport();return;}
      save();refresh();
      if(action==='step')root.querySelector('.tliq-steps').scrollIntoView({block:'start'});
      else if(['sample','confirm-reset','undo'].includes(action))root.scrollIntoView({block:'start'});
    }catch(error){showMessage(error.message || String(error),true);}
  });
  refresh();
}
