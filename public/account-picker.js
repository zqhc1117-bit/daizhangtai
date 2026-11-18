// Search the current book's real accounts; parents remain visible but cannot be posted.
export const escapeHtml = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const h=escapeHtml;
const categories=[['','全部'],['asset','资产'],['liability','负债'],['equity','权益'],['cost','成本'],['income','收入'],['expense','费用']];
const accountCodeLabel = account => account.displayCode ?? account.code;
const accountLabel = account => `${accountCodeLabel(account)} ${account.name}${account.note ? `（${account.note}）` : ''}`;
const normalizeQuery = value => value.trim().replace(/\s+/g,' ').toLocaleLowerCase();
const canSelect = account => account?.leaf && account.report_key !== 'prior_adjustment';

export function searchableAccounts(accounts){
  return accounts.map(a=>{
    const parents=accounts.filter(p=>p.code!==a.code&&a.code.startsWith(p.code)).sort((x,y)=>x.code.length-y.code.length);
    const fullName=[...parents.map(p=>p.name),a.name].join(' / ');
    return {...a,fullName,leaf:!accounts.some(p=>p.code!==a.code&&p.code.startsWith(a.code)),search:normalizeQuery(`${accountCodeLabel(a)} ${a.name} ${fullName}`)};
  });
}
export function accountPickerField(accounts,code='',attrs=''){
  const account=accounts.find(a=>a.code===code);
  return `<div class="account-picker-field"><input type="hidden" class="voucher-account" data-account-value value="${h(code)}"><input type="search" class="account-picker-search" role="combobox" aria-expanded="false" aria-autocomplete="list" aria-haspopup="listbox" autocomplete="off" placeholder="输入科目编码或名称搜索" value="${h(account?accountLabel(account):code)}" ${attrs}><span class="account-picker-arrow" aria-hidden="true">⌄</span></div>`;
}
export function bindAccountPicker(root,accounts,{onChange=()=>{},onCreate,include=()=>true,autoMatch=false}={}){
  const data=searchableAccounts(accounts).filter(include);
  const selectable = data.filter(canSelect);
  const startIndex = autoMatch ? -1 : 0;
  const popup=document.createElement('div');popup.className='account-picker-popup';popup.hidden=true;root.append(popup);
  const surface=root.closest('.cw-shell')||root.closest('.modal')||root;
  let field=null,query='',category='',index=startIndex,results=[];
  const outside = event => { if (!field.contains(event.target) && !popup.contains(event.target)) close(); };
  const scroll = event => {
    const scroller=event.target;
    if(popup.contains(scroller))return;
    if(!autoMatch){close();return;}
    if(!scroller.contains(field))return;
    const bounds=scroller.getBoundingClientRect(), rect=field.getBoundingClientRect();
    if(rect.bottom<=bounds.top || rect.top>=bounds.bottom || rect.right<=bounds.left || rect.left>=bounds.right){close();return;}
    position();
  };
  const close=()=>{
    if(!field)return;
    popup.hidden=true;field.querySelector('[role=combobox]').setAttribute('aria-expanded','false');field=null;
    surface.removeEventListener('pointerdown',outside);
    surface.removeEventListener('scroll',scroll,true);
  };
  const paint=()=>{
    const terms=normalizeQuery(query).split(' ');
    results=data.filter(a=>(!category||a.category===category)&&terms.every(term=>a.search.includes(term)));
    index=Math.max(startIndex,Math.min(index,results.length-1));
    const options=results.map((account,i)=>{
      const note=!account.leaf?'上级科目':account.report_key==='prior_adjustment'?'暂不支持记账':account.note;
      return `<button type="button" role="option" aria-selected="${i===index}" data-account-option="${h(account.code)}" class="${i===index?'highlighted':''}" ${canSelect(account)?'':'disabled'}><b>${h(accountCodeLabel(account))}</b><span>${h(account.fullName)}</span>${note?`<small>${h(note)}</small>`:''}</button>`;
    }).join('');
    popup.innerHTML=`<div class="account-picker-tabs">${categories.map(([id,label])=>`<button type="button" data-account-category="${id}" class="${id===category?'selected':''}">${label}</button>`).join('')}</div><div class="account-picker-options" role="listbox" aria-label="会计科目搜索结果">${options||'<p class="account-picker-empty">没有匹配科目，请更换名称或编码</p>'}</div><div class="account-picker-footer"><span>共 ${results.length} 项 · ↑↓ 选择，Enter 确认</span>${onCreate?'<button type="button" data-account-create>＋ 新增科目</button>':''}</div>`;
  };
  const position=()=>{
    if(!field)return;
    const rect=field.getBoundingClientRect(),width=Math.min(570,Math.max(rect.width,400),innerWidth-24);
    popup.style.width=`${width}px`;popup.style.left=`${Math.max(12,Math.min(rect.left,innerWidth-width-12))}px`;
    const space=innerHeight-rect.bottom-12,above=space<260&&rect.top>260;
    popup.style.top=above?'auto':`${rect.bottom+3}px`;popup.style.bottom=above?`${innerHeight-rect.top+3}px`:'auto';
    popup.style.maxHeight=`${Math.max(180,Math.min(380,above?rect.top-16:space))}px`;
  };
  const open=(next,typing=false)=>{
    const input=next.querySelector('[role=combobox]');
    if(field!==next){
      if(!field){surface.addEventListener('pointerdown',outside);surface.addEventListener('scroll',scroll,true);}
      field?.querySelector('[role=combobox]').setAttribute('aria-expanded','false');
      field=next;category='';index=startIndex;
      query=typing || (autoMatch && !next.querySelector('[data-account-value]').value) ? input.value : '';
    }else if(typing){query=input.value;category=autoMatch?'':category;}
    input.setAttribute('aria-expanded','true');paint();popup.hidden=false;position();
  };
  const setValue=(next,account)=>{
    next.querySelector('[data-account-value]').value=account?.code || '';
    onChange(next,account);
  };
  const select=code=>{
    const account=selectable.find(a=>a.code===code);if(!account||!field)return;
    const changed=field, input=changed.querySelector('[role=combobox]');
    input.value=accountLabel(account);
    close();setValue(changed,account);input.focus();
  };
  root.addEventListener('focusin',e=>{if(e.target.matches('.account-picker-search'))open(e.target.closest('.account-picker-field'));});
  root.addEventListener('input',e=>{
    if(!e.target.matches('.account-picker-search')||e.isComposing)return;
    const input=e.target, current=input.closest('.account-picker-field'), typed=normalizeQuery(input.value);
    const exact=autoMatch && typed ? selectable.filter(a=>[a.code,a.name,accountLabel(a)].some(value=>normalizeQuery(value)===typed)) : [];
    const account=exact.length===1 && exact[0].displayCode==null ? exact[0] : null;
    setValue(current,account);
    // Keep the query editable when it can still become a longer name or code.
    if(account && !selectable.some(a=>a!==account && [a.code,a.name].some(value=>normalizeQuery(value).startsWith(typed)))){
      input.value=accountLabel(account);
      close();return;
    }
    index=startIndex;open(current,true);
  });
  if(autoMatch) root.addEventListener('compositionstart',e=>{
    if(e.target.matches('.account-picker-search'))setValue(e.target.closest('.account-picker-field'),null);
  });
  root.addEventListener('compositionend',e=>{if(e.target.matches('.account-picker-search'))e.target.dispatchEvent(new Event('input',{bubbles:true}));});
  popup.addEventListener('pointerdown',e=>e.preventDefault());
  root.addEventListener('click',e=>{
    const option=e.target.closest('[data-account-option]');if(option&&!option.disabled){select(option.dataset.accountOption);return;}
    const tab=e.target.closest('[data-account-category]');if(tab){category=tab.dataset.accountCategory;index=startIndex;paint();return;}
    if(e.target.closest('[data-account-create]')){close();onCreate?.();return;}
    if(e.target.matches('.account-picker-search'))open(e.target.closest('.account-picker-field'));
  });
  root.addEventListener('keydown',e=>{
    if(!e.target.matches('.account-picker-search')||e.isComposing||e.keyCode===229)return;
    if(e.key==='Escape'||e.key==='Tab'){
      if(autoMatch && field && e.key==='Escape')e.preventDefault();
      close();return;
    }
    if(!['ArrowDown','ArrowUp','Enter'].includes(e.key))return;
    e.preventDefault();if(!field)open(e.target.closest('.account-picker-field'));
    if(e.key==='Enter'){
      const choices=results.filter(canSelect);
      const selected=results[index] || choices.find(a=>a.code===field.querySelector('[data-account-value]').value) || (choices.length===1 ? choices[0] : null);
      if(selected)select(selected.code);
      return;
    }
    const direction=e.key==='ArrowDown'?1:-1;
    if(index===-1 && direction===-1)index=0;
    for(let count=0;count<results.length;count++){index=(index+direction+results.length)%results.length;if(canSelect(results[index]))break;}
    paint();popup.querySelector('.highlighted')?.scrollIntoView({block:'nearest'});
  });
  root.addEventListener('focusout',e=>{
    if(!e.target.matches('.account-picker-search')||popup.contains(e.relatedTarget))return;
    if(autoMatch){
      const value=e.target.closest('.account-picker-field').querySelector('[data-account-value]').value;
      const account=selectable.find(a=>a.code===value);
      if(account)e.target.value=accountLabel(account);
    }
    close();
  });
  return {close};
}
