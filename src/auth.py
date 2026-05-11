"""
Authentication and authorization for the SIMO Daily Check dashboard.

Users are stored in the same SQLite database as dynamic modules.
Passwords are hashed with PBKDF2-HMAC-SHA256 (no extra dependency).
Two roles are supported:
  - admin: full access to all features.
  - user:  can only create/view/edit/delete/trigger their own dynamic modules.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from dataclasses import dataclass
from functools import wraps
from typing import Optional

from flask import session, redirect, url_for, request, abort

from src.config_manager import get

logger = logging.getLogger("dailycheck.auth")

ROLES = {"admin", "user"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class User:
    id: int
    username: str
    role: str          # "admin" or "user"
    display_name: str = ""
    enabled: bool = True


# ---------------------------------------------------------------------------
# Low-level DB helpers
# ---------------------------------------------------------------------------

def _db_path() -> str:
    return get("general.db_path", "data/dailycheck.db")


def _conn():
    return sqlite3.connect(_db_path())


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return (hex_hash, hex_salt) via PBKDF2-HMAC-SHA256."""
    if salt is None:
        salt = os.urandom(16).hex()
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=260_000,
    ).hex()
    return hashed, salt


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------

def init_auth_db():
    """Create the users table and seed the default admin account."""
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                display_name  TEXT    NOT NULL DEFAULT '',
                role          TEXT    NOT NULL DEFAULT 'user',
                password_hash TEXT    NOT NULL,
                password_salt TEXT    NOT NULL,
                enabled       INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Add created_by to dynamic_modules if the column does not yet exist.
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(dynamic_modules)").fetchall()
        }
        if "created_by" not in columns:
            conn.execute(
                "ALTER TABLE dynamic_modules ADD COLUMN created_by TEXT NOT NULL DEFAULT 'admin'"
            )
            logger.info("Added created_by column to dynamic_modules.")

    _ensure_default_admin()


def _ensure_default_admin():
    """Create the default admin user if no users exist yet."""
    admin_username = get("auth.admin_username", "admin")
    admin_password = get("auth.admin_password", "admin123")
    with _conn() as conn:
        exists = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (admin_username,),
        ).fetchone()
        if not exists:
            hashed, salt = _hash_password(admin_password)
            conn.execute(
                """INSERT INTO users
                   (username, display_name, role, password_hash, password_salt)
                   VALUES (?, ?, ?, ?, ?)""",
                (admin_username, "Administrator", "admin", hashed, salt),
            )
            logger.info("Default admin user '%s' created.", admin_username)


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def _hydrate_user(row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"] or row["username"],
        role=row["role"],
        enabled=bool(row["enabled"]),
    )


def list_users() -> list[User]:
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM users ORDER BY username COLLATE NOCASE"
        ).fetchall()
    return [_hydrate_user(r) for r in rows]


def get_user_by_id(user_id: int) -> Optional[User]:
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _hydrate_user(row) if row else None


def get_user_by_username(username: str) -> Optional[User]:
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
    return _hydrate_user(row) if row else None


def create_user(
    username: str,
    password: str,
    role: str = "user",
    display_name: str = "",
) -> User:
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    if not username.strip():
        raise ValueError("username is required")
    if not password:
        raise ValueError("password is required")
    hashed, salt = _hash_password(password)
    with _conn() as conn:
        try:
            cursor = conn.execute(
                """INSERT INTO users
                   (username, display_name, role, password_hash, password_salt)
                   VALUES (?, ?, ?, ?, ?)""",
                (username.strip(), display_name.strip(), role, hashed, salt),
            )
            return get_user_by_id(cursor.lastrowid)
        except sqlite3.IntegrityError:
            raise ValueError(f"Username '{username}' already exists")


def update_user(
    user_id: int,
    display_name: Optional[str] = None,
    role: Optional[str] = None,
    enabled: Optional[bool] = None,
    password: Optional[str] = None,
) -> Optional[User]:
    if get_user_by_id(user_id) is None:
        return None
    if role is not None and role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    with _conn() as conn:
        if display_name is not None:
            conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ?",
                (display_name, user_id),
            )
        if role is not None:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        if enabled is not None:
            conn.execute(
                "UPDATE users SET enabled = ? WHERE id = ?",
                (int(enabled), user_id),
            )
        if password:
            hashed, salt = _hash_password(password)
            conn.execute(
                "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
                (hashed, salt, user_id),
            )
    return get_user_by_id(user_id)


def delete_user(user_id: int):
    with _conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def verify_password(username: str, password: str) -> Optional[User]:
    """Return a User if credentials are valid, else None."""
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE AND enabled = 1",
            (username,),
        ).fetchone()
    if not row:
        return None
    hashed, _ = _hash_password(password, row["password_salt"])
    if hashed == row["password_hash"]:
        return _hydrate_user(row)
    return None


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def get_current_user() -> Optional[User]:
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = get_user_by_id(user_id)
    if not user or not user.enabled:
        session.clear()
        return None
    session["username"] = user.username
    session["role"] = user.role
    return user


def login_user(user: User):
    session.permanent = True
    session["user_id"] = user.id
    session["username"] = user.username
    session["role"] = user.role


def logout_user():
    session.clear()


def is_admin() -> bool:
    user = get_current_user()
    return bool(user and user.role == "admin")


def current_username() -> str:
    user = get_current_user()
    return user.username if user else ""


# ---------------------------------------------------------------------------
# Route decorators
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated
