import {importStatement} from './statement-import.js';
import {buildUploadReport,statementSummary} from './upload-report-model.js';
import {renderReport} from './report.js';
import {downloadWord,printReport} from './export.js';
import {excelAmount} from './full/excel.js';
import {h} from './shared/fields.js';
const sessions=new Map(),storageKey=(book,ctx)=>`ydz:sales-statement:v1:${ctx.demo?'demo':'local'}:${ctx.user?.id||'local'}`;
function stateFor(book,ctx){
  const key=storageKey(book,ctx);if(sessions.has(key))return sessions.get(key);
  const state={taxpayer:'small',statement:null,companyName:'',baseDate:'',generated:false,error:'',busy:false};
  try{const stored=JSON.parse(localStorage.getItem(key)||'null');if(stored?.version===1){
    if(['small','general'].includes(stored.taxpayer))state.taxpayer=stored.taxpayer;
    if(stored.statement&&Array.isArray(stored.statement.entries)&&stored.statement.entries.length<=100&&stored.statement.entries.every(e=>e&&typeof e.label==='string'&&['asset','liability','equity'].includes(e.group)&&excelAmount(e.value)!==null)){
      state.statement=stored.statement;state.companyName=String(stored.companyName||'').slice(0,180);state.baseDate=String(stored.baseDate||'');state.generated=stored.generated===true;
    }
  }}catch{state.error='浏览器草稿读取失败，请重新上传报表。';}
  sessions.set(key,state);return state;
}
export function renderUploadReport(book,ctx){
  const state=stateFor(book,ctx);
  return `<div class="tliq-workspace sales-tax-report" data-sales-tax><section class="sales-upload"><fieldset class="sales-taxpayer"><legend>纳税人类型</legend>${[['small','小规模纳税人'],['general','一般纳税人']].map(([value,label])=>`<label><input type="radio" name="sales-taxpayer" value="${value}" ${state.taxpayer===value?'checked':''}>${label}</label>`).join('')}</fieldset>
    <h2>上传资产负债表或科目余额表</h2><label class="sales-drop" data-sales-drop><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 22v6h22v-6M16 23V4M9 11l7-7 7 7"/></svg><b>点击选择文件，或拖拽到此处</b><span>PDF · Excel（.xlsx / .xls）· CSV / TSV · PNG / JPG / WebP</span><span>每次上传一份报表，最大 10 MB；支持扫描件识别</span><input type="file" data-sales-file accept=".pdf,.xlsx,.xls,.csv,.tsv,.png,.jpg,.jpeg,.webp" aria-label="上传资产负债表或科目余额表"></label>
    <div class="sales-file-status" data-sales-status role="status" aria-live="polite"></div><button type="button" class="btn btn-primary sales-generate" data-sales-generate>生成清算清偿报告</button>
    <details class="sales-recognition" data-sales-recognition hidden><summary>查看 / 修正识别结果</summary><div data-sales-recognized></div></details></section>
    <section class="sales-report-result" data-sales-result hidden><div class="sales-report-actions"><h2>清算清偿报告</h2><button type="button" class="btn" data-sales-word>导出 Word</button><button type="button" class="btn btn-primary" data-sales-print>打印 / 导出 PDF</button></div><div class="sales-report-note"><span>实际变现、债务清偿和完税信息请在清算填报工具中录入。</span><button type="button" class="btn" data-sales-fill>去填写</button></div><div data-sales-report></div></section></div>`;
}
export function bindUploadReport(container,book,ctx){
  const root=container.querySelector('[data-sales-tax]');if(!root||root.dataset.bound)return;root.dataset.bound='1';
  const state=stateFor(book,ctx),status=root.querySelector('[data-sales-status]'),generate=root.querySelector('[data-sales-generate]');
  const persist=()=>{try{const {taxpayer,statement,companyName,baseDate,generated}=state;localStorage.setItem(storageKey(book,ctx),JSON.stringify({version:1,taxpayer,statement,companyName,baseDate,generated}));state.error='';}catch{state.error='浏览器未能保存草稿，请及时导出报告；刷新后本次内容可能丢失。';}};
  const report=()=>buildUploadReport(state.statement,state.taxpayer,state.companyName,state.baseDate);
  const showResult=()=>{const result=root.querySelector('[data-sales-result]');result.hidden=!state.generated;if(state.generated)root.querySelector('[data-sales-report]').innerHTML=renderReport(report());};
  const invalidate=()=>{state.generated=false;persist();showResult();};
  const refresh=()=>{
    generate.disabled=state.busy||!state.statement;
    root.querySelector('[data-sales-file]').disabled=state.busy;
    root.querySelector('[data-sales-drop]').classList.toggle('sales-upload-busy',state.busy);
    generate.textContent=state.busy?'正在识别报表…':'生成清算清偿报告';
    const recognized=root.querySelector('[data-sales-recognition]');recognized.hidden=!state.statement;
    if(state.statement){const s=state.statement;root.querySelector('[data-sales-recognized]').innerHTML=`<div class="sales-import-fields"><label>企业名称<input data-sales-company maxlength="180" value="${h(state.companyName)}" placeholder="未识别时可补填"></label><label>报表基准日<input type="date" data-sales-date value="${h(state.baseDate)}"></label></div><div class="sales-import-table"><table><thead><tr><th>报表项目</th><th>原始科目</th><th>期末金额（元）</th></tr></thead><tbody>${s.entries.map((e,i)=>`<tr><td>${h(e.label)}</td><td>${h(e.sourceLabel)}</td><td><input data-sales-amount="${i}" inputmode="decimal" aria-label="${h(e.label)}期末金额" value="${h(e.value)}"></td></tr>`).join('')}</tbody></table></div><ul class="sales-import-warnings">${statementSummary(s).warnings.map(w=>`<li>${h(w)}</li>`).join('')}</ul>`;}
    status.classList.toggle('error',Boolean(state.error));status.textContent=state.busy?'正在识别报表，请稍候。扫描 PDF / 图片可能需要更长时间…':state.error|| (state.statement?`已识别「${state.statement.fileName}」 · ${state.statement.kind} · ${state.statement.entries.length} 项 · 金额已换算为元。\n可直接生成报告，或展开下方查看识别结果。`:'请选择报表文件，识别完成后即可生成报告。');
    showResult();
  };
  const upload=async file=>{
    if(!file||state.busy)return;
    state.busy=true;state.error='';state.statement=null;state.generated=false;state.companyName='';state.baseDate='';persist();refresh();
    try{
      const next=await importStatement(file,ctx);state.statement=next;state.companyName=next.metadata.companyName;state.baseDate=next.metadata.baseDate;persist();
    }catch(error){state.error=error.message||'文件识别失败，请重新上传。';}
    finally{state.busy=false;if(root.isConnected)root.querySelector('[data-sales-file]').value='';state.refresh?.();}
  };
  root.addEventListener('change',event=>{
    const input=event.target;
    if(input.matches('[data-sales-file]'))upload(input.files?.[0]);
    else if(input.name==='sales-taxpayer'){state.taxpayer=input.value;invalidate();}
    else if(input.hasAttribute('data-sales-amount')){
      const value=excelAmount(input.value);if(value===null){input.setCustomValidity('请输入有效金额');input.reportValidity();generate.disabled=true;return;}
      input.setCustomValidity('');state.statement.entries[Number(input.dataset.salesAmount)].value=value;state.statement.warnings=[...new Set([...state.statement.warnings,'部分识别金额已由操作员修正，请复核与原表总计的一致性。'])];invalidate();refresh();
    }
  });
  root.addEventListener('input',event=>{
    if(event.target.matches('[data-sales-company]')){state.companyName=event.target.value;invalidate();}
    else if(event.target.matches('[data-sales-date]')){state.baseDate=event.target.value;invalidate();}
    else if(event.target.hasAttribute('data-sales-amount')){state.generated=false;showResult();}
  });
  const drop=root.querySelector('[data-sales-drop]');
  drop.addEventListener('dragover',event=>{event.preventDefault();drop.classList.add('dragging');});
  drop.addEventListener('dragleave',()=>drop.classList.remove('dragging'));
  drop.addEventListener('drop',event=>{event.preventDefault();drop.classList.remove('dragging');if(event.dataTransfer.files.length>1){ctx.toast('请每次上传一份报表。','error');return;}upload(event.dataTransfer.files[0]);});
  root.addEventListener('click',async event=>{
    const button=event.target.closest('[data-sales-generate],[data-sales-word],[data-sales-print]');if(!button||button.disabled)return;
    try{
      if(button.hasAttribute('data-sales-generate')){if(!state.statement||state.busy)return;state.generated=true;persist();showResult();if(state.error){status.textContent=state.error;status.classList.add('error');}root.querySelector('[data-sales-result]').scrollIntoView({behavior:'smooth',block:'start'});}
      else if(button.hasAttribute('data-sales-word')){button.disabled=true;await downloadWord(report());}
      else printReport();
    }catch(error){ctx.toast(error.message,'error');}finally{button.disabled=false;}
  });
  state.refresh=()=>{if(root.isConnected)refresh();};
  refresh();
}
