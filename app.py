@app.route("/group-bookings", methods=["GET", "POST"])
@login_required
def group_bookings():
    connection = db()

    if request.method == "POST":
        group_name = request.form.get("group_name", "").strip()
        contact_name = request.form.get("contact_name", "").strip()
        phone = request.form.get("phone", "").strip()
        company_id = request.form.get("company_id") or None
        hotel_id = request.form.get("hotel_id")
        checkin = request.form.get("checkin", "").strip()
        checkout = request.form.get("checkout", "").strip()
        notes = request.form.get("notes", "").strip()

        try:
            guests = int(request.form.get("guests", "0") or 0)
        except (ValueError, TypeError):
            guests = 0

        try:
            requested_rooms = int(
                request.form.get("requested_rooms",
                                 request.form.get("rooms_count", "0")) or 0
            )
        except (ValueError, TypeError):
            requested_rooms = 0

        raw_room_ids = request.form.getlist("room_ids")
        room_ids = sorted({
            int(r)
            for r in raw_room_ids
            if r and r.isdigit()
        })

        if not hotel_id:
            connection.close()
            flash("يرجى اختيار الفندق.", "error")
            return redirect(url_for("group_bookings"))

        if not group_name:
            connection.close()
            flash("يرجى إدخال اسم المجموعة.", "error")
            return redirect(url_for("group_bookings"))

        if not room_ids:
            connection.close()
            flash("يرجى اختيار غرفة واحدة على الأقل.", "error")
            return redirect(url_for("group_bookings"))

        if requested_rooms != len(room_ids):
            connection.close()
            flash("عدد الغرف المطلوبة يجب أن يساوي عدد الغرف التي اخترتها.", "error")
            return redirect(url_for("group_bookings"))

        if not checkin or not checkout:
            connection.close()
            flash("يرجى إدخال تواريخ الوصول والمغادرة.", "error")
            return redirect(url_for("group_bookings"))

        try:
            ci = datetime.fromisoformat(checkin)
            co = datetime.fromisoformat(checkout)
        except ValueError:
            connection.close()
            flash("تاريخ الوصول أو المغادرة غير صالح.", "error")
            return redirect(url_for("group_bookings"))

        if co <= ci:
            connection.close()
            flash("تاريخ المغادرة يجب أن يكون بعد تاريخ الوصول.", "error")
            return redirect(url_for("group_bookings"))

        nights = (co - ci).days
        if nights <= 0:
            connection.close()
            flash("يجب أن يكون هناك ليلة واحدة على الأقل.", "error")
            return redirect(url_for("group_bookings"))

        try:
            connection.execute("BEGIN")

            room_rows = connection.execute(
                f"""
                SELECT *
                FROM rooms
                WHERE id IN ({",".join("?" for _ in room_ids)})
                AND hotel_id = ?
                """,
                (*room_ids, hotel_id),
            ).fetchall()

            rooms_by_id = {room["id"]: room for room in room_rows}

            if len(rooms_by_id) != len(room_ids):
                connection.rollback()
                connection.close()
                flash("إحدى الغرف غير موجودة.", "error")
                return redirect(url_for("group_bookings"))

            valid_rooms = []
            total = 0.0

            for rid in room_ids:
                room = rooms_by_id[rid]

                if room["status"] in {"maintenance", "out_of_service", "cleaning"}:
                    connection.rollback()
                    connection.close()
                    flash("إحدى الغرف غير متاحة.", "error")
                    return redirect(url_for("group_bookings"))

                if (
                    room_conflict(connection, rid, checkin, checkout)
                    or group_room_conflict(connection, rid, checkin, checkout)
                ):
                    connection.rollback()
                    connection.close()
                    flash(f"الغرفة {room['number']} متعارضة مع حجز آخر.", "error")
                    return redirect(url_for("group_bookings"))

                valid_rooms.append(room)
                total += float(room["price"]) * nights

            paid = max(0, min(float(request.form.get("paid", "0") or 0), total))
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

            flash(f"تم إنشاء حجز المجموعة {code} لعدد {len(valid_rooms)} غرفة.", "ok")
            return redirect(url_for("group_bookings"))

        except Exception:
            connection.rollback()
            connection.close()
            flash("حدث خطأ أثناء إنشاء الحجز. حاول مرة أخرى.", "error")
            return redirect(url_for("group_bookings"))

    rows = connection.execute(
        """
        SELECT
            gb.*,
            h.name AS hotel_name,
            c.name AS company_name,
            COALESCE(r.room_count, 0) AS room_count
        FROM group_bookings gb
        LEFT JOIN hotels h ON h.id = gb.hotel_id
        LEFT JOIN companies c ON c.id = gb.company_id
        LEFT JOIN (
            SELECT group_booking_id, COUNT(*) AS room_count
            FROM group_booking_rooms
            GROUP BY group_booking_id
        ) r ON r.group_booking_id = gb.id
        ORDER BY gb.created_at DESC
        """
    ).fetchall()

    hotels_list = connection.execute("SELECT * FROM hotels ORDER BY name").fetchall()
    companies_list = connection.execute("SELECT * FROM companies ORDER BY name").fetchall()
    rooms_list = connection.execute("SELECT * FROM rooms ORDER BY number").fetchall()

    connection.close()

    return render_template(
        "group_bookings.html",
        group_bookings=rows,
        hotels=hotels_list,
        companies=companies_list,
        rooms=rooms_list,
    )
