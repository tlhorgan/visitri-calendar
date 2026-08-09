from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from icalendar import Calendar, Event
from playwright.sync_api import sync_playwright

BASE = "https://www.visitrhodeisland.com"
CALENDAR_URL = f"{BASE}/events/"
OUTPUT = Path("visitri.ics")

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def discover_event_urls(page) -> list[str]:
    page.goto(CALENDAR_URL, wait_until="networkidle", timeout=90000)
    page.wait_for_timeout(2000)

    urls = set()
    stable_rounds = 0
    previous_count = 0

    # Scroll and click common "more" controls so Simpleview can reveal
    # additional event cards.
    for _ in range(50):
        hrefs = page.locator('a[href*="/event/"]').evaluate_all(
            "(els) => els.map(e => e.href)"
        )
        for href in hrefs:
            parsed = urlparse(href)
            if parsed.netloc.endswith("visitrhodeisland.com") and "/event/" in parsed.path:
                urls.add(href.split("?")[0].split("#")[0])

        clicked = False
        for label in ["Load More", "Show More", "More Events", "Next"]:
            loc = page.get_by_text(label, exact=False)
            if loc.count() and loc.first.is_visible():
                try:
                    loc.first.click(timeout=1500)
                    page.wait_for_timeout(1200)
                    clicked = True
                    break
                except Exception:
                    pass

        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(700)

        if len(urls) == previous_count and not clicked:
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous_count = len(urls)

        if stable_rounds >= 6:
            break

    return sorted(urls)

def find_after_label(text: str, label: str, stop_labels: list[str]) -> str:
    stop = "|".join(re.escape(x) for x in stop_labels)
    pat = rf"{re.escape(label)}:\s*(.*?)(?=(?:{stop}):|$)"
    m = re.search(pat, text, re.I | re.S)
    return clean(m.group(1)) if m else ""

def extract_dates(text: str) -> list:
    # Prefer the "Dates:" line in Additional Information.
    dates_text = find_after_label(
        text, "Dates", ["Location", "Address", "Phone", "Price", "Presented By"]
    )
    results = []
    if dates_text:
        for token in re.split(r",\s*", dates_text):
            token = clean(token)
            if not token:
                continue
            try:
                results.append(dtparser.parse(token, fuzzy=False).date())
            except Exception:
                pass

    # Fallback: collect numeric dates appearing near the event details.
    if not results:
        for token in re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text):
            try:
                d = dtparser.parse(token).date()
                if d not in results:
                    results.append(d)
            except Exception:
                pass

    return sorted(set(results))

def extract_date_times(text: str, dates: list) -> dict:
    """
    Capture lines such as:
      August 7 – 7 PM
      August 8 - 7:30 PM
    Returns {date: time}.
    """
    by_date = {}
    if not dates:
        return by_date

    years = sorted({d.year for d in dates})
    for year in years:
        pattern = (
            r"\b("
            r"January|February|March|April|May|June|July|August|September|October|November|December"
            r")\s+(\d{1,2})\s*[–—-]\s*"
            r"(\d{1,2}(?::\d{2})?\s*(?:AM|PM))\b"
        )
        for month, day, timestr in re.findall(pattern, text, re.I):
            try:
                d = dtparser.parse(f"{month} {day}, {year}").date()
                if d in dates:
                    t = dtparser.parse(timestr).time()
                    by_date[d] = t
            except Exception:
                pass
    return by_date

def extract_description(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return clean(meta["content"])
    meta = soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        return clean(meta["content"])
    return ""

def scrape_event(page, url: str):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(400)

    soup = BeautifulSoup(page.content(), "html.parser")
    text = clean(soup.get_text(" ", strip=True))

    h1 = soup.find("h1")
    title = clean(h1.get_text(" ", strip=True)) if h1 else ""
    if not title:
        return []

    dates = extract_dates(text)
    if not dates:
        return []

    location = find_after_label(
        text, "Location", ["Address", "Phone", "Price", "Presented By", "Dates"]
    )
    address = find_after_label(
        text, "Address", ["Phone", "Price", "Presented By", "Dates", "Location"]
    )
    place = ", ".join(x for x in [location, address] if x)
    description = extract_description(soup)
    date_times = extract_date_times(text, dates)

    events = []
    for d in dates:
        start_dt = None
        end_dt = None
        if d in date_times:
            start_dt = datetime.combine(d, date_times[d])
            # Event pages often give a start time but not an ending time.
            # Use a conservative 2-hour duration.
            end_dt = start_dt + timedelta(hours=2)

        events.append({
            "title": title,
            "date": d,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "location": place,
            "description": description,
            "url": url,
        })
    return events

def build_calendar(items):
    cal = Calendar()
    cal.add("prodid", "-//Visit Rhode Island Events Calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Visit Rhode Island Events")
    cal.add("x-wr-timezone", "America/New_York")

    seen = set()
    for item in items:
        key = (item["url"], item["date"])
        if key in seen:
            continue
        seen.add(key)

        ev = Event()
        uid_source = f"{item['url']}|{item['date'].isoformat()}"
        ev.add("uid", hashlib.sha256(uid_source.encode()).hexdigest()[:28] + "@visitri-calendar")
        ev.add("dtstamp", datetime.utcnow())
        ev.add("summary", item["title"])
        ev.add("url", item["url"])

        if item["location"]:
            ev.add("location", item["location"])

        desc = item["description"]
        if desc:
            desc += "\n\n"
        desc += f"Source: {item['url']}"
        ev.add("description", desc)

        if item["start_dt"]:
            ev.add("dtstart", item["start_dt"])
            ev.add("dtend", item["end_dt"])
        else:
            ev.add("dtstart", item["date"])
            ev.add("dtend", item["date"] + timedelta(days=1))

        cal.add_component(ev)

    OUTPUT.write_bytes(cal.to_ical())
    return len(seen)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 1200},
            user_agent="Mozilla/5.0 (compatible; VisitRICalendar/1.0)"
        )

        urls = discover_event_urls(page)
        print(f"Found {len(urls)} event URLs")

        all_events = []
        for i, url in enumerate(urls, 1):
            try:
                events = scrape_event(page, url)
                all_events.extend(events)
                if events:
                    print(f"[{i}/{len(urls)}] {events[0]['title']} ({len(events)} date(s))")
                else:
                    print(f"[{i}/{len(urls)}] SKIPPED {url}: no dates found")
            except Exception as exc:
                print(f"[{i}/{len(urls)}] ERROR {url}: {exc}")

        browser.close()

    all_events.sort(key=lambda x: (x["date"], x["title"]))
    count = build_calendar(all_events)
    print(f"Wrote {OUTPUT} with {count} calendar events")

if __name__ == "__main__":
    main()
