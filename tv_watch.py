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
LD_RE = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S)


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


def fmt(a):
    s = a["start"]
    return f"{DAYS[s.weekday()]} {s.day}.{s.month}. {s.hour:02d}.{s.minute:02d} {a['channel']}: {a['title']}"


def build(airings, now):
    today = now.date()
    next_monday = today + timedelta(days=7 - today.weekday())
    next_sunday = next_monday + timedelta(days=6)
    items = sorted((a for a in airings.values() if a["start"].date() >= today),
                   key=lambda a: a["start"])
    todays = [a for a in items if a["start"].date() == today]
    week = [a for a in items if next_monday <= a["start"].date() <= next_sunday]
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "today_count": len(todays),
        "today_summary": "\n".join(fmt(a) for a in todays),
        "week_start": next_monday.isoformat(),
        "week_end": next_sunday.isoformat(),
        "week_count": len(week),
        "week_summary": "\n".join(fmt(a) for a in week),
        "airings": [{"start": a["start"].isoformat(timespec="minutes"),
                     "channel": a["channel"], "title": a["title"]} for a in items],
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
                    airings[(a["channel"], a["start"])] = a
        except Exception as exc:  # network error, timeout, 5xx
            status[slug] = f"error: {exc}"
        time.sleep(1)
    for slug, text in status.items():
        print(f"{slug}: {text}")
    if all(text.startswith("error") for text in status.values()):
        print("All fetches failed, keeping the previous data.", file=sys.stderr)
        sys.exit(1)
    result = build(airings, datetime.now(TZ))
    result["sources"] = status
    out = root / "data" / "matches.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}: today {result['today_count']}, next week {result['week_count']}")


if __name__ == "__main__":
    main()
