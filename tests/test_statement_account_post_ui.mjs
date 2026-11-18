import assert from 'node:assert/strict';
import {bindReconcile, renderReconcile, resetReconcile, statementRecommendation} from '../public/reconcile.js';
import {statementPostPreview} from '../public/reconcile-voucher.js';

const interestPreview = statementPostPreview({transaction:{date:'2026-09-21',amount:'0.04'},businessAccount:{code:'5603',name:'财务费用'},bankAccount:{code:'1002',name:'银行存款'}});
assert.equal(interestPreview.direction, '收款');
assert.deepEqual(interestPreview.lines.map(line=>[line.account_code,line.debit_fen,line.credit_fen]), [['1002',4,0],['5603',0,4]]);
const paymentPreview = statementPostPreview({transaction:{amount:'-12.50'},businessAccount:{code:'5602',name:'管理费用'},bankAccount:{code:'1002',name:'银行存款'}});
assert.equal(paymentPreview.direction, '付款');
assert.deepEqual(paymentPreview.lines.map(line=>[line.account_code,line.debit_fen,line.credit_fen]), [['5602',1250,0],['1002',0,1250]]);
assert.equal(statementRecommendation({rule_suggestion:{account_code:'5603',account_name:'财务费用'}},[{code:'5603',name:'财务费用',report_key:'finance'},{code:'560301',name:'利息收入',report_key:'finance'}]).ok, false);

