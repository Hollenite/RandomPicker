# Daily Revisit Picker

Picks one previously solved question per day with weighted randomness, sends it on Telegram, and records when you tap **Done**. GitHub Actions keeps the bot awake in two public-repo windows (4 hours on, 2 hours off, 4 hours on). Secrets stay in Actions secrets; `.env` is never committed.

Timezone for every “today” is **Asia/Kolkata (IST)**.

## What you need

- Python 3.12
- A Telegram bot token and your numeric chat id
- A Groq API key and model name (for Explain / Hint / Solution)
- GitHub Actions secrets for the same values

## 1. Create the bot

1. Open Telegram and chat with [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, follow the prompts, and copy the token. That is `TELEGRAM_BOT_TOKEN`.
3. Start a chat with your new bot and send `/start`.

## 2. Find your chat id

Chat with [@userinfobot](https://t.me/userinfobot) (or similar) and copy your numeric id. That is `TELEGRAM_CHAT_ID`.

The bot ignores every update whose chat id **or** user id does not match this value.

## 3. Local `.env`

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
GROQ_API_KEY=...
GROQ_MODEL=...
PREFERRED_LANG=python
```

Optional reminder knobs (defaults shown):

```
REMINDER_START=09:00
REMINDER_END=22:00
REMINDER_INTERVAL_MIN=120
MAX_REMINDERS=8
```

Never commit `.env`.

## 4. Catalog and green set

`catalog.json` is the source of truth for every question (`id`, `difficulty`, optional `statement`). Convert a dump:

```bash
python scripts/build_catalog.py path/to/questions.csv
# or a LeetCode dump (skips paidOnly=true, maps Hard → difficult):
python scripts/build_catalog.py path/to/leetcode_problems.json --green-list scripts/green_seed.txt
```

Simple CSV/JSON columns: `id,difficulty,statement`. A LeetCode dump may use `frontendQuestionId`, `paidOnly`, `difficulty`, and `description`. `--green-list` is `id - title` per line; titles win when the listed id does not match the dump.

Then add questions you have already solved (difficulty is looked up from the catalog):

```bash
python src/add.py 316
python src/add.py 316 317 320
python src/add.py 300-315
python src/add.py --from-file ids.txt
```

On Telegram: `/add 316`, `/add 316 317 320`, or `/add 300-315`.

## 5. Test locally with `--dry-run`

`--dry-run` prints Bot API payloads and never sends HTTP.

```bash
python src/run_prepare.py --dry-run --now 2026-11-09T08:00:00
python src/run_poll.py --dry-run --now 2026-11-09T10:00:00
python src/run_poll.py --dry-run --now 2026-11-09T12:00:00
```

`--now` is an ISO datetime interpreted as IST, so you can simulate a full day of reminders at `REMINDER_INTERVAL_MIN`.

Live local run (uses `.env`, talks to Telegram):

```bash
python src/run_prepare.py
python src/run_poll.py --watch
```

`--watch` long-polls until Ctrl+C. `--watch-for 14400` (used by Actions) stops after 4 hours or at the end of the current IST window, whichever comes first.

Tests:

```bash
pytest
```

## 6. GitHub Actions secrets

In the repo: **Settings → Secrets and variables → Actions**. Add:

| Secret | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather token |
| `TELEGRAM_CHAT_ID` | Your numeric user/chat id |
| `GROQ_API_KEY` | Groq API key |
| `GROQ_MODEL` | Groq model id (no default in code) |

Optional repository variables override timing without a code change: reminder knobs, plus `WATCH_WINDOWS` (default `08:00-12:00,14:00-18:00` IST).

Schedules (UTC):

| Workflow | Cron | IST | What it does |
|---|---|---|---|
| [`.github/workflows/poll.yml`](.github/workflows/poll.yml) | `30 2 * * *` | 08:00–12:00 | Morning pick (self-heal) + live buttons |
| [`.github/workflows/poll.yml`](.github/workflows/poll.yml) | `30 8 * * *` | 14:00–18:00 | Second live window |
| [`.github/workflows/prepare.yml`](.github/workflows/prepare.yml) | manual only | — | Force a morning pick |

GitHub-hosted jobs can run up to **6 hours**; each watch is capped at 4 hours so the runner stops cleanly. Public repos do not spend Actions minutes on this. The two sessions share a concurrency group so they never overlap.

At the start of a window the bot texts `I'm awake until HH:MM IST`. When the window ends (or the job is stopped) it texts `I'm going offline. I'll be awake after …`. Between 12:00 and 14:00 IST, and after 18:00 IST, buttons sit until the next window.

Each cycle commits `state.json`, `green.json`, `log.jsonl`, and `tutor_cache.json` so a dropped runner does not lose Done / hint progress.

If you want Explain / Hint to reply outside those 8 hours, run `python src/run_poll.py --watch` locally.

A public repo makes `green.json` (which questions you have solved) visible. `catalog.json` is public problem text and is fine to share. Secrets never go in git. A private database for the attempt list can come later if you want that hidden.

## Daily flow

08:00 IST you get:

```
Good morning. Today's revisit: #142 (easy)
Open it, solve it, then tap Done.
```

Buttons while the day is still pending:

`Explain 🧠` `Hint 💡` `Solution 🔓` `Done ✅` `Reroll 🎲`

- **Done** / `/done` marks the pick revisited. `/done 142` is accepted only if 142 is today’s pending pick.
- **Reroll** / `/reroll` offers a different question, never one already offered today.
- **Explain** restates the problem (no solution).
- **Hint** advances 1 → 2 → 3, then “No more hints.”
- **Solution** asks “Sure? Try a hint first” before revealing.

Reminders fire between `REMINDER_START` and `REMINDER_END` until you tap Done or hit `MAX_REMINDERS`. After Done, buttons disappear and the morning message becomes `Done ✅ #142`.

A missed pending day is logged as `missed` the next morning and is never backfilled.

## Files

| File | Role |
|---|---|
| `catalog.json` | All questions |
| `green.json` | Progress for questions you have solved |
| `state.json` | Today’s pick, message ids, Telegram offset |
| `log.jsonl` | Append-only audit log |
| `tutor_cache.json` | Cached tutor replies, keyed by id + stage |

## Picker

Bucket draw: 55% easy, 40% medium, 3% difficult, 2% wildcard (the oldest medium). Inside a difficulty bucket, weight is `days_since_last_revisit + 1`, and never-revisited questions get `max_days_in_bucket + 2`.

## Phase 3

[`src/submitter.py`](src/submitter.py) is a `Submitter` protocol stub only. Marking a revisit happens only when you tap Done. Nothing is submitted to any website.
