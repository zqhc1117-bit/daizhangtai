import assert from 'node:assert/strict';
import {createVoucherDraft,voucherPayload,renderVoucherEditor,chineseMoney,parseVoucherMoney,moneyDigits} from '../public/voucher-editor.js';
import {renderBook,setBookView} from '../public/finance-ui.js';
import {searchableAccounts} from '../public/account-picker.js';
import {renderPayrollPage,payrollSlipMarkup,payrollExportRows,adjacentPayrollPeriod} from '../public/payroll.js';

const book={client:{id:88771,name:'界面<测试>',start_period:'2026-01'},period:'2026-11',periods:[],vouchers:[],payroll:[],accounts:[
  {code:'1002',name:'银行存款',category:'asset',report_key:'cash'},
  {code:'5602',name:'管理费用',category:'expense',report_key:'administration'},
  {code:'560201',name:'办公费',category:'expense',report_key:'administration'},
  {code:'2211',name:'应付职工薪酬',category:'liability'},
]};
const draft=createVoucherDraft(book);
assert.equal(draft.lines.length,5);
assert.equal(draft.clientId,book.client.id);
assert.throws(()=>voucherPayload(draft,book),/摘要/);
draft.summary='日常办公';draft.lines[0]={summary:'购买办公用品',account_code:'560201',debit:'1234.56',credit:'',cashflow_category:'unclassified'};
draft.lines[1]={summary:'',account_code:'1002',debit:'',credit:'1234.56',cashflow_category:'operating'};
const payload=voucherPayload(draft,book);
assert.equal(payload.lines.length,2);
assert.equal(payload.lines[0].debit_fen,123456);assert.equal(payload.lines[1].credit_fen,123456);
assert.equal(payload.lines[1].summary,'日常办公');
draft.date='2027-01-05';assert.equal(voucherPayload(draft,book).date,'2027-01-05');
draft.lines[1].credit='1234.55';assert.throws(()=>voucherPayload(draft,book),/不平衡/);draft.lines[1].credit='1234.56';
draft.lines[0].account_code='5602';assert.throws(()=>voucherPayload(draft,book),/末级科目/);draft.lines[0].account_code='560201';
draft.lines[2].summary='漏填科目';assert.throws(()=>voucherPayload(draft,book),/第 3 行/);draft.lines[2].summary='';
draft.attachment_count=1.5;assert.throws(()=>voucherPayload(draft,book),/整数/);draft.attachment_count=2;
const original={id:10,date:'2026-11-17',period:'2026-11',number:5,version:2,summary:'原摘要',source_type:'manual',attachments:[{id:1,name:'原件.pdf',size:20}],lines:payload.lines};
const edit=createVoucherDraft(book,original);edit.date='2026-12-01';assert.throws(()=>voucherPayload(edit,book),/原月份/);
edit.date='2026-11-18';assert.equal(voucherPayload(edit,book).version,2);
edit.lines[0].summary=original.summary;edit.lines[1].summary='独立分录摘要';edit.summary='修改后的总摘要';
assert.equal(voucherPayload(edit,book).lines[0].summary,'修改后的总摘要');
assert.equal(voucherPayload(edit,book).lines[1].summary,'独立分录摘要');
const reconcile=createVoucherDraft(book,{...original,source_type:'bank_statement'});reconcile.date='2026-11-18';assert.throws(()=>voucherPayload(reconcile,book),/原入账日期/);
assert.equal(edit.files[0].id,1);assert.equal(edit.lines.length,5);

const html=renderVoucherEditor(book,draft,{user:{name:'测试员'}});
assert.match(html,/ve-page/);assert.match(html,/会计科目/);assert.match(html,/输入科目编码或名称搜索/);
for(const action of ['save','save-new','save-print','add','templates','save-template','preview','attach','settings','balance'])assert.match(html,new RegExp(`data-ve-action="${action}"`));
assert.equal((html.match(/class="voucher-line"/g)||[]).length,5);
assert.doesNotMatch(html,/app-modal-form|role="dialog"/,'Voucher entry is a page rather than a dialog.');
const subjects=searchableAccounts(book.accounts);
assert.equal(subjects.find(a=>a.code==='5602').leaf,false);
assert.match(subjects.find(a=>a.code==='560201').search,/管理费用.*办公费/);

for(const [fen,label]of [[0,'零元整'],[1,'零元零壹分'],[100,'壹元整'],[101,'壹元零壹分'],[123456,'壹仟贰佰叁拾肆元伍角陆分'],[100010000,'壹佰万零壹佰元整'],[100000000000,'壹拾亿元整']])assert.equal(chineseMoney(fen),label);
assert.equal(parseVoucherMoney('0.01'),1);assert.equal(parseVoucherMoney(''),0);
for(const value of ['--1','1.001','1,000','NaN','99999999999999999','-99999999999999999'])assert.throws(()=>parseVoucherMoney(value));
assert.equal(parseVoucherMoney('-0.01'),-1);
assert.equal(parseVoucherMoney('-123.45'),-12345);
assert.match(chineseMoney(-12345),/^负/);
assert.match(moneyDigits(-12345),/-123.45/);
const redDraft=createVoucherDraft(book);
redDraft.summary='红字冲销';
redDraft.lines[0]={summary:'红字冲销',account_code:'560201',debit:'-123.45',credit:'',cashflow_category:'unclassified'};
redDraft.lines[1]={summary:'红字冲销',account_code:'1002',debit:'',credit:'-123.45',cashflow_category:'operating'};
assert.equal(voucherPayload(redDraft,book).lines[0].debit_fen,-12345);
assert.match(moneyDigits(900000000000000),/9,000,000,000,000.00/,'Large permitted amounts must not lose leading digits.');

const person={id:1,period:'2026-11',employee_name:'测试<员工>',employee_code:'E01',department:'行政',position:'助理',gross_fen:800000,withheld_tax_fen:20000,social_fen:80000,other_deduction_fen:1500,net_fen:698500,expense_account:'560201',note:'<script>alert(1)</script>'};
const wages={...book,payroll:[person]};
const payrollHtml=renderPayrollPage(wages),slip=payrollSlipMarkup([person],wages);
assert.match(payrollHtml,/carry-payroll/);assert.match(payrollHtml,/edit-payroll/);assert.match(payrollHtml,/view-payslip/);
assert.match(slip,/6,985.00/);assert.match(slip,/测试&lt;员工&gt;/);assert.doesNotMatch(slip,/<script>|测试<员工>/);
const exportRows=payrollExportRows(wages);assert.equal(exportRows[1][0],'2026年11月');assert.equal(exportRows[4].at(-1),'6985.00');assert.equal(exportRows.at(-1).at(-1),'6985.00');
assert.equal(adjacentPayrollPeriod('2026-12',1),'2027-01');assert.equal(adjacentPayrollPeriod('2027-01',-1),'2026-12');

globalThis.document={querySelector:()=>null};
setBookView('voucher-editor');const page=await renderBook(book.client.id,book.period,{api:async()=>book,clients:[book.client]});
assert.match(page,/data-cw-page="voucher-editor"/);assert.match(page,/class="ve-page/);
assert.match(page,/cw-nav-item[^>]+data-cw-view="reconcile"[^>]+aria-label="流水及发票原件"/);
assert.match(page,/cw-nav-item[^>]+data-cw-view="payroll"[^>]+aria-label="工资管理"/);
console.log('full-page voucher validation, searchable subjects, Chinese totals, payroll slips, export and navigation passed');
