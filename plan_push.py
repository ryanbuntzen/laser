#!/usr/bin/env python3
"""Time-block today's Todoist tasks into the free gaps on Google Calendar.

The planner already exists in index.html (freeGaps / planDay); this is the same
maths outside the browser so the blocks become real calendar events instead of a
view that dies with the tab.

Blocks carry extendedProperties.private.laserplan = the date, which is how a
re-run finds and clears its own previous blocks. Nothing else on the calendar is
ever touched.

    python3 plan_push.py            # print the plan
    python3 plan_push.py --push     # write it to the calendar
    python3 plan_push.py --no-llm   # skip ollama, every task is 30 min
"""
import sys, re, json, urllib.request, urllib.parse, datetime as dt
from pathlib import Path
from gcal_push import service, TZ            # reuse the OAuth + client

TODOIST = "https://api.todoist.com/api/v1/tasks/filter"
OLLAMA, MODEL = "http://localhost:11434/api/generate", "qwen2.5:3b"
WINDOW = (9 * 60, 18 * 60)   # 09:00-18:00, widened below by anything outside it
EVENING = (18 * 60, 22 * 60) # where a task labelled "Evening" goes instead
PAD = 15                     # minutes of buffer each side of a calendar event
MIN_GAP = 15                 # a sliver smaller than this is not a block
CHORE = 15                   # tasks this short are batched into one block
# Recurring Todoist dailies that duplicate the training plan. gcal_push.py owns the
# gym schedule outright -- including which days are rest days -- so these never get
# a block, and a rest day stays a rest day instead of growing a phantom workout.
COVERED = re.compile(r"^(workout|v0?2|vo2|cardio|lift|speed|gym)\b", re.I)
DEFAULT = 30


def hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


# --- inputs ---------------------------------------------------------------

def todoist():
    token = (Path.home() / ".todoist_token").read_text().strip()
    url = TODOIST + "?" + urllib.parse.urlencode({"query": "(today | overdue)"})
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    return json.load(urllib.request.urlopen(req))["results"]


def busy(svc, day):
    lo = dt.datetime.combine(day, dt.time.min).isoformat() + "-07:00"
    hi = dt.datetime.combine(day, dt.time.max).isoformat() + "-07:00"
    r = svc.events().list(calendarId="primary", timeMin=lo, timeMax=hi,
                          singleEvents=True, orderBy="startTime").execute(num_retries=5)
    out = []
    for e in r.get("items", []):
        if (e.get("extendedProperties", {}).get("private", {}).get("laserplan")):
            continue                      # our own block from an earlier run
        s, en = e["start"].get("dateTime"), e["end"].get("dateTime")
        if not s:
            continue                      # all-day events do not consume hours
        s, en = dt.datetime.fromisoformat(s), dt.datetime.fromisoformat(en)
        out.append({"s": s.hour * 60 + s.minute, "en": en.hour * 60 + en.minute,
                    "what": e.get("summary", "")})
    return out


# --- estimation -----------------------------------------------------------

def ask_ollama(tasks):
    """Minutes per task, guessed by a local model. Loud on failure, never fatal."""
    lines = "\n".join(f"{i}. {t['content']}" for i, t in enumerate(tasks))
    prompt = (
        "Estimate how many minutes each personal task realistically takes.\n"
        "Quick grooming/chores are 5-10. Errands and calls 15-30. Writing, studying,\n"
        "editing and workouts are 45-120.\n"
        f"Tasks:\n{lines}\n\n"
        'Reply with JSON only: {"minutes": {"0": 5, "1": 30, ...}} covering every index.')
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                       "format": "json", "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    try:
        raw = json.load(urllib.request.urlopen(req, timeout=180))["response"]
        got = json.loads(raw)["minutes"]
    except Exception as err:                      # ollama down, or 3B talked nonsense
        print(f"!! ollama unusable ({err.__class__.__name__}: {err}); using {DEFAULT} min for everything",
              file=sys.stderr)
        return {}
    # ponytail: a 3B model WILL occasionally emit junk. Clamp rather than trust.
    return {int(k): max(5, min(180, int(v))) for k, v in got.items() if str(k).isdigit()}


def stated(t):
    """Your own estimate, if you gave one: a `45m` / `2h` label, or [45m] in the text.

    Todoist's real `duration` field is Pro-only and this account is free, so the
    label is the knob. Labels are free and sort in the app, which is close enough.
    """
    for src in t["labels"] + re.findall(r"\[([^\]]+)\]", t["content"]):
        m = re.fullmatch(r"(\d+)\s*(m|min|h|hr)", src.strip(), re.I)
        if m:
            return int(m[1]) * (60 if m[2].lower().startswith("h") else 1)
    return None


