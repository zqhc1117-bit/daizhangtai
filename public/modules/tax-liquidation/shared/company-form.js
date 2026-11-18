import {field,select,textarea,section,h} from './fields.js';
export function renderCompanyForm(draft) {
  const c = draft.company;
  const paste = section('粘贴工商信息，一键带入','支持企查查、天眼查等复制的整段文字；提取后可继续修改。',`${textarea('paste','工商信息原文',draft.paste)}<button type="button" class="tliq-button tliq-soft" data-tliq-action="extract">一键提取</button>`);
  const basic = section('企业与清算信息','先说明是哪家公司、为什么关门、清算到哪一天。',`<div class="tliq-fields">
    ${field('company.name','企业名称',c.name,{full:true})}${field('company.creditCode','统一社会信用代码',c.creditCode,{hint:'营业执照上的18位代码'})}${field('company.legalPerson','法定代表人',c.legalPerson)}
    ${field('company.established','成立日期',c.established,{type:'date'})}${field('company.registeredCapital','注册资本',c.registeredCapital,{money:true,hint:'工商登记金额，统一按元录入'})}
    ${field('company.address','注册地址',c.address,{full:true})}${select('company.reason','解散事由',c.reason,['股东会决议解散','营业期限届满','吊销','其他'].map(v => [v,v]))}
    ${c.reason === '其他' ? field('company.reasonOther','其他解散事由',c.reasonOther) : ''}
    ${field('company.baseDate','清算基准日',c.baseDate,{type:'date',hint:'决定关门、盘点家底的日子'})}${field('company.endDate','清算结束日',c.endDate,{type:'date'})}
    ${field('company.leader','清算组负责人',c.leader)}${field('company.members','清算组成员',c.members,{hint:'多人以顿号、逗号或换行分隔',full:true})}
    </div>`);
  const rows = draft.shareholders.map((row,i) => `<tr><td>${i+1}</td><td><input data-tliq-path="shareholders.${i}.name" aria-label="股东${i+1}名称" value="${h(row.name)}" maxlength="160"></td><td><select data-tliq-path="shareholders.${i}.type" aria-label="股东${i+1}性质"><option value="natural" ${row.type==='natural'?'selected':''}>自然人</option><option value="corporate" ${row.type==='corporate'?'selected':''}>法人企业</option></select></td>${[['ratio','持股比例'],['subscribed','认缴出资额'],['paid','实缴出资额']].map(([key,label]) => `<td><input inputmode="decimal" maxlength="24" data-tliq-path="shareholders.${i}.${key}" aria-label="股东${i+1}${label}" value="${h(row[key])}"></td>`).join('')}<td><button type="button" class="tliq-link tliq-danger-text" data-tliq-action="remove-shareholder" data-tliq-index="${i}" ${draft.shareholders.length === 1?'disabled':''} aria-label="删除股东${i+1}">删除</button></td></tr>`).join('');
  return paste+basic+section('股东名册','认缴是约定投入的钱；实缴是已经实际投入的钱。比例合计应为100%。',`<div class="tliq-table-scroll"><table class="tliq-input-table"><thead><tr><th>#</th><th>股东名称</th><th>股东性质</th><th>持股比例 %</th><th>认缴出资 元</th><th>实缴出资 元</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div><div class="tliq-card-footer"><button type="button" class="tliq-button" data-tliq-action="add-shareholder" ${draft.shareholders.length>=80?'disabled':''}>＋ 添加股东</button><div data-tliq-live="share-total"></div></div>`);
}
