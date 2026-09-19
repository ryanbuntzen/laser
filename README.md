# Laser

A single-file todo app that time-blocks itself around your calendar.

Live: https://todo-focus-8rn.pages.dev

The whole client is one `index.html` — no build step, no framework, no
dependencies. Open the file and it runs.

## Focus modes

The two modes are clock-based, not a manual filter. Given the current time:

- **Tunnel** — only the block you are supposed to be in right now.
- **Laser** — that block, plus anything from earlier blocks you did not finish.

The point is that neither mode can be gamed by reordering a list. What you see
is a function of the wall clock and what you actually completed.

## Auto time-blocking

`planDay` / `freeGaps` in `index.html` read the day's calendar events, find the
gaps between them, and lay today's tasks into those gaps. A 15-minute pad is
kept either side of every calendar event, gaps smaller than 15 minutes are not
treated as blocks at all, and tasks under 15 minutes are batched together rather
than each taking a slot.

## Scripts

`plan_push.py` is the same gap maths outside the browser, so the blocks become
real Google Calendar events instead of a view that dies with the tab. Each block
it writes carries `extendedProperties.private.laserplan`, which is how a re-run
finds and clears only its own previous blocks — nothing else on the calendar is
ever touched.

    python3 plan_push.py            # print the plan
    python3 plan_push.py --push     # write it to the calendar
    python3 plan_push.py --no-llm   # skip ollama, every task is 30 min

`gcal_push.py` creates recurring class and training events. Event ids are
derived from a stable key, so re-running updates each event in place instead of
creating a duplicate. There is no delete-and-recreate step and nothing to
orphan.

    python3 gcal_push.py            # show what would be written
    python3 gcal_push.py --push     # write it

Both default to a dry run. `--push` is always the explicit opt-in.

## Setup

Task data comes from the Todoist API and calendar data from the Google Calendar
API, so both scripts need your own credentials:

- A Google OAuth **browser** client for the web app. No client id ships in this
  repo, so set your own once from the devtools console and reload:

      localStorage.setItem("laser.gcalid", "<your-id>.apps.googleusercontent.com")

- A Google OAuth **desktop** client for the scripts — the loopback consent flow
  needs one, and a browser client will not work there. `gcal_push.py` writes the
  resulting token to `.gcal_token.json`, which is gitignored and must stay that
  way: it holds a long-lived refresh token.
- A Todoist API token, pasted into the app (kept in `localStorage`, never in the
  repo).

`plan_push.py` estimates task durations with a local ollama model
(`qwen2.5:3b`). Without ollama running, pass `--no-llm` and every task gets a
flat 30 minutes.
