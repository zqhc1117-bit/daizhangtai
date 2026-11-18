import {amount} from '../money.js';
export function calculateQuick(quick) {
  const balance = {cash:amount(quick.cash),paidCapital:amount(quick.paidCapital),retainedEarnings:amount(quick.retainedEarnings),taxPayable:quick.hasTax ? amount(quick.taxPayable) : 0};
  return {balance,assetsTotal:balance.cash,liabilitiesTotal:balance.taxPayable,equityTotal:balance.paidCapital+balance.retainedEarnings,
    assetRows:[],debtRows:[],realizedTotal:0,disposalGain:0,debtIncome:0,externalPaid:0,assumedTotal:0,pending:[]};
}
