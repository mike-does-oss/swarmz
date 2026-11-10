// End to end against a scratch database. Usage: node --env-file=.env.local --test test/*.test.mjs
// Needs TEST_PASSWORD, the password behind ADMIN_PASS_HASH. It empties the tables, so never point it at production.
import test from 'node:test';
import assert from 'node:assert/strict';
import { db } from '../lib/db.js';
import * as state from '../api/state.js';
import * as admin from '../api/admin.js';

const ORIGIN = 'http://localhost:3210', pw = process.env.TEST_PASSWORD, user = process.env.ADMIN_USER;
const jar = () => ({ cookie: '' });
async function call(mod, j, body, headers = {}) {
  const res = await mod[body ? 'POST' : 'GET'](new Request(ORIGIN + '/api/x', { method: body ? 'POST' : 'GET', body: body ? JSON.stringify(body) : undefined,
    headers: { host: 'localhost:3210', 'x-real-ip': '10.0.0.1', ...(body ? { origin: ORIGIN, 'content-type': 'application/json' } : {}), ...(j.cookie ? { cookie: j.cookie } : {}), ...headers } }));
  const set = res.headers.get('set-cookie'); if (set) j.cookie = set.split(';')[0].endsWith('=') ? '' : set.split(';')[0];
  return { status: res.status, set, ...(await res.json()) };
}
const Q = { text: 'Will the test suite pass on the first try?', resolves: '2099-01-01', criteria: 'Every assertion in this file holds.' };
const TK = 'KXTEST-99JAN01-T1';

