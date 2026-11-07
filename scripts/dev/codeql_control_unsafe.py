"""DELIBERATELY UNSAFE. A detection control for KAV-110, never merged and never imported.

It exists only on a throwaway pull request, to show that CodeQL notices real mistakes: a scan
that finds nothing proves little until it has been seen finding something.
"""

import os
import sqlite3

from flask import Flask, request

app = Flask(__name__)


@app.route("/lookup")
def lookup() -> str:
    name = request.args.get("name", "")
    connection = sqlite3.connect("example.db")
    connection.execute("SELECT * FROM people WHERE name = '" + name + "'")  # SQL injection
    os.system("echo " + name)  # command injection
    return "ok"


if __name__ == "__main__":
    app.run(debug=True)  # the Flask debugger lets anyone run code
