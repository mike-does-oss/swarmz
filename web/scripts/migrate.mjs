// Creates the tables. Safe to run again. Usage: node --env-file=.env.local scripts/migrate.mjs
import { db } from '../lib/db.js';
const sql = db();
await sql.transaction([
  sql`create table if not exists submissions (id text not null, viewer text not null, kind text not null check (kind in ('k','q')), text text, resolves text, criteria text,
      at timestamptz not null default now(), ip_hash text not null, primary key (id, viewer))`,
  sql`create index if not exists submissions_ip on submissions (ip_hash, at)`,
  sql`create table if not exists queue (id text primary key, kind text not null check (kind in ('k','q')), status text not null check (status in ('queued','rejected')),
      text text, resolves text, criteria text, at timestamptz not null default now())`,
  sql`create table if not exists login_attempts (ip_hash text not null, at timestamptz not null default now())`,
  sql`create index if not exists login_attempts_at on login_attempts (at)`,
]);
console.log('tables ready');
