# Security posture

What this build actually does, stated plainly rather than claimed. No
control listed here is aspirational — each one names the code that
implements it, so a reviewer can check the claim instead of taking it on
faith. What isn't here isn't done; see **Known gaps** at the end.

This is a hackathon prototype on synthetic data (see `DataSource` in
`app/core/enums.py`), not a certified government system. Nothing below is
a claim of compliance with a specific Government of India cybersecurity
standard (CERT-In guidelines, MeitY's GIGW, or an ISO 27001 certification)
— that requires a formal audit this project has not undergone.

## Authentication

- **Passwords** are hashed with bcrypt (`passlib[bcrypt]`), never stored
  or logged in the clear. See `app/core/security.py`.
- **A second factor is mandatory on every password login**, not optional
  — `POST /auth/login` never returns a session token by itself; the
  follow-up code step at `POST /auth/login/verify` always runs. An
  account with no authenticator enrolled is asked for a fixed fallback
  code instead of a real one — visibly, in the UI — documented as a
  temporary measure in `app/services/totp.py`, not hidden as if it were
  real security.
- **Face recognition** is a full login method in its own right (not just
  a second factor), matched entirely server-side against a stored
  embedding — see `app/services/face.py` and `app/routers/biometrics.py`.
- **Login attempts are rate-limited** per the same limiter across
  password, face, and the TOTP code step — `app/services/ratelimit.py`.
  In-memory and per-process, stated as a real limitation in
  `DEPLOYMENT.md`, not silently assumed to scale.
- **Accounts are invitation-only.** Nobody self-registers into an officer
  role; every account is created against an invite code an administrator
  issued for a specific role and district (`InviteCode` in
  `app/models/tables.py`), split selector/verifier the same way a
  password-reset token would be, hashed, never recoverable once expired.

## Access control

- **Role-based access control enforced at the query layer**, not just the
  route: `app.dependencies.require_role` and `scope_cases_to_user` narrow
  every query to what the calling role and district may see, so a bug in
  one endpoint's role check cannot leak another district's cases through
  a different one. Nine roles, see `app/core/enums.py:Role`.
- **The public API surface is deliberately narrow.** Unauthenticated
  routes (`/notices/lookup`, `/public-acquisitions`) return only what is
  already published in the gazette — no personal contact details, no bank
  references, no ability to enumerate parcels by browsing.

## Data protection

- **At-rest encryption for the two field kinds where it earns its
  place**: TOTP secrets and biometric templates, via
  `app.services.crypto.EncryptedString` (Fernet — AES-128-CBC with an
  HMAC, authenticated). Reserved for these two rather than applied
  everywhere: each is a live bypass or an unrotatable credential if read
  once from a raw database file or backup, which is the specific threat
  this control addresses — it does nothing against a compromised
  application-level account, which role-based access control above is
  what actually defends against. See the module docstring for the key
  fallback behaviour on rows written before this existed.
- **Files never carry a client-controlled name on disk** — uploaded
  documents are written under a server-generated `stored_name`; see
  `app/routers/documents.py`.
- **Every document is versioned and hashed** (SHA-256) rather than
  overwritten — `app/models/tables.py:Document`. A superseded version
  stays on disk and in the trail.
- **Transport encryption is handled at the platform layer, not the
  application's.** The deployed frontend and API are served over HTTPS by
  Render; the database connection requires `sslmode=require` (enforced by
  host in `app/database.py`, per `DEPLOYMENT.md`). Local Docker Compose
  development does not use TLS between its own containers — that traffic
  never leaves the machine it runs on.

## Audit and accountability

- **Every mutating action is attributed and timestamped** in an
  append-only `audit_log` — `app/services/audit.py`, written by every
  router that changes state, nothing updates or deletes a row here.
  Includes exports (`app/routers/exports.py`) and external-portal lookups
  (`app/routers/integrations.py`): a query against someone's landholding
  is itself logged, not just the write that follows it.
- **A biometric re-confirmation is required before high-impact actions**
  (a case hold, or advancing into Declaration, Award, Possession, or a
  case's final stage) even for an already-authenticated session — see
  `app.dependencies.verify_stepup` and `STEPUP_REQUIRED_STAGES` in
  `app/routers/cases.py`.

## Known gaps

Stated here rather than left for a reviewer to find:

- **No formal compliance certification** of any kind — see the note at
  the top.
- **At-rest encryption is not applied to every sensitive column** — a
  landowner's phone number (`Person.phone`) and other contact fields are
  not encrypted today. TOTP secrets and biometric templates were chosen
  first because each is a single-point, unrotatable exposure; a wider
  pass is future work, not done.
- **No formal penetration test or third-party security review** has been
  performed against this codebase.
- **The rate limiter does not survive a restart or scale past one
  process** — see `DEPLOYMENT.md`'s Known Limitations.
