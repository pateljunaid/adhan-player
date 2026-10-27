import functools
import json
import random
import subprocess
import sys
import threading
import time
import traceback
from datetime import date, datetime
from pathlib import Path

import requests
import schedule
from bs4 import BeautifulSoup

import dashboard
import loudness

DEBUG = False
RAMADAN = False

NAMES = ['Fajr', 'Dhuhr', 'Asr', 'Maghrib', 'Isha']
BASE_DIR = Path(__file__).resolve().parent
FAJR_DIR = BASE_DIR / 'mp3' / 'fajr'
NON_FAJR_DIR = BASE_DIR / 'mp3' / 'non-fajr'
DUA_PATH = BASE_DIR / 'mp3' / 'dua' / 'dua.mp3'
BISMILLAH_PATH = BASE_DIR / 'mp3' / 'bismillah' / 'bismillah.mp3'
SETTINGS_PATH = BASE_DIR / 'settings.json'
CACHE_PATH = BASE_DIR / 'prayer_times.json'
LEVELS_PATH = BASE_DIR / 'loudness.json'  # written by normalize_levels.py
DASHBOARD_PORT = 8080

PRAYER_TIMES_URL = 'https://www.islamicfinder.org/prayer-widget/5882873/hanfi/5/0/15.0/15.0'
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Referer': 'https://www.islamicfinder.org/',
}
PRAYER_ROW_CLASS = 'd-flex flex-direction-row flex-justify-sb pad-top-sm pad-left-sm pad-right-sm'
FETCH_ERRORS = (requests.RequestException, AttributeError, IndexError, ValueError)

WIFI_INTERFACE = 'en1'
WIFI_RESET_AFTER_FAILURES = 3  # consecutive connection failures before power-cycling Wi-Fi
RETRY_MINUTES = 5
MAX_CACHE_AGE_DAYS = 7  # how stale cached times can be and still beat playing nothing
NIGHTLY_REFRESH_AT = '00:30'
LATE_GRACE_MINUTES = 10  # skip an adhan this much later than scheduled (e.g. the Mac was asleep)
PLAY_TIMEOUT_SECONDS = 900
JOB_TAG = 'prayer'
RETRY_TAG = 'retry'

# Indexed by day of month (day 1 -> index 0).
FAJR_RAMADAN = [
    "05:33", "05:31", "05:30", "05:28", "05:26",
    "05:24", "05:23", "05:21", "06:20", "06:19",
    "06:17", "06:15", "06:14", "06:12", "06:10",
    "06:08", "06:06", "06:04", "06:02", "06:00",
    "05:59", "05:57", "05:55", "05:53", "05:51",
    "05:49", "05:47", "05:45", "05:43", "05:41",
]

MAGHRIB_RAMADAN = [
    "18:06", "18:07", "18:08", "18:10", "18:11",
    "18:12", "18:13", "18:15", "19:15", "19:16",
    "19:17", "19:18", "19:20", "19:21", "19:22",
    "19:23", "19:25", "19:26", "19:27", "19:28",
    "19:29", "19:31", "19:32", "19:33", "19:34",
    "19:36", "19:37", "19:38", "19:39", "19:40",
]


def survive_errors(result=None):
    """Keeps an unexpected error in a scheduled job from killing the whole script.

    `result` is what the job returns after an error; a one-shot job must return
    schedule.CancelJob or `schedule` would re-run the failed job every second.
    """
    def decorator(job):
        @functools.wraps(job)
        def wrapper(*args, **kwargs):
            try:
                return job(*args, **kwargs)
            except Exception:
                traceback.print_exc()
                return result
        return wrapper
    return decorator


