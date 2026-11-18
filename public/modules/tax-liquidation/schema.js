import {ACCOUNTS, DISPOSABLE_ASSETS, EXTERNAL_DEBTS} from './accounts.config.js';
import {resolveMode} from './modes.config.js';
export const uid = () => globalThis.crypto?.randomUUID?.() || `row-${Date.now()}-${Math.random().toString(36).slice(2)}`;
export function shareholder() { return {id:uid(),name:'',type:'natural',ratio:'100',subscribed:'',paid:''}; }
export function createDraft(client = {}) {
  return {
    version:1, mode:resolveMode('quick'), step:1, sample:false, paste:'',
    company:{name:client.name || '',creditCode:client.tax_no || client.credit_code || '',legalPerson:client.legal_person || '',established:'',registeredCapital:'',address:client.address || '',reason:'股东会决议解散',reasonOther:'',baseDate:'',endDate:'',leader:'',members:''},
    shareholders:[shareholder()],
    quick:{cash:'',paidCapital:'',retainedEarnings:'',hasTax:false,taxPayable:'',capitalLinked:true},
    full:{balance:Object.fromEntries(ACCOUNTS.map(row => [row.key,''])),
      assets:Object.fromEntries(DISPOSABLE_ASSETS.map(row => [row.key,{realized:'',note:''}])),
      debts:Object.fromEntries(EXTERNAL_DEBTS.map(row => [row.key,{method:'paid',paid:'',note:'',assignee:'',consent:false}]))},
    settlement:{agencyFee:'',miscFee:'',employeeExtra:'',vat:'',otherTax:'',taxRate:'25',manualTax:'',exemptionBasis:'',taxes:{vat:true,income:true,personal:true,other:true}},
  };
}
// Whitelist persisted fields. Unknown properties never become form fields or HTML.
function mergeShape(shape, value) {
  if (typeof shape === 'string') return typeof value === 'string' ? value.slice(0,10000) : shape;
  if (typeof shape === 'boolean') return typeof value === 'boolean' ? value : shape;
  if (typeof shape === 'number') return typeof value === 'number' && Number.isFinite(value) ? value : shape;
  if (Array.isArray(shape)) return Array.isArray(value) && value.length ? value.slice(0,80).map(row => mergeShape(shareholder(),row)) : shape;
  return Object.fromEntries(Object.entries(shape).map(([key, field]) => [key, mergeShape(field, value && Object.hasOwn(value,key) ? value[key] : undefined)]));
}
export function normalizeDraft(value, client = {}) {
  const result = mergeShape(createDraft(client), value?.version === 1 ? value : {});
  result.mode = resolveMode(result.mode);
  const steps = result.mode === 'quick' ? [1,2,4] : [1,2,3,4];
  if (!steps.includes(result.step)) result.step = 1;
  return result;
}
