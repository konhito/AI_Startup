import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
import time
from unittest.mock import patch
from urllib.parse import parse_qs

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from app.main import app

SECRET = 'whsec_local_test_not_a_real_credential'


@pytest.fixture
def client(tmp_path):
    with patch.dict(os.environ, {'API_KEYS': '', 'PROVIDER': 'mock', 'DATABASE_PATH': str(tmp_path / 'billing.db'),
        'STRIPE_SECRET_KEY': 'sk_test_local_not_real', 'STRIPE_WEBHOOK_SECRET': SECRET,
        'PUBLIC_ORIGIN': 'http://testserver', 'TRIAL_CREDIT_MICRO_USD': '0', 'OPENROUTER_API_KEY': ''}):
        with TestClient(app) as client:
            result = client.post('/api/auth/register', json={'name': 'Buyer', 'email': 'buyer@example.com',
                'password': 'a-long-testing-password'})
            assert result.status_code == 201
            yield client


@pytest.fixture
def stripe_mock(client):
    original = app.state.client
    sessions = {}
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.host == 'api.stripe.com'
        if request.method == 'POST':
            data = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            order_id = data['metadata[order_id]']
            session_id = 'cs_test_' + order_id
            assert data['line_items[0][price_data][unit_amount]'] in ('500', '2000', '5000')
            assert request.headers['Idempotency-Key'] == 'unbound-' + order_id
            sessions[session_id] = {'id': session_id, 'url': 'https://checkout.stripe.com/c/pay/' + session_id,
                'metadata': {'application': 'unbound', 'order_id': order_id},
                'client_reference_id': data['client_reference_id'], 'amount_total': int(data['line_items[0][price_data][unit_amount]']),
                'currency': 'usd', 'mode': 'payment', 'livemode': False, 'payment_status': 'unpaid', 'status': 'open'}
            return httpx.Response(200, json=sessions[session_id])
        return httpx.Response(200, json=sessions[request.url.path.split('/')[-1]])
    replacement = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.state.client = replacement
    yield sessions, calls
    asyncio.run(replacement.aclose())
    app.state.client = original


def checkout(client, key='checkout-test-001', pack='starter'):
    response = client.post('/api/billing/checkout', headers={'Idempotency-Key': key}, json={'pack_id': pack})
    assert response.status_code == 200, response.text
    return response.json()


def webhook(client, session_id, kind='checkout.session.completed', timestamp=None, secret=SECRET):
    timestamp = int(time.time()) if timestamp is None else timestamp
    raw = json.dumps({'id': 'evt_test_123', 'object': 'event', 'type': kind,
                      'data': {'object': {'id': session_id}}}).encode()
    signature = hmac.new(secret.encode(), str(timestamp).encode() + b'.' + raw, hashlib.sha256).hexdigest()
    return client.post('/api/billing/webhook', content=raw,
        headers={'Stripe-Signature': f't={timestamp},v1={signature}', 'Content-Type': 'application/json'})


def test_checkout_signature_payment_verification_and_idempotency(client, stripe_mock):
    sessions, calls = stripe_mock
    order = checkout(client)
    assert checkout(client) == order
    assert len(calls) == 1
    session = sessions['cs_test_' + order['order_id']]
    assert webhook(client, session['id']).json()['credited'] is False
    assert client.get('/api/me').json()['wallet']['balance_micro_usd'] == 0
    session['payment_status'] = 'paid'
    session['status'] = 'complete'
    assert webhook(client, session['id'], secret='wrong-secret').status_code == 400
    assert webhook(client, session['id'], timestamp=int(time.time()) - 600).status_code == 400
    assert webhook(client, session['id']).json()['credited'] is True
    assert webhook(client, session['id']).json()['credited'] is False
    wallet = client.get('/api/me').json()['wallet']
    assert wallet['balance_micro_usd'] == 0
    assert wallet['test_balance_micro_usd'] == 5_000_000
    assert client.get('/api/billing/orders/' + order['order_id']).json()['order']['status'] == 'paid'
    assert client.get('/api/ledger').json()['entries'][0]['kind'] == 'test_payment'
    assert client.post('/api/billing/checkout', headers={'Idempotency-Key': 'checkout-test-001'},
                       json={'pack_id': 'starter'}).status_code == 409


