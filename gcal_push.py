#!/usr/bin/env python3
"""Create the class and training events on Google Calendar via the API.

Needs a DESKTOP OAuth client, because a desktop client can do the loopback
consent flow; the browser client the web app uses cannot. Point SECRET at its
downloaded client_secret json.

Event ids are derived from a stable key, so re-running UPDATES each event
instead of creating a second copy. That is the whole idempotency story --
there is no delete-and-recreate and nothing to orphan.

    python3 gcal_push.py            # show what would be written
    python3 gcal_push.py --push     # write it
"""
import sys, hashlib, datetime as dt
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
SECRET = Path.home() / "Claude/emailManager/credentials/client_secret.json"
TOKEN = Path.home() / "laser/.gcal_token.json"
TZ = "America/Los_Angeles"
PAD = 30  # minutes of slack after a final before a bumped workout starts

TERM_START, TERM_END = dt.date(2026, 9, 24), dt.date(2026, 12, 4)
TRAIN_START = dt.date(2026, 9, 12)
WD = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

# summary, byday, start, end, location, until(None = open ended)
CLASSES = [
    ("ECON 1 Lecture",            ["TU","TH"], "09:30", "10:45", "Haines Hall 39"),
    ("EPS SCI 70A Lecture",       ["TU","TH"], "11:00", "12:15", "De Neve Plaza Commons P350"),
    ("CHIN 1 Lecture",            ["MO","WE"], "12:30", "13:45", "Royce Hall 150"),
    ("CHIN 1 Lecture (Fri)",      ["FR"],      "13:00", "13:50", "Royce Hall 148"),
    ("MGMT 1A Lecture",           ["MO","WE"], "14:00", "15:15", "Entrepreneurs Hall C314"),
    ("MGMT 1A Discussion 2D",     ["MO"],      "11:00", "11:50", "Royce Hall 154"),
    ("EPS SCI 70A Discussion 1H", ["TH"],      "13:00", "14:50", "Geology Building 5644"),
    ("ECON 1 Discussion 1F",      ["WE"],      "17:00", "17:50", "Bunche Hall 3211"),
]
FINALS = [
    ("CHIN 1 FINAL",      dt.date(2026,12,5),  "11:30", "14:30"),
    ("MGMT 1A FINAL",     dt.date(2026,12,7),  "18:30", "21:30"),
    ("EPS SCI 70A FINAL", dt.date(2026,12,9),  "08:00", "11:00"),
    ("ECON 1 FINAL",      dt.date(2026,12,11), "11:30", "14:30"),
]

# Dropped courses whose events are already on the calendar. Insert/update never
# removes anything, so name the dead keys here and --push deletes them.
STALE = ["class:JAPAN 75 Lecture", "class:JAPAN 75 Discussion 1E", "final:JAPAN 75 FINAL"]
TRAINING = [
    ("Upper + VO2",   ["SA"], "07:30", 107, "Upper lifting 70m, then VO2 4x4 @ 8.9 mph, 37m"),
    ("Lower",         ["SU"], "07:30",  65, "Lower lifting 65m"),
    ("Upper",         ["MO"], "07:30",  70, "Upper lifting 70m"),
    ("Lower",         ["TU"], "07:30",  65, "Lower lifting 65m"),
    ("Upper + Speed", ["WE"], "07:30",  95, "Upper lifting 70m, then speed 6x30s @ 12.5 mph, 25m"),
]


def eid(key):
    # Google event ids allow only a-v and 0-9; hex qualifies.
    return "laser" + hashlib.sha1(key.encode()).hexdigest()[:24]


def first_on_or_after(d, days):
    return min(d + dt.timedelta(days=(WD[x] - d.weekday()) % 7) for x in days)


def at(d, hhmm):
    h, m = map(int, hhmm.split(":"))
    return dt.datetime(d.year, d.month, d.day, h, m).isoformat()


def until_utc(d):
    # RRULE UNTIL must be UTC when DTSTART carries a timezone. PST = UTC-8.
    return (dt.datetime(d.year, d.month, d.day, 23, 59, 59) + dt.timedelta(hours=8)).strftime("%Y%m%dT%H%M%SZ")


def collisions(days, start, mins):
    """(date, new start) for every training occurrence a final exam runs over."""
    out = []
    for day in sorted({d for _, d, _, _ in FINALS if WD[days[0]] == d.weekday()}):
        h, m = map(int, start.split(":"))
        ws = dt.datetime(day.year, day.month, day.day, h, m)
        we = ws + dt.timedelta(minutes=mins)
        busy = [(dt.datetime.fromisoformat(at(day, a)), dt.datetime.fromisoformat(at(day, b)))
                for _, d, a, b in FINALS if d == day]
        if any(bs < we and ws < be for bs, be in busy):
            out.append((day, max(be for _, be in busy) + dt.timedelta(minutes=PAD)))
    return out


