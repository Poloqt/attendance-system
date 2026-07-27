import os
import io
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
# "https://attendance-system.onrender.com"
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")

DB_PATH = os.path.join(os.path.dirname(__file__), "attendance.db")

# Hardcoded admin account — change the password below before deploying.
ADMIN_USERNAME = "gertrudemonic"
ADMIN_PASSWORD_HASH = generate_password_hash("Fluffdobby25", method="pbkdf2:sha256")


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
        CREATE TABLE IF NOT EXISTS meeting (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            date TEXT NOT NULL,
            minutes TEXT NOT NULL
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
            FOREIGN KEY (member_id) REFERENCES members (id)
        )
    """)
    existing = db.execute("SELECT COUNT(*) AS c FROM meeting").fetchone()
    if existing["c"] == 0:
        db.execute(
            "INSERT INTO meeting (id, title, date, minutes) VALUES (1, ?, ?, ?)",
            ("Team Meeting", datetime.now().strftime("%B %d, %Y"),
             "Minutes of the meeting will appear here once the admin adds them.")
        )
    db.commit()
    db.close()


# Make sure the database and tables exist as soon as the app module loads.
# This matters on hosts like Render, where gunicorn imports this file
# directly instead of running "python app.py".
init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/")
def main():
    db = get_db()
    meeting = db.execute("SELECT * FROM meeting WHERE id = 1").fetchone()
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
    meeting = db.execute("SELECT * FROM meeting WHERE id = 1").fetchone()
    members = db.execute("SELECT * FROM members ORDER BY name").fetchall()

    if request.method == "POST":
        member_id = request.form.get("member_id")
        if not member_id:
            flash("Please select your name to check in.")
            return redirect(url_for("attend"))

        already = db.execute(
            "SELECT * FROM attendance WHERE member_id = ? AND meeting_id = 1",
            (member_id,)
        ).fetchone()

        if not already:
            db.execute(
                "INSERT INTO attendance (member_id, meeting_id, timestamp) VALUES (?, 1, ?)",
                (member_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            db.commit()

        member = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        return render_template("attend.html", meeting=meeting, member=member, checked_in=True)

    return render_template("attend.html", meeting=meeting, members=members, checked_in=False)


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


@app.route("/admin")
@login_required
def admin():
    db = get_db()
    meeting = db.execute("SELECT * FROM meeting WHERE id = 1").fetchone()
    members = db.execute("SELECT * FROM members ORDER BY name").fetchall()
    attendance = db.execute("""
        SELECT attendance.timestamp, members.name
        FROM attendance
        JOIN members ON members.id = attendance.member_id
        WHERE attendance.meeting_id = 1
        ORDER BY attendance.timestamp DESC
    """).fetchall()
    present_ids = {row["member_id"] for row in db.execute(
        "SELECT member_id FROM attendance WHERE meeting_id = 1"
    ).fetchall()}
    return render_template(
        "admin.html",
        meeting=meeting,
        members=members,
        attendance=attendance,
        present_count=len(present_ids),
        total_count=len(members),
    )


@app.route("/admin/update_minutes", methods=["POST"])
@login_required
def update_minutes():
    db = get_db()
    title = request.form.get("title", "").strip()
    date = request.form.get("date", "").strip()
    minutes = request.form.get("minutes", "").strip()
    db.execute(
        "UPDATE meeting SET title = ?, date = ?, minutes = ? WHERE id = 1",
        (title, date, minutes)
    )
    db.commit()
    flash("Meeting details updated.")
    return redirect(url_for("admin"))


@app.route("/admin/add_member", methods=["POST"])
@login_required
def add_member():
    db = get_db()
    name = request.form.get("name", "").strip()
    if name:
        try:
            db.execute("INSERT INTO members (name) VALUES (?)", (name,))
            db.commit()
        except sqlite3.IntegrityError:
            flash("That member already exists.")
    return redirect(url_for("admin"))


@app.route("/admin/delete_member/<int:member_id>", methods=["POST"])
@login_required
def delete_member(member_id):
    db = get_db()
    db.execute("DELETE FROM attendance WHERE member_id = ?", (member_id,))
    db.execute("DELETE FROM members WHERE id = ?", (member_id,))
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/reset_attendance", methods=["POST"])
@login_required
def reset_attendance():
    db = get_db()
    db.execute("DELETE FROM attendance WHERE meeting_id = 1")
    db.commit()
    flash("Attendance has been reset for a new session.")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=True)
