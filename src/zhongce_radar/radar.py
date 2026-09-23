"""Two harness views over the official DRadar API. aiohttp transport."""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.parse

from . import paths
from . import __version__

ROOT = Path(os.environ.get('RADAR_WORKSPACE', os.getcwd())).resolve()
HOME = paths.data_home()
SERVER = 'https://api.codexradar.com'
EFFORTS = {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
SECRET_KEYS = re.compile(r'token|secret|password|credential|authorization|cookie|run_code|^decision$|environment|command_line|^argv$|stdout|stderr', re.I)
SECRETS: set[str] = set()


class RadarError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise RadarError(f'无法读取有效 JSON：{path.name}') from None


def safe(value):
    if isinstance(value, dict):
        return {k: safe(v) for k, v in value.items() if not SECRET_KEYS.search(k)}
    if isinstance(value, list):
        return [safe(v) for v in value]
    if isinstance(value, str):
        for secret in SECRETS:
            if secret:
                value = value.replace(secret, '[redacted]')
        value = re.sub(r'(?i)Bearer\s+\S+|\b(?:drp_|sk-)[A-Za-z0-9_-]+', '[redacted]', value)
        value = re.sub(r'(?i)("(?:[^"\n]*(?:token|secret|password|credential|authorization|cookie)[^"\n]*|decision)"\s*:\s*)"[^"\n]*"', r'\1"[redacted]"', value)
        # Do not let website-controlled labels inject terminal controls.
        return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', value)
    return value


def fields(value, names):
    return {k: value[k] for k in names.split() if k in value}


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def parallel(**jobs):
    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        result = {}
        for name, future in futures.items():
            try:
                result[name] = future.result()
            except RadarError as exc:
                result[name] = {'error': str(exc)}
        return result


class API:
    def __init__(self, home=None):
        self.home = HOME if home is None else home

    def token(self):
        if token := os.environ.get('RADAR_TOKEN'):
            SECRETS.add(token)
            return token
        cfg = read_json(self.home / 'config.json')
        if cfg.get('server', SERVER).rstrip('/') != SERVER:
            raise RadarError('雷达凭据绑定的服务器不是 api.codexradar.com。')
        token = cfg.get('token')
        if not isinstance(token, str) or not token:
            raise RadarError('缺少雷达账号 Token，请用官方 DRadar 登录。')
        SECRETS.add(token)
        return token

    def request(self, endpoint, *, private=False, token=None, payload=None):
        from .radar_http import sync_request
        return sync_request(self, endpoint, private=private, token=token, payload=payload)


    def get(self, name, private=False, **query):
        query = {k: v for k, v in query.items() if v is not None}
        endpoint = name + ('?' + urllib.parse.urlencode(query) if query else '')
        return self.request(endpoint, private=private)


def harness_for(combo):
    if combo.get('model', '').startswith('claude-'):
        return 'claude-code'
    agent = combo.get('agent')
    if agent:
        return agent
    return 'codex' if combo.get('model', '').startswith(('gpt-', 'deepseek-')) else None


def cells(table, harness):
    if not isinstance(table.get('cells'), dict) or not isinstance(table.get('combos'), list):
        raise RadarError('格子 API 缺少 cells/combos。')
    combos = {(c['model'], c['effort']): c for c in table['combos']}
    rows = []
    for key, value in table['cells'].items():
        parts = key.split('|')
        if len(parts) != 3 or not isinstance(value, dict):
            continue
        task, model, effort = parts
        combo = combos.get((model, effort))
        # Stale cells without a currently advertised combo are not candidates.
        if combo is None or harness_for(combo) != harness:
            continue
        rows.append({'cell': key, 'task': task, 'model': model, 'effort': effort,
                     'state': value.get('st'), 'multiplier': number(value.get('mult')),
                     'base_multiplier': number(value.get('base_mult')),
                     'wasteland': value.get('wasteland'),
                     'wasteland_multiplier': number(value.get('wasteland_multiplier')),
                     'minutes': number(value.get('min')), 'api_equivalent_usd': number(value.get('cost')),
                     'cost_basis': value.get('src'), 'samples': value.get('ns'),
                     'manual_only': combo.get('manual_only', False),
                     'billing_mode': combo.get('billing_mode', 'subscription'),
                     'provider': combo.get('provider', 'openai')})
    return rows


def hot(api, args, table=None):
    table = table if table is not None else api.get('table', benchmark=args.benchmark)
    rows = cells(table, args.harness)
    rows = [r for r in rows if r['state'] == 'open' and r['effort'] in EFFORTS
            and (args.provider == 'all' or r['provider'] == (args.provider or ('openai' if args.harness == 'codex' else 'anthropic-subscription')))
            and (not args.model or r['model'] == args.model)
            and (not args.effort or r['effort'] == args.effort)
            and r['effort'] not in args.exclude_effort
            and (args.min_multiplier is None or r['multiplier'] is not None and r['multiplier'] >= args.min_multiplier)]
    rows.sort(key=lambda r: (-(r['multiplier'] or 0), r['minutes'] if r['minutes'] is not None else float('inf'), r['cell']))
    return {'benchmark': args.benchmark, 'provider_filter': args.provider or ('openai' if args.harness == 'codex' else 'anthropic-subscription'),
            'available_non_ultra': len(rows), 'items': rows[:args.limit],
            'note': '倍率是当前快照；费用为 API 等价估计，不是订阅实际扣款。'}


def identity(api):
    return fields(api.get('whoami', private=True), 'nickname github_login avatar_seed volunteer_id ladder_rank claim_limit concurrent_limit accessible_benchmarks')


def find_me(rows, me):
    # Avatar seed is stable when the user hides their GitHub identity or renames.
    for key in ['avatar_seed', 'github_login', 'nickname']:
        value = me.get(key)
        if value:
            matches = [i for i, r in enumerate(rows) if r.get(key) == value]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise RadarError('榜单身份匹配不唯一，未猜测你的排名。')
    return None


def ranking(api, args, me=None, board=None):
    if me is None or board is None:
        data = parallel(identity=lambda: identity(api), leaderboard=lambda: api.get('leaderboard', benchmark=args.benchmark))
        for item in data.values():
            if 'error' in item:
                raise RadarError(item['error'])
        me, board = data['identity'], data['leaderboard']
    if not isinstance(board.get('contributors'), list):
        raise RadarError('榜单 API 缺少 contributors。')
    rows = [r for r in board['contributors'] if not r.get('is_radar_admin')]
    if args.period == 'month':
        if any('month_points' not in r for r in rows):
            raise RadarError('服务器没有返回完整月榜。')
        rows.sort(key=lambda r: (-(r.get('month_points') or 0), -(r.get('month_graded') or 0), -(r.get('points') or 0)))
    index = find_me(rows, me)
    me_row = rows[index] if index is not None else None
    return {'identity': me, 'period': args.period, 'month': board.get('month'), 'competitors': len(rows),
            'rank': index + 1 if index is not None else None,
            'standing': fields(me_row or {}, 'points month_points graded month_graded submissions month_submissions points_by_harness month_points_by_harness'),
            'note': '全账号贡献者榜（包含各 harness）；管理员不占名次。' if me_row else '当前榜单没有唯一匹配的账号记录。'}


def tasks(api, args):
    data = api.get('assignment', private=True, benchmark=args.benchmark, inventory='true')
    active = data.get('active')
    if active is None:
        active = [data['assignment']] if data.get('assignment') else []
    if not isinstance(active, list):
        raise RadarError('租约 API 格式改变。')
    names = 'assignment_id task_id model effort benchmark_id harness status expires_at started_at inactive_at reason'
    def belongs(row):
        return (row.get('harness') or harness_for(row)) == args.harness
    rows = []
    for item in active:
        if not belongs(item):
            continue
        row = fields(item, names)
        row['state'] = assignment_state(item)
        rows.append(row)
    counts = {}
    for row in rows:
        state = row['state'] or 'unknown'
        counts[state] = counts.get(state, 0) + 1
    return {'benchmark': args.benchmark, 'count': len(rows), 'states': counts,
            'items': rows, 'account_active_in_benchmark': len(active),
            'recent_inactive': [fields(r, names) for r in data.get('recent_inactive', []) if belongs(r)],
            'note': '仅统计当前活跃租约；已结束历史单独列在 recent_inactive。'}


def assignment_state(row):
    state = row.get('runner_state')
    if state in {'resumable', 'checkpoint_retired'}:
        return 'stale'
    if state in {'running', 'paused', 'stale', 'waiting'}:
        return state
    if 'heartbeat_running' in row:
        if row['heartbeat_running']:
            return 'running'
        if row.get('execution_state') == 'paused':
            return 'paused'
        return 'stale' if row.get('started_at') else 'waiting'
    return 'running' if row.get('started_at') else 'waiting'


SUB_FIELDS = ('submission_id task_id benchmark_id harness model effort submitted_at graded_at grade_status '
              'earned_points score passed duration_sec api_equivalent_cost_usd cost_complete flagged failure_layer failure_code')


def record_matches(row, args):
    return (row.get('benchmark_id') == args.benchmark and
            (getattr(args, 'records_scope', 'current') == 'all' or row.get('harness') == args.harness))


def local_record_time(value, tz=None):
    if not value:
        return '—'
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return '未知'  # Do not silently interpret an unspecified server timezone.
        return stamp.astimezone(tz).isoformat(sep=' ', timespec='seconds')
    except (ValueError, TypeError, AttributeError):
        return '未知'


def records_scope_label(scope, harness):
    return '全账号（全部 harness）' if scope == 'all' else f'仅 {harness}'


def submissions(api, args):
    result, cursor, seen = [], None, set()
    points = None
    complete = False
    for page in range(50):
        data = api.get('my-submissions', private=True, limit=20, period=args.period, cursor=cursor)
        points = data.get('points')
        if not isinstance(data.get('submissions'), list):
            raise RadarError('个人提交 API 格式改变。')
        for row in data['submissions']:
            if record_matches(row, args):
                result.append(fields(row, SUB_FIELDS))
        cursor = data.get('next_cursor')
        complete = not cursor
        if len(result) >= args.limit or not cursor:
            break
        if cursor in seen:
            raise RadarError('服务器返回重复分页游标。')
        seen.add(cursor)
    return {'period': args.period, 'account_points': points, 'items': result[:args.limit],
            'records_scope': getattr(args, 'records_scope', 'current'), 'harness': args.harness,
            'benchmark': args.benchmark, 'order': 'submitted_at_desc', 'fetched_at': now(),
            'pages_read': page + 1, 'history_exhausted': complete,
            'truncated': len(result) > args.limit or not complete,
            'note': '按最新提交排序；判分时间另列。范围由 records_scope 和 benchmark 指定；不是全部历史统计。'}


def local_status(home=None, harness=None):
    home = HOME if home is None else home
    result = {'home': str(home), 'plans': [], 'fleet': None, 'runtime': None,
              'note': '本地保存的状态；不等同于进程/容器仍存活。'}
    manifest = home / 'ota/current.json'
    if manifest.exists():
        result['runtime'] = fields(read_json(manifest), 'version release_id sequence')
    plan_harness = {}
    for path in sorted((home / 'run-plans').glob('plan-*.json')):
        state = read_json(path)
        plan = state.get('plan', {})
        if plan.get('plan_id'):
            plan_harness[plan['plan_id']] = plan.get('harness')
        if plan and (not harness or plan.get('harness') == harness):
            row = fields(plan, 'plan_id batch_id benchmark_id harness concurrency refill expires_at')
            row['assignments'] = [fields(a, 'assignment_id task_id model effort provider') for a in plan.get('assignments', [])]
            result['plans'].append(row)
    fleet_file = home / 'fleet/state.json'
    if fleet_file.exists():
        state = read_json(fleet_file)
        result['fleet'] = fields(state, 'status heartbeat_at started_at pid total_workers dradar_version')
        result['fleet']['batches'] = []
        for batch in state.get('batches', {}).values():
            h = batch.get('refill_harness') or plan_harness.get(batch.get('plan_id'))
            if not harness or h == harness or not h:
                row = fields(batch, 'batch_id plan_id status workers pid returncode updated_at refill refill_model refill_effort')
                row['harness'] = h or 'unknown'
                result['fleet']['batches'].append(row)
    pending = home / 'pending_uploads.json'
    if pending.exists():
        data = read_json(pending)
        result['pending_uploads_file_present'] = True
        result['pending_uploads_count'] = len(data) if isinstance(data, list) else None
        if not isinstance(data, list):
            result['pending_uploads_note'] = '账本格式改变，无法确定数量。'
    return result


def saved_plan(code, home=None):
    home = HOME if home is None else home
    digest = hashlib.sha256(b'dradar:local-run-code-v1:' + code.encode('utf-8')).hexdigest()
    for path in (home / 'run-plans').glob('plan-*.json'):
        state = read_json(path)
        if state.get('run_code_hash') == digest:
            return state
    return None


def validate_plan(state, harness, *, execution=False):
    if not isinstance(state, dict) or state.get('server', '').rstrip('/') != SERVER:
        raise RadarError('没有匹配的本地运行计划，或服务器不匹配。')
    plan = state.get('plan', {})
    if plan.get('schema_version') != 1 or plan.get('plan_version') != 1:
        raise RadarError('运行计划版本不受支持。')
    if plan.get('harness') != harness:
        raise RadarError('计划 harness 与当前 CLI 不同；请使用对应入口。')
    if execution:
        try:
            expires = datetime.fromisoformat(plan['expires_at'].replace('Z', '+00:00'))
            if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise RadarError('运行计划已到期或缺少有效期；请从网站重新获取。') from None
        assignments = plan.get('assignments')
        if not assignments or not all(isinstance(a, dict) and a.get('effort') in EFFORTS for a in assignments):
            raise RadarError('计划包含 ultra、未知档位或空任务；没有启动。')
        if any(not a.get('model') or not a.get('assignment_id') for a in assignments):
            raise RadarError('计划任务信息不完整。')
        refill = plan.get('refill', {})
        if refill.get('enabled'):
            if len({(a['model'], a['effort']) for a in assignments}) != 1:
                raise RadarError('补领计划必须明确限定同一模型及非 ultra 档位。')
            if type(refill.get('max_tasks')) is not int or refill['max_tasks'] < len(assignments):
                raise RadarError('补领计划缺少有效任务数上限。')
    return plan


def dispatch(mode, arguments, executable=None):
    if mode not in {'query', 'control', 'exact'}:
        raise RadarError('未知控制操作。')
    if mode == 'query' and (len(arguments) != 4 or arguments[0] != 'progress' or arguments[1] != '--plan' or arguments[3] != '--json'):
        raise RadarError('登记计划只允许 progress --plan <code> --json。')
    # A managed project must keep using its existing mandatory transport.
    if (HOME.parent/'dradar-tenbin.ps1').exists() or (ROOT/'dradar-tenbin.ps1').exists():
        raise RadarError('此数据目录属于受管理的路由项目。请使用项目自己的控制入口；通用客户端仅查询，不替代强制路由。')
    runtime = paths.read_settings().get('runtime', {})
    prefix = runtime.get('command')
    if not isinstance(prefix, list) or not prefix or not all(isinstance(s, str) and s for s in prefix):
        raise RadarError('尚未配置 runtime.command 命令数组；未启动任何程序。运行 radar config 查看配置位置。')
    command = prefix + list(arguments)
    if mode == 'exact':
        if runtime.get('allow_exact_commands') is not True or not executable:
            raise RadarError('精确命令需要显式配置 runtime.allow_exact_commands=true。')
        command = [executable, *arguments]
    if Path(command[0]).suffix.lower() in {'.ps1', '.cmd', '.bat'}:
        raise RadarError('runtime.command 必须以可执行文件开头；不能隐式启动 shell 脚本。')
    cwd = Path(runtime.get('cwd', ROOT)).expanduser().resolve()
    if not cwd.is_dir():
        raise RadarError('runtime.cwd 目录不存在。')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', DRADAR_HOME=str(HOME))
    # No shell string interpolation and no inherited stdout: redact before display.
    try:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding='utf-8', shell=False)
    except OSError:
        raise RadarError('无法启动已配置运行器；检查可执行文件与工作目录。') from None
    output = []
    output_size = 0
    try:
        for line in proc.stdout:
            if mode != 'query':
                try:
                    display = json.dumps(safe(json.loads(line)), ensure_ascii=False)
                except ValueError:
                    display = safe(line.rstrip())
                print(display, file=sys.stderr, flush=True)
            elif output_size < 2_000_000:
                output.append(line)
                output_size += len(line)
        return proc.wait(), ''.join(output)
    except KeyboardInterrupt:
        # Do not kill Docker or release unrelated work. Let the official runtime
        # receive console Ctrl-C, then explicitly inspect progress before stop.
        raise RadarError('控制被中断；请检查 progress，需要时执行 stop。') from None
    finally:
        proc.stdout.close()


