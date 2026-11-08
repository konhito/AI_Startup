import asyncio
from contextlib import asynccontextmanager, suppress
import json
import logging
import os
from pathlib import Path
import secrets

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .accounts import RateLimiter, pricing, router, user_session
from .inference import run_chat
from .lifecycle import Worker
from .providers import GCPProvider, MockProvider
from .store import Store
from .billing import BillingStore, router as billing_router


@asynccontextmanager
async def lifespan(app):
    keys = [key.strip() for key in os.getenv('API_KEYS', '').split(',') if key.strip()]
    if any(len(key) < 24 or key.startswith('replace-') for key in keys):
        raise RuntimeError('Optional operator API_KEYS must be generated secrets of at least 24 characters')
    app.state.keys = keys
    app.state.store = Store(os.getenv('DATABASE_PATH', 'data/unbound.db'))
    app.state.store.recover()
    app.state.billing = BillingStore(app.state.store)
    app.state.limiter = RateLimiter()
    app.state.stream_tasks = set()
    rates = pricing()
    if any(value < 0 for name, value in rates.items() if name.endswith('micro_usd')):
        raise RuntimeError('Prices and trial credits must be nonnegative')
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
        name = os.getenv('PROVIDER', 'mock')
        if name not in ('mock', 'gcp'):
            raise RuntimeError('PROVIDER must be mock or gcp')
        app.state.mock = name == 'mock'
        app.state.client = client
        provider = MockProvider() if app.state.mock else GCPProvider(client)
        idle = float(os.getenv('IDLE_SECONDS', '300'))
        startup = float(os.getenv('STARTUP_TIMEOUT', '900'))
        capacity = int(os.getenv('MAX_CONCURRENT', '8'))
        if min(idle, startup, capacity) <= 0:
            raise RuntimeError('Timeouts and capacity must be positive')
        app.state.worker = Worker(provider, idle, startup, capacity)
        watcher = asyncio.create_task(app.state.worker.watch())
        yield
        if app.state.stream_tasks:
            # Finish active accounting before closing provider connections.
            _, pending = await asyncio.wait(app.state.stream_tasks, timeout=30)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        warmup = app.state.worker.warmup
        if warmup and not warmup.done():
            warmup.cancel()
            with suppress(asyncio.CancelledError):
                await warmup
        try:
            await asyncio.wait_for(provider.stop(), 60)
        except Exception:
            logging.exception('Shutdown failed; check the GPU VM manually')


app = FastAPI(title='Unbound API', version='0.2.0', lifespan=lifespan)
app.include_router(router)
app.include_router(billing_router)


class RequestProtection:
    """Bound all request bodies without buffering SSE responses."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        messages = []
        length = 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            length += len(message.get('body', b''))
            if length > 2 * 1024 * 1024:
                return await JSONResponse({'detail': 'Request exceeds 2 MiB'}, status_code=413)(scope, receive, send)
            messages.append(message)
            if not message.get('more_body', False):
                break

        async def bounded_receive():
            return messages.pop(0) if messages else await receive()

        async def headers(message):
            if message['type'] == 'http.response.start':
                extra = [(b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'same-origin'),
                    (b'x-frame-options', b'DENY')]
                if scope['path'].startswith(('/api/', '/v1/', '/worker')):
                    extra.append((b'cache-control', b'no-store'))
                message['headers'] = list(message.get('headers', [])) + extra
            await send(message)
        await self.app(scope, bounded_receive, headers)


app.add_middleware(RequestProtection)


async def authenticate(request: Request):
    token = request.headers.get('authorization', '').removeprefix('Bearer ')
    if any(secrets.compare_digest(token.encode(), key.encode()) for key in request.app.state.keys):
        return None  # Operator keys are unmetered and never given to customers.
    identity = request.app.state.store.key_user(token)
    if identity is None:
        raise HTTPException(401, 'Invalid API key')
    return identity


@app.get('/healthz')
async def health():
    return {'status': 'ok'}


@app.get('/v1/models', dependencies=[Depends(authenticate)])
async def models():
    return {'object': 'list', 'data': [{'id': name, 'object': 'model', 'created': 0, 'owned_by': 'unbound'}
        for name in ('auto', 'qwen-derestricted', 'glm-5.3', os.getenv('MODEL_NAME', 'glm'))]}


@app.get('/worker', dependencies=[Depends(authenticate)])
async def worker_status(request: Request):
    worker = request.app.state.worker
    return {'state': worker.state, 'active_requests': worker.active,
            'provider': 'mock' if request.app.state.mock else 'gcp'}


async def read_body(request):
    try:
        return await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, 'Invalid JSON')


@app.post('/v1/chat/completions')
async def chat(request: Request, identity=Depends(authenticate)):
    return await run_chat(request, await read_body(request), identity)


@app.post('/api/chat')
async def browser_chat(request: Request, user=Depends(user_session)):
    body = await read_body(request)
    if not isinstance(body, dict):
        raise HTTPException(400, 'Expected a JSON object')
    body.setdefault('model', 'auto')
    body['stream'] = False
    return await run_chat(request, body, {'user_id': user['id'], 'id': None})


frontend = Path(__file__).resolve().parent.parent / 'frontend'
# check_dir=False lets API-only tests run while the frontend is being built.
app.mount('/assets', StaticFiles(directory=str(frontend), check_dir=False), name='assets')
app.mount('/', StaticFiles(directory=str(frontend), html=True, check_dir=False), name='frontend')
