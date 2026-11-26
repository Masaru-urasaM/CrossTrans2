"""An isolated, fake-key profile using the production configuration contract."""
from copy import deepcopy
from collections import Counter
import json
from pathlib import Path
import threading
import time

from config import Config


FIXTURE_KEYS = [
    dict(id='trial-primary', provider='OpenAI', model_name='fixture-primary',
         api_key='fixture-key-primary', vision_capable=True, file_capable=True),
    dict(id='trial-backup', provider='OpenAI', model_name='fixture-backup',
         api_key='fixture-key-backup', vision_capable=True, file_capable=True),
]


class TrialConfig(Config):
    """Never reads a real profile, invokes DPAPI or persists credentials."""
    def __init__(self, directory):
        self.CONFIG_DIR = str(Path(directory).resolve())
        self.CONFIG_FILE = str(Path(self.CONFIG_DIR) / 'fixture-profile.json')
        if Path(self.CONFIG_DIR) == Path(Config.CONFIG_DIR).resolve():
            raise ValueError('The trial must use a separate profile directory')
        self._lock = threading.RLock()
        self._save_retry_counts = Counter()
        super().__init__()

    def load(self):
        with self._lock:
            self._config = deepcopy(self.DEFAULT_CONFIG)
            self._config['nlp_basic_mode'] = ['English']
            path = Path(self.CONFIG_FILE)
            if path.exists():
                data = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(data, dict) or data.pop('fixture_profile', None) != 1:
                    raise ValueError('This is not a trial fixture profile')
                self._config.update(data)
            self._config.update(api_keys=deepcopy(FIXTURE_KEYS), autostart=False,
                                shortcuts_enabled=False, auto_check_updates=False)
            self._config.setdefault('trial_fixture_mode', True)
            return self._config

    def save(self, secure=False):
        with self._lock:
            data = deepcopy(self._config)
            data.pop('api_keys', None)
            data['fixture_profile'] = 1
            path = Path(self.CONFIG_FILE)
            temporary = path.with_suffix('.pending')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            for attempt in range(4):
                try:
                    temporary.replace(path)
                    break
                except OSError as error:
                    code = getattr(error, 'winerror', None)
                    if code not in (5, 32, 33) or attempt == 3:
                        raise
                    self._save_retry_counts[code] += 1
                    time.sleep(.02 * 2 ** attempt)

    @property
    def save_retry_stats(self):
        with self._lock:
            return dict(self._save_retry_counts)

    def get_api_keys(self):
        return deepcopy(FIXTURE_KEYS)

    def set_api_keys(self, *_args, **_kwargs):
        raise ValueError('Live credentials are not accepted by the offline trial')

    def set(self, key, value):
        if key == 'api_keys':
            raise ValueError('Live credentials are not accepted by the offline trial')
        with self._lock:
            self._config[key] = value
            self.save()

    def get(self, key, default=None):
        with self._lock:
            value = self._config.get(key, default)
            return deepcopy(value) if isinstance(value, (dict, list)) else value
