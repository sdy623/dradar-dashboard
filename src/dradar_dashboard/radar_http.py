"""All website traffic uses one aiohttp event loop and a reusable connection pool."""
from __future__ import annotations

import asyncio
import atexit
import json
import threading
import urllib.parse

import aiohttp
from . import radar


def create_session():
    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_read=10),
                                 connector=aiohttp.TCPConnector(limit=10, limit_per_host=10, ttl_dns_cache=300),
                                 cookie_jar=aiohttp.DummyCookieJar(), trust_env=True)


def prepare(credentials, endpoint, private=False, token=None, payload=None):
    name = endpoint.split('?')[0]
    if name not in {'benchmarks', 'table', 'leaderboard', 'intelligence-efficiency', 'whoami',
                    'my-submissions', 'my-cells', 'assignment', 'run-plans/progress'}:
        raise radar.RadarError('查询接口不在允许列表中。')
    if payload is not None and endpoint != 'run-plans/progress':
        raise radar.RadarError('查询模块不允许写入操作。')
    if name == 'assignment' and urllib.parse.parse_qs(urllib.parse.urlsplit(endpoint).query).get('inventory') != ['true']:
        raise radar.RadarError('任务查询必须使用只读 inventory=true。')
    headers = {'User-Agent': 'radar-cli/1.0', 'Accept': 'application/json'}
    if private or token:
        token = token or credentials.token()
        radar.SECRETS.add(token)
        headers['Authorization'] = 'Bearer ' + token
    return radar.SERVER + '/api/v1/' + endpoint, headers


async def request(session, url, headers, payload=None):
    try:
        async with session.request('POST' if payload is not None else 'GET', url,
                                   headers=headers, json=payload, allow_redirects=False) as response:
            if 300 <= response.status < 400:
                raise radar.RadarError('API 返回重定向；未向新地址发送凭据。')
            if response.status >= 400:
                reason = {401: '雷达身份已失效，请重新登录', 403: '服务器拒绝访问', 429: '请求过于频繁，请稍后重试'}.get(response.status, '请求失败')
                raise radar.RadarError(f'HTTP {response.status}：{reason}')
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > 24 * 1024 * 1024:
                    raise radar.RadarError('API 响应过大，已停止读取。')
            result = json.loads(body)
            if not isinstance(result, dict):
                raise radar.RadarError('API 响应格式改变。')
            return result
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
        raise radar.RadarError('API 网络或 JSON 响应异常；其他板块仍可浏览。') from None


class AsyncAPI:
    def __init__(self, credentials, client):
        self.credentials, self.client = credentials, client

    async def get(self, name, private=False, **query):
        endpoint = name + ('?' + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None}) if query else '')
        url, headers = prepare(self.credentials, endpoint, private=private)
        return await request(self.client, url, headers)


class Portal:
    """Synchronous command compatibility; sockets always run on the aiohttp loop."""
    def __init__(self):
        self.ready = threading.Event()
        self.session = None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        self.ready.wait()

    def _serve(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        self.loop.run_forever()
        self.loop.close()

    def submit(self, factory):
        async def invoke():
            if self.session is None:
                self.session = create_session()
            return await factory(self.session)
        return asyncio.run_coroutine_threadsafe(invoke(), self.loop)

    def close(self):
        if not self.thread.is_alive():
            return
        async def cleanup():
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.session is not None:
                await self.session.close()
            await asyncio.sleep(.05)
        try:
            asyncio.run_coroutine_threadsafe(cleanup(), self.loop).result(timeout=1)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=1)


_portal = None
_lock = threading.Lock()


def portal():
    global _portal
    with _lock:
        if _portal is None:
            _portal = Portal()
            atexit.register(_portal.close)
        return _portal


def sync_request(credentials, endpoint, **kwargs):
    url, headers = prepare(credentials, endpoint, **kwargs)
    return portal().submit(lambda session: request(session, url, headers, kwargs.get('payload'))).result()
