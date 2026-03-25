from flask import Flask, send_file

app = Flask(__name__)


@app.route("/")
def dashboard():
    return send_file("dashboard.html")


if __name__ == "__main__":
    app.run(debug=True)