def prepare(code, harness):
    state = saved_plan(code)
    if not state:
        rc, _ = dispatch('query', ['progress', '--plan', code, '--json'])
        state = saved_plan(code)
        if not state:
            raise RadarError(f'官方客户端未能登记运行码（退出码 {rc}）；未启动任务。')
    validate_plan(state, harness)
    return state


def progress(api, args):
    if args.plan_id:
        if not re.fullmatch(r'[0-9a-f]{32}', args.plan_id):
            raise RadarError('plan-id 必须是本地计划的 32 位十六进制 ID。')
        state = read_json(HOME / 'run-plans' / f'plan-{args.plan_id}.json')
    else:
        state = saved_plan(args.plan)
    if not state:
        raise RadarError('此运行码尚未在本机登记。先执行 prepare；查询不会自动兑换运行码。')
    plan = validate_plan(state, args.harness)
    token = state.get('token')
    if not token:
        raise RadarError('本地计划凭据已清理；请在网站获取新的运行说明。')
    response = api.request('run-plans/progress', token=token,
                           payload={'schema_version': 1, 'plan_id': plan['plan_id']})
    return safe(response)


def exact_command(path, code, action):
    data = read_json(Path(path))
    executable = data.get('FilePath')
    argv = data.get('ArgumentList')
    if not isinstance(executable, str) or not executable or not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise RadarError('命令文件需要 FilePath 字符串和 ArgumentList 字符串数组。')
    try:
        index = argv.index('--plan')
        if argv[index + 1] != code or argv[index - 1] != ('stop' if action == 'stop' else 'run'):
            raise ValueError
    except (ValueError, IndexError):
        raise RadarError('精确参数必须包含匹配的 run/stop --plan <运行码>。') from None
    if action == 'upload' and '--upload-only' not in argv:
        raise RadarError('upload 的精确命令缺少 --upload-only。')
    if any(a in argv for a in ['--auto', '--refill', '--pick']):
        raise RadarError('精确运行计划不能混入另一次领题命令。')
    return executable, argv


