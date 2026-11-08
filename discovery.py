"""Finds adhans to try: downloads a few from the Internet Archive and tracks the verdicts.

Candidates live in mp3/candidates/ until you Save one (it moves into the regular adhan
folders) or Skip it (it's deleted and never offered again). State is kept in a JSON file.
"""
import hashlib
import json
import os
import random
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import quote

import requests

# Internet Archive item ids to draw adhans from. Add an id here to widen the pool.
ADHAN_SOURCES = [
    'adhans_sunnah',
    'adhan_202303',
    'adhan_201605',
    'AdhaN_M_up-by-muslem',
    'MakkahFajrAdhan6913SheikhAliMullah',
]
METADATA_URL = 'https://archive.org/metadata/{item}'
DOWNLOAD_URL = 'https://archive.org/download/{item}/{name}'

STASH = 3  # candidates to keep downloaded and waiting
MAX_CANDIDATES = 12  # hard cap, however often "get more" is pressed
MIN_SECONDS, MAX_SECONDS = 90, 360  # an adhan, not a Quran recitation or a clip
MAX_BYTES = 15 * 1024 * 1024
INDEX_MAX_AGE_SECONDS = 24 * 3600
FAJR_WORDS = ('fajr', 'فجر')


def parse_length(value):
    """Seconds from the archive's 'length' field, which is '224.47' or '04:01' or '1:02:03'."""
    try:
        seconds = 0.0
        for part in str(value).split(':'):
            seconds = seconds * 60 + float(part)
        return seconds
    except ValueError:
        return None


def local_name(item, remote_name):
    """'<item>__<readable part>~<hash>.mp3'. The hash keeps different remote files apart even
    when their readable parts match (e.g. Arabic names, which keep only their ASCII bits)."""
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', Path(remote_name).stem).strip('._-')[:60]
    digest = hashlib.sha1(f'{item}/{remote_name}'.encode('utf-8')).hexdigest()[:8]
    return f'{item}__{safe}~{digest}.mp3'


def saved_name(name):
    """The file name a candidate gets when it joins the regular adhans."""
    safe, digest = Path(name).stem.partition('__')[2].rpartition('~')[0::2]
    return (safe or f'adhan-{digest}') + '.mp3'


def clean_title(title):
    """The archive's title for a file, if it's usable text (some are mojibake)."""
    if not isinstance(title, str):
        return None
    title = ' '.join(title.split())[:120]
    return title if title and not re.search(r'[\u2e80-\ud7ff]', title) else None


def label_for(name):
    """A readable title from a local file name."""
    stem = name.partition('__')[2].rpartition('~')[0] or Path(name).stem
    return re.sub(r'[_\s]+', ' ', stem).strip()