test('api', async t => {
  assert.ok(pw && user && /ep-.*dev|localhost|test/.test(process.env.DATABASE_URL + process.env.TEST_DB_OK), 'refusing to run: set TEST_PASSWORD and TEST_DB_OK=test on a scratch database');
  const sql = db();
  await sql.transaction([sql`delete from submissions`, sql`delete from queue`, sql`delete from login_attempts`]);
  const a = jar(), b = jar(), adm = jar();

  await t.test('public read is empty and not admin', async () => {
    const r = await call(state, a);
    assert.deepEqual([r.status, r.admin, r.state], [200, false, { queued: [], submitted: [] }]);
  });
  await t.test('posts need this site as origin, JSON, and a small body', async () => {
    assert.equal((await call(state, a, { action: 'submit', ticker: TK }, { origin: 'https://evil.example' })).status, 403);
    assert.equal((await call(state, a, { action: 'submit', ticker: TK }, { 'content-type': 'text/plain' })).status, 415);
    assert.equal((await call(state, a, { action: 'submit', ticker: TK, pad: 'x'.repeat(5000) })).status, 413);
  });
  await t.test('bad entries are refused', async () => {
    for (const body of [{ ticker: 'nope' }, { ticker: 'KXTRUMPAPPROVE-26NOV10-U38.8' }, { ...Q, text: 'short' }, { ...Q, resolves: '2099-13-45' }, {}])
      assert.equal((await call(state, a, { action: 'submit', ...body })).error, 'bad');
  });
  await t.test('submit a ticker and a question, see them as mine', async () => {
    const r1 = await call(state, a, { action: 'submit', ticker: 'kxtest-99jan01-t1' });
    assert.equal(r1.status, 200); assert.match(r1.set, /^sc_v=.*HttpOnly; SameSite=Lax/);
    const r2 = await call(state, a, { action: 'submit', ...Q });
    assert.deepEqual(r2.state.submitted.map(s => [s.kind, s.n, s.mine]), [['k', 1, true], ['q', 1, true]]);
    assert.equal(r2.state.submitted[0].id, TK); assert.match(r2.state.submitted[1].id, /^q-[0-9a-f]{14}$/);
    assert.equal((await call(state, a, { action: 'submit', ticker: TK })).error, 'dup');
  });
  await t.test('a second visitor adds a request but cannot withdraw the first one\'s', async () => {
    const r = await call(state, b, { action: 'submit', ticker: TK });
    assert.deepEqual(r.state.submitted.map(s => [s.n, s.mine]), [[2, true], [1, false]]);
    const qid = r.state.submitted[1].id;
    assert.equal((await call(state, b, { action: 'withdraw', id: qid })).state.submitted.length, 2);
    const w = await call(state, b, { action: 'withdraw', id: TK });
    assert.deepEqual(w.state.submitted.map(s => [s.n, s.mine]), [[1, false], [1, false]]);
  });
  await t.test('the cap on waiting submissions per visitor', async () => {
    const c = jar();
    for (let i = 0; i < 10; i++) assert.equal((await call(state, c, { action: 'submit', ticker: 'KXCAP-T' + i }, { 'x-real-ip': '10.0.0.2' })).status, 200);
    assert.equal((await call(state, c, { action: 'submit', ticker: 'KXCAP-T10' }, { 'x-real-ip': '10.0.0.2' })).error, 'cap');
    for (let i = 0; i < 10; i++) await call(state, c, { action: 'withdraw', id: 'KXCAP-T' + i });
  });
  await t.test('admin is closed without a session', async () => {
    assert.deepEqual(await call(admin, adm), { status: 200, set: null, admin: false });
    assert.equal((await call(admin, adm, { action: 'approve', id: TK })).status, 401);
    assert.equal((await call(admin, adm, { action: 'add', ticker: 'KXADD-T1' })).status, 401);
    assert.equal((await call(admin, { cookie: 'sc_admin=' + Buffer.from(JSON.stringify({ u: user, exp: Date.now() + 1e6 })).toString('base64url') + '.forged' })).admin, false);
  });
  await t.test('wrong sign ins fail, then lock the address out', async () => {
    const h = { 'x-real-ip': '10.0.0.9' };
    assert.equal((await call(admin, adm, { action: 'login', username: 'someone@else.test', password: pw }, h)).error, 'login');
    for (let i = 0; i < 4; i++) assert.equal((await call(admin, adm, { action: 'login', username: user, password: 'wrong' + i }, h)).error, 'login');
    assert.equal((await call(admin, adm, { action: 'login', username: user, password: pw }, h)).error, 'locked');
  });
  let qid;
  await t.test('sign in, approve, decline, undo, add, remove', async () => {
    const r = await call(admin, adm, { action: 'login', username: user.toUpperCase(), password: pw });
    assert.equal(r.status, 200); assert.match(r.set, /^sc_admin=.*HttpOnly; SameSite=Strict; Max-Age=43200/);
    assert.equal(r.admin, true); assert.equal(r.costs.runs.length, 1); assert.equal(r.state.submitted.length, 2);
    qid = r.state.submitted[1].id;
    let s = (await call(admin, adm, { action: 'approve', id: TK })).state;
    assert.deepEqual([s.queued.map(q => q.id), s.submitted.map(q => q.id), s.rejected], [[TK], [qid], []]);
    s = (await call(admin, adm, { action: 'decline', id: qid })).state;
    assert.deepEqual([s.queued.length, s.submitted.length, s.rejected.map(q => q.text)], [1, 0, [Q.text]]);
    assert.equal((await call(admin, adm, { action: 'approve', id: 'KXNOBODY-ASKED' })).state.queued.length, 1);
    s = (await call(admin, adm, { action: 'add', ticker: 'KXADD-T1' })).state;
    assert.deepEqual(s.queued.map(q => q.id), [TK, 'KXADD-T1']);
    s = (await call(admin, adm, { action: 'remove', id: 'KXADD-T1' })).state;
    assert.deepEqual(s.queued.map(q => q.id), [TK]);
  });
  await t.test('visitors see the queue but not what was declined, and cannot resubmit either', async () => {
    const r = await call(state, a);
    assert.deepEqual([r.state.queued.map(q => q.id), r.state.submitted, 'rejected' in r.state, 'costs' in r], [[TK], [], false, false]);
    assert.equal((await call(state, a, { action: 'submit', ticker: TK })).error, 'queued');
    assert.equal((await call(state, a, { action: 'submit', ...Q })).error, 'declined');
  });
  await t.test('undoing a decision puts the submission back', async () => {
    const s = (await call(admin, adm, { action: 'remove', id: qid })).state;
    assert.deepEqual([s.submitted.map(q => q.id), s.rejected], [[qid], []]);
  });
  await t.test('what the runner does to a queue row shows up in the right places', async () => {
    await sql.transaction([
      sql`insert into queue (id, kind, status, ran_at) values ('KXRUN-T1', 'k', 'running', now())`,
      sql`insert into queue (id, kind, status) values ('KXDONE-T1', 'k', 'done')`,
      sql`insert into queue (id, kind, status, note) values ('KXFAIL-T1', 'k', 'failed', 'brief rejected: word_count_out_of_range:412')`]);
    const pub = (await call(state, a)).state;
    assert.deepEqual(pub.queued.map(q => [q.id, q.running === true]), [[TK, false], ['KXRUN-T1', true]]);
    assert.equal('failed' in pub, false);
    assert.equal((await call(state, a, { action: 'submit', ticker: 'KXDONE-T1' })).error, 'done');
    assert.equal((await call(state, a, { action: 'submit', ticker: 'KXRUN-T1' })).error, 'queued');
    assert.equal((await call(state, a, { action: 'submit', ticker: 'KXFAIL-T1' })).error, 'declined');
    let s = (await call(admin, adm)).state;
    assert.deepEqual(s.failed.map(q => [q.id, q.note]), [['KXFAIL-T1', 'brief rejected: word_count_out_of_range:412']]);
    s = (await call(admin, adm, { action: 'remove', id: 'KXRUN-T1' })).state;
    assert.ok(s.queued.some(q => q.id === 'KXRUN-T1'), 'a running market cannot be removed');
    s = (await call(admin, adm, { action: 'add', ticker: 'KXDONE-T1' })).state;
    assert.equal(s.queued.some(q => q.id === 'KXDONE-T1'), false, 'a finished market is not queued again');
    s = (await call(admin, adm, { action: 'retry', id: 'KXFAIL-T1' })).state;
    assert.deepEqual([s.failed, s.queued.at(-1).id], [[], 'KXFAIL-T1']);
  });
  await t.test('sign out ends the session', async () => {
    const old = adm.cookie;
    assert.equal((await call(admin, adm, { action: 'logout' })).admin, false);
    assert.equal(adm.cookie, ''); assert.equal((await call(admin, adm)).admin, false);
    assert.equal((await call(admin, { cookie: old })).admin, true, 'cookie itself stays valid until it expires');
  });
  await sql.transaction([sql`delete from submissions`, sql`delete from queue`, sql`delete from login_attempts`]);
});
