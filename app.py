from flask import jsonify
from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
import hashlib
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
        expense = float(row["expenses"])
        hotel_breakdown.append({
            "id": row["id"],
            "name": row["name"],
            "revenue": revenue,
            "expenses": expense,
            "profit": revenue - expense
        })

    category_q = """
        SELECT category, COALESCE(SUM(amount), 0) AS total
        FROM expenses
        WHERE substr(expense_date, 1, 10) BETWEEN ? AND ?
        GROUP BY category
        ORDER BY total DESC
    """
    category_params = [start_date, end_date]
    if hotel_id:
        category_q += " AND hotel_id=?"
        category_params.append(hotel_id)

    categories = connection.execute(
        category_q,
        category_params
    ).fetchall()

    return {
        "start_date": start_date,
        "end_date": end_date,
        "hotel_id": hotel_id or "",
        "revenue": total_revenue,
        "expenses": float(expenses_total),
        "profit": profit,
        "hotel_breakdown": hotel_breakdown,
        "categories": [dict(row) for row in categories]
    }

@app.context_processor
def ctx():
    return {
        "ROLES": ROLES,
        "user_name": session.get("name"),
        "user_role": session.get("role"),
        "system_settings": get_settings()
  checkin]

    if exclude_booking_id is not None:
        sql += " AND id != ?"
        params.append(exclude_booking_id)

    return connection.execute(sql, params).fetchone() is not None


def group_room_conflict(
    connection,
    room_id,
    checkin,
    checkout
):
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
        (room_id, checkout, checkin)
    ).fetchone() is not None


@app.route("/")
def index():
    if "uid" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get(
            "username", ""
        ).strip()

        password = request.form.get(
            "password", ""
        )

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


@app.route("/logout")
def logout():   }
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
        hotel_id=hotel_id
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
        categories=summary["categories"]
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
        hotel_id=hotel_id
    )

    output = []
    output.append("hotel,revenue,expenses,profit")

    for row in summary["hotel_breakdown"]:
        output.append(
            f"{row['name']},{row['revenue']},{row['expenses']},{row['profit']}"
        )

    connection.close()

    return (
        "\n".join(output),
        200,
        {
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": "attachment; filename=finance_report.csv"
        }
    )

