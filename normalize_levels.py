"""Measures how loud each mp3 is and writes loudness.json for adhan.py to level playback with.

Run after adding or replacing any mp3 (needs ffmpeg; the Mac that plays the adhans doesn't):

    python normalize_levels.py          # measure new/changed files, recompute gains
    python normalize_levels.py --all    # re-measure everything

Every file gets a gain (at most 1.0) that brings it down to the loudness of the quietest
adhan, so no file is ever boosted (boosting could clip). The overall level is then set
with the dashboard's volume slider.
"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LEVELS_PATH = BASE_DIR / 'loudness.json'
ADHAN_DIRS = ['mp3/fajr', 'mp3/non-fajr']  # the target is the quietest file in these
OTHER_DIRS = ['mp3/bismillah', 'mp3/dua']  # leveled to the same target


def measure_lufs(path):
    """Integrated loudness of one file, via ffmpeg's loudnorm analysis pass."""
    result = subprocess.run(
        ['ffmpeg', '-hide_banner', '-nostats', '-i', str(path),
         '-af', 'loudnorm=print_format=json', '-f', 'null', '-'],
        stderr=subprocess.PIPE, universal_newlines=True, check=True)
    report = result.stderr[result.stderr.rindex('{'):result.stderr.rindex('}') + 1]
    return float(json.loads(report)['input_i'])


def list_files(directories):
    return sorted(p for d in directories for p in (BASE_DIR / d).glob('*.mp3')
                  if p.is_file() and not p.name.startswith('.'))


def main():
    remeasure_all = '--all' in sys.argv
    try:
        known = {} if remeasure_all else json.loads(LEVELS_PATH.read_text())['files']
    except (OSError, ValueError, KeyError):
        known = {}

    adhans = list_files(ADHAN_DIRS)
    files = adhans + list_files(OTHER_DIRS)
    key = lambda p: p.relative_to(BASE_DIR).as_posix()

    stale = [p for p in files
             if key(p) not in known or known[key(p)].get('size') != p.stat().st_size]
    print(f'Measuring {len(stale)} of {len(files)} files...')
    with ThreadPoolExecutor(max_workers=4) as pool:
        for path, lufs in zip(stale, pool.map(measure_lufs, stale)):
            known[key(path)] = {'lufs': lufs, 'size': path.stat().st_size}

    entries = {key(p): known[key(p)] for p in files}  # drops files that no longer exist
    target = min(entries[key(p)]['lufs'] for p in adhans)
    for entry in entries.values():
        entry['gain'] = round(min(1.0, 10 ** ((target - entry['lufs']) / 20)), 4)

    LEVELS_PATH.write_text(json.dumps({'target_lufs': target, 'files': entries}, indent=2) + '\n')
    for name, entry in entries.items():
        print(f'{name:48s} {entry["lufs"]:6.1f} LUFS  gain {entry["gain"]:.3f}')
    print(f'\nTarget {target:.1f} LUFS (the quietest adhan). Wrote {LEVELS_PATH.name}')


if __name__ == '__main__':
    main()
