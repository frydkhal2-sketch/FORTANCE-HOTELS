import csv
import io
import os
import sqlite3
import hashlib
import secrets
import requests

from urllib.parse import quote
from functools import wraps
from datetime import datetime, date

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)


# =========================================================
# إعداد التطبيق وقاعدة البيانات
# =========================================================

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "fortans.db")

app = Flask(
    __name__,
    template_folder=".",
    static_folder=".",
    static_url_path="/static"
)

app.secret_key = (
    os.environ.get("FORTANCE_SECRET")
    or secrets.token_hex(32)
)

ROLES = {
    "admin": "مدير النظام",
    "manager": "مدير",
    "reception": "استقبال",
    "accountant": "محاسب",
}

DEFAULT_SETTINGS = {
    "company_name": "FORTANCE HOTELS",
    "company_phone": "",
    "company_email": "",
    "company_address": "",
    "currency": "ريال سعودي",
    "payment_url": "",
    "payment_provider": "",
}


# =========================================================
# الاتصال بقاعدة البيانات
# =========================================================

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def hash_pw(password):
    return hashlib.sha256(
        password.encode("utf-8")
    ).hexdigest()


def ensure_column(conn, table, column, definition):
    columns = {
        row["name"]
        for row in conn.execute(
            f"PRAGMA table_info({table})"
        )
    }

    if column not in columns:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


# =========================================================
# إعدادات النظام
# =========================================================

def get_settings(conn=None):
    own_connection = conn is None

    if conn is None:
        conn = db()

    result = DEFAULT_SETTINGS.copy()

    rows = conn.execute(
        "SELECT key, value FROM settings"
    ).fetchall()

    result.update({
        row["key"]: row["value"] or ""
        for row in rows
    })

    if own_connection:
        conn.close()

    return result


def save_setting(conn, key, value):
    conn.execute(
        """
        INSERT INTO settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """,
        (key, value)
    )


# =========================================================
# إنشاء الجداول وتجهيز قاعدة البيانات
# =========================================================

