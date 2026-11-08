import asyncio
import os
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import pytest

from app.main import app
from app.lifecycle import Worker
from app.providers import MockProvider

KEY = 'test-secret-key-with-at-least-32-characters'
HEADERS = {'Authorization': 'Bearer ' + KEY}
BODY = {'model': 'glm', 'messages': [{'role': 'user', 'content': 'Hello'}]}


@pytest.fixture
def client(tmp_path):
    with patch.dict(os.environ, {'API_KEYS': KEY, 'PROVIDER': 'mock', 'MODEL_NAME': 'glm', 'DATABASE_PATH': str(tmp_path / 'test.db'), 'OPENROUTER_API_KEY': ''}):
        with TestClient(app) as client:
            yield client


def test_auth_and_validation_do_not_boot(client):
    assert client.post('/v1/chat/completions', json=BODY).status_code == 401
    assert client.post('/v1/chat/completions', headers=HEADERS, json={}).status_code == 400
    assert client.post('/v1/chat/completions', headers=HEADERS,
                       json={**BODY, 'model': 'missing'}).status_code == 404
    assert app.state.worker.provider.starts == 0


def test_mock_chat_and_stream_release_lease(client):
    response = client.post('/v1/chat/completions', json=BODY, headers=HEADERS)
    assert response.status_code == 200
    assert 'Mock worker' in response.json()['choices'][0]['message']['content']
    response = client.post('/v1/chat/completions', json={**BODY, 'stream': True}, headers=HEADERS)
    assert 'data: [DONE]' in response.text
    assert app.state.worker.active == 0


def test_concurrent_boot_idle_and_capacity():
    async def scenario():
        provider = MockProvider()
        worker = Worker(provider, idle_seconds=0.01, capacity=2)
        await asyncio.gather(worker.acquire(), worker.acquire())
        assert provider.starts == 1
        with pytest.raises(OverflowError):
            await worker.acquire()
        worker.last_used = time.monotonic() - worker.idle_seconds - 1
        await worker.reap()
        assert provider.stops == 0
        worker.release()
        worker.release()
        worker.last_used = time.monotonic() - worker.idle_seconds - 1
        await worker.reap()
        assert provider.stops == 1
        await worker.acquire()
        assert provider.starts == 2
        worker.release()
    asyncio.run(scenario())


def test_failed_start_can_retry_and_reap():
    class Failing(MockProvider):
        async def start(self):
            self.starts += 1
            if self.starts == 1:
                raise RuntimeError('No GPU capacity')
            self.running = True
    async def scenario():
        worker = Worker(Failing())
        with pytest.raises(RuntimeError):
            await worker.acquire()
        assert worker.active == 0
        await worker.acquire()
        assert worker.state == 'ready'
        worker.release()
    asyncio.run(scenario())


def test_tool_calls_and_stream_forwarded(client):
    seen = []
    def handler(request):
        import json
        seen.append(json.loads(request.content))
        assert request.headers['Authorization'] == 'Bearer private-worker-key'
        if seen[-1].get('stream'):
            return httpx.Response(200, text='data: {"choices":[{"delta":{"tool_calls":[]}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={'choices': [{'message': {'tool_calls': [
            {'id': 'call_1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"main.py"}'}}
        ]}}]})
    body = {**BODY, 'tools': [{'type': 'function', 'function': {'name': 'read_file',
        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}}}}], 'tool_choice': 'auto'}
    original = app.state.client
    app.state.mock = False
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with patch.dict(os.environ, {'UPSTREAM_URL': 'http://worker', 'UPSTREAM_API_KEY': 'private-worker-key'}):
            response = client.post('/v1/chat/completions', headers=HEADERS, json=body)
            assert response.json()['choices'][0]['message']['tool_calls'][0]['id'] == 'call_1'
            assert seen[-1] == {**body, 'max_tokens': 1024}
            response = client.post('/v1/chat/completions', headers=HEADERS, json={**body, 'stream': True})
            assert 'tool_calls' in response.text and '[DONE]' in response.text
            assert app.state.worker.active == 0
    finally:
        asyncio.run(app.state.client.aclose())
        app.state.client = original


def test_reasoning_details_are_forwarded_unchanged(client):
    reasoning_details = [
        {'type': 'reasoning.text', 'text': 'provider reasoning', 'signature': 'signed-value'},
        {'type': 'reasoning.encrypted', 'data': 'opaque-provider-data'},
    ]
    assistant = {'role': 'assistant', 'content': 'First answer', 'reasoning_details': reasoning_details}
    messages = [BODY['messages'][0], assistant, {'role': 'user', 'content': 'Continue'}]

    def handler(request):
        import json
        payload = json.loads(request.content)
        assert payload['messages'] == messages
        assert payload['messages'][1]['reasoning_details'] == reasoning_details
        return httpx.Response(200, json={
            'choices': [{'message': {'role': 'assistant', 'content': 'Continued'}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15},
        })

    original = app.state.client
    app.state.mock = False
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with patch.dict(os.environ, {'UPSTREAM_URL': 'http://worker', 'UPSTREAM_API_KEY': 'private-worker-key'}):
            response = client.post('/v1/chat/completions', headers=HEADERS,
                                   json={**BODY, 'messages': messages, 'max_tokens': 32})
            assert response.status_code == 200
            assert response.json()['choices'][0]['message']['content'] == 'Continued'
    finally:
        asyncio.run(app.state.client.aclose())
        app.state.client = original


def test_cancelled_waiter_does_not_cancel_shared_boot():
    class Slow(MockProvider):
        async def start(self):
            await asyncio.sleep(0.02)
            await super().start()
    async def scenario():
        worker = Worker(Slow())
        waiter = asyncio.create_task(worker.acquire())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await worker.acquire()
        assert worker.active == 1
        assert worker.provider.starts == 1
        worker.release()
    asyncio.run(scenario())


def test_upstream_failure_releases_lease(client):
    def handler(request):
        raise httpx.ConnectError('worker disconnected', request=request)
    original = app.state.client
    app.state.mock = False
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with patch.dict(os.environ, {'UPSTREAM_URL': 'http://worker', 'UPSTREAM_API_KEY': 'private-worker-key'}):
            response = client.post('/v1/chat/completions', headers=HEADERS, json=BODY)
            assert response.status_code == 502
            assert app.state.worker.active == 0
    finally:
        asyncio.run(app.state.client.aclose())
        app.state.client = original


def test_startup_timeout_and_restart_reconciliation():
    class Unready(MockProvider):
        async def ready(self):
            return False
    async def scenario():
        provider = Unready()
        worker = Worker(provider, startup_timeout=0.01)
        with pytest.raises(asyncio.TimeoutError):
            await worker.acquire()
        assert worker.active == 0
        worker.last_used = time.monotonic() - worker.idle_seconds - 1
        await worker.reap()
        assert provider.stops == 1
        # A new controller must also stop an orphaned running VM after idle.
        provider.running = True
        restarted = Worker(provider)
        restarted.last_used = time.monotonic() - restarted.idle_seconds - 1
        await restarted.reap()
        assert not provider.running
    asyncio.run(scenario())
