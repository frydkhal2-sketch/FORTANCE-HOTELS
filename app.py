import csv
import io
import os
import sqlite3
import hashlib
import secrets
import requests

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

    ensure_column(connection, "bookings", "payment_method", "TEXT DEFAULT 'cash'")
    ensure_column(connection, "bookings", "payment_url", "TEXT DEFAULT ''")
    ensure_column(connection, "bookings", "company_id", "INTEGER")
    ensure_column(connection, "bookings", "group_booking_id", "INTEGER")
    ensure_column(connection, "bookings", "moyasar_invoice_id", "TEXT DEFAULT ''")
    ensure_column(connection, "bookings", "payment_status", "TEXT DEFAULT 'pending'")

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

    connection.execute(
        """
        INSERT INTO settings(key, value)
        VALUES('initial_rooms_cleanup', 'done')
        ON CONFLICT(key) DO NOTHING
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
            flash("هذه الصفحة متاحة لمدير النظام فقط.", "error")
            return redirect(url_for("dashboard"))
        return function(*args, **kwargs)
    return wrapper


def accountant_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if session.get("role") not in {"admin", "accountant"}:
            flash("هذه الصفحة متاحة لمدير النظام أو المحاسب فقط.", "error")
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
    bookings_revenue = connection.execute(bookings_revenue_q, params).fetchone()["total"]

    group_revenue_q = f"""
        SELECT COALESCE(SUM(paid), 0) AS total
        FROM group_bookings
        WHERE status != 'cancelled'
          AND substr(created_at, 1, 10) BETWEEN ? AND ?
        {hotel_filter}
    """
    group_revenue = connection.execute(group_revenue_q, params).fetchone()["total"]

    expenses_q = """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE substr(expense_date, 1, 10) BETWEEN ? AND ?
    """
    expense_params = [start_date, end_date]
    if hotel_id:
        expenses_q += " AND hotel_id=?"
        expense_params.append(hotel_id)
    expenses_total = connection.execute(expenses_q, expense_params).fetchone()["total"]

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
        if hotel_id and str(row["id"]) != str(hotel_id):
            continue
        revenue = float(row["booking_revenue"]) + float(row["group_revenue"])
        expense = float(row["expenses"])
        hotel_breakdown.append({
            "id": row["id"],
            "name": row["name"],
            "revenue": revenue,
            "expenses": expense,
            "profit": revenue - expense,
        })

    category_q = """
        SELECT category, COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE substr(expense_date, 1, 10) BETWEEN ? AND ?
    """
    category_params = [start_date, end_date]
    if hotel_id:
        category_q += " AND hotel_id=?"
        category_params.append(hotel_id)
    category_q += " GROUP BY category ORDER BY total DESC"

    categories = connection.execute(category_q, category_params).fetchall()

    return {
        "start_date": start_date,
        "end_date": end_date,
        "hotel_id": hotel_id or "",
        "revenue": total_revenue,
        "expenses": float(expenses_total),
        "profit": profit,
        "hotel_breakdown": hotel_breakdown,
        "categories": [dict(row) for row in categories],
    }


@app.context_processor
def ctx():
    return {
        "ROLES": ROLES,
        "user_name": session.get("name"),
        "user_role": session.get("role"),
        "system_settings": get_settings(),
    }


@app.before_request
def ensure_valid_session():
    if "uid" not in session:
        return None

    connection = db()
    try:
        user = connection.execute(
            "SELECT active FROM employees WHERE id=?",
            (session["uid"],),
        ).fetchone()
    finally:
        connection.close()

    if user is None or user["active"] != 1:
        session.clear()
        flash("تم إلغاء تفعيل حسابك. سجل الدخول مرة أخرى.", "error")
        return redirect(url_for("login"))

    return None


def room_conflict(connection, room_id, checkin, checkout, exclude_booking_id=None):
    sql = """
        SELECT id
        FROM bookings
        WHERE room_id=?
          AND status='confirmed'
          AND checkin < ?
          AND checkout > ?
    """
    params = [room_id, checkout, checkin]

    if exclude_booking_id is not None:
        sql += " AND id != ?"
        params.append(exclude_booking_id)

    return connection.execute(sql, params).fetchone() is not None


def group_room_conflict(connection, room_id, checkin, checkout):
    return connection.execute(
        """
        SELECT gbr.id
        FROM group_booking_rooms gbr
        JOIN group_bookings gb
          ON gb.id=gbr.group_booking_id
        WHERE gbr.room_id=?
          AND gb.status='confirmed'
          AND gb.checkin < ?
          AND gb.checkout > ?
        LIMIT 1
        """,
        (room_id, checkout, checkin),
    ).fetchone() is not None


@app.route("/")
def index():
    if "uid" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


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
            (username,),
        ).fetchone()
        connection.close()

        if user and user["password"] == hash_pw(password):
            session.update(uid=user["id"], name=user["name"], role=user["role"])
            return redirect(url_for("dashboard"))

        flash("بيانات الدخول غير صحيحة.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/finance")
@login_required
@accountant_required
def finance():
    connection = db()

    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    hotel_id = request.args.get("hotel_id", "").strip() or None

    if not start_date and not end_date:
        first_day = date.today().replace(day=1).isoformat()
        end_day = date.today().isoformat()
        start_date = first_day
        end_date = end_day

    summary = get_financial_summary(
        connection,
        start_date=start_date,
        end_date=end_date,
        hotel_id=hotel_id,
    )

    hotels_list = connection.execute(
        """
        SELECT id, name
        FROM hotels
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()
    connection.close()

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
        categories=summary["categories"],
    )


