import {ACCOUNTS} from '../accounts.config.js';
import {yuan,MAX_FEN} from '../money.js';
const compact = text => String(text ?? '').replace(/[\s（）()、：:【】\[\]—－_]/g,'').replace(/^(?:其中|减|加)[：:]?/,'');
const ending = value => /期末(?:余额|数|金额)?|年末(?:余额|数)|本期期末/.test(compact(value));
function score(label,account) {
  const normalized = compact(label).replace(/^[一二三四五六七八九十\d.]+[、.]/,'');
  if (/(原价|原值|折旧|摊销|减值|清理|合计|总计)/.test(normalized)) return 0;
  return Math.max(0,...account.aliases.map(alias => {
    const a = compact(alias);
    if (normalized === a) return 100+a.length;
    return normalized.startsWith(a) && normalized.length-a.length<=4 ? 70+a.length : 0;
  }));
}
export function excelAmount(value,unit = 1) {
  if (value === null || value === undefined || value === '') return null;
  let text = String(value).trim().replace(/[,，\s¥￥]/g,'');
  if (/^[-—–]$/.test(text)) return '0.00';
  if (/^[（(].+[）)]$/.test(text)) text = '-'+text.slice(1,-1);
  if (!/^-?\d+(?:\.\d{0,12})?$/.test(text)) return null;
  const negative = text.startsWith('-'), [whole, fraction=''] = text.replace(/^-/,'').split('.');
  const divisor = 10n**BigInt(fraction.length), numerator = BigInt(whole+fraction)*BigInt(unit)*100n;
  const fen = (numerator+divisor/2n)/divisor*(negative?-1n:1n);
  if (fen>BigInt(MAX_FEN) || fen< -BigInt(MAX_FEN)) return null;
  return yuan(Number(fen));
}
export function matchBalanceRows(rows) {
  const unitText = compact(rows.slice(0,20).flat().map(String).join(' '));
  const unit = /(?:金额单位|单位)(?:人民币)?亿元/.test(unitText) ? 100000000 : /(?:金额单位|单位)(?:人民币)?万元/.test(unitText) ? 10000 : 1;
  const headers = [];
  rows.forEach((row,index) => {
    const columns = row.map((cell,col) => ending(cell) || ending(String(cell)+String(rows[index+1]?.[col] || '')) ? col : -1).filter(col => col>=0);
    if (columns.length) headers.push({index,columns});
  });
  if (!headers.length) return {values:{},matches:[],warnings:['未找到“期末余额/期末数”列，请使用含明确期末列标题的资产负债表。'],unit};
  const found = new Map(), warnings = [];
  rows.forEach((row,index) => row.forEach((cell,col) => {
    if (typeof cell !== 'string' || !cell.trim()) return;
    const candidates = ACCOUNTS.map(account => ({account,score:score(cell,account)})).filter(match => match.score>0).sort((a,b) => b.score-a.score);
    if (!candidates.length) return;
    const header = headers.filter(h => h.index<index).at(-1);
    if (!header) return;
    const targetCol = header.columns.find(column => column>col);
    if (targetCol === undefined) return;
    // Never borrow the amount from the other half of a side-by-side statement.
    if (row.slice(col+1,targetCol).some(v => typeof v === 'string' && ACCOUNTS.some(a => score(v,a)>0))) return;
    const value = excelAmount(row[targetCol],unit), best = candidates[0];
    if (value === null) {warnings.push(`${best.account.label}第${index+1}行金额为空或格式不支持，保留原值`);return;}
    const previous = found.get(best.account.key);
    if (!previous || best.score>previous.score) found.set(best.account.key,{key:best.account.key,label:best.account.label,value,score:best.score,row:index+1,column:targetCol+1});
    else if (best.score===previous.score && previous.value!==value) warnings.push(`${best.account.label}存在多个不同期末金额，已取第${previous.row}行，请核实`);
  }));
  const matches = [...found.values()];
  return {values:Object.fromEntries(matches.map(row => [row.key,row.value])),matches,warnings:[...new Set(warnings)],unit};
}
export async function importBalance(file) {
  if (!/\.(xlsx|xls)$/i.test(file.name)) throw new Error('请选择 .xlsx 或 .xls 资产负债表。');
  if (file.size>10*1024*1024) throw new Error('文件超过10MB，请缩小文件后重试。');
  const XLSX = await import('../vendor/xlsx.js');
  const workbook = XLSX.read(await file.arrayBuffer(),{type:'array',cellFormula:false,cellHTML:false,sheetRows:3001});
  let best = null;
  for (const name of workbook.SheetNames.slice(0,30)) {
    const sheet = workbook.Sheets[name];
    if (!sheet['!ref']) continue;
    const range = XLSX.utils.decode_range(sheet['!ref']);
    if (range.e.r>=3000 || range.e.c>80) continue;
    const rows = XLSX.utils.sheet_to_json(sheet,{header:1,raw:true,defval:'',blankrows:true});
    const result = {...matchBalanceRows(rows),sheetName:name};
    if (!best || result.matches.length>best.matches.length) best = result;
  }
  if (!best?.matches.length) throw new Error(best?.warnings[0] || '未识别到可用的资产负债表。请确认科目名称、期末列标题及报表范围（最多3000行、81列）。');
  return best;
}
