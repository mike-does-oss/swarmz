import { neon } from '@neondatabase/serverless';
let cached = null;
// One HTTP call per query or batch: no connection is held open, so the database can sleep between visits.
export function db() {
  if (!cached) {
    if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL is not set');
    cached = neon(process.env.DATABASE_URL);
  }
  return cached;
}
