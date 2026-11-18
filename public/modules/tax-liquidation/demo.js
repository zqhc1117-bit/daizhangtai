import {createDraft,uid} from './schema.js';
export function sampleDraft(mode,client) {
  const draft = createDraft(client);
  draft.mode = mode;draft.sample = true;
  Object.assign(draft.company,{name:`${client.name || '演示企业'}（示例）`,creditCode:'91310000MA1K12345X',legalPerson:'张明',established:'2020-06-18',registeredCapital:'100000.00',address:'上海市示例路100号',baseDate:'2026-10-31',endDate:'2026-11-17',leader:'张明',members:'张明、李华'});
  draft.shareholders=[{id:uid(),name:'张明',type:'natural',ratio:'60',subscribed:'60000.00',paid:'30000.00'},{id:uid(),name:'示例投资有限公司',type:'corporate',ratio:'40',subscribed:'40000.00',paid:'20000.00'}];
  draft.quick={cash:'32000.00',paidCapital:'50000.00',retainedEarnings:'-20000.00',hasTax:true,taxPayable:'2000.00',capitalLinked:true};
  Object.assign(draft.settlement,{agencyFee:'1500.00',miscFee:'200.00',employeeExtra:'0',vat:'300.00',otherTax:'50.00',taxRate:'25'});
  if (mode==='full') {
    Object.assign(draft.full.balance,{cash:'80000.00',receivables:'25000.00',otherReceivables:'5000.00',prepayments:'2000.00',inventory:'30000.00',fixedAssets:'18000.00',intangibleAssets:'10000.00',payables:'20000.00',otherPayables:'10000.00',employeePayable:'5000.00',taxPayable:'3000.00',paidCapital:'50000.00',capitalReserve:'20000.00',surplusReserve:'10000.00',retainedEarnings:'52000.00'});
    for (const [key,realized,note] of [['receivables','25000.00','客户欠款全部收回'],['otherReceivables','5000.00','租赁押金收回'],['prepayments','2000.00','供应商退回预付款'],['inventory','42000.00','剩余商品打包转让'],['fixedAssets','25000.00','设备按协议转让'],['intangibleAssets','12000.00','软件使用权依法转让']]) draft.full.assets[key]={realized,note};
    draft.full.debts.payables={method:'paid',paid:'',note:'按清偿协议全额支付供应商',assignee:'',consent:false};
    draft.full.debts.otherPayables={method:'partial',paid:'6000.00',note:'债权人已签署书面协议，同意免除4000元余额',assignee:'',consent:false};
    Object.assign(draft.settlement,{employeeExtra:'3000.00',vat:'2000.00',otherTax:'200.00'});
  }
  return draft;
}
