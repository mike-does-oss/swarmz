// The run in progress, if there is one, as the cloud runner last saved it. The page asks every few
// seconds while a run is on, so the answer is shared for a couple of seconds between visitors.
import { fail, json } from '../lib/auth.js';
import { db } from '../lib/db.js';

export async function GET() {
  try {
    const rows = await db()`select value from site_data where key = 'live'`;
    let doc = { active: false };
    try { if (rows[0]) doc = JSON.parse(rows[0].value); } catch (e) {}
    return json(doc, 200, { 'cache-control': 'public, max-age=0, s-maxage=2, stale-while-revalidate=4' });
  } catch (e) { console.error(e); return fail('server', 500); }
}
