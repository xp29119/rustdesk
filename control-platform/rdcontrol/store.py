import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
 enabled INTEGER NOT NULL DEFAULT 1, created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 name TEXT NOT NULL, platform TEXT NOT NULL, public_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','revoked')),
 created INTEGER NOT NULL, last_seen INTEGER, last_ip TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 device_id TEXT NOT NULL REFERENCES devices(id), created INTEGER NOT NULL,
 expires INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
 hash TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS admin_sessions (
 hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 csrf TEXT NOT NULL, expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS proofs (
 jti TEXT PRIMARY KEY, expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, time INTEGER NOT NULL,
 actor TEXT, device_id TEXT, event TEXT NOT NULL, ip TEXT, details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connections (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, device_id TEXT NOT NULL,
 kind TEXT NOT NULL, started INTEGER NOT NULL, ended INTEGER,
 bytes_up INTEGER NOT NULL DEFAULT 0, bytes_down INTEGER NOT NULL DEFAULT 0,
 outcome TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_time ON audit(time);
CREATE INDEX IF NOT EXISTS sessions_device ON sessions(device_id);
"""


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        # A crash does not prove when a remote desktop session ended.
        self.db.execute(
            "UPDATE connections SET outcome='interrupted_unknown' WHERE ended IS NULL"
        )
        self.db.commit()

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def write(self, sql, args=()):
        with self.db:
            return self.db.execute(sql, args)