def init_db():
    conn = db()

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'reception',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS hotels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        city TEXT,
        phone TEXT,
        notes TEXT,
        active INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hotel_id INTEGER NOT NULL,
        number TEXT NOT NULL,
        room_type TEXT,
        price REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'available',
        UNIQUE(hotel_id, number),
        FOREIGN KEY(hotel_id) REFERENCES hotels(id)
    );

    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT,
        notes TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS companies (
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

    CREATE TABLE IF NOT EXISTS bookings (
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

    CREATE TABLE IF NOT EXISTS group_bookings (
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

    CREATE TABLE IF NOT EXISTS group_booking_rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_booking_id INTEGER NOT NULL,
        room_id INTEGER NOT NULL,
        price REAL NOT NULL DEFAULT 0,
        FOREIGN KEY(group_booking_id)
            REFERENCES group_bookings(id) ON DELETE CASCADE,
        FOREIGN KEY(room_id) REFERENCES rooms(id)
    );

    CREATE TABLE IF NOT EXISTS seasons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        notes TEXT
    );

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE IF NOT EXISTS payments (
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
    """)

    # أعمدة إضافية للحجوزات القديمة والجديدة
    booking_columns = (
        ("payment_method", "TEXT DEFAULT 'cash'"),
        ("payment_url", "TEXT DEFAULT ''"),
        ("company_id", "INTEGER"),
        ("group_booking_id", "INTEGER"),
        ("moyasar_invoice_id", "TEXT DEFAULT ''"),
        ("payment_status", "TEXT DEFAULT 'pending'"),
    )

    for column, definition in booking_columns:
        ensure_column(
            conn,
            "bookings",
            column,
            definition
        )

    group_columns = (
        ("payment_method", "TEXT DEFAULT 'cash'"),
        ("payment_url", "TEXT DEFAULT ''"),
        ("payment_status", "TEXT DEFAULT 'pending'"),
    )

    for column, definition in group_columns:
        ensure_column(
            conn,
            "group_bookings",
            column,
            definition
        )

    # إعدادات النظام الافتراضية
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            """
            INSERT INTO settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (key, value)
        )

    # إنشاء حساب المدير عند أول تشغيل فقط
    employee_count = conn.execute(
        "SELECT COUNT(*) FROM employees"
    ).fetchone()[0]

    if employee_count == 0:
        conn.execute(
            """
            INSERT INTO employees(
                name, username, password, role, created_at
            )
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                "مدير النظام",
                "admin",
                hash_pw("ChangeMe123!"),
                "admin",
                datetime.now().isoformat()
            )
        )

    # إنشاء الفندق الافتراضي عند عدم وجود فنادق
 
        )

    # لا يتم إنشاء غرف تجريبية تلقائيًا
    # ولا يتم حذف أي بيانات قديمة

    conn.execute(
        """
        INSERT INTO settings(key, value)
        VALUES('initial_rooms_cleanup', 'done')
        ON CONFLICT(key) DO NOTHING
        """
    )

    conn.commit()
    conn.close()


# =========================================================
# الصلاحيات وتسجيل الدخول
# =========================================================

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if "uid" not in session:
            return redirect(url_for("login"))

        return fn(*args, **kwargs)

    return wrapped


def role_required(roles, message):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if "uid" not in session:
                return redirect(url_for("login"))

            if session.get("role") not in roles:
                flash(message, "error")
                return redirect(url_for("dashboard"))

            return fn(*args, **kwargs)

        return wrapped

    return decorate


admin_required = role_required(
    {"admin"},
    "هذه الصفحة متاحة لمدير النظام فقط."
)

accountant_required = role_required(
    {"admin", "accountant"},
    "هذه الصفحة متاحة لمدير النظام أو المحاسب فقط."
)


# =========================================================
# أدوات مساعدة
# =========================================================

def normalize_date_range(start=None, end=None):
    if start and end:
        return start, end

    if start:
        return start, start

    if end:
        return end, end

    return (
        date.today().replace(day=1).isoformat(),
        date.today().isoformat()
    )


def parse_nights(checkin, checkout):
    first = date.fromisoformat(checkin)
    last = date.fromisoformat(checkout)

    nights = (last - first).days

    if nights <= 0:
        raise ValueError("تاريخ المغادرة يجب أن يكون بعد الوصول")

    return nights


def safe_float(value, default=None):
    try:
        result = float(value or 0)

        if result < 0:
            raise ValueError

        return result

    except (TypeError, ValueError):
        if default is not None:
            return default

        raise


def normalize_phone(phone):
    digits = "".join(
        char for char in str(phone or "")
        if char.isdigit()
    )

    if digits.startswith("00"):
        digits = digits[2:]

    if digits.startswith("0"):
        digits = "966" + digits[1:]

    return digits


def generate_code(prefix="BK"):
    return (
        prefix
        + "-"
        + datetime.now().strftime("%y%m%d")
        + "-"
        + secrets.token_hex(3).upper()
    )


# =========================================================
# ملخص الحسابات المالية
# =========================================================

def get_financial_summary(
    conn,
    start_date=None,
    end_date=None,
    hotel_id=None
):
    start_date, end_date = normalize_date_range(
        start_date,
        end_date
    )

    hotel_clause = " AND hotel_id=?" if hotel_id else ""

    params = [start_date, end_date]

    if hotel_id:
        params.append(hotel_id)

    booking_revenue = conn.execute(
        f"""
        SELECT COALESCE(SUM(paid), 0)
        FROM bookings
        WHERE status != 'cancelled'
          AND substr(created_at, 1, 10) BETWEEN ? AND ?
          {hotel_clause}
        """,
        params
    ).fetchone()[0]

    group_revenue = conn.execute(
        f"""
        SELECT COALESCE(SUM(paid), 0)
        FROM group_bookings
        WHERE status != 'cancelled'
          AND substr(created_at, 1, 10) BETWEEN ? AND ?
          {hotel_clause}
        """,
        params
    ).fetchone()[0]

    expense_params = [start_date, end_date]

    if hotel_id:
        expense_params.append(hotel_id)

    expenses = conn.execute(
        f"""
        SELECT COALESCE(SUM(amount), 0)
        FROM expenses
        WHERE substr(expense_date, 1, 10) BETWEEN ? AND ?
        {hotel_clause}
        """,
        expense_params
    ).fetchone()[0]

    rows = conn.execute(
        """
        SELECT
            h.id,
            h.name,

            COALESCE((
                SELECT SUM(b.paid)
                FROM bookings b
                WHERE b.hotel_id = h.id
                  AND b.status != 'cancelled'
                  AND substr(b.created_at, 1, 10)
                      BETWEEN ? AND ?
            ), 0) AS booking_revenue,

            COALESCE((
                SELECT SUM(g.paid)
                FROM group_bookings g
                WHERE g.hotel_id = h.id
                  AND g.status != 'cancelled'
                  AND substr(g.created_at, 1, 10)
                      BETWEEN ? AND ?
            ), 0) AS group_revenue,

            COALESCE((
                SELECT SUM(e.amount)
                FROM expenses e
                WHERE e.hotel_id = h.id
                  AND substr(e.expense_date, 1, 10)
                      BETWEEN ? AND ?
            ), 0) AS expenses

        FROM hotels h
        WHERE h.active = 1
        ORDER BY h.name
        """,
        (
            start_date,
            end_date,
            start_date,
            end_date,
            start_date,
            end_date
        )
    ).fetchall()

    breakdown = []

    for row in rows:
        if hotel_id and str(row["id"]) != str(hotel_id):
            continue

        revenue = (
            float(row["booking_revenue"] or 0)
            + float(row["group_revenue"] or 0)
        )

        expense = float(row["expenses"] or 0)

        breakdown.append({
            "id": row["id"],
            "name": row["name"],
            "revenue": revenue,
            "expenses": expense,
            "profit": revenue - expense
        })

    category_params = [start_date, end_date]

    if hotel_id:
        category_params.append(hotel_id)

    categories = conn.execute(
        f"""
        SELECT
            category,
            COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE substr(expense_date, 1, 10) BETWEEN ? AND ?
        {hotel_clause}
        GROUP BY category
        ORDER BY total DESC
        """,
        category_params
    ).fetchall()

    revenue = (
        float(booking_revenue or 0)
        + float(group_revenue or 0)
    )

    expenses = float(expenses or 0)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "hotel_id": hotel_id or "",
        "revenue": revenue,
        "expenses": expenses,
        "profit": revenue - expenses,
        "hotel_breakdown": breakdown,
        "categories": [dict(row) for row in categories]
   }
# =========================================================
# المتغيرات العامة التي تستخدمها صفحات HTML
# =========================================================

@app.context_processor
def ctx():
    employee_name = session.get("name")
    employee_role = session.get("role")

    return {
        "ROLES": ROLES,
        "user_name": employee_name,
        "user_role": employee_role,
        "current_employee": employee_name,
        "current_role": employee_role,
        "system_settings": get_settings()
    }


# =========================================================
# التحقق من تعارض مواعيد الغرف
# =========================================================

def room_conflict(
    conn,
    room_id,
    checkin,
    checkout,
    exclude_booking_id=None
):
    sql = """
        SELECT 1
        FROM bookings
        WHERE room_id=?
          AND status='confirmed'
          AND checkin<?
          AND checkout>?
    """

    params = [room_id, checkout, checkin]

    if exclude_booking_id is not None:
        sql += " AND id!=?"
        params.append(exclude_booking_id)

    if conn.execute(
        sql + " LIMIT 1",
        params
    ).fetchone():
        return True

    group_conflict = conn.execute(
        """
        SELECT 1
        FROM group_booking_rooms r
        JOIN group_bookings g
            ON g.id=r.group_booking_id
        WHERE r.room_id=?
          AND g.status='confirmed'
          AND g.checkin<?
          AND g.checkout>?
        LIMIT 1
        """,
        (room_id, checkout, checkin)
    ).fetchone()

    return group_conflict is not None


def get_room_status(room_id, today, bookings):
    matching = [
        booking
        for booking in bookings
        if str(booking["room_id"]) == str(room_id)
    ]

    if any(
        booking["checkin"] <= today < booking["checkout"]
        for booking in matching
    ):
        return "occupied"

    if any(
        booking["checkout"] == today
        for booking in matching
    ):
        return "checkout_today"

    if any(
        booking["checkin"] > today
        for booking in matching
    ):
        return "upcoming"

    return "vacant"


# =========================================================
# الصفحة الرئيسية
# =========================================================

@app.route("/")
def index():
    if "uid" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


# =========================================================
# تسجيل الدخول
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        conn = db()

        user = conn.execute(
            """
            SELECT *
            FROM employees
            WHERE username=?
              AND active=1
            """,
            (username,)
        ).fetchone()

        conn.close()

        if user and user["password"] == hash_pw(password):
            session.clear()

            session.update({
                "uid": user["id"],
                "name": user["name"],
                "role": user["role"]
            })

            return redirect(url_for("dashboard"))

        flash(
            "اسم المستخدم أو كلمة المرور غير صحيحة.",
            "error"
        )

    return render_template("login.html")


# =========================================================
# تسجيل الخروج
# =========================================================

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================================================
# لوحة التحكم
# =========================================================

@app.route("/dashboard")
@login_required
def dashboard():
    conn = db()

    data = {
        "hotels": conn.execute(
            """
            SELECT COUNT(*) n
            FROM hotels
            WHERE active=1
            """
        ).fetchone()["n"],

        "rooms": conn.execute(
            "SELECT COUNT(*) n FROM rooms"
        ).fetchone()["n"],

        "booked": conn.execute(
            """
            SELECT COUNT(*) n
            FROM bookings
            WHERE status='confirmed'
            """
        ).fetchone()["n"],

        "revenue": conn.execute(
            """
            SELECT COALESCE(SUM(paid), 0) n
            FROM bookings
            WHERE status!='cancelled'
            """
        ).fetchone()["n"],

        "bookings": conn.execute(
            """
            SELECT
                b.*,
                c.name customer,
                h.name hotel,
                r.number room
            FROM bookings b
            JOIN customers c ON c.id=b.customer_id
            JOIN hotels h ON h.id=b.hotel_id
            JOIN rooms r ON r.id=b.room_id
            ORDER BY b.id DESC
            LIMIT 8
            """
        ).fetchall(),

        "now": date.today().isoformat()
    }

    conn.close()

    return render_template(
        "dashboard.html",
        **data
    )


# =========================================================
# إدارة الموظفين
# =========================================================

@app.route("/employees", methods=["GET", "POST"])
@login_required
@admin_required
def employees():
    conn = db()

    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "reception")

            if not name or not username or not password:
                raise ValueError

            if role not in ROLES:
                raise ValueError

            conn.execute(
                """
                INSERT INTO employees(
                    name,
                    username,
                    password,
                    role,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    name,
                    username,
                    hash_pw(password),
                    role,
                    datetime.now().isoformat()
                )
            )

            conn.commit()
            flash("تمت إضافة الموظف بنجاح.", "ok")

        except sqlite3.IntegrityError:
            flash(
                "اسم المستخدم مستخدم من قبل.",
                "error"
            )

        except (ValueError, KeyError):
            flash(
                "تحقق من بيانات الموظف.",
                "error"
            )

    rows = conn.execute(
        """
        SELECT id, name, username, role, active, created_at
        FROM employees
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "employees.html",
        employees=rows
    )


@app.route("/employees/toggle/<int:eid>", methods=["POST"])
@login_required
@admin_required
def toggle_employee(eid):
    if eid == session.get("uid"):
        flash(
            "لا يمكنك تعطيل حسابك الحالي.",
            "error"
        )
        return redirect(url_for("employees"))

    conn = db()

    cursor = conn.execute(
        """
        UPDATE employees
        SET active = CASE
            WHEN active=1 THEN 0
            ELSE 1
        END
        WHERE id=?
        """,
        (eid,)
    )

    conn.commit()
    conn.close()

    if cursor.rowcount:
        flash("تم تحديث حالة الموظف.", "ok")
    else:
        flash("الموظف غير موجود.", "error")

    return redirect(url_for("employees"))


# =========================================================
# إدارة الفنادق
# =========================================================

@app.route("/hotels", methods=["GET", "POST"])
@login_required
def hotels():
    conn = db()

    if request.method == "POST":
        name = request.form.get(
            "name",
            ""
        ).strip()

        if name:
            conn.execute(
                """
                INSERT INTO hotels(
                    name,
                    city,
                    phone,
                    notes
                )
                VALUES(?, ?, ?, ?)
                """,
                (
                    name,
                    request.form.get("city", "").strip(),
                    request.form.get("phone", "").strip(),
                    request.form.get("notes", "").strip()
                )
            )

            conn.commit()
            flash("تمت إضافة الفندق.", "ok")

        else:
            flash("اسم الفندق مطلوب.", "error")

    rows = conn.execute(
        """
        SELECT
            h.*,
            (
                SELECT COUNT(*)
                FROM rooms r
                WHERE r.hotel_id=h.id
            ) AS rooms
        FROM hotels h
        ORDER BY h.id DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "hotels.html",
        hotels=rows
    )


# =========================================================
# إدارة الغرف
# =========================================================

@app.route("/rooms", methods=["GET", "POST"])
@login_required
def rooms():
    conn = db()

    today = date.today().isoformat()
    selected = request.args.get("hotel_id", "")

    if request.method == "POST":
        hotel_id = request.form.get(
            "hotel_id",
            ""
        ).strip()

        status = request.form.get(
            "status",
            "available"
        )

        allowed_statuses = {
            "available",
            "maintenance",
            "out_of_service",
            "cleaning"
        }

        if status not in allowed_statuses:
            status = "available"

        room_type = request.form.get(
            "room_type",
            ""
        ).strip()

        try:
            price = safe_float(
                request.form.get("price", "0")
            )

            if not hotel_id:
                raise ValueError

            hotel_exists = conn.execute(
                """
                SELECT id
                FROM hotels
                WHERE id=? AND active=1
                """,
                (hotel_id,)
            ).fetchone()

            if not hotel_exists:
                raise ValueError

            if request.form.get("action") == "bulk":
                start = int(request.form["start_number"])
                end = int(request.form["end_number"])

                if start < 0 or end < start or end - start > 1000:
                    raise ValueError

                created = 0

                for number in range(start, end + 1):
                    try:
                        conn.execute(
                            """
                            INSERT INTO rooms(
                                hotel_id,
                                number,
                                room_type,
                                price,
                                status
                            )
                            VALUES(?, ?, ?, ?, ?)
                            """,
                            (
                                hotel_id,
                                str(number),
                                room_type,
                                price,
                                status
                            )
                        )

                        created += 1

                    except sqlite3.IntegrityError:
                        # تجاهل رقم الغرفة الموجود مسبقًا
                        continue

                conn.commit()

                flash(
                    f"تم إنشاء {created} غرفة بنجاح.",
                    "ok"
                )

            else:
                number = request.form.get(
                    "number",
                    ""
                ).strip()

                if not number:
                    raise ValueError

                conn.execute(
                    """
                    INSERT INTO rooms(
                        hotel_id,
                        number,
                        room_type,
                        price,
                        status
                    )
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        hotel_id,
                        number,
                        room_type,
                        price,
                        status
                    )
                )

                conn.commit()
                flash("تمت إضافة الغرفة بنجاح.", "ok")

        except (
            ValueError,
            TypeError,
            sqlite3.IntegrityError,
            KeyError
        ):
            conn.rollback()

            flash(
                "تعذر إضافة الغرفة. تحقق من البيانات ورقم الغرفة.",
                "error"
            )

        conn.close()

        return redirect(
            url_for(
                "rooms",
                hotel_id=hotel_id
            )
        )

    hotels_list = conn.execute(
        """
        SELECT id, name
        FROM hotels
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    rows = conn.execute(
        """
        SELECT
            r.*,
            h.name AS hotel_name
        FROM rooms r
        JOIN hotels h ON h.id=r.hotel_id
        WHERE (?='' OR r.hotel_id=?)
        ORDER BY h.name, r.number
        """,
        (selected, selected)
    ).fetchall()

    bookings_list = [
        dict(row)
        for row in conn.execute(
            """
            SELECT room_id, checkin, checkout
            FROM bookings
            WHERE status='confirmed'
              AND checkout>=?
            """,
            (today,)
        ).fetchall()
    ]

    group_bookings_list = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                gbr.room_id,
                gb.checkin,
                gb.checkout
            FROM group_booking_rooms gbr
            JOIN group_bookings gb
                ON gb.id=gbr.group_booking_id
            WHERE gb.status='confirmed'
              AND gb.checkout>=?
            """,
            (today,)
        ).fetchall()
    ]

    all_bookings = (
        bookings_list + group_bookings_list
    )

    room_list = []

    for row in rows:
        item = dict(row)

        item["room_status"] = item["status"]

        if item["status"] in {"available", "booked"}:
            item["room_status"] = get_room_status(
                item["id"],
                today,
                all_bookings
            )

        room_list.append(item)

    count_keys = (
        "vacant",
        "occupied",
        "checkout_today",
        "upcoming",
        "maintenance",
        "out_of_service",
        "cleaning"
    )

    counts = {
        key: sum(
            room["room_status"] == key
            for room in room_list
        )
        for key in count_keys
    }

    counts["total"] = len(room_list)

    conn.close()

    return render_template(
        "rooms.html",
        hotels=hotels_list,
        rooms=room_list,
        counts=counts,
        selected_hotel=selected,
        today=today
    )


# =========================================================
# تحديث حالة الغرفة
# =========================================================

@app.route("/rooms/status/<int:rid>", methods=["POST"])
@login_required
def room_status(rid):
    status = request.form.get(
        "status",
        "available"
    )

    allowed_statuses = {
        "available",
        "maintenance",
        "out_of_service",
        "cleaning"
    }

    if status not in allowed_statuses:
        flash("حالة الغرفة غير صحيحة.", "error")
        return redirect(url_for("rooms"))

    conn = db()

    cursor = conn.execute(
        """
        UPDATE rooms
        SET status=?
        WHERE id=?
        """,
        (status, rid)
    )

    conn.commit()
    conn.close()

    if cursor.rowcount:
        flash("تم تحديث حالة الغرفة.", "ok")
    else:
        flash("الغرفة غير موجودة.", "error")

    return redirect(url_for("rooms"))
    # =========================================================
# الحجوزات الفردية
# =========================================================

@app.route("/bookings", methods=["GET", "POST"])
@login_required
def bookings():
    conn = db()

    if request.method == "POST":
        form = request.form

        name = form.get(
            "name",
            form.get("customer", "")
        ).strip()

        phone = form.get("phone", "").strip()

        hotel_id = form.get(
            "hotel_id",
            form.get("hotel", "")
        )

        room_id = form.get(
            "room_id",
            form.get("room", "")
        )

        checkin = form.get("checkin", "")
        checkout = form.get("checkout", "")

        try:
            if not name:
                raise ValueError

            nights = parse_nights(
                checkin,
                checkout
            )

            room = conn.execute(
                """
                SELECT *
                FROM rooms
                WHERE id=? AND hotel_id=?
                """,
                (room_id, hotel_id)
            ).fetchone()

            if not room:
                raise ValueError

            if room["status"] in {
                "maintenance",
                "out_of_service",
                "cleaning"
            }:
                raise ValueError

            if room_conflict(
                conn,
                room_id,
                checkin,
                checkout
            ):
                raise ValueError

            customer = conn.execute(
                """
                SELECT id
                FROM customers
                WHERE phone=? AND phone!=''
                """,
                (phone,)
            ).fetchone()

            if customer:
                customer_id = customer["id"]

                conn.execute(
                    """
                    UPDATE customers
                    SET name=?
                    WHERE id=?
                    """,
                    (name, customer_id)
                )

            else:
                customer_id = conn.execute(
                    """
                    INSERT INTO customers(
                        name,
                        phone,
                        notes,
                        created_at
                    )
                    VALUES(?, ?, ?, ?)
                    """,
                    (
                        name,
                        phone,
                        "",
                        datetime.now().isoformat()
                    )
                ).lastrowid

            price = float(room["price"] or 0) * nights

            paid = safe_float(
                form.get("paid", "0")
            )

            if paid > price:
                raise ValueError

            code = generate_code("BK")

            method = form.get(
                "payment_method",
                "cash"
            )

            payment_url = form.get(
                "payment_url",
                ""
            ).strip()

            booking_id = conn.execute(
                """
                INSERT INTO bookings(
                    code,
                    customer_id,
                    hotel_id,
                    room_id,
                    checkin,
                    checkout,
                    nights,
                    price,
                    paid,
                    status,
                    created_by,
                    created_at,
                    payment_method,
                    payment_url
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    customer_id,
                    hotel_id,
                    room_id,
                    checkin,
                    checkout,
                    nights,
                    price,
                    paid,
                    "confirmed",
                    session["uid"],
                    datetime.now().isoformat(),
                    method,
                    payment_url
                )
            ).lastrowid

            conn.execute(
                """
                UPDATE rooms
                SET status='booked'
                WHERE id=?
                """,
                (room_id,)
            )

            if paid > 0:
                conn.execute(
                    """
                    INSERT INTO payments(
                        booking_id,
                        amount,
                        method,
                        payment_url,
                        status,
                        created_at,
                        created_by
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        booking_id,
                        paid,
                        method,
                        payment_url,
                        "paid",
                        datetime.now().isoformat(),
                        session["uid"]
                    )
                )

            conn.commit()

            flash(
                f"تم إنشاء الحجز {code}.",
                "ok"
            )

        except (
            ValueError,
            TypeError,
            KeyError,
            sqlite3.IntegrityError
        ):
            conn.rollback()

            flash(
                "تعذر إنشاء الحجز. تحقق من الاسم والتواريخ والفندق والغرفة والمبلغ.",
                "error"
            )

        conn.close()

        return redirect(url_for("bookings"))

    rows = conn.execute(
        """
        SELECT
            b.*,
            cu.name AS customer,
            cu.phone AS customer_phone,
            h.name AS hotel,
            r.number AS room,
            r.room_type
        FROM bookings b
        JOIN customers cu ON cu.id=b.customer_id
        JOIN hotels h ON h.id=b.hotel_id
        JOIN rooms r ON r.id=b.room_id
        ORDER BY b.id DESC
        """
    ).fetchall()

    hotels_list = conn.execute(
        """
        SELECT *
        FROM hotels
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    rooms_list = conn.execute(
        """
        SELECT *
        FROM rooms
        WHERE status NOT IN(
            'maintenance',
            'out_of_service',
            'cleaning'
        )
        ORDER BY hotel_id, number
        """
    ).fetchall()

    conn.close()

    return render_template(
        "bookings.html",
        bookings=rows,
        hotels=hotels_list,
        rooms=rooms_list
    )


# =========================================================
# إلغاء الحجز الفردي
# =========================================================

@app.route("/bookings/cancel/<int:bid>", methods=["POST"])
@login_required
def cancel_booking(bid):
    conn = db()

    booking = conn.execute(
        """
        SELECT *
        FROM bookings
        WHERE id=?
        """,
        (bid,)
    ).fetchone()

    if not booking:
        conn.close()
        flash("الحجز غير موجود.", "error")
        return redirect(url_for("bookings"))

    if booking["status"] == "cancelled":
        conn.close()
        flash("الحجز ملغى بالفعل.", "error")
        return redirect(url_for("bookings"))

    conn.execute(
        """
        UPDATE bookings
        SET status='cancelled'
        WHERE id=?
        """,
        (bid,)
    )

    # التحقق من وجود حجز آخر للغرفة في نفس الفترة
    used = room_conflict(
        conn,
        booking["room_id"],
        booking["checkin"],
        booking["checkout"]
    )

    if not used:
        conn.execute(
            """
            UPDATE rooms
            SET status='available'
            WHERE id=? AND status='booked'
            """,
            (booking["room_id"],)
        )

    conn.commit()
    conn.close()

    flash("تم إلغاء الحجز.", "ok")

    return redirect(url_for("bookings"))


# =========================================================
# الحجوزات الجماعية
# =========================================================

@app.route("/group-bookings", methods=["GET", "POST"])
@login_required
def group_bookings():
    conn = db()

    if request.method == "POST":
        form = request.form

        group_name = form.get(
            "group_name",
            ""
        ).strip()

        hotel_id = form.get(
            "hotel_id",
            ""
        ).strip()

        room_ids = list(dict.fromkeys(
            room_id
            for room_id in form.getlist("room_ids")
            if room_id
        ))

        checkin = form.get("checkin", "")
        checkout = form.get("checkout", "")

        try:
            if not group_name or not hotel_id or not room_ids:
                raise ValueError

            nights = parse_nights(
                checkin,
                checkout
            )

            guests = int(
                form.get("guests", "0") or 0
            )

            if guests < 0:
                raise ValueError

            rooms_selected = []

            for room_id in room_ids:
                room = conn.execute(
                    """
                    SELECT *
                    FROM rooms
                    WHERE id=? AND hotel_id=?
                    """,
                    (room_id, hotel_id)
                ).fetchone()

                if not room:
                    raise ValueError

                if room["status"] in {
                    "maintenance",
                    "out_of_service",
                    "cleaning"
                }:
                    raise ValueError

                if room_conflict(
                    conn,
                    room_id,
                    checkin,
                    checkout
                ):
                    raise ValueError

                rooms_selected.append(room)

            total = sum(
                float(room["price"] or 0) * nights
                for room in rooms_selected
            )

            paid = safe_float(
                form.get("paid", "0")
            )

            if paid > total:
                raise ValueError

            company_id = (
                form.get("company_id") or None
            )

            if company_id:
                company_exists = conn.execute(
                    """
                    SELECT 1
                    FROM companies
                    WHERE id=? AND active=1
                    """,
                    (company_id,)
                ).fetchone()

                if not company_exists:
                    raise ValueError

            code = generate_code("GR")

            payment_method = form.get(
                "payment_method",
                "cash"
            )

            payment_url = form.get(
                "payment_url",
                ""
            ).strip()

            group_id = conn.execute(
                """
                INSERT INTO group_bookings(
                    code,
                    group_name,
                    contact_name,
                    phone,
                    company_id,
                    hotel_id,
                    checkin,
                    checkout,
                    guests,
                    rooms_count,
                    total,
                    paid,
                    status,
                    notes,
                    created_by,
                    created_at,
                    payment_method,
                    payment_url
                )
                VALUES(
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    code,
                    group_name,
                    form.get("contact_name", "").strip(),
                    form.get("phone", "").strip(),
                    company_id,
                    hotel_id,
                    checkin,
                    checkout,
                    guests,
                    len(rooms_selected),
                    total,
                    paid,
                    "confirmed",
                    form.get("notes", "").strip(),
                    session["uid"],
                    datetime.now().isoformat(),
                    payment_method,
                    payment_url
                )
            ).lastrowid

            for room in rooms_selected:
                room_price = (
                    float(room["price"] or 0) * nights
                )

                conn.execute(
                    """
                    INSERT INTO group_booking_rooms(
                        group_booking_id,
                        room_id,
                        price
                    )
                    VALUES(?, ?, ?)
                    """,
                    (
                        group_id,
                        room["id"],
                        room_price
                    )
                )

                conn.execute(
                    """
                    UPDATE rooms
                    SET status='booked'
                    WHERE id=?
                    """,
                    (room["id"],)
                )

            if paid > 0:
                conn.execute(
                    """
                    INSERT INTO payments(
                        group_booking_id,
                        amount,
                        method,
                        payment_url,
                        status,
                        created_at,
                        created_by
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        group_id,
                        paid,
                        payment_method,
                        payment_url,
                        "paid",
                        datetime.now().isoformat(),
                        session["uid"]
                    )
                )

            conn.commit()

            flash(
                f"تم إنشاء حجز المجموعة {code} لعدد {len(rooms_selected)} غرفة.",
                "ok"
            )

        except (
            ValueError,
            TypeError,
            KeyError,
            sqlite3.IntegrityError
        ):
            conn.rollback()

            flash(
                "تعذر إنشاء حجز المجموعة. تحقق من الفندق والغرف والتواريخ والمبالغ والتعارضات.",
                "error"
            )

        conn.close()

        return redirect(url_for("group_bookings"))

    rows = conn.execute(
        """
        SELECT
            gb.*,
            h.name AS hotel,
            c.name AS company
        FROM group_bookings gb
        JOIN hotels h ON h.id=gb.hotel_id
        LEFT JOIN companies c ON c.id=gb.company_id
        ORDER BY gb.id DESC
        """
    ).fetchall()

    hotels_list = conn.execute(
        """
        SELECT *
        FROM hotels
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    companies_list = conn.execute(
        """
        SELECT *
        FROM companies
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    rooms_list = conn.execute(
        """
        SELECT
            r.*,
            h.name AS hotel
        FROM rooms r
        JOIN hotels h ON h.id=r.hotel_id
        WHERE r.status NOT IN(
            'maintenance',
            'out_of_service',
            'cleaning'
        )
        ORDER BY h.name, r.number
        """
    ).fetchall()

    conn.close()

    return render_template(
        "group_bookings.html",
        group_bookings=rows,
        hotels=hotels_list,
        companies=companies_list,
        rooms=rooms_list
    )


# =========================================================
# إلغاء حجز المجموعة
# =========================================================

@app.route(
    "/group-bookings/cancel/<int:gid>",
    methods=["POST"]
)
@login_required
def cancel_group_booking(gid):
    conn = db()

    group = conn.execute(
        """
        SELECT *
        FROM group_bookings
        WHERE id=?
        """,
        (gid,)
    ).fetchone()

    if not group:
        conn.close()
        flash("حجز المجموعة غير موجود.", "error")
        return redirect(url_for("group_bookings"))

    if group["status"] == "cancelled":
        conn.close()
        flash("حجز المجموعة ملغى بالفعل.", "error")
        return redirect(url_for("group_bookings"))

    conn.execute(
        """
        UPDATE group_bookings
        SET status='cancelled'
        WHERE id=?
        """,
        (gid,)
    )

    assigned_rooms = conn.execute(
        """
        SELECT room_id
        FROM group_booking_rooms
        WHERE group_booking_id=?
        """,
        (gid,)
    ).fetchall()

    for row in assigned_rooms:
        room_id = row["room_id"]

        booking_exists = conn.execute(
            """
            SELECT 1
            FROM bookings
            WHERE room_id=?
              AND status='confirmed'
              AND checkin<?
              AND checkout>?
            LIMIT 1
            """,
            (
                room_id,
                group["checkout"],
                group["checkin"]
            )
        ).fetchone()

        group_exists = conn.execute(
            """
            SELECT 1
            FROM group_booking_rooms x
            JOIN group_bookings g
                ON g.id=x.group_booking_id
            WHERE x.room_id=?
              AND g.status='confirmed'
              AND g.id!=?
              AND g.checkin<?
              AND g.checkout>?
            LIMIT 1
            """,
            (
                room_id,
                gid,
                group["checkout"],
                group["checkin"]
            )
        ).fetchone()

        if not booking_exists and not group_exists:
            conn.execute(
                """
                UPDATE rooms
                SET status='available'
                WHERE id=? AND status='booked'
                """,
                (room_id,)
            )

    conn.commit()
    conn.close()

    flash("تم إلغاء حجز المجموعة.", "ok")

    return redirect(url_for("group_bookings"))


# =========================================================
# إدارة الشركات
# =========================================================

@app.route("/companies", methods=["GET", "POST"])
@login_required
def companies():
    conn = db()

    if request.method == "POST":
        name = request.form.get(
            "name",
            ""
        ).strip()

        if name:
            conn.execute(
                """
                INSERT INTO companies(
                    name,
                    contact_name,
                    phone,
                    email,
                    tax_number,
                    notes,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    request.form.get("contact_name", "").strip(),
                    request.form.get("phone", "").strip(),
                    request.form.get("email", "").strip(),
                    request.form.get("tax_number", "").strip(),
                    request.form.get("notes", "").strip(),
                    datetime.now().isoformat()
                )
            )

            conn.commit()
            flash("تمت إضافة الشركة.", "ok")

        else:
            flash("اسم الشركة مطلوب.", "error")

    rows = conn.execute(
        """
        SELECT *
        FROM companies
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "companies.html",
        companies=rows
    )


# =========================================================
# الفواتير
# =========================================================

@app.route("/invoices/<int:bid>")
@login_required
def invoice(bid):
    conn = db()

    booking = conn.execute(
        """
        SELECT
            b.*,
            cu.name AS customer,
            cu.phone AS phone,
            h.name AS hotel,
            h.city AS city,
            h.phone AS hotel_phone,
            r.number AS room,
            r.room_type AS room_type
        FROM bookings b
        JOIN customers cu ON cu.id=b.customer_id
        JOIN hotels h ON h.id=b.hotel_id
        JOIN rooms r ON r.id=b.room_id
        WHERE b.id=?
        """,
        (bid,)
    ).fetchone()

    settings_data = get_settings(conn)

    conn.close()

    if not booking:
        return "الحجز غير موجود", 404

    base_url = request.host_url.rstrip("/")

    invoice_url = (
        f"{base_url}{url_for('invoice', bid=bid)}"
    )

    whatsapp_phone = normalize_phone(
        booking["phone"]
    )

    whatsapp_url = ""

    if whatsapp_phone:
        remaining = max(
            0,
            float(booking["price"]) - float(booking["paid"])
        )

        whatsapp_text = (
            f"مرحباً {booking['customer']}، "
            f"فورتانس هوتلز: فاتورتك للحجز "
            f"{booking['code']} بقيمة "
            f"{float(booking['price']):.2f} ريال، "
            f"والمتبقي {remaining:.2f} ريال. "
            f"رابط الفاتورة: {invoice_url}"
        )

        whatsapp_url = (
            f"https://wa.me/{whatsapp_phone}"
            f"?text={quote(whatsapp_text)}"
        )

    return render_template(
        "invoice.html",
        b=booking,
        settings=settings_data,
        remaining=max(
            0,
            float(booking["price"]) - float(booking["paid"])
        ),
        invoice_url=invoice_url,
        whatsapp_url=whatsapp_url
    )
    # =========================================================
# المواسم
# =========================================================

@app.route("/seasons", methods=["GET", "POST"])
@login_required
def seasons():
    conn = db()

    if request.method == "POST":
        try:
            name = request.form.get(
                "name",
                ""
            ).strip()

            start = request.form["start_date"]
            end = request.form["end_date"]

            if not name:
                raise ValueError

            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)

            if end_date < start_date:
                raise ValueError

            conn.execute(
                """
                INSERT INTO seasons(
                    name,
                    start_date,
                    end_date,
                    notes
                )
                VALUES(?, ?, ?, ?)
                """,
                (
                    name,
                    start,
                    end,
                    request.form.get("notes", "").strip()
                )
            )

            conn.commit()
            flash("تمت إضافة الموسم.", "ok")

        except (ValueError, KeyError):
            flash(
                "تحقق من بيانات الموسم وتواريخه.",
                "error"
            )

    rows = conn.execute(
        """
        SELECT *
        FROM seasons
        ORDER BY start_date DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "seasons.html",
        seasons=rows
    )


# =========================================================
# التقارير
# =========================================================

@app.route("/reports")
@login_required
def reports():
    conn = db()

    rows = conn.execute(
        """
        SELECT
            h.name AS hotel,
            COUNT(b.id) AS bookings,

            COALESCE(
                SUM(
                    CASE
                        WHEN b.status!='cancelled'
                        THEN b.paid
                        ELSE 0
                    END
                ),
                0
            ) AS paid,

            COALESCE(
                SUM(
                    CASE
                        WHEN b.status!='cancelled'
                        THEN b.price
                        ELSE 0
                    END
                ),
                0
            ) AS total

        FROM hotels h
        LEFT JOIN bookings b
            ON b.hotel_id=h.id

        GROUP BY h.id
        ORDER BY h.name
        """
    ).fetchall()

    conn.close()

    return render_template(
        "reports.html",
        rows=rows
    )


# =========================================================
# الحسابات والمالية
# =========================================================

@app.route("/finance")
@login_required
@accountant_required
def finance():
    conn = db()

    start = request.args.get(
        "start_date",
        ""
    ).strip()

    end = request.args.get(
        "end_date",
        ""
    ).strip()

    hotel = request.args.get(
        "hotel_id",
        ""
    ).strip() or None

    summary = get_financial_summary(
        conn,
        start,
        end,
        hotel
    )

    hotels_list = conn.execute(
        """
        SELECT id, name
        FROM hotels
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    conn.close()

    return render_template(
        "finance.html",
        hotels=hotels_list,
        selected_hotel=summary["hotel_id"],
        start_date=summary["start_date"],
        end_date=summary["end_date"],
        revenue=summary["revenue"],
        expenses=summary["expenses"],
        profit=summary["profit"],
        hotel_breakdown=summary["hotel_breakdown"],
        categories=summary["categories"]
    )


# =========================================================
# تصدير تقرير الحسابات CSV
# =========================================================

@app.route("/finance/export.csv")
@login_required
@accountant_required
def export_finance_csv():
    conn = db()

    summary = get_financial_summary(
        conn,
        request.args.get("start_date"),
        request.args.get("end_date"),
        request.args.get("hotel_id") or None
    )

    conn.close()

    output = io.StringIO(newline="")
    writer = csv.writer(output)

    writer.writerow([
        "الفندق",
        "الإيرادات",
        "المصروفات",
        "صافي الربح"
    ])

    for row in summary["hotel_breakdown"]:
        writer.writerow([
            row["name"],
            row["revenue"],
            row["expenses"],
            row["profit"]
        ])

    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                "attachment; filename=finance_report.csv"
        }
    )


# =========================================================
# إعدادات النظام
# =========================================================

@app.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    conn = db()

    if request.method == "POST":
        for key in DEFAULT_SETTINGS:
            save_setting(
                conn,
                key,
                request.form.get(key, "").strip()
            )

        conn.commit()
        conn.close()

        flash(
            "تم حفظ إعدادات النظام بنجاح.",
            "ok"
        )

        return redirect(url_for("settings"))

    values = get_settings(conn)

    conn.close()

    return render_template(
        "settings.html",
        settings=values
    )


# =========================================================
# Moyasar - التحقق من الفواتير
# =========================================================

def moyasar_get_invoice(invoice_id):
    secret = os.environ.get(
        "MOYASAR_SECRET_KEY",
        ""
    ).strip()

    if not secret:
        raise RuntimeError(
            "مفتاح Moyasar السري غير مضبوط"
        )

    response = requests.get(
        f"https://api.moyasar.com/v1/invoices/{invoice_id}",
        auth=(secret, ""),
        timeout=20
    )

    response.raise_for_status()

    return response.json()


# =========================================================
# إنشاء رابط دفع للحجز
# =========================================================

@app.route("/payments/link/<int:bid>", methods=["POST"])
@login_required
def create_payment_link(bid):
    conn = db()

    try:
        booking = conn.execute(
            """
            SELECT
                b.*,
                c.name AS customer_name
            FROM bookings b
            JOIN customers c
                ON c.id=b.customer_id
            WHERE b.id=?
            """,
            (bid,)
        ).fetchone()

        if not booking or booking["status"] == "cancelled":
            flash(
                "الحجز غير موجود أو ملغى.",
                "error"
            )

            return redirect(url_for("bookings"))

        remaining = round(
            float(booking["price"])
            - float(booking["paid"]),
            2
        )

        if remaining <= 0:
            flash(
                "تم سداد قيمة الحجز بالكامل.",
                "error"
            )

            return redirect(url_for("bookings"))

        secret = os.environ.get(
            "MOYASAR_SECRET_KEY",
            ""
        ).strip()

        if not secret:
            flash(
                "أضف MOYASAR_SECRET_KEY في إعدادات البيئة أولاً.",
                "error"
            )

            return redirect(url_for("bookings"))

        old_id = booking["moyasar_invoice_id"] or ""

        if old_id:
            try:
                old_invoice = moyasar_get_invoice(old_id)

                if (
                    old_invoice.get("status") == "initiated"
                    and old_invoice.get("amount") == round(remaining * 100)
                    and old_invoice.get("url")
                ):
                    conn.execute(
                        """
                        UPDATE bookings
                        SET payment_url=?
                        WHERE id=?
                        """,
                        (old_invoice["url"], bid)
                    )

                    conn.commit()

                    flash(
                        "رابط الدفع السابق ما زال صالحاً.",
                        "ok"
                    )

                    return redirect(url_for("bookings"))

            except (
                requests.RequestException,
                ValueError,
                RuntimeError
            ):
                pass

        base_url = os.environ.get(
            "PUBLIC_BASE_URL",
            "https://fortance-hotels.onrender.com"
        ).rstrip("/")

        payload = {
            "amount": round(remaining * 100),
            "currency": "SAR",
            "description": (
                f"FORTANCE HOTELS - Booking {booking['code']}"
            ),
            "callback_url": (
                base_url + "/payments/moyasar/callback"
            ),
            "success_url": (
                base_url + "/payments/moyasar/return"
            ),
            "back_url": (
                base_url + "/bookings"
            ),
            "metadata": {
                "booking_id": str(bid),
                "booking_code": booking["code"]
            }
        }

        response = requests.post(
            "https://api.moyasar.com/v1/invoices",
            auth=(secret, ""),
            json=payload,
            timeout=25
        )

        response.raise_for_status()

        data = response.json()

        invoice_id = data.get("id")
        payment_url = data.get("url")

        if not invoice_id or not payment_url:
            raise RuntimeError(
                "بيانات فاتورة الدفع غير مكتملة"
            )

        conn.execute(
            """
            UPDATE bookings
            SET
                payment_url=?,
                moyasar_invoice_id=?,
                payment_status='pending'
            WHERE id=?
            """,
            (
                payment_url,
                invoice_id,
                bid
            )
        )

        conn.execute(
            """
            INSERT INTO payments(
                booking_id,
                amount,
                method,
                payment_url,
                status,
                reference,
                created_at,
                created_by
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bid,
                remaining,
                "moyasar",
                payment_url,
                "pending",
                invoice_id,
                datetime.now().isoformat(),
                session["uid"]
            )
        )

        conn.commit()

        flash(
            "تم إنشاء رابط الدفع بنجاح.",
            "ok"
        )

    except (
        requests.RequestException,
        RuntimeError,
        ValueError
    ):
        conn.rollback()

        app.logger.exception(
            "Moyasar invoice creation failed"
        )

        flash(
            "تعذر إنشاء رابط الدفع. تحقق من إعدادات Moyasar.",
            "error"
        )

    finally:
        conn.close()

    return redirect(url_for("bookings"))


# =========================================================
# التحقق من عملية الدفع
# =========================================================

def verify_moyasar_payment(conn, invoice_id):
    data = moyasar_get_invoice(
        str(invoice_id)
    )

    metadata = data.get("metadata") or {}

    booking_id = metadata.get("booking_id")

    if not booking_id:
        return None, None, data

    if str(data.get("id")) != str(invoice_id):
        return None, None, data

    booking = conn.execute(
        """
        SELECT *
        FROM bookings
        WHERE id=?
        """,
        (booking_id,)
    ).fetchone()

    payment = conn.execute(
        """
        SELECT *
        FROM payments
        WHERE reference=?
          AND booking_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (invoice_id, booking_id)
    ).fetchone()

    if not booking or not payment:
        return None, None, data

    if booking["moyasar_invoice_id"] != str(invoice_id):
        return None, None, data

    if (
        data.get("amount")
        != round(float(payment["amount"]) * 100)
    ):
        return None, None, data

    if data.get("currency") != "SAR":
        return None, None, data

    return booking, payment, data


# =========================================================
# تحديث حالة الدفع بعد التحقق
# =========================================================

def apply_moyasar_status(
    conn,
    booking,
    payment,
    data
):
    status = data.get("status")

    if status == "paid" and payment["status"] != "paid":
        conn.execute(
            """
            UPDATE bookings
            SET
                paid=MIN(price, paid+?),
                payment_status='paid'
            WHERE id=?
            """,
            (
                payment["amount"],
                booking["id"]
            )
        )

        conn.execute(
            """
            UPDATE payments
            SET status='paid'
            WHERE id=?
            """,
            (payment["id"],)
        )

        conn.commit()

    elif status in {
        "failed",
        "expired",
        "canceled",
        "voided"
    }:
        conn.execute(
            """
            UPDATE payments
            SET status=?
            WHERE id=? AND status='pending'
            """,
            (
                status,
                payment["id"]
            )
        )

        conn.execute(
            """
            UPDATE bookings
            SET payment_status=?
            WHERE id=?
              AND payment_status!='paid'
            """,
            (
                status,
                booking["id"]
            )
        )

        conn.commit()


# =========================================================
# استقبال إشعار Moyasar
# =========================================================

@app.route(
    "/payments/moyasar/callback",
    methods=["POST"]
)
def moyasar_callback():
    incoming = request.get_json(
        silent=True
    ) or {}

    invoice_id = incoming.get("id")

    if not invoice_id:
        return jsonify({
            "error": "missing invoice id"
        }), 400

    conn = db()

    try:
        booking, payment, data = verify_moyasar_payment(
            conn,
            invoice_id
        )

        if not booking:
            return jsonify({
                "error": "invoice not linked or payment mismatch"
            }), 400

        apply_moyasar_status(
            conn,
            booking,
            payment,
            data
        )

        return jsonify({
            "received": True
        }), 200

    except (
        requests.RequestException,
        ValueError,
        RuntimeError
    ):
        conn.rollback()

        app.logger.exception(
            "Moyasar callback verification failed"
        )

        return jsonify({
            "error": "verification failed"
        }), 502

    finally:
        conn.close()


# =========================================================
# العودة من صفحة الدفع
# =========================================================

@app.route("/payments/moyasar/return")
@login_required
def moyasar_return():
    invoice_id = request.args.get(
        "id",
        ""
    ).strip()

    if not invoice_id:
        flash(
            "عدت من صفحة الدفع. تحقق من حالة الدفع في الحجوزات.",
            "ok"
        )

        return redirect(url_for("bookings"))

    conn = db()

    try:
        booking, payment, data = verify_moyasar_payment(
            conn,
            invoice_id
        )

        if not booking:
            flash(
                "تعذر مطابقة عملية الدفع مع الحجز.",
                "error"
            )

        else:
            apply_moyasar_status(
                conn,
                booking,
                payment,
                data
            )

            if data.get("status") == "paid":
                flash(
                    "تم التحقق من الدفع بنجاح.",
                    "ok"
                )

            else:
                flash(
                    "لم يتم تأكيد الدفع بعد. حالة الفاتورة: "
                    + str(data.get("status")),
                    "error"
                )

    except (
        requests.RequestException,
        ValueError,
        RuntimeError
    ):
        app.logger.exception(
            "Moyasar return verification failed"
        )

        flash(
            "تعذر التحقق من الدفع الآن. حاول لاحقاً.",
            "error"
        )

    finally:
        conn.close()

    return redirect(url_for("bookings"))


# =========================================================
# تشغيل وتجهيز التطبيق
# =========================================================

init_db()


if __name__ == "__main__":
    app.run(
        debug=False,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000"))
    )
