"""Gunicorn hooks for invoice routes and guest-safe invoice sharing."""


def post_worker_init(worker):
    """Register invoice routes after the Flask application is initialized."""
    from urllib.parse import quote

    from flask import abort, flash, redirect, render_template, url_for
    from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

    import app as application

    flask_app = application.app
    serializer = URLSafeTimedSerializer(flask_app.secret_key, salt="fortance-invoice-share-v1")

    def make_share_token(kind, record_id):
        return serializer.dumps({"kind": kind, "id": int(record_id)})

    def validate_share_token(token, kind, record_id):
        try:
            data = serializer.loads(token, max_age=365 * 24 * 60 * 60)
        except (BadSignature, SignatureExpired):
            abort(404)
        if data != {"kind": kind, "id": int(record_id)}:
            abort(404)

    @application.login_required
    def send_invoice_whatsapp(bid):
        conn = application.db()
        booking = conn.execute(
            """
            SELECT b.id, b.code, b.price, b.paid,
                   c.name AS customer, c.phone
            FROM bookings b
            JOIN customers c ON c.id=b.customer_id
            WHERE b.id=?
            """,
            (bid,),
        ).fetchone()
        conn.close()
        if not booking:
            abort(404)

        phone = application.normalize_phone(booking["phone"])
        if not phone:
            flash("لا يوجد رقم هاتف صالح لهذا الحجز.", "error")
            return redirect(url_for("bookings"))

        remaining = max(0, float(booking["price"] or 0) - float(booking["paid"] or 0))
        token = make_share_token("booking", bid)
        invoice_url = url_for("public_booking_invoice", bid=bid, token=token, _external=True)
        message = (
            f"مرحباً {booking['customer']}، فاتورتك من FORTANCE HOTELS "
            f"للحجز {booking['code']}، الإجمالي {float(booking['price'] or 0):.2f} ريال، "
            f"المدفوع {float(booking['paid'] or 0):.2f} ريال، "
            f"المتبقي {remaining:.2f} ريال. رابط الفاتورة: {invoice_url}"
        )
        return redirect(f"https://wa.me/{phone}?text={quote(message)}")

    @application.login_required
    def send_group_invoice_whatsapp(gid):
        conn = application.db()
        group = conn.execute(
            "SELECT id, code, group_name, contact_name, phone, total, paid "
            "FROM group_bookings WHERE id=?",
            (gid,),
        ).fetchone()
        conn.close()
        if not group:
            abort(404)

        phone = application.normalize_phone(group["phone"])
        if not phone:
            flash("لا يوجد رقم هاتف صالح لحجز المجموعة.", "error")
            return redirect(url_for("group_bookings"))

        token = make_share_token("group", gid)
        invoice_url = url_for("public_group_invoice", gid=gid, token=token, _external=True)
        total = float(group["total"] or 0)
        paid = float(group["paid"] or 0)
        message = (
            f"مرحباً {group['contact_name'] or group['group_name']}، "
            f"فاتورة حجز المجموعة {group['code']} من FORTANCE HOTELS. "
            f"الإجمالي {total:.2f} ريال، المدفوع {paid:.2f} ريال، "
            f"المتبقي {max(0, total - paid):.2f} ريال. رابط الفاتورة: {invoice_url}"
        )
        return redirect(f"https://wa.me/{phone}?text={quote(message)}")

    def load_group_invoice(gid, public=False):
        conn = application.db()
        group = conn.execute(
            """
            SELECT g.*, h.name AS hotel, h.phone AS hotel_phone,
                   c.name AS company
            FROM group_bookings g
            JOIN hotels h ON h.id=g.hotel_id
            LEFT JOIN companies c ON c.id=g.company_id
            WHERE g.id=?
            """,
            (gid,),
        ).fetchone()
        if not group:
            conn.close()
            abort(404)

        rooms = conn.execute(
            """
            SELECT r.number, r.room_type, gr.price
            FROM group_booking_rooms gr
            JOIN rooms r ON r.id=gr.room_id
            WHERE gr.group_booking_id=?
            ORDER BY r.number
            """,
            (gid,),
        ).fetchall()
        settings = application.get_settings(conn)
        conn.close()

        phone = application.normalize_phone(group["phone"])
        whatsapp_url = ""
        if phone and not public:
            whatsapp_url = url_for("send_group_invoice_whatsapp", gid=gid)
        return render_template(
            "group_invoice.html",
            g=group,
            rooms=rooms,
            settings=settings,
            remaining=max(0, float(group["total"] or 0) - float(group["paid"] or 0)),
            whatsapp_url=whatsapp_url,
        )

    @application.login_required
    def group_invoice(gid):
        return load_group_invoice(gid)

    def public_booking_invoice(bid, token):
        validate_share_token(token, "booking", bid)
        conn = application.db()
        booking = conn.execute(
            """
            SELECT b.*, cu.name AS customer, cu.phone AS phone,
                   h.name AS hotel, h.city AS city, h.phone AS hotel_phone,
                   r.number AS room, r.room_type AS room_type
            FROM bookings b
            JOIN customers cu ON cu.id=b.customer_id
            JOIN hotels h ON h.id=b.hotel_id
            JOIN rooms r ON r.id=b.room_id
            WHERE b.id=?
            """,
            (bid,),
        ).fetchone()
        settings = application.get_settings(conn)
        conn.close()
        if not booking:
            abort(404)
        return render_template(
            "invoice.html",
            b=booking,
            settings=settings,
            remaining=max(0, float(booking["price"] or 0) - float(booking["paid"] or 0)),
            invoice_url="",
            whatsapp_url="",
        )

    def public_group_invoice(gid, token):
        validate_share_token(token, "group", gid)
        return load_group_invoice(gid, public=True)

    routes = (
        ("/send-invoice-whatsapp/<int:bid>", "send_invoice_whatsapp", send_invoice_whatsapp),
        ("/send-group-invoice-whatsapp/<int:gid>", "send_group_invoice_whatsapp", send_group_invoice_whatsapp),
        ("/group-invoices/<int:gid>", "group_invoice", group_invoice),
        ("/shared/invoices/<int:bid>/<token>", "public_booking_invoice", public_booking_invoice),
        ("/shared/group-invoices/<int:gid>/<token>", "public_group_invoice", public_group_invoice),
    )
    for url_rule, endpoint, view in routes:
        if endpoint not in flask_app.view_functions:
            flask_app.add_url_rule(url_rule, endpoint=endpoint, view_func=view)
