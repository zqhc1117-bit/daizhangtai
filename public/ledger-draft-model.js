/* Isolated working papers. No vouchers, movements or account writes. */
import {productVariants,expenseVariants,explicitVariant} from './ledger-draft-catalog.js';
import {expenseAliases,expenseItem,isExpense,ledgerPresentation} from './ledger-draft-expenses.js';
import {householdVariants,householdGroups} from './ledger-draft-household.js';
export const MAX_FEN = 999999999999;
export const MAX_ROWS = 80;
export const SUBJECTS = ['其他应收款','其他应付款','库存商品','固定资产','管理费用'];

export function draftAccounts(book) {
  const expected = ['1221','2241','1405','1601',book.client.standard === 'small2013' ? '5602' : '6602'];
  return SUBJECTS.map((name, i) => {
    const matches = (book.accounts || []).filter(a => a.name === name && !a.disabled);
    const account = matches.find(a => a.code === expected[i]) || matches.sort((a,b) => a.code.length-b.code.length)[0];
    return {name, code:account?.code || '', direction:account?.direction === 'credit' ? 'credit' : account?.direction === 'debit' ? 'debit' : i === 1 ? 'credit' : 'debit'};
  });
}

export function parseMoney(value) {
  const text = String(value ?? '').trim();
  if (!/^\d+(\.\d{1,2})?$/.test(text)) throw new Error('金额须为非负数，最多两位小数（不含千分位）');
  const [whole, fraction = ''] = text.split('.');
  const result = Number(whole) * 100 + Number(fraction.padEnd(2,'0'));
  if (!Number.isSafeInteger(result) || result > MAX_FEN) throw new Error('金额超出上限 9,999,999,999.99 元');
  return result;
}
export function yuan(fen) {
  if (!Number.isSafeInteger(fen)) throw new Error('金额必须精确到分');
  const absolute = Math.abs(fen);
  return `${fen < 0 ? '-' : ''}${Math.floor(absolute/100)}.${String(absolute%100).padStart(2,'0')}`;
}
export function quantity(value) {
  if (!/^\d+$/.test(String(value)) || Number(value) < 1 || Number(value) > 1000000) throw new Error('数量须为 1 至 1,000,000 的整数');
  return Number(value);
}
export function rowAmount(row) {
  const result = quantity(row.quantity) * parseMoney(row.price);
  if (!Number.isSafeInteger(result) || result > MAX_FEN) throw new Error('单项金额超出允许范围');
  return result;
}
export function keywords(value,account) {
  const segments=String(value).split(/[，,、;；\n|]+/).map(s=>s.trim()).filter(Boolean);
  const values = [...new Set(segments.flatMap(segment=>{
    const parts=segment.split('/').map(s=>s.trim());
    // Keep specification slashes, such as 16GB / 512GB, within one item.
    return parts.length>1&&parts.every(part=>knownKeywords.has(part))?parts:[segment];
  }))];
  if (!values.length) throw new Error(isExpense(account)?'请输入费用项目或业务关键词':'请输入商品或业务关键词');
  if (values.length > 20 || values.some(s => s.length > 80)) throw new Error('最多 20 个关键词，每个不超过 80 字');
  return values;
}

