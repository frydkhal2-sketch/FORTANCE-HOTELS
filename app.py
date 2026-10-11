from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
import hashlib
import secrets
from functools import wraps
from datetime import datetime


BASE = os.path.dirname(__file__)
DB = os.path.join(BASE, "fortans.db")

app = Flask(
    __name__,
    template_folder=".",
    static_folder=".",
    static_url_path="/static"
)

app.secret_key = os.environ.get(
    "FORTANCE_SECRET",
    secrets.token_hex(32)
)


ROLES = {
    "admin": "مدير النظام",
    "manager": "مدير",
    "reception": "استقبال",
    "accountant": "محاسب"
}


# =========================
# قاعدة البيانات
# =========================

def db():
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    return connection


def hash_pw(password):
    return hashlib.sha256(
        password.encode("utf-8")
    ).hexdigest()


# =========================
# الإعدادات
# =========================

DEFAULT_SETTINGS = {
    "company_name": "FORTANCE HOTELS",
    "company_phone": "",
    "company_email": "",
    "company_address": "",
    "currency": "ريال سعودي"
}


def get_settings():
    connection = db()

    rows = connection.execute(
        "SELECT key, value FROM settings"
    ).fetchall()

    connection.close()

    settings = DEFAULT_SETTINGS.copy()

    for row in rows:
        settings[row["key"]] = row["value"]

    return settings


def save_setting(key, value):
    connection = db()

    connection.execute(
        """
        INSERT INTO settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """,
        (key, value)
    )

    connection.commit()
    connection.close()


