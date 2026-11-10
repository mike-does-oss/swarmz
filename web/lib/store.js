// Everything the API reads from or writes to Postgres.
import { db } from './db.js';
import { FINISHED } from './generated.js';
import { MAX_MINE, normTicker, okId, own } from './shared.js';

export const MAX_PER_IP = 20, MAX_PENDING = 500, MAX_FAILS = 5, MAX_FAILS_ALL = 60;
const finished = new Set(FINISHED);

// What a request asks to add: a Kalshi ticker or an own question. Null if it is neither, or already has a finished run.
export function entry(body) {
  const e = typeof body.ticker === 'string' ? (t => t && { id: t, kind: 'k', text: null, resolves: null, criteria: null })(normTicker(body.ticker)) : own(body);
  return e && !finished.has(e.id) ? e : null;
}
export const validId = id => okId(id);

const row = r => ({ id: r.id, kind: r.kind, ...(r.kind === 'q' ? { text: r.text, resolves: r.resolves, criteria: r.criteria } : {}), at: new Date(r.at).toISOString() });
const pendingQ = (sql, who) => sql`
  select id, kind, text, resolves, criteria, at, n, mine from (
    select s.id, s.kind, s.text, s.resolves, s.criteria, s.at,
           count(*) over (partition by s.id)::int as n,
           bool_or(s.viewer = ${who || ''}) over (partition by s.id) as mine,
           row_number() over (partition by s.id order by s.at) as rn
    from submissions s where not exists (select 1 from queue q where q.id = s.id)) t
  where rn = 1 order by at limit ${MAX_PENDING}`;
const queueQ = sql => sql`select id, kind, status, text, resolves, criteria, at, note from queue where status <> 'done' order by at limit 1000`;
function shape(pending, queue, admin) {
  const state = { queued: queue.filter(q => q.status === 'queued' || q.status === 'running').map(q => ({ ...row(q), ...(q.status === 'running' ? { running: true } : {}) })),
    submitted: pending.map(r => ({ ...row(r), n: r.n, mine: r.mine === true })) };
  if (admin) {                                           // visitors are never told what was declined or what failed
    state.rejected = queue.filter(q => q.status === 'rejected').map(row);
    state.failed = queue.filter(q => q.status === 'failed').map(q => ({ ...row(q), note: String(q.note || '').slice(0, 400) }));
  }
  return state;
}
// Runs the given writes, then reads the state back, all in one round trip.
async function run(writes, who, admin) {
  const sql = db(), out = await sql.transaction([...writes(sql), pendingQ(sql, who), queueQ(sql)]);
  return { results: out.slice(0, -2), state: shape(out.at(-2), out.at(-1), admin) };
}
export const readState = async (who, admin) => (await run(() => [], who, admin)).state;

// One statement decides and inserts, so two requests at once cannot both slip under a limit.
export async function submit(e, who, ip) {
  const { results, state } = await run(sql => [sql`
    with q as (select status from queue where id = ${e.id}),
    mine as (select count(*)::int as c, coalesce(bool_or(s.id = ${e.id}), false) as dup from submissions s where s.viewer = ${who} and not exists (select 1 from queue x where x.id = s.id)),
    ip as (select count(*)::int as c from submissions where ip_hash = ${ip} and at > now() - interval '24 hours'),
    total as (select count(*)::int as c from submissions s where not exists (select 1 from queue x where x.id = s.id)),
    ins as (insert into submissions (id, viewer, kind, text, resolves, criteria, ip_hash)
            select ${e.id}, ${who}, ${e.kind}, ${e.text}, ${e.resolves}, ${e.criteria}, ${ip}
            where not exists (select 1 from q) and not (select dup from mine) and (select c from mine) < ${MAX_MINE}
              and (select c from ip) < ${MAX_PER_IP} and (select c from total) < ${MAX_PENDING}
            on conflict (id, viewer) do nothing returning 1)
    select (select status from q) as status, (select dup from mine) as dup, (select c from mine) as mine, (select c from ip) as ip, (select c from total) as total, (select count(*)::int from ins) as inserted`], who, false);
  const r = results[0][0];
  const error = r.inserted ? null : r.status === 'done' ? 'done' : r.status === 'rejected' || r.status === 'failed' ? 'declined' : r.status ? 'queued' : r.dup ? 'dup' : r.mine >= MAX_MINE ? 'cap' : r.ip >= MAX_PER_IP ? 'rate' : r.total >= MAX_PENDING ? 'full' : 'dup';
  return { error, state };
}
export const withdraw = async (id, who) => (await run(sql => [sql`delete from submissions where id = ${id} and viewer = ${who}`], who, false)).state;

// Admin: a decision copies the earliest submission into the queue. The submissions stay, so undoing it puts them back.
export const decide = async (id, status) => (await run(sql => [sql`
  insert into queue (id, kind, status, text, resolves, criteria)
  select id, kind, ${status}, text, resolves, criteria from submissions where id = ${id} order by at limit 1
  on conflict (id) do update set status = excluded.status, at = now(), note = null where queue.status in ('queued', 'rejected')`], null, true)).state;
// A failed run goes back in the queue, at the end.
export const retry = async id => (await run(sql => [sql`update queue set status = 'queued', note = null, ran_at = null, at = now() where id = ${id} and status = 'failed'`], null, true)).state;
// A market that is running is left alone: the runner is about to write to its row.
export const remove = async id => (await run(sql => [sql`delete from queue where id = ${id} and status <> 'running'`], null, true)).state;
export const add = async e => (await run(sql => [sql`
  insert into queue (id, kind, status, text, resolves, criteria) values (${e.id}, ${e.kind}, 'queued', ${e.text}, ${e.resolves}, ${e.criteria})
  on conflict (id) do update set status = 'queued', at = now(), note = null where queue.status in ('rejected', 'failed')`], null, true)).state;

// Sign in attempts: five wrong tries from one address in 15 minutes locks it out, and so do sixty from anywhere in an hour.
export async function locked(ip) {
  const [r] = await db()`select count(*) filter (where ip_hash = ${ip} and at > now() - interval '15 minutes')::int as mine, count(*) filter (where at > now() - interval '1 hour')::int as everyone from login_attempts`;
  return r.mine >= MAX_FAILS || r.everyone >= MAX_FAILS_ALL;
}
export async function failedLogin(ip) {
  const sql = db();
  await sql.transaction([sql`insert into login_attempts (ip_hash) values (${ip})`, sql`delete from login_attempts where at < now() - interval '1 day'`]);
}
