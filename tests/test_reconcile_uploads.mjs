import assert from 'node:assert/strict';
import { bindReconcile, renderReconcile, resetReconcile } from '../public/reconcile.js';

const client = { id: 41, name: '合成多文件验收公司', enterprise_name: '合成多文件验收公司' };
const control = dataset => ({ dataset, disabled: false, value: '', files: [], handlers: {},
  addEventListener(type, handler) { this.handlers[type] = handler; } });
const root = { isConnected: true, contains(node) { return node === this.page; },
  querySelector(selector) { return { '.rc-root': this.page, '[data-rc-account]': this.account,
    '[data-rc-client]': this.client }[selector] || null; },
  querySelectorAll(selector) {
    if (selector === '[data-rc-file]') return this.files;
    if (selector === 'input,select,button') return [this.account, this.client, ...this.files];
    if (selector === '[data-rc-progress]') return this.progress;
    return [];
  }
};
Object.defineProperty(root, 'innerHTML', { get() { return this.html; }, set(value) {
  this.html = value;
  if (this.page) this.page.isConnected = false;
  this.page = { isConnected: true };
  this.account = control({}); this.client = control({});
  this.files = [control({ rcFile: 'statement' }), control({ rcFile: 'invoices' })];
  this.progress = [control({ rcProgress: 'statement' }), control({ rcProgress: 'invoice' })];
} });
const requests = [], alerts = [];
const api = async (url, options) => {
  requests.push({ url, options });
  if (url === '/api/reconcile/parse/start') return { job_id: 'synthetic-upload-job' };
  if (url.startsWith('/api/reconcile/parse/progress')) return { status: 'completed', result: {
    transactions: [], invoices: [], warnings: [], account_name: client.name
  } };
  throw new Error('Unexpected route: ' + url);
};
function file(name, content = name) { const item = new Blob([content]); item.name = name; return item; }
async function select(index, files) { root.files[index].files = files; await root.files[index].handlers.change(); }
async function click(selector, dataset) {
  const target = { dataset, closest(value) { return value === selector ? this : null; } };
  await root.onclick({ target });
}
const priorReader = globalThis.FileReader, priorAlert = globalThis.alert;
const priorClone = Object.getOwnPropertyDescriptor(globalThis, 'structuredClone');
globalThis.FileReader = class { async readAsDataURL(blob) {
  this.result = 'data:application/pdf;base64,' + Buffer.from(await blob.arrayBuffer()).toString('base64');
  this.onload();
} };
globalThis.alert = message => alerts.push(message);
try {
  resetReconcile();
  root.innerHTML = renderReconcile([client], { client });
  bindReconcile(root, api, [client], null, { client });
  assert.match(root.innerHTML, /data-rc-file="statement"[^>]*multiple/);
  assert.match(root.innerHTML, /data-rc-file="invoices"[^>]*multiple/);
  assert.doesNotMatch(root.innerHTML, /选择一份|一份收支交易清单/);
  await select(0, [file('第一份.pdf'), file('第二份.pdf')]);
  await select(0, [file('第三份.zip')]);
  await select(1, [file('发票.pdf')]);
  assert.match(root.innerHTML, /收支交易清单：已选 3 个文件/);
  await click('[data-rc-remove-file]', { rcRemoveFile: 'statement', index: '1' });
  assert.match(root.innerHTML, /收支交易清单：已选 2 个文件/);
  assert.doesNotMatch(root.innerHTML, /第二份\.pdf/);
  delete globalThis.structuredClone;
  await click('[data-rc-action]', { rcAction: 'parse' });
  assert.deepEqual(alerts, [], 'File recognition must start without structuredClone.');
  const sent = requests.find(row => row.url === '/api/reconcile/parse/start').options.body;
  assert.deepEqual(sent.statements.map(row => row.name), ['第一份.pdf', '第三份.zip']);
  assert.deepEqual(sent.statements.map(row => Buffer.from(row.content_base64, 'base64').toString()), ['第一份.pdf', '第三份.zip']);
  assert.equal(sent.invoices[0].name, '发票.pdf');
  assert.equal(sent.client_id, 41);
  assert.equal(Object.hasOwn(sent, 'statement'), false);
  await select(0, Array.from({ length: 29 }, (_, index) => file(`额外${index}.pdf`)));
  const requestCount = requests.length;
  await click('[data-rc-action]', { rcAction: 'parse' });
  assert.equal(requests.length, requestCount);
  assert.deepEqual(alerts, ['文件合计超过 30 份，请分批处理。']);
  console.log('Multiple statement selection, removal, upload without structuredClone, and file limit passed.');
} finally {
  resetReconcile();
  if (priorClone) Object.defineProperty(globalThis, 'structuredClone', priorClone);
  if (priorReader === undefined) delete globalThis.FileReader; else globalThis.FileReader = priorReader;
  if (priorAlert === undefined) delete globalThis.alert; else globalThis.alert = priorAlert;
}
