# Order Platform

Multi-Account Google & YouTube Order Management Platform: Django backend with
plain HTML / CSS / JS templates, light and dark theme, role-based staff access,
YouTube count monitoring, client portal, weekly PDF reports, two-factor login and
optional AI helpers.

> The platform **records observed counts**. It does not prove that any software
> increased a count, and a client page never promises results.

## Contents
1. [Quick start](#quick-start)
2. [Features](#features)
3. [Updating an existing project](#updating-an-existing-project)
4. [Daily use](#daily-use)
5. [Configuration (.env)](#configuration-env)
6. [Testing with your own keys](#testing-with-your-own-keys)
7. [WhatsApp Cloud API setup](#whatsapp-cloud-api-setup)
8. [Manual test checklist](#manual-test-checklist)
9. [Management commands](#management-commands)
10. [Scheduling](#scheduling)
11. [Security notes](#security-notes)
12. [Tests](#tests)
13. [Project layout](#project-layout)
14. [Deployment (Contabo)](#deployment-contabo--any-ubuntu-server)
15. [Deployment checklist](#deployment-checklist)

## Quick start
```bash
python -m venv venv
venv\Scripts\activate              # Windows   (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env             # Linux/macOS: cp .env.example .env  - then edit .env
python manage.py migrate
python manage.py createcachetable  # used by the login lockout
python manage.py createsuperuser
python manage.py seed_demo         # optional demo data (DEBUG only)
python manage.py runserver
```
Open http://127.0.0.1:8000/. Demo users (after `seed_demo`): `sales1`, `mgr1`, `op1`, `op2`
with password `Demo@12345`. Delete them before real use.

## Features

### Orders and monitoring
* Customers, YouTube channels, orders with a locked target after approval.
* Status workflow: Draft, Pending Review, Approved, Assigned, Monitoring, Paused,
  Target Observed, Completed, Cancelled, Exception. Every change is logged with who and why.
* YouTube checks with retry/back-off. A reached target is **never completed automatically**:
  a manager reviews it first.

### Growth chart and estimate
Every order page shows a line chart of the observed count over time, the target line and a
dashed estimate. A least-squares trend over the last 7 days gives sentences such as
*"Estimate: target in about 3 days (around 120 per day at the current pace)."* It says
when there is not enough data, when growth has stalled, or when the count is falling.
The estimate is shown as an estimate only (`orders/forecast.py`).

### Client portal (read-only link)
On a customer page, **Client Portal & Reports** creates a private link such as
`/portal/<long-random-token>/`. The client sees only their own orders: status, starting count,
latest count, target, progress, growth chart and a PDF download.
* Hidden from clients: drafts, orders waiting for approval, staff names, notes, activity log, Google data.
* Link controls: enable, **new link** (the old one stops working) and disable. Archived customers lose access.
* Pages are `noindex`, `no-store`, `no-referrer`, GET only, and wrong tokens are rate limited.
* `PORTAL_SHOW_ETA=False` hides the estimate line from clients.

### Weekly client reports (PDF, email, WhatsApp)
* Branded PDF (`BRAND_NAME`, `BRAND_COLOR`): summary tiles plus one row per order with a progress bar.
* **Download PDF report** and **Email report now** buttons on the customer page.
* Tick **Send weekly report by email** on the customer (needs an email address), then schedule
  `python manage.py send_client_reports` (see [Scheduling](#scheduling)). It sends at most once every 6 days per customer.
* **Send on WhatsApp** opens WhatsApp with a ready message and the portal link (one tap by staff).
  Fully automatic WhatsApp sending needs the Meta WhatsApp Business API with approved templates and is **not** included.
* Without email settings in `.env`, emails are printed in the console (safe for testing).

### Two-factor authentication and sessions
* Authenticator-app codes (TOTP) with QR setup, 8 one-time recovery codes, replay protection and lockout on wrong codes.
* The secret is encrypted in the database. A password alone never signs in a user who has 2FA on.
* `REQUIRE_2FA=True` forces every user to set it up.
* **Security** page (click your name in the top bar): see all signed-in devices, sign out one or all others,
  change password (this signs out other sessions).
* A user who lost their phone and recovery codes: Super Admin uses the **Reset two-factor** action on the user in the
  admin, or `python manage.py reset_2fa <username>`.

### AI helpers (optional)
Everything works without a key. With `ANTHROPIC_API_KEY`, text is polished by Claude (`AI_MODEL`).
Only small facts (counts, statuses, order numbers) are sent: never tokens, notes, passwords or Google data.
There is an hourly per-user limit (`AI_HOURLY_LIMIT`).
* **AI summary** (order page, customer page, dashboard): a short status in English or Roman Urdu.
* **Smart search** (Orders page): type things like `paused orders for Bilal`, `high priority review`,
  `stalled today`, `Saeed ke ruke hue orders`. Rules understand statuses, priority, customer, staff, dates,
  stalled, overdue, unassigned and "my orders". If the sentence is not understood and AI is on, Claude converts it to
  filters that are validated before use.
* **Anomaly alerts** (always on, no AI needed): a notification when a new count is lower than the previous by at least
  the configured percent, and when a monitored order shows no growth for the configured hours. Thresholds are on the
  Settings screen. The same alert is not repeated within 24 hours. The dashboard has a **Needs attention** card.

### Interface
Solid-colour design, light and dark theme (toggle in the top bar, remembered per browser), animations
(respecting "reduce motion"), dashboard charts (Chart.js is bundled, no internet needed).

## Updating an existing project
1. Stop the server. Copy this zip's contents over your old project folder. **Keep your own `.env` and `db.sqlite3`.**
2. `pip install -r requirements.txt`
3. `python manage.py migrate` (adds the new tables and fields; existing data is kept)
4. Add any new variables from `.env.example` to your `.env` (WhatsApp, AI, email are all optional).

## Daily use
| Who | What |
|---|---|
| Sales | Add customer and channel, create the order (baseline + requested increase). |
| Manager | Approve, assign, review reached targets, complete. Enable the client portal, send reports. |
| Operator | Work assigned orders, record observations or press **Check YouTube Now**. |
| Super Admin | Settings, users, backups, audit log, reset 2FA. |
| Auditor / Finance | Read-only views and reports according to their role. |

## Configuration (.env)
Copy `.env.example` to `.env`. Never commit `.env`. Key variables:

| Variable | Purpose |
|---|---|
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `SITE_URL` | Django basics. `SITE_URL` is used in emails and portal links. |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key. |
| `GOOGLE_OAUTH_CLIENT_ID/_SECRET/_REDIRECT_URI` | Google account connections. |
| `TOKEN_ENCRYPTION_KEY` | Fernet key: encrypts Google tokens and 2FA secrets. Changing it breaks saved tokens. |
| `BACKUP_ENCRYPTION_KEY`, `BACKUP_DIR`, `BACKUP_KEEP` | Encrypted backups. |
| `BRAND_NAME`, `BRAND_COLOR`, `DEFAULT_COUNTRY_CODE` | Branding and WhatsApp number format. |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL` | SMTP for reports (default: console). |
| `PORTAL_SHOW_ETA`, `REQUIRE_2FA` | Portal and security switches. |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Optional AI with Google Gemini (free key from aistudio.google.com/apikey). |
| `ANTHROPIC_API_KEY`, `AI_MODEL` | Optional AI with Claude instead. |
| `AI_PROVIDER`, `AI_HOURLY_LIMIT` | `gemini` or `anthropic` to force one (empty = whichever key is set); calls per user per hour. |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_TEMPLATE_NAME`, `WHATSAPP_TEMPLATE_LANG`, `WHATSAPP_API_VERSION` | Optional automatic WhatsApp (Meta Cloud API). |
| `ADMIN_URL`, `CSRF_TRUSTED_ORIGINS`, `TRUST_PROXY_HEADERS`, `HSTS_SECONDS`, `USE_HTTPS` | Production hardening (`USE_HTTPS=False` allows plain http on an IP). |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL (empty `DB_NAME` = SQLite). |

Generate a Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

## Testing with your own keys
The automated tests use mocks; real services need your own keys. Put them in `.env`, then run:
```bash
python manage.py check_integrations                       # shows what is configured (no network)
python manage.py check_integrations --email you@example.com   # sends a real test email
python manage.py check_integrations --ai                  # one small AI call (Gemini or Claude)
python manage.py check_integrations --youtube             # one YouTube Data API call
python manage.py check_integrations --whatsapp 03001234567    # sends the approved template to this number
```
For email without a mail account, leave `EMAIL_BACKEND` on console: the message prints in the terminal.
For Gmail use an App Password (`EMAIL_HOST=smtp.gmail.com`, `EMAIL_PORT=587`, `EMAIL_USE_TLS=True`).

## WhatsApp Cloud API setup
Automatic WhatsApp needs a Meta WhatsApp Business account. Without it, the **WhatsApp link** button
(click-to-chat) still works and opens WhatsApp with the message prefilled.
1. Create a Meta developer app, add the WhatsApp product, and note the **Phone number ID** and a permanent access token.
2. Create a message template named `weekly_progress_report` (category Utility, language `en`) with the body:
   `Hello {{1}}, your weekly progress report is ready. Follow your orders live here: {{2}}`
   Wait until Meta approves it. Business-initiated messages outside the 24 hour window must use an approved template.
3. Set `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` in `.env`, then run `check_integrations --whatsapp NUMBER`.
4. On a customer, enable the portal, tick **Weekly WhatsApp**, or use **Send WhatsApp message now (API)**.

## Manual test checklist
1. `seed_demo`, sign in as `mgr1`: the dashboard shows 4 stat cards, charts and the theme toggle works.
2. Open an order with history: growth chart and estimate show. Open a draft: no estimate.
3. Customer page: enable the portal, open the link in a private window (no login): only client-safe data shows. Disable it: the link stops working.
4. Download the PDF report; send it by email (console output or real SMTP).
5. Security page: enable 2FA with an authenticator app, log out and back in with a code, try a recovery code, revoke another session.
6. AI summary buttons and the smart search bar on the orders list (works without a key using rules; the key adds a fallback).
7. `run_checks` with a dropped count raises an anomaly notification; running it again within 24 hours does not repeat it.
8. WhatsApp: see the setup above.

## Management commands
| Command | Purpose |
|---|---|
| `run_checks [--loop --every 300]` | YouTube checks for due orders, review-overdue and no-growth alerts. |
| `send_client_reports [--customer ID] [--dry-run] [--force]` | Weekly PDF email and WhatsApp to customers who have them on. |
| `check_integrations [--email A] [--ai] [--whatsapp N] [--youtube]` | Shows configuration and runs live tests with your keys. |
| `backup_db`, `backup_db --verify FILE`, `backup_db --decrypt FILE --out PATH` | Encrypted backups. |
| `prune_old_data` | Delete old audit logs and read notifications (retention settings). |
| `reset_2fa <username>` | Turn off 2FA for a user who lost their device. |
| `seed_demo [--reset]` | Demo data with growth history (DEBUG only). |
| `createcachetable` | Cache table for the login lockout (first run). |

## Scheduling
Pick one way to run checks:
```bash
# Linux cron (every 5 minutes, daily report, daily backup, weekly cleanup)
*/5 * * * *  cd /srv/order_platform && venv/bin/python manage.py run_checks
0 9 * * *    cd /srv/order_platform && venv/bin/python manage.py send_client_reports
30 2 * * *   cd /srv/order_platform && venv/bin/python manage.py backup_db
0 3 * * 0    cd /srv/order_platform && venv/bin/python manage.py prune_old_data
```
Or use Celery + Redis (`config/celery.py`, `orders/tasks.py`). Windows: use Task Scheduler or
`python manage.py run_checks --loop --every 300`.

## Security notes
* Roles are enforced on the server for every URL; the sidebar only hides what you cannot use.
* Login lockout after repeated failures, optional 2FA, session list, audit log with secret redaction.
* Google refresh tokens and 2FA secrets are encrypted; backups are encrypted.
* Client portal tokens are random (about 256 bits) and can be rotated at any time.
* If a key was ever pasted in chat or committed, **rotate it** (YouTube key, OAuth secret, Fernet keys).

## Tests
```bash
python manage.py test
```
Covers calculations, workflow, observations, monitoring, reports and exports, audit, settings, backups,
growth estimate, anomaly alerts, portal privacy, PDF/email delivery, 2FA, sessions, AI summary and smart search
(email, AI and WhatsApp calls are mocked, so tests need no internet or key; 169 tests).

## Project layout
```
config/            settings, urls, celery
accounts/          users, roles, login, 2FA (twofactor.py), sessions, security pages
core/              dashboard, settings screen, AI client (ai.py), backups, commands
customers/         customers, portal link controls, report buttons
ytchannels/        YouTube channels
orders/            orders, workflow, observations, monitoring, forecast.py, anomalies.py, insights.py
portal/            public read-only client portal
reports/           staff reports, exports, client_report.py (PDF), delivery.py (email), whatsapp.py (Cloud API)
googleaccounts/    Google OAuth connections
notifications/     in-app notifications
audit/             audit log
templates/ static/ HTML templates, CSS (themes), JS (charts, theme, AI buttons), bundled Chart.js
```

## Deployment (Contabo / any Ubuntu server)
The `deploy/` folder deploys the project with PostgreSQL, Gunicorn, Nginx and scheduled jobs.

1. **GitHub (private repository).** `.env` and `deploy/env.production` are in `.gitignore`, so your keys are not pushed.
   ```bash
   git init && git add . && git commit -m "Order Platform"
   git branch -M main
   git remote add origin https://github.com/<user>/<repo>.git
   git push -u origin main
   ```
   Check on GitHub that `.env` is not listed.
2. **Copy the env file to the server** (this file holds your real keys):
   ```bash
   scp deploy/env.production root@167.86.77.100:/root/order.env
   ```
3. **On the server** (a GitHub personal access token with read access to the repo is used for a private repo):
   ```bash
   ssh root@167.86.77.100
   git clone https://<token>@github.com/<user>/<repo>.git /tmp/op && bash /tmp/op/deploy/deploy.sh https://<token>@github.com/<user>/<repo>.git
   ```
   Defaults: address `http://167.86.77.100:8090/`. Change with `PORT=8095 HOST=1.2.3.4 bash deploy.sh ...`.
   The script installs packages, creates the database, makes a new `SECRET_KEY`, starts Gunicorn, configures Nginx and the cron jobs.
4. Create the first admin: `sudo -u orderplatform bash -c 'cd /srv/order_platform && venv/bin/python manage.py createsuperuser'`
5. **Later updates:** push to GitHub, then on the server `bash /srv/order_platform/deploy/update.sh`.

Notes
* Without a domain the site runs on plain http (`USE_HTTPS=False`). Passwords then travel unencrypted, so use a domain with HTTPS as soon as you can: point the domain to the server, install a certificate (`certbot --nginx`), set `USE_HTTPS=True`, `SITE_URL=https://domain`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, then restart (`systemctl restart order_platform`).
* **Google sign-in needs a domain:** Google does not accept a raw IP address as an OAuth redirect URI. Connecting Google accounts will only work once the site has a domain name (and that redirect URI is added in Google Cloud Console).
* Logs: `journalctl -u order_platform`, `/var/log/order_platform_checks.log`. Service: `systemctl status order_platform`.
* The `WHATSAPP_TOKEN` from Meta's test page expires in 24 hours: create a permanent System user token for the server.

## Deployment checklist
1. `DEBUG=False`, a new `SECRET_KEY`, correct `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SITE_URL` (https).
2. PostgreSQL, Gunicorn + Nginx, HTTPS, `python manage.py collectstatic` (the deploy script does all of this).
3. Set `ADMIN_URL` to a private path; consider `REQUIRE_2FA=True`.
4. Configure SMTP; the scheduled jobs (`run_checks`, `send_client_reports`, `backup_db`, `prune_old_data`) are installed by the script.
5. Rotate any key that was exposed. Test a backup restore with `backup_db --verify`.
