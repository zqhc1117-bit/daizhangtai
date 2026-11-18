import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {draftAccounts,parseMoney,yuan,generateRows,summarize,balanceLastRow,rowAmount,draftKey,MAX_FEN,keywords} from '../public/ledger-draft-model.js';
import {createLedgerDraftXlsx,draftFilename} from '../public/ledger-draft-xlsx.js';
import {renderLedgerDraft,ledgerDraftTest} from '../public/ledger-draft.js';
import {renderBook,setBookView} from '../public/finance-ui.js';
import {expenseAliases,expenseItem,ledgerPresentation} from '../public/ledger-draft-expenses.js';
import {householdVariants,householdGroups} from '../public/ledger-draft-household.js';

const names=['其他应收款','其他应付款','库存商品','固定资产','管理费用'];
const book={client:{id:99,name:'合成测试公司',standard:'small2013',start_period:'2026-01'},period:'2026-11',periods:[],accounts:names.map((name,i)=>({name,code:['1221','2241','1405','1601','5602'][i],direction:i===1?'credit':'debit'}))};
const ctx={user:{id:17,name:'测试员'}};
const subjects=draftAccounts(book);
assert.deepEqual(subjects.map(a=>a.code),['1221','2241','1405','1601','5602']);
assert.equal(subjects[1].direction,'credit');
assert.equal(draftAccounts({...book,accounts:book.accounts.map(a=>a.name==='管理费用'?{...a,code:'6602'}:a)})[4].code,'6602','Actual table wins even when the standard differs.');
assert.equal(draftAccounts({...book,accounts:book.accounts.filter(a=>a.code!=='1405')})[2].code,'','Missing accounts are not fabricated.');
for(const invalid of ['','-1','1.234','NaN','Infinity','1e3','1,000','10000000000'])assert.throws(()=>parseMoney(invalid));
assert.equal(parseMoney('0.01'),1);assert.equal(parseMoney('9999999999.99'),MAX_FEN);
const make=(total,keywords,account=subjects[2])=>({total,keywords,rows:generateRows(total,keywords,account),reviewed:false});
for(const subject of subjects) {
  const draft=make('12345.67',subject.name.includes('应')?'押金、保证金':'办公设备、耗材',subject);
  assert.equal(summarize(draft).balanced,true);
  assert.equal(draft.rows.reduce((sum,r)=>sum+rowAmount(r),0),1234567);
  draft.rows.forEach(r=>assert.match(r.price,/^\d+\.\d{2}$/));
}
assert.throws(()=>make('0','耗材'),/大于 0/);
assert.throws(()=>make('0.01','a,b'),/无法识别商品关键词：a、b/);
assert.throws(()=>make('0.01','a,b',subjects[0]),/至少/);
assert.throws(()=>make('0.10','办公设备'),/金额过低/,'Never silently estimate a laptop at five cents.');
assert.throws(()=>make('0.01','实际项目'),/无法识别商品关键词：实际项目/);
assert.equal(summarize(make('0.01','实际项目',subjects[0])).total,1);
assert.equal(summarize(make('0.03','一,二,三',subjects[1])).balanced,true);
assert.equal(summarize(make('100000','办公设备')).balanced,true);
const last=make('100000','办公设备').rows.at(-1);
assert.equal(last.quantity,'14');assert.equal(last.price,'375.00');
assert.equal(make('10000','办公设备').rows.at(-1).price,'325.00');
for (const [words,minimum,expectedRows] of [['耗材',175,1],['日用品',225,1],['办公设备、耗材',17675,2]]) {
  assert.throws(()=>make(yuan(minimum-1),words),error=>error.message.includes(`估算至少 ${yuan(minimum)} 元`),'One cent below must show the exact attainable minimum.');
  const atMinimum=make(yuan(minimum),words);
  assert.equal(atMinimum.rows.length,expectedRows);
  assert.ok(atMinimum.rows.every(row=>row.quantity==='1'),'Adaptive minimum covers every keyword with one sensible item.');
  // Exercise the formerly failing gap and each cent around the .50 steps.
  for(let extra=0;extra<=1000;extra++) {
    const draft=make(yuan(minimum+extra),words);
    assert.equal(summarize(draft).balanced,true,`${words} at ${yuan(minimum+extra)}`);
    assert.ok(draft.rows.slice(0,-1).every(row=>parseMoney(row.price)%50===0),'Earlier item prices use exact .50 increments.');
    for(const word of words.split('、'))assert.ok(draft.rows.some(row=>row.category===word),'All requested categories remain covered.');
  }
}
for(const [words,fullMinimum,rows] of [['耗材','106.25',6],['日用品','22.75',5],['办公设备、耗材','4181.25',12]])assert.equal(make(fullMinimum,words).rows.length,rows,'Affordable broad categories retain the full template.');
for(const total of ['4582.21','4582.22'])assert.equal(summarize(make(total,'办公设备、耗材')).balanced,true,'The former combined-category gap is also reachable.');
for (let i=0;i<500;i++) {
  const fen=500000+(i*7919)%20000000;
  const d=make(yuan(fen),i%2?'办公设备':'耗材');
  assert.equal(summarize(d).balanced,true,`Exact generated sum ${fen}`);
  assert.ok(d.rows.length>=4&&d.rows.length<=8);
}
const many=make('1000000',['耗材','办公设备','日用品','食品','五金','服装','办公家具','厨房用具','炊具','餐具','清洁工具','办公文具','五金工具','化妆品','护肤品','电子产品','电脑配件','办公用品','休闲食品','纺织品'].join('、'));
assert.equal(summarize(many).balanced,true);
assert.ok(many.rows.length<=80);
let d=make('12345.67','办公设备');
d.rows[0].price='1.00';assert.equal(summarize(d).balanced,false);
d.rows=balanceLastRow(d);assert.equal(summarize(d).balanced,true);
d.rows[0].quantity='1.5';assert.match(summarize(d).errors[0],/数量/);
assert.throws(()=>createLedgerDraftXlsx({companyName:'测试',period:book.period,account:subjects[2],draft:d}));
const edit=make('12345.67','办公设备、耗材');
assert.equal(edit.rows.at(-1).quantity,'17');assert.equal(edit.rows.at(-1).price,'5.51');
edit.rows[0].price='4500.01';
const original=structuredClone(edit), balanced=balanceLastRow(edit);
assert.deepEqual(edit,original,'Balancing does not mutate the input draft.');
assert.deepEqual(balanced.slice(0,-1),original.rows.slice(0,-1),'Only the final row is adjusted.');
assert.equal(balanced.at(-1).quantity,'14');assert.equal(balanced.at(-1).price,'6.69');
assert.equal(summarize({...edit,rows:balanced}).balanced,true);
const divisible={total:'93.84',keywords:'封箱胶带',rows:[{category:'封箱胶带',name:'封箱胶带',spec:'48mm',unit:'卷',quantity:'17',price:'5.51'}]};
assert.equal(balanceLastRow(divisible)[0].quantity,'17','Existing quantity wins when the remainder is divisible.');
assert.equal(balanceLastRow(divisible)[0].price,'5.52');
const noReasonableQuantity={...divisible,total:'1.01',rows:[{...divisible.rows[0],quantity:'2',price:'0.10'}]};
const untouched=structuredClone(noReasonableQuantity);
assert.throws(()=>balanceLastRow(noReasonableQuantity),/未找到.*手工调整/,'No silent fallback to quantity one.');
assert.deepEqual(noReasonableQuantity,untouched,'A failed balance leaves the draft intact.');
const missing=make('48','锅、扫把、盆');missing.rows.splice(0,1);assert.match(summarize(missing).errors.join(''),/未覆盖关键词/);
assert.notEqual(draftKey(book,ctx),draftKey({...book,client:{...book.client,id:100}},ctx));
assert.notEqual(draftKey(book,ctx),draftKey(book,{user:{id:18}}));
assert.notEqual(draftKey(book,ctx),draftKey({...book,period:'2026-10'},ctx));
const storage=new Map();globalThis.localStorage={getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)};
const state=ledgerDraftTest.stateFor(book,ctx);Object.assign(state,make('12345.67','办公设备'));
ledgerDraftTest.saveState(book,ctx,state);ledgerDraftTest.states.clear();
assert.equal(summarize(ledgerDraftTest.stateFor(book,ctx)).balanced,true,'Draft survives a reload.');
assert.equal(ledgerDraftTest.stateFor({...book,client:{...book.client,id:100}},ctx).rows.length,0);
const html=renderLedgerDraft({...book,client:{...book.client,name:'<img src=x onerror=1>'}},ctx);
assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);assert.equal((html.match(/<option /g)||[]).length,5);
let calls=[];setBookView('ledger-draft');
const page=await renderBook(99,'2026-11',{...ctx,api:async path=>{calls.push(path);return book;}});
assert.doesNotMatch(page.match(/<nav class="cw-side-nav"[\s\S]*?<\/nav>/)?.[0]||'',/data-cw-view="ledger-draft"/,'Draft generation has moved to the sales workspace.');
assert.match(page,/data-cw-page="ledger-draft"/);
assert.match(page,/data-ledger-draft/);
assert.deepEqual(calls,['/api/books/99?period=2026-11']);
assert.equal(draftFilename('测试/公司',subjects[2],'12345.67'),'测试_公司-库存商品明细及总账(12345.67元).xlsx');

