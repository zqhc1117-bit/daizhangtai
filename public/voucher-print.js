/* Shared voucher print settings and physical-page pagination. */
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = value => (Number(value || 0) / 100).toLocaleString('zh-CN', {minimumFractionDigits:2,maximumFractionDigits:2});
const total = (lines,key) => lines.reduce((n,l) => n + Number(l[key] || 0),0);
const number = v => /^\d+$/.test(String(v.number)) ? `${v.voucher_word || '记'}-${String(v.number).padStart(3,'0')}` : String(v.number || '待保存');
const STORAGE = 'voucher-print-settings-v1';
export const PRINT_LAYOUTS = Object.freeze({
  'a4-two':{label:'A4 两版（居中）',width:210,height:297,perPage:2},
  'a4-two-top':{label:'A4 两版（靠上）',width:210,height:297,perPage:2,top:true},
  'a4-two-digits':{label:'A4 两版（数分式）',width:210,height:297,perPage:2,digits:true},
  'a4-three':{label:'A4 三版',width:210,height:297,perPage:3},
  'voucher-21x12':{label:'凭证纸 21 × 12 cm',width:210,height:120,perPage:1},
  'voucher-24x14':{label:'凭证纸 24 × 14 cm',width:240,height:140,perPage:1},
  'voucher-24x14-digits':{label:'凭证纸 24 × 14 cm（数分式）',width:240,height:140,perPage:1,digits:true},
  'a4-one':{label:'A4 一版',width:210,height:297,perPage:1}
});
export const DEFAULT_PRINT_SETTINGS = Object.freeze({layout:'a4-two',rows:5,left:10,right:10,top:8,bottom:8,hideCode:false,bold:false,cutLine:true,includeVoid:false,manager:true,bookkeeper:true,reviewer:true,cashier:true,preparer:true});
const FLAGS = ['hideCode','bold','cutLine','includeVoid','manager','bookkeeper','reviewer','cashier','preparer'];
export function normalizePrintSettings(value={}) {
  const out={...DEFAULT_PRINT_SETTINGS,...value};
  if(!Object.hasOwn(PRINT_LAYOUTS,out.layout))throw new Error('请选择有效的纸张版式');
  out.rows=Number(out.rows);
  if(![5,6,7,8,10].includes(out.rows))throw new Error('每张凭证的分录行数须为5、6、7、8或10行');
  for(const key of ['left','right','top','bottom']){
    out[key]=Number(out[key]);
    if(!Number.isFinite(out[key])||out[key]<0||out[key]>30)throw new Error('页边距须为0至30毫米');
  }
  const layout=PRINT_LAYOUTS[out.layout];
  if(layout.width-out.left-out.right<150||(layout.height-out.top-out.bottom-(layout.perPage-1)*6)/layout.perPage<77)throw new Error('当前边距过大，请减小边距或选择每页更少的凭证');
  for(const key of FLAGS)out[key]=Boolean(out[key]);
  return out;
}
function loadSettings(){try{return normalizePrintSettings(JSON.parse(globalThis.localStorage?.getItem(STORAGE)||'{}'));}catch{return {...DEFAULT_PRINT_SETTINGS};}}
function saveSettings(settings){try{globalThis.localStorage?.setItem(STORAGE,JSON.stringify(settings));}catch{/* Printing works when storage is unavailable. */}}
function printMetrics(opts){
  const layout=PRINT_LAYOUTS[opts.layout],slot=(layout.height-opts.top-opts.bottom-(layout.perPage-1)*6)/layout.perPage;
  return {slot,rowHeight:Math.min(8,Math.max(3.4,(slot-45)/(opts.rows+2))),width:layout.width-opts.left-opts.right};
}
function textRows(value,width){
  // Conservative font-width estimate, including explicit newlines. Long summaries
  // consume extra vertical space instead of disappearing below the paper edge.
  const capacity=Math.max(1,(width-2.4)/3.175);
  return String(value||'').split(/\r?\n/).reduce((n,line)=>n+Math.max(1,Math.ceil(Array.from(line).reduce((w,c)=>w+(/[^\x00-\xff]/.test(c)?1:.65),0)/capacity)),0);
}
export function paginateVouchers(vouchers,settings={}) {
  const opts=normalizePrintSettings(settings),layout=PRINT_LAYOUTS[opts.layout],slips=[];
  const metrics=printMetrics(opts),budget=metrics.slot-50;
  for(const voucher of vouchers || []){
    if(voucher.status==='void'&&!opts.includeVoid)continue;
    const chunks=[];let current=[],height=0;
    const add=()=>{chunks.push({lines:current,padding:Math.max(0,Math.min(opts.rows-current.length,Math.floor((budget-height)/metrics.rowHeight)))});current=[];height=0;};
    for(const line of voucher.lines||[]){
      const size=Math.max(metrics.rowHeight,Math.max(textRows(line.summary||voucher.summary,metrics.width*.22),textRows((opts.hideCode?'':line.account_code+' ')+(line.account_name||''),metrics.width*.34))*4+1.2);
      if(current.length&&(current.length>=opts.rows||height+size>budget))add();
      current.push(line);height+=size;
    }
    if(current.length||!chunks.length)add();
    chunks.forEach((chunk,i)=>slips.push({voucher,part:i+1,parts:chunks.length,...chunk}));
  }
  const pages=[];
  for(let i=0;i<slips.length;i+=layout.perPage)pages.push(slips.slice(i,i+layout.perPage));
  return pages;
}
function amountHtml(value,digits){
  if(!digits)return value ? esc(money(value)) : '';
  const amount=Number(value||0),text=amount?String(Math.abs(amount)).padStart(3,'0'):'';
  if(text.length>11)return `<span class="vp-large-amount">${esc(money(amount))}</span>`;
  return `<span class="vp-digits ${amount<0?'vp-negative':''}">${text.padStart(11,' ').split('').map(d=>`<i>${d===' '?'':d}</i>`).join('')}</span>`;
}
function slipHtml(slip,book,opts,layout){
  const {voucher,part,parts,lines}=slip,padded=lines.slice();
  for(let i=0;i<slip.padding;i++)padded.push(null);
  const digits=layout.digits;
  const row=padded.map(line=>line?`<tr><td>${esc(line.summary||voucher.summary||'')}</td><td>${opts.hideCode?'':esc(line.account_code)+' '}${esc(line.account_name||book.accounts?.find(a=>a.code===line.account_code)?.name||'')}</td><td class="vp-amount">${amountHtml(line.debit_fen,digits)}</td><td class="vp-amount">${amountHtml(line.credit_fen,digits)}</td></tr>`:'<tr><td></td><td></td><td class="vp-amount">'+amountHtml(0,digits)+'</td><td class="vp-amount">'+amountHtml(0,digits)+'</td></tr>').join('');
  const labels=[['manager','会计主管',voucher.manager_name],['bookkeeper','记账人',voucher.bookkeeper_name],['reviewer','审核人',voucher.reviewed?voucher.reviewed_by_name:''],['cashier','出纳',voucher.cashier_name],['preparer','制单人',voucher.created_by_name||voucher.preparer]];
  const units='<span class="vp-digit-labels">'+Array.from('百十亿千百十万千百十元角分'.slice(-11)).map(s=>`<i>${s}</i>`).join('')+'</span>';
  return `<article class="cv-slip${digits?' vp-digit-slip':''}" data-voucher-id="${esc(voucher.id)}" data-part="${part}"><header><h1>记账凭证</h1><p>${esc(book.client?.name||'')}</p></header><div class="cv-slip-meta"><span>日期：${esc(voucher.date)}</span><span>凭证号：${esc(number(voucher))}${parts>1?`（${part}/${parts}）`:''}</span><span>附单据 ${Number(voucher.attachment_count||0)} 张</span><span>${voucher.status==='void'?'已作废':voucher.reviewed?'已审核':'未审核'}</span></div><table><colgroup><col style="width:22%"><col style="width:34%"><col style="width:22%"><col style="width:22%"></colgroup><thead><tr><th>摘要</th><th>会计科目</th><th>借方金额${digits?units:''}</th><th>贷方金额${digits?units:''}</th></tr></thead><tbody>${row}</tbody><tfoot><tr><td colspan="2">${parts>1?'本页合计':'合计'}</td><td class="vp-amount">${amountHtml(total(lines,'debit_fen'),digits)}</td><td class="vp-amount">${amountHtml(total(lines,'credit_fen'),digits)}</td></tr></tfoot></table>${parts>1?`<div class="vp-voucher-total">凭证合计：借方 ${esc(money(total(voucher.lines,'debit_fen')))}　贷方 ${esc(money(total(voucher.lines,'credit_fen')))}</div>`:''}<div class="cv-slip-sign">${labels.filter(([key])=>opts[key]).map(([,label,value])=>`<span>${label}：${esc(value||'')}</span>`).join('')}</div></article>`;
}
export function voucherPrintMarkup(vouchers,book,settings={}) {
  const opts=normalizePrintSettings(settings),layout=PRINT_LAYOUTS[opts.layout],pages=paginateVouchers(vouchers,opts);
  const {rowHeight}=printMetrics(opts);
  const variables=`--vp-width:${layout.width}mm;--vp-height:${layout.height}mm;--vp-left:${opts.left}mm;--vp-right:${opts.right}mm;--vp-top:${opts.top}mm;--vp-bottom:${opts.bottom}mm;--vp-count:${layout.perPage};--vp-row:${rowHeight}mm`;
  return `<div class="cv-print-pack ${layout.top?'vp-align-top':''} ${opts.bold?'vp-bold':''} ${opts.cutLine?'vp-cut-line':''}" style="${variables}">${pages.map((page,i)=>`<section class="vp-sheet" data-print-page="${i+1}">${page.map(slip=>`<div class="vp-slot">${slipHtml(slip,book,opts,layout)}</div>`).join('')}</section>`).join('')}</div>`;
}
let activeCleanup;
export function printVoucherSheets(vouchers,book,settings=loadSettings()) {
  const opts=normalizePrintSettings(settings),layout=PRINT_LAYOUTS[opts.layout],doc=globalThis.document;
  if(!doc?.body||typeof globalThis.print!=='function')throw new Error('当前环境不能直接打印');
  if(!paginateVouchers(vouchers,opts).length)throw new Error('没有可打印的凭证');
  activeCleanup?.();
  const root=doc.createElement('div');root.id='cv-print-root';root.className='cv-print-root';
  root.innerHTML=voucherPrintMarkup(vouchers,book,opts);
  // Zero page margins suppress browser-added title, URL and date. Content margins
  // live inside each physical sheet, so two vouchers remain on one A4 page.
  const style=doc.createElement('style');style.media='print';
  style.textContent=`@page {size:${layout.width}mm ${layout.height}mm;margin:0}`;
  root.appendChild(style);doc.body.appendChild(root);
  if(root.querySelectorAll){
    root.classList.add('vp-measuring');
    const overflow=[...root.querySelectorAll('.vp-slot')].some(slot=>slot.firstElementChild.getBoundingClientRect().height>slot.getBoundingClientRect().height+1);
    root.classList.remove('vp-measuring');
    if(overflow){root.remove();throw new Error('当前版面无法完整容纳较长的摘要或科目，请选择每页更少的凭证、增加纸张高度或减小边距');}
  }
  const previousTitle=doc.title;doc.title='';doc.body.classList.add('printing-vouchers');
  const media=globalThis.matchMedia?.('print');let done=false;
  const cleanup=()=>{if(done)return;done=true;doc.title=previousTitle;doc.body.classList.remove('printing-vouchers');root.remove();media?.removeEventListener?.('change',onMedia);globalThis.removeEventListener('afterprint',cleanup);activeCleanup=undefined;};
  const onMedia=()=>{if(!media.matches)cleanup();};
  activeCleanup=cleanup;media?.addEventListener?.('change',onMedia);globalThis.addEventListener('afterprint',cleanup,{once:true});
  try{globalThis.print();}catch(error){cleanup();throw error;}
}
function readForm(form){
  const value={};for(const key of ['layout','rows','left','right','top','bottom'])value[key]=form.elements[key].value;
  for(const key of FLAGS)value[key]=form.elements[key].checked;
  return normalizePrintSettings(value);
}
export async function openVoucherPrintSettings(vouchers,book,ctx,{selected=[]}={}) {
  const opts=loadSettings(),all=book.vouchers||vouchers;
  const ranges={selected,filtered:vouchers,all};
  const range=selected.length?'selected':'filtered';
  const check=(key,label)=>`<label><input type="checkbox" name="${key}" ${opts[key]?'checked':''}>${label}</label>`;
  const html=`<div class="vp-settings"><div class="vp-tabs"><span>凭证打印</span><small>设置完成后选择打印机，或另存为 PDF</small></div><div class="vp-setting-row"><strong>打印范围</strong><div class="vp-choices">${[['selected','选中凭证'],['filtered','当前筛选'],['all','本期全部凭证']].map(([key,label])=>`<label><input type="radio" name="range" value="${key}" ${key===range?'checked':''} ${!ranges[key].length?'disabled':''}>${label}（${ranges[key].length} 张）</label>`).join('')}</div></div><div class="vp-setting-row"><strong>选择纸张</strong><div class="vp-paper-choices">${Object.entries(PRINT_LAYOUTS).map(([key,layout])=>`<label><input type="radio" name="layout" value="${key}" ${opts.layout===key?'checked':''}>${layout.label}</label>`).join('')}</div></div><div class="vp-setting-row"><strong>边距调整</strong><div class="vp-margins">${[['left','左'],['right','右'],['top','上'],['bottom','下']].map(([key,label])=>`<label>${label}<input type="number" name="${key}" value="${opts[key]}" min="0" max="30" step="0.5" required aria-label="${label}边距">毫米</label>`).join('')}</div></div><div class="vp-setting-row"><strong>行数设置</strong><div class="vp-choices"><select name="rows" aria-label="每张凭证分录行数">${[5,6,7,8,10].map(n=>`<option value="${n}" ${n===opts.rows?'selected':''}>${n} 行</option>`).join('')}</select><span class="vp-help">超出行数自动续页，保留凭证号和全部分录</span></div></div><div class="vp-setting-row"><strong>其他设置</strong><div class="vp-choices">${check('hideCode','隐藏科目编码')}${check('bold','打印字体加粗')}${check('cutLine','显示裁切线')}${check('includeVoid','打印作废凭证')}</div></div><div class="vp-setting-row"><strong>显示人员</strong><div class="vp-choices">${check('manager','会计主管')}${check('bookkeeper','记账人')}${check('reviewer','审核人')}${check('cashier','出纳')}${check('preparer','制单人')}</div></div><div class="vp-preview-summary" data-vp-count role="status"></div><div class="vp-preview-wrap"><div class="vp-preview" data-vp-preview aria-label="打印首页预览"></div></div></div>`;
  const pending=ctx.modal('凭证打印设置',html,async form=>{
    const settings=readForm(form),chosen=ranges[form.elements.range.value];
    if(!paginateVouchers(chosen,settings).length)throw new Error('当前范围没有可打印的凭证');
    printVoucherSheets(chosen,book,settings);saveSettings(settings);
  },{wide:true,submitLabel:'打印'});
  const form=globalThis.document?.getElementById('app-modal-form');
  if(form?.querySelector('.vp-settings')){
    const update=()=>{const count=form.querySelector('[data-vp-count]'),preview=form.querySelector('[data-vp-preview]');try{
      const settings=readForm(form),chosen=ranges[form.elements.range.value],pages=paginateVouchers(chosen,settings),layout=PRINT_LAYOUTS[settings.layout];
      const valid=chosen.filter(v=>v.status!=='void'||settings.includeVoid);
      count.textContent=`${valid.length} 张凭证 · ${pages.reduce((n,p)=>n+p.length,0)} 个凭证版面 · ${pages.length} 张纸　${layout.label}（${layout.width} × ${layout.height} mm）`;
      count.classList.remove('vp-error');preview.innerHTML=voucherPrintMarkup(valid.slice(0,layout.perPage),book,settings);
      preview.querySelectorAll('.vp-sheet').forEach((el,i)=>{if(i)el.remove();});
      preview.style.width=`${layout.width*0.756}px`;preview.style.height=`${layout.height*0.756}px`;
    }catch(e){count.textContent=e.message;count.classList.add('vp-error');preview.replaceChildren();}};
    form.addEventListener('input',update);form.addEventListener('change',update);update();
  }
  return pending;
}
