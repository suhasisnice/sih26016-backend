# Deploying to Supabase + Render

Two repositories, three moving parts: a Postgres database on Supabase, the
API on Render, the built frontend on Render as a static site.

Both repos need a GitHub remote before any of this works — Render deploys
from git, and neither repo has one yet.

Read [Known limitations](#known-limitations) before the demo, not after.

---

## 1. Supabase: the database

1. Create the project. Pick the **Singapore** region — the same one both
   `render.yaml` files pin.

   Supabase also offers Mumbai (`ap-south-1`), which is closer to the users
   and is still the wrong choice. Render has no India region at all — its
   five are Oregon, Ohio, Virginia, Frankfurt and Singapore — so the API is
   going to sit in Singapore regardless. The question is only where the
   database goes relative to it, and the two hops are not paid at the same
   rate: user-to-API is paid once per page request, API-to-database once
   per *query*, and endpoints like the dashboard KPIs issue several.

   The pooled connection makes that worse rather than better. pgbouncer
   requires `NullPool` (see below), so each request opens a fresh
   connection, and a TLS handshake is several round trips before any SQL
   moves. Across Mumbai↔Singapore that handshake alone costs more than the
   entire India→Singapore hop it was meant to save.

   Co-located, the database hop is effectively free and the distance to
   India is paid exactly once per request. Move both together or neither.
2. **Enable PostGIS before the API ever starts.** Dashboard →
   Database → Extensions → search `postgis` → enable.

   This is not optional and it is not something the app can do for itself.
   `Parcel.geom` is a `Geometry(POINT, 4326)`; without the extension the
   type does not exist, `create_all()` fails, and the API dies at boot with
   a type error that reads like a code bug.
3. Copy both connection strings from Connect → ORMs / psycopg2:

   | Connection | Port | Use it for |
   |---|---|---|
   | Transaction pooler | 6543 | The Render service (`DATABASE_URL`) |
   | Session pooler | 5432 | Seeding and psql from your machine |

   Both pooled strings' user looks like `postgres.<project-ref>`, not plain
   `postgres`. Copy them whole rather than editing one into the other.

   **Not the direct connection**, even though it is the one Supabase shows
   first and the one that looks right for a one-off admin task. On the free
   tier `db.<project-ref>.supabase.co` resolves to an IPv6 address only, and
   a Docker Desktop container on Windows has no IPv6 route to it, so the
   seed dies with `psycopg2.OperationalError: connection to server ... failed`
   — a message that reads like a wrong password and is not one. The session
   pooler is IPv4 and behaves the same for this purpose.

   **Percent-encode the password when you paste it in.** Supabase prints
   `[YOUR-PASSWORD]` as a placeholder and substituting it by hand is where
   this goes wrong: the password sits in the userinfo half of a URI, so
   `#`, `?`, `/`, `@`, `:` and `+` are read as structure rather than as
   characters. A `#` is the cruel one — it starts a URI fragment, so
   everything after it is discarded and the client authenticates with a
   silently truncated password. Nothing reports a parsing problem; you get
   an authentication or connection error against a password that is, as
   typed, correct. `#` → `%23`, `?` → `%3F`, `/` → `%2F`, `+` → `%2B`,
   `@` → `%40`, `:` → `%3A`. Resetting the password to an alphanumeric one
   in Settings → Database sidesteps the whole class of it, and is worth
   doing before the string goes into the Render dashboard too.

   The transaction pooler string also arrives with `?pgbouncer=true`
   appended. That flag is Prisma's; libpq rejects unknown connection
   options outright, so it would fail the connection with
   `invalid connection option "pgbouncer"`. `app/database.py` strips it on
   the way in, so pasting the string whole is fine — but that is the app
   absorbing it, not libpq tolerating it.

Why two: the pooler is pgbouncer, which multiplexes many clients onto few
server connections and breaks anything assuming a session survives between
statements. `app/database.py` detects port 6543 (or a `pooler.supabase.com`
host) and switches to `NullPool` so SQLAlchemy stops pooling on top of a
pooler. That detection covers the session pooler too, since it matches on
the host as well as the port — so the seed run below also gets `NullPool`,
which costs it nothing. The difference that matters between the two poolers
is that the transaction pooler hands a connection back after every
statement, while the session pooler holds one for the length of the
session: `TRUNCATE` plus `create_all()` plus a few thousand inserts wants
the latter.

SSL is required on both, and is applied by host rather than by port: any
non-local host gets `sslmode=require`.

---

## 2. Seed the database

Run this **once**, from your machine, against the **session pooler** (5432)
string.

```bash
docker compose run --rm --no-deps \
  -e DATABASE_URL="postgresql://postgres.PROJECT:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres" \
  api python -m app.ai_layer.seed --allow-remote
```

- `--no-deps` stops compose from starting the local database alongside it;
  without it you boot a Postgres container this command never touches.
- `--allow-remote` is required, and it is a guard rather than ceremony:
  `run_seed()` `TRUNCATE`s every table. It refuses a non-local host unless
  you say so explicitly, so a reflexive re-run cannot silently wipe the
  deployed database.
- `run_seed()` calls `create_all()` itself, so this both creates the schema
  and fills it. Deploying the API first also works — it creates the same
  tables at boot — but the order above means the API's first boot finds a
  database that is already right.

Add `--rebuild` only when the models have changed shape since the last
seed; it drops every table first. See [Known limitations](#known-limitations).

The seed writes **eleven** accounts, all sharing the password `demo1234`
(override with `SEED_PASSWORD` at seed time):

| Username | Role |
|---|---|
| `admin` | State Administrator |
| `state.karnataka` | State officer |
| `ministry` | Ministry officer |
| `dc.bengaluru`, `dc.tumakuru` | District officer |
| `slao.bengaluru` | SLAO |
| `rnr.bengaluru` | RnR officer |
| `field.bengaluru` | Field officer |
| `landowner` | Landowner |
| `nhai`, `kiadb` | Requiring body |

The last five exist because the proposal chain runs requiring body →
district → state → ministry, and without an account at each tier the
approval path cannot be walked end to end.

**The deployed login page does not list any of them** — see §4 — so whoever
is demonstrating needs this table from somewhere other than the screen in
front of them. It is also the reason §4 leaves `VITE_SHOW_DEMO_ACCOUNTS`
unset: eleven working credentials on a public page, two of them able to
approve at state and ministry level, is a different proposition from six.

---

## 3. Render: the API

New → Blueprint → point it at the backend repo. It reads `render.yaml` and
creates one Docker web service. Three variables are marked `sync: false`
and must be filled in the dashboard:

| Variable | Value |
|---|---|
| `DATABASE_URL` | The **pooled** string, port **6543** |
| `FRONTEND_ORIGIN` | The static site's URL, e.g. `https://bhoomimitra.onrender.com` |
| `SECRET_KEY` | Leave it — Render generates one and keeps it |

`ENVIRONMENT=production` is set by the blueprint, and it is what arms the
boot guard in `app/config.py`. That guard refuses to start on any of:

- `SECRET_KEY` still the built-in development value, or under 32 characters
- `FRONTEND_ORIGIN` set to `*`
- `DATABASE_URL` still pointing at the compose host `@db:5432`

It fails loudly at boot rather than serving a quietly insecure API, so a
deploy that dies with `Refusing to start in production…` in the logs is the
guard working — read the message, it names the specific problem.

Chicken-and-egg on `FRONTEND_ORIGIN`: you do not know the static site's URL
until §4 creates it. Deploy the API with a placeholder, create the frontend,
then set the real origin and let the API redeploy. Until that is right,
every browser request fails CORS while `curl` keeps working — which is the
signature of exactly this mistake.

---

## 4. Render: the frontend

New → Blueprint → the frontend repo. Set one variable:

| Variable | Value |
|---|---|
| `VITE_API_URL` | The API service's URL, **no trailing slash** |

Vite inlines `VITE_*` at **build** time. Changing this in the dashboard has
no effect until the next deploy — a build made with the wrong value ships a
bundle that calls the wrong host forever, and the fix is always a rebuild,
never a restart.

`VITE_SHOW_DEMO_ACCOUNTS` is deliberately not in the blueprint. The demo
account list renders only when that variable is exactly `"true"`; leaving it
unset is what keeps six working credentials — one of them a State
Administrator — off a public login page. Set it only if you accept that.

The blueprint also adds the SPA rewrite (`/*` → `/index.html`). Without it
a browser reload on `/cases/12` 404s, because the app ships one HTML file
and React Router resolves the rest.

---

## 5. Check it

```bash
curl https://sih26016-api.onrender.com/health
# {"api":"ok","database":"ok"}
```

`"database":"unreachable"` alongside a 200 is intentional — a Supabase blip
should surface in the body, not restart the API instance underneath it. But
it means the health check will not catch a database outage for you: read the
body, not just the status code.

Then open the frontend and sign in. If the page loads but sign-in fails
while `/health` is fine, it is `FRONTEND_ORIGIN` (CORS) or `VITE_API_URL`
(built against the wrong host) — in that order of likelihood.

---

## Known limitations

These are known and deliberate, not oversights. Each is something to decide
about rather than something to discover at 2am.

**No migrations.** The app calls `create_all()` at boot, which only ever
*adds* missing tables. It cannot alter a table whose columns changed. So
after a model change the deployed database keeps its old shape, and the
first query against the changed column fails. Today the only way out is
`--rebuild`, which destroys the data. That is acceptable while the data is
generated demo data and stops being acceptable the moment it is not — at
which point this needs Alembic before the next schema change, not after it.

**Uploads are ephemeral.** Documents are written to `/app/uploads` inside
the container. On Render's free plan that filesystem is discarded on every
deploy and every wake-from-sleep, while the `documents` rows still point at
the vanished files — so a download fails with a 404 rather than explaining
itself. Attaching a disk (commented out in `render.yaml`) fixes it and costs
money; moving storage to Supabase Storage fixes it properly and is a code
change. Until one of those, treat an uploaded file as lasting until the
next deploy.

**The free tier sleeps.** After roughly 15 minutes idle the API stops, and
the next request waits out a cold start of about 30–50 seconds.
`pool_recycle=300` keeps it from handing out a connection that died during
the sleep. If this is being judged live, hit `/health` a minute beforehand.

**The login rate limiter is per process.** `app/services/ratelimit.py`
keeps its counters in memory. One instance, one set of counters — correct
here, since the free plan runs exactly one. Scale to two and the effective
limit doubles, and a restart clears every counter. It stops password
guessing at this scale; it is not a distributed limiter and does not claim
to be.

**`docker-compose.yml` has a hardcoded database password.** It is
`sih26016/sih26016`, on a loopback-only port, for the local development
database only. Nothing in the Supabase or Render path reads it. It is not a
deployed credential — those live in the Render dashboard, never in a file.
