"""User data stays outside wheels, uvx caches, and frozen executable bundles."""
import json
import os
from pathlib import Path
import sys


def data_home():
    return Path(os.environ.get('DRADAR_HOME', Path.home()/'.dradar')).expanduser().resolve()


def user_directory(kind):
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA' if kind == 'cache' else 'APPDATA', Path.home()/'AppData'/'Local'))
    elif sys.platform == 'darwin':
        base = Path.home()/'Library'/('Caches' if kind == 'cache' else 'Application Support')
    else:
        base = Path(os.environ.get('XDG_CACHE_HOME' if kind == 'cache' else 'XDG_CONFIG_HOME',
                                   Path.home()/('.cache' if kind == 'cache' else '.config')))
    return base/'dradar-dashboard'


def config_file():
    return Path(os.environ.get('DRADAR_DASHBOARD_CONFIG', user_directory('config')/'config.json')).expanduser().resolve()


def read_settings():
    path = config_file()
    if not path.exists():
        return {}
    from .radar import RadarError
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError):
        raise RadarError('客户端配置不是有效 JSON 对象。运行 dradar-dashboard config 查看配置位置。') from None
