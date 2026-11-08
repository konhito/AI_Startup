"""Hosted checkout; a verified server-side payment is the only credit authority."""
import os
import re
import time
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
import stripe

from .accounts import user_session
from .store import now

router = APIRouter(prefix='/api/billing')
PACKS = {
    'starter': {'amount_cents': 500, 'credit_micro_usd': 5_000_000},
    'builder': {'amount_cents': 2000, 'credit_micro_usd': 20_000_000},
    'studio': {'amount_cents': 5000, 'credit_micro_usd': 50_000_000},
}


def settings():
    secret = os.getenv('STRIPE_SECRET_KEY', '')
    origin = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
    parsed = urlsplit(origin)
    live = secret.startswith(('sk_live_', 'rk_live_'))
    valid_key = secret.startswith(('sk_live_', 'sk_test_', 'rk_live_', 'rk_test_'))
    valid_origin = (parsed.scheme in ('http', 'https') and bool(parsed.netloc)
        and not parsed.path and not parsed.query and not parsed.fragment and not parsed.username)
    enabled = (valid_key and bool(os.getenv('STRIPE_WEBHOOK_SECRET')) and valid_origin
        and (not live or (parsed.scheme == 'https' and os.getenv('COOKIE_SECURE', '').lower() == 'true')))
    return {'enabled': enabled, 'mode': ('live' if live else 'test') if enabled else 'disabled',
            'currency': 'usd', 'packs': [{'id': key, **value} for key, value in PACKS.items()]}


