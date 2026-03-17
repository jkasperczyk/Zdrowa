# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Development server
cd app && python manage.py runserver

# Apply migrations
cd app && python manage.py migrate

# Create migrations after model changes
cd app && python manage.py makemigrations

# Collect static files
cd app && python manage.py collectstatic

# Create superuser
cd app && python manage.py createsuperuser

# Generate VAPID keys (run once, store in .env)
cd app && python manage.py generate_vapid_keys

# Generate weekly AI health reports (all users)
cd app && python manage.py generate_weekly_report

# Generate report for a single user
cd app && python manage.py generate_weekly_report --phone +48123456789
```

No test framework is configured. There is no Makefile.

## Environment

The app is configured via environment variables (or a `.env` file loaded via `python-dotenv`):

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | (required) | Django secret key |
| `DJANGO_DEBUG` | `False` | Debug mode |
| `DJANGO_ALLOWED_HOSTS` | `zdrowa.pracunia.pl` | Comma-separated allowed hosts |
| `ZDROWA_DB_PATH` | `../data/db.sqlite3` | App database path |
| `WEATHERGUARD_DB` | `/opt/weatherguard/data/feedback.db` | External WeatherGuard SQLite DB (read/write for push & queue) |
| `WEATHERGUARD_TRENDS_DIR` | `/opt/weatherguard/public_media/trends` | Directory of trend PNG files |
| `VAPID_PRIVATE_KEY` | (required for push) | VAPID private key for Web Push |
| `VAPID_PUBLIC_KEY` | (required for push) | VAPID public key for Web Push |
| `VAPID_SUBJECT` | `mailto:admin@zdrowa.pracunia.pl` | VAPID contact |
| `OPENAI_API_KEY` | (optional) | GPT-4o-mini for weekly reports |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model for reports |

## Architecture

### Overview

Django 5 health alert portal ("Zdrowa" = Polish for "Healthy"). The app wraps an external **WeatherGuard** system, providing a user-facing web UI for health alerts driven by weather conditions (migraine, allergy, heart). Notifications are delivered via **Web Push / PWA** — no SMS/Twilio.

### Data Flow

```
User browser (PWA) → Django views → portal DB (SQLite, owns users/profiles)
                                  → WeatherGuard DB (feedback.db, shared with Health_Guard)
                                      ├── alerts_queue  (runner writes → Zdrowa reads & pushes)
                                      ├── forecast_alerts (predictive risk windows)
                                      ├── push_subscriptions (per-user browser endpoints)
                                      ├── weekly_reports (AI-generated health summaries)
                                      ├── readings / alerts / symptom_log / wellbeing
                                      └── Trends filesystem (read-only: PNG charts by phone)
```

### Key Modules

- **`portal/models.py`** — Two models:
  - `UserProfile` (one-to-one with Django `User`): phone (E.164), gender, alert preferences (migraine/allergy/heart), menstrual cycle data, force-password-change flag.
  - `DailyWellbeing`: daily stress/exercise level per user (indexed by user + day).

- **`portal/views.py`** — Auth views (login, logout, password change), dashboard, settings, staff-only admin tools, PWA service worker, push subscription endpoint.

- **`portal/views_wg.py`** — Views reading from WeatherGuard integration layer: sensor data, alerts, trends, symptom log, wellbeing, weekly reports + correlation.

- **`portal/wg_sources.py`** — Data access layer for WeatherGuard SQLite (`feedback.db`). Direct SQLite3 queries (not Django ORM). Includes push subscription management, alerts queue processing, correlation computation, and tip generation.

- **`portal/context_processors.py`** — Injects `vapid_public_key` and `unread_count` (unsent alerts_queue entries) into every authenticated template.

- **`portal/users_import.py`** — Parses user import files (JSON, CSV, space-separated).

- **`portal/middleware.py`** — `ForcePasswordChangeMiddleware`: redirects non-staff users to `/password/change/` if `UserProfile.must_change_password` is set.

### URL Structure

```
/                        → dashboard (with Porada dnia + Prognoza 12h)
/alerts/                 → health alerts history (Web Push, no SMS)
/data/                   → recent sensor readings
/trends/                 → list trend charts
/trends/file/<fname>     → serve trend PNG (phone-number-validated)
/raporty/                → weekly AI reports + correlation chart
/settings/               → edit profile (name, phone, gender, alert types, cycle)
/wellbeing/              → daily wellbeing entry
/symptom_log/            → symptom log (records feats_json for ML)
/export/                 → CSV export
/password/change/        → password change
/push/subscribe/         → POST: save/remove push subscription
/account/export/         → download ZIP of all personal data
/account/delete/         → POST: delete account (password required)
/account/deleted/        → confirmation page (no login required)
/register/               → registration (disabled by default)
/admin-tools/            → staff: user management
/admin-tools/push-queue/ → staff: process pending push queue
/admin/                  → Django admin
```

### Web Push / PWA

- VAPID keys generated once with `python manage.py generate_vapid_keys`.
- `base.html` requests push permission on load and POSTs subscription to `/push/subscribe/`.
- Browser subscriptions stored in `feedback.db` `push_subscriptions` table.
- Health_Guard runner writes alert triggers to `alerts_queue` table.
- `/admin-tools/push-queue/` (staff) or a cron job calls `process_alerts_queue()` to dispatch pushes.
- Service worker (`/sw.js`) handles push events and notification clicks.

### Auth Notes

- Login uses email as username.
- New users get temporary passwords and `must_change_password=True`, enforced by middleware.
- Minimum password length: 8 characters + at least one digit (`portal.validators.HasNumberValidator`).
- Login rate limiting: 5 failed attempts → account locked for 15 minutes (`UserProfile.failed_login_count`, `locked_until`).
- Registration disabled by default (`REGISTRATION_OPEN = False` in settings.py); `/register/` shows a "closed" page.

### Account Management

- `/account/export/` — generates a ZIP archive with profile.json + all personal data CSVs (wellbeing, readings, alerts, symptoms, reports).
- `/account/delete/` — POST with password confirmation; purges all user data from both DBs, logs deletion to `deletions_log` table, then deletes the Django account and redirects to `/account/deleted/`.
- Staff/superuser accounts are protected from self-deletion via the UI.
