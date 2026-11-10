# Supercast

The web front end for swarmz. One static page (`public/index.html`), two Vercel functions (`api/state.js`, `api/admin.js`) and a Neon Postgres database that sleeps when nobody visits.

- `npm run build` rebuilds the page from `src/app.html`, `src/app_data.json` and `src/admin_costs.json`.
- `node --env-file=.env.local dev.mjs` serves it at http://localhost:3210, with the admin view at `/wp-admin`.
- `TEST_PASSWORD=... TEST_DB_OK=test node --env-file=.env.local --test test/*.test.mjs` runs the API tests. They empty the tables, so point them at the `dev` database branch only.
- `vercel deploy --prod` deploys.

Settings, all kept in Vercel and `.env.local`, never in git: `DATABASE_URL`, `ADMIN_USER`, `ADMIN_PASS_HASH` (make one with `node scripts/hash-password.mjs`, password on standard input), `SESSION_SECRET`.
