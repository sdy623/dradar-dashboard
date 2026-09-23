"""Lynx-style read-only radar dashboard with independent async data sections."""
from __future__ import annotations

import copy
import contextlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import threading
import time
import unicodedata

from . import radar
from . import paths


def supported_models(harness, table, root=radar.ROOT):
    advertised = {c['model'] for c in table.get('combos', [])
                  if radar.harness_for(c) == harness and c.get('effort') in radar.EFFORTS}
    configured = paths.read_settings().get('models', {}).get(harness)
    if configured is not None:
        if not isinstance(configured, list) or not all(isinstance(s, str) for s in configured):
            raise radar.RadarError('models 配置必须是模型名称数组。')
        return advertised & set(configured)
    if harness == 'codex':
        try:
            source = (root / 'tenbin-route/relay.mjs').read_text(encoding='utf-8')
        except OSError:
            return advertised
        match = re.search(r'export\s+const\s+MODELS\s*=\s*\[([^\]]*)\]', source)
        return advertised & set(re.findall(r"['\"]([^'\"]+)['\"]", match[1])) if match else set()
    return advertised


def iq_rows(table, feed, allowed):
    if feed.get('mode') != 'equal_latest_3':
        raise ValueError('IQ 接口统计口径已改变，暂停展示分数。')
    published = {(p['model'], p['effort']): p for p in feed.get('points', [])}
    result = []
    for combo in table.get('combos', []):
        model, effort = combo['model'], combo['effort']
        if model not in allowed or effort not in radar.EFFORTS:
            continue
        point = published.get((model, effort), {})
        covered, running = 0, 0
        for task in table.get('tasks', []):
            cell = table.get('cells', {}).get(f"{task['id']}|{model}|{effort}", {})
            covered += int((radar.number(cell.get('n')) or 0) > 0)
            running += sum(bool(h.get('running')) for h in cell.get('holders', []))
        result.append({'model': model, 'effort': effort, 'iq': radar.number(point.get('iq')),
                       'samples': radar.number(point.get('total')), 'coverage': f"{covered}/{len(table.get('tasks', []))}",
                       'sparse': bool(table.get('tasks')) and covered / len(table['tasks']) < .6,
                       'minutes': radar.number(point.get('average_minutes')),
                       'price': radar.number(point.get('average_price_usd')),
                       'price_basis': point.get('price_aggregation'),
                       'runs_24h': radar.number(point.get('runs_24h')), 'running': running,
                       'source_updated_at': point.get('source_updated_at')})
    result.sort(key=lambda row: (-(row['iq'] if row['iq'] is not None else -1), row['model'], row['effort']))
    return result


def pipeline(table, allowed=None):
    counts = {'running': 0, 'waiting': 0, 'grading': 0}
    keys = {f"{task['id']}|{c['model']}|{c['effort']}"
            for task in table.get('tasks', []) for c in table.get('combos', [])
            if allowed is None or c['model'] in allowed and c['effort'] in radar.EFFORTS}
    for key in keys:
        cell = table.get('cells', {}).get(key, {})
        holders = cell.get('holders', [])
        running = sum(bool(h.get('running')) for h in holders)
        counts['running'] += running
        counts['waiting'] += len(holders) - running
        counts['grading'] += max(0, int(radar.number(cell.get('q')) or 0))
    return counts


def live_counts(live):
    riders = [r for r in live.get('riders', []) if r.get('riding_open') is True and r.get('riding_since')]
    values = [radar.number(r.get('riding_workers')) for r in riders]
    return {'online': radar.number(live.get('online_volunteers')),
            'riders': len(riders) if isinstance(live.get('riders'), list) else None,
            'workers': sum(values) if isinstance(live.get('riders'), list) and all(v is not None for v in values) else None}


CACHE_DIR = paths.user_directory('cache')


def cache_path(args, directory):
    key = re.sub(r'[^A-Za-z0-9_-]', '_', args.harness + '-' + args.benchmark)
    return directory / (key + '.json')