const cosmetics=make('1000.00','口红、粉饼、眼线笔');
assert.equal(cosmetics.rows.length,8);
assert.equal(summarize(cosmetics).total,100000);
for(const word of ['口红','粉饼','眼线笔']){
  const rows=cosmetics.rows.filter(row=>row.category===word);
  assert.ok(rows.length>=2&&rows.length<=3);
  assert.equal(new Set(rows.map(row=>row.spec)).size,rows.length);
  assert.ok(rows.every(row=>row.name!==word));
}
assert.deepEqual(new Set(cosmetics.rows.map(row=>row.unit)),new Set(['支','盒']));
assert.ok(new Set(cosmetics.rows.map(row=>row.price)).size>=4);
assert.deepEqual(make('1000.00','口红、粉饼、眼线笔'),cosmetics,'Generation is deterministic.');
assert.deepEqual(make('1000.00','口红、口红、粉饼、眼线笔').rows,cosmetics.rows,'Repeated keywords do not create duplicate categories.');
assert.throws(()=>make('62.49','口红、粉饼、眼线笔'),/估算至少 62.50 元/);
assert.equal(make('62.50','口红、粉饼、眼线笔').rows.length,3);
assert.equal(make('100.00','口红、粉饼、眼线笔').rows.length,4);
for(let fen=6250;fen<=23000;fen+=37){const draft=make(yuan(fen),'口红、粉饼、眼线笔');assert.equal(summarize(draft).balanced,true);assert.deepEqual(new Set(draft.rows.map(row=>row.category)),new Set(['口红','粉饼','眼线笔']));}
const fineWords=['口红','粉饼','眼线笔','粉底液','眼影','面膜','洁面乳','笔记本电脑','打印机','显示器','键盘','鼠标','办公桌椅','文件柜','办公费','差旅费','服务费','维修费'];
for(const word of fineWords){const draft=make('50000.13',word);assert.ok(draft.rows.length>=2&&draft.rows.length<=3,word);assert.equal(summarize(draft).balanced,true);assert.ok(draft.rows.every(row=>row.category===word&&row.spec&&row.unit!=='待核对'));}
const specified='笔记本电脑 32GB / 1TB SSD / 16英寸';
assert.deepEqual(keywords(specified),[specified],'Specification slashes are preserved.');
assert.deepEqual(keywords('办公设备/耗材'),['办公设备','耗材'],'Known category slash separators still work.');
const configured=make('6800.00',specified,subjects[3]);
assert.equal(configured.rows.length,1);assert.equal(configured.rows[0].name,specified);assert.ok(configured.rows[0].spec.includes(specified));assert.equal(configured.rows[0].unit,'台');assert.match(configured.rows[0].spec,/使用部门/);
const shade='哑光口红 3.5g 用户给定色号999';
assert.ok(make('450.00',shade).rows.every(row=>row.name===shade&&row.spec.includes(shade)),'Explicit styles and codes are not replaced by invented variants.');
for(const account of [subjects[2],subjects[3]]){
  for(const word of ['未知专用仪器','口红、未知专用仪器'])assert.throws(()=>make('1000',word,account),/无法识别商品关键词：未知专用仪器.*添加明细.*原草稿已保留/);
  assert.throws(()=>make('1000','未知甲、未知乙',account),/未知甲、未知乙/);
}
assert.throws(()=>make('100.00','口红收纳盒'),/无法识别/,'An accessory is not silently treated as the product itself.');
assert.equal(make('100.00','打印机硒鼓').rows[0].unit,'个','A specified consumable is not converted into a printer.');
const expenses=make('10000.00','办公费、差旅费、服务费、维修费',subjects[4]);
assert.equal(summarize(expenses).balanced,true);assert.ok(expenses.rows.every(row=>row.unit==='项'&&row.spec.includes('用途')&&row.spec.includes('所属期间待核对')));
assert.equal(expenses.rows.length,4,'Explicit expense items do not expand unrelated business.');
const expenseAccount=subjects[4], enterpriseExpense={...expenseAccount,code:'6602'};
const salary=make('1000.00','工资',expenseAccount);
for(const account of [expenseAccount,enterpriseExpense])for(const total of ['0.01','1000.00','9999999999.99']){
  const draft=make(total,'工资',account), row=draft.rows[0];
  assert.equal(draft.rows.length,1);assert.equal(row.name,'工资薪金（职工薪酬，归属待核对）');assert.equal(row.category,'工资');
  assert.equal(row.quantity,'1');assert.equal(row.unit,'项');assert.equal(row.price,total);
  assert.doesNotMatch(row.spec,/未识别|规格|单价/);assert.match(row.spec,/工资表及计提依据待核对/);
  assert.equal(summarize(draft,account).balanced,true);
}
for(const alias of expenseAliases){
  const draft=make('1000.13',alias,expenseAccount);
  assert.equal(draft.rows.length,1,alias);assert.equal(draft.rows[0].category,alias);
  if(draft.rows[0].spec.includes('工资表及计提依据'))assert.match(draft.rows[0].name,/职工薪酬/);
  else assert.equal(draft.rows[0].name,alias);
  assert.equal(expenseItem(alias).recognized,true,alias);assert.equal(expenseItem(alias).totalOnly,true);
  assert.doesNotMatch(draft.rows[0].spec,/未识别|商品|规格|单价/);assert.equal(draft.rows[0].quantity,'1');
}
const payroll=make('1000.00','工资、社保、公积金',expenseAccount);
assert.equal(payroll.rows.length,3);assert.equal(summarize(payroll).balanced,true);
assert.deepEqual(payroll.rows.map(r=>r.price),['333.34','333.33','333.33'],'Draft allocation has no invented payroll ratios or reference prices.');
assert.ok(payroll.rows.every(r=>r.quantity==='1'&&r.unit==='项'&&!r.spec.includes('未识别')));
assert.ok(payroll.rows.slice(1).every(r=>r.spec.includes('单位承担口径')&&r.spec.includes('个人代扣部分请另行核对')));
assert.deepEqual(keywords('工资/社保/公积金'),['工资','社保','公积金']);
assert.equal(make('0.03','工资、社保、公积金',expenseAccount).rows.length,3);
assert.throws(()=>make('0.02','工资、社保、公积金',expenseAccount),/至少/);
const mixedExpense=make('1000.01','工资、办公费',expenseAccount);
assert.deepEqual(mixedExpense.rows.map(r=>r.price),['500.01','500.00'],'A fictional office price must not outweigh payroll.');
const electric=make('1000','电费',expenseAccount);
assert.equal(electric.rows.length,1);assert.doesNotMatch(JSON.stringify(electric.rows),/水/);
assert.equal(make('1000','职工薪酬',expenseAccount).rows.length,1);
assert.match(make('1000','职工薪酬',expenseAccount).rows[0].spec,/薪酬项目构成.*不自动拆分/);
assert.match(make('1000','餐费',expenseAccount).rows[0].spec,/用餐用途.*不预判/);
for(const word of ['研发费用','研究费用','研究费','租赁费'])assert.match(make('1000',word,expenseAccount).rows[0].spec,/费用化.*待核对/);
const specifiedExpense='行政部 2026年11月电费（用户已给期间）';
const specifiedSalary='研发部门 2026年11月工资';
for(const word of [specifiedExpense,specifiedSalary]){
  const draft=make('1000',word,expenseAccount);assert.equal(draft.rows[0].name,word);assert.equal(draft.rows.length,1);
  assert.doesNotMatch(draft.rows[0].spec,/未识别/);
}
const unknownExpense=make('123.45','未说明业务支出',expenseAccount);
assert.match(unknownExpense.rows[0].spec,/未识别费用.*费用用途、归属及实际金额/);
assert.doesNotMatch(unknownExpense.rows[0].spec,/规格|商品|单价/);
const expenseInvalid={...salary,accountName:'管理费用',rows:[{...salary.rows[0],spec:''}]};
assert.match(summarize(expenseInvalid).errors[0],/费用类别.*用途归属期间/);
expenseInvalid.rows[0]={...salary.rows[0],price:'0.00'};assert.match(summarize(expenseInvalid).errors[0],/单项金额/);
const expenseEdited={...payroll,accountName:'管理费用',rows:structuredClone(payroll.rows)};
expenseEdited.rows[0].price='333.35';assert.equal(summarize(expenseEdited).balanced,false);
assert.throws(()=>createLedgerDraftXlsx({companyName:book.client.name,period:book.period,account:expenseAccount,draft:expenseEdited}));
expenseEdited.rows=balanceLastRow(expenseEdited);assert.equal(expenseEdited.rows.at(-1).quantity,'1');
assert.equal(summarize(expenseEdited).balanced,true);

