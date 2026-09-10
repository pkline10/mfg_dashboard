#!/usr/bin/env python3
"""Create the dashboard database schema from the SQLAlchemy models."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app, db


def main():
    app = create_app(os.environ.get("FLASK_ENV", "production"))
    with app.app_context():
        db.create_all()
        print("Database schema is ready.")


if __name__ == "__main__":
    main()
