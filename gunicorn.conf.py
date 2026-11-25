"""Register optional invoice sharing pages after the Flask app is loaded."""


def post_worker_init(worker):
    """Add manual WhatsApp and group-invoice routes to the Flask app."""
    from urllib.parse import quote

    from flask import abort, flash, redirect, render_template, url_for

    import app as application

    flask_app = application.app

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
        invoice_url = url_for("invoice", bid=bid, _external=True)
        message = (
            f"مرحباً {booking['customer']}، فاتورتك من FORTANCE HOTELS "
            f"للحجز {booking['code']}، الإجمالي {float(booking['price'] or 0):.2f} ريال، "
            f"المدفوع {float(booking['paid'] or 0):.2f} ريال، "
            f"المتبقي {remaining:.2f} ريال. رابط الفاتورة: {invoice_url}"
        )
        return redirect(f"https://wa.me/{phone}?text={quote(message)}")

    @application.login_required
    def group_invoice(gid):
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
        if phone:
            remaining = max(0, float(group["total"] or 0) - float(group["paid"] or 0))
            invoice_url = url_for("group_invoice", gid=gid, _external=True)
            message = (
                f"مرحباً {group['contact_name'] or group['group_name']}، "
                f"فاتورة حجز المجموعة {group['code']} من FORTANCE HOTELS. "
                f"الإجمالي {float(group['total'] or 0):.2f} ريال، "
                f"المدفوع {float(group['paid'] or 0):.2f} ريال، "
                f"المتبقي {remaining:.2f} ريال. رابط الفاتورة: {invoice_url}"
            )
            whatsapp_url = f"https://wa.me/{phone}?text={quote(message)}"

        return render_template(
            "group_invoice.html",
            g=group,
            rooms=rooms,
            settings=settings,
            remaining=max(0, float(group["total"] or 0) - float(group["paid"] or 0)),
            whatsapp_url=whatsapp_url,
        )

    flask_app.add_url_rule(
        "/send-invoice-whatsapp/<int:bid>",
        endpoint="send_invoice_whatsapp",
        view_func=send_invoice_whatsapp,
    )
    flask_app.add_url_rule(
        "/group-invoices/<int:gid>",
        endpoint="group_invoice",
        view_func=group_invoice,
    )