@app.route("/finance/export.csv")
@login_required
@accountant_required
def export_finance_csv():
    connection = db()

    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    hotel_id = request.args.get("hotel_id", "").strip() or None

    if not start_date and not end_date:
        first_day = date.today().replace(day=1).isoformat()
        end_day = date.today().isoformat()
        start_date = first_day
        end_date = end_day

    summary = get_financial_summary(
        connection,
        start_date=start_date,
        end_date=end_date,
        hotel_id=hotel_id,
    )

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["تقرير مالي", "من تاريخ", summary["start_date"], "إلى تاريخ", summary["end_date"]])
    writer.writerow([])
    writer.writerow(["إجمالي الإيرادات", summary["revenue"]])
    writer.writerow(["إجمالي المصروفات", summary["expenses"]])
    writer.writerow(["صافي الربح", summary["profit"]])
    writer.writerow([])
    writer.writerow(["الفندق", "الإيرادات", "المصروفات", "صافي الربح"])

    for row in summary["hotel_breakdown"]:
        writer.writerow([row["name"], row["revenue"], row["expenses"], row["profit"]])

    writer.writerow([])
    writer.writerow(["تصنيف المصروف", "الإجمالي"])
    for row in summary["categories"]:
        writer.writerow([row["category"], row["total"]])

    connection.close()
    csv_content = "\ufeff" + buffer.getvalue()
    return Response(
        csv_content,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=fortance_finance.csv"},
    )


