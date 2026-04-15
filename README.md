# Quench

Quench is an SMS-first hydration reminder app. Users answer a short quiz, verify their phone number, and then receive personalized water reminders throughout the day. The interaction loop stays simple:

- `DONE` logs the latest reminder
- `SKIP` snoozes the next reminder by 4 hours
- `STOP` pauses the plan
- `HELP` explains the commands

The repo is intentionally structured so `fresh-bowl` and `fresh-sheets` can fork from the same engine later.

## Stack

- Python 3.11+
- Flask + Flask-SQLAlchemy
- PostgreSQL on Railway for production
- APScheduler for reminder sweeps
- Twilio for SMS send and inbound webhooks
- Single-file frontend in [`frontend/index.html`](/Users/michaelpyon/Documents/quench-hydration/frontend/index.html)

## Project Layout

```text
app.py             Flask API routes and Twilio webhook
models.py          SQLAlchemy models for users, reminders, and daily logs
calculator.py      Hydration goal math and reminder schedule generation
scheduler.py       APScheduler jobs for reminders, daily schedules, and weekly summaries
sms.py             Twilio send helper plus reminder/copy rotation
frontend/index.html Single-file onboarding + dashboard UI
tests/test_app.py  Basic API and calculator coverage
```

## Environment Variables

Copy [`.env.example`](/Users/michaelpyon/Documents/quench-hydration/.env.example) to `.env` and set:

- `DATABASE_URL`
- `SECRET_KEY`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER`
- `EXPOSE_VERIFY_CODE`
- `RUN_SCHEDULER`

Notes:

- Local development can use `sqlite:///quench.db`
- Railway will inject `DATABASE_URL` for PostgreSQL
- If Twilio env vars are missing, SMS sends are mocked in logs so the app is still testable locally
- `EXPOSE_VERIFY_CODE=1` is useful locally because the API will return the verification code in JSON

## Run Locally

1. Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the backend:

```bash
python app.py
```

The Flask API runs on `http://127.0.0.1:5001`.

4. Open the frontend:

- Open [`frontend/index.html`](/Users/michaelpyon/Documents/quench-hydration/frontend/index.html) directly in a browser, or
- Serve the repo with a tiny static server:

```bash
python -m http.server 8080
```

Then visit:

- `http://127.0.0.1:8080/frontend/index.html`

If your API is not on the default port or domain, pass it via query string:

- `http://127.0.0.1:8080/frontend/index.html?apiBase=http://127.0.0.1:5001`

## Tests

Run the test pass with:

```bash
pytest
```

## Twilio Setup

1. Buy or provision a Twilio number with SMS enabled.
2. Put the credentials in your env vars:

```bash
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1...
```

3. Point the inbound webhook at:

```text
https://your-railway-app.railway.app/sms/inbound
```

4. Twilio should send `POST` requests. No extra auth layer is needed for v1 beyond Twilio's own webhook source.

Commands users can text back:

- `DONE`
- `SKIP`
- `HELP`
- `STOP`

## Railway Deploy

1. Create a new Railway project from this repo.
2. Add a PostgreSQL service.
3. Set the env vars from the list above.
4. Railway will use [`Procfile`](/Users/michaelpyon/Documents/quench-hydration/Procfile):

```text
web: gunicorn app:app
```

5. After deploy, update Twilio's inbound webhook to:

```text
https://your-railway-app.railway.app/sms/inbound
```

## V1 Checklist

- Quiz creates or updates a user
- Verification code flow works
- Schedules are generated from wake/sleep window plus hydration math
- Reminder sweeps run through APScheduler
- DONE and SKIP update the plan
- Dashboard shows today's progress and streak dots
