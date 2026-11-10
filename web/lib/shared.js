// Checks shared by the page and the API. The build copies this file into the page, so both agree on what is valid.
export const TICKER = /^[A-Z0-9]{2,}(-[A-Z0-9.]+){1,4}$/, QID = /^q-[0-9a-f]{14}$/, DATE = /^\d{4}-\d{2}-\d{2}$/, MAX_MINE = 10;
export const okTicker = s => typeof s === 'string' && s.length <= 64 && TICKER.test(s);
export const okId = s => okTicker(s) || (typeof s === 'string' && QID.test(s));
// a ticker as typed, or the last part of a pasted Kalshi link
export const normTicker = raw => { let s = String(raw || '').trim(); if (s.includes('/')) s = s.split(/[?#]/)[0].split('/').filter(Boolean).pop() || ''; s = s.toUpperCase(); return okTicker(s) ? s : null; };
export const squash = s => String(s).replace(/\s+/g, ' ').trim();
export const today = () => new Date().toISOString().slice(0, 10);
export const okDate = s => typeof s === 'string' && DATE.test(s) && !isNaN(Date.parse(s + 'T00:00:00Z'));
export function hash53(s) {
  let a = 0xdeadbeef, b = 0x41c6ce57;
  for (let i = 0; i < s.length; i++) { const c = s.charCodeAt(i); a = Math.imul(a ^ c, 2654435761); b = Math.imul(b ^ c, 1597334677); }
  a = Math.imul(a ^ (a >>> 16), 2246822507) ^ Math.imul(b ^ (b >>> 13), 3266489909);
  b = Math.imul(b ^ (b >>> 16), 2246822507) ^ Math.imul(a ^ (a >>> 13), 3266489909);
  return 4294967296 * (2097151 & b) + (a >>> 0);
}
// An own question, cleaned and given its ID, or null. The ID is a hash of the wording, so the same question asked twice is one row.
export function own(o) {
  if (!o || typeof o.text !== 'string' || typeof o.criteria !== 'string' || !okDate(o.resolves)) return null;
  const text = squash(o.text), criteria = squash(o.criteria);
  if (text.length < 12 || text.length > 200 || criteria.length < 12 || criteria.length > 400) return null;
  return { id: 'q-' + hash53([text, o.resolves, criteria].join('\n').toLowerCase()).toString(16).padStart(14, '0'), kind: 'q', text, resolves: o.resolves, criteria };
}
