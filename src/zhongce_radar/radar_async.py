"""Concurrent, cancellable HTTP I/O. Each completed section is immediately usable."""
from __future__ import annotations

import asyncio
import copy
import json
import time

from . import radar
from . import radar_tui as ui
from .radar_http import AsyncAPI


class SnapshotAPI:
    """Reuse existing validated projections without making synchronous requests."""
    def __init__(self, data):
        self.data = data

    def get(self, name, **query):
        return self.data


def assemble(raw, args, pending, computed, timings):
    errors = {name: value['error'] for name, value in raw.items() if 'error' in value}
    data = {'harness': args.harness, 'benchmark': args.benchmark, 'observed_at': radar.now(),
            'partial_loading': bool(pending), 'loading_sections': sorted(pending),
            'iq': [], 'traffic': {}, 'identity': raw.get('identity', {}),
            'tasks': raw.get('tasks', {'loading': True}), 'personal': raw.get('personal', {'loading': True}),
            'rank': {}, 'other_rank': {}, 'hot': [], 'matrix': [], 'leaders': [],
            'errors': errors, 'timings_ms': dict(timings)}
    table = raw.get('table')
    if table and 'error' not in table:
        if 'table' not in computed:
            allowed = ui.supported_models(args.harness, table)
            rows = [r for r in radar.cells(table, args.harness) if r['model'] in allowed and r['effort'] in radar.EFFORTS
                    and (not args.model or r['model'] == args.model) and (not args.effort or r['effort'] == args.effort)]
            hot = sorted([r for r in rows if r['state'] == 'open'],
                         key=lambda r: (-(r['multiplier'] or 0), r['minutes'] if r['minutes'] is not None else float('inf'), r['cell']))[:30]
            computed['table'] = {'allowed_models': sorted(allowed), 'matrix': rows, 'hot': hot}
            computed['pipeline'] = {'benchmark': ui.pipeline(table), 'visible_models': ui.pipeline(table, allowed)}
        data.update(computed['table'])
        data['traffic'].update(computed['pipeline'])
        others = [b['id'] for b in table.get('benchmarks', []) if b['id'] != args.benchmark]
        if all('table:' + b in raw and 'error' not in raw['table:' + b] for b in others):
            totals = [computed['pipeline']['benchmark']] + [ui.pipeline(raw['table:' + b]) for b in others]
            data['traffic']['site'] = {k: sum(t[k] for t in totals) for k in ('running', 'waiting', 'grading')}
    if 'iq' in raw and 'iq' not in errors:
        feed = raw['iq']
        # IQ is independently published; it must not wait for the much larger table.
        source = table if table and 'error' not in table else {'combos': feed.get('points', [])}
        allowed = ui.supported_models(args.harness, source)
        data['allowed_models'] = sorted(allowed)
        try:
            data['iq'] = ui.iq_rows(source, feed, allowed)
            if not table or 'error' in table:
                for row in data['iq']:
                    row.update(coverage='—', running=None, sparse=False)
            data['source_updated_at'] = feed.get('source_updated_at')
        except ValueError as exc:
            errors['iq'] = str(exc)
    if 'live' in raw and 'live' not in errors:
        data['traffic'].update(ui.live_counts(raw['live']))
        data['riders'] = [radar.fields(r, 'nickname github_login riding_workers riding_harnesses runner_phase')
                          for r in raw['live'].get('riders', []) if r.get('riding_open') is True]
    if 'board' in raw and 'board' not in errors:
        board = raw['board']
        data['traffic'].update(contributors=len(board.get('contributors', [])), pending_grades=board.get('pending_grades'),
                               error_grades=board.get('error_grades'), pedal_speed=board.get('pedal_speed', {}))
        leaders = [r for r in board.get('contributors', []) if not r.get('is_radar_admin')]
        leaders.sort(key=lambda r: (-(r.get('month_points') or 0), -(r.get('month_graded') or 0), -(r.get('points') or 0)))
        data['leaders'] = [radar.fields(r, 'nickname github_login month_points points month_graded') for r in leaders[:30]]
        if 'identity' in raw and 'identity' not in errors:
            rank_args = copy.copy(args)
            for key, period in [('rank', 'month'), ('other_rank', 'all')]:
                rank_args.period = period
                try:
                    data[key] = radar.ranking(None, rank_args, raw['identity'], board)
                except radar.RadarError as exc:
                    errors[key] = str(exc)
    return data


async def load_dashboard(api, args, on_progress=None, timeout=20):
    raw, computed, timings, pending = {}, {}, {}, {}
    started = time.monotonic()
    def emit():
        if on_progress:
            on_progress(assemble(raw, args, set(pending.values()), computed, timings))
    async def personal():
        rows, cursor, seen = [], None, set()
        for page in range(50):
            value = await api.get('my-submissions', private=True, limit=20, period=args.period, cursor=cursor)
            if not isinstance(value.get('submissions'), list):
                raise radar.RadarError('个人提交 API 格式改变。')
            rows.extend(radar.fields(r, radar.SUB_FIELDS) for r in value['submissions']
                        if radar.record_matches(r, args))
            cursor = value.get('next_cursor')
            result = {'period': args.period, 'account_points': value.get('points'), 'items': list(rows[:200]),
                      'records_scope': getattr(args, 'records_scope', 'current'), 'harness': args.harness,
                      'benchmark': args.benchmark, 'order': 'submitted_at_desc', 'fetched_at': radar.now(),
                      'pages_read': page + 1, 'history_exhausted': not cursor,
                      'truncated': len(rows) > 200 or bool(cursor), 'loading_more': bool(cursor) and len(rows) < 200}
            raw['personal'] = result
            emit()
            if not cursor or len(rows) >= 200:
                return result
            if cursor in seen:
                raise radar.RadarError('服务器返回重复分页游标。')
            seen.add(cursor)
        return result
    async def identity():
        value = await api.get('whoami', private=True)
        return radar.identity(SnapshotAPI(value))
    async def tasks():
        value = await api.get('assignment', private=True, benchmark=args.benchmark, inventory='true')
        return radar.tasks(SnapshotAPI(value), args)
    def schedule(name, coroutine):
        pending[asyncio.create_task(coroutine)] = name
    schedule('iq', api.get('intelligence-efficiency', benchmark=args.benchmark))
    schedule('live', api.get('leaderboard', benchmark=args.benchmark, view='live'))
    schedule('identity', identity())
    schedule('tasks', tasks())
    schedule('personal', personal())
    schedule('table', api.get('table', benchmark=args.benchmark))
    schedule('board', api.get('leaderboard', benchmark=args.benchmark))
    try:
        while pending:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                break
            done, _ = await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                break
            for task in done:
                name = pending.pop(task)
                timings[name] = round((time.monotonic() - started) * 1000, 1)
                try:
                    raw[name] = task.result()
                except Exception as exc:
                    message = str(exc) if isinstance(exc, radar.RadarError) else '响应无法解析。'
                    # Preserve pages already received if a later page fails.
                    raw[name] = {**raw.get(name, {}), 'error': message, 'loading_more': False}
                if name == 'table' and 'error' not in raw[name]:
                    for benchmark in raw[name].get('benchmarks', []):
                        if benchmark['id'] != args.benchmark:
                            schedule('table:' + benchmark['id'], api.get('table', benchmark=benchmark['id']))
            emit()
        for name in pending.values():
            raw[name] = {**raw.get(name, {}), 'error': '本轮请求超时；按 r 重试。', 'loading_more': False}
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    return assemble(raw, args, set(), computed, timings)
