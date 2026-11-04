#!/usr/bin/env python3
"""tv-watch: read upcoming airings from tv.fi show pages, write data/matches.json."""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

BASE = "https://tv.fi/ohjelma/"
UA = "tv-watch/0.1 (personal home use)"
TZ = ZoneInfo("Europe/Helsinki")
DAYS = ["Ma", "Ti", "Ke", "To", "Pe", "La", "Su"]
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
LD_RE = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S)
EP_RE = re.compile(r"Kausi \d+, jakso \d+")


def fetch(slug):
    """Return page HTML, or None if the page does not exist (404)."""
    req = urllib.request.Request(BASE + slug, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def walk(obj):
    if isinstance(obj, dict):
        if obj.get("@type") == "BroadcastEvent":
            yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def parse(html):
    found = []
    for match in LD_RE.finditer(html):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        for event in walk(data):
            raw = event.get("startDate")
            if not raw:
                continue
            start = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(TZ)
            pub = event.get("publishedOn") or {}
            if isinstance(pub, list):
                pub = pub[0] if pub else {}
            found.append({
                "start": start,
                "channel": pub.get("name", "?"),
                "title": event.get("name", "?"),
            })
    return found


def match_slot(a, slots, tolerance):
    """Return the new-episode slot this airing falls in, or None (a repeat)."""
    s = a["start"]
    for slot in slots:
        if slot["slug"] != a["slug"] or WEEKDAYS[slot["weekday"]] != s.weekday():
            continue
        hour, minute = map(int, str(slot["time"]).split(":"))
        if abs(s.hour * 60 + s.minute - (hour * 60 + minute)) <= tolerance:
            return slot
    return None


def fmt(n):
    s = n["start"]
    return f"{DAYS[s.weekday()]} {s.day}.{s.month}. {s.hour:02d}.{s.minute:02d} {n['label']}: {n['episode']}"


def build(airings, cfg, now):
    slots = cfg.get("new_episodes", [])
    tolerance = cfg.get("slot_tolerance_minutes", 10)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())  # this week's Monday
    week_end = week_start + timedelta(days=6)  # this week's Sunday
    news = []
    for a in sorted(airings.values(), key=lambda x: x["start"]):
        if a["start"].date() < today:
            continue
        slot = match_slot(a, slots, tolerance)
        if slot:
            ep = EP_RE.search(a["title"])
            news.append({"start": a["start"], "label": slot["label"],
                         "episode": ep.group(0) if ep else a["title"],
                         "channel": a["channel"]})
    week = [n for n in news if week_start <= n["start"].date() <= week_end]
    tomorrows = [n for n in news if n["start"].date() == tomorrow]
    upcoming = next((n for n in news if n["start"] >= now), None)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "week_count": len(week),
        "week_summary": "\n".join(fmt(n) for n in week),
        "tomorrow_count": len(tomorrows),
        "tomorrow_summary": "\n".join(fmt(n) for n in tomorrows),
        "next_new_start": upcoming["start"].isoformat(timespec="seconds") if upcoming else None,
        "next_new_label": upcoming["label"] if upcoming else None,
        "next_new_episode": upcoming["episode"] if upcoming else None,
        "new_episodes": [{"start": n["start"].isoformat(timespec="minutes"),
                          "label": n["label"], "episode": n["episode"],
                          "channel": n["channel"]} for n in news],
    }


def main():
    root = Path(__file__).parent
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    airings, status = {}, {}
    for slug in cfg["shows"]:
        try:
            html = fetch(slug)
            if html is None:
                status[slug] = "not listed"
            else:
                found = parse(html)
                status[slug] = f"{len(found)} airings"
                for a in found:
                    a["slug"] = slug
                    airings[(slug, a["channel"], a["start"])] = a
        except Exception as exc:  # network error, timeout, 5xx
            status[slug] = f"error: {exc}"
        time.sleep(1)
    for slug, text in status.items():
        print(f"{slug}: {text}")
    if all(text.startswith("error") for text in status.values()):
        print("All fetches failed, keeping the previous data.", file=sys.stderr)
        sys.exit(1)
    result = build(airings, cfg, datetime.now(TZ))
    result["sources"] = status
    out = root / "data" / "matches.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}: new episodes this week {result['week_count']}, next {result['next_new_start']}")


if __name__ == "__main__":
    main()
