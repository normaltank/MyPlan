"""Local employee accounts and revocable browser sessions.

The task database is shared with the queue, but no session credential is ever
stored there in plaintext. This module deliberately has no web dependencies.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


PASSWORD_ITERATIONS = 600_000
SESSION_SECONDS = 12 * 60 * 60
_USERNAME = re.compile(r"^[a-z][a-z0-9_.-]{2,31}$")


@dataclass(frozen=True)
class Account:
    id: int
    username: str
    display_name: str
    role: str
    enabled: bool = True


class AccountStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','employee')),
                password_hash TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS browser_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                csrf_hash TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL)""")
            con.execute("CREATE INDEX IF NOT EXISTS idx_browser_sessions_user ON browser_sessions(user_id)")
            con.execute("""CREATE TABLE IF NOT EXISTS login_failures (
                username TEXT PRIMARY KEY,
                failed_count INTEGER NOT NULL,
                window_start REAL NOT NULL,
                locked_until REAL NOT NULL DEFAULT 0)""")

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        try:
            with con:
                yield con
        finally:
            con.close()

    @staticmethod
    def _password_hash(password: str, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PASSWORD_ITERATIONS)
        return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}'

    @staticmethod
    @lru_cache(maxsize=1)
    def _dummy_hash() -> str:
        return AccountStore._password_hash('dummy password', b'\0' * 16)

    @staticmethod
    def _verify_password(password: str, encoded: str) -> bool:
        try:
            name, iterations, salt, expected = encoded.split('$')
            if name != 'pbkdf2_sha256' or int(iterations) < PASSWORD_ITERATIONS:
                return False
            actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                         bytes.fromhex(salt), int(iterations))
            return hmac.compare_digest(actual, bytes.fromhex(expected))
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _account(row: sqlite3.Row) -> Account:
        return Account(row['id'], row['username'], row['display_name'], row['role'], bool(row['enabled']))

    def create_user(self, username: str, display_name: str, password: str,
                    role: str = 'employee') -> Account:
        username = username.strip().lower()
        display_name = display_name.strip()
        if not _USERNAME.fullmatch(username):
            raise ValueError('用户名须为 3–32 位小写字母、数字、点、下划线或连字符，且以字母开头')
        if not display_name or len(display_name) > 80:
            raise ValueError('请填写不超过 80 字的显示名称')
        if len(password) < 12 or len(password) > 1024:
            raise ValueError('密码长度须为 12–1024 字符')
        if role not in {'admin', 'employee'}:
            raise ValueError('账号角色无效')
        encoded = self._password_hash(password)
        with self._connect() as con:
            cur = con.execute("INSERT INTO users(username,display_name,role,password_hash,created_at) "
                              "VALUES (?,?,?,?,?)", (username, display_name, role, encoded, time.time()))
            return Account(cur.lastrowid, username, display_name, role)

    def get_user(self, user_id: int) -> Account | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._account(row) if row else None

    def list_users(self) -> list[Account]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [self._account(row) for row in rows]

    def change_password(self, user_id: int, old_password: str, new_password: str) -> bool:
        if len(new_password) < 12 or len(new_password) > 1024:
            raise ValueError('新密码长度须为 12–1024 字符')
        with self._connect() as con:
            row = con.execute('SELECT password_hash,enabled FROM users WHERE id=?', (user_id,)).fetchone()
            if not row or not row['enabled'] or not self._verify_password(old_password, row['password_hash']):
                return False
            con.execute('UPDATE users SET password_hash=? WHERE id=?',
                        (self._password_hash(new_password), user_id))
            con.execute('DELETE FROM browser_sessions WHERE user_id=?', (user_id,))
        return True

    def has_admin(self) -> bool:
        with self._connect() as con:
            return con.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone() is not None

    def bootstrap_admin(self, username: str, display_name: str, password: str) -> Account:
        """Create exactly one first admin through a local-only web route."""
        username = username.strip().lower()
        display_name = display_name.strip()
        if not _USERNAME.fullmatch(username):
            raise ValueError('用户名须为 3–32 位小写字母、数字、点、下划线或连字符，且以字母开头')
        if not display_name or len(display_name) > 80:
            raise ValueError('请填写不超过 80 字的显示名称')
        if len(password) < 12 or len(password) > 1024:
            raise ValueError('密码长度须为 12–1024 字符')
        encoded = self._password_hash(password)
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
                raise ValueError('管理员已存在')
            cur = con.execute("INSERT INTO users(username,display_name,role,password_hash,created_at) "
                              "VALUES (?,?,'admin',?,?)", (username, display_name, encoded, time.time()))
            return Account(cur.lastrowid, username, display_name, 'admin')

    def authenticate(self, username: str, password: str) -> Account | None:
        username = username.strip().lower()
        now = time.time()
        with self._connect() as con:
            fail = con.execute("SELECT * FROM login_failures WHERE username=?", (username,)).fetchone()
            row = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if fail and fail['locked_until'] > now:
            return None
        # Equal-cost check for missing accounts prevents a trivial timing oracle.
        encoded = row['password_hash'] if row else self._dummy_hash()
        valid = self._verify_password(password, encoded)
        if not row or not valid or not row['enabled']:
            count = 1 if not fail or now - fail['window_start'] > 900 else fail['failed_count'] + 1
            start = now if count == 1 else fail['window_start']
            with self._connect() as con:
                con.execute("""INSERT INTO login_failures(username,failed_count,window_start,locked_until)
                    VALUES (?,?,?,?) ON CONFLICT(username) DO UPDATE SET
                    failed_count=excluded.failed_count,window_start=excluded.window_start,
                    locked_until=excluded.locked_until""",
                    (username, count, start, now + 900 if count >= 5 else 0))
            return None
        with self._connect() as con:
            con.execute("DELETE FROM login_failures WHERE username=?", (username,))
        return self._account(row)

    @staticmethod
    def _hash_token(value: str) -> str:
        return hashlib.sha256(value.encode('ascii')).hexdigest()

    def create_session(self, user_id: int) -> tuple[str, str]:
        user = self.get_user(user_id)
        if user is None or not user.enabled:
            raise ValueError('账号不可用')
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = time.time()
        with self._connect() as con:
            con.execute("INSERT INTO browser_sessions VALUES (?,?,?,?,?)",
                        (self._hash_token(token), user_id, self._hash_token(csrf),
                         now + SESSION_SECONDS, now))
        return token, csrf

    def resolve_session(self, token: str | None) -> Account | None:
        if not token:
            return None
        with self._connect() as con:
            row = con.execute("""SELECT u.* FROM browser_sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1""",
                (self._hash_token(token), time.time())).fetchone()
        return self._account(row) if row else None

    def verify_csrf(self, token: str | None, csrf: str | None) -> bool:
        if not token or not csrf or self.resolve_session(token) is None:
            return False
        with self._connect() as con:
            row = con.execute("SELECT csrf_hash FROM browser_sessions WHERE token_hash=?",
                              (self._hash_token(token),)).fetchone()
        return bool(row and hmac.compare_digest(row['csrf_hash'], self._hash_token(csrf)))

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as con:
            con.execute("DELETE FROM browser_sessions WHERE token_hash=?", (self._hash_token(token),))