def control(args):
    if args.command == 'prepare':
        state = prepare(args.plan, args.harness)
        plan = validate_plan(state, args.harness, execution=True)
        return {'prepared': True, 'plan': fields(plan, 'plan_id harness benchmark_id concurrency refill assignments'), 'started': False}
    # A dry run never exchanges a new run code.
    state = saved_plan(args.plan) if args.dry_run else prepare(args.plan, args.harness)
    plan = validate_plan(state, args.harness, execution=args.command == 'run')
    if state.get('token'):
        SECRETS.add(state['token'])
    command = 'stop' if args.command == 'stop' else 'run'
    argv = [command, '--plan', args.plan, '--json']
    if args.command == 'stop':
        argv += ['--scope', args.scope]
    if args.command == 'upload':
        argv.append('--upload-only')
    if args.concurrency is not None:
        argv += ['--concurrency', args.concurrency]
    if args.decision_token:
        SECRETS.add(args.decision_token)
        argv += ['--decision-token', args.decision_token]
    if args.confirm:
        pending = state.get('pending_decision') or {}
        if args.decision_token or pending.get('command') != command or not pending.get('decision'):
            raise RadarError('没有匹配的待确认操作。先运行原命令并查看官方说明。')
        SECRETS.add(pending['decision'])
        argv += ['--decision-token', pending['decision']]
    executable = None
    if args.command_file:
        if args.concurrency is not None or args.decision_token or args.confirm:
            raise RadarError('精确命令文件不能与追加参数混用；请保留网站原数组。')
        executable, argv = exact_command(args.command_file, args.plan, args.command)
        if args.command == 'stop':
            try:
                if argv[argv.index('--scope') + 1] != args.scope:
                    raise ValueError
            except (ValueError, IndexError):
                raise RadarError('精确命令中的 --scope 必须与本次请求一致。') from None
    if args.dry_run:
        return {'dry_run': True, 'action': args.command, 'plan_id': plan['plan_id'],
                'harness': plan['harness'], 'concurrency': plan.get('concurrency'),
                'route': 'exact-command' if executable else 'configured-runtime',
                'exact_arguments_preserved': bool(executable), 'started': False}
    rc, _ = dispatch('exact' if executable else 'control', argv, executable)
    return {'action': args.command, 'exit_code': rc, 'plan_id': plan['plan_id'],
            'note': '官方输出在 stderr。退出码不代替容器路由验证或服务端判分。'}