// Local reference weights, not market quotations. Users review every generated line.
const catalog = [
  [['办公设备'], [['笔记本电脑','16GB / 512GB','台',450000],['激光打印机','A4 黑白','台',150000],['显示器','24 英寸','台',80000],['办公椅','可调节','把',35000],['文件柜','双门钢制','组',60000],['碎纸机','桌面型','台',40000]]],
  [['办公家具'], [['办公桌','1200×600mm / 木质桌面','张',65000],['办公椅','可升降 / 网布靠背','把',35000],['会议椅','金属椅架 / 软垫','把',18000],['文件柜','双门钢制','组',60000],['活动柜','三抽屉 / 带轮','个',30000]]],
  [['耗材','办公耗材','文具','办公用品'], [['复印纸','A4 / 80g / 500张','包',2500],['中性笔','0.5mm / 12支','盒',1800],['文件夹','A4 双夹','个',850],['硒鼓','型号待核对','个',15000],['便利贴','76×76mm','本',350],['封箱胶带','48mm','卷',650]]],
  [['化妆品','护肤品'], [['洁面乳','100g','支',4500],['爽肤水','150ml','瓶',6500],['保湿乳液','100ml','瓶',8000],['面霜','50g','瓶',9500],['面膜','5片装','盒',3500],['防晒乳','50ml','支',7000]]],
  [['日用品','清洁用品'], [['洗手液','500ml','瓶',1500],['抽纸','3层 / 120抽','包',550],['垃圾袋','45×50cm / 30只','卷',650],['清洁剂','500ml','瓶',1250],['抹布','30×30cm','条',450]]],
  [['电子产品','电脑配件'], [['键盘','USB','个',8500],['鼠标','无线','个',5500],['移动硬盘','1TB','个',35000],['USB 扩展坞','4口','个',6500],['网线','六类 / 3米','根',1500]]],
  [['五金','五金配件'], [['螺栓','M8 / 50只','盒',2500],['螺母','M8 / 100只','盒',1800],['平垫圈','M8 / 100只','包',1000],['铰链','不锈钢 / 2只','副',1500],['角码','40mm / 4只','包',1200]]],
  [['食品','休闲食品'], [['苏打饼干','400g','袋',1500],['混合坚果','500g','袋',4500],['燕麦片','1kg','袋',2500],['纯牛奶','250ml / 12盒','箱',3600],['茶叶','100g','罐',5000]]],
  [['服装','纺织品'], [['纯棉T恤','尺码待核对','件',3500],['休闲长裤','尺码待核对','条',6500],['针织衫','尺码待核对','件',8000],['衬衫','尺码待核对','件',5500],['棉袜','均码 / 3双','组',1500]]],
  [['管理费用','业务费用'], [['办公用品费','用途：日常办公；所属期间待核对','项',50000],['通信费','用途：办公通信；所属期间待核对','项',30000],['软件服务费','用途：软件使用；所属期间待核对','项',100000],['维修维护费','用途：设备维护；所属期间待核对','项',60000]]],
];
const allCatalogs=[...productVariants,...householdVariants,...householdGroups,...expenseVariants,...catalog];
const knownKeywords=new Set([...allCatalogs.flatMap(([aliases])=>aliases),...expenseAliases]);
const concreteCatalog=[...productVariants,...householdVariants,...expenseVariants,...[...householdGroups,...catalog].flatMap(([,rows])=>rows.map(row=>[[row[0]],[row]]))];

function candidateGroups(words, account) {
  const isBalance = ['其他应收款','其他应付款'].includes(account.name);
  const groups=words.map(word => {
    if (isBalance) return [{category:word,name:word,spec:'按输入性质及对象列示；往来依据、账龄待核对；金额仅预算分配',unit:'笔',weight:1,unpriced:true}];
    const entry=allCatalogs.find(([aliases])=>aliases.includes(word));
    const explicit=entry?null:explicitVariant(word,concreteCatalog);
    let rows;
    if(entry)rows=entry[1].map(([name,spec,unit,weight])=>({category:word,name,spec,unit,weight,reference:true}));
    else if(explicit)rows=[{category:word,name:word,spec:`原文规格：${word}；其余规格待核对`,unit:explicit[2],weight:explicit[3],reference:true}];
    else rows=[{category:word,unknown:true}];
    return rows.map(row=>{
      if(row.unknown)return row;
      if(account.name==='固定资产')return {...row,spec:`配置：${row.spec}；使用部门/地点待核对`};
      return row;
    });
  });
  return groups;
}

