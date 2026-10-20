"""Tiny web dashboard for adhan.py (standard library only). No authentication: LAN use only."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PAGE_PATH = BASE_DIR / 'dashboard.html'
MAX_BODY_BYTES = 4096


def _percent(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError('volume must be an integer from 0 to 100')
    return value


class Settings:
    """User-adjustable settings, persisted to a JSON file and safe to share across threads."""

    def __init__(self, path, prayer_names):
        self.path = Path(path)
        self.prayer_names = list(prayer_names)
        self.lock = threading.Lock()
        self.volume = None  # system output volume, 0-100; None until known
        self.dua_volume = 30
        self.muted = set()
        self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            self.update(data, save=False)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print(f'Ignoring unreadable {self.path.name}: {e!r}')

    def _save(self):
        tmp = self.path.with_name(self.path.name + '.tmp')
        try:
            tmp.write_text(json.dumps(self._snapshot()))
            os.replace(tmp, self.path)
        except OSError as e:
            print(f'Could not save {self.path.name}: {e!r}')

    def _snapshot(self):
        return {
            'volume': self.volume,
            'dua_volume': self.dua_volume,
            'muted': {name: name in self.muted for name in self.prayer_names},
        }

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def is_muted(self, name):
        with self.lock:
            return name in self.muted

    def update(self, changes, save=True):
        """Validates and applies changes, returning the set of keys that changed."""
        if not isinstance(changes, dict):
            raise ValueError('expected a JSON object')

        parsed = {}
        for key, value in changes.items():
            if key in ('volume', 'dua_volume'):
                parsed[key] = _percent(value)
            elif key == 'muted':
                if not isinstance(value, dict):
                    raise ValueError('muted must be an object of prayer name to true/false')
                for name, flag in value.items():
                    if name not in self.prayer_names or not isinstance(flag, bool):
                        raise ValueError(f'invalid muted entry: {name!r}')
                parsed[key] = value
            else:
                raise ValueError(f'unknown setting: {key!r}')

        with self.lock:
            changed = set()
            for key in ('volume', 'dua_volume'):
                if key in parsed and parsed[key] != getattr(self, key):
                    setattr(self, key, parsed[key])
                    changed.add(key)
            for name, flag in parsed.get('muted', {}).items():
                if flag != (name in self.muted):
                    (self.muted.add if flag else self.muted.discard)(name)
                    changed.add('muted')
            if changed and save:
                self._save()
            return changed


def _make_handler(settings, get_status, on_change):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # the page polls constantly; keep the terminal readable

        def _send(self, status, body, content_type, headers=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status, payload):
            self._send(status, json.dumps(payload).encode('utf-8'), 'application/json')

        def _send_state(self):
            self._send_json(200, dict(get_status(), settings=settings.snapshot()))

        def do_GET(self):
            path = self.path.partition('?')[0]
            if path == '/':
                try:
                    page = PAGE_PATH.read_bytes()
                except OSError:
                    return self._send(500, b'dashboard.html is missing', 'text/plain')
                self._send(200, page, 'text/html; charset=utf-8')
            elif path == '/api/state':
                self._send_state()
            else:
                self._send(404, b'Not found', 'text/plain')

        def do_POST(self):
            if self.path.partition('?')[0] != '/api/settings':
                return self._send(404, b'Not found', 'text/plain')
            # A web page on another site can't send a JSON content type cross-origin without
            # a preflight (which we don't answer), so requiring it stops drive-by changes.
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                return self._send_json(415, {'error': 'expected application/json'})
            try:
                length = int(self.headers.get('Content-Length', ''))
            except ValueError:
                return self._send_json(411, {'error': 'Content-Length required'})
            if not 0 < length <= MAX_BODY_BYTES:
                return self._send_json(413, {'error': 'bad request size'})
            try:
                changed = settings.update(json.loads(self.rfile.read(length)))
            except ValueError as e:  # includes json.JSONDecodeError
                return self._send_json(400, {'error': str(e)})
            if changed and on_change:
                on_change(changed)
            self._send_state()

    return Handler


def serve(settings, get_status, port=8080, host='0.0.0.0', on_change=None):
    """Starts the dashboard in a daemon thread. Raises OSError if the port can't be bound."""
    server = ThreadingHTTPServer((host, port), _make_handler(settings, get_status, on_change))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