// Upgrading presentation must never silently rewrite previously saved work.
const legacySalary={...salary,accountName:'管理费用',rows:[{...salary.rows[0],name:'人工核对工资',spec:'旧未识别规格；人工备注保留',price:'1000.00'}]};
Object.assign(ledgerDraftTest.stateFor(book,ctx),legacySalary);
ledgerDraftTest.saveState(book,ctx,ledgerDraftTest.stateFor(book,ctx));ledgerDraftTest.states.clear();
assert.deepEqual(ledgerDraftTest.stateFor(book,ctx).rows,legacySalary.rows);
const expenseState=ledgerDraftTest.stateFor(book,ctx);
renderLedgerDraft(book,ctx);assert.deepEqual(expenseState.rows,legacySalary.rows,'Rendering does not regenerate a legacy draft.');
expenseState.rows=generateRows(expenseState.total,expenseState.keywords,expenseAccount);
assert.deepEqual(expenseState.rows,salary.rows,'Explicit regeneration uses recognized salary semantics.');
const expenseHtml=renderLedgerDraft(book,ctx);
assert.match(expenseHtml,/费用发生清单（待核对拟稿）/);assert.match(expenseHtml,/用途、归属及期间说明/);
assert.match(expenseHtml,/费用汇总口径/);assert.match(expenseHtml,/费用汇总金额/);
assert.match(expenseHtml,/1 项代表一项费用，不代表员工人数/);
assert.match(expenseHtml,/aria-label="第 1 行单项金额"/);
assert.doesNotMatch(expenseHtml,/结存|总账余额|规格型号|单价|口红|商品\/项目名称/);
const retained=structuredClone(expenseState.rows);
expenseState.accountName='库存商品';assert.match(renderLedgerDraft(book,ctx),/结存清单/);
assert.deepEqual(expenseState.rows,retained,'Changing account changes presentation only.');
expenseState.accountName='管理费用';assert.match(renderLedgerDraft(book,ctx),/费用发生清单/);
assert.deepEqual(expenseState.rows,retained);
ledgerDraftTest.saveState(book,ctx,expenseState);ledgerDraftTest.states.clear();
assert.deepEqual(ledgerDraftTest.stateFor(book,ctx).rows,salary.rows);
assert.equal(ledgerDraftTest.stateFor({...book,period:'2026-10'},ctx).rows.length,0);
assert.equal(ledgerDraftTest.stateFor(book,{user:{id:18}}).rows.length,0);
assert.equal(ledgerPresentation(enterpriseExpense).sheetName,'费用发生清单');
assert.equal(ledgerPresentation(expenseAccount).summary('工资'),'管理费用汇总（工资）');