class AdhanPool:
    """Plays every mp3 in a directory once, in random order, before any repeats."""

    def __init__(self, directory):
        self.directory = directory
        self.files = []
        self.queue = []
        self.last = None

    def refresh(self):
        self.files = sorted(f for f in self.directory.glob('*.mp3')
                            if f.is_file() and not f.name.startswith('.'))
        self.queue = [f for f in self.queue if f in self.files]
        if not self.files:
            print(f'WARNING: no mp3 files in {self.directory}')

    def next(self):
        if not self.files:
            return None
        if not self.queue:
            self.queue = list(self.files)
            random.shuffle(self.queue)
            # the end of the queue plays next: don't repeat the last pick of the previous cycle
            if len(self.queue) > 1 and self.queue[-1] == self.last:
                self.queue[0], self.queue[-1] = self.queue[-1], self.queue[0]
        self.last = self.queue.pop()
        return self.last


FAJR_POOL = AdhanPool(FAJR_DIR)
NON_FAJR_POOL = AdhanPool(NON_FAJR_DIR)

settings = dashboard.Settings(SETTINGS_PATH, NAMES)
today_prayers = []  # full [(name, 'HH:MM')] list for the dashboard, including ones already past
last_played = None
network_failures = 0


GAINS = {}  # per-file playback gains (<= 1.0) that even out loudness; see loudness.py
measured = {}  # loudness measured on this machine for mp3s missing from loudness.json
measuring = False


def level_key(path):
    return Path(path).relative_to(BASE_DIR).as_posix()


def load_levels():
    try:
        levels = json.loads(LEVELS_PATH.read_text())['files']
    except (OSError, ValueError, KeyError, TypeError):
        levels = {}
    levels.update(measured)
    return levels


def update_gains():
    global GAINS
    levels = {name: entry for name, entry in load_levels().items() if (BASE_DIR / name).is_file()}
    GAINS = loudness.compute_gains(levels)


def gain_for(path):
    name = level_key(path)
    if name in GAINS:
        return GAINS[name]
    if GAINS:
        # not measured yet (just added): err on the quiet side rather than blast it
        print(f'WARNING: {name} has not been measured yet; playing it quietly')
        return min(GAINS.values())
    return 1.0


def measure_files(paths):
    global measuring
    try:
        for path in paths:
            try:
                measured[level_key(path)] = {'lufs': loudness.measure_lufs(path), 'size': path.stat().st_size}
            except Exception as e:  # a thread can't be allowed to die quietly half-way through
                print(f'Could not measure {path.name}: {e!r}')
                continue
            update_gains()
            print(f'Measured loudness of {path.name}')
    finally:
        measuring = False


def measure_new_files():
    """Measures mp3s missing from loudness.json (e.g. adhans just dropped into a folder).

    Runs in the background; until a file is measured it plays quietly (see gain_for).
    """
    global measuring
    levels = load_levels()
    files = FAJR_POOL.files + NON_FAJR_POOL.files + [BISMILLAH_PATH, DUA_PATH]
    todo = [p for p in files if p.is_file() and levels.get(level_key(p), {}).get('size') != p.stat().st_size]
    if todo and not measuring:
        print(f'Measuring loudness of {len(todo)} new file(s) in the background')
        measuring = True
        threading.Thread(target=measure_files, args=(todo,), daemon=True).start()


def refresh_adhans():
    FAJR_POOL.refresh()
    NON_FAJR_POOL.refresh()
    update_gains()
    measure_new_files()


def fetch_prayer_times():
    page = requests.get(PRAYER_TIMES_URL, headers=REQUEST_HEADERS, timeout=10)
    page.raise_for_status()
    soup = BeautifulSoup(page.content, 'html.parser')
    rows = soup.find('div', id='calendar-slider').find_all('div', {'class': PRAYER_ROW_CLASS})

    times = []
    for row in rows:
        raw = ' '.join(row.find_all('p')[1].get_text().split())  # also folds non-breaking spaces
        times.append(datetime.strptime(raw, '%I:%M %p').strftime('%H:%M'))

    del times[1]  # remove sunrise
    if len(times) != len(NAMES):
        raise ValueError(f'expected {len(NAMES)} prayer times, got {len(times)}: {times}')
    return times