class BillingStore:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS payment_orders (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                idempotency_key TEXT NOT NULL, pack_id TEXT NOT NULL, amount_cents INTEGER NOT NULL,
                credit_micro_usd INTEGER NOT NULL, livemode INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', session_id TEXT UNIQUE, checkout_url TEXT,
                created_at TEXT NOT NULL, created_epoch INTEGER NOT NULL, paid_at TEXT,
                UNIQUE(user_id,idempotency_key));
            CREATE INDEX IF NOT EXISTS payments_user ON payment_orders(user_id,created_at);
            ''')

    def create(self, user_id, pack_id, key, live):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM payment_orders WHERE user_id=? AND idempotency_key=?', (user_id, key)).fetchone()
            if row:
                if row['pack_id'] != pack_id or row['livemode'] != int(live):
                    raise HTTPException(409, 'Idempotency key already used for another checkout')
                return dict(row)
            order_id = uuid.uuid4().hex
            pack = PACKS[pack_id]
            db.execute('INSERT INTO payment_orders (id,user_id,idempotency_key,pack_id,amount_cents,credit_micro_usd,'
                'livemode,created_at,created_epoch) VALUES (?,?,?,?,?,?,?,?,?)',
                (order_id, user_id, key, pack_id, pack['amount_cents'], pack['credit_micro_usd'], int(live), now(), int(time.time())))
            return dict(db.execute('SELECT * FROM payment_orders WHERE id=?', (order_id,)).fetchone())

    def bind(self, order_id, session_id, url):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT session_id FROM payment_orders WHERE id=?', (order_id,)).fetchone()
            if row['session_id'] and row['session_id'] != session_id:
                raise HTTPException(409, 'Checkout session does not match this order')
            db.execute('UPDATE payment_orders SET session_id=?,checkout_url=? WHERE id=?', (session_id, url, order_id))

    def orders(self, user_id, order_id=None):
        with self.store.connection() as db:
            query = ('SELECT id,amount_cents,credit_micro_usd,status,created_at,paid_at,livemode '
                     'FROM payment_orders WHERE user_id=?')
            if order_id:
                row = db.execute(query + ' AND id=?', (user_id, order_id)).fetchone()
                if not row:
                    raise HTTPException(404, 'Order not found')
                return dict(row)
            return [dict(row) for row in db.execute(query + ' ORDER BY created_at DESC,rowid DESC LIMIT 50', (user_id,))]

    def fulfill(self, session, event_type=None):
        metadata = session.get('metadata') or {}
        if metadata.get('application') != 'unbound':
            return False
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM payment_orders WHERE id=?', (metadata.get('order_id'),)).fetchone()
            if not row:
                raise HTTPException(409, 'Payment order not found')
            if (session.get('client_reference_id') != row['user_id'] or session.get('mode') != 'payment'
                or session.get('currency') != 'usd' or type(session.get('amount_total')) is not int
                or session['amount_total'] != row['amount_cents']
                or type(session.get('livemode')) is not bool or int(session['livemode']) != row['livemode']
                or not isinstance(session.get('id'), str) or not session['id'].startswith('cs_')
                or (row['session_id'] and row['session_id'] != session['id'])):
                raise HTTPException(409, 'Payment details do not match the stored order')
            if row['status'] == 'paid':
                return False
            if event_type == 'checkout.session.async_payment_failed':
                db.execute("UPDATE payment_orders SET status='failed' WHERE id=?", (row['id'],))
                return False
            if session.get('payment_status') != 'paid':
                status = 'expired' if session.get('status') == 'expired' else 'pending'
                db.execute('UPDATE payment_orders SET status=? WHERE id=?', (status, row['id']))
                return False
            balance_column = 'balance' if row['livemode'] else 'test_balance'
            db.execute(f'UPDATE wallets SET {balance_column}={balance_column}+? WHERE user_id=?',
                       (row['credit_micro_usd'], row['user_id']))
            db.execute('INSERT INTO ledger VALUES (?,?,?,?,?,?)', (uuid.uuid4().hex, row['user_id'],
                row['credit_micro_usd'], 'test_payment' if not row['livemode'] else 'payment', 'stripe:' + row['id'], now()))
            db.execute("UPDATE payment_orders SET status='paid',session_id=?,paid_at=? WHERE id=?",
                       (session['id'], now(), row['id']))
            return True


class Checkout(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pack_id: str


@router.get('/config')
async def config():
    return settings()


async def stripe_request(request, method, path, **kwargs):
    try:
        result = await request.app.state.client.request(method, 'https://api.stripe.com/v1/' + path,
            headers={'Authorization': 'Bearer ' + os.environ['STRIPE_SECRET_KEY'], **kwargs.pop('headers', {})},
            timeout=20, **kwargs)
        result.raise_for_status()
        data = result.json()
        if not isinstance(data, dict):
            raise ValueError('Invalid Stripe response')
        return data
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'Payment service is temporarily unavailable; retry with the same request key')


@router.post('/checkout')
async def checkout(body: Checkout, request: Request, user=Depends(user_session)):
    conf = settings()
    if not conf['enabled']:
        raise HTTPException(503, 'Checkout is not configured for this deployment')
    if body.pack_id not in PACKS:
        raise HTTPException(400, 'Unknown credit pack')
    key = request.headers.get('idempotency-key', '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', key):
        raise HTTPException(400, 'Supply an Idempotency-Key of 8–128 simple characters')
    request.app.state.limiter.check(('checkout', user['id']), 10)
    order = request.app.state.billing.create(user['id'], body.pack_id, key, conf['mode'] == 'live')
    if order['status'] != 'pending':
        raise HTTPException(409, 'This order is already paid or expired; refresh your balance')
    if order['checkout_url']:
        return {'order_id': order['id'], 'url': order['checkout_url']}
    if int(time.time()) - order['created_epoch'] > 23 * 3600:
        raise HTTPException(409, 'Checkout creation is too old to retry safely; create a new checkout')
    origin = os.environ['PUBLIC_ORIGIN'].rstrip('/')
    session = await stripe_request(request, 'POST', 'checkout/sessions',
        headers={'Idempotency-Key': 'unbound-' + order['id']}, data={
            'mode': 'payment', 'payment_method_types[0]': 'card',
            'client_reference_id': user['id'], 'customer_email': user['email'],
            'metadata[application]': 'unbound', 'metadata[order_id]': order['id'],
            'line_items[0][price_data][currency]': 'usd',
            'line_items[0][price_data][unit_amount]': str(order['amount_cents']),
            'line_items[0][price_data][product_data][name]': 'UNBOUND API credits',
            'line_items[0][quantity]': '1',
            'success_url': origin + '/?checkout=' + order['id'] + '#workspace',
            'cancel_url': origin + '/?checkout=' + order['id'] + '&canceled=1#workspace',
        })
    url = session.get('url', '')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'checkout.stripe.com' or parsed.username or not session.get('id', '').startswith('cs_'):
        raise HTTPException(502, 'Payment service returned an invalid checkout session')
    request.app.state.billing.bind(order['id'], session['id'], url)
    return {'order_id': order['id'], 'url': url}


@router.get('/orders')
async def orders(request: Request, user=Depends(user_session)):
    return {'orders': request.app.state.billing.orders(user['id'])}


@router.get('/orders/{order_id}')
async def order(order_id: str, request: Request, user=Depends(user_session)):
    return {'order': request.app.state.billing.orders(user['id'], order_id)}


@router.post('/webhook')
async def webhook(request: Request):
    secret = os.getenv('STRIPE_WEBHOOK_SECRET', '')
    if not secret or not os.getenv('STRIPE_SECRET_KEY'):
        raise HTTPException(503, 'Payment webhook is not configured')
    raw = await request.body()
    try:
        event = stripe.Webhook.construct_event(raw, request.headers.get('stripe-signature', ''), secret, tolerance=300)
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(400, 'Invalid webhook signature')
    if event['type'] not in ('checkout.session.completed', 'checkout.session.async_payment_succeeded',
                              'checkout.session.expired', 'checkout.session.async_payment_failed'):
        return {'received': True, 'credited': False}
    session_id = event.get('data', {}).get('object', {}).get('id', '')
    if not isinstance(session_id, str) or not re.fullmatch(r'cs_[A-Za-z0-9_]+', session_id):
        raise HTTPException(400, 'Invalid checkout session')
    # Never grant credits from the redirect URL or client-supplied amounts. Fetch
    # canonical payment details even when a signed event is duplicated/out of order.
    session = await stripe_request(request, 'GET', 'checkout/sessions/' + session_id)
    credited = request.app.state.billing.fulfill(session, event['type'])
    return {'received': True, 'credited': credited}
