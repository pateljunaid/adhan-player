# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running

Single-file, long-running script; there is no build, lint, or test setup.

```
pip install schedule requests beautifulsoup4
python adhan.py
```

macOS-only (`afplay` for playback, `networksetup` for Wi-Fi toggling). Paths are resolved relative to `adhan.py`, so the working directory doesn't matter.

Set `DEBUG=True` at the top of `adhan.py` to play one adhan immediately and exit, without scraping or scheduling. Set `RAMADAN=True` to override Fajr and Maghrib with the hardcoded `FAJR_RAMADAN` / `MAGHRIB_RAMADAN` tables, which are indexed by day of month and must be updated each year. Both are currently `False`.

To smoke-test without making sound, import the module and monkeypatch `adhan.subprocess.run` (it covers `afplay`, `osascript` and `networksetup`). `main()` is behind a `__main__` guard.

The dashboard is served on port 8080 (`DASHBOARD_PORT`) from the same process, with no authentication, so it is meant for the LAN only. If the port can't be bound, the script logs it and keeps playing adhans.

## Architecture

`adhan.py` holds the scheduler. `dashboard.py` (stdlib `http.server`, run in a daemon thread) and `dashboard.html` (one static page that polls `/api/state` and POSTs to `/api/settings`) provide the web UI.

- **Startup (`main`):** `refresh_adhans()` rescans the two mp3 pools. `refresh_prayers()` scrapes the islamicfinder.org prayer widget (`fetch_prayer_times`: the `#calendar-slider` div, Sunrise dropped) and `apply_prayers()` schedules only the prayers still ahead today, so a mid-day restart doesn't replay earlier ones.
- **Nightly refresh:** `schedule` runs `nightly_refresh` at 00:30. It rescans the mp3 folders and calls `refresh_prayers()`; `apply_prayers()` clears leftover jobs tagged `JOB_TAG` before rescheduling.
- **Playback:** each job calls `play_adhan(name)`; Fajr uses the Fajr pool, everything else the non-Fajr pool. It skips muted prayers and ones more than `LATE_GRACE_MINUTES` past their scheduled time (e.g. the Mac slept through them), sets the Mac's output volume from `settings.volume` via `osascript`, then plays `mp3/bismillah/bismillah.mp3` (no gap), the adhan, and `mp3/dua/dua.mp3` at `settings.dua_volume`. The bismillah marks the start even when the adhan's own intro is silent. `AdhanPool` draws randomly without replacement, so every file plays once before any repeats. The job returns `schedule.CancelJob`, so each fires once and the nightly job schedules the next day.
- **Dashboard settings:** `dashboard.Settings` holds the volume, dua volume and per-prayer mutes. It is thread-safe and persisted to `settings.json` (gitignored). At first startup the current Mac volume is read and saved as the baseline. `today_prayers` and `last_played` in `adhan.py` are read by `get_status()` for the page, and the web thread never touches the `schedule` jobs. POSTs must be `application/json`, which is what stops other web pages from changing the settings.
- **Scrape failure handling:** `refresh_prayers()` never blocks. On failure it schedules the last good times from `prayer_times.json` (gitignored, at most `MAX_CACHE_AGE_DAYS` old) and adds a `RETRY_TAG` job that retries every `RETRY_MINUTES` until it succeeds. Only repeated connection errors (`WIFI_RESET_AFTER_FAILURES`) power-cycle Wi-Fi on `WIFI_INTERFACE` (hardcoded `en1`), and it is always turned back on. Parse errors just retry. The scrape depends on the widget's exact CSS class string (`PRAYER_ROW_CLASS`) and a hardcoded city ID in `PRAYER_TIMES_URL`.
- **Error isolation:** scheduled jobs are wrapped in `@survive_errors`, because an exception escaping `schedule.run_pending()` would kill the script. A one-shot job must return `schedule.CancelJob` on error, or `schedule` re-runs it every second.

`mp3/bismillah/` is gitignored because its clip has no stated license; keep it out of commits unless the license is confirmed.

New adhans are added by dropping `.mp3` files into `mp3/fajr/` or `mp3/non-fajr/`. They are picked up at the next nightly refresh (or restart), with no code change.
