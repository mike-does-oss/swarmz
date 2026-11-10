// Asks the cloud runner to run one queued market now, without waiting for its two-hourly slot.
// A run takes minutes and this function has seconds, so it waits only long enough to hear an early
// refusal. The runner carries on by itself once it has the request.
const URL_ = (process.env.ENGINE_URL || 'https://swarmz-engine.vercel.app').replace(/\/+$/, '');

// 'started', 'busy' (another run holds the lock), 'cap' (the daily limit), 'gone' (not in the queue), or 'engine' (could not ask).
export async function runNow(ticker) {
  if (process.env.SUPERCAST_ENGINE === 'off') return 'started';
  const secret = process.env.CRON_SECRET;
  if (!secret) return 'engine';
  try {
    const r = await fetch(`${URL_}/api/tick?ticker=${encodeURIComponent(ticker)}`, { headers: { authorization: `Bearer ${secret}` }, signal: AbortSignal.timeout(6000) });
    if (!r.ok) return 'engine';
    const j = await r.json(), log = (j.log || []).join(' ');
    return j.skipped === 'locked' ? 'busy' : /daily cap/.test(log) ? 'cap' : /not waiting/.test(log) ? 'gone' : j.ran || j.failed ? 'started' : 'engine';
  } catch (e) { return e && e.name === 'TimeoutError' ? 'started' : 'engine'; }   // no answer yet means it is working
}
