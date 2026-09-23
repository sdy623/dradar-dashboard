import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zhongce_radar import radar
from zhongce_radar import radar_tui as ui


class AsyncDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_account_scope_keeps_latest_claude_record_in_codex_dashboard(self):
        from zhongce_radar.radar_async import load_dashboard
        class API:
            async def get(self, name, **query):
                if name == 'my-submissions':
                    return {'submissions': [{'task_id':'latest','harness':'claude-code','benchmark_id':'deep-swe'}]}
                if name == 'assignment': return {'active':[]}
                if name == 'table': return {'combos':[],'cells':{},'tasks':[]}
                if name == 'intelligence-efficiency': return {'mode':'equal_latest_3','points':[]}
                return {'contributors':[]}
        args = radar.parser().parse_args(['codex','dashboard'])
        args.records_scope='all'
        result = await load_dashboard(API(),args)
        self.assertEqual(result['personal']['items'][0]['task_id'],'latest')
        self.assertEqual(result['personal']['records_scope'],'all')

    async def test_real_aiohttp_fast_response_is_not_blocked_by_slow_socket(self):
        from aiohttp import web, ClientSession
        from zhongce_radar.radar_http import request
        release = asyncio.Event()
        async def slow(req):
            await release.wait()
            return web.json_response({'slow': True})
        async def fast(req):
            return web.json_response({'fast': True})
        app = web.Application()
        app.router.add_get('/slow', slow)
        app.router.add_get('/fast', fast)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        address = 'http://127.0.0.1:' + str(runner.addresses[0][1])
        try:
            async with ClientSession() as session:
                waiting = asyncio.create_task(request(session, address + '/slow', {}))
                result = await asyncio.wait_for(request(session, address + '/fast', {}), 1)
                self.assertEqual(result, {'fast': True})
                self.assertFalse(waiting.done())
                release.set()
                await waiting
        finally:
            release.set()
            await runner.cleanup()

    async def test_iq_and_personal_render_before_slow_table(self):
        from zhongce_radar.radar_async import load_dashboard
        release = asyncio.Event()
        updates = []
        class API:
            async def get(self, name, **query):
                if name == 'table':
                    await release.wait()
                    return {'tasks': [], 'cells': {}, 'combos': []}
                if name == 'intelligence-efficiency':
                    return {'mode': 'equal_latest_3', 'points': [{'model': 'gpt-6-astra', 'effort': 'high', 'iq': 110, 'total': 12}]}
                if name == 'whoami':
                    return {'nickname': 'me'}
                if name == 'my-submissions':
                    return {'submissions': [{'harness': 'codex', 'benchmark_id': 'deep-swe', 'task_id': 'mine'}]}
                if name == 'assignment':
                    return {'active': []}
                return {'contributors': [], 'riders': [], 'online_volunteers': 3}
        args = radar.parser().parse_args(['codex', 'dashboard'])
        def progress(data):
            updates.append(data)
            if data.get('iq') and data.get('personal', {}).get('items'):
                release.set()
        result = await asyncio.wait_for(load_dashboard(API(), args, on_progress=progress, timeout=.15), .5)
        ready = [d for d in updates if d.get('iq') and d.get('personal', {}).get('items')]
        self.assertTrue(ready, 'Neither IQ nor personal data may wait for table completion')
        self.assertIn('table', ready[0]['loading_sections'])
        self.assertIsNone(ready[0]['iq'][0]['running'])
        self.assertEqual(ready[0]['iq'][0]['coverage'], '—')
        self.assertFalse(result['partial_loading'])

    async def test_timeout_cancels_only_pending_io(self):
        from zhongce_radar.radar_async import load_dashboard
        from types import SimpleNamespace
        cancelled = asyncio.Event()
        started = []
        class API:
            async def get(self, name, **query):
                started.append(name)
                if name == 'whoami':
                    return {'nickname': 'loaded'}
                try:
                    await asyncio.sleep(10)
                finally:
                    cancelled.set()
        args = radar.parser().parse_args(['codex', 'dashboard'])
        # Expire after the first I/O scheduling round, independent of OS timing.
        clock = SimpleNamespace(monotonic=lambda: 0 if len(started) < 7 else 1)
        with patch('zhongce_radar.radar_async.time', clock):
            result = await load_dashboard(API(), args, timeout=.5)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(result['identity']['nickname'], 'loaded')
        self.assertIn('iq', result['errors'])

    async def test_http_redirect_never_forwards_private_token(self):
        from zhongce_radar.radar_http import AsyncAPI
        from unittest.mock import Mock, AsyncMock
        response = Mock(status=302)
        context = Mock(__aenter__=AsyncMock(return_value=response), __aexit__=AsyncMock(return_value=False))
        client = Mock(request=Mock(return_value=context))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config.json').write_text(json.dumps({'token': 'private-fixture'}), encoding='utf-8')
            api = AsyncAPI(radar.API(root), client)
            with self.assertRaises(radar.RadarError):
                await api.get('whoami', private=True)
        self.assertEqual(client.request.call_count, 1)
        request = client.request.call_args
        self.assertEqual(request.args[1], 'https://api.codexradar.com/api/v1/whoami')
        self.assertEqual(request.kwargs['headers']['Authorization'], 'Bearer private-fixture')
        self.assertFalse(request.kwargs['allow_redirects'])

    async def test_async_transport_refuses_claim_endpoint(self):
        from zhongce_radar.radar_async import AsyncAPI
        api = AsyncAPI(radar.API(), None)
        with self.assertRaises(radar.RadarError):
            await api.get('assignment', private=True)


