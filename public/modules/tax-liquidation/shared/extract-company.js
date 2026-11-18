import {yuan} from '../money.js';
const LABELS = '企业名称|公司名称|统一社会信用代码|社会信用代码|法定代表人|成立日期|成立时间|注册资本|登记状态|经营状态|注册地址|地址|曾用名|核准日期|经营范围|纳税人识别号|人员规模|所属行业|营业期限|登记机关|实缴资本';
const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
function labeled(text, labels) {
  return new RegExp(`(?:^|[\\s:：;；])(?:${labels.map(escape).join('|')})\\s*[:：]?\\s*([\\s\\S]*?)(?=\\s*(?:${LABELS})\\s*[:：]?|[\\r\\n;；]|$)`,'m').exec(text)?.[1]?.trim() || '';
}
export function extractCompany(text) {
  const result = {}, normalized = String(text).replace(/\u00a0/g,' ');
  const name = labeled(normalized,['企业名称','公司名称']) || normalized.split(/\r?\n/).map(line => line.trim()).find(line => /^[\u4e00-\u9fffA-Za-z0-9（）()·]+(?:有限公司|有限责任公司|股份公司|合伙企业(?:（[^）]+）)?)$/.test(line));
  if (name) result.name = name;
  const code = /(?:统一社会信用代码|社会信用代码|纳税人识别号)\s*[:：]?\s*([0-9A-HJ-NPQRTUWXY]{18})/i.exec(normalized)?.[1] || /\b[0-9A-HJ-NPQRTUWXY]{18}\b/i.exec(normalized)?.[0];
  if (code) result.creditCode = code.toUpperCase();
  const person = labeled(normalized,['法定代表人']).replace(/\s*(?:关联企业|任职).*$/,'').trim();
  if (person && person.length<=40) result.legalPerson = person;
  const date = /(?:成立日期|成立时间)\s*[:：]?\s*(\d{4})\s*[-年/.]\s*(\d{1,2})\s*[-月/.]\s*(\d{1,2})/.exec(normalized);
  if (date) result.established = `${date[1]}-${date[2].padStart(2,'0')}-${date[3].padStart(2,'0')}`;
  const capital = /注册资本\s*[:：]?\s*([\d,，]+(?:\.\d+)?)\s*(亿元?|万元?|元)?\s*(?:人民币|RMB|CNY)?/i.exec(normalized);
  if (capital) {
    const clean = capital[1].replace(/[,，]/g,''), [whole,fraction=''] = clean.split('.'), multiplier = capital[2]?.startsWith('亿') ? 100000000n : capital[2]?.startsWith('万') ? 10000n : 1n;
    if (fraction.length<=8) {
      const divisor = 10n ** BigInt(fraction.length), numerator = BigInt(whole+fraction)*multiplier*100n;
      const cents = (numerator+divisor/2n)/divisor;
      if (cents<=999999999999n) result.registeredCapital = yuan(Number(cents));
    }
  }
  return result;
}
