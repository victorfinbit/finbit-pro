from flask import Flask, send_file
import os
import subprocess

app = Flask(__name__)


def run_finbit():

    print("Running Finbit update...")

    base = os.getcwd()
    script = os.path.join(base, "finbit.py")

    subprocess.Popen(["python3", script])

    print("Finbit launched:", script)


@app.route("/")
def dashboard():
    return send_file("dashboard.html")


@app.route("/update")
def update():

    run_finbit()

    return "update started"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

current_tf = "swing"


@app.route("/set_tf/<tf>")
def set_tf(tf):

    global current_tf

    current_tf = tf

    print("Timeframe:", current_tf)

    return "ok"