class CacheTests(unittest.TestCase):
    def test_first_screen_precedes_http_start_and_quit_does_not_wait(self):
        import io
        import threading
        import time
        from unittest.mock import Mock
        output = io.StringIO()
        output.isatty = lambda: True
        terminal = Mock()
        terminal.__enter__ = Mock(return_value=terminal)
        terminal.__exit__ = Mock(return_value=False)
        terminal.read.side_effect = [None, 'q']
        observed = []
        completed = threading.Event()
        def fetch(api, args, on_progress, stop):
            observed.append('众测雷达' in output.getvalue())
            stop.wait(1)
            completed.set()
            return None
        args = radar.parser().parse_args(['codex', 'tui'])
        started = time.monotonic()
        with patch('sys.stdout', output), patch('sys.stdin.isatty', return_value=True), patch.object(ui, 'Terminal', return_value=terminal), patch.object(ui, 'dashboard', side_effect=fetch):
            self.assertEqual(ui.run(None, args), 0)
        self.assertTrue(completed.wait(.5))
        self.assertEqual(observed, [True])
        self.assertLess(time.monotonic() - started, .75)

    def test_cache_contains_only_public_iq_and_marks_age(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = radar.parser().parse_args(['codex', 'dashboard'])
            data = {'harness': 'codex', 'benchmark': 'deep-swe', 'observed_at': radar.now(),
                    'iq': [{'model': 'gpt-6-astra', 'effort': 'high', 'iq': 111}],
                    'identity': {'nickname': 'PRIVATE-NAME'}, 'personal': {'items': ['PRIVATE-RESULT']}}
            ui.save_iq_cache(data, root)
            snapshot = ui.load_iq_cache(args, root)
            self.assertTrue(snapshot['cached'])
            self.assertEqual(snapshot['iq'][0]['iq'], 111)
            saved = ''.join(p.read_text(encoding='utf-8') for p in root.glob('*.json'))
            self.assertNotIn('PRIVATE', saved)
            text = '\n'.join(row[1] for row in ui.document(snapshot, 100)[0])
            self.assertIn('缓存', text)
            self.assertTrue(snapshot['personal']['loading'])