def apply_ramadan_override(times):
    day = datetime.now().day - 1
    if day < len(FAJR_RAMADAN):
        times[0] = FAJR_RAMADAN[day]
        times[3] = MAGHRIB_RAMADAN[day]


def save_cache(times):
    try:
        CACHE_PATH.write_text(json.dumps({'date': date.today().isoformat(), 'times': times}))
    except OSError as e:
        print(f'Could not save {CACHE_PATH.name}: {e!r}')


def load_cache():
    """Returns (times, age_in_days) from the last successful fetch, or None if unusable."""
    try:
        cached = json.loads(CACHE_PATH.read_text())
        times = cached['times']
        age = (date.today() - date.fromisoformat(cached['date'])).days
        if len(times) != len(NAMES) or age > MAX_CACHE_AGE_DAYS:
            return None
        for t in times:
            datetime.strptime(t, '%H:%M')
        return times, age
    except (OSError, ValueError, KeyError, TypeError):
        return None


def set_wifi(on):
    state = 'on' if on else 'off'
    try:
        subprocess.run(['networksetup', '-setairportpower', WIFI_INTERFACE, state], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f'Failed to turn Wi-Fi {state} (is WIFI_INTERFACE={WIFI_INTERFACE!r} right?): {e!r}')


def reset_wifi():
    print('Network looks down, power-cycling Wi-Fi')
    try:
        set_wifi(False)
        time.sleep(10)
    finally:
        set_wifi(True)  # never leave Wi-Fi off, even if interrupted


def handle_fetch_failure(error):
    global network_failures
    print(f'Error fetching prayer times: {error!r}')
    # Only a connection problem is worth bouncing Wi-Fi for; a changed page layout isn't.
    if isinstance(error, (requests.ConnectionError, requests.Timeout)):
        network_failures += 1
        if network_failures % WIFI_RESET_AFTER_FAILURES == 0:
            reset_wifi()
    else:
        network_failures = 0


