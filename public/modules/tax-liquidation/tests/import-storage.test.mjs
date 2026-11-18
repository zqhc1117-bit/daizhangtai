import {test} from 'node:test';
import assert from 'node:assert/strict';
import {matchBalanceRows,excelAmount} from '../full/excel.js';
import {extractCompany} from '../shared/extract-company.js';
import {createDraft,normalizeDraft} from '../schema.js';
import {loadDraft,saveDraft,draftKey} from '../storage.js';
import {availableModes,resolveMode} from '../modes.config.js';
test('extracts multi-line and inline registry text, including capital unit conversion',()=>{
  const input='企业名称：示例科技有限公司 统一社会信用代码：91310000MA1K12345X\n法定代表人：李明\n成立日期：2020年6月8日\n注册资本：12.3456万元人民币';
  assert.deepEqual(extractCompany(input),{name:'示例科技有限公司',creditCode:'91310000MA1K12345X',legalPerson:'李明',established:'2020-06-08',registeredCapital:'123456.00'});
  assert.equal(extractCompany('注册资本：1.005亿元').registeredCapital,'100500000.00');
  assert.equal(extractCompany('无关文字').name,undefined);
});
test('side-by-side report selects ending values, skips line numbers and converts ten-thousands',()=>{
  const rows=[['单位：人民币（万元）'],['资产','行次','期末余额','年初余额','负债和所有者权益','行次','期末数','年初数'],['货币资金',1,1.234567,999,'应付账款',30,2,888],['固定资产原价',10,20,9,'应付职工薪酬',31,0.5,2],['固定资产净值',11,1.1,9,'实收资本（或股本）',32,0.834567,5],['资产总计',99,0,9,'未分配利润',33,-1,9]];
  const result=matchBalanceRows(rows);
  assert.equal(result.values.cash,'12345.67');assert.equal(result.values.payables,'20000.00');
  assert.equal(result.values.fixedAssets,'11000.00');assert.equal(result.values.retainedEarnings,'-10000.00');
  assert.equal(result.values.employeePayable,'5000.00');assert.equal(result.values.paidCapital,'8345.67');
  assert.equal(result.matches.length,6);
});
test('Excel rounding handles signed decimal and dash values; missing headers never guess a column',()=>{
  assert.equal(excelAmount('(1,234.565)'),'-1234.57');assert.equal(excelAmount('—'),'0.00');
  assert.equal(excelAmount(0.000001,10000),'0.01');assert.equal(excelAmount('#VALUE!'),null);
  assert.deepEqual(matchBalanceRows([['货币资金',1,999]]).values,{});
  assert.equal(matchBalanceRows([['项目','期末余额'],['货币资金','']]).matches.length,0);
});
test('local drafts persist independently by company and survive a failed save in memory',()=>{
  const data=new Map(),storage={getItem:key=>data.get(key),setItem:(key,value)=>data.set(key,value)};
  const a={id:9911,code:'A',name:'A'},b={id:9912,code:'B',name:'B'};
  const sa=loadDraft(a,{},storage),sb=loadDraft(b,{},storage);sa.draft.quick.cash='123.45';
  assert.equal(saveDraft(a,{},sa,storage),true);assert.equal(sb.draft.quick.cash,'');
  assert.equal(JSON.parse(storage.getItem(draftKey(a))).quick.cash,'123.45');
  assert.equal(saveDraft(a,{},sa,{setItem(){throw new Error('quota');}}),false);
  assert.match(sa.error,/本地保存失败/);assert.equal(loadDraft(a,{},storage).draft.quick.cash,'123.45');
});
test('blocked localStorage getter and corrupt JSON produce a recoverable warning',()=>{
  Object.defineProperty(globalThis,'localStorage',{configurable:true,get(){throw new Error('blocked');}});
  const blocked=loadDraft({id:9920,name:'blocked'});assert.match(blocked.error,/读取失败/);
  assert.equal(saveDraft({id:9920},{},blocked),false);
  delete globalThis.localStorage;
  assert.match(loadDraft({id:9921},{},{getItem:()=>'{broken'}).error,/读取失败/);
});
test('mode configurations lock automatically, and persisted input has a bounded whitelist',()=>{
  assert.deepEqual(availableModes(['full','full','bad']),['full']);
  assert.equal(resolveMode('quick',['full']),'full');assert.equal(resolveMode('full',['quick']),'quick');
  assert.throws(()=>resolveMode('quick',[]));
  const draft=createDraft();draft.company.injected='<script>';draft.company.name='ok';
  const normalized=normalizeDraft(draft);assert.equal(normalized.company.name,'ok');assert.equal(normalized.company.injected,undefined);
});