function control(dataset={}){
 return {dataset,disabled:false,hidden:false,value:'',files:[],handlers:new Map(),addEventListener(type,handler){this.handlers.set(type,handler);}};
}
function fixtureHost(){
 const root={isConnected:true,onclick:null,contains(node){return node===this.page;},querySelector(selector){
  return {'[data-rc-account]':this.account,'[data-rc-client]':this.client,'.rc-root':this.page}[selector]||null;
 },querySelectorAll(selector){
  if(selector==='[data-rc-file]')return this.files;
  if(selector==='input,select,button')return [this.account,this.client,...this.files];
  if(selector==='[data-rc-progress]')return this.progress;
  return [];
 }};
 let html='';
 Object.defineProperty(root,'innerHTML',{get(){return html;},set(value){
  html=value;if(this.page)this.page.isConnected=false;
  this.page={isConnected:true};this.account=control();this.client=control();
  this.files=[control({rcFile:'statement'}),control({rcFile:'invoices'})];this.progress=[control(),control()];
 }});
 root.innerHTML='';
 return root;
}
function installDocument(){
 const nodes={};
 const make=name=>({name,value:'',hidden:false,disabled:false,textContent:'',listeners:{},addEventListener(type,fn){(this.listeners[type]||=[]).push(fn);},focus(){},remove(){}});
 const node=name=>nodes[name]||=make(name);
 const root={html:'',remove(){},addEventListener(){},set innerHTML(value){this.html=value;},get innerHTML(){return this.html;},querySelector(selector){
  const found={form:node('form'),'[data-sp-error]':node('error'),'[data-sp-lines]':node('lines'),'[data-sp-direction]':node('direction'),'[data-sp-bank]':node('bank'),'[data-sp-duplicate]':node('duplicate'),'[data-sp-duplicate-message]':node('message'),'[name="summary"]':node('summary'),'[name="duplicate_review_note"]':node('note'),'[type="submit"]':node('submit')}[selector];
  return found||null;
 },querySelectorAll(selector){
  if(selector==='[data-sp-cancel]')return [node('cancel')];
  if(selector==='button,input,select,textarea')return [node('bank'),node('note'),node('submit'),node('cancel')];
  return [];
 }};
 node('form').querySelector=selector=>root.querySelector(selector);
 const document={activeElement:null,body:{append(child){this.node=child;}},createElement(){return root;},addEventListener(){},removeEventListener(){}};
 globalThis.document=document;
 return {root,node};
}
const clients=[{id:4,name:'合成本方公司',standard:'small2013',start_period:'2026-01'}];
const suggestion={status:'provisional',account_code:'5603',account_name:'财务费用',rule_ids:['P1-2'],basis:'银行结息收入，建议借记银行存款、贷记财务费用。'};
function accounts({child=false}={}){
 const rows=[{code:'1001',name:'库存现金',report_key:'cash'},{code:'1002',name:'银行存款',report_key:'cash'},{code:'5602',name:'管理费用',report_key:'administration'},{code:'5603',name:'财务费用',report_key:'finance'}];
 if(child)rows.push({code:'560301',name:'利息收入',report_key:'finance'});
 return rows;
}
async function openInterest({child=false}={}){
 resetReconcile();
 const root=fixtureHost(),calls=[];
 const tx={date:'2026-09-21',amount:'0.04',counterparty:'应付利息-单位活期存款利息-自动计提',note:'应付利息-单位活期存款利息-自动计提',source:'synthetic.pdf'};
 const fixtureApi=async(url,options)=>{
  calls.push({url,options});
  if(url==='/api/reconcile/parse/start')return {job_id:'synthetic-job'};
  if(url.startsWith('/api/reconcile/parse/progress?'))return {status:'completed',result:{account_name:clients[0].name,transactions:[tx],invoices:[],receipts:[]}};
  if(url==='/api/reconcile/match')return {matches:[{transaction_index:0,invoice_index:null,status:'review',reasons:['未找到唯一匹配发票']}],classification_transactions:[{status:'review',account_code:null,rule_suggestion:suggestion}],receipt_matches:{matches:[]},unmatched_invoices:[]};
  if(url.startsWith('/api/books/'))return {accounts:accounts({child})};
  if(url==='/api/reconcile/statement-post')return {status:'posted',voucher_id:9,voucher_ref:'记 2026-09-0009',period:'2026-09',posted_lines:[{account_code:'1002',account_name:'银行存款',debit_fen:4,credit_fen:0},{account_code:options.body.business_account_code,account_name:'科目',debit_fen:0,credit_fen:4}]};
  throw new Error('Unexpected fixture route: '+url);
 };
 root.innerHTML=renderReconcile(clients);
 bindReconcile(root,fixtureApi,clients);
 root.client.value=String(clients[0].id);
 await root.client.handlers.get('change')({target:root.client});
 const file=new Blob(['synthetic input'],{type:'application/pdf'});file.name='synthetic.pdf';
 root.files[0].files=[file];
 await root.files[0].handlers.get('change')();
 await root.onclick({target:{dataset:{rcAction:'parse'},textContent:'',closest(selector){return selector==='[data-rc-action]'?this:null;}}});
 await new Promise(resolve=>setTimeout(resolve,0));
 return {root,calls};
}
const previousAlert=globalThis.alert;
const alerts=[];
globalThis.alert=message=>alerts.push(message);
const previousReader=globalThis.FileReader;
globalThis.FileReader=class {readAsDataURL(){this.result='data:application/pdf;base64,c3ludGhldGlj';queueMicrotask(()=>this.onload());}};
try{
 const pending=await openInterest();
 assert.equal(pending.calls.some(call=>call.url==='/api/reconcile/statement-post'), false);
 assert.match(pending.root.innerHTML, /采用推荐科目入账（5603 财务费用）/);
 assert.match(pending.root.innerHTML, /按所选科目入账/);
 assert.match(pending.root.innerHTML, /data-rc-statement-bank="0"/);
 assert.doesNotMatch(pending.root.innerHTML, /补充业务科目及依据/);
 assert.match(pending.root.innerHTML, /<input type="search"[^>]*role="combobox"[^>]*data-rc-manual-account="0"/);
 assert.match(pending.root.innerHTML, /data-account-value value=""/);
 assert.match(pending.root.innerHTML, /data-rc-manual-post="0" disabled/);
 assert.match(pending.root.innerHTML, /已入账 <b>0<\/b>/);

 const blocked=await openInterest({child:true});
 assert.match(blocked.root.innerHTML, /不是可入账的末级科目/);
 assert.match(blocked.root.innerHTML, /data-rc-adopt-post="0" disabled/);
 assert.match(blocked.root.innerHTML, /data-rc-manual-post="0" disabled/);

 const dialog=installDocument();
 const posting=await openInterest();
 const adoptClick=posting.root.onclick({target:{dataset:{rcAdoptPost:'0'},closest(selector){return selector==='[data-rc-adopt-post]'?this:null;}}});
 assert.match(dialog.root.html, /确认入账/);
 assert.match(dialog.root.html, /5603 财务费用/);
 assert.match(dialog.root.html, /不会写入账套/);
 dialog.node('cancel').listeners.click[0]();
 await adoptClick;
 assert.equal(posting.calls.some(call=>call.url==='/api/reconcile/statement-post'), false, '取消确认不得入账');

 const manualClick=posting.root.onclick({target:{dataset:{rcManualPost:'0'},closest(selector){
  if(selector==='[data-rc-manual-post]')return this;
  if(selector==='[data-rc-account-post]')return {querySelector(query){return query==='[data-account-value]'?{value:'5602'}:null;}};
  return null;
 }}});
 assert.match(dialog.root.html, /不会改用推荐科目/);
 dialog.node('summary').value='更正后的结息摘要';
 await dialog.node('form').listeners.submit.at(-1)({preventDefault(){}});
 await manualClick;
 const request=posting.calls.find(call=>call.url==='/api/reconcile/statement-post');
 assert.equal(request.options.body.mode, 'manual');
 assert.equal(request.options.body.confirmed, true);
 assert.equal(request.options.body.business_account_code, '5602');
 assert.equal(request.options.body.bank_account_code, '1002');
 assert.equal(request.options.body.transaction.amount, '0.04');
 assert.equal(request.options.body.summary, '更正后的结息摘要');
 assert.match(posting.root.innerHTML, /已入账 <b>1<\/b>/);
 assert.match(posting.root.innerHTML, /记 2026-09-0009/);
 assert.match(posting.root.innerHTML, /data-rc-action="show-balance"/);
 const opened=[];
 bindReconcile(posting.root, async()=>({accounts:accounts()}), clients, detail=>opened.push(detail));
 await posting.root.onclick({target:{dataset:{rcAction:'show-balance'},closest(selector){return selector==='[data-rc-action]'?this:null;}}});
 assert.equal(opened.length, 1);
 assert.equal(opened[0].clientId, 4);
 assert.equal(opened[0].postings[0].voucher_id, 9);
 assert.equal(opened[0].postings[0].period, '2026-09');
 assert.deepEqual(opened[0].postings.map(item=>item.posted_lines.map(line=>line.account_code)), [['1002','5602']]);

 const historyRoot=fixtureHost();
 let historyHtml='';
 const baseQuery=historyRoot.querySelector.bind(historyRoot);
 historyRoot.querySelector=selector=>selector==='.rc-saved-panel'?{set outerHTML(value){historyHtml=value;}}:baseQuery(selector);
 const historyCalls=[];
 historyRoot.innerHTML=renderReconcile(clients,{client:clients[0]});
 bindReconcile(historyRoot, async url=>{
  historyCalls.push(url);
  if(String(url).startsWith('/api/reconcile/records'))return {records:[],postings:[{voucher_id:9,voucher_ref:'记 2026-09-0009',period:'2026-09',voucher_date:'2026-09-21',source_type:'bank_statement',summary:'收款 结息',transaction:{date:'2026-09-21',amount:'0.04',counterparty:'结息对方',note:'结息'},posted_lines:[{account_code:'1002',account_name:'银行存款',debit_fen:4,credit_fen:0},{account_code:'5603',account_name:'财务费用',debit_fen:0,credit_fen:4}]}]};
  if(String(url).startsWith('/api/books/'))return {accounts:accounts()};
  throw new Error('unexpected '+url);
 }, clients, null, {client:clients[0],loadHistory:true});
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.ok(historyCalls.some(url=>String(url).startsWith('/api/reconcile/records')));
 assert.match(historyHtml, /记 2026-09-0009/);
 assert.match(historyHtml, /结息对方/);
 assert.match(historyHtml, /0\.04/);
 assert.match(historyHtml, /data-rc-view-voucher="9"/);

 resetReconcile();
 const progressRoot=fixtureHost();
 const progressTx={date:'2026-09-21',amount:'0.04',counterparty:'结息',note:'结息',source:'ok.pdf'};
 progressRoot.innerHTML=renderReconcile(clients);
 bindReconcile(progressRoot, async url=>{
  if(url==='/api/reconcile/parse/start')return {job_id:'progress-job'};
  if(String(url).startsWith('/api/reconcile/parse/progress'))return {status:'completed',elapsed_seconds:1.2,files:[
   {name:'ok.pdf',kind:'statement',status:'completed',elapsed_seconds:0.4,recognized_count:2,pages:[]},
   {name:'mix.pdf',kind:'statement',status:'partial',elapsed_seconds:0.5,recognized_count:1,warnings:['易混字符'],pages:[]},
   {name:'bad.pdf',kind:'statement',status:'failed',elapsed_seconds:0.2,recognized_count:0,pages:[]}
  ],result:{account_name:clients[0].name,transactions:[progressTx],invoices:[],receipts:[],warnings:[]}};
  if(url==='/api/reconcile/match')return {matches:[{transaction_index:0,invoice_index:null,status:'review',reasons:['未找到唯一匹配发票']}],classification_transactions:[{status:'review',account_code:null,rule_suggestion:suggestion}],receipt_matches:{matches:[]},unmatched_invoices:[]};
  if(String(url).startsWith('/api/books/'))return {accounts:accounts()};
  throw new Error('unexpected '+url);
 }, clients);
 progressRoot.client.value=String(clients[0].id);
 await progressRoot.client.handlers.get('change')({target:progressRoot.client});
 const progressFile=new Blob(['synthetic input'],{type:'application/pdf'});progressFile.name='ok.pdf';
 progressRoot.files[0].files=[progressFile];
 await progressRoot.files[0].handlers.get('change')();
 await progressRoot.onclick({target:{dataset:{rcAction:'parse'},textContent:'',closest(selector){return selector==='[data-rc-action]'?this:null;}}});
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.match(progressRoot.innerHTML, /ok\.pdf<\/span><span>已处理 · 0\.4 秒 · 2 条/);
 assert.match(progressRoot.innerHTML, /mix\.pdf<\/span><span>部分识别，需核对 · 0\.5 秒 · 1 条/);
 assert.match(progressRoot.innerHTML, /bad\.pdf<\/span><span>识别失败 · 0\.2 秒 · 0 条/);
 assert.equal(alerts.length, 0, alerts.join('\n'));
 console.log('statement account confirmation, manual account and preview passed');
}finally{
 resetReconcile();
 if(previousReader===undefined)delete globalThis.FileReader;else globalThis.FileReader=previousReader;
 if(previousAlert===undefined)delete globalThis.alert;else globalThis.alert=previousAlert;
 if(globalThis.document)delete globalThis.document;
}