def play(path, volume=None):
    command = ['afplay', str(path)]
    if volume is not None:
        command += ['-v', f'{volume:.3f}']
    try:
        subprocess.run(command, check=True, timeout=PLAY_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as e:
        print(f'Failed to play {path}: {e!r}')


def get_system_volume():
    try:
        result = subprocess.run(['osascript', '-e', 'output volume of (get volume settings)'],
                                stdout=subprocess.PIPE, universal_newlines=True, check=True)
        return int(result.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None  # e.g. the output device has no volume control


def set_system_volume(level):
    if level is None:
        return
    try:
        subprocess.run(['osascript', '-e', f'set volume output volume {level}'], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f'Failed to set volume: {e!r}')


def minutes_late(scheduled_time):
    now = datetime.now()
    scheduled = datetime.strptime(scheduled_time, '%H:%M')
    return (now.hour * 60 + now.minute - scheduled.hour * 60 - scheduled.minute) % 1440


@survive_errors(schedule.CancelJob)
def play_adhan(name, scheduled_time=None):
    global last_played
    if settings.is_muted(name):
        print(f'{name} is muted, skipping')
        return schedule.CancelJob

    if scheduled_time is not None and minutes_late(scheduled_time) > LATE_GRACE_MINUTES:
        print(f'Skipping {name}: scheduled for {scheduled_time} but it is {time.strftime("%H:%M")}')
        return schedule.CancelJob

    adhan = (FAJR_POOL if name == 'Fajr' else NON_FAJR_POOL).next()
    if adhan is None:
        print('No adhan mp3s found, skipping')
        return schedule.CancelJob

    print(f'playing {name} adhan: {adhan.name}')
    print(f'Time is {time.strftime("%I:%M %p")}\n')
    last_played = {'name': name, 'file': adhan.name, 'at': time.strftime('%H:%M')}
    set_system_volume(settings.volume)
    play(BISMILLAH_PATH, volume=gain_for(BISMILLAH_PATH))  # right away, so it marks the start even if the adhan's intro is silent
    play(adhan, volume=gain_for(adhan))
    play(DUA_PATH, volume=gain_for(DUA_PATH) * settings.dua_volume / 100)
    return schedule.CancelJob


def schedule_prayers(prayers):
    for name, prayer_time in prayers:
        schedule.every().day.at(prayer_time).do(
            play_adhan, name=name, scheduled_time=prayer_time).tag(JOB_TAG)
        print(f'Scheduled {name} to play at {prayer_time}')


def apply_prayers(prayers):
    """Makes `prayers` today's schedule, skipping any that have already passed."""
    global today_prayers
    today_prayers = prayers
    schedule.clear(JOB_TAG)  # drop jobs that never fired so they don't double up
    now = datetime.now().strftime('%H:%M')  # after the fetch, so a slow fetch can't make this stale
    schedule_prayers([(name, t) for name, t in prayers if t > now])  # "HH:MM" compares chronologically


def apply_cached_prayers():
    cached = load_cache()
    if cached is None:
        print('No usable cached prayer times; waiting for the website')
        return
    times, age = cached
    print(f'Using prayer times cached {age} day(s) ago')
    apply_prayers(list(zip(NAMES, times)))


def refresh_prayers(is_retry=False):
    """Fetches today's times and schedules the ones still ahead. Returns True on success.

    On failure it falls back to the last good times and retries in the background, so a
    flaky network never blocks the scheduler (and with it, the adhans).
    """
    global network_failures
    try:
        times = fetch_prayer_times()
    except FETCH_ERRORS as e:
        handle_fetch_failure(e)
        if not is_retry:
            apply_cached_prayers()
        if not any(RETRY_TAG in job.tags for job in schedule.jobs):
            print(f'Retrying every {RETRY_MINUTES} minutes')
            schedule.every(RETRY_MINUTES).minutes.do(retry_refresh).tag(RETRY_TAG)
        return False

    network_failures = 0
    if RAMADAN:
        apply_ramadan_override(times)
    save_cache(times)
    apply_prayers(list(zip(NAMES, times)))
    schedule.clear(RETRY_TAG)
    return True


@survive_errors()
def retry_refresh():
    if refresh_prayers(is_retry=True):
        return schedule.CancelJob


@survive_errors()
def nightly_refresh():
    refresh_adhans()
    refresh_prayers()


def get_status():
    now = datetime.now()
    upcoming = None
    for name, prayer_time in today_prayers:
        at = datetime.combine(now.date(), datetime.strptime(prayer_time, '%H:%M').time())
        if at > now:
            upcoming = {'name': name, 'time': prayer_time, 'seconds': int((at - now).total_seconds())}
            break
    return {
        'prayers': [{'name': name, 'time': t} for name, t in today_prayers],
        'next': upcoming,
        'last_played': last_played,
    }


def start_dashboard():
    # Never let the dashboard stop the adhans: a taken port just means no dashboard.
    try:
        dashboard.serve(settings, get_status, port=DASHBOARD_PORT,
                        on_change=lambda changed: set_system_volume(settings.volume) if 'volume' in changed else None)
    except OSError as e:
        print(f'Dashboard failed to start: {e!r}')
    else:
        print(f'Dashboard running on http://<this-mac>:{DASHBOARD_PORT}/')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(line_buffering=True)  # so logs show up when redirected to a file

    print(f'DEBUG: {DEBUG}')
    refresh_adhans()

    if DEBUG:
        play_adhan('Fajr' if time.strftime('%p') == 'AM' else 'Dhuhr')
        return

    if settings.volume is None:
        current = get_system_volume()
        if current is not None:
            settings.update({'volume': current})
    start_dashboard()

    refresh_prayers()

    # auto schedule all adhans after today every night
    schedule.every().day.at(NIGHTLY_REFRESH_AT).do(nightly_refresh)

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == '__main__':
    main()
