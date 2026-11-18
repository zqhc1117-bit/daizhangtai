// Keep the host startup lightweight; load this module only when a company opens it.
const bound = new WeakSet();
export function renderTaxLiquidation() {
  return '<div class="tliq-module-host"><div class="tliq-loading" role="status">正在打开企业清算工作台…</div></div>';
}
export async function bindTaxLiquidation(container,book,ctx) {
  const host=container.querySelector('.tliq-module-host');
  if(!host || bound.has(host))return;
  bound.add(host);
  try{
    const module=await import('./index.js');
    if(!host.isConnected)return;
    host.innerHTML=module.renderTaxLiquidation(book,ctx);
    module.bindTaxLiquidation(host,book,ctx);
  }catch(error){
    if(!host.isConnected)return;
    const message=document.createElement('p');
    message.className='tliq-load-error';
    message.textContent=`清算模块暂未加载成功：${error.message || error}。请重新打开本页或刷新浏览器。`;
    host.replaceChildren(message);
  }
}
