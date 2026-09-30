---
project: laser
type: app
tags: [yt/app, project/laser]
---
# Laser

A single-file todo app that time-blocks itself around your calendar.

Live: https://todo-focus-8rn.pages.dev

The whole client is one `index.html`, with no build step, no framework and no dependencies.
Open the file and it runs.

## Focus modes

Both modes work off the clock rather than a filter you set by hand. Given the current time:

- **Tunnel** shows only the block you are supposed to be in right now.
- **Laser** shows that block plus anything from earlier blocks you did not finish.

Neither can be gamed by reordering a list, since what you see depends on the wall clock and
on what you actually completed.

## Auto time-blocking

`planDay` and `freeGaps` in `index.html` read the day's calendar events, find the gaps between
them, and lay today's tasks into those gaps. Every calendar event keeps a 15-minute pad either
side. Gaps under 15 minutes are not treated as blocks at all, and tasks under 15 minutes get
batched together instead of each taking its own slot.

## Scripts

`plan_push.py` runs the same gap maths outside the browser, so the blocks become real Google
Calendar events instead of a view that dies with the tab. Every block it writes carries
`extendedProperties.private.laserplan`, which is how a re-run finds and clears its own previous
blocks without touching anything else on the calendar.

    python3 plan_push.py            # print the plan
    python3 plan_push.py --push     # write it to the calendar
    python3 plan_push.py --no-llm   # skip ollama, every task is 30 min

`gcal_push.py` creates the recurring class and training events. Event ids come from a stable
key, so re-running updates each event in place rather than creating a duplicate. There is no
delete-and-recreate step, so nothing gets orphaned.

    python3 gcal_push.py            # show what would be written
    python3 gcal_push.py --push     # write it

Both default to a dry run, and `--push` is the explicit opt-in.

## Setup

Task data comes from the Todoist API and calendar data from the Google Calendar API, so both
scripts need your own credentials:

- A Google OAuth **browser** client for the web app. No client id ships in this repo, so set
  your own once from the devtools console and reload:

      localStorage.setItem("laser.gcalid", "<your-id>.apps.googleusercontent.com")

- A Google OAuth **desktop** client for the scripts. The loopback consent flow needs one and a
  browser client will not work there. `gcal_push.py` writes the resulting token to
  `.gcal_token.json`, which is gitignored and has to stay that way, because it holds a
  long-lived refresh token.
- A Todoist API token, pasted into the app. It is kept in `localStorage` and never in the repo.

`plan_push.py` estimates task durations with a local ollama model (`qwen2.5:3b`). Without
ollama running, pass `--no-llm` and every task gets a flat 30 minutes.
