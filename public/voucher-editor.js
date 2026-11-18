import {escapeHtml as h,accountPickerField,bindAccountPicker} from './account-picker.js';
import {voucherPrintMarkup,openVoucherPrintSettings,isReconciliationVoucher} from './company-vouchers.js';
const flow=[['unclassified','待分类'],['operating','经营活动'],['investing','投资活动'],['financing','筹资活动'],['transfer','内部资金划转']];
const plain=n=>(Number(n||0)/100).toFixed(2);
const money=n=>(Number(n||0)/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
export function parseVoucherMoney(value){
  const text=String(value??'').trim()||'0';
  if(!/^-?\d+(\.\d{1,2})?$/.test(text))throw new Error('金额最多两位小数，红字填写负数');
  const [whole,decimal='']=text.replace(/^-/,'').split('.'),result=(text.startsWith('-')?-1:1)*(Number(whole)*100+Number(decimal.padEnd(2,'0')));
  if(!Number.isSafeInteger(result)||Math.abs(result)>900000000000000)throw new Error('金额超出允许范围');
  return result;
}
export function chineseMoney(fen){
  if(!Number.isSafeInteger(fen))return '';
  if(fen<0)return '负'+chineseMoney(-fen);
  if(!fen)return '零元整';
  const digits='零壹贰叁肆伍陆柒捌玖',units=['','万','亿','兆'];
  let whole=Math.floor(fen/100),groups=[];
  while(whole){groups.unshift(whole%10000);whole=Math.floor(whole/10000);}
  let result='',zero=false;
  groups.forEach((n,i)=>{
    if(!n){zero=!!result;return;}
    if(result&&(zero||n<1000))result+='零';zero=false;
    let part='',pending=false;
    for(let p=3;p>=0;p--){const d=Math.floor(n/10**p)%10;if(d){if(pending)part+='零';part+=digits[d]+['','拾','佰','仟'][p];pending=false;}else if(part)pending=true;}
    result+=part+units[groups.length-i-1];
  });
  result=(result||'零')+'元';
  const jiao=Math.floor(fen%100/10),cent=fen%10;
  if(jiao)result+=digits[jiao]+'角';else if(cent)result+='零';
  if(cent)result+=digits[cent]+'分';else result+='整';
  return result;
}
const emptyLine=()=>({summary:'',account_code:'',debit:'',credit:'',cashflow_category:'unclassified'});
function settings(){try{return {...{large:false,rows:5,cashflow:true},...JSON.parse(localStorage.getItem('voucher-entry-settings')||'{}')};}catch{return {large:false,rows:5,cashflow:true};}}
function localDate(){const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
export function createVoucherDraft(book,v=null){
  const prefs=settings(),date=localDate();
  const lines=(v?.lines||[]).map(l=>({...l,summary:l.summary||'',debit:l.debit_fen?plain(l.debit_fen):'',credit:l.credit_fen?plain(l.credit_fen):''}));
  while(lines.length<Math.max(2,Math.min(20,Number(prefs.rows)||5)))lines.push(emptyLine());
  return {clientId:book.client.id,original:v,date:v?.date||(date.slice(0,7)===book.period?date:`${book.period}-${new Date(Number(book.period.slice(0,4)),Number(book.period.slice(5)),0).getDate()}`),summary:v?.summary||'',attachment_count:v?.attachment_count||0,voucher_word:v?.voucher_word||'记',lines,files:(v?.attachments||[]).map(f=>({...f})),dirty:false,busy:false,settings:prefs};
}
export function voucherPayload(draft,book){
  const v=draft.original,date=draft.date;
  const timestamp=Date.parse(`${date}T00:00:00Z`);
  if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||!Number.isFinite(timestamp)||new Date(timestamp).toISOString().slice(0,10)!==date)throw new Error('请选择有效的凭证日期');
  if(date.slice(0,7)<book.client.start_period)throw new Error('凭证日期不能早于建账期间');
  if(v&&date.slice(0,7)!==v.period&&date.slice(0,7)!==v.date.slice(0,7))throw new Error('编辑凭证日期须在原月份内');
  if(isReconciliationVoucher(v)&&date!==v.date)throw new Error('核对来源凭证日期须与原入账日期一致');
  const summary=String(draft.summary||draft.lines.find(l=>l.summary.trim())?.summary||'').trim();
  if(!summary||summary.length>200)throw new Error('请填写摘要，最多200字');
  const lines=draft.lines.map((l,i)=>({...l,index:i+1,debit_fen:parseVoucherMoney(l.debit),credit_fen:parseVoucherMoney(l.credit)})).filter(l=>l.account_code||l.debit_fen||l.credit_fen||l.summary.trim());
  if(lines.length<2||lines.length>500)throw new Error('凭证需要2至500条完整分录');
  for(const l of lines){
    const account=book.accounts.find(a=>a.code===l.account_code);
    if(!account)throw new Error(`第 ${l.index} 行请选择会计科目`);
    if(book.accounts.some(a=>a.code!==account.code&&a.code.startsWith(account.code)))throw new Error(`第 ${l.index} 行请选择末级科目`);
    if(account.report_key==='prior_adjustment')throw new Error('以前年度损益调整暂不支持记账');
    if(Boolean(l.debit_fen)===Boolean(l.credit_fen))throw new Error(`第 ${l.index} 行借贷金额只能一侧有金额，红字填写负数`);
    if(l.summary.length>200)throw new Error(`第 ${l.index} 行摘要超出200字`);
  }
  if(lines.reduce((n,l)=>n+l.debit_fen-l.credit_fen,0)!==0)throw new Error('借贷金额不平衡，请检查后保存');
  const count=Number(draft.attachment_count);
  if(!Number.isInteger(count)||count<0||count>9999)throw new Error('附单据张数须为0至9999的整数');
  const debitTotal=lines.reduce((n,l)=>n+l.debit_fen,0);
  if(!Number.isSafeInteger(debitTotal))throw new Error('凭证合计金额超出允许范围');
  const shared=v?.lines?.[0]?.summary,linkSummary=isReconciliationVoucher(v)&&summary!==v.summary&&v.lines.every(l=>l.summary===shared);
  return {date,summary,attachment_count:count,voucher_word:draft.voucher_word,lines:lines.map(l=>({summary:!l.summary.trim()||(v&&l.summary.trim()===v.summary)||(linkSummary&&l.summary===shared)?summary:l.summary.trim(),account_code:l.account_code,debit_fen:l.debit_fen,credit_fen:l.credit_fen,cashflow_category:l.cashflow_category||'unclassified'})),...(v?{version:v.version}:{})};
}
const digitLabels=['百','十','亿','千','百','十','万','千','百','十','元','角','分'];
const digitHeader=()=>`<div class="ve-digit-head" aria-hidden="true">${digitLabels.map((d,i)=>`<span class="${[2,6,10].includes(i)?'ve-place':''}">${d}</span>`).join('')}</div>`;
export function moneyDigits(fen){const digits=fen?String(fen).padStart(3,'0'):'';return `<div class="ve-money-digits" aria-hidden="true">${(fen<0||digits.length>13)?`<span class="ve-money-wide">${money(fen)}</span>`:digitLabels.map((_,i)=>`<span>${h(digits[i-(13-digits.length)]||'')}</span>`).join('')}</div>`;}
function rowHtml(line,index,book,draft){
  const account=book.accounts.find(a=>a.code===line.account_code),cash=account?.report_key==='cash';
  return `<tr class="voucher-line" data-ve-row="${index}"><td class="ve-sequence">${index+1}</td><td><textarea class="voucher-summary" data-ve-field="summary" rows="2" maxlength="200" aria-label="第${index+1}行摘要" placeholder="${index?'同凭证摘要':'填写摘要'}">${h(line.summary)}</textarea></td><td class="ve-account-cell">${accountPickerField(book.accounts,line.account_code,`aria-label="第${index+1}行会计科目"`)}<label class="ve-flow" ${cash&&draft.settings.cashflow?'':'hidden'}><span>资金活动</span><select class="voucher-cf" data-ve-field="cashflow_category" aria-label="第${index+1}行资金活动">${flow.map(([value,label])=>`<option value="${value}" ${value===line.cashflow_category?'selected':''}>${label}</option>`).join('')}</select></label></td>${['debit','credit'].map(key=>`<td class="ve-amount-cell">${moneyDigits(0)}<input class="voucher-${key}" data-ve-field="${key}" inputmode="decimal" autocomplete="off" value="${h(line[key])}" aria-label="第${index+1}行${key==='debit'?'借方':'贷方'}金额"></td>`).join('')}<td class="ve-row-actions"><button type="button" data-ve-row-action="up" title="上移分录" aria-label="上移第${index+1}行" ${index?'':'disabled'}>↑</button><button type="button" data-ve-row-action="down" title="下移分录" aria-label="下移第${index+1}行" ${index<draft.lines.length-1?'':'disabled'}>↓</button><button type="button" data-ve-row-action="copy" title="复制分录" aria-label="复制第${index+1}行">⧉</button><button type="button" data-ve-row-action="remove" title="删除分录" aria-label="删除分录">×</button></td></tr>`;
}
export function renderVoucherEditor(book,draft,ctx={}){
  const v=draft.original,period=v?.date.slice(0,7),lockedDate=isReconciliationVoucher(v),number=v?.number||Math.max(0,...(book.vouchers||[]).map(v=>Number(v.number)||0))+1;
  return `<section class="ve-page ${draft.settings.large?'ve-large':''}" aria-label="记账凭证录入"><div class="ve-toolbar"><h1>记账凭证</h1><div><button type="button" data-ve-action="shortcuts">⌨ 快捷键</button><button type="button" class="ve-circle" data-ve-action="previous" aria-label="上一张凭证">←</button><button type="button" class="ve-circle" data-ve-action="next" aria-label="下一张凭证">→</button><button type="button" class="ve-outlined" data-ve-action="list">更多凭证</button><button type="button" data-ve-action="size" title="切换字号">${draft.settings.large?'小':'大'}字</button><button type="button" class="ve-orange" data-ve-action="preview">预览</button><button type="button" data-ve-action="reset">↻ 重填</button></div></div><div class="ve-paper"><aside class="ve-attachments"><div class="ve-upload-zone" role="button" tabindex="0" aria-label="选择凭证附件" data-ve-action="attach"><span>原始凭证上传区</span><small>PDF / 图片 / 表格<br>每份最大10MB</small></div><div class="ve-file-list" data-ve-files></div><button type="button" class="ve-outlined" data-ve-action="attach">⇧ 添加附件</button><input type="file" data-ve-upload multiple accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.csv,.xlsx,.txt" hidden></aside><form class="ve-form" novalidate><div class="ve-meta"><label><select name="voucher_word" aria-label="凭证字" ${lockedDate?'disabled':''}>${['记','收','付','转'].map(word=>`<option ${word===draft.voucher_word?'selected':''}>${word}</option>`).join('')}</select>字第 <strong data-ve-number title="新增凭证保存时自动分配编号">${String(number).padStart(3,'0')}</strong> 号</label><label>日期：<input type="date" name="date" value="${h(draft.date)}" required ${lockedDate?'readonly':v?`min="${period}-01" max="${period}-${new Date(Number(period.slice(0,4)),Number(period.slice(5)),0).getDate()}"`:''} aria-label="凭证日期"></label><span class="ve-meta-space"></span><label>附单据 <input name="attachment_count" type="number" min="0" max="9999" step="1" value="${draft.attachment_count}" aria-label="附单据张数"> 张</label><span data-ve-file-count></span></div><label class="ve-shared-summary">凭证摘要<input name="summary" maxlength="200" value="${h(draft.summary)}" placeholder="也可在下方逐行填写摘要" aria-label="凭证摘要"></label>${v?`<p class="ve-edit-hint">正在编辑 ${h(v.voucher_word||'记')}-${String(v.number).padStart(3,'0')} · ${lockedDate?'资金净额须与原凭证一致。':'日期限原月份，保存后更新原凭证。'}</p>`:''}<div class="ve-grid-scroll"><table class="ve-grid"><colgroup><col class="ve-col-index"><col class="ve-col-summary"><col class="ve-col-account"><col class="ve-col-money"><col class="ve-col-money"><col class="ve-col-actions"></colgroup><thead><tr><th rowspan="2">序号</th><th rowspan="2">摘要</th><th rowspan="2"><div class="ve-account-heading">会计科目<button type="button" data-ve-action="settings">习惯设置</button></div></th><th>借方金额</th><th>贷方金额</th><th rowspan="2">操作</th></tr><tr><th>${digitHeader()}</th><th>${digitHeader()}</th></tr></thead><tbody data-ve-lines>${draft.lines.map((l,i)=>rowHtml(l,i,book,draft)).join('')}</tbody><tfoot><tr><td colspan="3"><b>合计：</b><span data-ve-chinese>零元整</span></td><td data-ve-debit-total>${moneyDigits(0)}</td><td data-ve-credit-total>${moneyDigits(0)}</td><td></td></tr></tfoot></table></div><div class="ve-under-grid"><button type="button" data-ve-action="add">＋ 添加分录</button><button type="button" data-ve-action="balance">＝ 自动配平当前行</button><span data-ve-balance role="status" aria-live="polite"></span></div><div class="ve-error" role="alert" data-ve-error></div><footer class="ve-footer"><span>制单人：${h(v?.created_by_name||ctx.user?.name||'')}<span class="ve-reviewer">审核人：${h(v?.reviewed_by_name||'')}</span></span><div><details class="ve-template-menu"><summary>模板 ⌄</summary><div><button type="button" data-ve-action="save-template">保存为模板</button><button type="button" data-ve-action="templates">选择 / 管理模板</button></div></details><button type="button" class="ve-orange" data-ve-action="save">保存</button><button type="button" class="ve-orange" data-ve-action="save-new">保存并新增</button><button type="button" class="ve-orange" data-ve-action="save-print">保存并打印</button></div></footer></form></div><p class="ve-page-hint">支持按科目编码、名称搜索。Tab 切换输入格，Ctrl + S 保存，Ctrl + Enter 保存并新增；金额按元输入，保存时校验借贷平衡。</p></section>`;
}
export function bindVoucherEditor(container,book,draft,ctx,actions){
  const root=container.querySelector('.ve-page');if(!root)return;
  const form=root.querySelector('form'),error=root.querySelector('[data-ve-error]');
  let activeRow=0;
  const fail=e=>{error.textContent=e.message||String(e);error.scrollIntoView({block:'nearest'});};
  const sync=()=>{
    for(const key of ['date','summary','attachment_count','voucher_word'])draft[key]=form.elements[key].value;
    root.querySelectorAll('[data-ve-row]').forEach((row,i)=>{for(const input of row.querySelectorAll('[data-ve-field]'))draft.lines[i][input.dataset.veField]=input.value;draft.lines[i].account_code=row.querySelector('[data-account-value]').value;});
  };
  const update=()=>{
    try{
      if(!draft.original)root.querySelector('[data-ve-number]').textContent=draft.date.slice(0,7)===book.period?String(Math.max(0,...(book.vouchers||[]).map(v=>Number(v.number)||0))+1).padStart(3,'0'):'自动';
      let debit=0,credit=0;
      root.querySelectorAll('[data-ve-row]').forEach((row,i)=>{
        const l=draft.lines[i];debit+=parseVoucherMoney(l.debit);credit+=parseVoucherMoney(l.credit);
        for(const key of ['debit','credit']){const input=row.querySelector(`.voucher-${key}`),cell=input.closest('td');cell.querySelector('.ve-money-digits').outerHTML=moneyDigits(parseVoucherMoney(l[key]));cell.classList.toggle('has-amount',!!l[key]);}
        const cash=book.accounts.find(a=>a.code===l.account_code)?.report_key==='cash';row.querySelector('.ve-flow').hidden=!cash||!draft.settings.cashflow;
      });
      root.querySelector('[data-ve-debit-total]').innerHTML=moneyDigits(debit);root.querySelector('[data-ve-credit-total]').innerHTML=moneyDigits(credit);
      root.querySelector('[data-ve-chinese]').textContent=chineseMoney(debit);
      const status=root.querySelector('[data-ve-balance]');status.textContent=`借 ${money(debit)}　贷 ${money(credit)}　差额 ${money(debit-credit)}`;status.className=debit===credit&&debit>0?'ve-balanced':'ve-unbalanced';
    }catch(e){root.querySelector('[data-ve-balance]').textContent=e.message;}
  };
  const files=()=>{
    root.querySelector('[data-ve-files]').innerHTML=draft.files.map((f,i)=>`<div class="ve-file"><button type="button" data-ve-preview-file="${i}" title="查看 ${h(f.name)}">${h(f.name)}</button><small>${Math.ceil(f.size/1024)} KB</small><button type="button" data-ve-remove-file="${i}" aria-label="移除附件 ${h(f.name)}">×</button></div>`).join('');
    root.querySelector('[data-ve-file-count]').textContent=`📎 ${draft.files.length}`;
  };
  const picker=bindAccountPicker(root,book.accounts,{onChange:(field,a)=>{const row=field.closest('[data-ve-row]');draft.lines[Number(row.dataset.veRow)].account_code=a?.code||'';draft.dirty=true;update();},onCreate:()=>actions.newAccount()});
  const redraw=()=>{picker.close();root.querySelector('[data-ve-lines]').innerHTML=draft.lines.map((l,i)=>rowHtml(l,i,book,draft)).join('');update();};
  root.addEventListener('focusin',e=>{const row=e.target.closest('[data-ve-row]');if(row)activeRow=Number(row.dataset.veRow);});
  root.addEventListener('input',e=>{if(!e.target.matches('[data-ve-field],[name]'))return;sync();draft.dirty=true;error.textContent='';update();});
  root.addEventListener('change',e=>{if(e.target.matches('[data-ve-field],[name]')){sync();draft.dirty=true;update();}});
  root.querySelector('[data-ve-upload]').addEventListener('change',e=>{
    try{
      const selected=[...e.target.files];if(selected.length+draft.files.length>30)throw new Error('每张凭证最多30份附件');
      for(const file of selected)if(!file.size||file.size>10*1024*1024||! /\.(pdf|png|jpe?g|gif|webp|csv|xlsx|txt)$/i.test(file.name))throw new Error('附件须为PDF、图片、CSV、XLSX或TXT，每份1字节至10MB');
      draft.files.push(...selected.map(file=>({file,name:file.name,size:file.size})));draft.dirty=true;files();
    }catch(e){fail(e);}finally{e.target.value='';}
  });
  const previewVoucher=()=>{sync();const payload=voucherPayload(draft,book);return {...draft.original,...payload,number:draft.original?.number||'待保存',created_by_name:draft.original?.created_by_name||ctx.user?.name,lines:payload.lines.map(l=>({...l,account_name:book.accounts.find(a=>a.code===l.account_code)?.name||''}))};};
  const save=async mode=>{
    if(draft.busy)return;
    sync();const payload=voucherPayload(draft,book);draft.busy=true;error.textContent='';picker.close();
    const controls=[...root.querySelectorAll('button,input,select,textarea')].map(el=>({el,disabled:el.disabled}));
    controls.forEach(({el})=>el.disabled=true);
    try{
      for(const file of draft.files)if(!file.id){const b64=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('读取附件失败'));reader.readAsDataURL(file.file);});const uploaded=await ctx.api('/api/upload',{method:'POST',body:{name:file.name,content_base64:b64}});file.id=uploaded.id;}
      payload.attachment_ids=draft.files.map(f=>f.id);
      const v=draft.original,saved=await ctx.api(`/api/books/${book.client.id}/vouchers${v?`/${v.id}`:''}`,{method:v?'PATCH':'POST',body:payload});
      draft.dirty=false;draft.original=saved;
      // Move to the committed record before any navigation/print; retry must never create it twice.
      await actions.saved(saved,mode);
      if(mode==='save-print')try{await openVoucherPrintSettings([saved],{...book,period:saved.period,vouchers:[saved]},ctx,{selected:[saved]});}catch(e){ctx.toast(`凭证已保存，打印未打开：${e.message}`,'error');}
    }finally{draft.busy=false;controls.forEach(({el,disabled})=>el.disabled=disabled);}
  };
  const confirmReset=async()=>!draft.dirty||await ctx.modal('重新填写凭证','<p>清除当前尚未保存的修改，重新载入这张凭证？</p>',async()=>{},{submitLabel:'重新填写'});
  const command=async action=>{
    sync();error.textContent='';
    root.querySelector('.ve-template-menu')?.removeAttribute('open');
    if(action.startsWith('save')&&action!=='save-template')return save(action);
    switch(action){
      case 'attach':root.querySelector('[data-ve-upload]').click();return;
      case 'add':if(draft.lines.length>=500)throw new Error('最多500条分录');draft.lines.push(emptyLine());draft.dirty=true;redraw();root.querySelector('[data-ve-row]:last-child textarea').focus();return;
      case 'balance':{
        const row=draft.lines[activeRow];if(!row)throw new Error('请先点击需要配平的分录');
        const delta=draft.lines.reduce((n,l,i)=>i===activeRow?n:n+parseVoucherMoney(l.debit)-parseVoucherMoney(l.credit),0);
        if(!delta)throw new Error('其他分录已平衡，请填写当前分录的实际金额');
        row.debit=delta<0?plain(-delta):'';row.credit=delta>0?plain(delta):'';draft.dirty=true;redraw();return;
      }
      case 'list':return actions.list();
      case 'previous':case 'next':return actions.move(action==='previous'?-1:1);
      case 'size':draft.settings.large=!draft.settings.large;root.classList.toggle('ve-large',draft.settings.large);root.querySelector('[data-ve-action=size]').textContent=draft.settings.large?'小字':'大字';try{localStorage.setItem('voucher-entry-settings',JSON.stringify(draft.settings));}catch{}return;
      case 'reset':if(await confirmReset())actions.reset();return;
      case 'preview':return ctx.modal('凭证预览',voucherPrintMarkup([previewVoucher()],book),null,{wide:true});
      case 'shortcuts':return ctx.modal('凭证录入快捷键','<p>Ctrl + S：保存<br>Ctrl + Enter：保存并新增<br>Tab / Shift + Tab：切换输入格<br>科目框输入编码、名称后，↑ / ↓ 选择，Enter 确认，Esc 收起<br>金额按元输入，保留最多两位小数；点击“自动配平当前行”补齐借贷差额。</p>');
      case 'settings':return ctx.modal('凭证录入习惯设置',`<label class="field"><span>新凭证默认行数</span><input type="number" name="rows" min="2" max="20" value="${draft.settings.rows}" required></label><label><input type="checkbox" name="cashflow" ${draft.settings.cashflow?'checked':''}> 显示现金、银行科目的资金活动分类</label>`,async f=>{const rows=Number(f.elements.rows.value);if(!Number.isInteger(rows)||rows<2||rows>20)throw new Error('行数须为2至20');draft.settings.rows=rows;draft.settings.cashflow=f.elements.cashflow.checked;try{localStorage.setItem('voucher-entry-settings',JSON.stringify(draft.settings));}catch{}update();});
      case 'save-template':{
        const payload=voucherPayload(draft,book);
        return ctx.modal('保存凭证模板','<label class="field"><span>模板名称</span><input name="name" required maxlength="80" placeholder="如：月度房租"></label><p class="muted">保存摘要、科目、金额和资金活动分类，使用时可继续修改。</p>',async f=>{await ctx.api(`/api/books/${book.client.id}/voucher-templates`,{method:'POST',body:{...payload,name:f.elements.name.value}});ctx.toast('模板已保存');});
      }
      case 'templates':{
        const result=await ctx.api(`/api/books/${book.client.id}/voucher-templates`),templates=result.templates||[];
        if(!templates.length)return ctx.modal('凭证模板','<p>还没有模板。填写一张完整、平衡的凭证后，点击“保存为模板”。</p>');
        const pending=ctx.modal('选择 / 管理凭证模板',`<label class="field"><span>选择模板</span><select name="template_id">${templates.map(t=>`<option value="${t.id}">${h(t.name)}</option>`).join('')}</select></label><p class="muted">应用后替换当前分录和摘要，日期和附件保持不变。</p><div data-ve-template-info></div><button type="button" class="btn" data-ve-delete-template>删除所选模板</button>`,async f=>{
          const t=templates.find(t=>String(t.id)===f.elements.template_id.value);if(!t)throw new Error('请选择模板');
          draft.summary=t.summary;draft.voucher_word=t.voucher_word;draft.lines=t.lines.map(l=>({...l,debit:l.debit_fen?plain(l.debit_fen):'',credit:l.credit_fen?plain(l.credit_fen):''}));
          form.elements.summary.value=draft.summary;form.elements.voucher_word.value=draft.voucher_word;draft.dirty=true;redraw();
        },{submitLabel:'使用模板'});
        queueMicrotask(()=>{
          const f=document.querySelector('#app-modal-form');if(!f)return;
          const info=()=>{const t=templates.find(t=>String(t.id)===f.elements.template_id.value);f.querySelector('[data-ve-template-info]').textContent=t?`${t.summary} · ${t.lines.length} 条分录`:'';};info();f.elements.template_id.addEventListener('change',info);
          f.querySelector('[data-ve-delete-template]').addEventListener('click',async e=>{e.target.disabled=true;try{const t=templates.find(t=>String(t.id)===f.elements.template_id.value);if(!t)return;await ctx.api(`/api/books/${book.client.id}/voucher-templates/${t.id}`,{method:'DELETE',body:{version:t.version}});templates.splice(templates.indexOf(t),1);f.elements.template_id.querySelector(`[value="${t.id}"]`).remove();info();ctx.toast('模板已删除');}catch(e){ctx.toast(e.message,'error');}finally{e.target.disabled=false;}});
        });return pending;
      }
    }
  };
  root.addEventListener('click',e=>{
    if(draft.busy)return;
    const rowAction=e.target.closest('[data-ve-row-action]');if(rowAction&&!rowAction.disabled){
      sync();const i=Number(rowAction.closest('[data-ve-row]').dataset.veRow),action=rowAction.dataset.veRowAction;
      if(action==='remove'){if(draft.lines.length<=2){fail(new Error('至少保留两行分录'));return;}draft.lines.splice(i,1);}
      if(action==='copy'){if(draft.lines.length>=500){fail(new Error('最多500条分录'));return;}draft.lines.splice(i+1,0,{...draft.lines[i]});}
      if(action==='up'&&i>0)[draft.lines[i-1],draft.lines[i]]=[draft.lines[i],draft.lines[i-1]];
      if(action==='down'&&i<draft.lines.length-1)[draft.lines[i+1],draft.lines[i]]=[draft.lines[i],draft.lines[i+1]];
      draft.dirty=true;redraw();return;
    }
    const remove=e.target.closest('[data-ve-remove-file]');if(remove){draft.files.splice(Number(remove.dataset.veRemoveFile),1);draft.dirty=true;files();return;}
    const preview=e.target.closest('[data-ve-preview-file]');if(preview){const f=draft.files[Number(preview.dataset.vePreviewFile)];if(f.id&&draft.original?.attachments?.some(a=>a.id===f.id))window.open(`/api/books/${book.client.id}/vouchers/${draft.original.id}/attachments/${f.id}?inline=1`,'_blank','noopener');else if(f.file){const url=URL.createObjectURL(f.file);window.open(url,'_blank','noopener');setTimeout(()=>URL.revokeObjectURL(url),60000);}return;}
    const control=e.target.closest('[data-ve-action]');if(control&&!control.disabled){e.preventDefault();Promise.resolve(command(control.dataset.veAction)).catch(fail);}
  });
  root.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&['s','Enter'].includes(e.key)){e.preventDefault();save(e.key==='s'?'save':'save-new').catch(fail);}if(e.target.matches('.ve-upload-zone')&&['Enter',' '].includes(e.key)){e.preventDefault();root.querySelector('[data-ve-upload]').click();}});
  form.addEventListener('submit',e=>{e.preventDefault();save('save').catch(fail);});
  const editable=(book.vouchers||[]).filter(v=>v.status==='active'&&!v.reviewed&&(v.source_type==='manual'||isReconciliationVoucher(v)));
  const current=editable.findIndex(v=>v.id===draft.original?.id);
  root.querySelector('[data-ve-action=previous]').disabled=!editable.length||(current===0);
  root.querySelector('[data-ve-action=next]').disabled=current<0||current>=editable.length-1;
  files();update();
}
