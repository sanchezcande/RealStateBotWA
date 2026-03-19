"""Minimal dev server to preview the landing page locally."""
from flask import Flask, render_template
import time

app = Flask(__name__)

@app.route("/")
def landing():
    return render_template("landing.html", v=int(time.time()))

if __name__ == "__main__":
    app.run(debug=True, port=5050)
