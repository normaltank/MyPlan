"""Name-only staff sign-in and revocable browser sessions.

Staff type their name once per browser; it attributes tasks and daily counts.
There are no passwords or roles in use: everyone signed in has the same rights.
The task database is shared with the queue, but no session credential is ever
stored there in plaintext. This module deliberately has no web dependencies.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


SESSION_SECONDS = 30 * 24 * 60 * 60
NAME_MAX_LENGTH = 40


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
    def _account(row: sqlite3.Row) -> Account:
        return Account(row['id'], row['username'], row['display_name'], row['role'], bool(row['enabled']))

    def sign_in(self, display_name: str) -> Account:
        """The person's typed name is their identity; the same name returns the same account.

        Names only attribute work and daily counts. They are not a secret, so
        anyone on the LAN can type any name; there is no password to check.
        """
        display_name = ' '.join(display_name.split())
        if not display_name or len(display_name) > NAME_MAX_LENGTH:
            raise ValueError(f'请填写不超过 {NAME_MAX_LENGTH} 字的姓名')
        username = 'name-' + hashlib.sha256(display_name.encode('utf-8')).hexdigest()[:16]
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM users WHERE display_name=? AND enabled=1 '
                              'ORDER BY id LIMIT 1', (display_name,)).fetchone()
            if row is None:
                # The password column predates name sign-in; '!' matches no password.
                cur = con.execute("INSERT INTO users(username,display_name,role,password_hash,created_at) "
                                  "VALUES (?,?,'employee','!',?)", (username, display_name, time.time()))
                row = con.execute('SELECT * FROM users WHERE id=?', (cur.lastrowid,)).fetchone()
        return self._account(row)

    def get_user(self, user_id: int) -> Account | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._account(row) if row else None

    def list_users(self) -> list[Account]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [self._account(row) for row in rows]

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
