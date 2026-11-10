// Builds public/index.html (the whole page, one file) and lib/generated.js (what the API needs from the same data).
// The run data comes from the site_data table when DATABASE_URL is set, which is how a cloud run reaches the site:
// the runner stores fresh data there and asks Vercel to build again. Without it, the files in src/ are used.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
const at = p => new URL(p, import.meta.url);
const file = name => JSON.parse(readFileSync(at('src/' + name + '.json'), 'utf8'));
let data = file('app_data'), costs = file('admin_costs'), from = 'src/';
if (process.env.DATABASE_URL) {
  try {
    const { neon } = await import('@neondatabase/serverless');
    const rows = await neon(process.env.DATABASE_URL)`select key, value from site_data where key in ('app_data', 'admin_costs')`;
    const got = Object.fromEntries(rows.map(r => [r.key, JSON.parse(r.value)]));
    if (got.app_data && Array.isArray(got.app_data.runs) && got.admin_costs) { data = got.app_data; costs = got.admin_costs; from = 'the database'; }
  } catch (e) { console.warn('could not read site data from the database, using src/:', e.message); }
}
const src = readFileSync(at('src/app.html'), 'utf8');
const shared = readFileSync(at('lib/shared.js'), 'utf8').replace(/^export /gm, '');
for (const mark of ['__DATA__', '/*__SHARED__*/']) if (src.split(mark).length !== 2) throw new Error('expected one ' + mark);
const blob = JSON.stringify(data).replace(/<\//g, '<\\/');
const html = src.replace('/*__SHARED__*/', () => shared).replace('__DATA__', () => blob);
mkdirSync(at('public/'), { recursive: true });
writeFileSync(at('public/index.html'), html);
// costs stay on the server: only the signed-in admin API sends them
writeFileSync(at('lib/generated.js'), '// Written by build.mjs. Do not edit.\n'
  + `export const FINISHED = ${JSON.stringify(data.runs.map(r => r.question_id))};\n`
  + `export const COSTS = ${JSON.stringify(costs)};\n`);
console.log(`public/index.html ${Buffer.byteLength(html)} bytes, ${data.runs.length} finished run(s), data from ${from}, as of ${data.generated}`);
