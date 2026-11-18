import {createDraft, normalizeDraft} from './schema.js';
const sessions = new Map();
export function draftKey(client, ctx = {}) {
  return `ydz:tax-liquidation:v1:${ctx.demo ? 'demo' : 'local'}:${encodeURIComponent(String(client.id))}:${encodeURIComponent(String(client.code || ''))}`;
}
export function loadDraft(client, ctx = {}, storage) {
  const key = draftKey(client, ctx);
  if (sessions.has(key)) return sessions.get(key);
  const session = {draft:createDraft(client),error:'',savedAt:''};
  try {
    storage ??= globalThis.localStorage;
    const text = storage?.getItem(key);
    if (text) {
      const parsed = JSON.parse(text);
      if (parsed.version !== 1) throw new Error('不支持的草稿版本');
      session.draft = normalizeDraft(parsed,client);
    }
  } catch {session.error = '浏览器草稿读取失败，本次录入仍可继续；原存储内容尚未修改。';}
  sessions.set(key,session);
  return session;
}
export function saveDraft(client, ctx, session, storage) {
  try {
    storage ??= globalThis.localStorage;
    if (!storage) throw new Error('浏览器存储不可用');
    storage.setItem(draftKey(client,ctx),JSON.stringify(session.draft));
    session.savedAt = new Date().toLocaleTimeString('zh-CN',{hour12:false});
    session.error = '';
    return true;
  } catch {session.error = '本地保存失败（存储已满或被禁用）。当前内容暂存在本页，请先导出 Word，避免刷新丢失。';return false;}
}
