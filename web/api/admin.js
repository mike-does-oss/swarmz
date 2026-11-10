// The admin API behind /wp-admin. Every call but sign in needs the signed session cookie.
import { checkLogin, clearSession, fail, ipHash, json, readPost, session, sessionCookie } from '../lib/auth.js';
import { COSTS } from '../lib/generated.js';
import { add, decide, entry, failedLogin, locked, readState, remove, retry, validId } from '../lib/store.js';

const page = async user => ({ admin: true, user, state: await readState(null, true), costs: COSTS });

export async function GET(request) {
  try { const user = session(request); return json(user ? await page(user) : { admin: false }); }
  catch (e) { console.error(e); return fail('server', 500); }
}

export async function POST(request) {
  try {
    const body = await readPost(request); if (body instanceof Response) return body;
    if (body.action === 'login') {
      if (typeof body.username !== 'string' || typeof body.password !== 'string' || body.password.length > 200) return fail('login', 401);
      const ip = ipHash(request);
      if (await locked(ip)) return fail('locked', 429);
      if (!checkLogin(body.username, body.password)) { await failedLogin(ip); return fail('login', 401); }
      const user = process.env.ADMIN_USER.trim();
      return json(await page(user), 200, { 'set-cookie': sessionCookie(request, user) });
    }
    if (body.action === 'logout') return json({ admin: false }, 200, { 'set-cookie': clearSession(request) });
    const user = session(request); if (!user) return fail('auth', 401);
    let state;
    if (body.action === 'add') { const e = entry(body); if (!e) return fail('bad'); state = await add(e); }
    else if (!validId(body.id)) return fail('bad');
    else if (body.action === 'approve') state = await decide(body.id, 'queued');
    else if (body.action === 'decline') state = await decide(body.id, 'rejected');
    else if (body.action === 'remove') state = await remove(body.id);
    else if (body.action === 'retry') state = await retry(body.id);
    else return fail('bad');
    return json({ admin: true, user, state, costs: COSTS });
  } catch (e) { console.error(e); return fail('server', 500); }
}
