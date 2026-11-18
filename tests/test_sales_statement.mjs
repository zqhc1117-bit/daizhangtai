import test from 'node:test';
import assert from 'node:assert/strict';
import {parseStatementRows,importStatement} from '../public/modules/tax-liquidation/statement-import.js';
import {buildUploadReport,statementSummary} from '../public/modules/tax-liquidation/upload-report-model.js';
import {salesBook} from '../public/sales-workspace.js';
import * as X from '../public/modules/tax-liquidation/vendor/xlsx.js';
const balance=[['资产负债表'],['编制单位：销售验收甲公司'],['2026年11月30日','单位：万元'],
  ['资产','行次','期末余额','年初余额','负债及权益','行次','期末余额','年初余额'],
  ['货币资金',1,8,90,'应付账款',1,2,80],['固定资产原价',2,99,99,'实收资本',2,5,30],
  ['固定资产净值',3,1.8,80,'未分配利润',3,2.8,80],['资产总计',4,9.8,180,'负债和所有者权益总计',4,9.8,180]];
const trial=[['科目余额表'],['编制单位：销售验收乙公司'],['单位：元'],
  ['科目编码','科目名称','期初余额','','本期发生额','','期末余额',''],['','','借方','贷方','借方','贷方','借方','贷方'],
  ['1001','库存现金',999,0,999,0,1000,0],['1002','银行存款',999,0,999,0,9000,0],['100201','银行存款_甲银行',999,0,999,0,9000,0],
  ['1601','固定资产',0,0,0,0,10000,0],['1602','累计折旧',0,0,0,0,0,2000],
  ['2202','应付账款',0,999,0,999,0,3000],['3001','实收资本',0,999,0,999,0,10000],['3104','利润分配',0,999,0,999,0,5000]];
test('side-by-side balance sheet uses ending columns, units and net assets',()=>{
  const result=parseStatementRows(balance);assert.equal(result.kind,'资产负债表');
  assert.equal(result.values.cash,'80000.00');assert.equal(result.values.fixedAssets,'18000.00');assert.equal(result.values.payables,'20000.00');
  assert.equal(result.sourceTotals.assetsTotal,'98000.00');assert.equal(result.metadata.companyName,'销售验收甲公司');assert.equal(result.metadata.baseDate,'2026-11-30');assert.equal(statementSummary(result).difference,0);
});
test('trial ending debit-credit pairs ignore opening/activity and child duplicate, subtract depreciation',()=>{
  const r=parseStatementRows(trial);assert.equal(r.kind,'科目余额表');assert.equal(r.values.cash,'10000.00');assert.equal(r.values.fixedAssets,'8000.00');assert.equal(r.values.payables,'3000.00');assert.equal(r.values.retainedEarnings,'5000.00');assert.equal(statementSummary(r).difference,0);
});
test('trial balance supports flat ending debit-credit and reverse direction',()=>{
  const r=parseStatementRows([['科目编码','科目名称','期初借方','期初贷方','期末借方余额','期末贷方余额'],['1002','银行存款',999,0,100,0],['2202','应付账款',0,999,30,0],['3001','实收资本',0,0,0,130]]);
  assert.equal(r.values.cash,'100.00');assert.equal(r.values.payables,'-30.00');assert.equal(statementSummary(r).difference,0);
});
test('single ending balance with direction and signed losses',()=>{
  const r=parseStatementRows([['科目名称','方向','期末余额'],['银行存款','借','1,000.00'],['实收资本','贷','1500.00'],['未分配利润','借','500.00']]);
  assert.equal(r.values.retainedEarnings,'-500.00');assert.equal(statementSummary(r).difference,0);
});
test('blank and unrecognized nonzero balances remain review issues',()=>{
  const r=parseStatementRows([['项目','期末余额'],['货币资金','100'],['存货',''],['应付账款','10'],['实收资本','90'],['无法识别的资产','50'],['资产总计','150']]);
  assert.equal(r.values.inventory,undefined);assert.match(r.warnings.join(' '),/未按零|未归入/);assert.equal(statementSummary(r).difference,5000);
  assert.throws(()=>parseStatementRows([['项目','期初金额'],['货币资金',100],['实收资本',100]]),/期末/);
});
test('equivalent balance sheet aliases never double-count and mixed companies are rejected',()=>{
  const r=parseStatementRows([['项目','期末余额'],['固定资产净值','100'],['固定资产账面价值','100'],['实收资本','100']]);
  assert.equal(r.values.fixedAssets,'100.00');
  assert.throws(()=>parseStatementRows([...balance,['编制单位：另一家公司'],...balance.slice(3)]),/多个不同企业/);
});
test('a company name containing 万元 is not an amount-unit caption',()=>{
  const r=parseStatementRows([['编制单位：万元商贸有限公司'],['单位：元'],['项目','期末余额'],['货币资金','100'],['实收资本','100']]);
  assert.equal(r.unit,1);assert.equal(r.values.cash,'100.00');
});
test('xlsx, legacy xls, UTF-8 CSV and TSV import the same ending balances',async()=>{
  for(const type of ['xlsx','xls','csv','txt']){
    const book=X.utils.book_new();X.utils.book_append_sheet(book,X.utils.aoa_to_sheet(balance),'资产负债表');
    const bytes=type==='txt'?X.utils.sheet_to_csv(book.Sheets['资产负债表'],{FS:'\t'}):X.write(book,{type:'array',bookType:type==='xls'?'biff8':type});
    const file=new File([bytes],`资产负债表.${type==='txt'?'tsv':type}`);
    const result=await importStatement(file);assert.equal(result.values.cash,'80000.00',type);
  }
});
test('automatic report contains taxpayer and provenance, never fabricates settlement or tax',()=>{
  const statement={...parseStatementRows(balance),fileName:'test.xlsx',sheetName:'资产负债表'};
  const report=buildUploadReport(statement,'general');const text=JSON.stringify(report);
  assert.match(text,/一般纳税人/);assert.match(text,/80,000.00/);assert.match(text,/test.xlsx/);assert.match(text,/税额待核实/);assert.equal(report.status,'待核实草稿');assert.match(text,/未将未提供的变现额视作零/);
  assert.match(JSON.stringify(buildUploadReport(statement,'small')),/小规模纳税人/);
});
test('a sales ledger opens without an existing company and retains a separate namespace',()=>{
  const book=salesBook({demo:true,user:{id:2},clients:[],period:'2026-11'},{clientId:'',period:'2026-11'});
  assert.equal(book.client.name,'');assert.equal(book.client.id,'sales-demo-2');assert.equal(book.accounts.length,5);
});
