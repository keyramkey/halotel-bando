import os
import secrets
import time
import requests
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "kijiji-tanzania-secret-key-change-me")

# Session inabaki muda mrefu (mwaka 1) mpaka user atoke mwenyewe
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Railway hutumia HTTPS — cookie salama
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") != "development"
# Refresh cookie kila request ili isimalize
app.config["SESSION_REFRESH_EACH_REQUEST"] = True

basedir = os.path.abspath(os.path.dirname(__file__))

# Database - Railway PostgreSQL
database_url = os.environ.get("DATABASE_URL")
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "instance", "kijiji.db")

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

_upload_root = os.environ.get("UPLOAD_ROOT") or os.path.join(basedir, "static", "uploads")
app.config["UPLOAD_FOLDER"] = _upload_root
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "slide"), exist_ok=True)
os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "nakala"), exist_ok=True)
os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "agency"), exist_ok=True)
os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)

_static_uploads = os.path.join(basedir, "static", "uploads")
os.makedirs(_static_uploads, exist_ok=True)
os.makedirs(os.path.join(_static_uploads, "slide"), exist_ok=True)

db = SQLAlchemy(app)

# Manual payment
PAYMENT_NUMBER = os.environ.get("PAYMENT_NUMBER", "37912416")
PAYMENT_NAME = os.environ.get("PAYMENT_NAME", "Matondo Maduhu")
PAYMENT_NETWORK = os.environ.get("PAYMENT_NETWORK", "Vodacom")

# ClickPesa
CLICKPESA_CLIENT_ID = os.environ.get("CLICKPESA_CLIENT_ID")
CLICKPESA_API_KEY = os.environ.get("CLICKPESA_API_KEY")

# SMS OTP (Beem Africa - SMS ya kawaida, si bulk marketing)
# Jisajili: https://beem.africa  → API key + Secret
SMS_API_KEY = os.environ.get("SMS_API_KEY", "")
SMS_API_SECRET = os.environ.get("SMS_API_SECRET", "")
SMS_SENDER_ID = os.environ.get("SMS_SENDER_ID", "INFO")  # jina au namba; max 11. Si lazima Sender Name maalum

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reset_otp = db.Column(db.String(10), default="")
    reset_otp_expires = db.Column(db.DateTime, nullable=True)
    orders = db.relationship("Order", backref="user", lazy=True)
    messages = db.relationship("Message", backref="user", lazy=True, foreign_keys="Message.user_id")
    applications = db.relationship("Application", backref="user", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Bundle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.String(50), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    validity = db.Column(db.String(50), default="30 siku")
    is_active = db.Column(db.Boolean, default=True)


class Offer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.String(50), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(255), default="")
    is_active = db.Column(db.Boolean, default=True)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.String(80), nullable=False)
    price = db.Column(db.Integer, default=0)
    note = db.Column(db.Text, default="")
    status = db.Column(db.String(30), default="awaiting_payment")  # awaiting_payment | pending | completed | rejected | failed
    order_reference = db.Column(db.String(30), unique=True, nullable=True)
    fail_reason = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sender = db.Column(db.String(20), nullable=False)
    message = db.Column(db.Text, default="")
    image = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Network(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    network = db.Column(db.String(50), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    services = db.relationship("AgencyService", backref="network_obj", lazy=True)


class AgencyService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    network_id = db.Column(db.Integer, db.ForeignKey("network.id"), nullable=False)
    network = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), default="")
    icon = db.Column(db.String(10), default="📱")
    is_active = db.Column(db.Boolean, default=True)


