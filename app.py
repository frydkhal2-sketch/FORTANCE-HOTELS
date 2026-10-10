
from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3, os, hashlib, secrets
from functools import wraps
from datetime import datetime


BASE = os.path.dirname(__file__)
DB = os.path.join(BASE, "fortans.db")
app = Flask(
    __name__,
    template_folder=".",
    static_folder=".",
    static_url_path="/static"
app.secret_key = os.environ.get("FORTANCE_SECRET", secrets.token_hex(32))

ROLES = {
    "admin": "مدير النظام",
    "manager": "مدير",
    "reception": "استقبال",
    "accountant": "محاسب"
}

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def hash_pw(p):
    return hashlib.sha256(p.encode("utf-8")).hexdigest()

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS employees(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, username TEXT UNIQUE NOT NULL,
      password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'reception',
      active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS hotels(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, city TEXT, phone TEXT, notes TEXT, active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS rooms(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      hotel_id INTEGER NOT NULL, number TEXT NOT NULL, room_type TEXT,
      price REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'available',
      UNIQUE(hotel_id, number), FOREIGN KEY(hotel_id) REFERENCES hotels(id)
    );
    CREATE TABLE IF NOT EXISTS customers(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT,
      notes TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS bookings(
      id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
      customer_id INTEGER NOT NULL, hotel_id INTEGER NOT NULL, room_id INTEGER NOT NULL,
      checkin TEXT NOT NULL, checkout TEXT NOT NULL, nights INTEGER NOT NULL,
      price REAL NOT NULL, paid REAL NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'confirmed', created_by INTEGER,
      created_at TEXT NOT NULL,
      FOREIGN KEY(customer_id) REFERENCES customers(id),
      FOREIGN KEY(hotel_id) REFERENCES hotels(id),
      FOREIGN KEY(room_id) REFERENCES rooms(id)
    );
    CREATE TABLE IF NOT EXISTS seasons(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      start_date TEXT NOT NULL, end_date TEXT NOT NULL, notes TEXT
    );
    """)
    if c.execute("SELECT COUNT(*) n FROM employees").fetchone()["n"] == 0:
        c.execute("INSERT INTO employees(name,username,password,role,created_at) VALUES(?,?,?,?,?)",
                  ("مدير النظام","admin",hash_pw("ChangeMe123!"),"admin",datetime.now().isoformat()))
    if c.execute("SELECT COUNT(*) n FROM hotels").fetchone()["n"] == 0:
        c.execute("INSERT INTO hotels(name,city,phone) VALUES(?,?,?)",("فندق ريان الجوار","مكة المكرمة",""))
        c.execute("INSERT INTO hotels(name,city,phone) VALUES(?,?,?)",("فندق 2","مكة المكرمة",""))
        c.execute("INSERT INTO hotels(name,city,phone) VALUES(?,?,?)",("فندق 3","جدة",""))
        for hid in range(1,4):
            for n in range(1,6):
                c.execute("INSERT INTO rooms(hotel_id,number,room_type,price) VALUES(?,?,?,?)",
                          (hid, f"{100+hid*10+n}", "مزدوجة", 350))
    c.commit(); c.close()

def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if "uid" not in session: return redirect(url_for("login"))
        return f(*a, **kw)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **kw):
        if session.get("role") != "admin":
            flash("هذه الصفحة متاحة لمدير النظام فقط.","error")
            return redirect(url_for("dashboard"))
        return f(*a, **kw)
    return w

@app.context_processor
def ctx():
    return {"ROLES": ROLES, "user_name": session.get("name"), "user_role": session.get("role")}

@app.route("/")
def index(): return redirect(url_for("dashboard") if "uid" in session else url_for("login"))
@app.route("/style.css")
def style_css():
    return send_file(os.path.join(BASE, "style.css"), mimetype="text/css")


@app.route("/logo.jpg")
def logo():
    return send_file(os.path.join(BASE, "logo.jpg"), mimetype="image/jpeg")
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        c=db(); u=c.execute("SELECT * FROM employees WHERE username=? AND active=1",(request.form["username"],)).fetchone(); c.close()
        if u and u["password"]==hash_pw(request.form["password"]):
            session.update(uid=u["id"], name=u["name"], role=u["role"])
            return redirect(url_for("dashboard"))
        flash("بيانات الدخول غير صحيحة.","error")
    return render_template("login.html")

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    c=db()
    data={
      "hotels":c.execute("SELECT COUNT(*) n FROM hotels WHERE active=1").fetchone()["n"],
      "rooms":c.execute("SELECT COUNT(*) n FROM rooms").fetchone()["n"],
      "booked":c.execute("SELECT COUNT(*) n FROM bookings WHERE status='confirmed'").fetchone()["n"],
      "revenue":c.execute("SELECT COALESCE(SUM(paid),0) n FROM bookings").fetchone()["n"],
      "bookings":c.execute("""SELECT b.*,cu.name customer,h.name hotel,r.number room
         FROM bookings b JOIN customers cu ON cu.id=b.customer_id JOIN hotels h ON h.id=b.hotel_id
         JOIN rooms r ON r.id=b.room_id ORDER BY b.id DESC LIMIT 8""").fetchall()
    }
    c.close(); data["now"] = datetime.now().strftime("%Y-%m-%d"); return render_template("dashboard.html", **data)

@app.route("/employees", methods=["GET","POST"])
@login_required
@admin_required
def employees():
    c=db()
    if request.method=="POST":
        try:
            c.execute("INSERT INTO employees(name,username,password,role,created_at) VALUES(?,?,?,?,?)",
                      (request.form["name"],request.form["username"],hash_pw(request.form["password"]),
                       request.form["role"],datetime.now().isoformat()))
            c.commit(); flash("تمت إضافة الموظف.","ok")
        except sqlite3.IntegrityError: flash("اسم المستخدم مستخدم بالفعل.","error")
    rows=c.execute("SELECT id,name,username,role,active,created_at FROM employees ORDER BY id DESC").fetchall()
    c.close(); return render_template("employees.html", employees=rows)

@app.route("/employees/toggle/<int:eid>", methods=["POST"])
@login_required
@admin_required
def toggle_employee(eid):
    if eid == session["uid"]: flash("لا يمكنك تعطيل حسابك الحالي.","error")
    else:
        c=db(); c.execute("UPDATE employees SET active=1-active WHERE id=?",(eid,)); c.commit(); c.close()
    return redirect(url_for("employees"))

@app.route("/hotels", methods=["GET","POST"])
@login_required
def hotels():
    c=db()
    if request.method=="POST":
        c.execute("INSERT INTO hotels(name,city,phone,notes) VALUES(?,?,?,?)",
                  (request.form["name"],request.form["city"],request.form["phone"],request.form["notes"]))
        c.commit(); flash("تمت إضافة الفندق.","ok")
    rows=c.execute("SELECT h.*, (SELECT COUNT(*) FROM rooms r WHERE r.hotel_id=h.id) rooms FROM hotels h ORDER BY h.id DESC").fetchall()
    c.close(); return render_template("hotels.html", hotels=rows)

@app.route("/rooms", methods=["GET","POST"])
@login_required
def rooms():
    c=db(); hotels=c.execute("SELECT * FROM hotels WHERE active=1").fetchall()
    if request.method=="POST":
        try:
            c.execute("INSERT INTO rooms(hotel_id,number,room_type,price,status) VALUES(?,?,?,?,?)",
                      (request.form["hotel_id"],request.form["number"],request.form["room_type"],
                       request.form["price"],request.form["status"]))
            c.commit(); flash("تمت إضافة الغرفة.","ok")
        except sqlite3.IntegrityError: flash("رقم الغرفة موجود لهذا الفندق.","error")
    rows=c.execute("SELECT r.*,h.name hotel FROM rooms r JOIN hotels h ON h.id=r.hotel_id ORDER BY r.id DESC").fetchall()
    c.close(); return render_template("rooms.html", rooms=rows, hotels=hotels)

@app.route("/bookings", methods=["GET","POST"])
@login_required
def bookings():
    c=db()
    if request.method=="POST":
        name=request.form["customer"]; phone=request.form["phone"]
        cu=c.execute("SELECT id FROM customers WHERE phone=? AND phone<>''",(phone,)).fetchone()
        if cu: cid=cu["id"]
        else:
            cur=c.execute("INSERT INTO customers(name,phone,notes,created_at) VALUES(?,?,?,?)",
                          (name,phone,"",datetime.now().isoformat())); cid=cur.lastrowid
        room=c.execute("SELECT * FROM rooms WHERE id=?",(request.form["room_id"],)).fetchone()
        ci=datetime.fromisoformat(request.form["checkin"]); co=datetime.fromisoformat(request.form["checkout"])
        nights=max(1,(co-ci).days); price=float(room["price"])*nights
        code="BK-"+secrets.token_hex(3).upper()
        c.execute("""INSERT INTO bookings(code,customer_id,hotel_id,room_id,checkin,checkout,nights,price,paid,status,created_by,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (code,cid,request.form["hotel_id"],request.form["room_id"],request.form["checkin"],
                   request.form["checkout"],nights,price,float(request.form["paid"] or 0),
                   "confirmed",session["uid"],datetime.now().isoformat()))
        c.execute("UPDATE rooms SET status='booked' WHERE id=?",(request.form["room_id"],))
        c.commit(); flash(f"تم إنشاء الحجز {code}.","ok"); return redirect(url_for("bookings"))
    rows=c.execute("""SELECT b.*,cu.name customer,h.name hotel,r.number room
                      FROM bookings b JOIN customers cu ON cu.id=b.customer_id
                      JOIN hotels h ON h.id=b.hotel_id JOIN rooms r ON r.id=b.room_id
                      ORDER BY b.id DESC""").fetchall()
    hotels=c.execute("SELECT * FROM hotels WHERE active=1").fetchall()
    rooms=c.execute("SELECT * FROM rooms WHERE status='available'").fetchall()
    c.close(); return render_template("bookings.html", bookings=rows, hotels=hotels, rooms=rooms)

@app.route("/invoices/<int:bid>")
@login_required
def invoice(bid):
    c=db(); b=c.execute("""SELECT b.*,cu.name customer,cu.phone,h.name hotel,h.city,r.number room,r.room_type
      FROM bookings b JOIN customers cu ON cu.id=b.customer_id JOIN hotels h ON h.id=b.hotel_id JOIN rooms r ON r.id=b.room_id WHERE b.id=?""",(bid,)).fetchone(); c.close()
    if not b: return "Not found",404
    return render_template("invoice.html", b=b)

@app.route("/seasons", methods=["GET","POST"])
@login_required
def seasons():
    c=db()
    if request.method=="POST":
        c.execute("INSERT INTO seasons(name,start_date,end_date,notes) VALUES(?,?,?,?)",
                  (request.form["name"],request.form["start_date"],request.form["end_date"],request.form["notes"])); c.commit()
        flash("تمت إضافة الموسم.","ok")
    rows=c.execute("SELECT * FROM seasons ORDER BY start_date DESC").fetchall(); c.close()
    return render_template("seasons.html", seasons=rows)

@app.route("/reports")
@login_required
def reports():
    c=db(); rows=c.execute("""SELECT h.name hotel, COUNT(b.id) bookings, COALESCE(SUM(b.paid),0) paid,
                                     COALESCE(SUM(b.price),0) total
                              FROM hotels h LEFT JOIN bookings b ON b.hotel_id=h.id GROUP BY h.id""").fetchall()
    c.close(); return render_template("reports.html", rows=rows)

init_db()

init_db()

if __name__=="__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
