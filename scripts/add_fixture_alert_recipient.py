#!/usr/bin/env python3
"""Add or update a fixture alert recipient."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app, db
from app.models import FixtureAlertRecipient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--email")
    parser.add_argument("--slack-user-id")
    parser.add_argument("--slack-channel")
    parser.add_argument("--min-status", default="needs_attention", choices=["degraded", "needs_attention", "down"])
    parser.add_argument("--fixture-id")
    parser.add_argument("--station-type", choices=["fct", "box", "hipot"])
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--no-slack", action="store_true")
    parser.add_argument("--disabled", action="store_true")
    parser.add_argument("--notes")
    args = parser.parse_args()

    if not args.email and not args.slack_user_id and not args.slack_channel:
        parser.error("provide --email, --slack-user-id, or --slack-channel")

    app = create_app(os.environ.get("FLASK_ENV", "production"))
    with app.app_context():
        db.create_all()
        recipient = (
            FixtureAlertRecipient.query
            .filter(FixtureAlertRecipient.name == args.name)
            .one_or_none()
        )
        if recipient is None:
            recipient = FixtureAlertRecipient(name=args.name)
            db.session.add(recipient)

        recipient.email = args.email
        recipient.slack_user_id = args.slack_user_id
        recipient.slack_channel = args.slack_channel
        recipient.min_status = args.min_status
        recipient.fixture_id = args.fixture_id
        recipient.station_type = args.station_type
        recipient.notify_email = not args.no_email
        recipient.notify_slack = not args.no_slack
        recipient.enabled = not args.disabled
        recipient.notes = args.notes

        db.session.commit()
        print(f"Recipient {recipient.id}: {recipient.name}")


if __name__ == "__main__":
    main()
