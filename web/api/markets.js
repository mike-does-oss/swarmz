// Open Kalshi markets for the explorer: one per event, liquid, not yet decided, most traded first.
// Listing Kalshi takes a few seconds, so the answer is kept at the edge for ten minutes.
import { fail, json } from '../lib/auth.js';

const BASE = (process.env.KALSHI_API_BASE || 'https://external-api.kalshi.com/trade-api/v2').replace(/\/+$/, '');
const HOUR = 3600;
// How soon the market resolves, in hours: [from, to, slices, pages of 1000 per slice]. Kalshi lists tens of thousands of
// open markets and pages them one after another, so the span is cut into slices by closing time and the slices are read side by side.
const SPANS = { short: [4, 72, 12, 6], medium: [72, 720, 8, 5], long: [720, 9600, 12, 5] };

// Kalshi limits how fast it can be asked: on "too many requests", wait and ask again.
async function ask(path) {
  let r = null;
  for (let tries = 0; tries < 9; tries++) {
    r = await fetch(BASE + path, { signal: AbortSignal.timeout(15000), headers: { accept: 'application/json' } });
    if (r.status !== 429) break;
    await new Promise(done => setTimeout(done, 500 * (tries + 1)));
  }
  if (!r.ok) throw new Error('Kalshi answered ' + r.status);
  return r.json();
}
const clean = (v, n) => String(v || '').replace(/\s+/g, ' ').trim().slice(0, n);
// run the jobs a few at a time
const pooled = (jobs, size, fn) => { const todo = [...jobs]; return Promise.all(Array.from({ length: size }, async () => { while (todo.length) await fn(todo.shift()); })); };

const num = (m, cents, dollars) => {
  const v = m[cents]; if (v !== null && v !== undefined && v !== 0 && v !== '') return Number(v);
  const d = Number(m[dollars]); return d > 0 ? d * 100 : null;
};
const mid = m => { const b = num(m, 'yes_bid', 'yes_bid_dollars'), a = num(m, 'yes_ask', 'yes_ask_dollars'); return b && a && a >= b ? (b + a) / 2 : num(m, 'last_price', 'last_price_dollars'); };
const volume = m => Number(m.volume ?? m.volume_fp ?? 0) || 0;

export async function GET(request) {
  try {
    const span = SPANS[new URL(request.url).searchParams.get('span')] ? new URL(request.url).searchParams.get('span') : 'short';
    const [from, to, slices, pages] = SPANS[span], now = Math.floor(Date.now() / 1000);
    const best = new Map(); let truncated = false;
    const lo = now + from * HOUR, hi = now + (to + 36) * HOUR, step = Math.ceil((hi - lo) / slices);
    const read = async k => {
      let cursor = '';
      for (let i = 0; i < pages; i++) {
        const q = new URLSearchParams({ status: 'open', limit: '1000', mve_filter: 'exclude', min_close_ts: String(lo + k * step), max_close_ts: String(Math.min(hi, lo + (k + 1) * step)) });
        if (cursor) q.set('cursor', cursor);
        const j = await ask(`/markets?${q}`);
        for (const m of j.markets || []) {
          if (!m.ticker || String(m.ticker).startsWith('KXMVE') || (m.market_type || 'binary') !== 'binary') continue;
          const p = mid(m), v = volume(m), at = Date.parse(m.expected_expiration_time || m.close_time), hours = (at / 1000 - now) / HOUR;
          if (p == null || p < 10 || p > 90 || v < 500 || !(hours >= from && hours <= to)) continue;
          const ev = m.event_ticker || m.ticker, had = best.get(ev);
          if (!had || v > had.volume) best.set(ev, { ticker: m.ticker, event_ticker: ev, title: clean(m.title, 200), yes: clean(m.yes_sub_title || m.subtitle, 80), rule: clean(m.rules_primary, 400),
            resolves: new Date(at).toISOString(), hours: Math.round(hours * 10) / 10, price: Math.round(p), volume: Math.round(v) });
        }
        cursor = j.cursor || '';
        if (!cursor) return;
      }
      truncated = true;
    };
    await pooled(Array.from({ length: slices }, (_, k) => k), 3, read);
    const markets = [...best.values()].sort((a, b) => b.volume - a.volume).slice(0, 250);
    // What each market is part of: the event's name and its category, asked for one series at a time. This is the slow part,
    // so the page first asks without it (detail=0) to show the list, then with it.
    const detail = new URL(request.url).searchParams.get('detail') === '1';
    const events = new Map(), series = detail ? [...new Set(markets.map(m => m.event_ticker.split('-')[0]))] : [];
    await pooled(series, 3, async s => {
      try { for (const e of (await ask(`/events?series_ticker=${encodeURIComponent(s)}&status=open&limit=200`)).events || []) events.set(e.event_ticker, e); } catch (e) {}
    });
    for (const m of markets) { const e = events.get(m.event_ticker) || {}; m.event = clean(e.title, 160); m.category = clean(e.category, 40); delete m.event_ticker; }
    return json({ span, detail, markets, truncated }, 200, { 'cache-control': 'public, max-age=60, s-maxage=600, stale-while-revalidate=3600' });
  } catch (e) { console.error(e); return fail('kalshi', 502); }
}
