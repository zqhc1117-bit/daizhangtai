import {field,section} from '../shared/fields.js';
export function renderQuickForm(draft) {
  const q = draft.quick;
  return section('关门时，还剩多少钱？','极速模式适用于只有货币资金、实收资本、累计盈亏及欠税的企业。有其他资产或债务时，请切换完整模式。',`<div class="tliq-fields tliq-single">
    ${field('quick.cash','银行卡及微信/支付宝/现金余额',q.cash,{hint:'货币资金：把所有账户里的钱加起来',money:true})}
    ${field('quick.paidCapital','股东实际打入公司的本钱',q.paidCapital,{hint:'实收资本：默认跟随股东实缴合计，可手动微调',money:true})}
    <div class="tliq-inline-hint"><span data-tliq-live="capital-link"></span> <button type="button" class="tliq-link" data-tliq-action="link-capital">重新带入实缴合计</button></div>
    ${field('quick.retainedEarnings','账面累计盈亏',q.retainedEarnings,{hint:'未分配利润：赚了填正数，亏了填负数',money:true})}
    <fieldset class="tliq-radio-group"><legend>基准日是否还有未交清的税款？</legend><label><input type="radio" name="tliq-has-tax" data-tliq-path="quick.hasTax" data-tliq-bool="false" value="false" ${!q.hasTax?'checked':''}> 无</label><label><input type="radio" name="tliq-has-tax" data-tliq-path="quick.hasTax" data-tliq-bool="true" value="true" ${q.hasTax?'checked':''}> 有</label></fieldset>
    ${q.hasTax?field('quick.taxPayable','基准日未交税费',q.taxPayable,{hint:'关门前已经欠下、还没有缴清的税',money:true}):''}
    </div><div data-tliq-live="balance"></div><p class="tliq-note">极速模式自动跳过第3步“卖货与还债”，直接进入善后费用与分配。</p>`);
}