class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_code = db.Column(db.String(30), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey("agency_service.id"), nullable=True)
    network = db.Column(db.String(50), nullable=False)
    service_type = db.Column(db.String(120), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    id_type = db.Column(db.String(50), default="")
    id_number = db.Column(db.String(80), default="")
    line_type = db.Column(db.String(50), default="")
    region = db.Column(db.String(80), default="")
    district = db.Column(db.String(80), default="")
    ward = db.Column(db.String(80), default="")
    description = db.Column(db.Text, default="")
    status = db.Column(db.String(30), default="pending")
    admin_note = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    messages = db.relationship("AgencyMessage", backref="application", lazy=True, cascade="all, delete-orphan")
    # Agency Lipa / Uwakala extra fields
    tin_number = db.Column(db.String(50), default="")
    target_phone = db.Column(db.String(20), default="")
    contact_phone = db.Column(db.String(20), default="")
    tin_cert = db.Column(db.String(255), default="")
    id_doc = db.Column(db.String(255), default="")
    shop_photo = db.Column(db.String(255), default="")
    extra_data = db.Column(db.Text, default="")


class AgencyMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), nullable=False)
    sender = db.Column(db.String(20), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Slide(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(500), default="")
    title = db.Column(db.String(120), default="")
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class NakalaRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    request_code = db.Column(db.String(30), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    service_type = db.Column(db.String(50), nullable=False)  # tin | license | nida
    full_name = db.Column(db.String(150), nullable=False)
    mother_name = db.Column(db.String(150), default="")
    nida_number = db.Column(db.String(20), default="")
    phone1 = db.Column(db.String(20), nullable=False)
    phone2 = db.Column(db.String(20), default="")
    primary_school = db.Column(db.String(150), default="")
    year_completed = db.Column(db.String(10), default="")
    school_district = db.Column(db.String(80), default="")
    school_region = db.Column(db.String(80), default="")
    nida_reg_district = db.Column(db.String(80), default="")
    nida_reg_region = db.Column(db.String(80), default="")
    nida_reg_street = db.Column(db.String(120), default="")
    nida_reg_phone = db.Column(db.String(20), default="")
    had_tin_before = db.Column(db.String(10), default="")
    license_type = db.Column(db.String(200), default="")
    price = db.Column(db.Integer, default=0)
    status = db.Column(db.String(30), default="pending")
    admin_note = db.Column(db.Text, default="")
    result_message = db.Column(db.Text, default="")
    result_file = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User", backref="nakala_requests")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


@app.context_processor
def inject_globals():
    return {
        "current_user": get_current_user(),
        "PAYMENT_NUMBER": PAYMENT_NUMBER,
        "PAYMENT_NAME": PAYMENT_NAME,
        "PAYMENT_NETWORK": PAYMENT_NETWORK,
    }


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user():
            flash("Tafadhali ingia kwanza ili upate huduma.", "error")
            # Kumbuka ukurasa aliotaka aende baada ya login
            session["next_url"] = request.path
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user or not user.is_admin:
            flash("Huna ruhusa ya kufikia ukurasa huu.", "error")
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return decorated


def generate_application_code():
    return "APP-" + secrets.token_hex(4).upper()


ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_SLIDE_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico",
    "tiff", "tif", "heic", "heif", "avif"
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_slide_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_SLIDE_EXTENSIONS


ALLOWED_AGENCY_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf", "bmp"}


def allowed_agency_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_AGENCY_EXTENSIONS


def save_agency_file(file_storage, prefix="doc"):
    """Save uploaded file under uploads/agency/ and return relative path."""
    if not file_storage or not file_storage.filename:
        return ""
    if not allowed_agency_file(file_storage.filename):
        return ""
    folder = os.path.join(app.config["UPLOAD_FOLDER"], "agency")
    os.makedirs(folder, exist_ok=True)
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    filename = secure_filename(f"{prefix}_{secrets.token_hex(6)}.{ext}")
    file_storage.save(os.path.join(folder, filename))
    return f"agency/{filename}"


def get_clickpesa_token():
    url = "https://api.clickpesa.com/third-parties/generate-token"
    headers = {
        "client-id": CLICKPESA_CLIENT_ID,
        "api-key": CLICKPESA_API_KEY
    }
    response = requests.post(url, headers=headers, timeout=15)
    data = response.json()
    if response.status_code == 200 and "token" in data:
        return data["token"]
    else:
        raise Exception(f"Imeshindwa kupata token: {data}")


def normalize_phone(phone):
    phone = phone.strip()
    if phone.startswith("0"):
        return "255" + phone[1:]
    elif phone.startswith("+255"):
        return phone[1:]
    elif not phone.startswith("255"):
        return "255" + phone
    return phone


def send_otp_sms(phone, otp):
    """
    Tuma OTP kwa SMS ya kawaida kupitia Beem Africa API.
    Inahitaji env: SMS_API_KEY, SMS_API_SECRET, (hiari) SMS_SENDER_ID
    """
    if not SMS_API_KEY or not SMS_API_SECRET:
        return False, "SMS haijawezeshwa (SMS_API_KEY/SECRET hazipo)."

    dest = normalize_phone(phone)
    # Beem inatarajia 255...
    message = f"OTP yako ni {otp}. Inaisha baada ya dakika 15. Usitoe kwa mtu yeyote. - MR SULE"

    try:
        import base64
        url = "https://apisms.beem.africa/v1/send"
        credentials = f"{SMS_API_KEY}:{SMS_API_SECRET}"
        token = base64.b64encode(credentials.encode()).decode()
        headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
        payload = {
            # source_addr: jina au namba. Bila Sender Name approved, mtandao mara nyingi unaonesha namba ya kawaida
            "source_addr": (SMS_SENDER_ID or "INFO")[:11],
            "encoding": 0,
            "message": message,
            "recipients": [{"recipient_id": 1, "dest_addr": dest}],
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        data = resp.json() if resp.content else {}
        if resp.status_code in (200, 201):
            return True, "SMS imetumwa"
        return False, data.get("message") or str(data)[:120] or f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)[:150]


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if get_current_user():
        return redirect(url_for("home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip() or None
        password = request.form.get("password", "")
        if not username or not phone or not password:
            flash("Jaza taarifa zote muhimu.", "error")
            return redirect(url_for("register"))
        if User.query.filter((User.username == username) | (User.phone == phone)).first():
            flash("Username au namba ya simu tayari inatumika.", "error")
            return redirect(url_for("register"))
        if email and User.query.filter_by(email=email).first():
            flash("Email tayari inatumika.", "error")
            return redirect(url_for("register"))
        user = User(username=username, phone=phone, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        session.permanent = True
        session["user_id"] = user.id
        flash("Akaunti imeundwa. Karibu!", "success")
        return redirect(url_for("home"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user():
        return redirect(url_for("home"))
    if request.method == "POST":
        identity = request.form.get("identity", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter(
            (User.username == identity)
            | (User.phone == identity)
            | (User.email == identity)
        ).first()
        if user and user.check_password(password):
            session.permanent = True
            session["user_id"] = user.id
            flash("Umefanikiwa kuingia.", "success")
            next_url = session.pop("next_url", None)
            if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("home"))
        flash("Username/namba/email au password si sahihi.", "error")
        return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Umetoka kwenye akaunti.", "success")
    return redirect(url_for("home"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Bure + otomatiki: namba/username + password mpya. Hakuna SMS."""
    if get_current_user():
        return redirect(url_for("home"))

    if request.method == "POST":
        identity = request.form.get("identity", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        if not identity or not password:
            flash("Jaza namba/username na password mpya.", "error")
            return redirect(url_for("forgot_password"))
        if password != password2:
            flash("Password hazifanani.", "error")
            return redirect(url_for("forgot_password"))
        if len(password) < 4:
            flash("Password iwe angalau herufi 4.", "error")
            return redirect(url_for("forgot_password"))

        user = User.query.filter(
            (User.username == identity)
            | (User.phone == identity)
            | (User.email == identity)
        ).first()

        if not user:
            flash("Akaunti haijapatikana. Hakikisha namba/username ni sahihi.", "error")
            return redirect(url_for("forgot_password"))

        user.set_password(password)
        user.reset_otp = ""
        user.reset_otp_expires = None
        db.session.commit()
        flash("Password imebadilishwa! Ingia sasa.", "success")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


# ---------------------------------------------------------------------------
# Main pages
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    bundles = Bundle.query.filter_by(is_active=True).all()
    offers = Offer.query.filter_by(is_active=True).all()
    slides = (
        Slide.query.filter_by(is_active=True)
        .order_by(Slide.sort_order.asc(), Slide.id.asc())
        .all()
    )
    return render_template(
        "index.html",
        bundles=bundles,
        offers=offers,
        slides=slides
    )


@app.route("/profile")
@login_required
def profile():
    user = get_current_user()
    orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
    return render_template("profile.html", orders=orders)


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    user = get_current_user()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        phone = request.form.get("phone", "").strip()
        if not username or not phone:
            flash("Jaza taarifa zote.", "error")
            return redirect(url_for("settings"))
        existing = User.query.filter(
            ((User.username == username) | (User.phone == phone)) & (User.id != user.id)
        ).first()
        if existing:
            flash("Username au namba tayari inatumika.", "error")
            return redirect(url_for("settings"))
        user.username = username
        user.phone = phone
        db.session.commit()
        flash("Taarifa zimehifadhiwa.", "success")
        return redirect(url_for("settings"))
    return render_template("settings.html")


# ---------------------------------------------------------------------------
# Bundle / Order + ONLINE PAYMENT (ClickPesa)
# ---------------------------------------------------------------------------
@app.route("/order", methods=["GET", "POST"])
@login_required
def order():
    user = get_current_user()
    bundles = Bundle.query.filter_by(is_active=True).all()
    selected_bundle_id = request.args.get("bundle_id")
    selected_bundle = Bundle.query.get(selected_bundle_id) if selected_bundle_id else None
    offer_title = request.args.get("offer_title")
    offer_price = request.args.get("offer_price")
    offer_amount = request.args.get("offer_amount")

    if request.method == "POST":
        payment_phone = request.form.get("payment_phone", "").strip()
        target_phone = request.form.get("target_phone", "").strip()
        amount = request.form.get("amount", "").strip()
        price_raw = request.form.get("price", "0").strip()
        note = request.form.get("note", "").strip()

        try:
            price = int(price_raw) if price_raw else 0
        except ValueError:
            price = 0

        if not payment_phone or not target_phone or not amount:
            flash("Weka namba ya kulipia, namba ya kuwekewa bando na kiasi cha bundle.", "error")
            return redirect(url_for("order"))

        # Validate Halotel (061 / 062 / 063)
        clean_target = target_phone.replace(" ", "").replace("+255", "0")
        if clean_target.startswith("255"):
            clean_target = "0" + clean_target[3:]
        if not clean_target.startswith(("061", "062", "063")):
            flash("Namba ya kuweka bando lazima iwe ya Halotel (061, 062 au 063).", "error")
            return redirect(url_for("order"))

        order_note = f"Namba ya Kuwekewa: {target_phone}" + (f" | Maelezo: {note}" if note else "")

        new_order = Order(
            user_id=user.id,
            phone=target_phone,
            amount=amount,
            price=price,
            note=order_note,
            status="awaiting_payment",
        )
        db.session.add(new_order)
        db.session.flush()

        # Short unique reference (max 20 chars)
        order_ref = f"ORD{new_order.id}{secrets.token_hex(3)}"[:20]
        new_order.order_reference = order_ref
        db.session.commit()

        if CLICKPESA_CLIENT_ID and CLICKPESA_API_KEY and price > 0:
            try:
                token = get_clickpesa_token()
                url = "https://api.clickpesa.com/third-parties/payments/initiate-ussd-push-request"
                headers = {
                    "Authorization": token,
                    "Content-Type": "application/json"
                }
                payload = {
                    "amount": str(price),
                    "currency": "TZS",
                    "orderReference": order_ref,
                    "phoneNumber": normalize_phone(payment_phone)
                }
                response = requests.post(url, json=payload, headers=headers, timeout=20)
                data = response.json()

                if response.status_code in [200, 201]:
                    flash("Ombi la malipo limeshushwa! Angalia simu yako na uingize PIN.", "success")
                    return redirect(url_for("asante"))
                else:
                    reason = data.get("message") or str(data)[:120]
                    new_order.status = "failed"
                    new_order.fail_reason = reason
                    db.session.commit()
                    flash(f"Malipo yameshindwa kuanzishwa: {reason}", "error")
            except Exception as e:
                new_order.status = "failed"
                new_order.fail_reason = str(e)[:120]
                db.session.commit()
                flash(f"Hitilafu ya malipo: {str(e)}", "error")
        else:
            new_order.status = "pending"
            db.session.commit()
            flash(
                f"Request imetumwa! Lipa kwa {PAYMENT_NETWORK} {PAYMENT_NUMBER} ({PAYMENT_NAME}). "
                "Baada ya malipo, wasiliana na chat.",
                "success",
            )
        return redirect(url_for("profile"))

    return render_template(
        "order.html",
        bundles=bundles,
        selected_bundle=selected_bundle,
        offer_title=offer_title,
        offer_price=offer_price,
        offer_amount=offer_amount
    )


@app.route("/admin/order/status/<int:order_id>", methods=["POST"])
@admin_required
def update_order_status(order_id):
    order_item = Order.query.get_or_404(order_id)
    status = request.form.get("status", "pending")
    if status in ("pending", "completed", "rejected", "failed", "awaiting_payment"):
        order_item.status = status
        db.session.commit()
        flash(f"Status ya Oda #{order_item.id} imebadilishwa kuwa '{status}'.", "success")
    else:
        flash("Status si sahihi.", "error")
    return redirect(url_for("dashboard"))


@app.route("/asante")
def asante():
    return """
    <!DOCTYPE html>
    <html lang="sw">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Asante | Malipo</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-50 min-h-screen flex items-center justify-center p-4">
        <div class="bg-white rounded-3xl shadow-xl p-8 max-w-md w-full text-center">
            <div class="inline-flex items-center justify-center w-16 h-16 bg-green-100 rounded-full mb-4">
                <span class="text-3xl text-green-600">✓</span>
            </div>
            <h1 class="text-2xl font-bold text-slate-800 mb-2">Ombi la Pesa Limeshushwa!</h1>
            <p class="text-slate-500 text-sm mb-6">
                Angalia simu yako sasa. Ujumbe wa PIN utajitokeza.<br>
                Ingiza namba yako ya siri kukamilisha malipo.
            </p>
            <a href="/" class="inline-block bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold py-3 px-6 rounded-xl">
                Rudi Nyumbani
            </a>
        </div>
    </body>
    </html>
    """


# ---------------------------------------------------------------------------
# ClickPesa Webhook
# ---------------------------------------------------------------------------
@app.route("/webhook/clickpesa", methods=["POST"])
def clickpesa_webhook():
    """
    Configure kwenye ClickPesa Dashboard:
    https://your-domain.com/webhook/clickpesa
    Events: PAYMENT RECEIVED + PAYMENT FAILED
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}

    event = (data.get("event") or "").upper()
    payload = data.get("data") or data

    order_ref = payload.get("orderReference") or payload.get("order_reference")
    status = (payload.get("status") or "").upper()
    message = payload.get("message") or ""

    if not order_ref:
        return jsonify({"ok": False, "error": "No orderReference"}), 400

    order = Order.query.filter_by(order_reference=order_ref).first()
    if not order:
        return jsonify({"ok": False, "error": "Order not found"}), 200

    if event == "PAYMENT RECEIVED" or status in ("SUCCESS", "SETTLED"):
        if order.status in ("awaiting_payment", "failed"):
            order.status = "pending"
            order.fail_reason = ""
            db.session.commit()

    elif event == "PAYMENT FAILED" or status == "FAILED":
        order.status = "failed"
        order.fail_reason = (message or "Malipo yameshindwa")[:200]
        db.session.commit()

    return jsonify({"ok": True}), 200


# ---------------------------------------------------------------------------
# Chat, Agency, Dashboard
# ---------------------------------------------------------------------------
@app.route("/chat")
@login_required
def chat():
    user = get_current_user()
    messages = Message.query.filter_by(user_id=user.id).order_by(Message.created_at.asc()).all()
    return render_template("chat.html", messages=messages)


@app.route("/send_message", methods=["POST"])
@login_required
def send_message():
    user = get_current_user()
    text = request.form.get("message", "").strip()
    image_file = request.files.get("image")
    image_name = None
    if image_file and image_file.filename and allowed_file(image_file.filename):
        filename = secure_filename(f"{user.id}_{secrets.token_hex(4)}_{image_file.filename}")
        image_file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        image_name = filename
    if not text and not image_name:
        return jsonify({"success": False, "error": "Ujumbe tupu"})
    msg = Message(
        user_id=user.id,
        sender="customer",
        message=text,
        image=image_name,
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/admin/chat/<int:user_id>")
@admin_required
def admin_chat(user_id):
    user = User.query.get_or_404(user_id)
    messages = Message.query.filter_by(user_id=user.id).order_by(Message.created_at.asc()).all()
    return render_template("admin_chat.html", user=user, messages=messages)


@app.route("/admin/send_message/<int:user_id>", methods=["POST"])
@admin_required
def admin_send_message(user_id):
    user = User.query.get_or_404(user_id)
    text = request.form.get("message", "").strip()
    image_file = request.files.get("image")
    image_name = None
    if image_file and image_file.filename and allowed_file(image_file.filename):
        filename = secure_filename(f"admin_{user.id}_{secrets.token_hex(4)}_{image_file.filename}")
        image_file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        image_name = filename
    if not text and not image_name:
        return jsonify({"success": False})
    msg = Message(
        user_id=user.id,
        sender="admin",
        message=text,
        image=image_name,
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"success": True})



def get_agency_form_fields(network, service_type):
    """Return form config for network + service_type (lipa|uwakala)."""
    network = network.strip()
    service_type = service_type.strip().lower()

    if network == "Vodacom" and service_type == "lipa":
        return {
            "form_title": "Usajili wa Lipa Namba (M-Pesa Merchant)",
            "service_label": "Lipa Namba / Merchant",
            "fields": [
                {"name": "full_name", "label": "1. Jina Kamili (Full Name)", "type": "text", "required": True, "placeholder": "Mfano: Juma Ally Hassan"},
                {"name": "tin_number", "label": "2. TIN Number", "type": "text", "required": True, "placeholder": "Namba ya TIN"},
                {"name": "target_phone", "label": "3. Namba ya Simu (haijawezeshwa M-Pesa)", "type": "tel", "required": True, "placeholder": "07XXXXXXXX", "help": "Namba unayotaka kuisajili kama Lipa Namba"},
                {"name": "tin_cert", "label": "4. TIN Certificate (PDF au Picha)", "type": "file", "required": True, "accept": "image/*,.pdf", "hint": "Pakia PDF au picha. Unaweza kupiga picha moja kwa moja."},
                {"name": "id_type", "label": "5. Aina ya Kitambulisho", "type": "select", "required": True, "options": [
                    "Kitambulisho cha Mpiga Kura",
                    "Kitambulisho cha Taifa (NIDA/NIN)",
                    "Passport",
                ]},
                {"name": "id_doc", "label": "6. Pakia Kitambulisho (PDF au Picha)", "type": "file", "required": True, "accept": "image/*,.pdf"},
            ],
        }

    if network == "Vodacom" and service_type == "uwakala":
        return {"contact": True, "title": "Huduma za Uwakala / Usajili wa Laini", "message": "Wasiliana na admin atakupa maelekezo kamili ya kupata huduma hii.", "service_label": "Uwakala"}

    if network == "Airtel" and service_type == "lipa":
        return {
            "form_title": "Usajili wa Lipa Namba (Airtel Money)",
            "service_label": "Lipa Namba / Merchant",
            "fields": [
                {"name": "target_phone", "label": "Namba ya Airtel unayotaka iwe Lipa Namba", "type": "tel", "required": True, "placeholder": "07XXXXXXXX"},
                {"name": "full_name", "label": "Jina Kamili", "type": "text", "required": True, "placeholder": "Majina matatu"},
                {"name": "contact_phone", "label": "Namba ya Mawasiliano", "type": "tel", "required": True, "placeholder": "07XXXXXXXX"},
            ],
        }

    if network == "Airtel" and service_type == "uwakala":
        return {
            "form_title": "Ombi la Uwakala (Airtel)",
            "service_label": "Uwakala",
            "fields": [
                {"name": "target_phone", "label": "1. Namba ya Airtel unayotaka iwe ya Uwakala", "type": "tel", "required": True, "placeholder": "07XXXXXXXX"},
                {"name": "full_name", "label": "2. Jina Kamili", "type": "text", "required": True, "placeholder": "Majina matatu"},
                {"name": "tin_cert", "label": "3. TIN Certificate (PDF au Picha)", "type": "file", "required": True, "accept": "image/*,.pdf"},
                {"name": "shop_photo", "label": "4. Picha ya Duka / Kioski", "type": "file", "required": True, "accept": "image/*", "hint": "Piga picha au chagua picha ya duka lako"},
            ],
        }

    if network == "Yas" and service_type == "lipa":
        return {
            "form_title": "Usajili wa Lipa Namba (Mixx by Yas)",
            "service_label": "Lipa Namba",
            "fields": [
                {"name": "target_phone", "label": "1. Namba ya Mixx by Yas unayotaka iwe Lipa Namba", "type": "tel", "required": True, "placeholder": "07XXXXXXXX"},
                {"name": "phone", "label": "2. Namba ya Pili (simu nyingine)", "type": "tel", "required": False, "placeholder": "07XXXXXXXX (si lazima)"},
                {"name": "contact_phone", "label": "3. Namba ya Tatu / Mawasiliano", "type": "tel", "required": True, "placeholder": "07XXXXXXXX"},
                {"name": "tin_cert", "label": "4. Certificate (PDF au Picha)", "type": "file", "required": True, "accept": "image/*,.pdf"},
                {"name": "full_name", "label": "5. Majina Matatu (Full Name)", "type": "text", "required": True, "placeholder": "Jina la kwanza, kati, mwisho"},
            ],
        }

    if network == "Yas" and service_type == "uwakala":
        return {"contact": True, "title": "Huduma ya Uwakala (Yas)", "message": "Huduma hii haipatikani kwa sasa. Tafadhali wasiliana na admini kupitia namba ya WhatsApp au simu ya kawaida.", "service_label": "Uwakala"}

    if network == "Halotel" and service_type == "lipa":
        return {
            "form_title": "Usajili wa Lipa Namba (HaloPesa)",
            "service_label": "Lipa Namba",
            "fields": [
                {"name": "target_phone", "label": "1. Namba ya Halotel (haijawezeshwa HaloPesa)", "type": "tel", "required": True, "placeholder": "06XXXXXXXX", "help": "Namba unayotaka iwe Lipa Namba"},
                {"name": "tin_number", "label": "2. TIN Number", "type": "text", "required": True, "placeholder": "Namba ya TIN"},
                {"name": "tin_cert", "label": "3. TIN Certificate (PDF au Picha)", "type": "file", "required": True, "accept": "image/*,.pdf"},
                {"name": "id_type", "label": "4. Aina ya Kitambulisho", "type": "select", "required": True, "options": [
                    "NIDA / NIN",
                    "Kitambulisho cha Mpiga Kura",
                    "Leseni ya Udereva",
                    "Passport",
                ]},
                {"name": "id_doc", "label": "5. Pakia Kitambulisho (PDF au Picha)", "type": "file", "required": True, "accept": "image/*,.pdf"},
                {"name": "full_name", "label": "6. Majina Matatu (Full Name)", "type": "text", "required": True, "placeholder": "Majina matatu"},
                {"name": "contact_phone", "label": "7. Namba ya Mawasiliano", "type": "tel", "required": True, "placeholder": "07XXXXXXXX"},
            ],
        }

    if network == "Halotel" and service_type == "uwakala":
        return {
            "form_title": "Ombi la Uwakala (Halotel)",
            "service_label": "Uwakala",
            "fields": [
                {"name": "target_phone", "label": "1. Namba ya Halotel (yenye salio ≥ 100,000)", "type": "tel", "required": True, "placeholder": "06XXXXXXXX", "help": "Lazima iwe na salio la angalau TSh 100,000"},
                {"name": "tin_number", "label": "2. TIN Number", "type": "text", "required": True, "placeholder": "Namba ya TIN"},
                {"name": "full_name", "label": "3. Jina Kamili", "type": "text", "required": True, "placeholder": "Majina matatu"},
                {"name": "tin_cert", "label": "4. TIN Certificate (PDF au Picha)", "type": "file", "required": True, "accept": "image/*,.pdf"},
                {"name": "id_type", "label": "5. Aina ya Kitambulisho", "type": "select", "required": True, "options": [
                    "NIDA / NIN",
                    "Kitambulisho cha Mpiga Kura",
                    "Leseni ya Udereva",
                    "Passport",
                ]},
                {"name": "id_doc", "label": "6. Pakia Kitambulisho (PDF au Picha)", "type": "file", "required": True, "accept": "image/*,.pdf"},
            ],
        }

    return {
        "form_title": f"{network} – {service_type}",
        "service_label": service_type.title(),
        "fields": [
            {"name": "full_name", "label": "Jina Kamili", "type": "text", "required": True},
            {"name": "phone", "label": "Namba ya Simu", "type": "tel", "required": True},
        ],
    }


@app.route("/agency")
@login_required
def agency():
    return render_template("agency.html")


@app.route("/agency/<network>")
@login_required
def agency_network(network):
    coming_soon = network in ("TTCL", "Azam Pesa")
    return render_template(
        "agency_network.html",
        network=network,
        coming_soon=coming_soon,
    )


@app.route("/agency/<network>/<service_type>", methods=["GET", "POST"])
@login_required
def agency_service(network, service_type):
    service_type = service_type.lower().strip()
    if service_type not in ("lipa", "uwakala"):
        flash("Huduma si sahihi.", "error")
        return redirect(url_for("agency_network", network=network))

    if network in ("TTCL", "Azam Pesa"):
        return redirect(url_for("agency_network", network=network))

    config = get_agency_form_fields(network, service_type)

    if config.get("contact"):
        return render_template(
            "agency_contact.html",
            network=network,
            service_label=config.get("service_label", service_type),
            title=config.get("title", "Wasiliana na Admin"),
            message=config.get("message", ""),
        )

    if request.method == "POST":
        data = {}
        for f in config["fields"]:
            if f["type"] in ("text", "tel", "select"):
                data[f["name"]] = request.form.get(f["name"], "").strip()

        for f in config["fields"]:
            if f.get("required") and f["type"] in ("text", "tel", "select"):
                if not data.get(f["name"]):
                    flash(f"Tafadhali jaza: {f['label']}", "error")
                    return redirect(url_for("agency_service", network=network, service_type=service_type))

        file_fields = [f for f in config["fields"] if f["type"] == "file"]
        file_paths = {}
        for f in file_fields:
            fs = request.files.get(f["name"])
            if f.get("required") and (not fs or not fs.filename):
                flash(f"Tafadhali pakia: {f['label']}", "error")
                return redirect(url_for("agency_service", network=network, service_type=service_type))
            if fs and fs.filename:
                if not allowed_agency_file(fs.filename):
                    flash("Aina ya faili hairuhusiwi. Ruhusu: PDF, JPG, PNG, WEBP.", "error")
                    return redirect(url_for("agency_service", network=network, service_type=service_type))
                path = save_agency_file(fs, prefix=f["name"])
                file_paths[f["name"]] = path

        draft = {
            "network": network,
            "service_type": service_type,
            "service_label": config.get("service_label", service_type),
            "form_title": config.get("form_title", ""),
            "data": data,
            "files": file_paths,
            "fields": config["fields"],
        }
        session["agency_draft"] = draft
        return redirect(url_for("agency_review"))

    return render_template(
        "agency_form.html",
        network=network,
        service_type=service_type,
        service_label=config.get("service_label", service_type),
        form_title=config.get("form_title", "Jaza Taarifa"),
        fields=config["fields"],
    )


@app.route("/agency/review")
@login_required
def agency_review():
    draft = session.get("agency_draft")
    if not draft:
        flash("Hakuna taarifa za kukaguliwa. Anza upya.", "error")
        return redirect(url_for("agency"))

    review_items = []
    for f in draft.get("fields", []):
        name = f["name"]
        label = f["label"]
        if f["type"] == "file":
            path = draft.get("files", {}).get(name, "")
            review_items.append({
                "label": label,
                "value": os.path.basename(path) if path else "—",
                "is_file": True,
            })
        else:
            review_items.append({
                "label": label,
                "value": draft.get("data", {}).get(name, ""),
                "is_file": False,
            })

    return render_template(
        "agency_review.html",
        network=draft["network"],
        service_type=draft["service_type"],
        service_label=draft.get("service_label", ""),
        review_items=review_items,
    )


@app.route("/agency/submit", methods=["POST"])
@login_required
def agency_submit():
    user = get_current_user()
    draft = session.get("agency_draft")
    if not draft:
        flash("Hakuna taarifa. Anza upya.", "error")
        return redirect(url_for("agency"))

    data = draft.get("data", {})
    files = draft.get("files", {})
    network = draft["network"]
    service_type = draft["service_type"]
    service_label = draft.get("service_label", service_type)

    code = generate_application_code()
    while Application.query.filter_by(application_code=code).first():
        code = generate_application_code()

    full_name = data.get("full_name") or data.get("target_phone") or "—"
    phone = data.get("contact_phone") or data.get("phone") or data.get("target_phone") or user.phone

    app_obj = Application(
        application_code=code,
        user_id=user.id,
        network=network,
        service_type=f"{service_label} ({network})",
        full_name=full_name,
        phone=phone,
        id_type=data.get("id_type", ""),
        id_number=data.get("id_number", ""),
        tin_number=data.get("tin_number", ""),
        target_phone=data.get("target_phone", ""),
        contact_phone=data.get("contact_phone", ""),
        tin_cert=files.get("tin_cert", ""),
        id_doc=files.get("id_doc", ""),
        shop_photo=files.get("shop_photo", ""),
        description=data.get("description", ""),
        status="pending",
    )
    db.session.add(app_obj)
    db.session.flush()

    sys_msg = AgencyMessage(
        application_id=app_obj.id,
        sender="system",
        message=f"Ombi {code} limepokelewa ({network} – {service_label}). Status: Inasubiri. Timu yetu itakujibu hivi karibuni.",
    )
    db.session.add(sys_msg)
    db.session.commit()

    session.pop("agency_draft", None)
    flash(f"Ombi limetumwa! Code: {code}. Unaweza kufuatilia kwenye Maombi Yangu.", "success")
    return redirect(url_for("my_applications"))


@app.route("/my-applications")
@login_required
def my_applications():
    user = get_current_user()
    applications = (
        Application.query.filter_by(user_id=user.id)
        .order_by(Application.created_at.desc())
        .all()
    )
    return render_template("my_applications.html", applications=applications)


@app.route("/application/<int:application_id>", methods=["GET", "POST"])
@login_required
def application_detail(application_id):
    user = get_current_user()
    application = Application.query.get_or_404(application_id)
    if application.user_id != user.id and not user.is_admin:
        flash("Huna ruhusa.", "error")
        return redirect(url_for("my_applications"))
    if request.method == "POST":
        if application.status in ("completed", "rejected"):
            flash("Ombi hili limefungwa.", "error")
            return redirect(url_for("application_detail", application_id=application.id))
        text = request.form.get("message", "").strip()
        if text:
            msg = AgencyMessage(
                application_id=application.id,
                sender="customer",
                message=text,
            )
            db.session.add(msg)
            db.session.commit()
            flash("Ujumbe umetumwa.", "success")
        return redirect(url_for("application_detail", application_id=application.id))
    messages = (
        AgencyMessage.query.filter_by(application_id=application.id)
        .order_by(AgencyMessage.created_at.asc())
        .all()
    )
    return render_template(
        "application_detail.html",
        application=application,
        messages=messages,
    )


# ---------------------------------------------------------------------------
# Nakala (TIN, Leseni, NIDA Copy)
# ---------------------------------------------------------------------------
def generate_nakala_code():
    return "NK-" + secrets.token_hex(4).upper()


@app.route("/nakala")
@login_required
def nakala():
    return render_template("nakala.html")


@app.route("/nakala/tin", methods=["GET", "POST"])
@login_required
def nakala_tin():
    if request.method == "POST":
        had_tin = request.form.get("had_tin_before", "").strip()
        if had_tin == "ndiyo":
            flash("Kwa sasa huduma hii inapatikana kwa wale ambao hawajawahi kuwa na TIN. Wasiliana na admin.", "error")
            return redirect(url_for("nakala_tin"))

        full_name = request.form.get("full_name", "").strip()
        mother_name = request.form.get("mother_name", "").strip()
        nida_number = request.form.get("nida_number", "").strip()
        phone1 = request.form.get("phone1", "").strip()
        phone2 = request.form.get("phone2", "").strip()
        primary_school = request.form.get("primary_school", "").strip()
        year_completed = request.form.get("year_completed", "").strip()
        school_district = request.form.get("school_district", "").strip()
        school_region = request.form.get("school_region", "").strip()
        nida_reg_district = request.form.get("nida_reg_district", "").strip()
        nida_reg_region = request.form.get("nida_reg_region", "").strip()
        nida_reg_street = request.form.get("nida_reg_street", "").strip()

        if not all([full_name, mother_name, nida_number, phone1, primary_school,
                    year_completed, school_district, school_region,
                    nida_reg_district, nida_reg_region, nida_reg_street]):
            flash("Jaza taarifa zote muhimu.", "error")
            return redirect(url_for("nakala_tin"))

        if len(nida_number) > 20 or not nida_number.isdigit():
            flash("Namba ya NIDA/NIN lazima iwe namba tu (max 20).", "error")
            return redirect(url_for("nakala_tin"))

        session["nakala_draft"] = {
            "service_type": "tin",
            "had_tin_before": had_tin,
            "full_name": full_name,
            "mother_name": mother_name,
            "nida_number": nida_number,
            "phone1": phone1,
            "phone2": phone2,
            "primary_school": primary_school,
            "year_completed": year_completed,
            "school_district": school_district,
            "school_region": school_region,
            "nida_reg_district": nida_reg_district,
            "nida_reg_region": nida_reg_region,
            "nida_reg_street": nida_reg_street,
            "price": 3000,
        }
        return redirect(url_for("nakala_review"))

    return render_template("nakala_tin.html")



# Bei za Leseni za Biashara (Halmashauri) — katalogi kamili kategoria 1–24
LICENSE_CATEGORIES = [
    "Maduka",
    "Hardware",
    "Mobile Money",
    "Hotel",
    "Salon",
    "Food",
    "Mitumba na Vitabu",
    "Magari",
    "Mifugo",
    "Afya",
    "Ujenzi",
    "Agency",
    "Fedha",
    "Usafirishaji",
    "Bima na Real Estate",
    "Telecom",
    "Media",
    "Viwanda",
    "Utalii",
    "Import/Export",
    "Dealership",
    "Postal",
    "Professional",
    "Burudani",
]

LICENSE_CATALOG = [
    # 1. Maduka
    {"id": "retail_city", "name": "Retail Shop (City/Municipal)", "price": 70000, "category": "Maduka"},
    {"id": "retail_district", "name": "Retail Shop (District)", "price": 50000, "category": "Maduka"},
    {"id": "retail_minor", "name": "Retail Shop (Minor Settlement)", "price": 20000, "category": "Maduka"},
    {"id": "retail_village", "name": "Retail Shop (Village)", "price": 8000, "category": "Maduka"},
    {"id": "supermarket_city", "name": "Supermarket (City/Municipal)", "price": 500000, "category": "Maduka"},
    {"id": "supermarket_district", "name": "Supermarket (District)", "price": 200000, "category": "Maduka"},
    {"id": "supermarket_minor", "name": "Supermarket (Minor Settlement)", "price": 100000, "category": "Maduka"},
    {"id": "supermarket_village", "name": "Supermarket (Village)", "price": 5000, "category": "Maduka"},
    {"id": "departmental_city", "name": "Departmental Store (City/Municipal)", "price": 400000, "category": "Maduka"},
    {"id": "wholesale", "name": "General Wholesale", "price": 300000, "category": "Maduka"},
    {"id": "sub_wholesale", "name": "General Sub-wholesale", "price": 200000, "category": "Maduka"},
    {"id": "kiosk_city", "name": "Kiosk/Grocery (City/Municipal)", "price": 60000, "category": "Maduka"},
    {"id": "kiosk_district", "name": "Kiosk/Grocery (District)", "price": 40000, "category": "Maduka"},
    {"id": "kiosk_village", "name": "Kiosk/Grocery (Minor Settlement/Village)", "price": 10000, "category": "Maduka"},
    # 2. Hardware
    {"id": "hardware_city", "name": "Hardware & Building Materials Retail (City/Municipal)", "price": 200000, "category": "Hardware"},
    {"id": "hardware_district", "name": "Hardware & Building Materials Retail (District)", "price": 150000, "category": "Hardware"},
    {"id": "hardware_village", "name": "Hardware & Building Materials Retail (Minor Settlement/Village)", "price": 60000, "category": "Hardware"},
    {"id": "appliances_city", "name": "Domestic Appliances Retail (City/Municipal)", "price": 200000, "category": "Hardware"},
    {"id": "appliances_district", "name": "Domestic Appliances Retail (District)", "price": 100000, "category": "Hardware"},
    {"id": "appliances_village", "name": "Domestic Appliances Retail (Minor Settlement/Village)", "price": 50000, "category": "Hardware"},
    {"id": "electrical_city", "name": "Electrical & Household Items Retail (City/Municipal)", "price": 150000, "category": "Hardware"},
    {"id": "electrical_district", "name": "Electrical & Household Items Retail (District)", "price": 100000, "category": "Hardware"},
    {"id": "electrical_minor", "name": "Electrical & Household Items Retail (Minor Settlement)", "price": 50000, "category": "Hardware"},
    {"id": "electrical_village", "name": "Electrical & Household Items Retail (Village)", "price": 10000, "category": "Hardware"},
    {"id": "machinery_city", "name": "Machinery Tools (City/Municipal)", "price": 300000, "category": "Hardware"},
    {"id": "machinery_district", "name": "Machinery Tools (District)", "price": 200000, "category": "Hardware"},
    {"id": "machinery_village", "name": "Machinery Tools (Minor Settlement/Village)", "price": 80000, "category": "Hardware"},
    {"id": "timber_city", "name": "Timber & Furniture Retail (City/Municipal)", "price": 200000, "category": "Hardware"},
    {"id": "timber_district", "name": "Timber & Furniture Retail (District/Town)", "price": 100000, "category": "Hardware"},
    # 3. Mobile money
    {"id": "emt_uwakala", "name": "Electronic Money Transfer (Uwakala)", "price": 80000, "category": "Mobile Money", "note": "Bei inaweza kutegemea Halmashauri"},
    {"id": "isp_agent", "name": "Internet Services Provider Agent", "price": 400000, "category": "Mobile Money"},
    {"id": "internet_cafe", "name": "Internet Surfing/Café", "price": 200000, "category": "Mobile Money"},
    {"id": "telecom_services", "name": "Telecommunication Services", "price": 300000, "category": "Mobile Money"},
    {"id": "telecom_accessories", "name": "Selling Telecommunication Accessories", "price": 300000, "category": "Mobile Money"},
    {"id": "payphone", "name": "Payphone Operator", "price": 400000, "category": "Mobile Money"},
    # 4. Hotel
    {"id": "hotel_liquor", "name": "Non-Tourist Business Hotel (With Liquor)", "price": 100000, "category": "Hotel", "note": "+ TSh 1,500 kwa kila bedroom"},
    {"id": "hotel_no_liquor", "name": "Non-Tourist Business Hotel (Without Liquor)", "price": 80000, "category": "Hotel", "note": "+ TSh 2,000 kwa kila bedroom"},
    {"id": "lodging", "name": "Lodging House", "price": 100000, "category": "Hotel", "note": "+ TSh 2,000 kwa kila bedroom"},
    {"id": "takeaway", "name": "Take-away", "price": 100000, "category": "Hotel"},
    {"id": "mobile_catering", "name": "Mobile Catering", "price": 100000, "category": "Hotel"},
    {"id": "tourist_hotel", "name": "Tourist Hotel", "price": 150000, "category": "Hotel", "note": "+ TSh 2,000 kwa kila bedroom"},
    {"id": "tourist_lodge", "name": "Tourist Lodge", "price": 150000, "category": "Hotel"},
    {"id": "tourist_camp", "name": "Tourist Camp", "price": 100000, "category": "Hotel", "note": "+ TSh 3,000 kwa kila hut/cottage"},
    {"id": "catering", "name": "Catering Services", "price": 200000, "category": "Hotel"},
    # 5. Salon
    {"id": "salon_city", "name": "Hair Salon/Barber Shop (City/Municipal)", "price": 40000, "category": "Salon"},
    {"id": "salon_district", "name": "Hair Salon/Barber Shop (District)", "price": 20000, "category": "Salon"},
    {"id": "salon_village", "name": "Hair Salon/Barber Shop (Minor Settlement/Village)", "price": 5000, "category": "Salon"},
    {"id": "beauty_city", "name": "Beauty Clinic Machinery/Tools (City/Municipal)", "price": 40000, "category": "Salon"},
    {"id": "beauty_district", "name": "Beauty Clinic Machinery/Tools (District)", "price": 30000, "category": "Salon"},
    {"id": "beauty_village", "name": "Beauty Clinic Machinery/Tools (Minor Settlement/Village)", "price": 10000, "category": "Salon"},
    # 6. Food
    {"id": "bakery_city", "name": "Bakery (City/Municipal)", "price": 100000, "category": "Food"},
    {"id": "bakery_district", "name": "Bakery (District)", "price": 80000, "category": "Food"},
    {"id": "bakery_village", "name": "Bakery (Minor Settlement/Village)", "price": 30000, "category": "Food"},
    {"id": "milling_city", "name": "Flour/Oil Milling (City/Municipal)", "price": 50000, "category": "Food"},
    {"id": "milling_district", "name": "Flour/Oil Milling (District/Town)", "price": 30000, "category": "Food"},
    {"id": "milling_village", "name": "Flour/Oil Milling (Minor Settlement/Village)", "price": 20000, "category": "Food"},
    {"id": "butchery_city", "name": "Butchery (City/Municipal)", "price": 80000, "category": "Food"},
    {"id": "butchery_district", "name": "Butchery (District/Town)", "price": 60000, "category": "Food"},
    {"id": "butchery_village", "name": "Butchery (Minor Settlement/Village)", "price": 10000, "category": "Food"},
    {"id": "tearoom_city", "name": "Tea Room (City/Municipal)", "price": 50000, "category": "Food"},
    {"id": "tearoom_district", "name": "Tea Room (District)", "price": 25000, "category": "Food"},
    {"id": "tearoom_village", "name": "Tea Room (Minor Settlement/Village)", "price": 5000, "category": "Food"},
    {"id": "fish_city", "name": "Selling Fish (City/Municipal)", "price": 40000, "category": "Food"},
    {"id": "fish_district", "name": "Selling Fish (District)", "price": 30000, "category": "Food"},
    {"id": "fish_village", "name": "Selling Fish (Minor Settlement/Village)", "price": 10000, "category": "Food"},
    # 7. Mitumba na Vitabu
    {"id": "mitumba", "name": "Second-hand Clothes & Shoes (Mitumba)", "price": 50000, "category": "Mitumba na Vitabu", "note": "Ada hutegemea category/halmashauri"},
    {"id": "bookstore_city", "name": "Bookstore & Stationery Retail (City/Municipal)", "price": 100000, "category": "Mitumba na Vitabu"},
    {"id": "bookstore_district", "name": "Bookstore & Stationery Retail (District/Town)", "price": 80000, "category": "Mitumba na Vitabu"},
    {"id": "bookstore_village", "name": "Bookstore & Stationery Retail (Minor Settlement/Village)", "price": 20000, "category": "Mitumba na Vitabu"},
    {"id": "printing_city", "name": "Printing & Publishing (City/Municipal)", "price": 400000, "category": "Mitumba na Vitabu"},
    {"id": "printing_district", "name": "Printing & Publishing (District)", "price": 250000, "category": "Mitumba na Vitabu"},
    {"id": "printing_village", "name": "Printing & Publishing (Minor Settlement/Village)", "price": 100000, "category": "Mitumba na Vitabu"},
    # 8. Magari
    {"id": "garage_city", "name": "Workshop & Garage (City/Municipal)", "price": 150000, "category": "Magari"},
    {"id": "garage_district", "name": "Workshop & Garage (District)", "price": 120000, "category": "Magari"},
    {"id": "garage_minor", "name": "Workshop & Garage (Minor Settlement)", "price": 100000, "category": "Magari"},
    {"id": "garage_village", "name": "Workshop & Garage (Village)", "price": 30000, "category": "Magari"},
    {"id": "oils_city", "name": "Motor Oils & Lubricants (City/Municipal)", "price": 120000, "category": "Magari"},
    {"id": "oils_district", "name": "Motor Oils & Lubricants (District)", "price": 100000, "category": "Magari"},
    {"id": "petrol_city", "name": "Petrol/Filling Station (City/Municipal)", "price": 200000, "category": "Magari"},
    {"id": "petrol_district", "name": "Petrol/Filling Station (District)", "price": 150000, "category": "Magari"},
    {"id": "petrol_village", "name": "Petrol/Filling Station (Minor Settlement/Village)", "price": 100000, "category": "Magari"},
    # 9. Mifugo
    {"id": "livestock_city", "name": "Livestock Trading (City/Municipal)", "price": 150000, "category": "Mifugo"},
    {"id": "livestock_district", "name": "Livestock Trading (District/Town)", "price": 80000, "category": "Mifugo"},
    {"id": "livestock_village", "name": "Livestock Trading (Minor Settlement/Village)", "price": 25000, "category": "Mifugo"},
    # 10. Afya
    {"id": "dispensary", "name": "Dispensary/Health Centre/Laboratory Clinic", "price": 80000, "category": "Afya"},
    {"id": "hospital", "name": "Hospital (Local)", "price": 150000, "category": "Afya"},
    {"id": "poison_1", "name": "Selling Medicines – Part I Poison Shop", "price": 200000, "category": "Afya"},
    {"id": "poison_2", "name": "Selling Medicines – Part II Poison Shop", "price": 100000, "category": "Afya"},
    # 11. Ujenzi
    {"id": "builder_1", "name": "Building Contractor Class I", "price": 1000000, "category": "Ujenzi"},
    {"id": "builder_2", "name": "Building Contractor Class II", "price": 800000, "category": "Ujenzi"},
    {"id": "builder_3", "name": "Building Contractor Class III", "price": 700000, "category": "Ujenzi"},
    {"id": "builder_4", "name": "Building Contractor Class IV", "price": 650000, "category": "Ujenzi"},
    {"id": "builder_5", "name": "Building Contractor Class V", "price": 500000, "category": "Ujenzi"},
    {"id": "builder_6", "name": "Building Contractor Class VI", "price": 400000, "category": "Ujenzi"},
    {"id": "builder_8", "name": "Building Contractor Class VIII", "price": 300000, "category": "Ujenzi"},
    {"id": "elec_a", "name": "Electrical Contractor Class A", "price": 500000, "category": "Ujenzi"},
    {"id": "elec_b", "name": "Electrical Contractor Class B", "price": 300000, "category": "Ujenzi"},
    {"id": "elec_c", "name": "Electrical Contractor Class C", "price": 200000, "category": "Ujenzi"},
    {"id": "elec_d", "name": "Electrical Contractor Class D", "price": 100000, "category": "Ujenzi"},
    # 12. Agency
    {"id": "commission_agent", "name": "Commission Agent", "price": 300000, "category": "Agency"},
    {"id": "travel_agent", "name": "Travel Agent", "price": 200000, "category": "Agency"},
    {"id": "air_charter", "name": "Air Charter Agent (Local)", "price": 300000, "category": "Agency"},
    {"id": "shipping_agent", "name": "Shipping Agent", "price": 1000000, "category": "Agency"},
    {"id": "other_agent", "name": "Any Other Agent (Local)", "price": 200000, "category": "Agency"},
    {"id": "insurance_broker", "name": "Insurance Broker (Local)", "price": 200000, "category": "Agency"},
    {"id": "stock_broker", "name": "Stock Exchange Broker (Local)", "price": 500000, "category": "Agency"},
    # 13. Fedha
    {"id": "banking", "name": "Banking Service (Local)", "price": 1000000, "category": "Fedha"},
    {"id": "bureau", "name": "Bureau de Change (Local)", "price": 600000, "category": "Fedha"},
    {"id": "coop_bank", "name": "Co-operative Bank", "price": 200000, "category": "Fedha"},
    {"id": "capital_markets", "name": "Capital Markets & Stock Exchange", "price": 500000, "category": "Fedha"},
    {"id": "social_security", "name": "Social Security Provider", "price": 1000000, "category": "Fedha"},
    {"id": "mortgage", "name": "Mortgage & Hire Purchase", "price": 600000, "category": "Fedha"},
    {"id": "mortgage_micro", "name": "Mortgage & Hire Purchase – Micro Enterprise", "price": 100000, "category": "Fedha"},
    {"id": "credit_card", "name": "Credit Card Management", "price": 400000, "category": "Fedha"},
    {"id": "microfinance", "name": "Micro Financing Investment (Local)", "price": 600000, "category": "Fedha"},
    # 14. Usafirishaji
    {"id": "clearing", "name": "Clearing & Forwarding", "price": 400000, "category": "Usafirishaji"},
    {"id": "freight", "name": "Freight Forwarding (Local)", "price": 300000, "category": "Usafirishaji"},
    {"id": "preshipment", "name": "Pre-shipment Inspection (Local)", "price": 300000, "category": "Usafirishaji"},
    {"id": "cargo_valuation", "name": "Cargo Valuation/Cargo Survey (Local)", "price": 400000, "category": "Usafirishaji"},
    {"id": "cargo_sourcing", "name": "Cargo Sourcing", "price": 300000, "category": "Usafirishaji"},
    {"id": "cargo_super", "name": "Cargo Superintendence", "price": 400000, "category": "Usafirishaji"},
    {"id": "cargo_handling", "name": "Cargo Handling", "price": 1000000, "category": "Usafirishaji"},
    {"id": "harbour", "name": "Harbour/Airport Management", "price": 1000000, "category": "Usafirishaji"},
    {"id": "ship_chandelling", "name": "Ship Chandelling", "price": 200000, "category": "Usafirishaji"},
    {"id": "maritime", "name": "Maritime Transportation", "price": 600000, "category": "Usafirishaji"},
    {"id": "ship_charter", "name": "Ship Charter", "price": 800000, "category": "Usafirishaji"},
    # 15. Bima na Real Estate
    {"id": "general_insurance", "name": "General Insurance/Assurance (Local)", "price": 1000000, "category": "Bima na Real Estate"},
    {"id": "underwriting", "name": "Underwriting & Loss Assessment", "price": 600000, "category": "Bima na Real Estate"},
    {"id": "reinsurance", "name": "Re-insurance & Endowment", "price": 800000, "category": "Bima na Real Estate"},
    {"id": "real_estate", "name": "Real Estate (Local)", "price": 600000, "category": "Bima na Real Estate"},
    {"id": "property_mgmt", "name": "Property Management (Local)", "price": 500000, "category": "Bima na Real Estate"},
    {"id": "estate_agent", "name": "Estate Agent (Local)", "price": 400000, "category": "Bima na Real Estate"},
    {"id": "property_dev", "name": "Property Development (Local)", "price": 400000, "category": "Bima na Real Estate"},
    # 16. Telecom
    {"id": "isp_local", "name": "Internet Service Provider (Local)", "price": 600000, "category": "Telecom"},
    {"id": "isp_agent2", "name": "Internet Service Provider Agent", "price": 400000, "category": "Telecom"},
    {"id": "internet_cafe2", "name": "Internet Café", "price": 200000, "category": "Telecom"},
    {"id": "telecom_services2", "name": "Telecommunication Services", "price": 300000, "category": "Telecom"},
    {"id": "telecom_accessories2", "name": "Selling Telecommunication Accessories", "price": 300000, "category": "Telecom"},
    {"id": "cellular", "name": "Cellular Telephone Operator (Local)", "price": 600000, "category": "Telecom"},
    {"id": "payphone2", "name": "Payphone Operator", "price": 400000, "category": "Telecom"},
    # 17. Media
    {"id": "radio_tv", "name": "Radio & Television", "price": 400000, "category": "Media"},
    {"id": "broadcast_tv", "name": "Broadcasting Television Provider", "price": 400000, "category": "Media"},
    {"id": "tv_station", "name": "Radio/Television Transmission Station", "price": 300000, "category": "Media"},
    # 18. Viwanda
    {"id": "industry_small", "name": "Small-scale Industry", "price": 100000, "category": "Viwanda"},
    {"id": "industry_medium", "name": "Medium-scale Industry", "price": 400000, "category": "Viwanda"},
    {"id": "industry_large", "name": "Large-scale Industry", "price": 600000, "category": "Viwanda"},
    # 19. Utalii
    {"id": "tourist_hotel2", "name": "Tourist Hotel", "price": 150000, "category": "Utalii", "note": "+ TSh 2,000/bedroom"},
    {"id": "tourist_lodge2", "name": "Tourist Lodge", "price": 150000, "category": "Utalii"},
    {"id": "tourist_camp2", "name": "Tourist Camp", "price": 100000, "category": "Utalii", "note": "+ TSh 3,000/hut/cottage"},
    {"id": "tourist_op_local", "name": "Tourist Operator (Local)", "price": 200000, "category": "Utalii"},
    {"id": "tourist_op_foreign", "name": "Tourist Operator (Foreign)", "price": 2500000, "category": "Utalii", "note": "USD 1,000 (approx TSh)"},
    # 20. Import/Export
    {"id": "export_cattle", "name": "Export – Cattle", "price": 300000, "category": "Import/Export"},
    {"id": "export_livestock", "name": "Export – Other Livestock", "price": 250000, "category": "Import/Export"},
    {"id": "export_raw", "name": "Export – Raw Materials", "price": 300000, "category": "Import/Export"},
    {"id": "export_agri", "name": "Export – Agricultural Goods", "price": 100000, "category": "Import/Export"},
    {"id": "export_finished", "name": "Export – Finished Goods/Other Commodities", "price": 100000, "category": "Import/Export"},
    {"id": "export_transit", "name": "Export – Transit Trade", "price": 300000, "category": "Import/Export"},
    {"id": "importation", "name": "Importation of Merchandise", "price": 400000, "category": "Import/Export"},
    # 21. Dealership
    {"id": "vehicle_dealer", "name": "Motor Vehicle Dealer", "price": 400000, "category": "Dealership"},
    {"id": "vehicle_assembly", "name": "Motor Vehicle Assembly", "price": 500000, "category": "Dealership"},
    {"id": "broadcast_dealer", "name": "Broadcasting Apparatus Dealer", "price": 400000, "category": "Dealership"},
    {"id": "arms_dealer", "name": "Arms & Ammunition Dealer", "price": 1000000, "category": "Dealership"},
    {"id": "explosives_dealer", "name": "Mining Explosives Dealer (Local)", "price": 1000000, "category": "Dealership"},
    # 22. Postal
    {"id": "postal_hq", "name": "Postal Services – Headquarters", "price": 300000, "category": "Postal"},
    {"id": "postal_muni", "name": "Postal Services – Municipal", "price": 200000, "category": "Postal"},
    {"id": "postal_town", "name": "Postal Services – Town/District", "price": 100000, "category": "Postal"},
    {"id": "courier", "name": "Courier/Mailing Agent (Local)", "price": 400000, "category": "Postal"},
    {"id": "ems", "name": "Expedited Mail Service (Local)", "price": 400000, "category": "Postal"},
    # 23. Professional
    {"id": "consultancy", "name": "Business Consultancy (Local)", "price": 200000, "category": "Professional"},
    {"id": "lawyer", "name": "Lawyer (Local)", "price": 300000, "category": "Professional"},
    {"id": "tax_practitioner", "name": "Tax Practitioner (Local)", "price": 300000, "category": "Professional"},
    {"id": "quantity_surveyor", "name": "Quantity Surveyor (Local)", "price": 300000, "category": "Professional"},
    {"id": "engineer", "name": "Engineer (Local)", "price": 300000, "category": "Professional"},
    {"id": "auditor", "name": "Auditor/Accountant (Local)", "price": 300000, "category": "Professional"},
    {"id": "medical_prac", "name": "Medical Practitioner (Local)", "price": 150000, "category": "Professional"},
    {"id": "other_consult", "name": "Other Consultancy (Local)", "price": 200000, "category": "Professional"},
    # 24. Burudani
    {"id": "entertainment_hall", "name": "Entertainment Hall", "price": 300000, "category": "Burudani"},
    {"id": "slot_machine", "name": "Slot Machine (Local, per station)", "price": 300000, "category": "Burudani"},
    {"id": "nightclub", "name": "Night Club", "price": 500000, "category": "Burudani"},
    {"id": "casino_dar", "name": "Casino (Dar es Salaam)", "price": 100000000, "category": "Burudani", "note": "USD 40,000 (approx)"},
    {"id": "casino_other", "name": "Casino (Other Towns)", "price": 37500000, "category": "Burudani", "note": "USD 15,000 (approx)"},
]

LICENSE_BY_ID = {x["id"]: x for x in LICENSE_CATALOG}



@app.route("/nakala/license", methods=["GET", "POST"])
@login_required
def nakala_license():
    if request.method == "POST":
        license_type = request.form.get("license_type", "").strip()
        full_name = request.form.get("full_name", "").strip()
        mother_name = request.form.get("mother_name", "").strip()
        nida_number = request.form.get("nida_number", "").strip()
        phone1 = request.form.get("phone1", "").strip()
        phone2 = request.form.get("phone2", "").strip()
        primary_school = request.form.get("primary_school", "").strip()
        year_completed = request.form.get("year_completed", "").strip()
        school_district = request.form.get("school_district", "").strip()
        school_region = request.form.get("school_region", "").strip()
        nida_reg_district = request.form.get("nida_reg_district", "").strip()
        nida_reg_region = request.form.get("nida_reg_region", "").strip()
        nida_reg_street = request.form.get("nida_reg_street", "").strip()

        lic = LICENSE_BY_ID.get(license_type)
        if not lic:
            flash("Chagua aina ya leseni kutoka kwenye orodha.", "error")
            return redirect(url_for("nakala_license"))

        if not all([full_name, mother_name, nida_number, phone1, primary_school,
                    year_completed, school_district, school_region,
                    nida_reg_district, nida_reg_region, nida_reg_street]):
            flash("Jaza taarifa zote muhimu.", "error")
            return redirect(url_for("nakala_license"))

        if len(nida_number) > 20 or not nida_number.isdigit():
            flash("Namba ya NIDA/NIN lazima iwe namba tu (max 20).", "error")
            return redirect(url_for("nakala_license"))

        session["nakala_draft"] = {
            "service_type": "license",
            "license_type": lic["name"],
            "license_id": lic["id"],
            "full_name": full_name,
            "mother_name": mother_name,
            "nida_number": nida_number,
            "phone1": phone1,
            "phone2": phone2,
            "primary_school": primary_school,
            "year_completed": year_completed,
            "school_district": school_district,
            "school_region": school_region,
            "nida_reg_district": nida_reg_district,
            "nida_reg_region": nida_reg_region,
            "nida_reg_street": nida_reg_street,
            "price": lic["price"],
        }
        return redirect(url_for("nakala_review"))

    return render_template("nakala_license.html", licenses=LICENSE_CATALOG, categories=LICENSE_CATEGORIES)


@app.route("/nakala/nida", methods=["GET", "POST"])
@login_required
def nakala_nida():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        mother_name = request.form.get("mother_name", "").strip()
        nida_number = request.form.get("nida_number", "").strip()
        phone1 = request.form.get("phone1", "").strip()
        phone2 = request.form.get("phone2", "").strip()
        primary_school = request.form.get("primary_school", "").strip()
        year_completed = request.form.get("year_completed", "").strip()
        school_district = request.form.get("school_district", "").strip()
        school_region = request.form.get("school_region", "").strip()
        nida_reg_district = request.form.get("nida_reg_district", "").strip()
        nida_reg_region = request.form.get("nida_reg_region", "").strip()
        nida_reg_street = request.form.get("nida_reg_street", "").strip()
        nida_reg_phone = request.form.get("nida_reg_phone", "").strip()

        if not all([full_name, mother_name, nida_number, phone1, primary_school,
                    year_completed, school_district, school_region,
                    nida_reg_district, nida_reg_region, nida_reg_street, nida_reg_phone]):
            flash("Jaza taarifa zote muhimu.", "error")
            return redirect(url_for("nakala_nida"))

        if len(nida_number) > 20 or not nida_number.isdigit():
            flash("Namba ya NIDA/NIN lazima iwe namba tu (max 20).", "error")
            return redirect(url_for("nakala_nida"))

        session["nakala_draft"] = {
            "service_type": "nida",
            "full_name": full_name,
            "mother_name": mother_name,
            "nida_number": nida_number,
            "phone1": phone1,
            "phone2": phone2,
            "primary_school": primary_school,
            "year_completed": year_completed,
            "school_district": school_district,
            "school_region": school_region,
            "nida_reg_district": nida_reg_district,
            "nida_reg_region": nida_reg_region,
            "nida_reg_street": nida_reg_street,
            "nida_reg_phone": nida_reg_phone,
            "price": 3000,
        }
        return redirect(url_for("nakala_review"))

    return render_template("nakala_nida.html")


@app.route("/nakala/review")
@login_required
def nakala_review():
    draft = session.get("nakala_draft")
    if not draft:
        flash("Hakuna taarifa za kukaguliwa. Anza upya.", "error")
        return redirect(url_for("nakala"))
    return render_template("nakala_review.html", draft=draft)


@app.route("/nakala/pay", methods=["POST"])
@login_required
def nakala_pay():
    user = get_current_user()
    draft = session.get("nakala_draft")
    if not draft:
        flash("Hakuna taarifa. Anza upya.", "error")
        return redirect(url_for("nakala"))

    payment_phone = request.form.get("payment_phone", "").strip()
    if not payment_phone:
        flash("Weka namba ya simu yenye pesa.", "error")
        return redirect(url_for("nakala_review"))

    code = generate_nakala_code()
    while NakalaRequest.query.filter_by(request_code=code).first():
        code = generate_nakala_code()

    license_name = (draft.get("license_type") or "")[:200]
    try:
        price_val = int(draft.get("price") or 0)
    except (TypeError, ValueError):
        price_val = 0

    req = NakalaRequest(
        request_code=code,
        user_id=user.id,
        service_type=draft["service_type"],
        full_name=draft["full_name"],
        mother_name=draft.get("mother_name", ""),
        nida_number=draft.get("nida_number", ""),
        phone1=draft["phone1"],
        phone2=draft.get("phone2", ""),
        primary_school=draft.get("primary_school", ""),
        year_completed=draft.get("year_completed", ""),
        school_district=draft.get("school_district", ""),
        school_region=draft.get("school_region", ""),
        nida_reg_district=draft.get("nida_reg_district", ""),
        nida_reg_region=draft.get("nida_reg_region", ""),
        nida_reg_street=draft.get("nida_reg_street", ""),
        nida_reg_phone=draft.get("nida_reg_phone", ""),
        had_tin_before=draft.get("had_tin_before", ""),
        license_type=license_name,
        price=price_val,
        status="pending",
    )
    try:
        db.session.add(req)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Hitilafu ya kuhifadhi ombi: {str(e)[:120]}", "error")
        return redirect(url_for("nakala_review"))

    session.pop("nakala_draft", None)

    price = req.price
    if CLICKPESA_CLIENT_ID and CLICKPESA_API_KEY and price > 0:
        try:
            token = get_clickpesa_token()
            url = "https://api.clickpesa.com/third-parties/payments/initiate-ussd-push-request"
            headers = {
                "Authorization": token,
                "Content-Type": "application/json"
            }
            payload = {
                "amount": str(price),
                "currency": "TZS",
                "orderReference": f"NK{req.id}{int(time.time())}"[:20],
                "phoneNumber": normalize_phone(payment_phone)
            }
            response = requests.post(url, json=payload, headers=headers, timeout=20)
            data = response.json()
            if response.status_code in [200, 201]:
                req.status = "paid"
                db.session.commit()
                flash("Ombi la malipo limeshushwa! Angalia simu yako na uingize PIN. Ombi limetumwa kwa admin.", "success")
                return redirect(url_for("my_nakala"))
            else:
                flash(f"ClickPesa imeshindwa: {data}. Ombi limehifadhiwa. Lipa manual: {PAYMENT_NETWORK} {PAYMENT_NUMBER}", "error")
        except Exception as e:
            flash(f"Hitilafu ya malipo: {str(e)}. Ombi limehifadhiwa. Lipa manual: {PAYMENT_NETWORK} {PAYMENT_NUMBER}", "error")
    else:
        flash(
            f"Ombi limehifadhiwa! Lipa TSh {price:,} kwa {PAYMENT_NETWORK} {PAYMENT_NUMBER} ({PAYMENT_NAME}). "
            "Baada ya malipo wasiliana na admin.",
            "success",
        )
    return redirect(url_for("my_nakala"))


@app.route("/my-nakala")
@login_required
def my_nakala():
    user = get_current_user()
    requests_list = (
        NakalaRequest.query.filter_by(user_id=user.id)
        .order_by(NakalaRequest.created_at.desc())
        .all()
    )
    return render_template("my_nakala.html", requests=requests_list)


@app.route("/nakala/<int:req_id>")
@login_required
def nakala_detail(req_id):
    user = get_current_user()
    req = NakalaRequest.query.get_or_404(req_id)
    if req.user_id != user.id and not user.is_admin:
        flash("Huna ruhusa.", "error")
        return redirect(url_for("my_nakala"))
    return render_template("nakala_detail.html", req=req)


@app.route("/dashboard")
@admin_required
def dashboard():
    users_count = User.query.count()
    pending_count = Order.query.filter_by(status="pending").count()
    agency_pending = Application.query.filter_by(status="pending").count()
    nakala_pending = NakalaRequest.query.filter(NakalaRequest.status.in_(["pending", "paid", "processing"])).count()
    orders = Order.query.order_by(Order.created_at.desc()).limit(50).all()
    applications = Application.query.order_by(Application.created_at.desc()).limit(50).all()
    slides = Slide.query.order_by(Slide.sort_order.asc(), Slide.id.asc()).all()
    nakala_list = NakalaRequest.query.order_by(NakalaRequest.created_at.desc()).limit(50).all()
    # Active password-reset OTPs (for admin to read to customer via WhatsApp)
    pending_resets = (
        User.query.filter(User.reset_otp.isnot(None), User.reset_otp != "")
        .filter(
            (User.reset_otp_expires.is_(None))
            | (User.reset_otp_expires > datetime.utcnow())
        )
        .order_by(User.reset_otp_expires.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "dashboard.html",
        users_count=users_count,
        pending_count=pending_count,
        agency_pending=agency_pending,
        nakala_pending=nakala_pending,
        orders=orders,
        applications=applications,
        slides=slides,
        nakala_list=nakala_list,
        pending_resets=pending_resets,
    )



@app.route("/admin/agency/status/<int:application_id>", methods=["POST"])
@admin_required
def update_agency_status(application_id):
    application = Application.query.get_or_404(application_id)
    status = request.form.get("status", "pending")
    note = request.form.get("note", "").strip()
    if status not in ("pending", "processing", "completed", "rejected"):
        flash("Status si sahihi.", "error")
        return redirect(url_for("dashboard"))
    application.status = status
    application.admin_note = note
    db.session.commit()
    db.session.add(AgencyMessage(
        application_id=application.id,
        sender="system",
        message=f"Status: {status}" + (f" — {note}" if note else ""),
    ))
    db.session.commit()
    flash("Status imehifadhiwa.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/agency/chat/<int:application_id>", methods=["GET", "POST"])
@admin_required
def admin_agency_chat(application_id):
    application = Application.query.get_or_404(application_id)
    application.username = application.user.username if application.user else "—"
    if request.method == "POST":
        text = request.form.get("message", "").strip()
        if text:
            msg = AgencyMessage(
                application_id=application.id,
                sender="admin",
                message=text,
            )
            db.session.add(msg)
            db.session.commit()
            flash("Ujumbe umetumwa.", "success")
        return redirect(url_for("admin_agency_chat", application_id=application.id))
    messages = (
        AgencyMessage.query.filter_by(application_id=application.id)
        .order_by(AgencyMessage.created_at.asc())
        .all()
    )
    return render_template(
        "admin_agency_chat.html",
        application=application,
        messages=messages,
    )


# ---------------------------------------------------------------------------
# Admin Slideshow
# ---------------------------------------------------------------------------
@app.route("/admin/slides/add", methods=["POST"])
@admin_required
def admin_slide_add():
    image_file = request.files.get("image")
    link = request.form.get("link", "").strip()
    title = request.form.get("title", "").strip()
    sort_order_raw = request.form.get("sort_order", "0").strip()

    if not image_file or not image_file.filename:
        flash("Picha ni lazima. Chagua picha ya banner.", "error")
        return redirect(url_for("dashboard"))

    if not link:
        flash("Link ni lazima. Weka URL au path (mfano /order).", "error")
        return redirect(url_for("dashboard"))

    if not allowed_slide_file(image_file.filename):
        flash(
            "Aina ya faili hairuhusiwi. Ruhusu: png, jpg, jpeg, gif, webp, bmp, svg, ico, tiff, heic, avif.",
            "error",
        )
        return redirect(url_for("dashboard"))

    try:
        sort_order = int(sort_order_raw) if sort_order_raw else 0
    except ValueError:
        sort_order = 0

    slide_folder = os.path.join(app.config["UPLOAD_FOLDER"], "slide")
    os.makedirs(slide_folder, exist_ok=True)

    ext = image_file.filename.rsplit(".", 1)[1].lower()
    filename = secure_filename(f"slide_{secrets.token_hex(6)}.{ext}")
    filepath = os.path.join(slide_folder, filename)
    image_file.save(filepath)

    slide = Slide(
        image=filename,
        link=link,
        title=title,
        sort_order=sort_order,
        is_active=True,
    )
    db.session.add(slide)
    db.session.commit()
    flash("Tangazo / slide limeongezwa!", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/slides/update/<int:slide_id>", methods=["POST"])
@admin_required
def admin_slide_update(slide_id):
    slide = Slide.query.get_or_404(slide_id)
    link = request.form.get("link", "").strip()
    title = request.form.get("title", "").strip()
    sort_order_raw = request.form.get("sort_order", "0").strip()
    is_active = request.form.get("is_active") == "1"

    try:
        sort_order = int(sort_order_raw) if sort_order_raw else 0
    except ValueError:
        sort_order = 0

    image_file = request.files.get("image")
    if image_file and image_file.filename and allowed_slide_file(image_file.filename):
        slide_folder = os.path.join(app.config["UPLOAD_FOLDER"], "slide")
        os.makedirs(slide_folder, exist_ok=True)
        old_path = os.path.join(slide_folder, slide.image)
        if os.path.isfile(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
        ext = image_file.filename.rsplit(".", 1)[1].lower()
        filename = secure_filename(f"slide_{secrets.token_hex(6)}.{ext}")
        image_file.save(os.path.join(slide_folder, filename))
        slide.image = filename

    slide.link = link
    slide.title = title
    slide.sort_order = sort_order
    slide.is_active = is_active
    db.session.commit()
    flash("Mabadiliko yamehifadhiwa.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/slides/delete/<int:slide_id>", methods=["POST"])
@admin_required
def admin_slide_delete(slide_id):
    slide = Slide.query.get_or_404(slide_id)
    slide_folder = os.path.join(app.config["UPLOAD_FOLDER"], "slide")
    filepath = os.path.join(slide_folder, slide.image)
    if os.path.isfile(filepath):
        try:
            os.remove(filepath)
        except OSError:
            pass
    db.session.delete(slide)
    db.session.commit()
    flash("Slide imefutwa.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/nakala/update/<int:req_id>", methods=["POST"])
@admin_required
def admin_nakala_update(req_id):
    req = NakalaRequest.query.get_or_404(req_id)
    status = request.form.get("status", "pending")
    admin_note = request.form.get("admin_note", "").strip()
    result_message = request.form.get("result_message", "").strip()

    if status not in ("pending", "paid", "processing", "completed", "rejected"):
        flash("Status si sahihi.", "error")
        return redirect(url_for("dashboard"))

    result_file = request.files.get("result_file")
    if result_file and result_file.filename:
        allowed_ext = {"pdf", "png", "jpg", "jpeg", "webp", "gif", "bmp"}
        ext = result_file.filename.rsplit(".", 1)[-1].lower() if "." in result_file.filename else ""
        if ext in allowed_ext:
            nakala_folder = os.path.join(app.config["UPLOAD_FOLDER"], "nakala")
            os.makedirs(nakala_folder, exist_ok=True)
            if req.result_file:
                old_path = os.path.join(app.config["UPLOAD_FOLDER"], req.result_file)
                if os.path.isfile(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass
            filename = secure_filename(f"nakala_{req.id}_{secrets.token_hex(4)}.{ext}")
            result_file.save(os.path.join(nakala_folder, filename))
            req.result_file = f"nakala/{filename}"
        else:
            flash("Aina ya faili hairuhusiwi. Ruhusu: pdf, png, jpg, jpeg, webp, gif, bmp.", "error")
            return redirect(url_for("dashboard"))

    req.status = status
    req.admin_note = admin_note
    req.result_message = result_message
    db.session.commit()
    flash(f"Ombi {req.request_code} limehifadhiwa.", "success")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------
def seed_data():
    if User.query.filter_by(username="admin").first():
        return

    admin = User(
        username="admin",
        phone="0700000000",
        email="admin@kijiji.tz",
        is_admin=True,
    )
    admin.set_password("admin123")
    db.session.add(admin)

    demo = User(username="demo", phone="0712345678", email="demo@kijiji.tz")
    demo.set_password("demo123")
    db.session.add(demo)

    bundles = [
        Bundle(name="Daily", amount="1 GB", price=1000, validity="Siku 1"),
        Bundle(name="Weekly", amount="5 GB", price=5000, validity="Siku 7"),
        Bundle(name="Monthly", amount="15 GB", price=15000, validity="Siku 30"),
        Bundle(name="Monthly+", amount="30 GB", price=25000, validity="Siku 30"),
        Bundle(name="Mega", amount="50 GB", price=40000, validity="Siku 30"),
    ]
    for b in bundles:
        db.session.add(b)

    offers = [
        Offer(
            title="Offer ya Leo",
            amount="10 GB",
            price=8000,
            description="Ofa maalum – lipa na upate haraka!",
        ),
    ]
    for o in offers:
        db.session.add(o)

    networks_data = {
        "Vodacom": [
            ("📱", "Usajili wa Laini", "Sajili laini mpya ya Vodacom"),
            ("🏪", "Uwakala", "Omba kuwa wakala wa Vodacom"),
            ("💳", "Merchant / Lipa kwa Simu", "Usajili wa merchant na malipo"),
            ("🔄", "Replacement", "Badilisha SIM au recovery"),
        ],
        "Airtel": [
            ("📱", "Usajili wa Laini", "Sajili laini mpya ya Airtel"),
            ("🏪", "Uwakala", "Omba kuwa wakala wa Airtel"),
            ("💳", "Merchant", "Usajili wa merchant"),
        ],
        "Halotel": [
            ("📱", "Usajili wa Laini", "Sajili laini mpya ya Halotel"),
            ("🏪", "Uwakala", "Omba kuwa wakala wa Halotel"),
            ("📡", "Bando & Packages", "Omba packages maalum"),
        ],
        "Tigo": [
            ("📱", "Usajili wa Laini", "Sajili laini mpya ya Tigo"),
            ("🏪", "Uwakala", "Omba kuwa wakala wa Tigo"),
            ("💳", "Merchant", "Usajili wa merchant"),
        ],
        "TTCL": [
            ("📱", "Usajili wa Laini", "Sajili laini ya TTCL"),
            ("🏪", "Uwakala", "Huduma za uwakala TTCL"),
        ],
    }
    for net_name, services in networks_data.items():
        net = Network(network=net_name)
        db.session.add(net)
        db.session.flush()
        for icon, title, desc in services:
            svc = AgencyService(
                network_id=net.id,
                network=net_name,
                title=title,
                description=desc,
                icon=icon,
            )
            db.session.add(svc)

    db.session.commit()
    print("✓ Seed data created (admin/admin123, demo/demo123)")


def ensure_agency_columns():
    """Add missing columns (Application + User) — safe for Railway Postgres / SQLite."""
    from sqlalchemy import text, inspect
    try:
        insp = inspect(db.engine)
        tables = insp.get_table_names()

        if "application" in tables:
            existing = {c["name"] for c in insp.get_columns("application")}
            cols = {
                "tin_number": "VARCHAR(50) DEFAULT ''",
                "target_phone": "VARCHAR(20) DEFAULT ''",
                "contact_phone": "VARCHAR(20) DEFAULT ''",
                "tin_cert": "VARCHAR(255) DEFAULT ''",
                "id_doc": "VARCHAR(255) DEFAULT ''",
                "shop_photo": "VARCHAR(255) DEFAULT ''",
                "extra_data": "TEXT DEFAULT ''",
            }
            with db.engine.begin() as conn:
                for name, typedef in cols.items():
                    if name not in existing:
                        conn.execute(text(f"ALTER TABLE application ADD COLUMN {name} {typedef}"))
                        print(f"✓ Added column application.{name}")

        if "user" in tables:
            existing = {c["name"] for c in insp.get_columns("user")}
            user_cols = {
                "reset_otp": "VARCHAR(10) DEFAULT ''",
                "reset_otp_expires": "TIMESTAMP",
            }
            with db.engine.begin() as conn:
                for name, typedef in user_cols.items():
                    if name not in existing:
                        conn.execute(text(f'ALTER TABLE "user" ADD COLUMN {name} {typedef}'))
                        print(f"✓ Added column user.{name}")

        # Widen license_type (majina marefu ya leseni)
        if "nakala_request" in tables:
            try:
                with db.engine.begin() as conn:
                    dialect = db.engine.dialect.name
                    if dialect == "postgresql":
                        conn.execute(text("ALTER TABLE nakala_request ALTER COLUMN license_type TYPE VARCHAR(200)"))
                    elif dialect == "sqlite":
                        pass  # SQLite ignores length
                    print("✓ nakala_request.license_type widened to 200")
            except Exception as e2:
                print(f"license_type widen note: {e2}")
    except Exception as e:
        print(f"ensure_agency_columns warning: {e}")


with app.app_context():
    db.create_all()
    ensure_agency_columns()
    seed_data()


@app.cli.command("init-db")
def init_db():
    db.create_all()
    seed_data()
    print("Database ready.")


@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')


@app.route("/media/<path:filename>")
def media_file(filename):
    folder = app.config["UPLOAD_FOLDER"]
    full = os.path.join(folder, filename)
    if os.path.isfile(full):
        return send_from_directory(folder, filename)
    static_fallback = os.path.join(basedir, "static", "uploads")
    full2 = os.path.join(static_fallback, filename)
    if os.path.isfile(full2):
        return send_from_directory(static_fallback, filename)
    return f"File not found: {filename}\nLooked in: {folder}\nand: {static_fallback}", 404


@app.route("/admin/debug-uploads")
@admin_required
def debug_uploads():
    folder = app.config["UPLOAD_FOLDER"]
    slide_dir = os.path.join(folder, "slide")
    files = []
    if os.path.isdir(slide_dir):
        try:
            files = os.listdir(slide_dir)
        except OSError as e:
            files = [f"ERROR: {e}"]
    slides_db = Slide.query.order_by(Slide.id).all()
    lines = [
        f"UPLOAD_FOLDER = {folder}",
        f"UPLOAD_ROOT env = {os.environ.get('UPLOAD_ROOT', '(not set)')}",
        f"basedir = {basedir}",
        f"slide folder exists = {os.path.isdir(slide_dir)}",
        f"files in slide/ = {files}",
        "",
        "Slides in DB:",
    ]
    for s in slides_db:
        path = os.path.join(slide_dir, s.image)
        lines.append(
            f"  id={s.id} image={s.image!r} active={s.is_active} "
            f"file_exists={os.path.isfile(path)} "
            f"url=/media/slide/{s.image}"
        )
    return "<pre style='font-size:13px;padding:16px;white-space:pre-wrap;'>" + "\n".join(lines) + "</pre>"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
