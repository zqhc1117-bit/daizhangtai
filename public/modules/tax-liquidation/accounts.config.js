export const ASSETS = [
  {key:'cash',label:'货币资金',hint:'银行卡、现金及微信/支付宝余额',aliases:['货币资金']},
  {key:'receivables',label:'应收账款',hint:'客户还欠公司的货款',aliases:['应收账款','应收帐款']},
  {key:'otherReceivables',label:'其他应收款',hint:'借出款、押金等还没收回的钱',aliases:['其他应收款','其它应收款']},
  {key:'prepayments',label:'预付账款',hint:'已付给供应商、还未结算的钱',aliases:['预付账款','预付款项']},
  {key:'inventory',label:'存货',hint:'剩余商品、材料和产成品',aliases:['存货']},
  {key:'fixedAssets',label:'固定资产',hint:'设备、车辆等扣除折旧后的净值',aliases:['固定资产净值','固定资产账面价值','固定资产']},
  {key:'intangibleAssets',label:'无形资产',hint:'软件、专利等摊销后的净值',aliases:['无形资产净值','无形资产']},
];
export const LIABILITIES = [
  {key:'payables',label:'应付账款',hint:'还欠供应商的钱',aliases:['应付账款','应付帐款']},
  {key:'otherPayables',label:'其他应付款',hint:'借款、暂收款等尚未还的钱',aliases:['其他应付款','其它应付款']},
  {key:'employeePayable',label:'应付职工薪酬',hint:'基准日前已入账的欠薪、社保等',aliases:['应付职工薪酬']},
  {key:'taxPayable',label:'应交税费',hint:'基准日尚未缴清的税款',aliases:['应交税费','应交税金']},
];
export const EQUITY = [
  {key:'paidCapital',label:'实收资本',hint:'股东已经实际投入的本钱',aliases:['实收资本（或股本）','实收资本','股本']},
  {key:'capitalReserve',label:'资本公积',hint:'资本溢价等形成的积累',aliases:['资本公积']},
  {key:'surplusReserve',label:'盈余公积',hint:'历年从利润中提取的积累',aliases:['盈余公积']},
  {key:'retainedEarnings',label:'未分配利润',hint:'历年累计盈亏，亏损填负数',aliases:['未分配利润']},
];
export const ACCOUNTS = [...ASSETS, ...LIABILITIES, ...EQUITY];
export const DISPOSABLE_ASSETS = ASSETS.filter(row => row.key !== 'cash');
export const EXTERNAL_DEBTS = LIABILITIES.filter(row => !['employeePayable','taxPayable'].includes(row.key));
export const DEBT_METHODS = {
  paid:'公司资金全额还清', waived:'债主书面同意免除（无需偿还）',
  assumed:'股东个人承诺兜底承接', partial:'部分偿还并取得豁免',
};