def overview(api, args):
    data = parallel(identity=lambda: identity(api), board=lambda: api.get('leaderboard', benchmark=args.benchmark),
                    tasks=lambda: tasks(api, args), submissions=lambda: submissions(api, args))
    result = {k: v for k, v in data.items() if k not in {'identity', 'board'}}
    result['identity'] = data['identity']
    if 'error' in data['identity'] or 'error' in data['board']:
        result['rank'] = {'error': data['identity'].get('error') or data['board'].get('error')}
    else:
        result['rank'] = ranking(api, args, data['identity'], data['board'])
        other = copy.copy(args)
        other.period = 'all' if args.period == 'month' else 'month'
        result['other_rank'] = ranking(api, other, data['identity'], data['board'])
    try:
        result['hot'] = hot(api, args)
    except RadarError as exc:
        result['hot'] = {'error': str(exc)}
    try:
        result['local'] = local_status(harness=args.harness)
    except RadarError as exc:
        result['local'] = {'error': str(exc)}
    result['partial'] = any(isinstance(v, dict) and 'error' in v for v in result.values())
    return result


def render(result, json_mode, compact=False):
    result = safe(result)
    if json_mode:
        print(json.dumps(result, ensure_ascii=False, indent=None if compact else 2))
        return
    print(f"{result.get('harness', '')}  {result.get('observed_at', '')}")
    if 'error' in result:
        print('错误：' + result['error'])
        return
    data = result['data']
    if result.get('command') == 'overview':
        me = data.get('identity', {})
        print(f"账号：{me.get('nickname', '不可用')} / {me.get('github_login') or '雷达身份'}")
        for name in ['rank', 'tasks', 'submissions', 'hot']:
            print('\n' + {'rank': '排名', 'tasks': '我的任务', 'submissions': '最近提交', 'hot': '高倍率候选'}[name])
            render_section(data.get(name, {}))
            if name == 'rank' and data.get('other_rank'):
                render_section(data['other_rank'])
        local = data.get('local', {})
        print(f"\n本地：DRadar {(local.get('runtime') or {}).get('version', '未知')}；{len(local.get('plans', []))} 个保存计划（不代表活跃）")
    else:
        render_section(data)


