import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from app.main import app
from app.store import Store, cost

ADMIN = 'administrator-secret-with-32-characters-minimum'
BODY = {'model': 'glm', 'messages': [{'role': 'user', 'content': 'Hello'}], 'max_tokens': 128}


@pytest.fixture
def client(tmp_path):
    with patch.dict(os.environ, {'API_KEYS': '', 'PROVIDER': 'mock', 'MODEL_NAME': 'glm',
        'DATABASE_PATH': str(tmp_path / 'accounts.db'), 'TRIAL_CREDIT_MICRO_USD': '1000000',
        'ADMIN_API_KEY': ADMIN, 'OPENROUTER_API_KEY': '', 'PUBLIC_ORIGIN': 'http://testserver',
        'INPUT_PRICE_MICRO_USD': '200000', 'OUTPUT_PRICE_MICRO_USD': '800000'}):
        with TestClient(app) as client:
            yield client


def signup(client, email='builder@example.com'):
    response = client.post('/api/auth/register', json={
        'name': 'Builder', 'email': email, 'password': 'long-password-for-testing'})
    assert response.status_code == 201, response.text
    return response.json()['user']


def key(client):
    response = client.post('/api/keys', json={'name': 'Coding agent'})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def provider(client):
    original = app.state.client
    clients = []
    def install(handler):
        app.state.mock = False
        replacement = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(replacement)
        app.state.client = replacement
    with patch.dict(os.environ, {'UPSTREAM_URL': 'http://worker', 'UPSTREAM_API_KEY': 'private-worker-key'}):
        yield install
    for replacement in clients:
        asyncio.run(replacement.aclose())
    app.state.client = original


def test_accounts_key_lifecycle_isolation_and_no_plaintext_secrets(client):
    first = signup(client)
    assert 'HttpOnly' in client.cookies.jar._cookies['testserver.local']['/']['unbound_session']._rest
    created = key(client)
    headers = {'Authorization': 'Bearer ' + created['key']}
    assert client.get('/v1/models', headers=headers).status_code == 200
    listed = client.get('/api/keys').json()['keys']
    assert created['key'] not in json.dumps(listed)
    with app.state.store.connection() as db:
        stored = db.execute('SELECT token_hash FROM api_keys').fetchone()[0]
        assert stored != created['key']
        assert db.execute('SELECT password_hash FROM users').fetchone()[0] != 'long-password-for-testing'
    assert client.post('/api/auth/logout').status_code == 204
    assert client.get('/api/me').status_code == 401
    signup(client, 'another@example.com')
    assert client.delete('/api/keys/' + created['record']['id']).status_code == 404
    assert client.get('/api/keys').json()['keys'] == []
    assert client.get('/api/usage').json()['recent'] == []
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login', json={'email': first['email'], 'password': 'wrong'}).status_code == 401
    assert client.post('/api/auth/login', json={'email': first['email'], 'password': 'long-password-for-testing'}).status_code == 200
    assert client.delete('/api/keys/' + created['record']['id']).status_code == 204
    assert client.get('/v1/models', headers=headers).status_code == 401


