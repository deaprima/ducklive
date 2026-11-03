"""SQLite: tabel goals, events, dan key-value (settings, state timer). Satu koneksi, dijaga lock."""

import json
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target INTEGER NOT NULL,
  text TEXT NOT NULL,
  manual_done INTEGER,
  reached_at REAL,
  bonus_minutes REAL NOT NULL DEFAULT 0,
  bonus_applied INTEGER NOT NULL DEFAULT 0,
  scene TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  ext_id TEXT UNIQUE,
  kind TEXT NOT NULL,
  author TEXT NOT NULL DEFAULT '',
  units INTEGER NOT NULL DEFAULT 0,
  counter_delta INTEGER NOT NULL DEFAULT 0,
  seconds_added REAL NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  undone INTEGER NOT NULL DEFAULT 0
);
"""


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def execute(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def kv_get(self, key, default=None):
        rows = self.query("SELECT v FROM kv WHERE k=?", (key,))
        return json.loads(rows[0]["v"]) if rows else default

    def kv_set(self, key, value):
        self.execute(
            "INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, json.dumps(value)),
        )