@app.route("/dashboard")
@login_required
def dashboard():
    connection = db()

    data = {
        "hotels": connection.execute(
            "SELECT COUNT(*) AS n FROM hotels WHERE active=1"
        ).fetchone()["n"],
        "rooms": connection.execute(
            """
            SELECT COUNT(*) AS n
            FROM rooms r
            JOIN hotels h ON h.id=r.hotel_id
            WHERE h.active=1
            """
        ).fetchone()["n"],
        "booked": connection.execute(
            """
            SELECT COUNT(*) AS n
            FROM bookings
            WHERE status='confirmed'
            """
        ).fetchone()["n"],
        "group_bookings": connection.execute(
            """
            SELECT COUNT(*) AS n
            FROM group_bookings
            WHERE status='confirmed'
            """
        ).fetchone()["n"],
        "revenue": connection.execute(
            """
            SELECT
                COALESCE((
                    SELECT SUM(paid)
                    FROM bookings
                    WHERE status!='cancelled'
                ), 0) +
                COALESCE((
                    SELECT SUM(paid)
                    FROM group_bookings
                    WHERE status!='cancelled'
                ), 0) AS total
            """
        ).fetchone()["total"],
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
        ).fetchall(),
    }

    connection.close()
    data["now"] = date.today().isoformat()
    return render_template("dashboard.html", **data)


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
                    name, username, password, role, created_at
                )
                VALUES(?,?,?,?,?)
                """,
                (
                    request.form["name"].strip(),
                    request.form["username"].strip(),
                    hash_pw(request.form["password"]),
                    request.form["role"],
                    datetime.now().isoformat(),
                ),
            )
            connection.commit()
            flash("تمت إضافة الموظف.", "ok")
        except sqlite3.IntegrityError:
            flash("اسم المستخدم مستخدم بالفعل.", "error")

    rows = connection.execute(
        """
        SELECT id,name,username,role,active,created_at
        FROM employees
        ORDER BY id DESC
        """
    ).fetchall()
    connection.close()
    return render_template("employees.html", employees=rows)


@app.route("/employees/toggle/<int:eid>", methods=["POST"])
@login_required
@admin_required
def toggle_employee(eid):
    if eid == session["uid"]:
        flash("لا يمكنك تعطيل حسابك الحالي.", "error")
    else:
        connection = db()
        connection.execute(
            """
            UPDATE employees
            SET active=1-active
            WHERE id=?
            """,
            (eid,),
        )
        connection.commit()
        connection.close()
    return redirect(url_for("employees"))


@app.route("/hotels", methods=["GET", "POST"])
@login_required
def hotels():
    connection = db()

    if request.method == "POST":
        connection.execute(
            """
            INSERT INTO hotels(name,city,phone,notes)
            VALUES(?,?,?,?)
            """,
            (
                request.form["name"].strip(),
                request.form.get("city", "").strip(),
                request.form.get("phone", "").strip(),
                request.form.get("notes", "").strip(),
            ),
        )
        connection.commit()
        flash("تمت إضافة الفندق.", "ok")

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
    return render_template("hotels.html", hotels=rows)


def get_room_status(room_id, today, bookings):
    for booking in bookings:
        if booking["room_id"] != room_id:
            continue

        if booking["checkin"] <= today < booking["checkout"]:
            return "occupied"
        if booking["checkout"] == today:
            return "checkout_today"
        if booking["checkin"] > today:
            return "upcoming"

    return "vacant"


@app.route("/rooms", methods=["GET", "POST"])
@login_required
def rooms():
    connection = db()
    today = date.today().isoformat()
    selected_hotel = request.args.get("hotel_id", "")

    if request.method == "POST":
        action = request.form.get("action", "single")
        hotel_id = request.form.get("hotel_id", "").strip()
        room_type = request.form.get("room_type", "").strip()
        status = request.form.get("status", "available")

        if action == "bulk":
            try:
                start = int(request.form["start_number"])
                end = int(request.form["end_number"])
                price = float(request.form.get("price", "0") or 0)

                if (end < start or start < 0 or end - start > 500 or price < 0 or not hotel_id):
                    raise ValueError

                created = 0
                for number in range(start, end + 1):
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
                                hotel_id,
                                str(number),
                                room_type,
                                price,
                                status,
                            ),
                        )
                        created += 1
                    except sqlite3.IntegrityError:
                        pass

                connection.commit()
                flash(f"تم إنشاء {created} غرفة بنجاح.", "ok")
            except (ValueError, TypeError):
                flash("بيانات نطاق الغرف غير صحيحة.", "error")

            connection.close()
            return redirect(url_for("rooms", hotel_id=hotel_id))

        number = request.form.get("number", "").strip()
        try:
            price = float(request.form.get("price", "0") or 0)
            if (not hotel_id or not number or price < 0):
                raise ValueError

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
                    hotel_id,
                    number,
                    room_type,
                    price,
                    status,
                ),
            )
            connection.commit()
            flash("تمت إضافة الغرفة بنجاح.", "ok")
        except (ValueError, sqlite3.IntegrityError):
            flash("تعذر إضافة الغرفة. تأكد من البيانات ورقم الغرفة.", "error")

        connection.close()
        return redirect(url_for("rooms", hotel_id=hotel_id))

    hotels_list = connection.execute(
        """
        SELECT id,name
        FROM hotels
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    query = """
        SELECT r.*, h.name AS hotel_name
        FROM rooms r
        JOIN hotels h ON h.id=r.hotel_id
    """
    params = []
    if selected_hotel:
        query += " WHERE r.hotel_id=?"
        params.append(selected_hotel)
    query += " ORDER BY h.name, r.number"

    rooms_list = connection.execute(query, params).fetchall()

    bookings = connection.execute(
        """
        SELECT room_id, checkin, checkout
        FROM bookings
        WHERE status='confirmed'
          AND checkout >= ?
        ORDER BY checkin
        """,
        (today,),
    ).fetchall()

    group_bookings = connection.execute(
        """
        SELECT gbr.room_id, gb.checkin, gb.checkout
        FROM group_booking_rooms gbr
        JOIN group_bookings gb
          ON gb.id=gbr.group_booking_id
        WHERE gb.status='confirmed'
          AND gb.checkout >= ?
        ORDER BY gb.checkin
        """,
        (today,),
    ).fetchall()

    all_bookings = list(bookings) + list(group_bookings)
    room_rows = []
    for room in rooms_list:
        item = dict(room)
        stored_status = item.get("status", "available")
        if stored_status in ("available", "booked"):
            item["display_status"] = get_room_status(room["id"], today, all_bookings)
        else:
            item["display_status"] = stored_status
        room_rows.append(item)

    connection.close()
    return render_template(
        "rooms.html",
        rooms=room_rows,
        hotels=hotels_list,
        selected_hotel=selected_hotel,
    )


if __name__ == "__main__":
    app.run(debug=True)
