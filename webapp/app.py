from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
import json
import os
import datetime
import re
from functools import wraps

app = Flask(__name__)
app.secret_key = "change-this-to-something-secret"  # needed for sessions/login
DATA_FILE = os.path.join(os.path.dirname(__file__), "medicines.json")
USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")

# Render's server runs on UTC time, not Indian time — so we force
# every "now" in this app to be Indian Standard Time (UTC+5:30),
# regardless of what timezone the actual server is physically in.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.datetime.now(IST)


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

HEALTH_TIPS = [
    "Drink at least 8 glasses of water daily.",
    "Take a 15-minute walk every morning.",
    "Eat fresh fruits and vegetables every day.",
    "Sleep at least 7-8 hours per night.",
    "Never skip your prescribed medicines.",
    "Keep your mind active — read books or solve puzzles.",
    "Visit your doctor for regular check-ups.",
    "Practice deep breathing to reduce stress.",
    "Brush your teeth twice daily for good oral health.",
    "Get some sunlight every day for Vitamin D.",
]


# ------------------------------------------------------------------
# USER ACCOUNTS (each person gets their own login, like Gmail)
# ------------------------------------------------------------------

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            try:
                data = json.load(f)
            except (json.JSONDecodeError, ValueError):
                return []
    else:
        return []

    if not isinstance(data, list):
        return []

    # Ignore any old/broken entries that don't have the fields we expect
    return [u for u in data if isinstance(u, dict) and "email" in u and "password_hash" in u]


def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)


def find_user(email):
    for u in load_users():
        if u.get("email", "").lower() == email.lower():
            return u
    return None


# ------------------------------------------------------------------
# MEDICINES — now stored PER USER, not shared by everyone
#
# Structure of medicines.json:
# {
#   "someone@gmail.com": [ {medicine1}, {medicine2}, ... ],
#   "another@gmail.com": [ {medicine1}, ... ]
# }
# ------------------------------------------------------------------

def load_all_medicines():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try:
                data = json.load(f)
            except (json.JSONDecodeError, ValueError):
                return {}
        # Old version stored a plain list — if we find that, start fresh
        # with the new per-user dictionary format instead of crashing.
        if not isinstance(data, dict):
            return {}
        return data
    return {}


def save_all_medicines(all_medicines):
    with open(DATA_FILE, "w") as f:
        json.dump(all_medicines, f, indent=4)


def get_user_medicines(email):
    all_medicines = load_all_medicines()
    return all_medicines.get(email, [])


def save_user_medicines(email, medicines):
    all_medicines = load_all_medicines()
    all_medicines[email] = medicines
    save_all_medicines(all_medicines)


def next_id(medicines):
    return (max([m["id"] for m in medicines]) + 1) if medicines else 1


ALL_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def format_time_12hr(time_str):
    """Convert '09:00' -> '9:00 AM' for display."""
    try:
        t = datetime.datetime.strptime(time_str, "%H:%M")
        return t.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return time_str


def format_days(days_list):
    """Turn ['Mon','Tue',...] into a friendly label."""
    if not days_list or set(days_list) == set(ALL_DAYS):
        return "Every day"
    return ", ".join(d for d in ALL_DAYS if d in days_list)


def classify(medicines, current_time, today_short):
    due, upcoming, taken, other_days = [], [], [], []
    for m in medicines:
        scheduled_days = m.get("days") or ALL_DAYS  # old medicines default to every day
        if today_short not in scheduled_days:
            other_days.append(m)
        elif m["taken"]:
            taken.append(m)
        elif m["time"] <= current_time:
            due.append(m)
        else:
            upcoming.append(m)
    upcoming.sort(key=lambda m: m["time"])
    return due, upcoming, taken, other_days


