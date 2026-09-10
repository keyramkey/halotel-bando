import os
import secrets
import time
import requests
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from flask import current_app

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "kijiji-tanzania-secret-key-change-me")

basedir = os.path.abspath(os.path.dirname(__file__))

# Database - Railway PostgreSQL
database_url = os.environ.get("DATABASE_URL")
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    # Local development
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "instance", "kijiji.db")

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join(basedir, "static", "uploads")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)

db = SQLAlchemy(app)

# Manual payment
PAYMENT_NUMBER = os.environ.get("PAYMENT_NUMBER", "37912416")
PAYMENT_NAME = os.environ.get("PAYMENT_NAME", "Matondo Maduhu")
PAYMENT_NETWORK = os.environ.get("PAYMENT_NETWORK", "Vodacom")

# ClickPesa
CLICKPESA_CLIENT_ID = os.environ.get("CLICKPESA_CLIENT_ID")
CLICKPESA_API_KEY = os.environ.get("CLICKPESA_API_KEY")

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
    status = db.Column(db.String(30), default="pending")
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


class AgencyMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), nullable=False)
    sender = db.Column(db.String(20), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


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
            flash("Tafadhali ingia kwanza.", "error")
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


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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
            session["user_id"] = user.id
            flash("Umefanikiwa kuingia.", "success")
            return redirect(url_for("home"))
        flash("Username/namba/email au password si sahihi.", "error")
        return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Umetoka kwenye akaunti.", "success")
    return redirect(url_for("home"))


# ---------------------------------------------------------------------------
# Main pages
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    bundles = Bundle.query.filter_by(is_active=True).all()
    offers = Offer.query.filter_by(is_active=True).all()

    # Picha za slideshow
    slide_folder = os.path.join(current_app.static_folder, 'uploads', 'slide')
    slides = []
    if os.path.exists(slide_folder):
        slides = [
            f for f in os.listdir(slide_folder)
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))
        ]
        slides.sort()  # optional

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

        order_note = f"Namba ya Kuwekewa: {target_phone}" + (f" | Maelezo: {note}" if note else "")
        new_order = Order(
            user_id=user.id,
            phone=target_phone,
            amount=amount,
            price=price,
            note=order_note,
            status="pending",
        )
        db.session.add(new_order)
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
                    "orderReference": f"ORD{new_order.id}{int(time.time())}",
                    "phoneNumber": normalize_phone(payment_phone)
                }
                response = requests.post(url, json=payload, headers=headers, timeout=20)
                data = response.json()
                if response.status_code in [200, 201]:
                    flash("Ombi la malipo limeshushwa! Angalia simu yako na uingize PIN.", "success")
                    return redirect(url_for("asante"))
                else:
                    flash(f"ClickPesa imeshindwa: {data}. Lipa manual: {PAYMENT_NETWORK} {PAYMENT_NUMBER}", "error")
            except Exception as e:
                flash(f"Hitilafu ya malipo: {str(e)}. Lipa manual: {PAYMENT_NETWORK} {PAYMENT_NUMBER}", "error")
        else:
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
    if status in ("pending", "completed", "rejected"):
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


@app.route("/agency")
def agency():
    networks = Network.query.filter_by(is_active=True).all()
    return render_template("agency.html", networks=networks)


@app.route("/agency/<network>")
def agency_network(network):
    net = Network.query.filter_by(network=network, is_active=True).first_or_404()
    services = AgencyService.query.filter_by(network=network, is_active=True).all()
    return render_template("agency_network.html", network=network, services=services)


@app.route("/agency/apply/<int:service_id>", methods=["GET", "POST"])
@login_required
def agency_apply(service_id):
    service = AgencyService.query.get_or_404(service_id)
    user = get_current_user()
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        id_type = request.form.get("id_type", "").strip()
        id_number = request.form.get("id_number", "").strip()
        line_type = request.form.get("line_type", "").strip()
        region = request.form.get("region", "").strip()
        district = request.form.get("district", "").strip()
        ward = request.form.get("ward", "").strip()
        description = request.form.get("description", "").strip()
        if not full_name or not phone or not id_type or not id_number:
            flash("Jaza taarifa muhimu zote.", "error")
            return redirect(url_for("agency_apply", service_id=service.id))
        code = generate_application_code()
        while Application.query.filter_by(application_code=code).first():
            code = generate_application_code()
        app_obj = Application(
            application_code=code,
            user_id=user.id,
            service_id=service.id,
            network=service.network,
            service_type=service.title,
            full_name=full_name,
            phone=phone,
            id_type=id_type,
            id_number=id_number,
            line_type=line_type,
            region=region,
            district=district,
            ward=ward,
            description=description,
            status="pending",
        )
        db.session.add(app_obj)
        db.session.flush()
        sys_msg = AgencyMessage(
            application_id=app_obj.id,
            sender="system",
            message=f"Ombi {code} limepokelewa. Status: Inasubiri. Timu yetu itakujibu hivi karibuni.",
        )
        db.session.add(sys_msg)
        db.session.commit()
        flash(f"Ombi limetumwa! Code: {code}. Unaweza kufuatilia kwenye Maombi Yangu.", "success")
        return redirect(url_for("my_applications"))
    return render_template("line_registration.html", service=service)


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


@app.route("/dashboard")
@admin_required
def dashboard():
    users_count = User.query.count()
    pending_count = Order.query.filter_by(status="pending").count()
    agency_pending = Application.query.filter_by(status="pending").count()
    orders = Order.query.order_by(Order.created_at.desc()).limit(50).all()
    applications = Application.query.order_by(Application.created_at.desc()).limit(50).all()
    return render_template(
        "dashboard.html",
        users_count=users_count,
        pending_count=pending_count,
        agency_pending=agency_pending,
        orders=orders,
        applications=applications,
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


with app.app_context():
    db.create_all()
    seed_data()


@app.cli.command("init-db")
def init_db():
    db.create_all()
    seed_data()
    print("Database ready.")


from flask import send_from_directory

@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
