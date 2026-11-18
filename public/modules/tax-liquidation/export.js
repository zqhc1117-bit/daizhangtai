export async function createWordBlob(report) {
  const D = await import('./vendor/word.js');
  const {Document,Paragraph,TextRun,Table,TableRow,TableCell,Packer,AlignmentType,WidthType,BorderStyle,PageNumber,Footer} = D;
  const run = (text,options={}) => new TextRun({text:String(text),font:{name:'SimSun',eastAsia:'宋体'},size:22,...options});
  const paragraph = (text,options={}) => new Paragraph({children:[run(text)],spacing:{after:120,line:340},...options});
  const border = {style:BorderStyle.SINGLE,size:4,color:'444444'};
  const children = [paragraph(report.sample?'示例数据 · 仅供测试':report.status,{alignment:AlignmentType.CENTER}),
    new Paragraph({children:[run(report.title,{bold:true,size:34,font:{name:'SimHei',eastAsia:'黑体'}})],alignment:AlignmentType.CENTER,spacing:{before:120,after:280},keepNext:true}),
    paragraph('金额单位：人民币元',{alignment:AlignmentType.RIGHT})];
  for (const section of report.sections) {
    children.push(new Paragraph({children:[run(section.title,{bold:true,size:26})],spacing:{before:240,after:140},keepNext:true}));
    for (const block of section.blocks) {
      if (block.type==='paragraph') {children.push(paragraph(block.text));continue;}
      const row = (cells,header=false) => new TableRow({cantSplit:true,tableHeader:header,children:cells.map((text,i) => new TableCell({shading:header?{fill:'F0F4F5'}:undefined,margins:{top:80,bottom:80,left:90,right:90},children:[new Paragraph({children:[run(text,{bold:header,size:20})],alignment:header?AlignmentType.CENTER:block.numeric.includes(i)?AlignmentType.RIGHT:AlignmentType.LEFT,spacing:{after:40,line:260}})]}))});
      children.push(new Table({width:{size:100,type:WidthType.PERCENTAGE},borders:{top:border,bottom:border,left:border,right:border,insideHorizontal:border,insideVertical:border},rows:[row(block.headers,true),...block.rows.map(cells => row(cells))]}));
      children.push(paragraph(''));
    }
  }
  children.push(paragraph(''));
  for (const text of report.signatures) children.push(paragraph(text,{alignment:AlignmentType.RIGHT,spacing:{before:120,after:120},keepNext:!text.startsWith('落款日期')}));
  const document = new Document({creator:'中企鸿昌代账台',title:report.title,description:'企业税务清算及财产清偿报告',styles:{default:{document:{run:{font:{name:'SimSun',eastAsia:'宋体'},size:22}}}},sections:[{properties:{page:{size:{width:11906,height:16838},margin:{top:1134,bottom:1134,left:1134,right:1134}}},footers:{default:new Footer({children:[new Paragraph({alignment:AlignmentType.CENTER,children:[run('第 '),new TextRun({children:[PageNumber.CURRENT]}),run(' 页') ]})]})},children}]});
  return Packer.toBlob(document);
}
export async function downloadWord(report) {
  const blob = await createWordBlob(report), {saveAs} = await import('./vendor/word.js');
  const name = `${report.sample?'示例-':''}${report.companyName}`.replace(/[\\/:*?"<>|\r\n]/g,'_').slice(0,140);
  const filename = `${name}-税务清算及财产清偿报告.docx`;
  saveAs(blob,filename);
}
export function printReport() {window.print();}