const household=make('1000.00','锅、扫把、盆');
assert.equal(household.rows.length,8);assert.equal(summarize(household).total,100000);
assert.deepEqual(household.rows.map(r=>[r.name,r.unit,r.quantity,r.price]),[
  ['铁制炒锅','口','2','128.00'],['不锈钢汤锅','口','3','89.00'],['平底煎锅','口','3','69.00'],
  ['软毛扫把','把','3','15.00'],['塑料扫把','把','3','22.00'],['竹制笤帚','把','3','18.00'],
  ['塑料洗衣盆','个','3','18.00'],['塑料洗脸盆','个','4','12.75'],
]);
assert.deepEqual(make('1000.00','锅、扫把、盆'),household,'Daily products are deterministic, not randomized prices.');
assert.deepEqual(make('1000.00','锅、扫帚、盆').rows.map(r=>({...r,category:r.category==='扫帚'?'扫把':r.category})),household.rows);
assert.deepEqual(make('1000.00','锅、扫把、锅、盆').rows,household.rows);
assert.throws(()=>make('47.99','锅、扫把、盆'),/估算至少 48.00 元/);
assert.throws(()=>make('0.01','锅、扫把、盆'),/估算至少 48.00 元/);
assert.equal(make('48.00','锅、扫把、盆').rows.length,3);
for(const fen of [...Array.from({length:101},(_,i)=>4800+i),6000,10000,100001,1000013]){
  const draft=make(yuan(fen),'锅、扫把、盆');
  assert.equal(summarize(draft).balanced,true);
  assert.deepEqual(new Set(draft.rows.map(r=>r.category)),new Set(['锅','扫把','盆']));
}
for(const [aliases,variants] of householdVariants){
  const draft=make('50000.13',aliases[0]);
  assert.ok(draft.rows.length>=2&&draft.rows.length<=3,aliases[0]);assert.equal(summarize(draft).balanced,true);
  for(const row of draft.rows){
    const reference=variants.find(v=>v[0]===row.name);
    assert.ok(reference,`${aliases[0]} keeps its concrete family`);assert.equal(row.unit,reference[2]);
    assert.ok(parseMoney(row.price)>=reference[3]/2&&parseMoney(row.price)<=reference[3]*2);
    assert.ok(Number.isInteger(Number(row.quantity))&&Number(row.quantity)>0);
  }
}
for(const [aliases] of householdGroups){
  const draft=make('10000.13',aliases[0]);assert.ok(draft.rows.length>=4&&draft.rows.length<=8,aliases[0]);assert.equal(summarize(draft).balanced,true);
}
for(const word of ['汤锅','炒锅','煎锅'])assert.ok(make('1000',word).rows.every(row=>row.name.includes(word)),'A concrete pan does not expand other kinds of pan.');
for(const word of ['盆栽','花盆','电饭锅','锅炉','扫把头','拖把头','杯架','笔芯','锅盖把手','笔记本电脑包','保鲜盒盖'])assert.throws(()=>make('1000',word),/无法识别/,word);
for(const [word,unit] of [['不锈钢盆30cm','个'],['汤锅 24cm','口'],['锅盖 32cm','个'],['笔记本 A5 60页','本'],['笔记本电脑 32GB / 1TB','台'],['打印机专用墨盒 型号待核对','个']]){
  const draft=make('6800',word);assert.ok(draft.rows.every(row=>row.name===word&&row.spec.includes(word)&&row.unit===unit),word);
}
assert.ok(make('100','锅盖').rows.every(row=>row.unit==='个'&&!row.name.includes('汤锅')));
assert.ok(make('100','笔记本').rows.every(row=>row.unit==='本'&&row.spec.includes('纸本')));
const legacyProduct={...household,rows:[{category:'锅、扫把、盆',name:'原稿人工备注',spec:'旧未识别商品',unit:'待核对',quantity:'1',price:'1000.00'}],accountName:'库存商品'};
Object.assign(ledgerDraftTest.stateFor(book,ctx),legacyProduct);
ledgerDraftTest.saveState(book,ctx,ledgerDraftTest.stateFor(book,ctx));ledgerDraftTest.states.clear();
const productState=ledgerDraftTest.stateFor(book,ctx), oldRows=structuredClone(productState.rows);
const productHtml=renderLedgerDraft(book,ctx);assert.deepEqual(productState.rows,oldRows);
assert.match(productHtml,/锅、扫把、盆/);assert.match(productHtml,/参考单价.*整数数量/);
assert.doesNotMatch(ledgerPresentation(subjects[2]).unknownHint,/预算分配|均分/);
assert.throws(()=>{productState.rows=generateRows('1000','锅、未知仪器',subjects[2]);},/未生成新拟稿/);
assert.deepEqual(productState.rows,oldRows,'Failed generation leaves existing editable rows untouched.');
productState.rows=generateRows(productState.total,productState.keywords,subjects[2]);assert.deepEqual(productState.rows,household.rows);
// Unknown goods can still be entered manually using real figures.
assert.equal(summarize({total:'123.45',keywords:'专用零件',rows:[{category:'专用零件',name:'专用零件',spec:'用户填写规格',unit:'个',quantity:'3',price:'41.15'}]}).balanced,true);
for(const subject of subjects.slice(0,2)){
  const word='甲公司/乙项目押金',draft=make('1000.00',word,subject);
  assert.deepEqual(keywords(word),[word]);assert.equal(draft.rows.length,1);assert.equal(draft.rows[0].name,word);assert.equal(draft.rows[0].quantity,'1');assert.equal(draft.rows[0].unit,'笔');assert.match(draft.rows[0].spec,/按输入性质及对象/);
  assert.match(ledgerPresentation(subject).notice,/往来金额仅作预算分配/);
  assert.doesNotMatch(ledgerPresentation(subject).hint,/未知商品停止|款式参考单价/);
}

