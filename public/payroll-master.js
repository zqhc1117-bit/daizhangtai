import {escapeHtml as h} from './account-picker.js';

const base64=file=>new Promise((resolve,reject)=>{
  const reader=new FileReader();
  reader.onload=()=>resolve(String(reader.result).split(',')[1]);
  reader.onerror=()=>reject(new Error('无法读取工资大表文件'));
  reader.readAsDataURL(file);
});

export async function exportPayrollMaster(ctx,book){
  const prefix=`/api/books/${book.client.id}/payroll/master`;
  let preview=await ctx.api(`${prefix}/preview?period=${encodeURIComponent(book.period)}`);
  let content=null,reading=false,request=0;
  const pending=ctx.modal('导出到工资大表',`<p><strong>${h(book.client.name)}</strong> · ${h(book.period)}</p>
    <p>按公司名称找到对应工作表，沿用历史格式和月份间隔，写入本月全部人员工资。已有月份会更新原记录。</p>
    <div class="form-grid"><label class="field wide"><span>${preview.configured?'更换工资大表（可选）':'选择工资大表'}</span><input name="master_file" type="file" accept=".xlsx"><small>支持 Excel（.xlsx）。更新后保存到工作台，后续导出会继续写入这份大表。</small></label>
    <label class="field wide"><span>对应公司工作表</span><select name="master_sheet" required></select></label></div>
    <p data-master-status role="status"></p><p data-master-error class="form-error" role="alert"></p>`,async form=>{
    if(reading)throw new Error('正在检查工资大表，请稍候');
    if(!preview.configured)throw new Error('请先选择有效的工资大表');
    const sheet=form.elements.master_sheet.value;
    if(!sheet)throw new Error('请选择当前公司对应的工作表');
    const result=await ctx.api(`${prefix}/export`,{method:'POST',body:{period:book.period,sheet_name:sheet,master_version:preview.master_version,...(content?{content_base64:content}:{})}});
    const a=document.createElement('a');a.href=result.download_url;a.download=result.name;document.body.append(a);a.click();a.remove();
    ctx.toast(`已${result.updated?'更新':'追加'} ${result.sheet_name} 的 ${result.period} 工资，共 ${result.employee_count} 人，大表已保存并下载`);
  },{submitLabel:'更新大表并下载'});
  const form=document.querySelector('#app-modal-form');
  if(!form)return pending;
  const select=form.elements.master_sheet,status=form.querySelector('[data-master-status]'),error=form.querySelector('[data-master-error]'),submit=form.querySelector('[type="submit"]');
  const updateStatus=()=>{
    const target=preview.sheets.find(s=>s.name===select.value);
    status.textContent=!preview.configured?'请选择包含各公司历史工资的完整大表。':!target?'未找到唯一对应的公司，请核对表内公司名称后选择。':target.existing_months>1?`${target.name} 中有多份 ${book.period} 工资，请先整理重复月份。`:`${preview.sheet_name===target.name?`已按${preview.match_kind}匹配。`:''}本月 ${preview.employee_count} 人，将${target.existing_months?'更新该月份原有工资':'追加本月工资'}，保留月份之间的空行。`;
    submit.disabled=reading||!target||target.existing_months>1||!preview.employee_count;
  };
  const render=()=>{
    select.innerHTML='<option value="">请选择对应公司</option>'+preview.sheets.map(s=>`<option value="${h(s.name)}">${h(s.name)}（${h(s.company)}）</option>`).join('');
    select.value=preview.sheet_name||'';updateStatus();
  };
  select.addEventListener('change',updateStatus);
  form.elements.master_file.addEventListener('change',async()=>{
    const ticket=++request,file=form.elements.master_file.files[0];reading=true;submit.disabled=true;error.textContent='';status.textContent='正在检查公司名称和月份…';
    try{
      if(file&&(!/\.xlsx$/i.test(file.name)||file.size>10*1024*1024))throw new Error('请选择不超过 10MB 的 .xlsx 工资大表');
      const nextContent=file?await base64(file):null;
      const next=await ctx.api(`${prefix}/preview`,{method:'POST',body:{period:book.period,...(nextContent?{content_base64:nextContent}:{})}});
      if(ticket!==request)return;
      content=nextContent;preview=next;reading=false;render();
    }catch(e){
      if(ticket!==request)return;
      reading=false;preview={configured:false,sheets:[],sheet_name:''};content=null;render();error.textContent=e.message;
    }
  });
  render();return pending;
}