# =========================
# إنشاء قاعدة البيانات
# =========================

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
        """
    )

    # إنشاء حساب مدير النظام إذا لم يوجد
    employee_count = connection.execute(
        "SELECT COUNT(*) AS n FROM employees"
    ).fetchone()["n"]

    if employee_count == 0:
        connection.execute(
            """
            INSERT INTO employees(
                name,
                username,
                password,
                role,
                created_at
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

    # إنشاء فندق افتراضي واحد فقط إذا لم يوجد أي فندق
    hotel_count = connection.execute(
        "SELECT COUNT(*) AS n FROM hotels"
    ).fetchone()["n"]

    if hotel_count == 0:
        connection.execute(
            """
            INSERT INTO hotels(name, city, phone)
            VALUES(?,?,?)
            """,
            (
                "فندق ريان الجوار",
                "مكة المكرمة",
                ""
            )
        )

    # تنظيف الغرف التجريبية القديمة مرة واحدة
    cleanup_done = connection.execute(
        "SELECT value FROM settings WHERE key='initial_rooms_cleanup'"
    ).fetchone()

    if not cleanup_done:
        connection.execute("DELETE FROM rooms")

        connection.execute(
            """
            INSERT INTO settings(key, value)
            VALUES('initial_rooms_cleanup', 'done')
            """
        )

    connection.commit()
    connection.close()


# =========================
# الصلاحيات
# =========================

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


# =========================
# بيانات عامة للقوالب
# =========================

@app.context_processor
def ctx():
    return {
        "ROLES": ROLES,
        "user_name": session.get("name"),
        "user_role": session.get("role"),
        "system_settings": get_settings()
    }


# =========================
# الرئيسية
# =========================

@app.route("/")
def index():
    if "uid" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


# =========================
# تسجيل الدخول
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        connection = db()

        user = connection.execute(
            """
            SELECT *
            FROM employees
            WHERE username=?
            AND active=1
            """,
            (username,)
        ).fetchone()

        connection.close()

        if user and user["password"] == hash_pw(password):

            session.update(
                uid=user["id"],
                name=user["name"],
                role=user["role"]
            )

            return redirect(url_for("dashboard"))

        flash(
            "بيانات الدخول غير صحيحة.",
            "error"
        )

    return render_template("login.html")


# =========================
# تسجيل الخروج
# =========================

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================
# لوحة التحكم
# =========================

@app.route("/dashboard")
@login_required
def dashboard():

    connection = db()

    data = {
        "hotels": connection.execute(
            """
            SELECT COUNT(*) AS n
            FROM hotels
            WHERE active=1
            """
        ).fetchone()["n"],

        "rooms": connection.execute(
            """
            SELECT COUNT(*) AS n
            FROM rooms
            """
        ).fetchone()["n"],

        "booked": connection.execute(
            """
            SELECT COUNT(*) AS n
            FROM bookings
            WHERE status='confirmed'
            """
        ).fetchone()["n"],

        "revenue": connection.execute(
            """
            SELECT COALESCE(SUM(paid),0) AS n
            FROM bookings
            WHERE status!='cancelled'
            """
        ).fetchone()["n"],

        "bookings": connection.execute(
            """
            SELECT
                b.*,
                cu.name AS customer,
                h.name AS hotel,
                r.number AS room
            FROM bookings b
            JOIN customers cu ON cu.id=b.customer_id
            JOIN hotels h ON h.id=b.hotel_id
            JOIN rooms r ON r.id=b.room_id
            ORDER BY b.id DESC
            LIMIT 8
            """
        ).fetchall()
    }

    connection.close()

    data["now"] = datetime.now().strftime("%Y-%m-%d")

    return render_template(
        "dashboard.html",
        **data
    )


# =========================
# الموظفون
# =========================

@app.route("/employees", methods=["GET", "POST"])
@login_required
@admin_required
def employees():

    connection = db()

    if request.method == "POST":

        try:
            connection.execute(
                """
                INSERT INTO employees(
                    name,
                    username,
                    password,
                    role,
                    created_at
                )
                VALUES(?,?,?,?,?)
                """,
                (
                    request.form["name"],
                    request.form["username"],
                    hash_pw(request.form["password"]),
                    request.form["role"],
                    datetime.now().isoformat()
                )
            )

            connection.commit()

            flash(
                "تمت إضافة الموظف بنجاح.",
                "ok"
            )

        except sqlite3.IntegrityError:

            flash(
                "اسم المستخدم مستخدم بالفعل.",
                "error"
            )

    rows = connection.execute(
        """
        SELECT
            id,
            name,
            username,
            role,
            active,
            created_at
        FROM employees
        ORDER BY id DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "employees.html",
        employees=rows
    )


@app.route(
    "/employees/toggle/<int:eid>",
    methods=["POST"]
)
@login_required
@admin_required
def toggle_employee(eid):

    if eid == session["uid"]:

        flash(
            "لا يمكنك تعطيل حسابك الحالي.",
            "error"
        )

    else:

        connection = db()

        connection.execute(
            """
            UPDATE employees
            SET active=1-active
            WHERE id=?
            """,
            (eid,)
        )

        connection.commit()
        connection.close()

    return redirect(
        url_for("employees")
    )


# =========================
# الفنادق
# =========================

@app.route("/hotels", methods=["GET", "POST"])
@login_required
def hotels():

    connection = db()

    if request.method == "POST":

        connection.execute(
            """
            INSERT INTO hotels(
                name,
                city,
                phone,
                notes
            )
            VALUES(?,?,?,?)
            """,
            (
                request.form["name"],
                request.form["city"],
                request.form["phone"],
                request.form["notes"]
            )
        )

        connection.commit()

        flash(
            "تمت إضافة الفندق.",
            "ok"
        )

    rows = connection.execute(
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

    connection.close()

    return render_template(
        "hotels.html",
        hotels=rows
    )


# =========================
# الغرف
# =========================

@app.route("/rooms", methods=["GET", "POST"])
@login_required
def rooms():

    connection = db()

    hotels_list = connection.execute(
        """
        SELECT *
        FROM hotels
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    if request.method == "POST":

        try:

            connection.execute(
                """
                INSERT INTO rooms(
                    hotel_id,
                    number,
                    room_type,
                    price,
                    status
                )
                VALUES(?,?,?,?,?)
                """,
                (
                    request.form["hotel_id"],
                    request.form["number"],
                    request.form["room_type"],
                    request.form["price"],
                    request.form["status"]
                )
            )

            connection.commit()

            flash(
                "تمت إضافة الغرفة.",
                "ok"
            )

        except sqlite3.IntegrityError:

            flash(
                "رقم الغرفة موجود لهذا الفندق.",
                "error"
            )

    rows = connection.execute(
        """
        SELECT
            r.*,
            h.name AS hotel
        FROM rooms r
        JOIN hotels h ON h.id=r.hotel_id
        ORDER BY r.id DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "rooms.html",
        rooms=rows,
        hotels=hotels_list
    )


# =========================
# الحجوزات
# =========================

@app.route("/bookings", methods=["GET", "POST"])
@login_required
def bookings():

    connection = db()

    if request.method == "POST":

        name = request.form["customer"]
        phone = request.form["phone"]

        customer = connection.execute(
            """
            SELECT id
            FROM customers
            WHERE phone=?
            AND phone!=''
            """,
            (phone,)
        ).fetchone()

        if customer:
            customer_id = customer["id"]

        else:

            cursor = connection.execute(
                """
                INSERT INTO customers(
                    name,
                    phone,
                    notes,
                    created_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    name,
                    phone,
                    "",
                    datetime.now().isoformat()
                )
            )

            customer_id = cursor.lastrowid

        room = connection.execute(
            """
            SELECT *
            FROM rooms
            WHERE id=?
            AND status='available'
            """,
            (request.form["room_id"],)
        ).fetchone()

        if not room:

            connection.close()

            flash(
                "الغرفة غير متاحة للحجز.",
                "error"
            )

            return redirect(
                url_for("bookings")
            )

        checkin = datetime.fromisoformat(
            request.form["checkin"]
        )

        checkout = datetime.fromisoformat(
            request.form["checkout"]
        )

        nights = max(
            1,
            (checkout - checkin).days
        )

        price = float(room["price"]) * nights

        code = (
            "BK-"
            + secrets.token_hex(3).upper()
        )

        connection.execute(
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
                created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                code,
                customer_id,
                request.form["hotel_id"],
                request.form["room_id"],
                request.form["checkin"],
                request.form["checkout"],
                nights,
                price,
                float(request.form["paid"] or 0),
                "confirmed",
                session["uid"],
                datetime.now().isoformat()
            )
        )

        connection.execute(
            """
            UPDATE rooms
            SET status='booked'
            WHERE id=?
            """,
            (request.form["room_id"],)
        )

        connection.commit()
        connection.close()

        flash(
            f"تم إنشاء الحجز {code}.",
            "ok"
        )

        return redirect(
            url_for("bookings")
        )

    rows = connection.execute(
        """
        SELECT
            b.*,
            cu.name AS customer,
            cu.phone AS customer_phone,
            h.name AS hotel,
            r.number AS room,
            r.room_type AS room_type
        FROM bookings b
        JOIN customers cu ON cu.id=b.customer_id
        JOIN hotels h ON h.id=b.hotel_id
        JOIN rooms r ON r.id=b.room_id
        ORDER BY b.id DESC
        """
    ).fetchall()

    hotels_list = connection.execute(
        """
        SELECT *
        FROM hotels
        WHERE active=1
        """
    ).fetchall()

    rooms_list = connection.execute(
        """
        SELECT *
        FROM rooms
        WHERE status='available'
        """
    ).fetchall()

    connection.close()

    return render_template(
        "bookings.html",
        bookings=rows,
        hotels=hotels_list,
        rooms=rooms_list
    )


# =========================
# إلغاء الحجز
# =========================

@app.route(
    "/bookings/cancel/<int:bid>",
    methods=["POST"]
)
@login_required
def cancel_booking(bid):

    connection = db()

    booking = connection.execute(
        """
        SELECT *
        FROM bookings
        WHERE id=?
        """,
        (bid,)
    ).fetchone()

    if not booking:

        connection.close()

        flash(
            "الحجز غير موجود.",
            "error"
        )

        return redirect(
            url_for("bookings")
        )

    if booking["status"] == "cancelled":

        connection.close()

        flash(
            "الحجز ملغى بالفعل.",
            "error"
        )

        return redirect(
            url_for("bookings")
        )

    connection.execute(
        """
        UPDATE bookings
        SET status='cancelled'
        WHERE id=?
        """,
        (bid,)
    )

    # التأكد هل توجد حجوزات مؤكدة أخرى لنفس الغرفة
    other_booking = connection.execute(
        """
        SELECT id
        FROM bookings
        WHERE room_id=?
        AND status='confirmed'
        AND id!=?
        LIMIT 1
        """,
        (
            booking["room_id"],
            bid
        )
    ).fetchone()

    if not other_booking:

        connection.execute(
            """
            UPDATE rooms
            SET status='available'
            WHERE id=?
            """,
            (booking["room_id"],)
        )

    connection.commit()
    connection.close()

    flash(
        "تم إلغاء الحجز وإتاحة الغرفة.",
        "ok"
    )

    return redirect(
        url_for("bookings")
    )


# =========================
# الفاتورة
# =========================

@app.route("/invoices/<int:bid>")
@login_required
def invoice(bid):

    connection = db()

    booking = connection.execute(
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
        JOIN customers cu
            ON cu.id=b.customer_id
        JOIN hotels h
            ON h.id=b.hotel_id
        JOIN rooms r
            ON r.id=b.room_id
        WHERE b.id=?
        """,
        (bid,)
    ).fetchone()

    connection.close()

    if not booking:
        return "الحجز غير موجود", 404

    settings = get_settings()

    remaining = (
        float(booking["price"])
        - float(booking["paid"])
    )

    return render_template(
        "invoice.html",
        b=booking,
        settings=settings,
        remaining=remaining
    )


# =========================
# الإعدادات
# =========================

@app.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():

    settings_data = get_settings()

    if request.method == "POST":

        save_setting(
            "company_name",
            request.form.get(
                "company_name",
                ""
            ).strip()
        )

        save_setting(
            "company_phone",
            request.form.get(
                "company_phone",
                ""
            ).strip()
        )

        save_setting(
            "company_email",
            request.form.get(
                "company_email",
                ""
            ).strip()
        )

        save_setting(
            "company_address",
            request.form.get(
                "company_address",
                ""
            ).strip()
        )

        save_setting(
            "currency",
            request.form.get(
                "currency",
                "ريال سعودي"
            ).strip()
        )

        flash(
            "تم حفظ إعدادات النظام بنجاح.",
            "ok"
        )

        return redirect(
            url_for("settings")
        )

    return render_template(
        "settings.html",
        settings=settings_data
    )


# =========================
# المواسم
# =========================

@app.route("/seasons", methods=["GET", "POST"])
@login_required
def seasons():

    connection = db()

    if request.method == "POST":

        connection.execute(
            """
            INSERT INTO seasons(
                name,
                start_date,
                end_date,
                notes
            )
            VALUES(?,?,?,?)
            """,
            (
                request.form["name"],
                request.form["start_date"],
                request.form["end_date"],
                request.form["notes"]
            )
        )

        connection.commit()

        flash(
            "تمت إضافة الموسم.",
            "ok"
        )

    rows = connection.execute(
        """
        SELECT *
        FROM seasons
        ORDER BY start_date DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "seasons.html",
        seasons=rows
    )


# =========================
# التقارير
# =========================

@app.route("/reports")
@login_required
def reports():

    connection = db()

    rows = connection.execute(
        """
        SELECT
            h.name AS hotel,
            COUNT(
                CASE
                    WHEN b.status='confirmed'
                    THEN b.id
                END
            ) AS bookings,
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
        """
    ).fetchall()

    connection.close()

    return render_template(
        "reports.html",
        rows=rows
    )


# =========================
# تشغيل قاعدة البيانات
# =========================

init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
