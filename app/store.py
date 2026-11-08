"""SQLite accounts and integer-microdollar wallet. One gateway process per DB."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from fastapi import HTTPException


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()
    return salt + ':' + value


def cost(input_tokens, output_tokens, input_rate, output_rate):
    # Round once per request, never use floating-point currency.
    return (input_tokens * input_rate + output_tokens * output_rate + 999_999) // 1_000_000


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
                prefix TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
                last_used_at TEXT, revoked_at TEXT);
            CREATE TABLE IF NOT EXISTS wallets (
                user_id TEXT PRIMARY KEY REFERENCES users(id), balance INTEGER NOT NULL CHECK(balance >= 0),
                held INTEGER NOT NULL DEFAULT 0 CHECK(held >= 0 AND held <= balance));
            CREATE TABLE IF NOT EXISTS usage (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), key_id TEXT,
                model TEXT NOT NULL, status TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0, cost_micro_usd INTEGER NOT NULL DEFAULT 0,
                reserved INTEGER NOT NULL, input_rate INTEGER NOT NULL, output_rate INTEGER NOT NULL,
                input_limit INTEGER NOT NULL, output_limit INTEGER NOT NULL, mock INTEGER NOT NULL,
                created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), amount_micro_usd INTEGER NOT NULL,
                kind TEXT NOT NULL, reference TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS usage_user_date ON usage(user_id, created_at);
            CREATE INDEX IF NOT EXISTS keys_user ON api_keys(user_id);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(usage)')}
            if 'provider' not in columns:
                db.execute("ALTER TABLE usage ADD COLUMN provider TEXT NOT NULL DEFAULT 'self-hosted'")
            wallet_columns = {row[1] for row in db.execute('PRAGMA table_info(wallets)')}
            if 'test_balance' not in wallet_columns:
                db.execute('ALTER TABLE wallets ADD COLUMN test_balance INTEGER NOT NULL DEFAULT 0 CHECK(test_balance >= 0)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def register(self, name, email, hashed_password, trial_credit):
        user_id = uuid.uuid4().hex
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('INSERT INTO users VALUES (?,?,?,?,?)', (user_id, name, email, hashed_password, now()))
            except sqlite3.IntegrityError:
                raise HTTPException(409, 'An account with this email already exists')
            db.execute('INSERT INTO wallets (user_id,balance,held) VALUES (?,?,0)', (user_id, trial_credit))
            db.execute('INSERT INTO ledger VALUES (?,?,?,?,?,?)',
                       (uuid.uuid4().hex, user_id, trial_credit, 'trial', 'trial:' + user_id, now()))
        return {'id': user_id, 'name': name, 'email': email}

    def user_by_email(self, email):
        with self.connection() as db:
            row = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            return dict(row) if row else None

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE expires_at < ?', (int(time.time()),))
            db.execute('INSERT INTO sessions VALUES (?,?,?)', (digest(token), user_id, int(time.time()) + 604800))
        return token

    def session_user(self, token):
        with self.connection() as db:
            row = db.execute('SELECT u.id,u.name,u.email FROM sessions s JOIN users u ON u.id=s.user_id '
                             'WHERE s.token_hash=? AND s.expires_at>?', (digest(token), int(time.time()))).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))

    def wallet(self, user_id):
        with self.connection() as db:
            row = db.execute('SELECT balance,held,test_balance FROM wallets WHERE user_id=?', (user_id,)).fetchone()
            return {'balance_micro_usd': row['balance'], 'held_micro_usd': row['held'],
                    'available_micro_usd': row['balance'] - row['held'],
                    'test_balance_micro_usd': row['test_balance']}

    def keys(self, user_id):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT id,name,prefix,created_at,last_used_at,revoked_at '
                'FROM api_keys WHERE user_id=? ORDER BY created_at DESC', (user_id,))]

    def create_key(self, user_id, name):
        key = 'ub_' + secrets.token_urlsafe(32)
        record = {'id': uuid.uuid4().hex, 'name': name, 'prefix': key[:11], 'created_at': now(),
                  'last_used_at': None, 'revoked_at': None}
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM api_keys WHERE user_id=? AND revoked_at IS NULL',
                          (user_id,)).fetchone()[0] >= 20:
                raise HTTPException(409, 'Limit of 20 active keys reached; revoke a key first')
            db.execute('INSERT INTO api_keys VALUES (?,?,?,?,?,?,NULL,NULL)',
                (record['id'], user_id, name, record['prefix'], digest(key), record['created_at']))
        return {'key': key, 'record': record}

    def key_user(self, token):
        with self.connection() as db:
            row = db.execute('SELECT user_id,id FROM api_keys WHERE token_hash=? AND revoked_at IS NULL',
                             (digest(token),)).fetchone()
            if row:
                db.execute('UPDATE api_keys SET last_used_at=? WHERE id=?', (now(), row['id']))
                return dict(row)
        return None

    def revoke_key(self, user_id, key_id):
        with self.connection() as db:
            result = db.execute('UPDATE api_keys SET revoked_at=COALESCE(revoked_at,?) WHERE id=? AND user_id=?',
                                (now(), key_id, user_id))
            if not result.rowcount:
                raise HTTPException(404, 'Key not found')

    def reserve(self, user_id, key_id, model, input_limit, output_limit, input_rate, output_rate, mock, provider='self-hosted'):
        amount = 0 if mock else cost(input_limit, output_limit, input_rate, output_rate)
        request_id = uuid.uuid4().hex
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            updated = db.execute('UPDATE wallets SET held=held+? WHERE user_id=? AND balance-held>=?',
                                 (amount, user_id, amount))
            if not updated.rowcount:
                raise HTTPException(402, 'Insufficient credits for this request token limit')
            db.execute('INSERT INTO usage (id,user_id,key_id,model,status,reserved,input_rate,output_rate,'
                       'input_limit,output_limit,mock,created_at,provider) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (request_id, user_id, key_id, model, 'pending', amount, input_rate, output_rate,
                        input_limit, output_limit, int(mock), now(), provider))
        return request_id

    def settle(self, request_id, usage=None, status='failed'):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM usage WHERE id=?', (request_id,)).fetchone()
            if not row or row['status'] != 'pending':
                return  # Idempotent: stream cleanup and background cleanup can both run.
            input_tokens = output_tokens = charge = 0
            if usage is not None:
                inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
                if (type(inp) is int and type(out) is int and 0 <= inp <= row['input_limit']
                        and 0 <= out <= row['output_limit']):
                    input_tokens, output_tokens = inp, out
                    charge = 0 if row['mock'] else cost(inp, out, row['input_rate'], row['output_rate'])
                    status = 'mock' if row['mock'] else 'completed'
                else:
                    status = 'invalid_usage'
            db.execute('UPDATE wallets SET balance=balance-?,held=held-? WHERE user_id=?',
                       (charge, row['reserved'], row['user_id']))
            db.execute('UPDATE usage SET status=?,input_tokens=?,output_tokens=?,cost_micro_usd=? WHERE id=?',
                       (status, input_tokens, output_tokens, charge, request_id))
            if charge:
                db.execute('INSERT INTO ledger VALUES (?,?,?,?,?,?)',
                    (uuid.uuid4().hex, row['user_id'], -charge, 'inference', 'usage:' + request_id, now()))

    def recover(self):
        # Single process only: no request can survive this process restart.
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE usage SET status='interrupted' WHERE status='pending'")
            db.execute('UPDATE wallets SET held=0')

    def usage(self, user_id):
        with self.connection() as db:
            summary = dict(db.execute('SELECT count(*) AS requests,COALESCE(sum(input_tokens),0) AS input_tokens,'
                'COALESCE(sum(output_tokens),0) AS output_tokens,COALESCE(sum(cost_micro_usd),0) AS cost_micro_usd '
                'FROM usage WHERE user_id=?', (user_id,)).fetchone())
            daily = [dict(r) for r in db.execute('SELECT substr(created_at,1,10) AS date,count(*) AS requests,'
                'sum(input_tokens) AS input_tokens,sum(output_tokens) AS output_tokens,sum(cost_micro_usd) AS cost_micro_usd '
                "FROM usage WHERE user_id=? AND created_at>=date('now','-29 days') GROUP BY date ORDER BY date", (user_id,))]
            recent = [dict(r) for r in db.execute('SELECT id,model,provider,status,input_tokens,output_tokens,cost_micro_usd,created_at '
                'FROM usage WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 50', (user_id,))]
            return {'summary': summary, 'daily': daily, 'recent': recent}

    def ledger(self, user_id):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT id,amount_micro_usd,kind,reference,created_at FROM ledger '
                'WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 100', (user_id,))]

    def grant(self, user_id, amount, reference):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT user_id,amount_micro_usd FROM ledger WHERE reference=?',
                               ('grant:' + reference,)).fetchone()
            if prior:
                if prior['user_id'] != user_id or prior['amount_micro_usd'] != amount:
                    raise HTTPException(409, 'Idempotency key already used with different grant details')
                return False
            if not db.execute('SELECT id FROM users WHERE id=?', (user_id,)).fetchone():
                raise HTTPException(404, 'User not found')
            db.execute('UPDATE wallets SET balance=balance+? WHERE user_id=?', (amount, user_id))
            db.execute('INSERT INTO ledger VALUES (?,?,?,?,?,?)',
                (uuid.uuid4().hex, user_id, amount, 'admin_credit', 'grant:' + reference, now()))
            return True