def estimate(tasks, use_llm):
    unknown = [t for t in tasks if not stated(t) and not (t.get("duration") or {}).get("amount")]
    guess = ask_ollama(unknown) if (use_llm and unknown) else {}
    for t in tasks:
        d = t.get("duration") or {}
        t["mins"] = (stated(t)
                     or (d["amount"] if d.get("unit") == "minute" else None)
                     or guess.get(unknown.index(t) if t in unknown else -1, DEFAULT))
    return tasks


# --- the planner (same shape as freeGaps / planDay in index.html) ----------

def free_gaps(evs, win, pad=PAD, widen=True):
    lo = min([win[0]] + [e["s"] - pad for e in evs]) if widen else win[0]
    hi = max([win[1]] + [e["en"] + pad for e in evs]) if widen else win[1]
    evs = [e for e in evs if e["en"] > lo and e["s"] < hi]
    gaps, at = [], lo
    for e in sorted(evs, key=lambda e: e["s"]):
        if e["s"] - pad > at:
            gaps.append({"s": at, "en": e["s"] - pad})
        at = max(at, e["en"] + pad)
    if at < hi:
        gaps.append({"s": at, "en": hi})
    return [g for g in gaps if g["en"] - g["s"] >= MIN_GAP]


def pack(items, gaps):
    """First gap the whole item fits in. Never split, never leave a usable gap idle."""
    at = [g["s"] for g in gaps]          # a cursor per gap, so a skipped gap stays open
    out = []
    for it in items:
        for i, g in enumerate(gaps):
            if at[i] + it["mins"] <= g["en"]:
                out.append({**it, "s": at[i]})
                at[i] += it["mins"]
                break
        else:
            out.append({**it, "s": None})   # no room left today
    return sorted(out, key=lambda x: (x["s"] is None, x["s"] or 0))


def plan(tasks, evs, now=None):
    """Day tasks into the working window, "Evening"-labelled ones into the night.

    `now` shrinks the day window so the 13:00 replan books the afternoon it still
    has, rather than rewriting a morning that has already happened.
    """
    win = list(WINDOW)
    if now is not None:
        win[0] = max(win[0], now)
    tasks.sort(key=lambda t: (-t["priority"], t["mins"]))   # Todoist p1 == priority 4
    night = [t for t in tasks if "Evening" in t["labels"]]
    day = [t for t in tasks if t not in night]

    chores = [t for t in day if t["mins"] <= CHORE]
    items = [{"name": t["content"], "mins": t["mins"], "id": t["id"], "ids": [t["id"]]}
             for t in day if t["mins"] > CHORE]
    if chores:
        # One early sweep, not fifteen scattered blocks -- and doing them first
        # keeps a 15-minute errand from being the thing that finds no room at 5pm.
        items.insert(0, {"name": "Chores: " + ", ".join(t["content"] for t in chores),
                         "mins": sum(t["mins"] for t in chores), "id": "chores",
                         "ids": [t["id"] for t in chores]})

    # free_gaps widens the window around early events, which would undo the `now`
    # clamp -- so clip what it returns back to the start of the day we still have.
    day_gaps = [{"s": max(g["s"], win[0]), "en": g["en"]}
                for g in free_gaps(evs, win) if g["en"] - max(g["s"], win[0]) >= MIN_GAP]
    out = pack(items, day_gaps)
    out += pack([{"name": t["content"], "mins": t["mins"], "id": t["id"], "ids": [t["id"]]}
                 for t in night],
                free_gaps(evs, list(EVENING), widen=False))
    return out


# --- output ---------------------------------------------------------------

