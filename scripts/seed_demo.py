#!/usr/bin/env python3
"""
Seed the database with realistic demo data so the dashboard has something to show.
Run from the repo root:  python scripts/seed_demo.py
"""
import os
import sys
import random
from datetime import datetime, timedelta

# Make sure the app is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://mfg_dashboard:mfg_dashboard@localhost/mfg_dashboard",
)

from app import create_app, db
from app.models import TestRun, TestResult, Measurement

random.seed(42)

PRODUCTS = ["C1", "C2"]
FIXTURES = ["BOX-01", "BOX-02", "BOX-03"]
PHASES   = ["diagnostic", "charging"]

DIAGNOSTIC_TESTS = [
    "intercom_test",
    "buzzer_function_test",
    "led_comms_test",
    "relay_function_test",
    "voltage_accuracy_test",
    "test_cp_sense",
]
CHARGING_TESTS = [
    "current_accuracy_test",
    "charging_check_test",
    "test_cp_pwm",
]

# Realistic pass rates per test (to simulate field failure patterns)
TEST_PASS_RATES = {
    "intercom_test":        0.99,
    "buzzer_function_test": 0.97,
    "led_comms_test":       0.95,
    "relay_function_test":  0.98,
    "voltage_accuracy_test":0.99,
    "test_cp_sense":        0.93,
    "current_accuracy_test":0.97,
    "charging_check_test":  0.96,
    "test_cp_pwm":          0.98,
}

# Approx test durations in seconds (mean, stdev)
TEST_DURATIONS = {
    "intercom_test":        (4,   1),
    "buzzer_function_test": (8,   2),
    "led_comms_test":       (30,  5),
    "relay_function_test":  (12,  3),
    "voltage_accuracy_test":(15,  3),
    "test_cp_sense":        (20,  4),
    "current_accuracy_test":(45, 10),
    "charging_check_test":  (75, 10),
    "test_cp_pwm":          (35,  8),
}

MEASUREMENT_SPECS = {
    "voltage_accuracy_test": [
        ("voltage_rms_240v", 240.0, 0.5, "V"),
    ],
    "current_accuracy_test": [
        ("current_rms_40a", 40.0, 0.4, "A"),   # ±1% of 40A
        ("current_rms_6a",   6.0, 0.06, "A"),  # ±1% of 6A
    ],
    "buzzer_function_test": [
        ("dominant_freq_hz", 2500.0, 250.0, "Hz"),
    ],
    "test_cp_pwm": [
        ("duty_cycle_6a",  10.0, 0.5, "%"),
        ("duty_cycle_32a", 53.3, 0.5, "%"),
        ("duty_cycle_48a", 80.0, 0.5, "%"),
    ],
}

# Per-fixture systematic bias (simulates real-world calibration offsets).
# These make the measurement quality scatter/trend charts interesting.
# bias = fraction of target added to both nominal and value independently.
FIXTURE_BIAS = {
    # (nominal_bias_pct, value_extra_bias_pct)
    "BOX-01": (0.00,  0.00),   # reference fixture, no bias
    "BOX-02": (0.00, +0.18),   # EVSE reads slightly high on this fixture
    "BOX-03": (0.00, -0.22),   # EVSE reads slightly low on this fixture
}


def rand_dur(test_name):
    mean, std = TEST_DURATIONS.get(test_name, (10, 2))
    return max(1.0, random.gauss(mean, std))


def make_measurement(spec, test_passed, fixture_id="BOX-01"):
    name, target, tolerance, unit = spec
    _, value_bias_pct = FIXTURE_BIAS.get(fixture_id, (0.0, 0.0))
    value_bias = target * value_bias_pct / 100.0

    # nominal = what the reference instrument (Chroma) measured — near target
    nominal = target + random.gauss(0, tolerance * 0.05)

    if test_passed:
        # value = EVSE-reported reading: tracks nominal closely + fixture bias
        value = nominal + random.gauss(value_bias, tolerance * 0.25)
    else:
        # Failing measurement — outside tolerance
        value = target + tolerance * random.uniform(1.2, 2.5) * random.choice([-1, 1])

    in_tol = abs(value - nominal) <= tolerance
    return {
        "metric": name,
        "value": round(value, 4),
        "nominal": round(nominal, 4),
        "unit": unit,
        "tolerance_min": round(nominal - tolerance, 4),
        "tolerance_max": round(nominal + tolerance, 4),
        "passed": in_tol,
    }


def seed(days=90, runs_per_day_range=(8, 20)):
    app = create_app()
    with app.app_context():
        print("Clearing existing data...")
        db.session.query(Measurement).delete()
        db.session.query(TestResult).delete()
        db.session.query(TestRun).delete()
        db.session.commit()

        now = datetime.utcnow()
        total = 0

        for day_offset in range(days, -1, -1):
            day_start = now - timedelta(days=day_offset)
            day_start = day_start.replace(hour=6, minute=0, second=0, microsecond=0)

            n_runs = random.randint(*runs_per_day_range)
            # Fewer runs on weekends
            if day_start.weekday() >= 5:
                n_runs = max(1, n_runs // 4)

            for _ in range(n_runs):
                product = random.choices(PRODUCTS, weights=[0.7, 0.3])[0]
                fixture = random.choice(FIXTURES)
                phase   = random.choice(PHASES)
                tests   = DIAGNOSTIC_TESTS if phase == "diagnostic" else CHARGING_TESTS

                started = day_start + timedelta(
                    seconds=random.randint(0, 50400)
                )

                results = []
                all_passed = True
                first_failure = None
                elapsed = 0.0

                for test_name in tests:
                    dur = rand_dur(test_name)
                    passed = random.random() < TEST_PASS_RATES.get(test_name, 0.95)
                    if not passed and all_passed:
                        all_passed = False
                        first_failure = test_name

                    t_start = started + timedelta(seconds=elapsed)
                    t_end   = t_start + timedelta(seconds=dur)
                    elapsed += dur

                    meas_dicts = []
                    for spec in MEASUREMENT_SPECS.get(test_name, []):
                        meas_dicts.append(make_measurement(spec, passed, fixture))

                    results.append(TestResult(
                        test_name=test_name,
                        started_at=t_start,
                        ended_at=t_end,
                        duration_s=round(dur, 2),
                        passed=passed,
                        failure_reason=None if passed else f"{test_name} threshold exceeded",
                        measurements=[
                            Measurement(
                                metric_name=m["metric"],
                                value=m["value"],
                                nominal=m.get("nominal"),
                                unit=m["unit"],
                                tolerance_min=m["tolerance_min"],
                                tolerance_max=m["tolerance_max"],
                                passed=m["passed"],
                            )
                            for m in meas_dicts
                        ],
                    ))

                run = TestRun(
                    serial_number=f"SN{random.randint(100000, 999999)}",
                    product=product,
                    fixture_id=fixture,
                    phase=phase,
                    started_at=started,
                    ended_at=started + timedelta(seconds=elapsed),
                    duration_s=round(elapsed, 2),
                    overall_pass=all_passed,
                    failure_reason=first_failure,
                    results=results,
                )
                db.session.add(run)
                total += 1

            if day_offset % 10 == 0:
                db.session.commit()
                print(f"  {days - day_offset}/{days} days seeded ({total} runs so far)")

        db.session.commit()
        print(f"\nDone. Seeded {total} test runs over {days} days.")


if __name__ == "__main__":
    seed()