def render_section(data):
    if 'error' in data:
        print('不可用：' + data['error'])
        return
    if 'rank' in data:
        standing = data.get('standing', {})
        print(f"{data['period']}：第 {data['rank'] or '未上榜'} / {data['competitors']}；月积分 {standing.get('month_points', '—')}；总积分 {standing.get('points', '—')}")
    if 'states' in data:
        print(f"持有 {data['count']} 个：{json.dumps(data['states'], ensure_ascii=False)}")
    if 'available_non_ultra' in data:
        print(f"可选 {data['available_non_ultra']} 个非 ultra 格子")
    if 'records_scope' in data:
        print(f"记录范围：{records_scope_label(data['records_scope'], data.get('harness', '当前 CLI'))}；题库 {data.get('benchmark', '当前题库')}；最新提交在前。")
    if 'items' in data:
        for row in data['items']:
            value = row.get('multiplier')
            mult = f' ×{value:g}' if value is not None else ''
            state = row.get('state') or row.get('grade_status') or ''
            timing = f" | {row['minutes']:g} 分钟" if row.get('minutes') is not None else ''
            cost = f" | API估价 ${row['api_equivalent_usd']:g}" if row.get('api_equivalent_usd') is not None else ''
            billing = ' | 按API计费' if row.get('billing_mode') == 'api' else ''
            print(f"  {state:10} {row.get('task', row.get('task_id', ''))} | {row.get('model', '')} / {row.get('effort', '')}{mult}{timing}{cost}{billing}")
            if 'grade_status' in row:
                print(f"    {row.get('harness', '未知 harness')} | 提交 {local_record_time(row.get('submitted_at'))} | 判分 {local_record_time(row.get('graded_at'))}")
        if not data['items']:
            print('  无记录')
    if not any(k in data for k in ['items', 'rank']):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    if data.get('note'):
        print(data['note'])