def save_iq_cache(data, directory=CACHE_DIR):
    if not data.get('iq') or data.get('cached'):
        return
    # Public IQ only: never persist identity, auth, results, leases, or live counters.
    payload = radar.fields(data, 'harness benchmark observed_at source_updated_at iq')
    try:
        directory.mkdir(parents=True, exist_ok=True)
        args = type('Scope', (), data)
        path = cache_path(args, directory)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(radar.safe(payload), ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
    except OSError:
        pass


def load_iq_cache(args, directory=CACHE_DIR):
    empty = {'harness': args.harness, 'benchmark': args.benchmark, 'partial_loading': True,
             'loading_sections': ['iq', 'table', 'identity', 'personal', 'tasks'],
             'personal': {'loading': True}, 'tasks': {'loading': True}}
    try:
        data = json.loads(cache_path(args, directory).read_text(encoding='utf-8'))
        if data.get('harness') != args.harness or data.get('benchmark') != args.benchmark:
            return empty
        allowed = supported_models(args.harness, {'combos': data.get('iq', [])})
        rows = [r for r in data.get('iq', []) if r['model'] in allowed and r['effort'] in radar.EFFORTS]
        for row in rows:
            row['running'] = None
        return {**empty, 'iq': rows, 'cached': True, 'cache_time': data.get('observed_at'),
                'source_updated_at': data.get('source_updated_at')}
    except (OSError, ValueError, KeyError, TypeError):
        return empty


def dashboard(api, args, on_progress=None, stop=None):
    from concurrent.futures import TimeoutError as FutureTimeout
    from .radar_http import AsyncAPI, portal
    from .radar_async import load_dashboard
    future = portal().submit(lambda session: load_dashboard(AsyncAPI(api, session), args, on_progress))
    while True:
        try:
            return future.result(timeout=.05)
        except FutureTimeout:
            if stop is not None and stop.is_set():
                future.cancel()
                return None


def display_width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def clip(text, width):
    out, used = [], 0
    for char in text:
        size = display_width(char)
        if used + size > max(0, width):
            break
        out.append(char)
        used += size
    return ''.join(out)


def pad(text, width):
    text = clip(str(text), width)
    return text + ' ' * max(0, width - display_width(text))


def fmt(value, places=0):
    return '—' if radar.number(value) is None else f'{value:,.{places}f}'


def document(data, width):
    """The same document feeds the interactive screen and plain-text export."""
    lines, sections = [], {}
    width = max(10, width)
    def add(text='', style='normal'):
        lines.append((style, clip(str(radar.safe(text)).replace('\n', ' ').replace('\r', ' '), width)))
    def section(key, title, subtitle):
        if lines:
            add()
        sections[key] = len(lines)
        add(f'[{key}] {title}', 'heading')
        add(subtitle, 'dim')
        add('─' * width, 'dim')
    section('1', '模型 IQ · 优先浏览', '网站原始 IQ / 150 · 每格最近 3 次 · 显示配置允许的模型和非 ultra 档位')
    if data.get('cached'):
        add('IQ 缓存：' + str(data.get('cache_time') or '时间未知') + '；正在后台刷新，非实时值。', 'warning')
    add('IQ     模型 / 档位                         样本   覆盖       分钟    API中位价   在跑 / 24h', 'label')
    for row in data.get('iq', []):
        label = row['model'] + ' / ' + row['effort']
        add(f"{pad(fmt(row.get('iq'), 1), 7)}{pad(label, 37)}{pad(fmt(row.get('samples')), 7)}{pad(row.get('coverage', '—'), 11)}"
            f"{pad(fmt(row.get('minutes'), 1), 8)}{pad('$'+fmt(row.get('price'), 2), 12)}{fmt(row.get('running'))} / {fmt(row.get('runs_24h'))}",
            'good' if row.get('iq') is not None and row['iq'] >= 100 else 'normal')
        if width < 95:
            add(f"       {label} | {fmt(row.get('minutes'), 1)}分钟  ${fmt(row.get('price'), 2)}  在跑{fmt(row.get('running'))}  24h {fmt(row.get('runs_24h'))}", 'dim')
        if row.get('sparse'):
            add('       覆盖不足 60%：样本较少，比较时留意覆盖范围。', 'warning')
    if not data.get('iq'):
        add('IQ 正在异步加载；可先浏览其他板块。' if 'iq' in data.get('loading_sections', []) else
            'IQ 数据暂不可用；未用个人通过率或零分替代。', 'dim')
    add('IQ 数据时间：' + str(data.get('source_updated_at') or '未知'), 'dim')

    section('2', '实时站点 · 现在多少人在用', '在线人数、正在参与的人数和并发任务使用不同统计口径')
    traffic = data.get('traffic', {})
    add(f"站点在线 {fmt(traffic.get('online'))} 人   正在参与 {fmt(traffic.get('riders'))} 人   活跃 worker {fmt(traffic.get('workers'))}", 'good')
    for name, label in [('site', '全站全部题库'), ('benchmark', '当前题库全部模型'), ('visible_models', '当前显示模型')]:
        counts = traffic.get(name, {})
        add(f"{label}：并发运行 {fmt(counts.get('running'))} 道  等待启动 {fmt(counts.get('waiting'))} 道  判分队列 {fmt(counts.get('grading'))} 道")
    speed = traffic.get('pedal_speed') or {}
    add(f"全站贡献者 {fmt(traffic.get('contributors'))} 人   待判分 {fmt(traffic.get('pending_grades'))}   判分异常 {fmt(traffic.get('error_grades'))}")
    add(f"总蹬速 ${fmt(speed.get('api_equivalent_usd_per_hour'), 2)}/小时（API等价）", 'dim')
    for rider in data.get('riders', []):
        add(f"  {pad(rider.get('github_login') or rider.get('nickname') or '匿名', 24)} {fmt(rider.get('riding_workers'))} worker  {', '.join(rider.get('riding_harnesses') or [])}")

    section('3', '我的跑题 · 个人数据', '保留本人真实历史；旧模型和曾跑过的 ultra 不会从历史中抹掉')
    me = data.get('identity', {})
    standing = data.get('rank', {}).get('standing', {})
    add(f"{me.get('nickname', '身份未知')}  {me.get('github_login') or ''}", 'good')
    add(f"月榜 #{fmt(data.get('rank', {}).get('rank'))}  总榜 #{fmt(data.get('other_rank', {}).get('rank'))}  月积分 {fmt(standing.get('month_points'), 1)}  总积分 {fmt(standing.get('points'), 1)}")
    add(f"账号并发上限 {fmt(me.get('concurrent_limit'))}  当前题库持有 {fmt(data.get('tasks', {}).get('count'))} 道")
    add('各 harness 积分：' + '  '.join(f'{k} {fmt(v, 1)}' for k, v in (standing.get('points_by_harness') or {}).items()))
    personal = data.get('personal', {})
    rows = personal.get('items', [])
    statuses = {}
    for row in rows:
        s = row.get('grade_status', 'unknown')
        statuses[s] = statuses.get(s, 0) + 1
    valid = [r for r in rows if r.get('grade_status') == 'graded' and not r.get('flagged')]
    passed = sum(r.get('passed') is True for r in valid)
    if personal.get('loading'):
        add('个人记录正在加载；其他板块可继续浏览。', 'dim')
    elif personal.get('error'):
        add('个人记录暂不可用，未将查询失败显示为 0 次。', 'warning')
    else:
        add(f"本页 {len(rows)} 次提交  有效判分 {len(valid)}  通过 {passed}  个人通过率 {fmt(100*passed/len(valid) if valid else None, 1)}%")
    if personal.get('loading_more'):
        add('已收到的记录可浏览，后续分页正在异步加载…', 'dim')
    add('状态分布：' + ' / '.join(f'{k} {v}' for k, v in statuses.items()))
    if personal.get('truncated'):
        add('仅显示已加载的最近 200 条；上方个人通过率不是完整历史统计。', 'warning')
    scope = radar.records_scope_label(personal.get('records_scope', 'current'), data.get('harness', '当前 CLI'))
    add(f"记录范围：{scope}；题库 {data.get('benchmark', '未知')}；{personal.get('period', '加载中')}", 'dim')
    add('最新提交在前；提交与判分时间均为本机时间（附 UTC 偏移）。', 'dim')
    if personal.get('fetched_at'):
        add('记录读取于：' + radar.local_record_time(personal['fetched_at']), 'dim')
    add('当前活跃任务', 'label')
    for row in data.get('tasks', {}).get('items', []):
        add(f"  {row.get('state')}  {row.get('task_id')}  {row.get('model')} / {row.get('effort')}")
    if data.get('tasks', {}).get('loading'):
        add('  租约加载中…', 'dim')
    elif not data.get('tasks', {}).get('items'):
        add('  无活跃租约' if 'error' not in data.get('tasks', {}) else '  租约查询失败', 'dim')
    add('我的提交与判分记录 · ' + scope, 'label')
    for row in rows:
        add(f" {pad(row.get('grade_status', '?'), 9)} {row.get('harness', '未知 harness')}  {row.get('task_id', '')}")
        add(f"   提交 {radar.local_record_time(row.get('submitted_at'))}  判分 {radar.local_record_time(row.get('graded_at'))}", 'dim')
        add(f"   {row.get('model', '')} / {row.get('effort', '')}  积分 +{fmt(row.get('earned_points'), 2)}  {fmt(row.get('duration_sec'), 0)}秒  {'通过' if row.get('passed') is True else '未通过' if row.get('passed') is False else '待定'}", 'dim')

    section('4', '高倍率 · 可认领候选', '只读浏览；终端按键不会认领或启动任务')
    for row in data.get('hot', []):
        add(f"×{fmt(row.get('multiplier'), 1)}  {row.get('task')}  {'荒地' if row.get('wasteland') else ''}", 'good')
        add(f"      {row.get('model')} / {row.get('effort')}  {fmt(row.get('minutes'))}分钟  API估价 ${fmt(row.get('api_equivalent_usd'), 2)}", 'dim')

    section('5', '题目大表 · 配置允许范围', '可用模型 × 档位 × 题目；/ 搜索，n 下一个，Home/End 首尾')
    for row in data.get('matrix', []):
        state = {'open': '可领取', 'running': '运行中', 'leased': '已认领', 'queued': '判分中', 'cooldown': '冷却中'}.get(row.get('state'), row.get('state') or '未知')
        add(f"{pad(state, 8)} ×{fmt(row.get('multiplier'), 1)}  {row.get('task')}")
        add(f"             {row.get('model')} / {row.get('effort')}  {fmt(row.get('minutes'))}分钟  ${fmt(row.get('api_equivalent_usd'), 2)}", 'dim')

    section('6', '雷达天梯 · 月榜', '管理员不占排名；全站贡献者，不按本机模型过滤')
    for index, row in enumerate(data.get('leaders', []), 1):
        add(f"{index:>3}  {pad(row.get('github_login') or row.get('nickname') or '匿名', 30)} 月积分 {fmt(row.get('month_points'), 1)}  判分 {fmt(row.get('month_graded'))}")
    if data.get('errors'):
        add()
        add('部分数据不可用', 'warning')
        for name, error in data['errors'].items():
            add(f'{name}: {error}', 'warning')
    return lines, sections


class View:
    def __init__(self):
        self.offset = 0
        self.query = ''

    def handle(self, key, total, height):
        delta = {'up': -1, 'k': -1, 'down': 1, 'j': 1, 'wheel_up': -3, 'wheel_down': 3,
                 'pgup': -max(1, height-1), 'pgdn': max(1, height-1), ' ': max(1, height-1)}.get(key, 0)
        self.offset += delta
        if key in {'home', 'g'}:
            self.offset = 0
        if key in {'end', 'G'}:
            self.offset = total
        self.offset = max(0, min(self.offset, max(0, total-height)))

    def search(self, query_text, lines):
        self.query = query_text
        indices = list(range(self.offset+1, len(lines))) + list(range(0, min(self.offset+1, len(lines))))
        for index in indices:
            if query_text.casefold() in lines[index].casefold():
                self.offset = index
                return True
        return False


def decode_escape(sequence):
    known = {'\x1b[A': 'up', '\x1b[B': 'down', '\x1b[5~': 'pgup', '\x1b[6~': 'pgdn',
             '\x1b[H': 'home', '\x1b[F': 'end', '\x1b[1~': 'home', '\x1b[4~': 'end'}
    if sequence in known:
        return known[sequence]
    mouse = re.fullmatch(r'\x1b\[<(64|65);\d+;\d+[Mm]', sequence)
    return ('wheel_up' if mouse[1] == '64' else 'wheel_down') if mouse else None


def decode_utf8_input(values):
    import codecs
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    for value in values:
        decoded = decoder.decode(bytes([value]))
        if decoded:
            return decoded
    return decoder.decode(b'', final=True)


class Terminal:
    """Windows native console events, POSIX raw terminal otherwise."""
    def __enter__(self):
        self.windows = os.name == 'nt'
        if self.windows:
            import ctypes as c
            from ctypes import wintypes as w
            self.c = c
            self.kernel = c.WinDLL('kernel32', use_last_error=True)
            self.kernel.GetStdHandle.argtypes = [w.DWORD]
            self.kernel.GetStdHandle.restype = w.HANDLE
            self.kernel.GetConsoleMode.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
            self.kernel.SetConsoleMode.argtypes = [w.HANDLE, w.DWORD]
            self.hin = self.kernel.GetStdHandle(-10 & 0xffffffff)
            self.hout = self.kernel.GetStdHandle(-11 & 0xffffffff)
            self.in_mode, self.out_mode = w.DWORD(), w.DWORD()
            if not self.kernel.GetConsoleMode(self.hin, c.byref(self.in_mode)) or not self.kernel.GetConsoleMode(self.hout, c.byref(self.out_mode)):
                raise radar.RadarError('没有交互终端。请用 PowerShell/Windows Terminal，或运行 dashboard 查看文本。')
            # Disable quick-edit/line input, enable mouse and resize events.
            self.kernel.SetConsoleMode(self.hin, (self.in_mode.value | 0x98) & ~(0x47 | 0x200))
            self.kernel.SetConsoleMode(self.hout, self.out_mode.value | 0x4)
            class Coord(c.Structure):
                _fields_ = [('X', w.SHORT), ('Y', w.SHORT)]
            class Key(c.Structure):
                _fields_ = [('down', w.BOOL), ('repeat', w.WORD), ('virtual', w.WORD), ('scan', w.WORD), ('char', w.WCHAR), ('control', w.DWORD)]
            class Mouse(c.Structure):
                _fields_ = [('position', Coord), ('buttons', w.DWORD), ('control', w.DWORD), ('flags', w.DWORD)]
            class Event(c.Union):
                _fields_ = [('key', Key), ('mouse', Mouse), ('size', Coord), ('padding', c.c_byte * 16)]
            class Record(c.Structure):
                _fields_ = [('kind', w.WORD), ('event', Event)]
            self.Record = Record
            self.kernel.GetNumberOfConsoleInputEvents.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
            self.kernel.ReadConsoleInputW.argtypes = [w.HANDLE, c.POINTER(Record), w.DWORD, c.POINTER(w.DWORD)]
            self.count = w.DWORD()
        else:
            import termios
            import tty
            self.old = termios.tcgetattr(sys.stdin.fileno())
            tty.setraw(sys.stdin.fileno())
        sys.stdout.write('\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h')
        sys.stdout.flush()
        return self

    def read(self):
        if self.windows:
            self.kernel.GetNumberOfConsoleInputEvents(self.hin, self.c.byref(self.count))
            if not self.count.value:
                return None
            record = self.Record()
            self.kernel.ReadConsoleInputW(self.hin, self.c.byref(record), 1, self.c.byref(self.count))
            if record.kind == 4:
                return 'resize'
            if record.kind == 2 and record.event.mouse.flags & 4:
                delta = self.c.c_short(record.event.mouse.buttons >> 16).value
                return 'wheel_up' if delta > 0 else 'wheel_down'
            if record.kind != 1 or not record.event.key.down:
                return None
            event = record.event.key
            key = {38: 'up', 40: 'down', 33: 'pgup', 34: 'pgdn', 36: 'home', 35: 'end', 13: 'enter', 8: 'backspace', 27: 'escape', 9: 'tab'}.get(event.virtual)
            return key or (event.char if event.char != '\0' else None)
        import select
        if not select.select([sys.stdin], [], [], 0)[0]:
            return None
        def input_bytes():
            for index in range(4):
                if index and not select.select([sys.stdin], [], [], .05)[0]:
                    return
                value = os.read(sys.stdin.fileno(), 1)
                if not value:
                    return
                yield value[0]
        char = decode_utf8_input(input_bytes())
        if char == '\x1b':
            sequence = char
            while select.select([sys.stdin], [], [], .008)[0]:
                sequence += os.read(sys.stdin.fileno(), 1).decode('ascii')
            return decode_escape(sequence) or 'escape'
        return {'\r': 'enter', '\n': 'enter', '\x7f': 'backspace', '\t': 'tab'}.get(char, char)

    def __exit__(self, *exc):
        sys.stdout.write('\x1b[0m\x1b[?1006l\x1b[?1000l\x1b[?25h\x1b[?1049l')
        sys.stdout.flush()
        if self.windows:
            self.kernel.SetConsoleMode(self.hin, self.in_mode.value)
            self.kernel.SetConsoleMode(self.hout, self.out_mode.value)
        else:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.old)


