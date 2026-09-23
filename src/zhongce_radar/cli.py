"""Portable console entry points; no shell launcher is needed."""
import json
import os
import sys


def main(argv=None):
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    from . import radar
    values = list(sys.argv[1:] if argv is None else argv)
    if not values or values[0] not in {'codex', 'claude-code'}:
        values.insert(0, 'codex')
    try:
        return radar.main(values)
    except radar.RadarError as exc:
        print(json.dumps({'error': radar.safe(str(exc))}, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


def codex():
    return main(['codex', *sys.argv[1:]])


def claude():
    return main(['claude-code', *sys.argv[1:]])
