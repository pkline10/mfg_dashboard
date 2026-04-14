#!/usr/bin/env python3
"""Development entry point.  Production uses gunicorn via systemd."""
import os
from app import create_app

app = create_app(os.environ.get("FLASK_ENV", "production"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=app.config["DEBUG"])