def login_required(view_function):
    @wraps(view_function)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view_function(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------------
# SIGN UP — anyone can create their own account
# ------------------------------------------------------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not EMAIL_PATTERN.match(email):
            error = "Please enter a valid email address."
        elif len(password) < 6:
            error = "Password must be at least 6 characters long."
        elif password != confirm:
            error = "Passwords do not match."
        elif find_user(email):
            error = "An account with this email already exists."
        else:
            users = load_users()
            users.append({
                "email": email,
                "password_hash": generate_password_hash(password),
            })
            save_users(users)
            # log them in immediately after signup
            session["logged_in"] = True
            session["username"] = email
            return redirect(url_for("dashboard"))

    return render_template("signup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        matched_user = find_user(email)

        if matched_user and check_password_hash(matched_user["password_hash"], password):
            session["logged_in"] = True
            session["username"] = email
            return redirect(url_for("dashboard"))
        else:
            error = "Incorrect email or password. Please try again."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------
# DASHBOARD & MEDICINE ROUTES — all scoped to the logged-in user
# ------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    email = session["username"]
    medicines = get_user_medicines(email)
    now = now_ist()
    current_time = now.strftime("%H:%M")
    today_short = now.strftime("%a")  # e.g. "Mon"
    due, upcoming, taken, other_days = classify(medicines, current_time, today_short)

    if due:
        next_dose = due[0]
        next_status = "due"
    elif upcoming:
        next_dose = upcoming[0]
        next_status = "upcoming"
    else:
        next_dose = None
        next_status = "done"

    tip = HEALTH_TIPS[now.timetuple().tm_yday % len(HEALTH_TIPS)]

    return render_template(
        "index.html",
        medicines=medicines,
        due=due,
        upcoming=upcoming,
        taken=taken,
        other_days=other_days,
        today_short=today_short,
        all_days=ALL_DAYS,
        format_time_12hr=format_time_12hr,
        format_days=format_days,
        next_dose=next_dose,
        next_status=next_status,
        current_date=now.strftime("%A, %d %B %Y"),
        current_time=now.strftime("%I:%M %p"),
        tip=tip,
        total=len(medicines),
        taken_count=len(taken),
        user_email=email,
    )


@app.route("/api/due")
@login_required
def api_due():
    """
    Returns the medicines that are currently due, as JSON.
    The browser polls this every so often to decide whether
    to fire a notification — it never fires anything itself,
    it just reports the current state using the server's clock.
    """
    email = session["username"]
    medicines = get_user_medicines(email)
    now = now_ist()
    current_time = now.strftime("%H:%M")
    today_short = now.strftime("%a")
    due, _, _, _ = classify(medicines, current_time, today_short)

    return jsonify({
        "due": [
            {
                "id": m["id"],
                "name": m["name"],
                "dosage": m["dosage"],
                "time": format_time_12hr(m["time"]),
            }
            for m in due
        ]
    })


@app.route("/add", methods=["POST"])
@login_required
def add_medicine():
    email = session["username"]
    medicines = get_user_medicines(email)

    name = request.form.get("name", "").strip()
    dosage = request.form.get("dosage", "").strip()
    time_str = request.form.get("time", "").strip()
    note = request.form.get("note", "").strip()
    days = request.form.getlist("days")  # e.g. ["Mon", "Wed", "Fri"]

    if name and dosage and time_str:
        medicines.append({
            "id": next_id(medicines),
            "name": name,
            "dosage": dosage,
            "time": time_str,
            "note": note if note else "No special note",
            "taken": False,
            "days": days if days else ALL_DAYS,  # nothing picked = every day
        })
        save_user_medicines(email, medicines)

    return redirect(url_for("dashboard"))


@app.route("/edit/<int:med_id>", methods=["POST"])
@login_required
def edit_medicine(med_id):
    email = session["username"]
    medicines = get_user_medicines(email)

    name = request.form.get("name", "").strip()
    dosage = request.form.get("dosage", "").strip()
    time_str = request.form.get("time", "").strip()
    note = request.form.get("note", "").strip()
    days = request.form.getlist("days")

    if name and dosage and time_str:
        for m in medicines:
            if m["id"] == med_id:
                m["name"] = name
                m["dosage"] = dosage
                m["time"] = time_str
                m["note"] = note if note else "No special note"
                m["days"] = days if days else ALL_DAYS
                # Note: we deliberately do NOT touch m["taken"] here —
                # editing details shouldn't undo today's "already taken" tick.
                break
        save_user_medicines(email, medicines)

    return redirect(url_for("dashboard"))


@app.route("/take/<int:med_id>", methods=["POST"])
@login_required
def mark_taken(med_id):
    email = session["username"]
    medicines = get_user_medicines(email)
    for m in medicines:
        if m["id"] == med_id:
            m["taken"] = True
    save_user_medicines(email, medicines)
    return redirect(url_for("dashboard"))


@app.route("/undo/<int:med_id>", methods=["POST"])
@login_required
def undo_taken(med_id):
    email = session["username"]
    medicines = get_user_medicines(email)
    for m in medicines:
        if m["id"] == med_id:
            m["taken"] = False
    save_user_medicines(email, medicines)
    return redirect(url_for("dashboard"))


@app.route("/delete/<int:med_id>", methods=["POST"])
@login_required
def delete_medicine(med_id):
    email = session["username"]
    medicines = get_user_medicines(email)
    medicines = [m for m in medicines if m["id"] != med_id]
    save_user_medicines(email, medicines)
    return redirect(url_for("dashboard"))


@app.route("/reset", methods=["POST"])
@login_required
def reset_daily():
    email = session["username"]
    medicines = get_user_medicines(email)
    for m in medicines:
        m["taken"] = False
    save_user_medicines(email, medicines)
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