@pytest.mark.parametrize('field,value', [('currency','eur'), ('amount_total',1), ('livemode',True),
    ('client_reference_id','another-user'), ('mode','subscription'), ('amount_total',500.0)])
def test_mismatched_payment_does_not_credit(client, stripe_mock, field, value):
    sessions, _ = stripe_mock
    order = checkout(client)
    session = sessions['cs_test_' + order['order_id']]
    session.update({'payment_status': 'paid', 'status': 'complete', field: value})
    assert webhook(client, session['id']).status_code == 409
    assert client.get('/api/me').json()['wallet']['balance_micro_usd'] == 0


def test_credit_amount_cannot_be_controlled_by_browser(client, stripe_mock):
    assert client.post('/api/billing/checkout', json={'pack_id': 'starter', 'amount_cents': 1},
        headers={'Idempotency-Key': 'tampering-test'}).status_code == 422
    assert client.post('/api/billing/checkout', json={'pack_id': 'unknown'},
        headers={'Idempotency-Key': 'tampering-test'}).status_code == 400
    assert client.post('/api/billing/checkout', json={'pack_id': 'starter'}).status_code == 400
    order = checkout(client)
    client.get('/?checkout=' + order['order_id'] + '#workspace')
    assert client.get('/api/me').json()['wallet']['balance_micro_usd'] == 0
    assert client.post('/api/billing/checkout', json={'pack_id': 'builder'},
        headers={'Idempotency-Key': 'checkout-test-001'}).status_code == 409


def test_orders_are_private_and_checkout_checks_origin(client, stripe_mock):
    order = checkout(client)
    assert client.post('/api/billing/checkout', json={'pack_id': 'starter'},
        headers={'Origin': 'https://other.example', 'Idempotency-Key': 'csrf-test-123'}).status_code == 403
    client.post('/api/auth/logout')
    assert client.get('/api/billing/orders').status_code == 401
    client.post('/api/auth/register', json={'name': 'Other', 'email': 'other@example.com',
        'password': 'another-long-password'})
    assert client.get('/api/billing/orders/' + order['order_id']).status_code == 404
    assert client.get('/api/billing/orders').json()['orders'] == []


def test_disabled_and_live_checkout_require_configuration(client):
    with patch.dict(os.environ, {'STRIPE_SECRET_KEY': ''}):
        assert client.get('/api/billing/config').json()['enabled'] is False
        assert client.post('/api/billing/checkout', json={'pack_id': 'starter'}).status_code == 503
    with patch.dict(os.environ, {'STRIPE_SECRET_KEY': 'sk_live_local_not_real', 'COOKIE_SECURE': 'false'}):
        assert client.get('/api/billing/config').json()['enabled'] is False


def test_concurrent_payment_events_credit_once(client, stripe_mock):
    sessions, _ = stripe_mock
    order = checkout(client)
    session = sessions['cs_test_' + order['order_id']]
    session.update(payment_status='paid', status='complete')
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: app.state.billing.fulfill(session), range(8)))
    assert sum(results) == 1
    wallet = client.get('/api/me').json()['wallet']
    assert wallet['balance_micro_usd'] == 0
    assert wallet['test_balance_micro_usd'] == 5_000_000


def test_expired_then_verified_delayed_payment(client, stripe_mock):
    sessions, _ = stripe_mock
    order = checkout(client)
    session = sessions['cs_test_' + order['order_id']]
    session['status'] = 'expired'
    assert webhook(client, session['id'], kind='checkout.session.expired').json()['credited'] is False
    assert client.get('/api/billing/orders/' + order['order_id']).json()['order']['status'] == 'expired'
    session.update(payment_status='paid', status='complete')
    assert webhook(client, session['id'], kind='checkout.session.async_payment_succeeded').json()['credited'] is True


def test_async_failure_is_recorded_without_credit(client, stripe_mock):
    sessions, _ = stripe_mock
    order = checkout(client)
    session = sessions['cs_test_' + order['order_id']]
    assert webhook(client, session['id'], kind='checkout.session.async_payment_failed').json()['credited'] is False
    assert client.get('/api/billing/orders/' + order['order_id']).json()['order']['status'] == 'failed'
    wallet = client.get('/api/me').json()['wallet']
    assert wallet['balance_micro_usd'] == 0 and wallet['test_balance_micro_usd'] == 0
