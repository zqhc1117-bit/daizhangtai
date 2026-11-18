import {zip} from './balance-xlsx.js';
import {payrollReport,legacySocialNote} from './payroll-report.js';

const XML='<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
const NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main';
const REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const xml=value=>String(value??'').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\uD800-\uDFFF\uFFFE\uFFFF]/gu,'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
const col=index=>String.fromCharCode(65+index);
const text=(ref,value,style)=>`<c r="${ref}" s="${style}" t="inlineStr"><is><t xml:space="preserve">${xml(value)}</t></is></c>`;
const blank=(ref,style)=>`<c r="${ref}" s="${style}"/>`;
const row=(index,cells,height=26)=>`<row r="${index}" ht="${height}" customHeight="1">${cells.join('')}</row>`;
const yuan=value=>`${Math.floor(value/100)}.${String(value%100).padStart(2,'0')}`;
const number=(ref,value,style,formula='')=>value===null?blank(ref,style):`<c r="${ref}" s="${style}">${formula?`<f>${xml(formula)}</f>`:''}<v>${yuan(value)}</v></c>`;

function styles(){
  const font=(size,bold=false)=>`<font>${bold?'<b/>':''}<sz val="${size}"/><color rgb="FF000000"/><name val="宋体"/><charset val="134"/></font>`;
  const xf=(fontId,borderId,horizontal,numFmtId=0)=>`<xf numFmtId="${numFmtId}" fontId="${fontId}" fillId="0" borderId="${borderId}" xfId="0" applyFont="1" applyBorder="1" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="${horizontal}" vertical="center" wrapText="1"/></xf>`;
  const side=name=>`<${name} style="thin"><color rgb="FF000000"/></${name}>`;
  // Styles: normal, company, month, unit, header, name, amount, bold amount, total, note.
  return `${XML}<styleSheet xmlns="${NS}"><numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00"/></numFmts><fonts count="4">${font(11)+font(16)+font(11,true)+font(10)}</fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border>${['left','right','top','bottom'].map(side).join('')}<diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="10">${[xf(0,0,'left'),xf(1,0,'center'),xf(2,0,'center'),xf(0,0,'right'),xf(2,1,'center'),xf(0,1,'center'),xf(0,1,'right',164),xf(2,1,'right',164),xf(2,1,'center'),xf(3,0,'left')].join('')}</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`;
}

export function createPayrollXlsx(book,records){
  const report=payrollReport(book,records),{columns}=report,last=col(columns.length-1);
  const mergedRow=(index,value,style,height)=>row(index,columns.map((_,i)=>i===0?text(`A${index}`,value,style):blank(`${col(i)}${index}`,style)),height);
  const output=[mergedRow(1,report.title,1,32),mergedRow(2,report.periodLabel,2,24),row(3,columns.map((_,i)=>i===columns.length-1?text(`${last}3`,'单位：元',3):blank(`${col(i)}3`,0)),24)];
  const merges=[`A1:${last}1`,`A2:${last}2`,'D4:G4'];
  for(let index=0;index<columns.length;index++)if(index<3||index>6)merges.push(`${col(index)}4:${col(index)}5`);
  output.push(row(4,columns.map((c,i)=>text(`${col(i)}4`,i===3?'扣社保自付部分':i>3&&i<7?'':c.label,4))));
  output.push(row(5,columns.map((c,i)=>text(`${col(i)}5`,i>=3&&i<=6?c.label:'',4))));
  const position=key=>col(columns.findIndex(c=>c.key===key));
  report.rows.forEach(({values},index)=>{
    const r=index+6;
    output.push(row(r,columns.map((c,i)=>{
      const ref=`${col(i)}${r}`,value=values[i];
      if(!c.money)return c.key==='sequence'?`<c r="${ref}" s="5"><v>${value}</v></c>`:text(ref,value,5);
      let formula='';
      if(c.key==='social_fen'&&values.slice(3,6).every(v=>v!==null))formula=`SUM(D${r}:F${r})`;
      if(c.key==='payable_fen')formula=`ROUND(C${r}-G${r}-H${r}${report.hasOther?`-I${r}`:''},2)`;
      if(c.key==='net_fen')formula=`ROUND(${position('payable_fen')}${r}-${position('withheld_tax_fen')}${r},2)`;
      if(c.key==='withheld_tax_fen'&&value===0)return blank(ref,6);
      return number(ref,value,c.key==='housing_fund_fen'||c.key==='withheld_tax_fen'?7:6,formula);
    })));
  });
  const totalRow=report.rows.length+6,end=totalRow-1;
  output.push(row(totalRow,columns.map((c,i)=>{
    const ref=`${col(i)}${totalRow}`,value=report.totals[i];
    if(!c.money)return i===0?text(ref,'合计',8):blank(ref,8);
    if(c.key==='withheld_tax_fen'&&value===0)return text(ref,'－',8);
    return number(ref,value,7,report.rows.length?`SUM(${col(i)}6:${col(i)}${end})`:'');
  })));
  merges.push(`A${totalRow}:B${totalRow}`);
  if(report.hasLegacySocial){output.push(mergedRow(totalRow+1,legacySocialNote,9,24));merges.push(`A${totalRow+1}:${last}${totalRow+1}`);}
  const sheet=`${XML}<worksheet xmlns="${NS}"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><dimension ref="A1:${last}${output.length}"/><sheetViews><sheetView showGridLines="0" workbookViewId="0"/></sheetViews><sheetFormatPr defaultRowHeight="26"/><cols>${columns.map((c,i)=>`<col min="${i+1}" max="${i+1}" width="${c.width}" customWidth="1"/>`).join('')}</cols><sheetData>${output.join('')}</sheetData><mergeCells count="${merges.length}">${merges.map(ref=>`<mergeCell ref="${ref}"/>`).join('')}</mergeCells><printOptions horizontalCentered="1"/><pageMargins left="0.25" right="0.25" top="0.45" bottom="0.45" header="0.2" footer="0.2"/><pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>`;
  return zip([
    ['[Content_Types].xml',`${XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`],
    ['_rels/.rels',`${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
    ['xl/workbook.xml',`${XML}<workbook xmlns="${NS}" xmlns:r="${REL}"><sheets><sheet name="工资表" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="_xlnm.Print_Area" localSheetId="0">'工资表'!$A$1:$${last}$${output.length}</definedName><definedName name="_xlnm.Print_Titles" localSheetId="0">'工资表'!$1:$5</definedName></definedNames><calcPr calcId="191029" calcMode="auto" fullCalcOnLoad="1" forceFullCalc="1"/></workbook>`],
    ['xl/_rels/workbook.xml.rels',`${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${REL}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="${REL}/styles" Target="styles.xml"/></Relationships>`],
    ['xl/styles.xml',styles()],['xl/worksheets/sheet1.xml',sheet],
  ]);
}

export function payrollFilename(book){
  const company=String(book.client.name).replace(/[<>:"/\\|?*\u0000-\u001f]/g,'_').slice(0,80).replace(/[ .]+$/,'')||'公司';
  return `${company}_${book.period}_工资表.xlsx`;
}
