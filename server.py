from flask import Flask, send_file
import os
import subprocess

app = Flask(__name__)


def run_finbit():

    print("Running Finbit update...")

    subprocess.Popen(["python3", "finbit.py"])

    print("Finbit launched")


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