def events():
    out = []
    for summary, days, s, e, loc in CLASSES:
        d = first_on_or_after(TERM_START, days)
        out.append({
            "id": eid("class:" + summary), "summary": summary, "location": loc,
            "start": {"dateTime": at(d, s), "timeZone": TZ},
            "end":   {"dateTime": at(d, e), "timeZone": TZ},
            "recurrence": [f"RRULE:FREQ=WEEKLY;BYDAY={','.join(days)};UNTIL={until_utc(TERM_END)}"],
            "description": "UCLA Fall 2026",
        })
    for summary, d, s, e in FINALS:
        out.append({
            "id": eid("final:" + summary), "summary": summary,
            "start": {"dateTime": at(d, s), "timeZone": TZ},
            "end":   {"dateTime": at(d, e), "timeZone": TZ},
            "description": "UCLA Fall 2026 final exam. Room posted 11/23.",
        })
    for summary, days, s, mins, desc in TRAINING:
        d = first_on_or_after(TRAIN_START, days)
        h, m = map(int, s.split(":"))
        end = (dt.datetime(d.year, d.month, d.day, h, m) + dt.timedelta(minutes=mins))
        key = "train:" + days[0] + ":" + summary
        body = {
            "id": eid(key), "summary": summary,
            "start": {"dateTime": at(d, s), "timeZone": TZ},
            "end":   {"dateTime": end.isoformat(), "timeZone": TZ},
            "recurrence": [f"RRULE:FREQ=WEEKLY;BYDAY={','.join(days)}"],
            "colorId": "11",   # Tomato -- training reads red at a glance
            "description": desc + "\nBuffers are NOT in this block: Laser pads 15 min each side.",
        }
        # A final is immovable; the workout is not. Drop the colliding occurrence
        # and re-add it that same day, PAD after the exam ends.
        for day, free in collisions(days, s, mins):
            body["recurrence"].append(f"EXDATE;TZID={TZ}:{day:%Y%m%d}T{s.replace(':','')}00")
            out.append({
                "id": eid(key + ":moved:" + day.isoformat()), "summary": summary + " (moved)",
                "start": {"dateTime": free.isoformat(), "timeZone": TZ},
                "end":   {"dateTime": (free + dt.timedelta(minutes=mins)).isoformat(), "timeZone": TZ},
                "colorId": "11",
                "description": desc + f"\nMoved off {s} -- a final exam owns that slot.",
            })
        out.append(body)
    return out


def service():
    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as err:
            # A revoked or expired refresh token is not fatal -- it just means we
            # need consent again. Anything else (network, clock skew) still raises.
            if "invalid_grant" not in str(err):
                raise
            print(f"Stored Google token is dead ({err.args[0]}); asking for consent again.",
                  file=sys.stderr)
            creds = None
    if not creds or not creds.valid:
        print("Opening a browser for consent -- pick the SAME Google account Laser uses.")
        creds = InstalledAppFlow.from_client_secrets_file(str(SECRET), SCOPES).run_local_server(port=0)
        TOKEN.write_text(creds.to_json())
        TOKEN.chmod(0o600)
    return build("calendar", "v3", credentials=creds)


if __name__ == "__main__":
    # ponytail: the only logic here worth a check is the collision math.
    assert collisions(["WE"], "07:30", 95) == [(dt.date(2026, 12, 9), dt.datetime(2026, 12, 9, 11, 30))]
    assert collisions(["SA"], "07:30", 107) == []   # CHIN final at 11:30, workout ends 09:17
    assert collisions(["MO"], "07:30", 70) == []    # MGMT final is an evening slot

    evs = events()
    if "--push" not in sys.argv:
        for e in evs:
            rec = e.get("recurrence", ["one-off"])[0].replace("RRULE:", "")
            print(f"  {e['summary']:28} {e['start']['dateTime'][:16]}  {rec}")
        print(f"\n{len(evs)} events. Re-run with --push to write them.")
        raise SystemExit

    svc = service()
    # calendars().get needs the Calendars resource, which calendar.events does not
    # grant. The owner comes back on the written event instead.
    made = upd = 0
    owner = "?"
    for e in evs:
        try:
            r = svc.events().insert(calendarId="primary", body=e).execute()
            owner = (r.get("organizer") or {}).get("email", owner)
            made += 1; print(f"  created  {e['summary']}")
        except HttpError as err:
            if err.resp.status != 409:
                raise
            r = svc.events().update(calendarId="primary", eventId=e["id"], body=e).execute()
            owner = (r.get("organizer") or {}).get("email", owner)
            upd += 1; print(f"  updated  {e['summary']}")
    gone = 0
    for key in STALE:
        try:
            svc.events().delete(calendarId="primary", eventId=eid(key)).execute()
            gone += 1; print(f"  deleted  {key}")
        except HttpError as err:
            if err.resp.status not in (404, 410):
                raise
    print(f"\n{made} created, {upd} updated, {gone} deleted on the primary calendar of {owner}")
