# Visit Rhode Island → Proton Calendar

This repository builds an iCalendar (`visitri.ics`) feed from the official Visit Rhode Island events calendar:

https://www.visitrhodeisland.com/events/

A GitHub Action runs daily and commits the refreshed ICS file.

## Proton Calendar subscription URL

After pushing this repository to GitHub, subscribe to:

https://raw.githubusercontent.com/YOUR-GITHUB-USERNAME/visitri-calendar/main/visitri.ics

Replace `YOUR-GITHUB-USERNAME` with your GitHub username.

In Proton Calendar, add a calendar from URL and paste the raw GitHub URL.

## Run manually

Actions → Update Visit Rhode Island calendar → Run workflow

## Files

- `generate_calendar.py` — discovers Visit Rhode Island event pages and converts their dates/times/locations into ICS events.
- `.github/workflows/update-calendar.yml` — runs the generator every day.
- `visitri.ics` — generated automatically after the first successful workflow run.

## Note about times

If an event page lists a start time, this feed uses it and assigns a default two-hour duration when no end time is supplied. Events without a time are created as all-day events.