COLORS = {'heading': '\x1b[1;36m', 'good': '\x1b[32m', 'warning': '\x1b[33m', 'dim': '\x1b[90m', 'label': '\x1b[36m', 'normal': '\x1b[37m'}


def run(api, args):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise radar.RadarError('tui 需要交互终端；管道中请使用 dashboard 或 dashboard --json。')
    result_queue = queue.Queue()
    loading, next_refresh = False, 0
    snapshot, lines, sections = load_iq_cache(args), [], {}
    stop = threading.Event()
    view = View()
    searching, search_text, message = False, '', ''
    last_size, dirty, last_paint = None, True, 0
    def load():
        try:
            data = dashboard(api, copy.copy(args), on_progress=result_queue.put, stop=stop)
            if data is not None:
                save_iq_cache(data)
                result_queue.put(data)
        except Exception:
            result_queue.put({'errors': {'dashboard': '刷新失败；请稍后重试。'}, 'harness': args.harness, 'benchmark': args.benchmark})
    with contextlib.ExitStack() as cleanup, Terminal() as terminal:
        cleanup.callback(stop.set)
        while True:
            clock = time.monotonic()
            start_pending = False
            if not loading and clock >= next_refresh:
                loading = True
                dirty = True
                start_pending = True
            try:
                fresh = result_queue.get_nowait()
                if not fresh.get('iq') and 'iq' in fresh.get('loading_sections', []) and snapshot.get('iq'):
                    fresh.update(iq=snapshot['iq'], cached=True, cache_time=snapshot.get('cache_time') or snapshot.get('observed_at'),
                                 source_updated_at=snapshot.get('source_updated_at'))
                snapshot = fresh
                loading = bool(fresh.get('partial_loading'))
                if not loading:
                    next_refresh = clock + args.interval
                last_size = None
                dirty = True
            except queue.Empty:
                pass
            size = shutil.get_terminal_size((110, 30))
            width, height = max(10, size.columns - 1), max(4, size.lines - 5)
            if last_size != size:
                lines, sections = document(snapshot, width) if snapshot else ([('heading', '正在读取模型 IQ、站点实时数据和个人记录…')], {})
                view.handle('resize', len(lines), height)
                last_size, dirty = size, True
            key = terminal.read()
            if key:
                dirty = True
                if searching:
                    if key == 'enter':
                        message = '' if view.search(search_text, [text for _, text in lines]) else '没有找到：' + search_text
                        searching = False
                    elif key == 'escape':
                        searching = False
                    elif key == 'backspace':
                        search_text = search_text[:-1]
                    elif len(key) == 1 and key.isprintable():
                        search_text += key
                elif key in {'q', 'Q', '\x03'}:
                    break
                elif key == '/':
                    searching, search_text = True, ''
                elif key == 'n' and view.query:
                    view.search(view.query, [text for _, text in lines])
                elif key in sections:
                    view.offset = sections[key]
                elif key == 'tab':
                    positions = sorted(sections.values())
                    view.offset = next((p for p in positions if p > view.offset), 0)
                elif key in {'r', 'R'}:
                    next_refresh = 0
                else:
                    view.handle(key, len(lines), height)
            if dirty or clock - last_paint >= 1:
                traffic = snapshot.get('traffic', {})
                title = f" DRadar Dashboard · 众测雷达 | {args.harness} | {args.benchmark} | IQ 优先 "
                status = f" 在线 {fmt(traffic.get('online'))}人  全站并发 {fmt(traffic.get('site', {}).get('running'))}道  "
                status += '刷新中…' if loading else f"更新 {snapshot.get('observed_at', '未知')}  {max(0, int(next_refresh-clock))}秒后刷新"
                if snapshot.get('cached'):
                    status += '  IQ缓存'
                if snapshot.get('errors'):
                    status += f"  {len(snapshot['errors'])}项数据不可用"
                output = ['\x1b[H', '\x1b[1;30;46m' + pad(title, width) + '\x1b[0m\x1b[K\r\n',
                          '\x1b[36m' + clip(status, width) + '\x1b[0m\x1b[K\r\n',
                          '\x1b[90m' + clip(' 1 IQ  2 实时  3 我的跑题  4 高倍率  5 题目大表  6 月榜   Tab 跳转', width) + '\x1b[0m\x1b[K\r\n']
                for index in range(height):
                    item = lines[view.offset+index] if view.offset+index < len(lines) else ('normal', '')
                    output.append(COLORS[item[0]] + clip(item[1], width) + '\x1b[0m\x1b[K\r\n')
                footer = '/' + search_text if searching else (message or f" ↑↓/滚轮 浏览  PgUp/PgDn 翻页  / 搜索  n 下一个  r 刷新  q 退出   {view.offset+1}/{len(lines)}")
                output.append('\x1b[30;47m' + pad(footer, width) + '\x1b[0m\x1b[K')
                sys.stdout.write(''.join(output))
                sys.stdout.flush()
                dirty, last_paint = False, clock
            # The first paint happens before importing/starting the HTTP runtime.
            if start_pending:
                threading.Thread(target=load, daemon=True).start()
            time.sleep(.025)
    return 0
