import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createDraft} from '../schema.js';
import {sampleDraft} from '../demo.js';
import {calculate} from '../calculate.js';
import {toFen,yuan,money,allocate,taxAt} from '../money.js';
import {buildReport} from '../report-model.js';
import {renderReport} from '../report.js';
const client={id:1,name:'测试公司'};
test('decimal input, half-up tax rounding and allocation conserve every cent',()=>{
  assert.equal(toFen('0.10')+toFen('0.20'),toFen('0.30'));
  assert.equal(toFen('-1,234.56'),-123456);
  assert.equal(money(123456789),'1,234,567.89');
  assert.equal(yuan(-1),'-0.01');
  assert.throws(()=>toFen('1.001'));
  assert.throws(()=>toFen('1e8'));
  assert.equal(taxAt(2,25),1);
  assert.deepEqual(allocate(101,[{ratio:'33.333333'},{ratio:'33.333333'},{ratio:'33.333334'}]),[34,33,34]);
  assert.deepEqual(allocate(1,[{ratio:'0'},{ratio:'50'},{ratio:'50'}]),[0,1,0]);
});
test('quick example balances, pays existing taxes and exports all six sections',()=>{
  const draft=sampleDraft('quick',client), t=calculate(draft), report=buildReport(draft);
  assert.equal(t.balanceDifference,0);
  assert.equal(t.cashPool,3200000);
  assert.equal(t.taxTotal,235000);
  assert.equal(t.distributable,2795000);
  assert.deepEqual(t.distributions.map(row=>row.received),[1677000,1118000]);
  assert.equal(t.incomeTax,0);
  assert.equal(report.sections.length,6);
  assert.match(renderReport(report),/示例数据/);
});
test('full example includes recoveries, debt forgiveness, old payroll and new compensation once',()=>{
  const draft=sampleDraft('full',client), t=calculate(draft);
  assert.equal(t.balanceDifference,0);
  assert.equal(t.disposalGain,2100000);
  assert.equal(t.debtIncome,400000);
  assert.equal(t.taxableIncome,1810000);
  assert.equal(t.incomeTax,452500);
  assert.equal(t.priorities[0].due,170000);
  assert.equal(t.priorities[1].due,800000);
  assert.equal(t.priorities[2].due,972500);
  assert.equal(t.priorities[3].due,2600000);
  assert.equal(t.remaining,14557500);
  assert.equal(t.distributions.reduce((n,row)=>n+row.received,0),t.distributable);
  assert.equal(t.distributions[0].personalTaxHint,true);
  assert.equal(t.distributions[1].personalTaxHint,false);
});
test('each priority can trigger insolvency and blocks shareholder distribution',()=>{
  for(const [field,value] of [['agencyFee','40000'],['employeeExtra','40000'],['vat','40000']]) {
    const draft=sampleDraft('quick',client);draft.settlement[field]=value;
    const t=calculate(draft);assert.equal(t.insolvent,true,field);assert.equal(t.distributable,0);
    assert.equal(t.distributions.reduce((n,row)=>n+row.received,0),0);
  }
  const draft=createDraft(client);draft.mode='full';Object.assign(draft.full.balance,{cash:'1',payables:'2',retainedEarnings:'-1'});
  assert.equal(calculate(draft).priorities[3].shortfall,100);
  assert.equal(calculate(draft).insolvent,true);
});
test('pending assets, unilateral debt assumption, invalid shares and money block distributions',()=>{
  const draft=sampleDraft('full',client);draft.full.assets.inventory.realized='';
  assert.equal(calculate(draft).distributable,0);
  draft.full.assets.inventory.realized='42000';draft.full.debts.otherPayables.method='assumed';
  let t=calculate(draft);assert.equal(t.debtIncome,0);assert.equal(t.externalPaid,2000000);assert.equal(t.distributable,0);
  Object.assign(draft.full.debts.otherPayables,{assignee:'张明',consent:true});
  assert.ok(calculate(draft).distributable>0);
  draft.shareholders[0].ratio='70';assert.equal(calculate(draft).distributable,0);
  draft.shareholders[0].ratio='60';draft.full.balance.cash='0.001';assert.ok(calculate(draft).numberIssues.length);assert.equal(calculate(draft).distributable,0);
});
test('partial waiver cannot exceed debt and cannot be treated as forgiven without written basis',()=>{
  const draft=sampleDraft('full',client);
  draft.full.debts.otherPayables.paid='10000.01';assert.equal(calculate(draft).distributable,0);
  draft.full.debts.otherPayables.paid='5000';draft.full.debts.otherPayables.note='';assert.equal(calculate(draft).distributable,0);
});
test('manual income tax and unchecked tax states stay visible in report conclusions',()=>{
  const draft=sampleDraft('full',client);draft.sample=false;
  draft.settlement.taxRate='manual';draft.settlement.manualTax='100.01';draft.settlement.taxes.personal=false;
  const t=calculate(draft);assert.equal(t.incomeTax,10001);assert.equal(t.ready,false);
  assert.match(JSON.stringify(buildReport(draft)),/个人所得税尚未勾选结清/);
  draft.settlement.taxes.personal=true;assert.equal(calculate(draft).ready,true);
  draft.settlement.taxRate='5';assert.equal(calculate(draft).ready,false);
});
test('quick and full calculation state remains independent when switching modes',()=>{
  const draft=sampleDraft('full',client), full=calculate(draft);
  draft.mode='quick';draft.quick.cash='0.30';draft.quick.paidCapital='0.10';draft.quick.retainedEarnings='0.20';draft.quick.hasTax=false;
  assert.equal(calculate(draft).balanceDifference,0);
  draft.mode='full';assert.equal(calculate(draft).remaining,full.remaining);
});
test('report escapes company text and does not declare completion from unchecked data',()=>{
  const draft=sampleDraft('quick',client);draft.company.name='<img src=x onerror=alert(1)>';
  const html=renderReport(buildReport(draft));assert.ok(!html.includes('<img'));assert.match(html,/&lt;img/);
  draft.company.endDate='2020-01-01';assert.equal(calculate(draft).ready,false);
});