def test_csrf_and_body_limits(client):
    signup(client)
    assert client.post('/api/keys', json={'name': 'evil'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/auth/logout', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert client.post('/api/chat', content=b'x' * (2 * 1024 * 1024 + 1)).status_code == 413
    assert client.post('/api/chat', json={**BODY, 'max_tokens': -1}).status_code == 400
    assert client.post('/api/chat', json={**BODY, 'n': 2}).status_code == 400
    assert client.post('/api/chat', json={**BODY, 'best_of': 200}).status_code == 400


def test_mock_usage_free_and_persistent(client):
    user = signup(client)
    before = client.get('/api/me').json()['wallet']
    response = client.post('/api/chat', json=BODY)
    assert response.status_code == 200, response.text
    assert response.json()['metering']['cost_micro_usd'] == 0
    assert response.json()['routing']['provider'] == 'mock'
    data = client.get('/api/usage').json()
    assert data['summary']['requests'] == 1
    assert data['summary']['input_tokens'] > 0
    assert data['recent'][0]['status'] == 'mock'
    assert client.get('/api/me').json()['wallet'] == before
    assert Store(app.state.store.path).usage(user['id'])['summary']['requests'] == 1


def test_real_usage_debit_and_insufficient_funds(client, provider):
    user = signup(client)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Hello'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}})
    provider(handler)
    response = client.post('/api/chat', json=BODY)
    assert response.status_code == 200, response.text
    assert response.json()['metering']['cost_micro_usd'] == 36
    wallet = client.get('/api/me').json()['wallet']
    assert wallet['balance_micro_usd'] == 1_000_000 - 36 and wallet['held_micro_usd'] == 0
    assert client.get('/api/ledger').json()['entries'][0]['amount_micro_usd'] == -36
    with app.state.store.connection() as db:
        db.execute('UPDATE wallets SET balance=0 WHERE user_id=?', (user['id'],))
    assert client.post('/api/chat', json=BODY).status_code == 402
    assert len(calls) == 1


def test_stream_usage_split_frames_billed_once(client, provider):
    signup(client)
    created = key(client)
    chunks = ('data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
              'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":20}}\n\n'
              'data: [DONE]\n\n').encode()
    class SplitStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for start in range(0, len(chunks), 7):
                yield chunks[start:start + 7]
    def handler(request):
        assert json.loads(request.content)['stream_options']['include_usage'] is True
        return httpx.Response(200, stream=SplitStream())
    provider(handler)
    response = client.post('/v1/chat/completions', headers={'Authorization': 'Bearer ' + created['key']},
        json={**BODY, 'stream': True, 'stream_options': {'include_usage': False}})
    assert response.status_code == 200 and '[DONE]' in response.text
    usage = client.get('/api/usage').json()
    assert usage['summary']['cost_micro_usd'] == 36
    app.state.store.settle(usage['recent'][0]['id'], {'prompt_tokens': 100, 'completion_tokens': 20})
    assert client.get('/api/usage').json()['summary']['cost_micro_usd'] == 36
    assert client.get('/api/me').json()['wallet']['held_micro_usd'] == 0
    assert app.state.worker.active == 0


@pytest.mark.parametrize('payload', [None, {'prompt_tokens': -1, 'completion_tokens': 20},
    {'prompt_tokens': 10, 'completion_tokens': 999999}, {'prompt_tokens': True, 'completion_tokens': 1}])
def test_missing_or_invalid_usage_never_charged(client, provider, payload):
    signup(client)
    provider(lambda request: httpx.Response(200, json={'choices': [], 'usage': payload}))
    assert client.post('/api/chat', json=BODY).status_code == 502
    assert client.get('/api/me').json()['wallet']['balance_micro_usd'] == 1_000_000
    assert client.get('/api/me').json()['wallet']['held_micro_usd'] == 0


def test_admin_credits_are_authenticated_and_idempotent(client):
    user = signup(client)
    body = {'user_id': user['id'], 'amount_micro_usd': 500000}
    assert client.post('/api/admin/credits', json=body).status_code == 403
    headers = {'Authorization': 'Bearer ' + ADMIN, 'Idempotency-Key': 'test-payment-001'}
    assert client.post('/api/admin/credits', json=body, headers=headers).json()['applied'] is True
    assert client.post('/api/admin/credits', json=body, headers=headers).json()['applied'] is False
    assert client.get('/api/me').json()['wallet']['balance_micro_usd'] == 1_500_000
    assert client.post('/api/admin/credits', json={**body, 'amount_micro_usd': 2}, headers=headers).status_code == 409
    assert client.post('/api/admin/credits', json={**body, 'amount_micro_usd': -1}, headers=headers).status_code == 422


def test_concurrent_reservation_never_overspends_and_recovery(tmp_path):
    store = Store(tmp_path / 'atomic.db')
    user = store.register('Builder', 'builder@example.com', 'not-used-in-this-test', 100)
    def reserve(_):
        try:
            return store.reserve(user['id'], None, 'glm', 60, 0, 1_000_000, 0, False)
        except HTTPException as exc:
            assert exc.status_code == 402
            return None
    with ThreadPoolExecutor(max_workers=8) as executor:
        reservations = list(executor.map(reserve, range(8)))
    assert len([r for r in reservations if r]) == 1
    assert store.wallet(user['id'])['held_micro_usd'] == 60
    store.recover()
    assert store.wallet(user['id'])['held_micro_usd'] == 0
    assert store.usage(user['id'])['recent'][0]['status'] == 'interrupted'
    assert cost(1, 0, 200000, 800000) == 1


def test_openrouter_does_not_wake_gpu_and_tracks_model(client, provider):
    signup(client)
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Hosted answer'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
    provider(handler)
    with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-openrouter', 'OPENROUTER_MODEL': 'deepseek/deepseek-v3.2'}):
        response = client.post('/api/chat', json={**BODY, 'model': 'auto'})
    assert response.status_code == 200, response.text
    assert seen[0].url.host == 'openrouter.ai'
    assert response.json()['routing']['provider'] == 'openrouter'
    assert app.state.worker.provider.starts == 0
    assert client.get('/api/usage').json()['recent'][0]['provider'] == 'openrouter'


def test_arliai_auto_route_is_free_and_does_not_wake_gpu(client, provider):
    signup(client)
    seen = []
    def handler(request):
        seen.append((request, json.loads(request.content)))
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'Arli answer'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
    provider(handler)
    with patch.dict(os.environ, {'ARLIAI_API_KEY': 'test-arliai',
        'ARLIAI_MODEL': 'Qwen3.5-27B-Derestricted', 'OPENROUTER_API_KEY': ''}):
        response = client.post('/api/chat', json={**BODY, 'model': 'auto'})
    assert response.status_code == 200, response.text
    assert seen[0][0].url.host == 'api.arliai.com'
    assert seen[0][1]['model'] == 'Qwen3.5-27B-Derestricted'
    assert seen[0][1]['reasoning_effort'] == 'none'
    assert response.json()['routing']['provider'] == 'arliai'
    assert response.json()['metering']['cost_micro_usd'] == 0
    assert app.state.worker.provider.starts == 0
    assert client.get('/api/usage').json()['recent'][0]['provider'] == 'arliai'


def test_explicit_arliai_route_requires_key(client):
    signup(client)
    with patch.dict(os.environ, {'ARLIAI_API_KEY': ''}):
        response = client.post('/api/chat', json={**BODY, 'model': 'qwen-derestricted'})
    assert response.status_code == 503


@pytest.mark.parametrize('status,expected_calls', [(429, 2), (503, 2), (403, 1), (401, 1), (402, 1)])
def test_router_fallback_only_availability_errors(client, provider, status, expected_calls):
    signup(client)
    calls = []
    def handler(request):
        calls.append(request.url.host)
        if request.url.host == 'openrouter.ai':
            return httpx.Response(status, json={'error': {'message': 'Provider error'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': 'GLM answer'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
    provider(handler)
    with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-openrouter', 'ROUTER_FALLBACK': 'true'}):
        response = client.post('/api/chat', json={**BODY, 'model': 'auto'})
    assert len(calls) == expected_calls
    assert response.status_code == (200 if expected_calls == 2 else status)
    assert client.get('/api/me').json()['wallet']['held_micro_usd'] == 0
    if expected_calls == 2:
        assert response.json()['routing']['fallback'] is True
        assert client.get('/api/usage').json()['summary']['cost_micro_usd'] == 36


def test_refusal_returned_without_rerouting(client, provider):
    signup(client)
    calls = []
    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, json={'choices': [{'message': {'refusal': 'I cannot assist with this request'},
            'finish_reason': 'content_filter'}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 10}})
    provider(handler)
    with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-openrouter'}):
        response = client.post('/api/chat', json={**BODY, 'model': 'auto'})
    assert response.status_code == 200
    assert response.json()['choices'][0]['message']['refusal']
    assert calls == ['openrouter.ai']
    assert app.state.worker.provider.starts == 0


def test_explicit_glm53_openrouter_route_and_price_snapshot(client, provider):
    signup(client)
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'GLM answer'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
    provider(handler)
    with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-openrouter',
        'OPENROUTER_GLM53_MODEL': 'z-ai/glm-5.3',
        'OPENROUTER_GLM53_INPUT_PRICE_MICRO_USD': '1500000',
        'OPENROUTER_GLM53_OUTPUT_PRICE_MICRO_USD': '5000000'}):
        response = client.post('/api/chat', json={**BODY, 'model': 'glm-5.3'})
    assert response.status_code == 200, response.text
    assert seen[0]['model'] == 'z-ai/glm-5.3'
    assert response.json()['routing'] == {
        'provider': 'openrouter', 'model': 'z-ai/glm-5.3', 'fallback': False}
    assert response.json()['metering']['cost_micro_usd'] == 250
    assert app.state.worker.provider.starts == 0


def test_explicit_glm53_requires_openrouter_and_does_not_fallback(client, provider):
    signup(client)
    with patch.dict(os.environ, {'OPENROUTER_API_KEY': ''}):
        assert client.post('/api/chat', json={**BODY, 'model': 'glm-5.3'}).status_code == 503
    calls = []
    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(503, json={'error': {'message': 'Unavailable'}})
    provider(handler)
    with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-openrouter', 'ROUTER_FALLBACK': 'true'}):
        response = client.post('/api/chat', json={**BODY, 'model': 'glm-5.3'})
    assert response.status_code == 503
    assert calls == ['openrouter.ai']
