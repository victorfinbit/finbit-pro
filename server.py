from flask import Flask, send_file
import os
import time
import subprocess

app = Flask(__name__)

LAST_UPDATE_FILE = "last_update.txt"
UPDATE_INTERVAL = 10  # prueba corta


def should_update():

    if not os.path.exists(LAST_UPDATE_FILE):
        return True

    with open(LAST_UPDATE_FILE, "r") as f:
        last = float(f.read())

    now = time.time()

    if now - last > UPDATE_INTERVAL:
        return True

    return False


def run_finbit():

    print("Running Finbit update...")

    subprocess.run(["python", "finbit.py"])

    with open(LAST_UPDATE_FILE, "w") as f:
        f.write(str(time.time()))

    print("Finbit updated")
    @app.route("/")
def dashboard():

    if should_update():
        run_finbit()

    return send_file("dashboard.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
