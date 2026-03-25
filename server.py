from flask import Flask, send_file
import os

app = Flask(__name__)

@app.route("/")
def dashboard():
    return send_file("dashboard.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 1000))
    app.run(host="0.0.0.0", port=port)
