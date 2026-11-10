// The public API: read the queue and what is waiting for approval, submit one, withdraw your own.
import { fail, ipHash, json, newViewer, readPost, viewer } from '../lib/auth.js';
import { titled } from '../lib/kalshi.js';
import { entry, readState, submit, validId, withdraw } from '../lib/store.js';

export async function GET(request) {
  try { return json({ admin: false, state: await readState(viewer(request), false) }); }
  catch (e) { console.error(e); return fail('server', 500); }
}

export async function POST(request) {
  try {
    const body = await readPost(request); if (body instanceof Response) return body;
    let who = viewer(request), headers = {};
    if (body.action === 'submit') {
      const e = entry(body); if (!e) return fail('bad');
      if (!await titled(e)) return fail('nomarket', 404);
      if (!who) { const v = newViewer(request); who = v.id; headers = { 'set-cookie': v.cookie }; }
      const { error, state } = await submit(e, who, ipHash(request));
      return error ? fail(error, error === 'rate' || error === 'full' ? 429 : 409) : json({ admin: false, state }, 200, headers);
    }
    if (body.action === 'withdraw') {
      if (!validId(body.id) || !who) return fail('bad');
      return json({ admin: false, state: await withdraw(body.id, who) });
    }
    return fail('bad');
  } catch (e) { console.error(e); return fail('server', 500); }
}
