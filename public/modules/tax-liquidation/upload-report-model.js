import {amount,yuan,money} from './money.js';
const p=text=>({type:'paragraph',text});
const table=(headers,rows,numeric=[])=>({type:'table',headers,rows,numeric});
const label={asset:'资产',liability:'负债',equity:'所有者权益'};
export function statementSummary(statement){
  const subtotal=group=>statement.entries.filter(e=>e.group===group).reduce((s,e)=>s+amount(e.value),0);
  const totals=statement.sourceTotals||{},provided=key=>Object.hasOwn(totals,key);
  const assets=provided('assetsTotal')?amount(totals.assetsTotal):subtotal('asset');
  const liabilities=provided('liabilitiesTotal')?amount(totals.liabilitiesTotal):subtotal('liability');
  const equity=provided('equityTotal')?amount(totals.equityTotal):subtotal('equity');
  const otherSide=provided('liabilitiesEquityTotal')?amount(totals.liabilitiesEquityTotal):liabilities+equity;
  const warnings=[...(statement.warnings||[])];
  for(const [key,group] of [['assetsTotal','asset'],['liabilitiesTotal','liability'],['equityTotal','equity']]){
    if(provided(key)&&amount(totals[key])!==subtotal(group))warnings.push(`原表${label[group]}总计与已识别明细合计相差 ${money(amount(totals[key])-subtotal(group))} 元，请检查遗漏项目。`);
  }
  const hasBoth=statement.entries.some(e=>e.group==='asset')&&statement.entries.some(e=>e.group==='liability'||e.group==='equity');
  const difference=assets-otherSide;
  if(!hasBoth)warnings.push('缺少资产或负债及权益一侧数据，暂不能完成平衡核对。');
  else if(difference!==0)warnings.push(`资产与负债及权益不平，差额 ${money(difference)} 元。`);
  const value=key=>{const entry=statement.entries.find(e=>e.key===key);return entry?amount(entry.value):null;};
  return {assets,liabilities,equity,difference,hasBoth,warnings:[...new Set(warnings)],value};
}
export function buildUploadReport(statement,taxpayer='small',companyName='',baseDate=''){
  if(!statement?.entries?.length)throw new Error('请先上传并识别报表。');
  const s=statementSummary(statement),name=companyName.trim()||statement.metadata?.companyName||'企业名称待补充',date=baseDate||statement.metadata?.baseDate||'待补充';
  const nature=taxpayer==='general'?'一般纳税人':'小规模纳税人';
  const show=value=>value===null?'未识别 / 待核实':money(value);
  const taxes=s.value('taxPayable'),employees=s.value('employeePayable'),cash=s.value('cash');
  const liabilities=statement.entries.filter(e=>e.group==='liability');
  const external=liabilities.filter(e=>!['taxPayable','employeePayable'].includes(e.key));
  const sections=[
    {title:'一、企业与报告依据',blocks:[
      table(['项目','内容'],[['企业名称',name],['纳税人类型',nature],['报表基准日',date],['资料来源',`${statement.fileName} · ${statement.sheetName}`],['识别报表类型',statement.kind]]),
      p('本报告根据上传报表的期末账面余额自动编制。实际变现、清算费用、税务申报及债务清偿凭证尚待补充，以下为清算清偿方案草稿。未识别金额以“待核实”列示。'),
    ]},
    {title:'二、资产、负债及所有者权益',blocks:[
      table(['分类','项目','期末账面金额（元）'],statement.entries.map(e=>[label[e.group]||'待分类',e.label,money(amount(e.value))]),[2]),
      table(['汇总项目','金额（元）','取数依据'],[['资产',money(s.assets),Object.hasOwn(statement.sourceTotals||{},'assetsTotal')?'原表资产总计':'已识别资产合计'],['负债',money(s.liabilities),Object.hasOwn(statement.sourceTotals||{},'liabilitiesTotal')?'原表负债合计':'已识别负债合计'],['所有者权益',money(s.equity),Object.hasOwn(statement.sourceTotals||{},'equityTotal')?'原表权益合计':'已识别权益合计']],[1]),
      p(!s.hasBoth?'平衡核对：资料不完整，待核实。':s.difference===0?'平衡核对：本次识别的资产与负债及权益金额相等。':`平衡核对：差额 ${money(s.difference)} 元，请对照原表复核。`),
      ...s.warnings.map(w=>p(`识别复核：${w}`)),
    ]},
    {title:'三、资产清理与变现方案',blocks:[
      p(`账面货币资金为 ${show(cash)} 元，需核对现金盘点、银行余额及冻结或受限资金。应收款项、存货、固定资产等应逐项核实可收回金额和可变现价值。`),
      table(['资产项目','账面金额（元）','清理事项'],statement.entries.filter(e=>e.group==='asset').map(e=>[e.label,money(amount(e.value)),e.key==='cash'?'核对账户余额与使用限制':'核实权属、可收回或可变现金额；留存处置依据']),[1]),
      p('本次未将非货币资产账面值视作已收回现金，未将未提供的变现额视作零。资产处置损益待实际交易价格、计税基础及相关凭证补齐后计算。'),
    ]},
    {title:'四、税务清算事项',blocks:[
      table(['事项','已知资料','待办理 / 核实内容'],[
        ['纳税人类型',nature,'按实际登记的纳税人身份核对清算期间应税业务'],
        ['账面应交税费',`${show(taxes)} 元`,'核对税种明细、申报记录、已缴及欠缴金额；负余额不直接视为欠税'],
        ['清算期增值税及附加','税额待核实',taxpayer==='general'?'核对资产处置的销项、进项及适用的计税办法':'核对应税处置收入、适用征收率及优惠条件'],
        ['清算企业所得税','税额待核实','补齐资产变现价、计税基础、清算费用、相关税费、债务清偿损益及可弥补亏损'],
        ['其他税费与股东相关税务','待核实','按实际业务及股东身份核对印花税、所得税等申报事项'],
      ]),
      p('小规模 / 一般纳税人的选择用于识别增值税身份，不据此推定企业所得税优惠、清算税率或已完税状态。清算所得与应缴税额应在资料补齐后核算。'),
    ]},
    {title:'五、债务清偿与剩余财产',blocks:[
      table(['顺序','清偿项目','已识别账面金额（元）','执行说明'],[
        ['1','清算费用','待补充','核实清算期间实际发生的必要支出'],
        ['2','职工工资、社会保险费用和法定补偿',show(employees),'另行核对未入账的工资、社保及补偿'],
        ['3','税款',show(taxes),'结合申报结果核实欠税及清算期新增税款'],
        ['4','其他公司债务',external.length?money(external.reduce((n,e)=>n+amount(e.value),0)):'未识别 / 待核实','逐项确认债权人、应偿金额、支付安排与凭证'],
      ],[2]),
      ...(external.length?[table(['债务项目','账面金额（元）','清偿状态'],external.map(e=>[e.label,money(amount(e.value)),'尚未提供支付或承接凭证']),[1])]:[]),
      p(`当前识别资产账面金额 ${money(s.assets)} 元、负债账面金额 ${money(s.liabilities)} 元，账面差额 ${money(s.assets-s.liabilities)} 元。该差额不等于可分配现金。`),
      p('清算费用、职工款项、税款及公司债务应依顺序处理。实际变现和上述支出未核实前，剩余财产及股东分配金额暂不确定；财产不足清偿债务时，应按适用程序处理。'),
    ]},
    {title:'六、清算结论及补充资料',blocks:[
      p('已依据上传报表整理资产、负债、税务核查事项与清偿安排。当前报告状态为待核实草稿，尚不能确认税务清算和债务清偿完成。'),
      p('请补充企业登记资料、清算组及股东信息、清算起止日期、资产处置与债务清偿凭证、费用清单和完税资料。复核报表识别结果后，形成实际清算结论并履行确认、签署手续。'),
    ]},
  ];
  return {schemaVersion:1,title:`关于 ${name} 税务清算及财产清偿报告`,companyName:name,mode:'upload',sample:false,status:'待核实草稿',sections,
    signatures:['清算组负责人（签字）：________________','清算组成员（签字）：________________',`${name}（公章）`,'落款日期：____年____月____日']};
}