def write(svc, blocks, day):
    old = svc.events().list(calendarId="primary", privateExtendedProperty=f"laserplan={day}",
                            singleEvents=True).execute(num_retries=5).get("items", [])
    for e in old:
        svc.events().delete(calendarId="primary", eventId=e["id"]).execute(num_retries=5)
    n = 0
    for b in blocks:
        if b["s"] is None:
            continue
        start = dt.datetime.combine(day, dt.time(b["s"] // 60, b["s"] % 60))
        svc.events().insert(calendarId="primary", body={
            "summary": b["name"][:120],
            "start": {"dateTime": start.isoformat(), "timeZone": TZ},
            "end": {"dateTime": (start + dt.timedelta(minutes=b["mins"])).isoformat(), "timeZone": TZ},
            "colorId": "8",                        # Graphite -- planned work, not a commitment
            "description": "Planned by plan_push.py from Todoist. Edits here are overwritten "
                           "on the next run; change the task in Todoist instead.",
            # Laser reads these back so its blue slot times are this exact plan,
            # rather than a second one it computes in the browser.
            "extendedProperties": {"private": {"laserplan": str(day), "task": b["id"],
                                               "tasks": ",".join(b["ids"])[:1000]}},
        }).execute(num_retries=5)
        n += 1
    return len(old), n


def selftest():
    g = free_gaps([{"s": 600, "en": 660}], [540, 1080])
    assert [(x["s"], x["en"]) for x in g] == [(540, 585), (675, 1080)], g
    assert free_gaps([{"s": 540, "en": 1080}], [540, 1080]) == []
    assert free_gaps([], [540, 600])[0]["en"] == 600
    # an event outside the window widens the day rather than being planned over
    assert free_gaps([{"s": 450, "en": 480}], [540, 1080])[0]["s"] == 495
    p = pack([{"mins": 60}, {"mins": 60}], [{"s": 540, "en": 600}, {"s": 700, "en": 800}])
    assert [x["s"] for x in p] == [540, 700], p     # exactly fills the first gap, then jumps
    p = pack([{"mins": 90}, {"mins": 30}], [{"s": 540, "en": 600}, {"s": 700, "en": 800}])
    assert [(x["mins"], x["s"]) for x in p] == [(30, 540), (90, 700)], p  # 90 skips gap 1 whole,
    assert pack([{"mins": 90}], [{"s": 540, "en": 600}])[0]["s"] is None  # and 30 still uses it
    assert pack([{"mins": 30}], [])[0]["s"] is None
    # a replan at 15:00 must not book the morning it already missed
    one = [{"content": "x", "mins": 30, "priority": 1, "labels": [], "id": "1"}]
    assert plan(one, [], now=900)[0]["ids"] == ["1"]          # every block names its tasks
    assert plan(one, [], now=900)[0]["s"] == 900
    # an early event must not drag the clamped window back before `now`
    assert plan(one, [{"s": 450, "en": 557, "what": "gym"}], now=900)[0]["s"] == 900
    assert plan(one, [], now=23 * 60)[0]["s"] is None   # nothing left today is not a crash
    assert stated({"labels": ["90m"], "content": "x"}) == 90
    assert stated({"labels": [], "content": "write essay [2h]"}) == 120
    assert stated({"labels": ["home"], "content": "x"}) is None
    assert COVERED.match("V02")
    assert not COVERED.match("work on essay")   # \b keeps it off ordinary words
    assert not COVERED.match("liftoff notes")
    assert COVERED.match("workout") and not COVERED.match("training")   # training is an evening session
    # the evening pass must ignore the morning, and still dodge an evening event
    assert free_gaps([{"s": 450, "en": 480}], list(EVENING), widen=False) == [{"s": 1080, "en": 1320}]
    assert free_gaps([{"s": 1140, "en": 1200}], list(EVENING), widen=False)[0]["en"] == 1125


if __name__ == "__main__":
    selftest()
    day = dt.date.today()
    svc = service()
    evs = busy(svc, day)
    raw = todoist()
    skipped = [t for t in raw if COVERED.match(t["content"])]
    tasks = estimate([t for t in raw if t not in skipped], "--no-llm" not in sys.argv)
    t = dt.datetime.now()
    blocks = plan(tasks, evs, now=t.hour * 60 + t.minute)

    for e in sorted(evs, key=lambda e: e["s"]):
        print(f"  {hhmm(e['s'])}-{hhmm(e['en'])}  [busy] {e['what']}")
    print()
    for b in blocks:
        when = f"{hhmm(b['s'])}-{hhmm(b['s'] + b['mins'])}" if b["s"] is not None else "  NO ROOM  "
        print(f"  {when}  {b['name'][:70]}")

    for t in skipped:
        print(f"  {'  on calendar':>13}  {t['content'][:70]}")

    if "--push" not in sys.argv:
        print("\nRe-run with --push to write these blocks.")
        raise SystemExit
    removed, made = write(svc, blocks, day)
    print(f"\n{made} blocks written, {removed} stale blocks cleared for {day}")
