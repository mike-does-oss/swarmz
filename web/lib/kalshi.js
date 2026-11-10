// Looks a ticker up on Kalshi so the queue can show the market's question instead of its ID.
const BASE = (process.env.KALSHI_API_BASE || 'https://external-api.kalshi.com/trade-api/v2').replace(/\/+$/, '');

// { found: true, title } for a real market, { found: false } when Kalshi says there is none,
// and { found: null } when Kalshi could not be asked: a lookup that fails must not block a submission.
export async function lookup(ticker) {
  if (process.env.SUPERCAST_KALSHI_LOOKUP === 'off') return { found: null };
  try {
    const r = await fetch(`${BASE}/markets/${encodeURIComponent(ticker)}`, { signal: AbortSignal.timeout(4000), headers: { accept: 'application/json' } });
    if (r.status === 404) return { found: false };
    if (!r.ok) return { found: null };
    const m = (await r.json()).market || {};
    const title = typeof m.title === 'string' ? m.title.replace(/\s+/g, ' ').trim().slice(0, 200) : '';
    return { found: true, title: title || null };
  } catch (e) { return { found: null }; }
}

// Fills in the question for a Kalshi entry. Returns false if Kalshi has no such market.
export async function titled(e) {
  if (e.kind !== 'k') return true;
  const got = await lookup(e.id);
  if (got.found === false) return false;
  e.text = got.title || null;
  return true;
}
