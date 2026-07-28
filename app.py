import os
import io
import csv
import sqlite3
from functools import wraps
from datetime import datetime

import qrcode
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, send_file, g
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")

# Set this to wherever your app is actually hosted, e.g.
# "https://varrons-attendance-system.onrender.com"
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")

DB_PATH = os.path.join(os.path.dirname(__file__), "attendance.db")

# ---------------------------------------------------------------------------
# Admin credentials come from environment variables, NOT from code.
# Set ADMIN_USERNAME and ADMIN_PASSWORD in Render's Environment tab.
# ---------------------------------------------------------------------------
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
_admin_password = os.environ.get("ADMIN_PASSWORD", "changeme123")
ADMIN_PASSWORD_HASH = generate_password_hash(_admin_password, method="pbkdf2:sha256")

# NOTE: On Render's free tier, the disk this file lives on is wiped whenever
# the app restarts, redeploys, or spins down from inactivity. The names below
# will always come back because they're re-inserted on every startup, but any
# member added later through "Add Member" / CSV import, and any attendance
# check-ins, will be lost on the next restart. To make everything persist
# permanently, connect a real database (e.g. Neon, Supabase) instead.
DEFAULT_MEMBERS = [
    "Abril, Mary Margarette B.", "Alday, Rogel Victor L.", "Alinea, Maria Nhelyn A.",
    "Añabieza, Paul Vincent P.", "Arellano, Lara Jhane A.", "Bonita, Eureem Clyde M.",
    "Conopio, John Denver NA", "Contreras, Marion Kyle", "Cortez, Carmela Joy B.",
    "Daileg, Nayeli Erica C.", "De Luna, Andrea Bianca P.", "De Luna, Christine Anne, P.",
    "De Pasion, Darren Mae F.", "De Villa, Reign Jhudiel O.", "Delfin, Earl Jan L.",
    "Dizon, Faustina Maryella R.", "Educado, Justine John L.", "Estoya, Jay Em Clark R.",
    "Famoleras, Princess Zyra T.", "Fortuna, Janeld Han M.", "Fortunato, Arvin Jhon S.",
    "Gamido, Joiaquin Gabriel U.", "Guevarra, Mary Anne G.", "Jose, Yuan Miguel C.",
    "Layosa, Iana Eirene R.", "Lazo, Fatima D.", "Lentijas, Jerome P.",
    "Lorenzo, Jae M.", "Losito, John Dharryl L.", "Loyola, Lance",
    "Magsisi, Lujelle V.", "Malabanan, Al-jiarro V.", "Maligalig, Curvy Romelson G.",
    "Manzano, Cloyd Louie M.", "Mendoza, Aliyah Dhana S.", "Mendoza, Chellarie P.",
    "Mendoza, Christian Laurence S.", "Menguito, Marc Luis C.", "Mortel, Sealthiel Ramos",
    "Mula, John Jessie M.", "Napoles, Victor Emmanuel A.", "Oñate, Nhil Andrei I.",
    "Orge, Marian Carmil C.", "Pagaspas, Gertrude Monic, L.", "Parale, Luis Joaquin G.",
    "Patricio, Russell Francesca C.", "Pelito, Samantha Nina", "Peñafiel, Angelle V.",
    "Plandez, Mark Xavier", "Revilleza, Aicel, C.", "Rosas, Kiert Hanzel D.",
    "Rubio, Lannz Angel G.", "Sagaya, Therese Ann Hope B.", "Sanchez, Arwin Jasper Y.",
    "Santos, Aceyah P.", "Saplala, Maria Nazreen E.", "Satorre, Peters Edward F.",
    "Supremo, Xyrelle C.", "Tapat, Chris Matthew M.", "Tayaban, Jemimah P.",
    "Titular, Khim RIezell", "Turla, Dhanalene Angelica B.", "Valencia, Kenneth",
    "Vallo, Christian Josef P.", "Ventayen, John Carlo P.", "Villamayor, Mark Emanuel K.",
    "Aguila, Dailyn Rui C.", "Almeda Kaye D.", "Barachina, Aliana Monique",
    "Canicosa, Arizza, H.", "Datu, Genesis G.", "Decano, Justine R.",
    "Epino, Nicole Heart A.", "Formaran, Am Kirstin R.", "Francisco, Liwliwa B.",
    "Garcia, Danielle, E", "Ilagan, Paul Warren J.", "Lentijas, Catherine P.",
    "Manalang, Jer Maine", "Matoto, Matly L.", "Melendres, Levi Joseph S.",
    "Ortega, Kyrill James S.", "Quilloy, Lee Robin", "Ramos, Nicole Jasmine",
    "Rance, Alison P.", "Relos, Jalen Rhudee B.", "Salvador, Eduardo Jose C.",
    "Sañez, Azel Mae A.", "Teloza, Rosella Mae O.", "Tunay, Christine Gail, D.",
    "Yalung, Aina Jhynelle B.", "Yapan, Gwyneth Hannah S.", "Evangelio, Zandrix Gabrielle C.",
    "Semilla, Jethro Kyle M.", "Cuyno, Joaquin Iñigo A.",
]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            date TEXT NOT NULL,
            minutes TEXT NOT NULL DEFAULT '',
            cutoff_time TEXT,
            is_active INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            meeting_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'on-time',
            UNIQUE (member_id, meeting_id),
            FOREIGN KEY (member_id) REFERENCES members (id),
            FOREIGN KEY (meeting_id) REFERENCES meetings (id)
        )
    """)

    existing = db.execute("SELECT COUNT(*) AS c FROM meetings").fetchone()
    if existing["c"] == 0:
        db.execute(
            "INSERT INTO meetings (title, date, minutes, is_active) VALUES (?, ?, ?, 1)",
            ("Team Meeting", datetime.now().strftime("%B %d, %Y"),
             "Minutes of the meeting will appear here once the admin adds them.")
        )

    for name in DEFAULT_MEMBERS:
        db.execute("INSERT OR IGNORE INTO members (name) VALUES (?)", (name,))

    db.commit()
    db.close()


init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def get_active_meeting(db):
    return db.execute(
        "SELECT * FROM meetings WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------
@app.route("/")
def main():
    db = get_db()
    meeting = get_active_meeting(db)
    return render_template("main.html", meeting=meeting)


@app.route("/qr.png")
def qr_code():
    attend_url = f"{BASE_URL}{url_for('attend')}"
    img = qrcode.make(attend_url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/attend", methods=["GET", "POST"])
def attend():
    db = get_db()
    meeting = get_active_meeting(db)

    if meeting is None:
        return render_template("attend.html", meeting=None, members=[], checked_in=False)

    members = db.execute("SELECT * FROM members ORDER BY name").fetchall()

    if request.method == "POST":
        member_id = request.form.get("member_id")
        if not member_id:
            flash("Please select your name to check in.")
            return redirect(url_for("attend"))

        already = db.execute(
            "SELECT * FROM attendance WHERE member_id = ? AND meeting_id = ?",
            (member_id, meeting["id"])
        ).fetchone()

        now = datetime.now()
        status = "on-time"
        if meeting["cutoff_time"]:
            try:
                cutoff_h, cutoff_m = [int(p) for p in meeting["cutoff_time"].split(":")]
                cutoff_dt = now.replace(hour=cutoff_h, minute=cutoff_m, second=0, microsecond=0)
                if now > cutoff_dt:
                    status = "late"
            except (ValueError, TypeError):
                pass

        if not already:
            db.execute(
                "INSERT INTO attendance (member_id, meeting_id, timestamp, status) "
                "VALUES (?, ?, ?, ?)",
                (member_id, meeting["id"], now.strftime("%Y-%m-%d %I:%M %p"), status)
            )
            db.commit()

        member = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        record = db.execute(
            "SELECT * FROM attendance WHERE member_id = ? AND meeting_id = ?",
            (member_id, meeting["id"])
        ).fetchone()
        return render_template(
            "attend.html", meeting=meeting, member=member,
            checked_in=True, record=record
        )

    return render_template("attend.html", meeting=meeting, members=members, checked_in=False)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session["logged_in"] = True
            return redirect(url_for("admin"))
        flash("Invalid username or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------
@app.route("/admin")
@login_required
def admin():
    db = get_db()
    meeting = get_active_meeting(db)
    all_meetings = db.execute("SELECT * FROM meetings ORDER BY id DESC").fetchall()
    members = db.execute("SELECT * FROM members ORDER BY name").fetchall()

    attendance = []
    present_ids = set()
    if meeting:
        attendance = db.execute("""
            SELECT attendance.timestamp, attendance.status, members.name, members.id AS member_id
            FROM attendance
            JOIN members ON members.id = attendance.member_id
            WHERE attendance.meeting_id = ?
            ORDER BY attendance.timestamp DESC
        """, (meeting["id"],)).fetchall()
        present_ids = {row["member_id"] for row in attendance}

    total_meetings = db.execute("SELECT COUNT(*) AS c FROM meetings").fetchone()["c"] or 1

    counts = db.execute("""
        SELECT member_id, COUNT(*) AS attended
        FROM attendance
        GROUP BY member_id
    """).fetchall()
    attended_counts = {row["member_id"]: row["attended"] for row in counts}

    member_stats = []
    for m in members:
        attended = attended_counts.get(m["id"], 0)
        pct = round((attended / total_meetings) * 100) if total_meetings else 0
        member_stats.append({"id": m["id"], "name": m["name"], "attended": attended, "pct": pct})

    return render_template(
        "admin.html",
        meeting=meeting,
        all_meetings=all_meetings,
        members=member_stats,
        attendance=attendance,
        present_count=len(present_ids),
        total_count=len(members),
        total_meetings=total_meetings,
    )


@app.route("/admin/update_minutes", methods=["POST"])
@login_required
def update_minutes():
    db = get_db()
    meeting = get_active_meeting(db)
    if not meeting:
        flash("No active meeting to update.")
        return redirect(url_for("admin"))

    title = request.form.get("title", "").strip()
    date = request.form.get("date", "").strip()
    minutes = request.form.get("minutes", "").strip()
    cutoff_time = request.form.get("cutoff_time", "").strip() or None

    db.execute(
        "UPDATE meetings SET title = ?, date = ?, minutes = ?, cutoff_time = ? WHERE id = ?",
        (title, date, minutes, cutoff_time, meeting["id"])
    )
    db.commit()
    flash("Meeting details updated.")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------
@app.route("/admin/meetings/new", methods=["POST"])
@login_required
def new_meeting():
    db = get_db()
    title = request.form.get("title", "").strip() or "New Meeting"
    date = request.form.get("date", "").strip() or datetime.now().strftime("%B %d, %Y")
    cutoff_time = request.form.get("cutoff_time", "").strip() or None

    db.execute("UPDATE meetings SET is_active = 0")
    db.execute(
        "INSERT INTO meetings (title, date, minutes, cutoff_time, is_active) "
        "VALUES (?, ?, ?, ?, 1)",
        (title, date, "", cutoff_time)
    )
    db.commit()
    flash(f'New meeting "{title}" created and set as active.')
    return redirect(url_for("admin"))


@app.route("/admin/meetings/<int:meeting_id>/activate", methods=["POST"])
@login_required
def activate_meeting(meeting_id):
    db = get_db()
    db.execute("UPDATE meetings SET is_active = 0")
    db.execute("UPDATE meetings SET is_active = 1 WHERE id = ?", (meeting_id,))
    db.commit()
    flash("Active meeting changed.")
    return redirect(url_for("admin"))


@app.route("/admin/meetings/<int:meeting_id>/reset_attendance", methods=["POST"])
@login_required
def reset_attendance(meeting_id):
    db = get_db()
    db.execute("DELETE FROM attendance WHERE meeting_id = ?", (meeting_id,))
    db.commit()
    flash("Attendance has been reset for that meeting.")
    return redirect(url_for("admin"))


@app.route("/admin/meetings/<int:meeting_id>/export")
@login_required
def export_meeting(meeting_id):
    """Downloadable CSV (opens in Excel) listing EVERY member with their
    status for this meeting: Present, Late, or Absent."""
    db = get_db()
    meeting = db.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    if not meeting:
        flash("Meeting not found.")
        return redirect(url_for("admin"))

    members = db.execute("SELECT * FROM members ORDER BY name").fetchall()
    records = db.execute(
        "SELECT member_id, timestamp, status FROM attendance WHERE meeting_id = ?",
        (meeting_id,)
    ).fetchall()
    record_by_member = {r["member_id"]: r for r in records}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Meeting", meeting["title"]])
    writer.writerow(["Date", meeting["date"]])
    writer.writerow([])
    writer.writerow(["Name", "Status", "Time Checked In"])

    present_count = late_count = absent_count = 0
    for m in members:
        record = record_by_member.get(m["id"])
        if record is None:
            status_label = "Absent"
            time_label = ""
            absent_count += 1
        elif record["status"] == "late":
            status_label = "Late"
            time_label = record["timestamp"]
            late_count += 1
        else:
            status_label = "Present"
            time_label = record["timestamp"]
            present_count += 1
        writer.writerow([m["name"], status_label, time_label])

    writer.writerow([])
    writer.writerow(["Present", present_count])
    writer.writerow(["Late", late_count])
    writer.writerow(["Absent", absent_count])
    writer.writerow(["Total Members", len(members)])

    mem_buf = io.BytesIO(buf.getvalue().encode("utf-8-sig"))  # BOM so Excel reads it cleanly
    mem_buf.seek(0)
    safe_title = "".join(c if c.isalnum() else "_" for c in meeting["title"])
    return send_file(
        mem_buf, mimetype="text/csv", as_attachment=True,
        download_name=f"attendance_{safe_title}.csv"
    )


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------
@app.route("/admin/add_member", methods=["POST"])
@login_required
def add_member():
    db = get_db()
    name = request.form.get("name", "").strip()
    if name:
        try:
            db.execute("INSERT INTO members (name) VALUES (?)", (name,))
            db.commit()
            flash(f'Added "{name}".')
        except sqlite3.IntegrityError:
            flash("That member already exists.")
    return redirect(url_for("admin"))


@app.route("/admin/import_members", methods=["POST"])
@login_required
def import_members():
    db = get_db()
    file = request.files.get("member_file")
    if not file or file.filename == "":
        flash("No file selected.")
        return redirect(url_for("admin"))

    content = file.read().decode("utf-8", errors="ignore")
    names = [line.strip() for line in content.splitlines() if line.strip()]

    added, skipped = 0, 0
    for name in names:
        if name.lower() in ("name", "names"):
            continue
        try:
            db.execute("INSERT INTO members (name) VALUES (?)", (name,))
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1
    db.commit()
    flash(f"Imported {added} new member(s), skipped {skipped} duplicate(s).")
    return redirect(url_for("admin"))


@app.route("/admin/delete_member/<int:member_id>", methods=["POST"])
@login_required
def delete_member(member_id):
    db = get_db()
    db.execute("DELETE FROM attendance WHERE member_id = ?", (member_id,))
    db.execute("DELETE FROM members WHERE id = ?", (member_id,))
    db.commit()
    return redirect(url_for("admin"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=True)
