
@app.route("/group-bookings", methods=["GET", "POST"])
@login_required
def group_bookings():
    # pylint: disable=too-many-return-statements,too-many-statements
    connection = db()

    try:
        if request.method == "POST":
            # 1. قراءة بيانات المجموعة
            group_name = request.form.get("group_name", "").strip()
            contact_name = request.form.get("contact_name", "").strip()
            phone = request.form.get("phone", "").strip()
            company_id = request.form.get("company_id") or None
            hotel_value = request.form.get("hotel_id", "").strip()
            checkin = request.form.get("checkin", "").strip()
            checkout = request.form.get("checkout", "").strip()
            notes = request.form.get("notes", "").strip()
            payment_method = request.form.get("payment_method", "cash").strip()

            # 2. التحقق من الأعداد والمدفوعات
            try:
                guests = int(request.form.get("guests", "0") or 0)
                requested_rooms = int(
                    request.form.get(
                        "requested_rooms",
                        request.form.get("rooms_count", "0"),
                    ) or 0
                )
                paid = float(request.form.get("paid", "0") or 0)

                if guests < 0 or requested_rooms < 1 or paid < 0:
                    raise ValueError

            except (ValueError, TypeError):
                flash("تحقق من عدد النزلاء والغرف والمبلغ المدفوع.", "error")
                return redirect(url_for("group_bookings"))

            # 3. تجهيز أرقام الغرف ومنع التكرار
            raw_room_ids = request.form.getlist("room_ids")

            try:
                room_ids = list(dict.fromkeys(
                    int(room_id)
                    for room_id in raw_room_ids
                    if room_id and int(room_id) > 0
                ))
            except (ValueError, TypeError):
                flash("اختيارات الغرف غير صحيحة.", "error")
                return redirect(url_for("group_bookings"))

            if not group_name:
                flash("يرجى إدخال اسم المجموعة.", "error")
                return redirect(url_for("group_bookings"))

            if not hotel_value:
                flash("يرجى اختيار الفندق.", "error")
                return redirect(url_for("group_bookings"))

            try:
                hotel_id = int(hotel_value)
            except (ValueError, TypeError):
                flash("الفندق المحدد غير صحيح.", "error")
                return redirect(url_for("group_bookings"))

            if not room_ids:
                flash("يرجى اختيار غرفة واحدة على الأقل.", "error")
                return redirect(url_for("group_bookings"))

            if requested_rooms != len(room_ids):
                flash(
                    "عدد الغرف المطلوبة يجب أن يساوي عدد الغرف التي اخترتها.",
                    "error",
                )
                return redirect(url_for("group_bookings"))

            # 4. التحقق من التواريخ
            try:
                ci = datetime.fromisoformat(checkin)
                co = datetime.fromisoformat(checkout)
            except (ValueError, TypeError):
                flash("تاريخ الدخول أو المغادرة غير صحيح.", "error")
                return redirect(url_for("group_bookings"))

            nights = (co - ci).days

            if nights <= 0:
                flash(
                    "يجب أن يكون تاريخ المغادرة بعد تاريخ الدخول بيوم واحد على الأقل.",
                    "error",
                )
                return redirect(url_for("group_bookings"))

            # 5. بدء معاملة قاعدة البيانات
            connection.execute("BEGIN")

            placeholders = ",".join("?" for _ in room_ids)

            room_rows = connection.execute(
                f"""
                SELECT *
                FROM rooms
                WHERE id IN ({placeholders})
                  AND hotel_id = ?
                """,
                (*room_ids, hotel_id),
            ).fetchall()

            rooms_by_id = {room["id"]: room for room in room_rows}

            if len(rooms_by_id) != len(room_ids):
                connection.rollback()
                flash(
                    "إحدى الغرف غير موجودة أو لا تتبع الفندق المحدد.",
                    "error",
                )
                return redirect(url_for("group_bookings"))

            # 6. التحقق من حالة كل غرفة وتعارض المواعيد
            valid_rooms = []
            total = 0.0

            unavailable_statuses = {
                "maintenance",
                "out_of_service",
                "cleaning",
            }

            for room_id in room_ids:
                room = rooms_by_id[room_id]

                if room["status"] in unavailable_statuses:
                    connection.rollback()
                    flash(
                        f"الغرفة رقم {room['number']} غير متاحة حاليًا.",
                        "error",
                    )
                    return redirect(url_for("group_bookings"))

                if (
                    room_conflict(
                        connection, room_id, checkin, checkout
                    )
                    or group_room_conflict(
                        connection, room_id, checkin, checkout
                    )
                ):
                    connection.rollback()
                    flash(
                        f"الغرفة رقم {room['number']} لديها حجز "
                        "يتعارض مع هذه التواريخ.",
                        "error",
                    )
                    return redirect(url_for("group_bookings"))

                valid_rooms.append(room)
                total += float(room["price"]) * nights

            if paid > total:
                paid = total

            # 7. إنشاء رقم الحجز
            code = "GR-" + secrets.token_hex(3).upper()

            cursor = connection.execute(
                """
                INSERT INTO group_bookings (
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

            # 8. ربط الغرف بالحجز
            for room in valid_rooms:
                connection.execute(
                    """
                    INSERT INTO group_booking_rooms (
                        group_booking_id,
                        room_id,
                        price
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        group_id,
                        room["id"],
                        float(room["price"]) * nights,
                    ),
                )

            # 9. تسجيل الدفعة إذا كان هناك مبلغ مدفوع
            if paid > 0:
                connection.execute(
                    """
                    INSERT INTO payments (
                        group_booking_id,
                        amount,
                        method,
                        payment_url,
                        status,
                        created_at,
                        created_by
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        group_id,
                        paid,
                        payment_method,
                        "",
                        "paid",
                        datetime.now().isoformat(),
                        session["uid"],
                    ),
                )

            # 10. حفظ جميع التغييرات
            connection.commit()

            flash(
                f"تم إنشاء حجز المجموعة {code} بنجاح "
                f"لعدد {len(valid_rooms)} غرفة.",
                "ok",
            )
            return redirect(url_for("group_bookings"))

        # 11. عرض الحجوزات المسجلة
        rows = connection.execute(
            """
            SELECT
                gb.*,
                h.name AS hotel_name,
                c.name AS company_name,
                COALESCE(r.room_count, 0) AS room_count
            FROM group_bookings AS gb
            LEFT JOIN hotels AS h
                ON h.id = gb.hotel_id
            LEFT JOIN companies AS c
                ON c.id = gb.company_id
            LEFT JOIN (
                SELECT
                    group_booking_id,
                    COUNT(*) AS room_count
                FROM group_booking_rooms
                GROUP BY group_booking_id
            ) AS r
                ON r.group_booking_id = gb.id
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

        return render_template(
            "group_bookings.html",
            group_bookings=rows,
            hotels=hotels_list,
            companies=companies_list,
            rooms=rooms_list,
        )

    except Exception:
        connection.rollback()
        app.logger.exception("Error in group_bookings")
        flash(
            "حدث خطأ أثناء معالجة حجز المجموعة. راجع سجل الأخطاء في Render.",
            "error",
        )
        return redirect(url_for("group_bookings"))

    finally:
        connection.close()
