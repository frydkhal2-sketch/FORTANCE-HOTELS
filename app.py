from flask import jsonify
from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
import hashlib
import hmac
import secrets
import requests
from functools import wraps
from datetime import datetime, date

BASE = os.path.dirname(__file__)
DB = os.path.join(BASE, "fortans.db")

app = Flask(
    __name__,
    template_folder=".",
    static_folder=".",
    static_url_path="/static"
)

app.secret_key = os.environ.get("FORTANCE_SECRET", secrets.token_hex(32))

ROLES = {
    "admin": "مدير النظام",
    "manager": "مدير",
    "reception": "استقبال",
    "accountant": "محاسب"
}

DEFAULT_SETTINGS = {
    "company_name": "FORTANCE HOTELS",
    "company_phone": "",
    "company_email": "",
    "company_address": "",
    "currency": "ريال سعودي",
    "payment_url": "",
    "payment_provider": ""
}


def db():
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def hash_pw(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def ensure_column(connection, table, column, definition):
    cols = {
        row["name"]
        for row in connection.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }
    if column not in cols:
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def get_settings(connection=None):
    own = connection is None
    connection = connection or db()

    rows = connection.execute(
        "SELECT key, value FROM settings"
    ).fetchall()

    settings = DEFAULT_SETTINGS.copy()

    for row in rows:
        settings[row["key"]] = row["value"] or ""

    if own:
        connection.close()

    return settings


def save_setting(connection, key, value):
    connection.execute(
        """
        INSERT INTO settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """,
        (key, value)
    )


def init_db():
    connection = db()

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS employees(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'reception',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS hotels(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            city TEXT,
            phone TEXT,
            notes TEXT,
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS rooms(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hotel_id INTEGER NOT NULL,
            number TEXT NOT NULL,
            room_type TEXT,
            price REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'available',
            UNIQUE(hotel_id, number),
            FOREIGN KEY(hotel_id) REFERENCES hotels(id)
        );

        CREATE TABLE IF NOT EXISTS customers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS companies(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact_name TEXT,
            phone TEXT,
            email TEXT,
            tax_number TEXT,
            notes TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bookings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            customer_id INTEGER NOT NULL,
            hotel_id INTEGER NOT NULL,
            room_id INTEGER NOT NULL,
            checkin TEXT NOT NULL,
            checkout TEXT NOT NULL,
            nights INTEGER NOT NULL,
            price REAL NOT NULL,
            paid REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'confirmed',
            created_by INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(customer_id) REFERENCES customers(id),
            FOREIGN KEY(hotel_id) REFERENCES hotels(id),
            FOREIGN KEY(room_id) REFERENCES rooms(id)
        );

        CREATE TABLE IF NOT EXISTS group_bookings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            group_name TEXT NOT NULL,
            contact_name TEXT,
            phone TEXT,
            company_id INTEGER,
            hotel_id INTEGER NOT NULL,
            checkin TEXT NOT NULL,
            checkout TEXT NOT NULL,
            guests INTEGER NOT NULL DEFAULT 0,
            rooms_count INTEGER NOT NULL DEFAULT 0,
            total REAL NOT NULL DEFAULT 0,
            paid REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'confirmed',
            notes TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(hotel_id) REFERENCES hotels(id)
        );

        CREATE TABLE IF NOT EXISTS group_booking_rooms(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_booking_id INTEGER NOT NULL,
            room_id INTEGER NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            FOREIGN KEY(group_booking_id)
                REFERENCES group_bookings(id)
                ON DELETE CASCADE,
            FOREIGN KEY(room_id) REFERENCES rooms(id)
        );

        CREATE TABLE IF NOT EXISTS seasons(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER,
            group_booking_id INTEGER,
            amount REAL NOT NULL,
            method TEXT,
            payment_url TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reference TEXT,
            created_at TEXT NOT NULL,
            created_by INTEGER
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hotel_id INTEGER,
            expense_date TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            amount REAL NOT NULL DEFAULT 0,
            payment_method TEXT DEFAULT 'cash',
            created_at TEXT NOT NULL,
            created_by INTEGER
        );
        """
    )

    # إضافة أعمدة جديدة للقاعدة الحالية بدون حذف البيانات القديمة.
    ensure_column(
        connection, "bookings", "payment_method",
        "TEXT DEFAULT 'cash'"
    )
    ensure_column(
        connection, "bookings", "payment_url",
        "TEXT DEFAULT ''"
    )
    ensure_column(
        connection, "bookings", "company_id",
        "INTEGER"
    )
    ensure_column(
        connection, "bookings", "group_booking_id",
        "INTEGER"
    )
    ensure_column(
        connection, "bookings", "moyasar_invoice_id",
        "TEXT DEFAULT ''"
    )
    ensure_column(
        connection, "bookings", "payment_status",
        "TEXT DEFAULT 'pending'"
    )

    for key, value in DEFAULT_SETTINGS.items():
        exists = connection.execute(
            "SELECT 1 FROM settings WHERE key=?",
            (key,)
        ).fetchone()

        if exists is None:
            connection.execute(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                (key, value)
            )

    if connection.execute(
        "SELECT COUNT(*) AS n FROM employees"
    ).fetchone()["n"] == 0:
        connection.execute(
            """
            INSERT INTO employees(
                name, username, password, role, created_at
            )
            VALUES(?,?,?,?,?)
            """,
            (
                "مدير النظام",
                "admin",
                hash_pw("ChangeMe123!"),
                "admin",
                datetime.now().isoformat()
            )
        )

    if connection.execute(
        "SELECT COUNT(*) AS n FROM hotels"
    ).fetchone()["n"] == 0:
        connection.execute(
            "INSERT INTO hotels(name,city,phone) VALUES(?,?,?)",
            ("فندق ريان الجوار", "مكة المكرمة", "")
        )

    # إزالة الغرف التجريبية القديمة مرة واحدة فقط،
    # وفقط إذا لم توجد حجوزات حالية مرتبطة بها.
    cleanup = connection.execute(
        """
        SELECT value
        FROM settings
        WHERE key='initial_rooms_cleanup'
        """
    ).fetchone()

    if cleanup is None:
        booked_rooms = connection.execute(
            "SELECT COUNT(*) AS n FROM bookings"
        ).fetchone()["n"]

        if booked_rooms == 0:
            connection.execute("DELETE FROM rooms")

        connection.execute(
            """
            INSERT INTO settings(key,value)
            VALUES('initial_rooms_cleanup','done')
            """
        )

    connection.commit()
    connection.close()


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "uid" not in session:
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return wrapper


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            flash(
                "هذه الصفحة متاحة لمدير النظام فقط.",
                "error"
            )
            return redirect(url_for("dashboard"))

        return function(*args, **kwargs)

    return wrapper


def accountant_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if session.get("role") not in {"admin", "accountant"}:
            flash(
                "هذه الصفحة متاحة لمدير النظام أو المحاسب فقط.",
                "error"
            )
            return redirect(url_for("dashboard"))
        return function(*args, **kwargs)
    return wrapper


def normalize_date_range(start_date=None, end_date=None):
    if start_date and end_date:
        return start_date, end_date

    if start_date and not end_date:
        return start_date, start_date

    if end_date and not start_date:
        return end_date, end_date

    first_day = date.today().replace(day=1).isoformat()
    today = date.today().isoformat()
    return first_day, today


def get_financial_summary(connection, start_date=None, end_date=None, hotel_id=None):
    start_date, end_date = normalize_date_range(start_date, end_date)

    hotel_filter = ""
    params = [start_date, end_date]

    if hotel_id:
        hotel_filter = " AND hotel_id=? "
        params.append(hotel_id)

    bookings_revenue_q = f"""
        SELECT COALESCE(SUM(paid), 0) AS total
        FROM bookings
        WHERE status != 'cancelled'
          AND substr(created_at, 1, 10) BETWEEN ? AND ?
        {hotel_filter}
    """
    bookings_revenue = connection.execute(
        bookings_revenue_q,
        params
    ).fetchone()["total"]

    group_revenue_q = f"""
        SELECT COALESCE(SUM(paid), 0) AS total
        FROM group_bookings
        WHERE status != 'cancelled'
          AND substr(created_at, 1, 10) BETWEEN ? AND ?
        {hotel_filter}
    """
    group_revenue = connection.execute(
        group_revenue_q,
        params
    ).fetchone()["total"]

    expenses_q = """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE substr(expense_date, 1, 10) BETWEEN ? AND ?
    """
    expense_params = [start_date, end_date]

    if hotel_id:
        expenses_q += " AND hotel_id=?"
        expense_params.append(hotel_id)

    expenses_total = connection.execute(
        expenses_q,
        expense_params
    ).fetchone()["total"]

    total_revenue = float(bookings_revenue) + float(group_revenue)
    profit = total_revenue - float(expenses_total)

    breakdown_q = """
        SELECT
            h.id,
            h.name,
            COALESCE((
                SELECT SUM(b.paid)
                FROM bookings b
                WHERE b.hotel_id = h.id
                  AND b.status != 'cancelled'
                  AND substr(b.created_at, 1, 10) BETWEEN ? AND ?
            ), 0) AS booking_revenue,
            COALESCE((
                SELECT SUM(gb.paid)
                FROM group_bookings gb
                WHERE gb.hotel_id = h.id
                  AND gb.status != 'cancelled'
                  AND substr(gb.created_at, 1, 10) BETWEEN ? AND ?
            ), 0) AS group_revenue,
            COALESCE((
                SELECT SUM(e.amount)
                FROM expenses e
                WHERE e.hotel_id = h.id
                  AND substr(e.expense_date, 1, 10) BETWEEN ? AND ?
            ), 0) AS expenses
        FROM hotels h
        WHERE h.active = 1
        ORDER BY h.name
    """
    breakdown_rows = connection.execute(
        breakdown_q,
        (start_date, end_date, start_date, end_date, start_date, end_date)
    ).fetchall()

    hotel_breakdown = []
    for row in breakdown_rows:
        revenue = float(row["booking_revenue"]) + float(row["group_revenue"]) 