class Discovery:
    def __init__(self, directory, state_path, fajr_dir, other_dir, sources=None):
        self.directory = Path(directory)
        self.state_path = Path(state_path)
        self.fajr_dir = Path(fajr_dir)
        self.other_dir = Path(other_dir)
        self.sources = list(sources if sources is not None else ADHAN_SOURCES)
        self.lock = threading.RLock()
        self.seen = set()  # 'item/remote name' of everything already offered
        self.files = {}  # local name -> {'source', 'kind', 'prayer', 'played_at'}
        self.index = {}  # item -> (fetched_at, [(remote name, kind)])
        self.downloading = False
        self.extra = 0  # "get more" requests that arrived while a download was running
        self._load()

    # --- persistence -------------------------------------------------------------

    def _load(self):
        try:
            data = json.loads(self.state_path.read_text())
            self.seen = set(data.get('seen', []))
            self.files = dict(data.get('files', {}))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError, TypeError) as e:
            print(f'Ignoring unreadable {self.state_path.name}: {e!r}')
        self.files = {name: info for name, info in self.files.items()
                      if isinstance(info, dict) and (self.directory / name).is_file()}
        self._remove_strays()

    def _remove_strays(self):
        """Deletes leftovers of an interrupted download: .part files and unrecorded mp3s."""
        if not self.directory.is_dir():
            return
        for stray in self.directory.iterdir():
            if stray.is_file() and stray.name not in self.files:
                try:
                    stray.unlink()
                except OSError:
                    pass

    def _save(self):
        tmp = self.state_path.with_name(self.state_path.name + '.tmp')
        try:
            tmp.write_text(json.dumps({'seen': sorted(self.seen), 'files': self.files}))
            os.replace(tmp, self.state_path)
        except OSError as e:
            print(f'Could not save {self.state_path.name}: {e!r}')

    # --- what the dashboard and the player see --------------------------------------

    def candidates(self):
        with self.lock:
            return [{'file': name, 'label': info.get('title') or label_for(name), 'kind': info['kind'],
                     'prayer': info.get('prayer'), 'played_at': info.get('played_at')}
                    for name, info in sorted(self.files.items())]

    def is_downloading(self):
        return self.downloading

    def path_for(self, name):
        """The file of a downloaded candidate, or None (so no path is built from user input)."""
        with self.lock:
            return self.directory / name if name in self.files else None

    def pick(self, kind, ok=None):
        """A random not-yet-played candidate of this kind ('fajr' or 'regular'), or None.

        `ok(path)` can veto candidates (e.g. ones that couldn't be decoded).
        """
        with self.lock:
            options = [name for name, info in self.files.items()
                       if info['kind'] == kind and not info.get('played_at')
                       and (ok is None or ok(self.directory / name))]
            return self.directory / random.choice(options) if options else None

    def mark_played(self, path, prayer):
        with self.lock:
            info = self.files.get(Path(path).name)
            if info is not None:
                info['prayer'] = prayer
                info['played_at'] = time.strftime('%H:%M')
                self._save()

    # --- verdicts --------------------------------------------------------------------

    def _forget(self, name):
        info = self.files.pop(name)
        self.seen.add(info['source'])
        self._save()
        return info

    def save(self, name):
        """Moves a candidate into the regular adhan folders; returns (old path, new path)."""
        with self.lock:
            if name not in self.files:
                raise KeyError(name)
            source = self.directory / name
            target_dir = self.fajr_dir if self.files[name]['kind'] == 'fajr' else self.other_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            base = saved_name(name)
            target = target_dir / base
            n = 2
            while target.exists():
                target = target_dir / f'{Path(base).stem}-{n}.mp3'
                n += 1
            shutil.move(str(source), str(target))
            self._forget(name)
            return source, target

    def skip(self, name):
        with self.lock:
            if name not in self.files:
                raise KeyError(name)
            try:
                (self.directory / name).unlink()
            except FileNotFoundError:
                pass
            self._forget(name)

    # --- downloading -------------------------------------------------------------------

    def _items(self, item):
        cached = self.index.get(item)
        if cached and time.time() - cached[0] < INDEX_MAX_AGE_SECONDS:
            return cached[1]
        response = requests.get(METADATA_URL.format(item=item), timeout=20)
        response.raise_for_status()
        entries = []
        for f in response.json().get('files', []):
            name = f.get('name', '')
            length = parse_length(f.get('length'))
            try:
                size = int(f.get('size'))
            except (TypeError, ValueError):
                continue
            if (name.lower().endswith('.mp3') and size <= MAX_BYTES
                    and length is not None and MIN_SECONDS <= length <= MAX_SECONDS):
                kind = 'fajr' if any(w in name.lower() for w in FAJR_WORDS) else 'regular'
                entries.append((name, kind, clean_title(f.get('title')), size))
        self.index[item] = (time.time(), entries)
        return entries

    def _unseen(self):
        """(item, remote name, kind, title, size) for every candidate we haven't offered or downloaded."""
        found = []
        for item in self.sources:
            try:
                entries = self._items(item)
            except (requests.RequestException, ValueError) as e:
                print(f'Could not list {item}: {e!r}')
                continue
            with self.lock:
                taken = self.seen | {info['source'] for info in self.files.values()}
            found += [(item, name, kind, title, size) for name, kind, title, size in entries
                      if f'{item}/{name}' not in taken]
        return found

    def _download(self, item, remote_name, kind, title, size):
        name = local_name(item, remote_name)
        target = self.directory / name
        part = target.with_name(name + '.part')
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            with requests.get(DOWNLOAD_URL.format(item=item, name=quote(remote_name)),
                              stream=True, timeout=(10, 30)) as response:
                response.raise_for_status()
                written = 0
                with open(part, 'wb') as out:
                    for chunk in response.iter_content(64 * 1024):
                        written += len(chunk)
                        if written > MAX_BYTES:
                            raise ValueError('file is larger than expected')
                        out.write(chunk)
            if written != size:  # older urllib3 silently ends a dropped connection early
                raise ValueError(f'got {written} of {size} bytes')
            os.replace(part, target)
        finally:
            try:
                part.unlink()
            except FileNotFoundError:
                pass
        with self.lock:
            self.files[name] = {'source': f'{item}/{remote_name}', 'kind': kind,
                                'title': title, 'prayer': None, 'played_at': None}
            self._save()
        return target

    def _fill(self, want, on_download):
        with self.lock:
            want = min(want, MAX_CANDIDATES - len(self.files))
        if want <= 0:
            return
        pool = self._unseen()
        random.shuffle(pool)
        for item, remote_name, kind, title, size in pool:
            if want <= 0:
                break
            try:
                path = self._download(item, remote_name, kind, title, size)
            except (requests.RequestException, OSError, ValueError) as e:
                print(f'Could not download {item}/{remote_name}: {e!r}')
                continue
            print(f'Downloaded candidate adhan {path.name}')
            want -= 1
            if on_download:
                try:
                    on_download(path)
                except Exception as e:  # measuring is a nicety; keep downloading
                    print(f'Post-download step failed for {path.name}: {e!r}')
        if want > 0 and not pool:
            print('No new adhans left to offer from the configured sources')

    def refill(self, count=None, on_download=None, _claimed=False):
        """Downloads candidates: up to STASH waiting, or `count` more. Never raises.

        Meant for a background thread; a second call while one runs does nothing (use
        start_refill, which queues a "more" request for the running download instead).
        """
        with self.lock:
            if self.downloading and not _claimed:
                return
            self.downloading = True
        try:
            while True:
                with self.lock:
                    want = count if count is not None else STASH - len(self.files)
                try:
                    self._fill(want, on_download)
                except Exception as e:  # a thread must not die quietly
                    print(f'Discovery refill failed: {e!r}')
                with self.lock:
                    count, self.extra = self.extra or None, 0
                    if count is None:
                        self.downloading = False
                        return
        except BaseException:
            self.downloading = False
            raise

    def start_refill(self, count=None, on_download=None):
        with self.lock:
            if self.downloading:
                self.extra += count or 0  # the running download picks this up when it finishes
                return
            self.downloading = True
        threading.Thread(target=self.refill, kwargs={'count': count, 'on_download': on_download,
                                                     '_claimed': True}, daemon=True).start()
