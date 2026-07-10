import os
import pymysql
from contextlib import contextmanager

def _env(*names, default=None):
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value
    return default

def _db_config():
    return {
        "host": _env("LIBRENMS_DB_HOST", "MYSQL_HOST", "DB_HOST", default="db"),
        "port": int(_env("LIBRENMS_DB_PORT", "MYSQL_PORT", "DB_PORT", default="3306")),
        "user": _env("LIBRENMS_DB_USER", "MYSQL_USER", "DB_USER", default="librenms"),
        "password": _env("LIBRENMS_DB_PASSWORD", "MYSQL_PASSWORD", "DB_PASSWORD", default=""),
        "database": _env("LIBRENMS_DB_NAME", "MYSQL_DATABASE", "DB_NAME", default="librenms"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
    }

@contextmanager
def db_conn():
    conn = pymysql.connect(**_db_config())
    try:
        yield conn
    finally:
        conn.close()

def fetch_all(sql, params=None):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return list(cur.fetchall())

def fetch_one(sql, params=None):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()
