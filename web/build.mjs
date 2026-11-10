// Builds public/index.html (the whole page, one file) and lib/generated.js (what the API needs from the same data).
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
const at = p => new URL(p, import.meta.url);
const src = readFileSync(at('src/app.html'), 'utf8');
const data = JSON.parse(readFileSync(at('src/app_data.json'), 'utf8'));
const costs = JSON.parse(readFileSync(at('src/admin_costs.json'), 'utf8'));
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
console.log(`public/index.html ${Buffer.byteLength(html)} bytes, ${data.runs.length} finished run(s)`);