const emitIndex=process.argv.indexOf('--emit-dir');
if(emitIndex>=0){
  const dir=process.argv[emitIndex+1];await mkdir(dir,{recursive:true});
  const fixtures=[];
  for(const account of subjects){
    const draft=make('12345.67',account.name.includes('应')?'押金、保证金':'办公设备、耗材',account);
    draft.rows[0].name='=HYPERLINK("https://example.invalid","literal")';
    draft.rows[0].spec='<型号 & 测试>';
    const file=`${account.code}.xlsx`;
    await writeFile(join(dir,file),createLedgerDraftXlsx({companyName:book.client.name,period:book.period,account,draft}));
    fixtures.push({file,account,draft,totals:summarize(draft)});
  }
  await writeFile(join(dir,'cosmetics-1000.xlsx'),createLedgerDraftXlsx({companyName:book.client.name,period:book.period,account:subjects[2],draft:cosmetics}));
  fixtures.push({file:'cosmetics-1000.xlsx',account:subjects[2],draft:cosmetics,totals:summarize(cosmetics),kind:'cosmetics'});
  await writeFile(join(dir,'household-1000.xlsx'),createLedgerDraftXlsx({companyName:book.client.name,period:book.period,account:subjects[2],draft:household}));
  fixtures.push({file:'household-1000.xlsx',account:subjects[2],draft:household,totals:summarize(household),kind:'household'});
  for(const account of [expenseAccount,enterpriseExpense])for(const [kind,draft] of [['salary',salary],['payroll',payroll],['unknown-expense',unknownExpense]]){
    const file=`${account.code}-${kind}.xlsx`;
    await writeFile(join(dir,file),createLedgerDraftXlsx({companyName:book.client.name,period:book.period,account,draft}));
    fixtures.push({file,account,draft,totals:summarize(draft,account),kind});
  }
  await writeFile(join(dir,'fixtures.json'),JSON.stringify(fixtures));
}
console.log('Ledger drafts: concrete variants, adaptive budgets, explicit specifications, unknowns, five subjects, 500 allocations, exact fen, tail balancing, persistence, scope isolation and safe text passed.');