def parser():
    p = argparse.ArgumentParser(description='雷达 Codex / Claude CLI：默认只读，不依赖模型或 Tenbin 查询网站。')
    p.add_argument('harness', choices=['codex', 'claude-code'])
    p.add_argument('command', nargs='?', default='auto', choices=['auto', 'tui', 'dashboard', 'overview', 'whoami', 'rank', 'hot', 'tasks', 'submissions', 'benchmarks', 'local', 'prepare', 'run', 'progress', 'stop', 'upload', 'config'])
    p.add_argument('--version', action='version', version='zhongce-radar-cli ' + __version__)
    p.add_argument('--home', type=Path, help='DRadar 数据目录；默认 DRADAR_HOME 或 ~/.dradar')
    p.add_argument('--config', type=Path, help='客户端配置文件；不包含模型认证')
    p.add_argument('local_action', nargs='?', choices=['status'])
    p.add_argument('--json', action='store_true')
    p.add_argument('--benchmark', default='deep-swe')
    p.add_argument('--period', choices=['month', 'all'], default='month')
    p.add_argument('--records-scope', choices=['all', 'current'], default='all',
                   help='个人提交/判分范围：all 全账号（默认），current 仅当前 CLI harness；不改变 IQ/任务范围')
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--model')
    p.add_argument('--provider', help='仅用于 hot/overview 候选；Codex 默认 openai，Claude 默认 anthropic-subscription，all 显示全部')
    p.add_argument('--effort', choices=sorted(EFFORTS))
    p.add_argument('--exclude-effort', action='append', default=['ultra'])
    p.add_argument('--min-multiplier', type=float)
    p.add_argument('--mine', action='store_true', help='tasks 始终只显示本人持有格子')
    p.add_argument('--available', action='store_true', help='hot 始终只显示开放格子')
    p.add_argument('--watch', action='store_true')
    p.add_argument('--interval', type=int, default=60)
    p.add_argument('--plan', help='网站运行码；也可设置 RADAR_PLAN_CODE 避免写入 shell 历史')
    p.add_argument('--plan-id', help='progress 可直接使用 local status 显示的本地计划 ID')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--concurrency', help='保留网站默认；也可明确指定 auto 或 1..40')
    p.add_argument('--scope', choices=['this-device', 'all-devices'], default='this-device')
    p.add_argument('--decision-token', help='官方要求确认时返回的短期值；不会自动同意')
    p.add_argument('--confirm', action='store_true', help='明确确认官方上一次返回的操作；读取本机待确认值，不打印凭据')
    p.add_argument('--command-file', help='网站精确命令 JSON，字段 FilePath、ArgumentList；原样经过项目路由启动')
    return p


