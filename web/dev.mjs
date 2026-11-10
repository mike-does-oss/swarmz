// Local server: the built page at / and /wp-admin, and the same API handlers Vercel runs. Usage: node --env-file=.env.local dev.mjs
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
const routes = { '/api/state': await import('./api/state.js'), '/api/admin': await import('./api/admin.js'), '/api/live': await import('./api/live.js') };
const port = Number(process.env.SUPERCAST_PORT) || 3210;
createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`), mod = routes[url.pathname];
  if (!mod) {
    if (url.pathname !== '/' && url.pathname !== '/wp-admin') { res.writeHead(404).end('Not found'); return; }
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }).end(readFileSync(new URL('public/index.html', import.meta.url)));
    return;
  }
  const handler = mod[req.method];
  if (!handler) { res.writeHead(405).end(); return; }
  const chunks = []; for await (const c of req) chunks.push(c);
  const out = await handler(new Request(url, { method: req.method, headers: req.headers, body: chunks.length ? Buffer.concat(chunks) : undefined }));
  res.writeHead(out.status, Object.fromEntries(out.headers)).end(Buffer.from(await out.arrayBuffer()));
}).listen(port, '127.0.0.1', () => console.log(`http://localhost:${port}`));
