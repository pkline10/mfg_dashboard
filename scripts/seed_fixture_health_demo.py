#!/usr/bin/env python3
"""Seed fixture-health demo records without touching manufacturing run data."""
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app, db
from app.models import Fixture, FixtureAlertEvent, FixtureComponent, FixtureHealthCheck, FixtureHealthSnapshot
from scripts.seed_demo import FIXTURE_DEFS, seed_fixture_health


def main():
    app = create_app(os.environ.get("FLASK_ENV", "production"))
    fixture_ids = [f["fixture_id"] for f in FIXTURE_DEFS]

    with app.app_context():
        db.create_all()

        snapshot_ids = (
            select(FixtureHealthSnapshot.id)
            .where(FixtureHealthSnapshot.fixture_id.in_(fixture_ids))
        )
        db.session.query(FixtureHealthCheck).filter(
            FixtureHealthCheck.snapshot_id.in_(snapshot_ids)
        ).delete(synchronize_session=False)
        db.session.query(FixtureAlertEvent).filter(
            FixtureAlertEvent.fixture_id.in_(fixture_ids)
        ).delete(synchronize_session=False)
        db.session.query(FixtureHealthSnapshot).filter(
            FixtureHealthSnapshot.fixture_id.in_(fixture_ids)
        ).delete(synchronize_session=False)
        db.session.query(FixtureComponent).filter(
            FixtureComponent.fixture_id.in_(fixture_ids)
        ).delete(synchronize_session=False)
        db.session.query(Fixture).filter(
            Fixture.fixture_id.in_(fixture_ids)
        ).delete(synchronize_session=False)

        seed_fixture_health(datetime.now(timezone.utc).replace(tzinfo=None))
        db.session.commit()

        print(f"Seeded {len(fixture_ids)} fixture health demo records.")


if __name__ == "__main__":
    main()
