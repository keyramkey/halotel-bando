import os
import re
import logging
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt

# --------------------------
# INITIALIZATION & CONFIG
# --------------------------
app = Flask(__name__, static_folder='static', template_folder='templates')

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'jamii-tanzania-secret-key-2026')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///jamii.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# Taarifa za Malipo
PAYMENT_NUMBER = "37912416"
PAYMENT_NAME = "MR SULE DIGITAL SERVICES"

# ClickPesa Credentials
CLICKPESA_API_URL = os.environ.get('CLICKPESA_API_URL', 'https://api.clickpesa.com/v1/payments')
CLICKPESA_API_KEY = os.environ.get('CLICKPESA_API_KEY', 'YOUR_CLICKPESA_API_KEY')

# --------------------------
# DATABASE MODELS
# --------------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=True)
    password = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    
    orders = db.relationship('Order', backref='user', lazy=True, cascade="all, delete-orphan")
    applications = db.relationship('Application', backref='user', lazy=True, cascade="all, delete-orphan")

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.String(50), nullable=False)
    price = db.Column(db.Integer, nullable=True)
    phone = db.Column(db.String(20), nullable=False)
    note = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default="Pending")
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_code = db.Column(db.String(50), unique=True, nullable=False)
    network = db.Column(db.String(50), nullable=False)
    service_type = db.Column(db.String(100), nullable=False)
    line_type = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(30), default="pending")
    admin_note = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.context_processor
def inject_globals():
    return dict(PAYMENT_NUMBER=PAYMENT_NUMBER, PAYMENT_NAME=PAYMENT_NAME)

# --------------------------
# CLICKPESA HELPER FUNCTION
# --------------------------
def clean_order_reference(raw_ref):
    """
    Inasafisha reference il iwe Alphanumeric pekee (A-Z, a-z, 0-9)
    bila alama kama '-', '_', au nafasi ili kuzuia kosa la ClickPesa.
    """
    cleaned = re.sub(r'[^a-zA-Z0-9]', '', str(raw_ref))
    if not cleaned:
        cleaned = f"ORD{raw_ref}"
    return cleaned

def send_clickpesa_payment(order_id, phone_number, amount):
    cleaned_ref = clean_order_reference(order_id)
    
    payload = {
        "orderReference": cleaned_ref,
        "phoneNumber": phone_number,
        "amount": amount,
        "currency": "TZS"
    }
    headers = {
        "Authorization": f"Bearer {CLICKPESA_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(CLICKPESA_API_URL, json=payload, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        return {"error": True, "message": str(e)}

# --------------------------
# ROUTES / CONTROLLERS
# --------------------------

@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('order'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('profile'))

    if request.method == 'POST':
        identity = request.form.get('identity', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter(
            (User.username == identity) | 
            (User.phone == identity) | 
            (User.email == identity)
        ).first()

        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('profile'))
        else:
            flash("Ingizo sio sahihi. Angalia namba/username/email na password yako.", "danger")

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('profile'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if User.query.filter((User.username == username) | (User.phone == phone)).first():
            flash("Username au Namba ya simu tayari vimeshasajiliwa.", "warning")
            return redirect(url_for('register'))

        hashed_pwd = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(username=username, phone=phone, email=email, password=hashed_pwd)

        db.session.add(new_user)
        db.session.commit()

        login_user(new_user)
        flash("Akaunti yako imefanikiwa kutengenezwa!", "success")
        return redirect(url_for('profile'))

    return render_template('register.html')

@app.route('/order', methods=['GET', 'POST'])
@login_required
def order():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        amount = request.form.get('amount', '').strip()
        price_val = request.form.get('price', '0').strip()
        note = request.form.get('note', '').strip()

        try:
            price = int(price_val)
        except ValueError:
            price = 0

        new_order = Order(
            amount=amount,
            price=price,
            phone=phone,
            note=note,
            user_id=current_user.id
        )
        db.session.add(new_order)
        db.session.commit()

        # Jaribu kutuma ombi ClickPesa
        if price > 0:
            cp_result = send_clickpesa_payment(f"ORD{new_order.id}", phone, price)
            if cp_result.get("error") or cp_result.get("message"):
                msg = cp_result.get('message', 'Hazijaunganishwa kikamilifu')
                flash(f"ClickPesa imeshindwa: {msg}. Lipa manual: Vodacom {PAYMENT_NUMBER}", "warning")
            else:
                flash("Request yako imetumwa! Fuata maelekezo ya M-Pesa kwenye simu yako.", "success")
        else:
            flash("Request yako imetumwa! Muuzaji atathibitisha bei hivi karibuni.", "info")

        return redirect(url_for('profile'))

    bundles = [
        {"name": "Bando la Siku", "amount": "1 GB", "price": 2000, "validity": "Siku 1"},
        {"name": "Bando la Wiki", "amount": "5 GB", "price": 10000, "validity": "Siku 7"},
        {"name": "Bando la Mwezi", "amount": "15 GB", "price": 30000, "validity": "Siku 30"}
    ]
    return render_template('order.html', bundles=bundles)

@app.route('/profile')
@login_required
def profile():
    user_orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.id.desc()).all()
    return render_template('profile.html', orders=user_orders)

@app.route('/my_applications')
@login_required
def my_applications():
    apps = Application.query.filter_by(user_id=current_user.id).order_by(Application.id.desc()).all()
    return render_template('my_applications.html', applications=apps)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        current_user.username = request.form.get('username', '').strip()
        current_user.phone = request.form.get('phone', '').strip()
        db.session.commit()
        flash("Taarifa zako zimehifadhiwa kikamilifu!", "success")
        return redirect(url_for('settings'))

    return render_template('settings.html')

@app.route('/agency')
@login_required
def agency():
    return render_template('agency.html') if os.path.exists('templates/agency.html') else "Kurasa ya Uwakala"

@app.route('/chat')
@login_required
def chat():
    return render_template('chat.html') if os.path.exists('templates/chat.html') else "Kurasa ya Chat na Mhudumu"

@app.route('/dashboard')
@login_required
def dashboard():
    if not current_user.is_admin:
        flash("Huna ruhusa ya kuingia hapa.", "danger")
        return redirect(url_for('profile'))
    return "Admin Dashboard"

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("Umetoka kwenye mfumo.", "info")
    return redirect(url_for('login'))

# --------------------------
# PWA & SERVICE WORKER ROUTES
# --------------------------
@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json', mimetype='application/json')

# --------------------------
# MAIN EXECUTION
# --------------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)
