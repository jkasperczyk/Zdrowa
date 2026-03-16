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
| `WEATHERGUARD_DB` | `/opt/weatherguard/data/feedback.db` | External WeatherGuard SQLite DB (read-only) |
| `WEATHERGUARD_TRENDS_DIR` | `/opt/weatherguard/public_media/trends` | Directory of trend PNG files |
| `WEATHERGUARD_SMS_USERS` | `/opt/weatherguard/data/sms_users.json` | SMS subscription JSON file |

## Architecture

### Overview

Django 5 health alert portal ("Zdrowa" = Polish for "Healthy"). The app wraps an external **WeatherGuard** system, providing a user-facing web UI for health alerts driven by weather conditions (migraine, allergy, heart).

### Data Flow

```
User browser → Django views → portal DB (SQLite, owns users/profiles)
                            → WeatherGuard DB (SQLite, read-only: sensor readings, alerts)
                            → SMS users JSON (read/write: subscription status)
                            → Trends filesystem (read-only: PNG charts keyed by phone number)
```

### Key Modules

- **`portal/models.py`** — Two models:
  - `UserProfile` (one-to-one with Django `User`): phone (E.164), gender, alert preferences (migraine/allergy/heart), SMS subscription flag, menstrual cycle data, force-password-change flag.
  - `DailyWellbeing`: daily stress/exercise level per user (indexed by user + day).

- **`portal/views.py`** — Auth views (login, logout, password change), dashboard, settings, and staff-only admin tools (create/import/reset/delete users). Staff-only views check `request.user.is_staff`.

- **`portal/views_wg.py`** — Views that read from the WeatherGuard integration layer: sensor data display, health alerts, and trend chart serving. Trend file access is restricted: the requested filename must contain the user's own phone number.

- **`portal/wg_sources.py`** — Data access layer for all external WeatherGuard sources. Direct SQLite3 queries (not Django ORM) against `WEATHERGUARD_DB`. Also manages SMS subscription JSON and trend file discovery.

- **`portal/users_import.py`** — Parses user import files (JSON, CSV, or space-separated) from `/opt/weatherguard/config/users.txt`. Deduplicates by phone number.

- **`portal/middleware.py`** — `ForcePasswordChangeMiddleware`: redirects non-staff users to `/password/change/` if `UserProfile.must_change_password` is set. Exempts logout, password change, admin, and static file URLs.

### URL Structure

```
/                    → dashboard
/alerts/             → health alerts + SMS toggle
/data/               → recent sensor readings (up to 1500 rows from WeatherGuard DB)
/trends/             → list trend charts
/trends/file/<fname> → serve trend PNG (phone-number-validated)
/settings/           → edit profile (name, phone, gender, alert types)
/password/change/    → password change
/admin-tools/        → staff: user management (create, import, reset, delete)
/admin-tools/user/<id>/ → staff: edit individual user
/admin/              → Django admin
```

### Auth Notes

- Login uses email as username (`USERNAME_FIELD` effectively via admin setup; check user creation logic in `views.py`).
- New users created by staff get temporary passwords and `must_change_password=True`, enforced by middleware.
- Minimum password length: 10 characters.
