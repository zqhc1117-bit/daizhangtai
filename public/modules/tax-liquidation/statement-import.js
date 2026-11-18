import {ACCOUNTS,ASSETS,LIABILITIES} from './accounts.config.js';
import {excelAmount} from './full/excel.js';
import {amount,yuan} from './money.js';
const clean=v=>String(v??'').normalize('NFKC').replace(/[\s,，、:：()（）【】\[\]—_]/g,'').replace(/^(?:其中|加|减)/,'');
const groups={asset:'资产',liability:'负债',equity:'所有者权益'};
const extra=[
  ['shortInvestment','短期投资','asset',['短期投资','交易性金融资产']],['notesReceivable','应收票据','asset',['应收票据']],
  ['longInvestment','长期投资','asset',['长期股权投资','长期债券投资','其他权益工具投资']],['construction','在建工程','asset',['在建工程','工程物资']],
  ['longPrepaid','长期待摊费用','asset',['长期待摊费用']],['otherAssets','其他资产','asset',['其他流动资产','其他非流动资产','待处理财产损溢']],
  ['shortLoans','短期借款','liability',['短期借款']],['longLoans','长期借款','liability',['长期借款','应付债券']],
  ['notesPayable','应付票据','liability',['应付票据']],['advances','预收款项','liability',['预收账款','预收款项','合同负债']],
  ['otherLiabilities','其他负债','liability',['其他流动负债','其他非流动负债','长期应付款','递延收益','应付利息','应付利润','应付股利']],
  ['otherEquity','其他权益','equity',['其他综合收益','其他权益工具','专项储备']],
];
const definitions=[...ACCOUNTS.map(a=>({...a,group:ASSETS.includes(a)?'asset':LIABILITIES.includes(a)?'liability':'equity'})),...extra.map(([key,label,group,aliases])=>({key,label,group,aliases,aggregateAliases:true}))];
const totals=[['assetsTotal','资产总计',['资产总计','资产合计']],['liabilitiesTotal','负债合计',['负债合计','负债总计']],['equityTotal','所有者权益合计',['所有者权益合计','所有者权益或股东权益合计','股东权益合计']],['liabilitiesEquityTotal','负债和所有者权益总计',['负债和所有者权益总计','负债及所有者权益总计','负债和所有者权益或股东权益总计','负债及所有者权益或股东权益总计']]];
const ending=v=>/期末(?:余额|数|金额)?|年末(?:余额|数)|本期期末/.test(clean(v));
function definition(label){const text=clean(label).replace(/^[一二三四五六七八九十\d.]+[.]/,'');return definitions.find(a=>a.aliases.some(alias=>clean(alias)===text));}
function unitOf(rows){
  const text=rows.slice(0,20).flat().join('\n').normalize('NFKC');
  const units=new Set([...text.matchAll(/(?:金额单位|(?<!编制)单位)[:\s(]*(?:人民币)?\s*(亿|万)?元(?=$|[\s),，;；])/g)].map(m=>m[1]==='亿'?100000000:m[1]==='万'?10000:1));
  if(units.size>1)throw new Error('报表出现不同金额单位，请按相同单位拆分后上传。');
  return [...units][0]||1;
}
export function statementMetadata(rows){
  const lines=rows.slice(0,20).map(r=>r.filter(x=>String(x).trim()).join('  ')),text=lines.join('\n');
  const company=text.match(/(?:编制单位|单位名称|企业名称|公司名称)\s*[：:]?\s*([^\n]+?)(?=\s{2,}|\s+(?:单位|日期|金额|纳税)|$)/m);
  const dateMatches=[...text.matchAll(/(20\d{2})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})\s*日?/g)];
  let baseDate='';
  if(dateMatches.length){const m=dateMatches.at(-1);if(+m[2]>=1&&+m[2]<=12&&+m[3]>=1&&+m[3]<=31)baseDate=`${m[1]}-${m[2].padStart(2,'0')}-${m[3].padStart(2,'0')}`;}
  return {companyName:company?.[1]?.trim().slice(0,180)||'',baseDate};
}
function complete(result,rows){
  if(result.entries.some(e=>excelAmount(e.value)===null))throw new Error('汇总金额超出支持范围，请缩小报表范围。');
  result.metadata=statementMetadata(rows);result.warnings=[...new Set(result.warnings)];
  result.matches=result.entries;
  result.values=Object.fromEntries(result.entries.map(e=>[e.key,e.value]));
  if(result.entries.length<2)throw new Error('未识别到足够的期末余额。请上传含“项目/科目名称”和“期末余额”的资产负债表或科目余额表。');
  return result;
}
export function parseBalanceSheet(rows){
  const unit=unitOf(rows),headers=[],found=new Map(),warnings=[],sourceTotals={};
  rows.forEach((row,index)=>{const columns=row.map((v,col)=>ending(v)||ending(String(v)+String(rows[index+1]?.[col]||''))?col:-1).filter(x=>x>=0);if(columns.length)headers.push({index,columns});});
  if(!headers.length)throw new Error('未找到明确的期末余额列，请上传含期末列标题的报表。');
  rows.forEach((row,index)=>row.forEach((cell,col)=>{
    if(typeof cell!=='string'||!cell.trim())return;
    const text=clean(cell),total=totals.find(t=>t[2].some(a=>clean(a)===text));
    let def=definition(cell);
    if(/(?:原价|原值|累计折旧|累计摊销|减值准备)/.test(text))def=null;
    const header=headers.filter(h=>h.index<index).at(-1);if(!header)return;
    const endCol=header.columns.find(c=>c>col);if(endCol===undefined)return;
    if(row.slice(col+1,endCol).some(v=>definition(v)||totals.some(t=>t[2].some(a=>clean(a)===clean(v)))))return;
    if(!def&&!total){
      if(/[\u4e00-\u9fff]/.test(text)&&!/(?:合计|总计|项目|资产$|负债$|权益$|行次|单位|年|月|日|原价|原值|折旧|摊销|减值|期初|期末)/.test(text)){
        const value=excelAmount(row[endCol],unit);if(value!==null&&amount(value)!==0)warnings.push(`“${cell}”期末金额 ${value} 元未归入分类，请在原表中核对；报告保留原表总计。`);
      }return;
    }
    const value=excelAmount(row[endCol],unit);
    if(value===null){warnings.push(`${total?.[1]||def.label}金额为空或无法识别，未按零处理。`);return;}
    if(total){if(sourceTotals[total[0]]!==undefined&&sourceTotals[total[0]]!==value)warnings.push(`${total[1]}有多个不同金额，请核对报表期间。`);else sourceTotals[total[0]]=value;return;}
    const priority=/净值|账面价值/.test(text)?2:1,previous=found.get(def.key);
    if(!previous||priority>previous.priority)found.set(def.key,{key:def.key,label:def.label,group:def.group,value,sourceLabel:String(cell),priority,row:index+1});
    else if(def.aggregateAliases&&previous.sourceLabel!==cell&&priority===previous.priority){previous.value=yuan(amount(previous.value)+amount(value));previous.sourceLabel+='、'+cell;}
    else if(previous.value!==value&&priority===previous.priority)warnings.push(`${def.label}有多个不同期末金额，已取第${previous.row}行。`);
  }));
  return complete({kind:'资产负债表',unit,entries:[...found.values()],sourceTotals,warnings},rows);
}
function trialHeaders(rows){
  let schema=null;
  for(let i=0;i<Math.min(rows.length,30);i++){
    const row=rows[i],end=row.map((c,j)=>ending(c)?j:-1).filter(j=>j>=0);
    if(!end.length)continue;
    const nearby=rows.slice(Math.max(0,i-2),i+3);
    const indexOf=re=>{for(const cells of nearby){const j=cells.findIndex(c=>re.test(clean(c)));if(j>=0)return j;}return -1;};
    const name=indexOf(/^(?:科目名称|账户名称|会计科目|科目)$/),code=indexOf(/科目(?:编码|代码|编号)|账户编码/);
    if(name<0)continue;
    let debit=row.findIndex(c=>ending(c)&&/借/.test(clean(c))),credit=row.findIndex(c=>ending(c)&&/贷/.test(clean(c))),direction=-1,value=-1,lastHeader=i;
    if(debit<0&&credit<0){
      const start=end.at(-1),nextGroup=row.findIndex((c,j)=>j>start&&/(?:期初|本期|本月|累计|年初|年末)/.test(clean(c)));
      const stop=nextGroup<0?Math.max(...nearby.map(r=>r.length)):nextGroup;
      for(let n=i+1;n<=i+2&&n<rows.length;n++){
        const sub=rows[n];
        if(/^[\d]+$/.test(String(sub[code]||'')))break;
        for(let j=start;j<stop;j++){
          if(/^借方(?:余额|金额)?$/.test(clean(sub[j]))){debit=j;lastHeader=n;}
          if(/^贷方(?:余额|金额)?$/.test(clean(sub[j]))){credit=j;lastHeader=n;}
          if(/^(?:余额)?方向$/.test(clean(sub[j]))){direction=j;lastHeader=n;}
          if(/^(?:余额|金额)$/.test(clean(sub[j]))){value=j;lastHeader=n;}
        }
      }
      if(debit<0&&credit<0){value=value<0?start:value;direction=direction<0?row.findIndex(c=>/^(?:期末)?(?:余额)?方向$/.test(clean(c))):direction;}
    }
    schema={name,code,debit,credit,direction,value,index:lastHeader};break;
  }
  if(!schema)throw new Error('科目余额表须包含科目名称、期末借方/贷方，或期末余额列。');
  return schema;
}
const trialAliases={
  '库存现金':['cash','asset',1],'现金':['cash','asset',1],'银行存款':['cash','asset',1],'其他货币资金':['cash','asset',1],
  '原材料':['inventory','asset',1],'材料采购':['inventory','asset',1],'在途物资':['inventory','asset',1],'库存商品':['inventory','asset',1],'周转材料':['inventory','asset',1],'生产成本':['inventory','asset',1],
  '委托加工物资':['inventory','asset',1],'存货跌价准备':['inventory','asset',-1],
  '累计折旧':['fixedAssets','asset',-1],'固定资产减值准备':['fixedAssets','asset',-1],'累计摊销':['intangibleAssets','asset',-1],'无形资产减值准备':['intangibleAssets','asset',-1],
  '本年利润':['retainedEarnings','equity',1],'利润分配':['retainedEarnings','equity',1],
  '坏账准备':['receivables','asset',-1],'库存股':['otherEquity','equity',-1],
};
export function parseTrialBalance(rows){
  const unit=unitOf(rows),head=trialHeaders(rows),warnings=[],sourceTotals={},found=new Map();
  const data=rows.slice(head.index+1).map((row,index)=>({row,index:index+head.index+2,code:head.code>=0?String(row[head.code]??'').trim():'',label:String(row[head.name]??'').trim()})).filter(r=>r.label&&!/合计|总计|科目名称|账户名称/.test(r.label));
  const codes=new Set(data.filter(r=>/^\d{4,}$/.test(r.code)).map(r=>r.code));
  const selected=data.filter(r=>!r.code||![...codes].some(p=>p!==r.code&&r.code.startsWith(p))),seen=new Map();
  for(const item of selected){
    let def=definition(item.label),alias=trialAliases[clean(item.label)];
    if(!def&&!alias&&item.code){const base=item.code.slice(0,4),map={'1001':'库存现金','1002':'银行存款','1012':'其他货币资金','1602':'累计折旧','1702':'累计摊销'};alias=trialAliases[map[base]];}
    if(alias){const [key,group]=alias;def=definitions.find(a=>a.key===key)||{key,label:groups[group],group};}
    const cells=item.row,contra=alias?.[2]===-1;
    let value=null;
    if(head.debit>=0&&head.credit>=0){
      const d=cells[head.debit],c=cells[head.credit];
      if(String(d??'').trim()||String(c??'').trim()){
        const debit=excelAmount(d===''||d==null?'0':d,unit),credit=excelAmount(c===''||c==null?'0':c,unit);
        if(debit!==null&&credit!==null)value=yuan((amount(debit)-amount(credit))*(def?.group==='asset'?1:-1));
      }
    }else if(head.value>=0){
      value=excelAmount(cells[head.value],unit);
      if(value!==null&&head.direction>=0){const direction=clean(cells[head.direction]);if(!['借','贷','借方','贷方','平',''].includes(direction)){value=null;}else if(amount(value)!==0&&!direction){value=null;}else if(direction.startsWith('借')&&def?.group!=='asset'||direction.startsWith('贷')&&def?.group==='asset')value=yuan(-amount(value));}
      else if(value!==null){if(contra)value=yuan(-amount(value));warnings.push('单列期末余额未提供借贷方向，按科目正常方向归类；请核对异常方向余额。');}
    }
    if(value===null){if(def)warnings.push(`${item.label}第${item.index}行期末金额或借贷方向无法识别，未按零处理。`);continue;}
    if(!def){if(amount(value)!==0)warnings.push(`“${item.label}”期末金额 ${value} 元尚未归类，请核对是否存在未结转损益或未识别科目。`);continue;}
    const identity=item.code||clean(item.label);
    if(seen.has(identity)){if(seen.get(identity)!==value)warnings.push(`${item.label}出现重复科目且金额不同，已保留首次识别值。`);continue;}seen.set(identity,value);
    const previous=found.get(def.key);
    if(previous){previous.value=yuan(amount(previous.value)+amount(value));previous.sourceLabel+='、'+item.label;}
    else found.set(def.key,{key:def.key,label:def.label,group:def.group,value,sourceLabel:item.label,row:item.index});
  }
  warnings.push('科目余额表按最上级已提供科目汇总，排除其下级明细，避免重复；固定资产、无形资产已扣除识别到的折旧和摊销。');
  return complete({kind:'科目余额表',unit,entries:[...found.values()],sourceTotals,warnings},rows);
}
export function parseStatementRows(rows){
  const names=new Set(rows.filter(row=>/(?:编制单位|单位名称|企业名称|公司名称)\s*[：:]/.test(row.join(' '))).map(row=>statementMetadata([row]).companyName).filter(Boolean));
  if(names.size>1)throw new Error('文件包含多个不同企业的报表，请按企业拆分后分别上传。');
  const header=rows.slice(0,25).flat().map(clean).join('|');
  return /科目名称|账户名称|科目编码|科目代码/.test(header)?parseTrialBalance(rows):parseBalanceSheet(rows);
}
function decodeText(bytes){const prefix=new Uint8Array(bytes);if(prefix[0]===255&&prefix[1]===254)return new TextDecoder('utf-16le').decode(bytes);if(prefix[0]===254&&prefix[1]===255)return new TextDecoder('utf-16be').decode(bytes);try{return new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{return new TextDecoder('gb18030').decode(bytes);}}
export async function importStatement(file,ctx={}){
  if(file.size>10*1024*1024)throw new Error('单个文件不能超过 10 MB。');
  if(!file.size)throw new Error('文件为空，请重新选择。');
  const extension=file.name.split('.').at(-1).toLowerCase();
  const supported=['xlsx','xls','csv','tsv','pdf','png','jpg','jpeg','webp'];
  if(!supported.includes(extension))throw new Error('请选择 PDF、Excel（.xlsx/.xls）、CSV、TSV 或 PNG/JPG/WebP 图片。');
  const bytes=await file.arrayBuffer();let candidates=[],sourceWarnings=[];
  if(['xlsx','xls','csv','tsv'].includes(extension)){
    const X=await import('./vendor/xlsx.js'),text=['csv','tsv'].includes(extension);
    const workbook=X.read(text?decodeText(bytes):bytes,{type:text?'string':'array',cellFormula:false,cellHTML:false,sheetRows:3001,raw:text});
    for(const name of workbook.SheetNames.slice(0,30)){
      const sheet=workbook.Sheets[name];if(!sheet['!ref'])continue;
      const range=X.utils.decode_range(sheet['!ref']);
      if(range.e.r>=3000||range.e.c>80){sourceWarnings.push(`工作表“${name}”超过3000行或81列，未读取。`);continue;}
      candidates.push({name,rows:X.utils.sheet_to_json(sheet,{header:1,raw:true,defval:'',blankrows:true})});
    }
  }else{
    if(!ctx.api)throw new Error('PDF / 图片识别需要连接工作台服务。');
    const data=new Uint8Array(bytes);let binary='';for(let i=0;i<data.length;i+=16384)binary+=String.fromCharCode(...data.subarray(i,i+16384));
    const result=await ctx.api('/api/sales/statement-text',{method:'POST',body:{name:file.name,content_base64:btoa(binary)},timeoutMs:240000});
    candidates=[{name:result.ocr?'扫描识别':'PDF 文字识别',rows:result.rows}];sourceWarnings.push(...(result.warnings||[]));
  }
  const parsed=[],errors=[];
  for(const candidate of candidates){try{parsed.push({...parseStatementRows(candidate.rows),sheetName:candidate.name});}catch(error){errors.push(error.message);}}
  parsed.sort((a,b)=>b.entries.length-a.entries.length);
  if(!parsed.length)throw new Error(errors[0]||sourceWarnings[0]||'没有识别到可用报表，请检查文件内容。');
  const best=parsed[0];
  if(parsed.length>1)sourceWarnings.push(`文件包含 ${parsed.length} 张可识别报表，本次使用“${best.sheetName}”；其他报表未叠加。`);
  return {...best,fileName:file.name,warnings:[...new Set([...sourceWarnings,...best.warnings])]};
}