def select_mode(command, input_tty, output_tty, json_mode, watch):
    if command != 'auto':
        return command
    return 'tui' if input_tty and output_tty and not json_mode and not watch else 'dashboard'


def main(argv=None):
    global HOME
    args = parser().parse_args(argv)
    if args.home:
        HOME = args.home.expanduser().resolve()
    if args.config:
        os.environ['RADAR_CONFIG_FILE'] = str(args.config.expanduser().resolve())
    os.environ['DRADAR_HOME'] = str(HOME)
    if args.command == 'config':
        print(json.dumps({'data_home':str(HOME), 'config_file':str(paths.config_file()),
                          'cache_directory':str(paths.user_directory('cache'))}, ensure_ascii=False, indent=2))
        return 0
    args.command = select_mode(args.command, sys.stdin.isatty(), sys.stdout.isatty(), args.json, args.watch)
    args.plan = args.plan or os.environ.get('RADAR_PLAN_CODE')
    if args.plan:
        SECRETS.add(args.plan)
    if not 1 <= args.limit <= 200 or args.interval < 15:
        raise RadarError('limit 必须为 1..200，刷新间隔至少 15 秒。')
    if args.concurrency is not None and args.concurrency != 'auto' and (not args.concurrency.isdigit() or not 1 <= int(args.concurrency) <= 40):
        raise RadarError('concurrency 必须为 auto 或 1..40。')
    controls = {'prepare', 'run', 'stop', 'upload'}
    if args.command in controls | {'progress'} and not args.plan and not (args.command == 'progress' and args.plan_id):
        raise RadarError('需要 --plan 或 RADAR_PLAN_CODE。')
    if args.watch and args.command in controls:
        raise RadarError('写入操作不允许 --watch。')
    if args.concurrency is not None and args.command != 'run':
        raise RadarError('只有 run 支持 --concurrency。')
    if args.local_action and args.command != 'local':
        raise RadarError('status 子命令只能用于 local。')
    if args.plan_id and (args.command != 'progress' or args.plan):
        raise RadarError('--plan-id 只用于 progress，且不能同时指定运行码。')
    if (args.confirm or args.decision_token or args.command_file or args.dry_run) and args.command not in {'run', 'stop', 'upload'}:
        raise RadarError('控制选项只能用于 run、stop 或 upload。')
    api = API()
    if args.command in {'tui', 'dashboard'}:
        from . import radar_tui
        if args.command == 'tui':
            if args.json:
                raise RadarError('tui 不输出 JSON；请使用 dashboard --json。')
            return radar_tui.run(api, args)
    handlers = {'overview': lambda: overview(api, args), 'whoami': lambda: identity(api),
                'rank': lambda: ranking(api, args), 'hot': lambda: hot(api, args),
                'tasks': lambda: tasks(api, args), 'submissions': lambda: submissions(api, args),
                'benchmarks': lambda: api.get('benchmarks'), 'local': lambda: local_status(harness=args.harness),
                'progress': lambda: progress(api, args)}
    if args.command == 'dashboard':
        handlers['dashboard'] = lambda: radar_tui.dashboard(api, args)
    while True:
        try:
            data = control(args) if args.command in controls else handlers[args.command]()
            result = {'schema_version': 1, 'command': args.command, 'harness': args.harness, 'observed_at': now(), 'data': data}
            rc = data.get('exit_code', 2 if data.get('partial') or data.get('errors') else 0)
        except RadarError as exc:
            result = {'schema_version': 1, 'command': args.command, 'harness': args.harness, 'observed_at': now(), 'error': str(exc)}
            rc = 2
        if args.command == 'dashboard' and not args.json and 'data' in result:
            lines, _ = radar_tui.document(result['data'], shutil.get_terminal_size((110, 30)).columns)
            print('\n'.join(line for _, line in lines))
        else:
            render(result, args.json, compact=args.watch)
        if not args.watch:
            return rc
        sys.stdout.flush()
        time.sleep(args.interval)
