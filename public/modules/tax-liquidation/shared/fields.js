export const h = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export function field(path,label,value,{hint='',type='text',money=false,full=false} = {}) {
  return `<label class="tliq-field ${full?'tliq-wide':''}"><span>${h(label)}</span>${hint?`<small>${h(hint)}</small>`:''}<span class="tliq-input-wrap"><input data-tliq-path="${h(path)}" value="${h(value)}" type="${type}" ${money?'inputmode="decimal" maxlength="24"':'maxlength="300"'} ${type === 'date'?'min="1900-01-01" max="2199-12-31"':''} aria-label="${h(label)}">${money?'<i>元</i>':''}</span></label>`;
}
export function select(path,label,value,options,hint = '') {
  return `<label class="tliq-field"><span>${h(label)}</span>${hint?`<small>${h(hint)}</small>`:''}<select data-tliq-path="${h(path)}" aria-label="${h(label)}">${options.map(([key,text]) => `<option value="${h(key)}" ${value === key?'selected':''}>${h(text)}</option>`).join('')}</select></label>`;
}
export function textarea(path,label,value,hint = '') {
  return `<label class="tliq-field tliq-wide"><span>${h(label)}</span>${hint?`<small>${h(hint)}</small>`:''}<textarea data-tliq-path="${h(path)}" rows="3" maxlength="10000" aria-label="${h(label)}">${h(value)}</textarea></label>`;
}
export function checkbox(path,label,value) {return `<label class="tliq-checkbox"><input type="checkbox" data-tliq-path="${h(path)}" ${value?'checked':''}>${h(label)}</label>`;}
export function section(title,description,content) {return `<section class="tliq-card"><div class="tliq-card-heading"><h3>${h(title)}</h3>${description?`<p>${h(description)}</p>`:''}</div>${content}</section>`;}
