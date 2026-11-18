// Monetary state is entered in yuan; all calculations and totals use integer fen.
export const MAX_FEN = 999999999999;
export function scaled(value, digits = 2) {
  const text = String(value ?? '').trim().replace(/[,，\s¥￥]/g, '') || '0';
  if (!new RegExp(`^-?\\d+(?:\\.\\d{0,${digits}})?$`).test(text)) throw new Error(`请输入数字，最多保留${digits}位小数`);
  const [whole, fraction = ''] = text.replace(/^-/, '').split('.');
  const result = (BigInt(whole) * 10n ** BigInt(digits) + BigInt(fraction.padEnd(digits, '0') || '0')) * (text.startsWith('-') ? -1n : 1n);
  if (result > BigInt(MAX_FEN) || result < -BigInt(MAX_FEN)) throw new Error('金额或比例超出允许范围');
  return Number(result);
}
export const toFen = value => scaled(value, 2);
export function amount(value) { try { return toFen(value); } catch { return 0; } }
export const yuan = value => `${value < 0 ? '-' : ''}${Math.floor(Math.abs(value) / 100)}.${String(Math.abs(value) % 100).padStart(2, '0')}`;
export const money = value => yuan(value).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
export const sum = values => values.reduce((total, value) => total + value, 0);
export function taxAt(fen, rate) { return Number((BigInt(Math.max(0, fen)) * BigInt(rate) + 50n) / 100n); }
export const SHARE_SCALE = 1000000;
export function shareUnits(value) { try { return scaled(value, 6); } catch { return 0; } }
export function allocate(fen, shareholders) {
  const units = shareholders.map(row => shareUnits(row.ratio));
  if (fen < 0 || units.some(n => n < 0) || sum(units) !== 100 * SHARE_SCALE) return shareholders.map(() => 0);
  const denominator = BigInt(100 * SHARE_SCALE);
  const raw = units.map((n, index) => ({index, product: BigInt(fen) * BigInt(n)}));
  const values = raw.map(row => Number(row.product / denominator));
  let left = fen - sum(values);
  const order = raw.filter(row => units[row.index] > 0).sort((a, b) => a.product % denominator === b.product % denominator ? a.index - b.index : a.product % denominator > b.product % denominator ? -1 : 1);
  for (let i = 0; i < left; i++) values[order[i % order.length].index]++;
  return values;
}
