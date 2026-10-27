"""Measures how loud each mp3 is and writes loudness.json, which adhan.py levels playback with.

    python normalize_levels.py          # measure new/changed files
    python normalize_levels.py --all    # re-measure everything

Optional: adhan.py also measures any mp3 missing from loudness.json by itself, so a file
dropped straight into a folder on the player works too. Running this and committing
loudness.json just saves the player the (one-off) work. See loudness.py for the method.
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import loudness

BASE_DIR = Path(__file__).resolve().parent
LEVELS_PATH = BASE_DIR / 'loudness.json'
AUDIO_DIRS = ['mp3/fajr', 'mp3/non-fajr', 'mp3/bismillah', 'mp3/dua']


def main():
    try:
        known = {} if '--all' in sys.argv else json.loads(LEVELS_PATH.read_text())['files']
    except (OSError, ValueError, KeyError):
        known = {}

    files = sorted(p for d in AUDIO_DIRS for p in (BASE_DIR / d).glob('*.mp3')
                   if p.is_file() and not p.name.startswith('.'))
    key = lambda p: p.relative_to(BASE_DIR).as_posix()

    stale = [p for p in files if known.get(key(p), {}).get('size') != p.stat().st_size]
    print(f'Measuring {len(stale)} of {len(files)} files...')
    with ProcessPoolExecutor(max_workers=4) as pool:
        for path, lufs in zip(stale, pool.map(loudness.measure_lufs, stale)):
            known[key(path)] = {'lufs': round(lufs, 2), 'size': path.stat().st_size}

    entries = {key(p): {'lufs': known[key(p)]['lufs'], 'size': known[key(p)]['size']} for p in files}
    LEVELS_PATH.write_text(json.dumps({'files': entries}, indent=2) + '\n')

    for name, gain in loudness.compute_gains(entries).items():
        print(f'{name:48s} {entries[name]["lufs"]:6.1f} LUFS  gain {gain:.3f}')
    print(f'\nWrote {LEVELS_PATH.name}')


if __name__ == '__main__':
    main()
