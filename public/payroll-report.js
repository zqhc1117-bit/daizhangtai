// One report model shared by the preview, printing and Excel export. Money stays in fen.
const socialKeys=['pension_fen','medical_fen','unemployment_fen'];
export const legacySocialNote='部分历史记录仅保存社保合计，养老、医保、失业明细待补填。';

export function payrollPeriodLabel(period){
  const match=/^(\d{4})-(0[1-9]|1[0-2])$/.exec(period);
  if(!match)throw new Error('请选择有效的工资所属月份');
  return `${match[1]}年${Number(match[2])}月`;
}

function fen(value){
  const result=Number(value??0);
  if(!Number.isSafeInteger(result)||result<0)throw new Error('工资金额须为有效的非负整数分');
  return result;
}

export function payrollReport(book,records=book.payroll||[]){
  const periodLabel=payrollPeriodLabel(book.period);
  const people=records.filter(p=>p.period===book.period&&(p.client_id==null||Number(p.client_id)===Number(book.client.id)));
  const hasOther=people.some(p=>fen(p.other_deduction_fen)>0);
  const definitions=[
    ['sequence','序号',7],['employee_name','姓名',12],['gross_fen','工资总额',15],
    ['pension_fen','养老',13],['medical_fen','医保',12],['unemployment_fen','失业',10],['social_fen','小计',13],
    ['housing_fund_fen','扣公积金\n自付部分',12],
    ...(hasOther?[['other_deduction_fen','其他扣款',13]]:[]),
    ['payable_fen','应发工资',17],['withheld_tax_fen','扣个税',13],['net_fen','实发工资',15],
  ];
  const columns=definitions.map(([key,label,width],index)=>({key,label,width,money:index>1}));
  let hasLegacySocial=false;
  const rows=people.map((p,index)=>{
    const amounts={};
    for(const key of ['gross_fen',...socialKeys,'social_fen','housing_fund_fen','other_deduction_fen','withheld_tax_fen'])amounts[key]=fen(p[key]);
    amounts.payable_fen=amounts.gross_fen-amounts.social_fen-amounts.housing_fund_fen-amounts.other_deduction_fen;
    amounts.net_fen=amounts.payable_fen-amounts.withheld_tax_fen;
    if(amounts.net_fen<0)throw new Error('工资扣款合计不能超过工资总额');
    if(amounts.social_fen>0&&socialKeys.every(key=>amounts[key]===0)){
      hasLegacySocial=true;
      for(const key of socialKeys)amounts[key]=null;
    }
    return {person:p,values:columns.map(c=>c.key==='sequence'?index+1:c.key==='employee_name'?p.employee_name:amounts[c.key])};
  });
  const totals=columns.map((c,index)=>{
    if(!c.money)return index===0?'合计':'';
    if(rows.some(row=>row.values[index]===null))return null;
    const total=rows.reduce((sum,row)=>sum+row.values[index],0);
    if(!Number.isSafeInteger(total))throw new Error('工资合计超出可精确导出的金额范围');
    return total;
  });
  return {title:`${book.client.name}工资表`,period:book.period,periodLabel,columns,rows,totals,hasOther,hasLegacySocial};
}