function minimumAmounts(items) {
  return items.map((item,index)=>!item.reference?1:index===items.length-1?Math.ceil(item.weight/2):Math.ceil(item.weight/100)*50);
}
function selectCandidates(groups,total) {
  const order=groups.map(group=>group.map((_,index)=>index).sort((a,b)=>group[a].weight-group[b].weight||a-b));
  const selected=groups.map((_,index)=>new Set([order[index][0]]));
  const flatten=()=>groups.flatMap((group,index)=>group.filter((_,row)=>selected[index].has(row)));
  const minimum=minimumAmounts(flatten()).reduce((sum,value)=>sum+value,0);
  if(total<minimum)throw new Error(`该品类的自动拆分金额过低（每个关键词至少保留一项，估算至少 ${yuan(minimum)} 元）。请改用具体商品关键词，或通过“添加明细”按实际数量和单价录入。`);
  // Add the next affordable subtype to each category in turn. Cheap subtypes
  // are considered first; final output retains the catalogue's stable order.
  for(let level=1;level<Math.max(...groups.map(group=>group.length));level++){
    for(let group=0;group<groups.length;group++){
      const candidate=order[group][level];if(candidate===undefined)continue;
      // A final fine category may need one extra row for a cents remainder;
      // keep its complete output within the promised two or three subitems.
      if(group===groups.length-1&&groups[group].length<=3&&selected[group].size>=2)continue;
      selected[group].add(candidate);
      const items=flatten();
      if(items.length>MAX_ROWS-1||minimumAmounts(items).reduce((sum,value)=>sum+value,0)>total)selected[group].delete(candidate);
    }
  }
  return flatten();
}

export function generateRows(totalText, keywordText, account) {
  if (!account?.code || !SUBJECTS.includes(account.name)) throw new Error('当前账套缺少所选科目，请先维护科目表');
  const total = parseMoney(totalText), words = keywords(keywordText,account);
  if (!total) throw new Error('总金额须大于 0');
  if (total < words.length && (isExpense(account)||['其他应收款','其他应付款'].includes(account.name))) throw new Error('总金额不足以为每个关键词分配至少 0.01 元');
  if (isExpense(account)) {
    // Expenses supplied only as a total have no market unit price or inferred
    // headcount. Allocate cents transparently; users supply actual composition.
    const base=Math.floor(total/words.length), remainder=total%words.length;
    return words.map((word,index)=>{
      const {category,name,spec,unit}=expenseItem(word);
      return {category,name,spec,unit,quantity:'1',price:yuan(base+(index<remainder?1:0))};
    });
  }
  const groups=candidateGroups(words,account);
  const unknown=groups.flat().filter(item=>item.unknown).map(item=>item.category);
  if(unknown.length)throw new Error(`无法识别商品关键词：${unknown.join('、')}。请改用明确品类或通过“添加明细”填写实际名称、规格、单位、数量及单价。未生成新拟稿，原草稿已保留。`);
  const items=selectCandidates(groups,total);
  // Earlier rows use 0.50 price increments; only the final row absorbs cents.
  const minimums = minimumAmounts(items);
  const minimum = minimums.reduce((sum,value) => sum+value,0);
  if (total < minimum) throw new Error(`该品类的自动拆分金额过低（估算至少 ${yuan(minimum)} 元）。请改用具体商品关键词，或通过“添加明细”按实际数量和单价录入。`);
  let remaining = total, minimumLeft = minimum, weightLeft = items.reduce((sum,item) => sum+item.weight,0);
  return items.flatMap((item,index) => {
    let amount, count = 1;
    if (index === items.length-1) {
      amount = remaining;
      count = item.unpriced?1:suitableQuantity(amount,item.weight,item.reference);
      if (!count) {
        // Keep a reference-priced group plus one individual estimate for the
        // cents remainder. Both are editable balance items, never movements.
        let bulk = Math.floor(amount/item.weight), tail = amount-bulk*item.weight;
        if (tail < item.weight/2) {bulk--;tail+=item.weight;}
        if (bulk < 1 || bulk > 1000000 || tail < item.weight/2 || tail > item.weight*2) throw new Error('总金额不适合该品类估算，请改用具体商品关键词或手工录入明细');
        const base={category:item.category,name:item.name,spec:item.spec,unit:item.unit};
        return [{...base,quantity:String(bulk),price:yuan(item.weight)},{...base,spec:`${item.spec}（单独估价待核对）`,quantity:'1',price:yuan(tail)}];
      }
    } else {
      const budget = Math.max(minimums[index],Math.min(remaining-(minimumLeft-minimums[index]), Number(BigInt(remaining)*BigInt(item.weight)/BigInt(weightLeft))));
      count = item.unpriced?1:Math.max(1,Math.min(1000000,Math.floor(budget/item.weight)));
      const price = !item.unpriced&&budget >= item.weight ? item.weight : budget >= 50 ? Math.floor(budget/50)*50 : budget;
      amount = count*price;
      if (item.reference && price < item.weight/2) throw new Error('总金额不足以合理覆盖这些品类，请改用具体商品关键词或手工录入明细');
    }
    remaining -= amount; minimumLeft -= minimums[index]; weightLeft -= item.weight;
    return {category:item.category,name:item.name,spec:item.spec,unit:item.unit,quantity:String(count),price:yuan(amount/count)};
  });
}

