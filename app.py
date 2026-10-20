from flask import Flask, redirect

app = Flask(__name__)

@app.route('/')
def home():
    return redirect("https://mrsulebusinessservices.dutoza.com/download-app", code=302)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