def room_conflict(
    connection,
    room_id,
    checkin,
    checkout,
    exclude_booking_id=None
):
    sql = """
        SELECT id
        FROM bookings
        WHERE room_id=?
          AND status='confirmed'
          AND checkin < ?
          AND checkout > ?
    """

    params = [room_id, checkout,
    session.clear()
    return redirect(url_for("login"))


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

    data["now"] = date.today().isoformat()

    return render_template(
        "dashboard.html",
        **data
    )


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
                    datetime.now().isoformat()
                )
            )

            connection.commit()

            flash(
                "تمت إضافة الموظف.",
                "ok"
            )

        except sqlite3.IntegrityError:
            flash(
                "اسم المستخدم مستخدم بالفعل.",
                "error"
            )

    rows = connection.execute(
        """
        SELECT id,name,username,role,active,created_at
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
                request.form.get("notes", "").strip()
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
    selected_hotel = request.args.get(
        "hotel_id", ""
    )

    if request.method == "POST":
        action = request.form.get(
            "action", "single"
        )

        hotel_id = request.form.get(
            "hotel_id", ""
        ).strip()

        room_type = request.form.get(
            "room_type", ""
        ).strip()

        status = request.form.get(
            "status", "available"
        )

        if action == "bulk":
            try:
                start = int(
                    request.form["start_number"]
                )

                end = int(
                    request.form["end_number"]
                )

                price = float(
                    request.form.get(
                        "price", "0"
                    ) or 0
                )

                if (
                    end < start
                    or end - start > 1000
                    or price < 0
                    or not hotel_id
                ):
                    raise ValueError

                created = 0

                for number in range(
                    start,
                    end + 1
                ):
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
                                status
                            )
                        )

                        created += 1

                    except sqlite3.IntegrityError:
                        pass

                connection.commit()

                flash(
                    f"تم إنشاء {created} غرفة بنجاح.",
                    "ok"
                )

            except (
                ValueError,
                TypeError
            ):
                flash(
                    "بيانات نطاق الغرف غير صحيحة.",
                    "error"
                )

            connection.close()

            return redirect(
                url_for(
                    "rooms",
                    hotel_id=hotel_id
                )
            )

        number = request.form.get(
            "number", ""
        ).strip()

        try:
            price = float(
                request.form.get(
                    "price", "0"
                ) or 0
            )

            if (
                not hotel_id
                or not number
                or price < 0
            ):
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
                    status
                )
            )

            connection.commit()

            flash(
                "تمت إضافة الغرفة بنجاح.",
                "ok"
            )

        except (
            ValueError,
            sqlite3.IntegrityError
        ):
            flash(
                "تعذر إضافة الغرفة. "
                "تأكد من البيانات ورقم الغرفة.",
                "error"
            )

        connection.close()

        return redirect(
            url_for(
                "rooms",
                hotel_id=hotel_id
            )
        )

    hotels_list = connection.execute(
        """
        SELECT id,name
        FROM hotels
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    room_rows = connection.execute(
        """
        SELECT
            r.*,
            h.name AS hotel_name
        FROM rooms r
        JOIN hotels h
            ON h.id=r.hotel_id
        WHERE (?='' OR r.hotel_id=?)
        ORDER BY h.name,r.number
        """,
        (
            selected_hotel,
            selected_hotel
        )
    ).fetchall()

    booking_rows = connection.execute(
        """
        SELECT
            room_id,
            checkin,
            checkout,
            status
        FROM bookings
        WHERE checkout>=?
        AND status='confirmed'
        """,
        (today,)
    ).fetchall()

    group_rows = connection.execute(
        """
        SELECT
            gbr.room_id,
            gb.checkin,
            gb.checkout,
            gb.status
        FROM group_booking_rooms gbr
        JOIN group_bookings gb
            ON gb.id=gbr.group_booking_id
        WHERE gb.checkout>=?
        AND gb.status='confirmed'
        """,
        (today,)
    ).fetchall()

    all_bookings = [
        dict(row)
        for row in booking_rows
    ] + [
        dict(row)
        for row in group_rows
    ]

    room_list = []

    for room in room_rows:
        item = dict(room)

        item["room_status"] = item["status"]

        if item["status"] == "available":
            item["room_status"] = get_room_status(
                item["id"],
                today,
                all_bookings
            )

        room_list.append(item)

    counts = {
        "total": len(room_list),
        "vacant": sum(
            x["room_status"] == "vacant"
            for x in room_list
        ),
        "occupied": sum(
            x["room_status"] == "occupied"
            for x in room_list
        ),
        "checkout_today": sum(
            x["room_status"] == "checkout_today"
            for x in room_list
        ),
        "upcoming": sum(
            x["room_status"] == "upcoming"
            for x in room_list
        ),
        "maintenance": sum(
            x["room_status"] == "maintenance"
            for x in room_list
        ),
        "out_of_service": sum(
            x["room_status"] == "out_of_service"
            for x in room_list
        ),
        "cleaning": sum(
            x["room_status"] == "cleaning"
            for x in room_list
        )
    }

    connection.close()

    return render_template(
        "rooms.html",
        hotels=hotels_list,
        rooms=room_list,
        counts=counts,
        selected_hotel=selected_hotel,
        today=today
    )


@app.route(
    "/rooms/status/<int:rid>",
    methods=["POST"]
)
@login_required
def room_status(rid):
    status = request.form.get(
        "status",
        "available"
    )

    allowed = {
        "available",
        "maintenance",
        "out_of_service",
        "cleaning"
    }

    if status not in allowed:
        flash(
            "حالة الغرفة غير صحيحة.",
            "error"
        )

        return redirect(
            url_for("rooms")
        )

    connection = db()

    connection.execute(
        """
        UPDATE rooms
        SET status=?
        WHERE id=?
        """,
        (status, rid)
    )

    connection.commit()
    connection.close()

    flash(
        "تم تحديث حالة الغرفة.",
        "ok"
    )

    return redirect(
        url_for("rooms")
    )


@app.route(
    "/bookings",
    methods=["GET", "POST"]
)
@login_required
def bookings():
    connection = db()

    if request.method == "POST":
        name = request.form.get(
            "customer", ""
        ).strip()

        phone = request.form.get(
            "phone", ""
        ).strip()

        hotel_id = request.form.get(
            "hotel_id"
        )

        room_id = request.form.get(
            "room_id"
        )

        checkin = request.form.get(
            "checkin", ""
        )

        checkout = request.form.get(
            "checkout", ""
        )

        try:
            ci = datetime.fromisoformat(
                checkin
            )

            co = datetime.fromisoformat(
                checkout
            )

            nights = (
                co - ci
            ).days

            if nights <= 0:
                raise ValueError

        except ValueError:
            connection.close()

            flash(
                "تاريخ الخروج يجب أن يكون "
                "بعد تاريخ الدخول.",
                "error"
            )

            return redirect(
                url_for("bookings")
            )

        room = connection.execute(
            """
            SELECT *
            FROM rooms
            WHERE id=?
            AND hotel_id=?
            """,
            (
                room_id,
                hotel_id
            )
        ).fetchone()

        if not room:
            connection.close()

            flash(
                "الغرفة غير موجودة في الفندق المحدد.",
                "error"
            )

            return redirect(
                url_for("bookings")
            )

        if room["status"] in {
            "maintenance",
            "out_of_service",
            "cleaning"
        }:
            connection.close()

            flash(
                "الغرفة غير متاحة بسبب حالتها الحالية.",
                "error"
            )

            return redirect(
                url_for("bookings")
            )

        if (
            room_conflict(
                connection,
                room_id,
                checkin,
                checkout
            )
            or group_room_conflict(
                connection,
                room_id,
                checkin,
                checkout
            )
        ):
            connection.close()

            flash(
                "هذه الغرفة محجوزة في الفترة "
                "المحددة. اختر غرفة أو تاريخًا آخر.",
                "error"
            )

            return redirect(
                url_for("bookings")
            )

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

            connection.execute(
                """
                UPDATE customers
                SET name=?
                WHERE id=?
                """,
                (
                    name,
                    customer_id
                )
            )

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

        price = (
            float(room["price"])
            * nights
        )

        paid = max(
            0,
            float(
                request.form.get(
                    "paid", "0"
                ) or 0
            )
        )

        paid = min(
            paid,
            price
        )

        code = (
            "BK-"
            + secrets.token_hex(3).upper()
        )

        payment_method = request.form.get(
            "payment_method",
            "cash"
        )

        payment_url = request.form.get(
            "payment_url",
            ""
        ).strip()

        cursor = connection.execute(
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
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                payment_method,
                payment_url
            )
        )

        booking_id = cursor.lastrowid

        connection.execute(
            """
            UPDATE rooms
            SET status='booked'
            WHERE id=?
            """,
            (room_id,)
        )

        if paid > 0:
            connection.execute(
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
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    booking_id,
                    paid,
                    payment_method,
                    payment_url,
                    "paid",
                    datetime.now().isoformat(),
                    session["uid"]
                )
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
        JOIN customers cu
            ON cu.id=b.customer_id
        JOIN hotels h
            ON h.id=b.hotel_id
        JOIN rooms r
            ON r.id=b.room_id
        ORDER BY b.id DESC
        """
    ).fetchall()

    hotels_list = connection.execute(
        """
        SELECT *
        FROM hotels
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    rooms_list = connection.execute(
        """
        SELECT *
        FROM rooms
        WHERE status NOT IN(
            'maintenance',
            'out_of_service',
            'cleaning'
        )
        ORDER BY hotel_id,number
        """
    ).fetchall()

    connection.close()

    return render_template(
        "bookings.html",
        bookings=rows,
        hotels=hotels_list,
        rooms=rooms_list
    )


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

    connection.execute(
        """
        UPDATE rooms
        SET status='available'
        WHERE id=?
        AND status='booked'
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

@app.route("/group-bookings", methods=["GET", "POST"])
@login_required
def group_bookings():
    connection = db()


    if request.method == "POST":
        group_name = request.form.get(
            "group_name", ""
        ).strip()

        contact_name = request.form.get(
            "contact_name", ""
        ).strip()

        phone = request.form.get(
            "phone", ""
        ).strip()

        company_id = (
            request.form.get(
                "company_id"
            )
            or None
        )

        hotel_id = request.form.get(
            "hotel_id"
        )

        checkin = request.form.get(
            "checkin", ""
        )

        checkout = request.form.get(
            "checkout", ""
        )

        try:
            guests = int(
                request.form.get(
                    "guests",
                    "0"
                ) or 0
            )
        except ValueError:
            guests = 0

        notes = request.form.get(
            "notes", ""
        ).strip()

        room_ids = [
            x
            for x in request.form.getlist(
                "room_ids"
            )
            if x
        ]

        try:
            ci = datetime.fromisoformat(
                checkin
            )

            co = datetime.fromisoformat(
                checkout
            )

            nights = (
                co - ci
            ).days

            if (
                nights <= 0
                or not group_name
                or not room_ids
            ):
                raise ValueError

        except ValueError:
            connection.close()

            flash(
                "أدخل اسم المجموعة والتواريخ "
                "واختر غرفة واحدة على الأقل.",
                "error"
            )

            return redirect(
                url_for("group_bookings")
            )

        valid_rooms = []
        total = 0

        for rid in room_ids:
            room = connection.execute(
                """
                SELECT *
                FROM rooms
                WHERE id=?
                AND hotel_id=?
                """,
                (
                    rid,
                    hotel_id
                )
            ).fetchone()

            if not room:
                connection.close()

                flash(
                    "إحدى الغرف غير موجودة.",
                    "error"
                )

                return redirect(
                    url_for("group_bookings")
                )

            if room["status"] in {
                "maintenance",
                "out_of_service",
                "cleaning"
            }:
                connection.close()

                flash(
                    "إحدى الغرف غير متاحة.",
                    "error"
                )

                return redirect(
                    url_for("group_bookings")
                )

            if (
                room_conflict(
                    connection,
                    rid,
                    checkin,
                    checkout
                )
                or group_room_conflict(
                    connection,
                    rid,
                    checkin,
                    checkout
                )
            ):
                connection.close()

                flash(
                    f"الغرفة {room['number']} "
                    "متعارضة مع حجز آخر.",
                    "error"
                )

                return redirect(
                    url_for("group_bookings")
                )

            valid_rooms.append(room)

            total += (
                float(room["price"])
                * nights
            )

        paid = max(
            0,
            min(
                float(
                    request.form.get(
                        "paid",
                        "0"
                    ) or 0
                ),
                total
            )
        )

        code = (
            "GR-"
            + secrets.token_hex(3).upper()
        )

        cursor = connection.execute(
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
                created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                code,
                group_name,
                contact_name,
                phone,
                company_id,
                hotel_id,
                checkin,
                checkout,
                guests,
                len(valid_rooms),
                total,
                paid,
                "confirmed",
                notes,
                session["uid"],
                datetime.now().isoformat()
            )
        )

        group_id = cursor.lastrowid

        for room in valid_rooms:
            connection.execute(
                """
                INSERT INTO group_booking_rooms(
                    group_booking_id,
                    room_id,
                    price
                )
                VALUES(?,?,?)
                """,
                (
                    group_id,
                    room["id"],
                    float(room["price"]) * nights
                )
            )

            connection.execute(
                """
                UPDATE rooms
                SET status='booked'
                WHERE id=?
                """,
                (room["id"],)
            )

        if paid > 0:
            connection.execute(
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
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    group_id,
                    paid,
                    request.form.get(
                        "payment_method",
                        "cash"
                    ),
                    request.form.get(
                        "payment_url",
                        ""
                    ),
                    "paid",
                    datetime.now().isoformat(),
                    session["uid"]
                )
            )

        connection.commit()
        connection.close()

        flash(
            f"تم إنشاء حجز المجموعة {code} "
            f"لعدد {len(valid_rooms)} غرفة.",
            "ok"
        )

        return redirect(
            url_for("group_bookings")
        )

    rows = connection.execute(
        """
        SELECT
            gb.*,
            h.name AS hotel,
            c.name AS company
        FROM group_bookings gb
        JOIN hotels h
            ON h.id=gb.hotel_id
        LEFT JOIN companies c
            ON c.id=gb.company_id
        ORDER BY gb.id DESC
        """
    ).fetchall()

    hotels_list = connection.execute(
        """
        SELECT *
        FROM hotels
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    companies_list = connection.execute(
        """
        SELECT *
        FROM companies
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    rooms_list = connection.execute(
        """
        SELECT
            r.*,
            h.name AS hotel
        FROM rooms r
        JOIN hotels h
            ON h.id=r.hotel_id
        WHERE r.status NOT IN(
            'maintenance',
            'out_of_service',
            'cleaning'
        )
        ORDER BY h.name,r.number
        """
    ).fetchall()

    connection.close()

    return render_template(
        "group_bookings.html",
        group_bookings=rows,
        hotels=hotels_list,
        companies=companies_list,
        rooms=rooms_list
    )


@app.route(
    "/group-bookings/cancel/<int:gid>",
    methods=["POST"]
)
@login_required
def cancel_group_booking(gid):
    connection = db()

    group = connection.execute(
        """
        SELECT *
        FROM group_bookings
        WHERE id=?
        """,
        (gid,)
    ).fetchone()

    if not group:
        connection.close()

        flash(
            "حجز المجموعة غير موجود.",
            "error"
        )

        return redirect(
            url_for("group_bookings")
        )

    connection.execute(
        """
        UPDATE group_bookings
        SET status='cancelled'
        WHERE id=?
        """,
        (gid,)
    )

    rooms = connection.execute(
        """
        SELECT room_id
        FROM group_booking_rooms
        WHERE group_booking_id=?
        """,
        (gid,)
    ).fetchall()

    for row in rooms:
        rid = row["room_id"]

        still_used = connection.execute(
            """
            SELECT 1
            FROM bookings
            WHERE room_id=?
            AND status='confirmed'
            AND checkin < ?
            AND checkout > ?
            LIMIT 1
            """,
            (
                rid,
                group["checkout"],
                group["checkin"]
            )
        ).fetchone()

        if not still_used:
            connection.execute(
                """
                UPDATE rooms
                SET status='available'
                WHERE id=?
                AND status='booked'
                """,
                (rid,)
            )

    connection.commit()
    connection.close()

    flash(
        "تم إلغاء حجز المجموعة.",
        "ok"
    )

    return redirect(
        url_for("group_bookings")
    )


@app.route(
    "/companies",
    methods=["GET", "POST"]
)
@login_required
def companies():
    connection = db()

    if request.method == "POST":
        connection.execute(
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
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                request.form["name"].strip(),
                request.form.get(
                    "contact_name",
                    ""
                ).strip(),
                request.form.get(
                    "phone",
                    ""
                ).strip(),
                request.form.get(
                    "email",
                    ""
                ).strip(),
                request.form.get(
                    "tax_number",
                    ""
                ).strip(),
                request.form.get(
                    "notes",
                    ""
                ).strip(),
                datetime.now().isoformat()
            )
        )

        connection.commit()

        flash(
            "تمت إضافة الشركة.",
            "ok"
        )

    rows = connection.execute(
        """
        SELECT *
        FROM companies
        ORDER BY id DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "companies.html",
        companies=rows
    )


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

    settings = get_settings(connection)

    connection.close()

    if not booking:
        return "الحجز غير موجود", 404

    remaining = max(
        0,
        float(booking["price"])
        - float(booking["paid"])
    )

    return render_template(
        "invoice.html",
        b=booking,
        settings=settings,
        remaining=remaining
    )


@app.route(
    "/seasons",
    methods=["GET", "POST"]
)
@login_required
def seasons():
    connection = db()

    if request.method == "POST":
        connection.execute(
            """
            INSERT INTO seasons(
                name,start_date,end_date,notes
            )
            VALUES(?,?,?,?)
            """,
            (
                request.form["name"],
                request.form["start_date"],
                request.form["end_date"],
                request.form.get(
                    "notes",
                    ""
                )
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


@app.route("/reports")
@login_required
def reports():
    connection = db()

    rows = connection.execute(
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

    connection.close()

    return render_template(
        "reports.html",
        rows=rows
    )


@app.route(
    "/settings",
    methods=["GET", "POST"]
)
@login_required
@admin_required
def settings():
    connection = db()

    if request.method == "POST":
        for key in DEFAULT_SETTINGS:
            save_setting(
                connection,
                key,
                request.form.get(
                    key,
                    ""
                ).strip()
            )

        connection.commit()

        flash(
            "تم حفظ إعدادات النظام بنجاح.",
            "ok"
        )

        connection.close()

        return redirect(
            url_for("settings")
        )

    settings_data = get_settings(
        connection
    )

    connection.close()

    return render_template(
        "settings.html",
        settings=settings_data
    )


def moyasar_get_invoice(invoice_id):
    secret = os.environ.get("MOYASAR_SECRET_KEY", "").strip()

    if not secret:
        raise RuntimeError("مفتاح Moyasar السري غير مضبوط")

    response = requests.get(
        f"https://api.moyasar.com/v1/invoices/{invoice_id}",
        auth=(secret, ""),
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


@app.route("/payments/link/<int:bid>", methods=["POST"])
@login_required
def create_payment_link(bid):
    connection = db()

    try:
        booking = connection.execute(
            """
            SELECT b.*, c.name AS customer_name
            FROM bookings b
            JOIN customers c ON c.id = b.customer_id
            WHERE b.id = ?
            """,
            (bid,),
        ).fetchone()

        if not booking:
            flash("الحجز غير موجود.", "error")
            return redirect(url_for("bookings"))

        if booking["status"] == "cancelled":
            flash("لا يمكن إنشاء رابط دفع لحجز ملغي.", "error")
            return redirect(url_for("bookings"))

        remaining = round(
            float(booking["price"]) - float(booking["paid"]),
            2,
        )

        if remaining <= 0:
            flash("تم سداد قيمة الحجز بالكامل.", "error")
            return redirect(url_for("bookings"))

        secret = os.environ.get("MOYASAR_SECRET_KEY", "").strip()
        base_url = os.environ.get(
            "PUBLIC_BASE_URL",
            "https://fortance-hotels.onrender.com",
        ).rstrip("/")

        if not secret:
            flash("أضف MOYASAR_SECRET_KEY في Render أولاً.", "error")
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
                    connection.execute(
                        "UPDATE bookings SET payment_url=? WHERE id=?",
                        (old_invoice["url"], bid),
                    )
                    connection.commit()
                    flash("رابط الدفع السابق ما زال صالحاً.", "ok")
                    return redirect(url_for("bookings"))

            except (requests.RequestException, ValueError):
                pass

        payload = {
            "amount": round(remaining * 100),
            "currency": "SAR",
            "description": f"FORTANCE HOTELS - Booking {booking['code']}",
            "callback_url": base_url + "/payments/moyasar/callback",
            "success_url": base_url + "/payments/moyasar/return",
            "back_url": base_url + "/bookings",
            "metadata": {
                "booking_id": str(bid),
                "booking_code": str(booking["code"]),
            },
        }

        response = requests.post(
            "https://api.moyasar.com/v1/invoices",
            auth=(secret, ""),
            json=payload,
            timeout=25,
        )
        response.raise_for_status()
        invoice_data = response.json()

        invoice_id = invoice_data.get("id")
        payment_url = invoice_data.get("url")

        if not invoice_id or not payment_url:
            raise RuntimeError("لم يُرجع Moyasar رقم الفاتورة ورابط الدفع.")

        connection.execute(
            """
            UPDATE bookings
            SET payment_url=?,
                moyasar_invoice_id=?,
                payment_status='pending'
            WHERE id=?
            """,
            (payment_url, invoice_id, bid),
        )

        connection.execute(
            """
            INSERT INTO payments
                (booking_id, amount, method, payment_url,
                 status, reference, created_at, created_by)
            VALUES (?, ?, 'moyasar', ?, 'pending', ?, ?, ?)
            """,
            (
                bid,
                remaining,
                payment_url,
                invoice_id,
                datetime.now().isoformat(timespec="seconds"),
                session.get("uid"),
            ),
        )

        connection.commit()
        flash("تم إنشاء رابط الدفع بنجاح.", "ok")

    except requests.RequestException:
        connection.rollback()
        app.logger.exception("Moyasar invoice creation failed")
        flash("تعذر إنشاء رابط الدفع. تحقق من إعدادات Moyasar.", "error")

    except (RuntimeError, ValueError):
        connection.rollback()
        app.logger.exception("Payment setup error")
        flash("تعذر إعداد الدفع. راجع إعدادات النظام.", "error")

    finally:
        connection.close()

    return redirect(url_for("bookings"))


@app.route("/payments/moyasar/callback", methods=["POST"])
def moyasar_callback():
    data = request.get_json(silent=True) or {}
    invoice_id = data.get("id")

    if not invoice_id:
        return jsonify({"error": "missing invoice id"}), 400

    connection = db()

    try:
        invoice_data = moyasar_get_invoice(str(invoice_id))

        if invoice_data.get("id") != invoice_id:
            return jsonify({"error": "invoice mismatch"}), 400

        metadata = invoice_data.get("metadata") or {}
        booking_id = metadata.get("booking_id")

        if not booking_id:
            return jsonify({"error": "missing booking reference"}), 400

        booking = connection.execute(
            "SELECT * FROM bookings WHERE id=?",
            (booking_id,),
        ).fetchone()

        if not booking:
            return jsonify({"error": "booking not found"}), 404

        if booking["moyasar_invoice_id"] != invoice_id:
            return jsonify({"error": "invoice not linked to booking"}), 400

        expected_amount = round(
            max(0, float(booking["price"]) - float(booking["paid"])) * 100
        )

        if (
            invoice_data.get("amount") != expected_amount
            or invoice_data.get("currency") != "SAR"
        ):
            return jsonify({"error": "amount or currency mismatch"}), 400

        status = invoice_data.get("status")

        if status == "paid":
            payment = connection.execute(
                """
                SELECT * FROM payments
                WHERE reference=? AND booking_id=?
                """,
                (invoice_id, booking_id),
            ).fetchone()

            if payment and payment["status"] != "paid":
                connection.execute(
                    """
                    UPDATE bookings
                    SET paid = MIN(price, paid + ?),
                        payment_status='paid'
                    WHERE id=?
                    """,
                    (payment["amount"], booking_id),
                )

                connection.execute(
                    """
                    UPDATE payments SET status='paid'
                    WHERE id=?
                    """,
                    (payment["id"],),
                )

                connection.commit()

        elif status in ("failed", "expired", "canceled", "voided"):
            connection.execute(
                """
                UPDATE payments SET status=?
                WHERE reference=? AND booking_id=?
                  AND status='pending'
                """,
                (status, invoice_id, booking_id),
            )
            connection.execute(
                """
                UPDATE bookings SET payment_status=?
                WHERE id=? AND payment_status!='paid'
                """,
                (status, booking_id),
            )
            connection.commit()

        return jsonify({"received": True}), 200

    except (requests.RequestException, ValueError):
        connection.rollback()
        app.logger.exception("Moyasar callback verification failed")
        return jsonify({"error": "verification failed"}), 502

    finally:
        connection.close()


@app.route("/payments/moyasar/return")
@login_required
def moyasar_return():
    invoice_id = request.args.get("id", "").strip()

    if not invoice_id:
        flash(
            "عدت من صفحة الدفع. تحقق من حالة الدفع في الحجوزات.",
            "ok",
        )
        return redirect(url_for("bookings"))

    connection = db()

    try:
        invoice_data = moyasar_get_invoice(invoice_id)
        metadata = invoice_data.get("metadata") or {}
        booking_id = metadata.get("booking_id")

        booking = connection.execute(
            "SELECT * FROM bookings WHERE id=?",
            (booking_id,),
        ).fetchone() if booking_id else None

        if not booking or booking["moyasar_invoice_id"] != invoice_id:
            flash("تعذر مطابقة عملية الدفع مع الحجز.", "error")
        elif (
            invoice_data.get("status") == "paid"
            and invoice_data.get("amount")
                == round(max(
                    0,
                    float(booking["price"]) - float(booking["paid"])
                ) * 100)
            and invoice_data.get("currency") == "SAR"
        ):
            flash(
                "تم التحقق من الدفع. إذا لم يتحدث الرصيد، انتظر إشعار الدفع.",
                "ok",
            )
        else:
            flash(
                "لم يتم تأكيد الدفع بعد. حالة الفاتورة: "
                + str(invoice_data.get("status")),
                "error",
            )

    except (requests.RequestException, ValueError):
        app.logger.exception("Moyasar return verification failed")
        flash("تعذر التحقق من الدفع الآن. حاول تحديث الحجوزات لاحقاً.", "error")

    finally:
        connection.close()

    return redirect(url_for("bookings"))


# Initialize the database when the application module loads.
# Keep existing tables and records; do not delete the database.
init_db()

if __name__ == "__main__":
    app.run(
        debug=False,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000"))
    )