function suitableQuantity(amount,reference,limited,preferredCount=null) {
  const low=limited?Math.max(1,Math.ceil(amount/(reference*2))):1;
  const high=limited?Math.min(1000000,Math.floor(amount/(reference/2))):1000000;
  let result=0, distance=Infinity;
  const consider=count=>{if(count>=low&&count<=high){const delta=preferredCount===null?Math.abs(amount/count-reference):Math.abs(count-preferredCount);if(delta<distance){result=count;distance=delta;}}};
  // Divisor pairs preserve whole quantities and two-decimal prices exactly.
  for(let divisor=1;divisor<=Math.min(Math.sqrt(amount),1000000);divisor++)if(amount%divisor===0){consider(divisor);consider(amount/divisor);}
  return result;
}

export function summarize(draft,account=draft.accountName) {
  const presentation=ledgerPresentation(account);
  const errors = [];
  let target = 0, total = 0, count = 0;
  try {target = parseMoney(draft.total); if (!target) errors.push('总金额须大于 0');} catch (error) {errors.push(error.message);}
  if (!draft.rows?.length) errors.push('请先生成或添加明细');
  if (draft.rows?.length > MAX_ROWS) errors.push(`最多 ${MAX_ROWS} 行明细`);
  for (const [index,row] of (draft.rows || []).entries()) {
    try {
      if (['category','name','spec','unit'].some(key => !String(row[key] ?? '').trim())) throw new Error(presentation.incomplete);
      if (['category','name','spec','unit'].some(key => String(row[key]).length > 160)) throw new Error('文字字段最多 160 字');
      const amount = rowAmount(row);
      if (!amount) throw new Error(`${presentation.fields.at(-1)}须大于 0`);
      total += amount; count += quantity(row.quantity);
    } catch (error) {errors.push(`第 ${index+1} 行：${error.message}`);}
  }
  if (!Number.isSafeInteger(total) || total > MAX_FEN) errors.push('明细合计超出金额上限');
  try {
    const missing = keywords(draft.keywords,account).filter(word => !(draft.rows || []).some(row => `${row.category} ${row.name}`.includes(word)));
    if (missing.length) errors.push(`明细未覆盖关键词：${missing.join('、')}`);
  } catch (error) {errors.push(error.message);}
  const difference = target-total;
  return {target,total,count,difference,errors,balanced:errors.length===0 && difference===0,unit:new Set((draft.rows || []).map(r => r.unit)).size > 1 ? '混合单位（数值合计）' : draft.rows?.[0]?.unit || '—'};
}

export function balanceLastRow(draft) {
  const priceLabel=isExpense(draft.accountName)?'单项金额':'单价';
  if (!draft.rows?.length) throw new Error('请先添加明细');
  const before = draft.rows.slice(0,-1).reduce((sum,row) => sum+rowAmount(row),0);
  const rest = parseMoney(draft.total)-before;
  if (rest < 1) throw new Error(`前面明细已达到或超过总金额，请先调整数量或${priceLabel}`);
  const last = draft.rows.at(-1), count = quantity(last.quantity);
  let nextCount = count;
  if (rest % count !== 0) {
    const reference = parseMoney(last.price);
    if (!reference) throw new Error(`末行原${priceLabel}须大于 0，请先填写实际${priceLabel}再配平`);
    nextCount = suitableQuantity(rest,reference,true,count);
    if (!nextCount) throw new Error(`未找到接近末行原${priceLabel}的可整除数量，请手工调整数量、${priceLabel}或前面明细后再配平`);
  }
  return [...draft.rows.slice(0,-1),{...last,quantity:String(nextCount),price:yuan(rest/nextCount)}];
}

export function draftKey(book,ctx) {
  return `ledger-draft:v1:${ctx.user?.id ?? 'local'}:${book.client.id}:${book.client.standard}:${book.period}`;
}
