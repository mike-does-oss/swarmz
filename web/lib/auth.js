// Admin sign in, the signed cookies, and the checks every POST goes through.
import { createHmac, randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';

const b64 = buf => Buffer.from(buf).toString('base64url');
const secret = () => { const s = process.env.SESSION_SECRET; if (!s || s.length < 32) throw new Error('SESSION_SECRET is not set'); return s; };
const mac = text => createHmac('sha256', secret()).update(text).digest('base64url');
const same = (a, b) => { const x = Buffer.from(String(a)), y = Buffer.from(String(b)); return x.length === y.length && timingSafeEqual(x, y); };
const SCRYPT = { N: 16384, r: 8, p: 1 };

export function hashPassword(password) {
  const salt = randomBytes(16);
  return `scrypt:${b64(salt)}:${b64(scryptSync(password, salt, 64, SCRYPT))}`;
}
export function checkPassword(password, stored) {
  const [kind, salt, hash] = String(stored || '').split(':');
  if (kind !== 'scrypt' || !salt || !hash) return false;
  const want = Buffer.from(hash, 'base64url'), got = scryptSync(String(password), Buffer.from(salt, 'base64url'), want.length || 64, SCRYPT);
  return want.length === got.length && timingSafeEqual(want, got);
}
// Checks a sign in. The hash is always computed, so a wrong email takes as long as a wrong password.
export function checkLogin(username, password) {
  const user = process.env.ADMIN_USER, stored = process.env.ADMIN_PASS_HASH;
  if (!user || !stored || !process.env.SESSION_SECRET) return false;          // not set up: nobody gets in
  const okPass = checkPassword(password, stored), okUser = same(String(username).trim().toLowerCase(), user.trim().toLowerCase());
  return okPass && okUser;
}

export function cookies(request) {
  const out = {};
  for (const part of (request.headers.get('cookie') || '').split(';')) { const i = part.indexOf('='); if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim(); }
  return out;
}
const isHttps = request => (request.headers.get('x-forwarded-proto') || new URL(request.url).protocol.replace(':', '')) === 'https';
const cookie = (request, name, value, maxAge, sameSite) => `${name}=${value}; Path=/; HttpOnly; SameSite=${sameSite}; Max-Age=${maxAge}${isHttps(request) ? '; Secure' : ''}`;

const SESSION = 'sc_admin', HOURS = 12;
export function sessionCookie(request, user) {
  const body = b64(JSON.stringify({ u: user, exp: Date.now() + HOURS * 3600e3 }));
  return cookie(request, SESSION, `${body}.${mac('admin.' + body)}`, HOURS * 3600, 'Strict');
}
export const clearSession = request => cookie(request, SESSION, '', 0, 'Strict');
// The signed-in admin's email, or null.
export function session(request) {
  try {
    const [body, sig] = (cookies(request)[SESSION] || '').split('.');
    if (!body || !sig || !process.env.ADMIN_USER || !same(sig, mac('admin.' + body))) return null;
    const s = JSON.parse(Buffer.from(body, 'base64url').toString());
    return typeof s.u === 'string' && s.exp > Date.now() && same(s.u.toLowerCase(), process.env.ADMIN_USER.trim().toLowerCase()) ? s.u : null;
  } catch (e) { return null; }
}

// Visitors are told apart by a random ID in a signed cookie, set the first time they submit.
const VIEWER = 'sc_v';
export function viewer(request) {
  try { const [id, sig] = (cookies(request)[VIEWER] || '').split('.'); return id && sig && /^[\w-]{16,40}$/.test(id) && same(sig, mac('viewer.' + id)) ? id : null; } catch (e) { return null; }
}
export function newViewer(request) { const id = b64(randomBytes(16)); return { id, cookie: cookie(request, VIEWER, `${id}.${mac('viewer.' + id)}`, 365 * 86400, 'Lax') }; }

// The address is only ever stored hashed.
export function ipHash(request) {
  const ip = (request.headers.get('x-real-ip') || (request.headers.get('x-forwarded-for') || '').split(',')[0] || 'unknown').trim();
  return mac('ip.' + ip).slice(0, 32);
}

export const json = (body, status = 200, headers = {}) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...headers } });
export const fail = (code, status = 400) => json({ error: code }, status);

// The JSON body of a POST from this site's own page, or a Response saying why not.
export async function readPost(request) {
  const origin = request.headers.get('origin'), host = request.headers.get('x-forwarded-host') || request.headers.get('host');
  let from = null; try { from = new URL(origin).host; } catch (e) {}
  if (!from || !host || from !== host) return fail('bad', 403);
  if (!(request.headers.get('content-type') || '').toLowerCase().startsWith('application/json')) return fail('bad', 415);
  const text = await request.text();
  if (text.length > 4096) return fail('bad', 413);
  try { const body = JSON.parse(text); return body && typeof body === 'object' && !Array.isArray(body) ? body : fail('bad'); } catch (e) { return fail('bad'); }
}
