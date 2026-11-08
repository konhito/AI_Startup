import asyncio
from collections import defaultdict, deque
import os
import re
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .store import password_hash

router = APIRouter(prefix='/api')
COOKIE = 'unbound_session'


class RateLimiter:
    def __init__(self):
        self.buckets = defaultdict(deque)
        self.last_clean = 0

    def check(self, key, limit, seconds=60):
        current = time.monotonic()
        # Also evict inactive identities so unauthenticated traffic cannot grow memory forever.
        if current - self.last_clean > 60:
            self.buckets = defaultdict(deque, {k: v for k, v in self.buckets.items()
                if v and current - v[-1] < 3600})
            self.last_clean = current
        bucket = self.buckets[key]
        while bucket and current - bucket[0] >= seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(429, 'Too many requests; try again shortly', headers={'Retry-After': str(seconds)})
        bucket.append(current)


def same_origin(request: Request):
    origin = request.headers.get('origin')
    expected = os.getenv('PUBLIC_ORIGIN', str(request.base_url).rstrip('/')).rstrip('/')
    if (origin and origin.rstrip('/') != expected) or request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Cross-site requests are not allowed')


def user_session(request: Request):
    same_origin(request)
    user = request.app.state.store.session_user(request.cookies.get(COOKIE, ''))
    if not user:
        raise HTTPException(401, 'Sign in to continue')
    return user


def pricing():
    hosted_mode = ('arliai' if os.getenv('ARLIAI_API_KEY') else 'openrouter'
                   if os.getenv('OPENROUTER_API_KEY') else os.getenv('PROVIDER', 'mock'))
    return {
        'input_per_million_micro_usd': int(os.getenv('INPUT_PRICE_MICRO_USD', '200000')),
        'output_per_million_micro_usd': int(os.getenv('OUTPUT_PRICE_MICRO_USD', '800000')),
        'trial_credit_micro_usd': int(os.getenv('TRIAL_CREDIT_MICRO_USD', '0')),
        'model': os.getenv('MODEL_NAME', 'glm'),
        'inference_mode': hosted_mode,
        'pricing_status': 'preview',
        'default_model': 'auto',
        'arliai_enabled': bool(os.getenv('ARLIAI_API_KEY')),
        'arliai_model': os.getenv('ARLIAI_MODEL', 'Qwen3.5-27B-Derestricted'),
        'openrouter_model': os.getenv('OPENROUTER_MODEL', 'deepseek/deepseek-v4.1-flash-1017'),
        'router_enabled': bool(os.getenv('ARLIAI_API_KEY') or os.getenv('OPENROUTER_API_KEY')),
        'fallback_enabled': os.getenv('ROUTER_FALLBACK', 'true').lower() == 'true' and os.getenv('PROVIDER') == 'gcp',
        'openrouter_input_per_million_micro_usd': int(os.getenv('OPENROUTER_INPUT_PRICE_MICRO_USD', '500000')),
        'openrouter_output_per_million_micro_usd': int(os.getenv('OPENROUTER_OUTPUT_PRICE_MICRO_USD', '1000000')),
        'glm53_model': os.getenv('OPENROUTER_GLM53_MODEL', 'z-ai/glm-5.3'),
        'glm53_input_per_million_micro_usd': int(os.getenv('OPENROUTER_GLM53_INPUT_PRICE_MICRO_USD', '1500000')),
        'glm53_output_per_million_micro_usd': int(os.getenv('OPENROUTER_GLM53_OUTPUT_PRICE_MICRO_USD', '5000000')),
    }


def set_session(response, token):
    response.set_cookie(COOKIE, token, max_age=604800, httponly=True,
        secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true', samesite='strict', path='/')


class Registration(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CreditGrant(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    amount_micro_usd: int = Field(strict=True, gt=0, le=1_000_000_000)


def auth_limit(request, action, email):
    same_origin(request)
    ip = request.client.host if request.client else 'unknown'
    request.app.state.limiter.check(('auth-ip', ip), 30, 60)
    request.app.state.limiter.check((action, email.casefold()), 10, 300)


@router.get('/pricing')
async def get_pricing():
    return pricing()


@router.post('/auth/register', status_code=201)
async def register(body: Registration, request: Request, response: Response):
    email = body.email.strip().casefold()
    auth_limit(request, 'register', email)
    name = body.name.strip()
    if not name or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise HTTPException(400, 'Enter a name and valid email address')
    hashed = await asyncio.to_thread(password_hash, body.password)
    user = request.app.state.store.register(name, email, hashed, pricing()['trial_credit_micro_usd'])
    set_session(response, request.app.state.store.create_session(user['id']))
    return {'user': user}


@router.post('/auth/login')
async def login(body: Login, request: Request, response: Response):
    email = body.email.strip().casefold()
    auth_limit(request, 'login', email)
    user = request.app.state.store.user_by_email(email)
    stored = user['password_hash'] if user else '00' * 16 + ':' + '00' * 32
    calculated = await asyncio.to_thread(password_hash, body.password, stored.split(':')[0])
    if not user or not secrets.compare_digest(calculated, stored):
        raise HTTPException(401, 'Invalid email or password')
    set_session(response, request.app.state.store.create_session(user['id']))
    return {'user': {key: user[key] for key in ('id', 'name', 'email')}}


@router.post('/auth/logout', status_code=204)
async def logout(request: Request, response: Response):
    same_origin(request)
    request.app.state.store.logout(request.cookies.get(COOKIE, ''))
    response.delete_cookie(COOKIE, path='/')


@router.get('/me')
async def me(request: Request, user=Depends(user_session)):
    mode = ('arliai' if os.getenv('ARLIAI_API_KEY') else 'openrouter' if os.getenv('OPENROUTER_API_KEY')
            else 'mock' if request.app.state.mock else 'gcp')
    return {'user': user, 'wallet': request.app.state.store.wallet(user['id']),
            'pricing': pricing(), 'inference_mode': mode}


@router.get('/keys')
async def keys(request: Request, user=Depends(user_session)):
    return {'keys': request.app.state.store.keys(user['id'])}


@router.post('/keys', status_code=201)
async def create_key(body: KeyCreate, request: Request, user=Depends(user_session)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, 'Enter a key name')
    request.app.state.limiter.check(('key-create', user['id']), 20)
    return request.app.state.store.create_key(user['id'], name)


@router.delete('/keys/{key_id}', status_code=204)
async def revoke(key_id: str, request: Request, user=Depends(user_session)):
    request.app.state.store.revoke_key(user['id'], key_id)


@router.get('/usage')
async def usage(request: Request, user=Depends(user_session)):
    return request.app.state.store.usage(user['id'])


@router.get('/ledger')
async def ledger(request: Request, user=Depends(user_session)):
    return {'entries': request.app.state.store.ledger(user['id'])}


@router.post('/admin/credits')
async def grant(body: CreditGrant, request: Request):
    admin = os.getenv('ADMIN_API_KEY', '')
    token = request.headers.get('authorization', '').removeprefix('Bearer ')
    if len(admin) < 32 or not secrets.compare_digest(admin.encode(), token.encode()):
        raise HTTPException(403, 'Administrator authentication required')
    reference = request.headers.get('idempotency-key', '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', reference):
        raise HTTPException(400, 'Supply an Idempotency-Key of 8–128 letters, digits, dots, colons, underscores or hyphens')
    applied = request.app.state.store.grant(body.user_id, body.amount_micro_usd, reference)
    return {'applied': applied, 'wallet': request.app.state.store.wallet(body.user_id)}
