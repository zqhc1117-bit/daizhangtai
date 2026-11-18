import {zip} from './balance-xlsx.js';
import {summarize,rowAmount,quantity,parseMoney,yuan} from './ledger-draft-model.js';
import {ledgerPresentation} from './ledger-draft-expenses.js';

const XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
const NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main';
const REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const xml = value => String(value ?? '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\uD800-\uDFFF\uFFFE\uFFFF]/gu,'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
const text = (ref,value,style=0) => `<c r="${ref}" s="${style}" t="inlineStr"><is><t xml:space="preserve">${xml(value)}</t></is></c>`;
const number = (ref,value,style=0,formula='') => `<c r="${ref}" s="${style}">${formula?`<f>${xml(formula)}</f>`:''}<v>${value}</v></c>`;
const row = (i,cells,height=30) => `<row r="${i}" ht="${height}" customHeight="1">${cells.join('')}</row>`;
const merged = (i,value,last,style=0) => row(i,[text(`A${i}`,value,style)],i===1?34:30);
function sheet(rows,widths,merges,filter='') {
  return `${XML}<worksheet xmlns="${NS}"><dimension ref="A1:${String.fromCharCode(64+widths.length)}${rows.length}"/><sheetViews><sheetView showGridLines="0" workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="30"/><cols>${widths.map((w,i)=>`<col min="${i+1}" max="${i+1}" width="${w}" customWidth="1"/>`).join('')}</cols><sheetData>${rows.join('')}</sheetData>${filter?`<autoFilter ref="${filter}"/>`:''}<mergeCells count="${merges.length}">${merges.map(ref=>`<mergeCell ref="${ref}"/>`).join('')}</mergeCells><pageMargins left="0.25" right="0.25" top="0.4" bottom="0.4" header="0.2" footer="0.2"/><pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>`;
}
function styles() {
  const xf = (font,fill,format=0) => `<xf numFmtId="${format}" fontId="${font}" fillId="${fill}" borderId="1" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" horizontal="${format?'right':'left'}" wrapText="1"/></xf>`;
  return `${XML}<styleSheet xmlns="${NS}"><numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts><fonts count="4"><font><sz val="11"/><name val="微软雅黑"/></font><font><b/><sz val="16"/><name val="微软雅黑"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="微软雅黑"/></font><font><b/><sz val="11"/><name val="微软雅黑"/></font></fonts><fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>${['2C3E50','F4F7FA','D6EAF8'].map(color=>`<fill><patternFill patternType="solid"><fgColor rgb="FF${color}"/><bgColor indexed="64"/></patternFill></fill>`).join('')}</fills><borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border>${['left','right','top','bottom'].map(side=>`<${side} style="thin"><color rgb="FFDDE4EB"/></${side}>`).join('')}<diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="9">${[xf(0,0),xf(1,0),xf(2,2),xf(0,0),xf(0,0,164),xf(0,3),xf(0,3,164),xf(3,4),xf(3,4,164)].join('')}</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`;
}

export function createLedgerDraftXlsx({companyName,period,account,draft}) {
  const totals = summarize(draft,account), copy=ledgerPresentation(account);
  if (!account?.code || !totals.balanced) throw new Error(totals.errors[0] || '明细合计与目标总金额不一致，请先配平');
  const status = draft.reviewed ? '已人工核对的拟稿' : '待核对拟稿';
  const metadata = `会计科目：${account.code} ${account.name} ｜ 金额单位：人民币元 ｜ 账期：${period}`;
  const note = `${status}；${copy.expense?'费用汇总口径；多项金额仅为预算分配，需核对用途、归属、期间及实际金额。1项为费用项目，非员工人数。':'自动拆分及单价为估算，请按原始资料核对。'}此表不写入正式账簿。`;
  const first = [merged(1,`${companyName} - ${copy.detailTitle}`,'I',1),merged(2,metadata,'I'),row(3,[text('A3',note)],copy.expense?48:30),row(4,copy.columns.map((v,i)=>text(`${String.fromCharCode(65+i)}4`,v,2)))];
  draft.rows.forEach((item,index) => {
    const r=index+5, base=index%2?5:3;
    first.push(row(r,[number(`A${r}`,index+1,base),text(`B${r}`,`${account.code}${String(index+1).padStart(2,'0')}`,base),...['category','name','spec','unit'].map((key,i)=>text(`${String.fromCharCode(67+i)}${r}`,item[key],base)),number(`G${r}`,quantity(item.quantity),base),number(`H${r}`,yuan(parseMoney(item.price)),base+1),number(`I${r}`,yuan(rowAmount(item)),base+1,`ROUND(G${r}*H${r},2)`)],copy.expense?90:42));
  });
  const totalRow=draft.rows.length+5, end=totalRow-1;
  first.push(row(totalRow,[text(`A${totalRow}`,'合计',7),text(`B${totalRow}`,'—',7),text(`C${totalRow}`,'—',7),text(`D${totalRow}`,copy.subtotal,7),text(`E${totalRow}`,'—',7),text(`F${totalRow}`,totals.unit,7),number(`G${totalRow}`,totals.count,7,`SUM(G5:G${end})`),text(`H${totalRow}`,'—',7),number(`I${totalRow}`,yuan(totals.total),8,`SUM(I5:I${end})`)],38));
  first.push(row(totalRow+1,[text(`A${totalRow+1}`,'目标金额',7),number(`B${totalRow+1}`,yuan(totals.target),8),text(`D${totalRow+1}`,'与目标差额（应为0）',7),number(`I${totalRow+1}`,'0.00',8,`ROUND(I${totalRow}-B${totalRow+1},2)`)]));
  const summary = `${copy.summary(draft.keywords)}，${draft.rows.length}项合计`;
  const second = [merged(1,`${companyName} - ${copy.generalTitle}`,'F',1),merged(2,metadata,'F'),row(3,[text('A3',note)],copy.expense?48:30),row(4,['科目代码','科目名称','摘要',`${copy.quantityLabel}（${totals.unit}）`,'方向',copy.amountLabel].map((v,i)=>text(`${String.fromCharCode(65+i)}4`,v,2)),copy.expense?42:30),row(5,[text('A5',account.code,7),text('B5',account.name,7),text('C5',summary,7),number('D5',totals.count,7,`'${copy.sheetName}'!G${totalRow}`),text('E5',account.direction==='credit'?'贷':'借',7),number('F5',yuan(totals.total),8,`'${copy.sheetName}'!I${totalRow}`)],65)];
  const names=[copy.sheetName,'总分类账'];
  return zip([
    ['[Content_Types].xml',`${XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${names.map((_,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}</Types>`],
    ['_rels/.rels',`${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
    ['xl/workbook.xml',`${XML}<workbook xmlns="${NS}" xmlns:r="${REL}"><sheets>${names.map((name,i)=>`<sheet name="${name}" sheetId="${i+1}" r:id="rId${i+1}"/>`).join('')}</sheets><calcPr calcId="191029" calcMode="auto" fullCalcOnLoad="1" forceFullCalc="1"/></workbook>`],
    ['xl/_rels/workbook.xml.rels',`${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${names.map((_,i)=>`<Relationship Id="rId${i+1}" Type="${REL}/worksheet" Target="worksheets/sheet${i+1}.xml"/>`).join('')}<Relationship Id="rId3" Type="${REL}/styles" Target="styles.xml"/></Relationships>`],
    ['xl/styles.xml',styles()],
    ['xl/worksheets/sheet1.xml',sheet(first,[7,15,20,26,copy.expense?44:29,17,14,18,20],['A1:I1','A2:I2','A3:I3'],`A4:I${end}`)],
    ['xl/worksheets/sheet2.xml',sheet(second,[16,23,58,25,9,24],['A1:F1','A2:F2','A3:F3'])],
  ]);
}

export function draftFilename(company,account,total) {
  const safe = value => String(value).replace(/[<>:"/\\|?*\u0000-\u001f]/g,'_').slice(0,70);
  return `${safe(company)}-${safe(account.name)}明细及总账(${yuan(parseMoney(total))}元).xlsx`;
}
