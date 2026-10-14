@app.route(
    "/group-bookings",
    methods=["GET", "POST"],
)
@login_required
def group_bookings():
    connection = db()

    if request.method == "POST":
        group_name = request.form.get("group_name", "").strip()
        contact_name = request.form.get("contact_name", "").strip()
        phone = request.form.get("phone", "").strip()
        company_id = request.form.get("company_id") or None
        hotel_id = request.form.get("hotel_id")
        checkin = request.form.get("checkin", "")
        checkout = request.form.get("checkout", "")

        try:
            guests = int(request.form.get("guests", "0") or 0)
        except (ValueError, TypeError):
            guests = 0

        notes = request.form.get("notes", "").strip()
        room_ids = [x for x in request.form.getlist("room_ids") if x]

        try:
            requested_rooms_value = request.form.get(
                "requested_rooms",
                request.form.get("rooms_count", "0"),
            )
            requested_rooms = int(requested_rooms_value or 0)
        except (ValueError, TypeError):
            requested_rooms = 0

        if requested_rooms < 1 or requested_rooms != len(room_ids):
            connection.close()
            flash(
                "عدد الغرف المطلوبة يجب أن يساوي عدد الغرف التي اخترتها.",
                "error",
            )
            return redirect(url_for("group_bookings"))

        try:
            ci = datetime.fromisoformat(checkin)
            co = datetime.fromisoformat(checkout)
            nights = (co - ci).days

            if nights <= 0 or not group_name or not room_ids:
                raise ValueError

        except ValueError:
            connection.close()
            flash(
                "أدخل اسم المجموعة والتواريخ واختر غرفة واحدة على الأقل.",
                "error",
            )
            return redirect(url_for("group_bookings"))

        valid_rooms = []
        total = 0.0

        for rid in room_ids:
            room = connection.execute(
                """
                SELECT *
                FROM rooms
                WHERE id=?
                AND hotel_id=?
                """,
                (rid, hotel_id),
            ).fetchone()

            if not room:
                connection.close()
                flash("إحدى الغرف غير موجودة.", "error")
                return redirect(url_for("group_bookings"))

            if room["status"] in {
                "maintenance",
                "out_of_service",
                "cleaning",
            }:
                connection.close()
                flash("إحدى الغرف غير متاحة.", "error")
                return redirect(url_for("group_bookings"))

            if (
                room_conflict(connection, rid, checkin, checkout)
                or group_room_conflict(connection, rid, checkin, checkout)
            ):
                connection.close()
                flash(f"الغرفة {room['number']} متعارضة مع حجز آخر.", "error")
                return redirect(url_for("group_bookings"))

            valid_rooms.append(room)
            total += float(room["price"]) * nights

        paid = max(
            0,
            min(
                float(request.form.get("paid", "0") or 0),
                total,
            ),
        )

        code = "GR-" + secrets.token_hex(3).upper()

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
                datetime.now().isoformat(),
            ),
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
                    float(room["price"]) * nights,
                ),
            )

            connection.execute(
                """
                UPDATE rooms
                SET status='booked'
                WHERE id=?
                """,
                (room["id"],),
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
                    request.form.get("payment_method", "cash"),
                    request.form.get("payment_url", ""),
                    "paid",
                    datetime.now().isoformat(),
                    session["uid"],
                ),
            )

        connection.commit()
        connection.close()

        flash(
            f"تم إنشاء حجز المجموعة {code} لعدد {len(valid_rooms)} غرفة.",
            "ok",
        )

        return redirect(url_for("group_bookings"))

    rows = connection.execute(
        """
        SELECT
            gb.*,
            h.name AS hotel_name,
            c.name AS company_name,
            COUNT(gbr.id) AS room_count
        FROM group_bookings gb
        LEFT JOIN hotels h ON h.id = gb.hotel_id
        LEFT JOIN companies c ON c.id = gb.company_id
        LEFT JOIN group_booking_rooms gbr ON gbr.group_booking_id = gb.id
        GROUP BY gb.id
        ORDER BY gb.created_at DESC
        """
    ).fetchall()

    hotels_list = connection.execute(
        "SELECT * FROM hotels ORDER BY name"
    ).fetchall()
    companies_list = connection.execute(
        "SELECT * FROM companies ORDER BY name"
    ).fetchall()
    rooms_list = connection.execute(
        "SELECT * FROM rooms ORDER BY number"
    ).fetchall()

    connection.close()

    return render_template(
        "group_bookings.html",
        group_bookings=rows,
        hotels=hotels_list,
        companies=companies_list,
        rooms=rooms_list,
    )
