#!/usr/bin/env python3
"""
Local test data feeder — publishes synthetic manufacturing test runs to MQTT
so the bridge picks them up and they appear on the dashboard in real time.

Usage:
  python scripts/feed_test_data.py                          # 1 run, random fixture
  python scripts/feed_test_data.py --fixture BOX-01         # specific fixture
  python scripts/feed_test_data.py --count 10 --delay 3     # 10 runs, 3s apart
  python scripts/feed_test_data.py --continuous --delay 8   # run forever
  python scripts/feed_test_data.py --fail-rate 0.2          # 20% overall fail rate
  python scripts/feed_test_data.py --product C2             # C2 units
"""
import argparse
import json
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# ── Defaults ────────────────────────────────────────────────────────────────
MQTT_HOST    = "192.168.1.188"
MQTT_PORT    = 1883
TOPIC_ROOT   = "mfg/results"

PRODUCTS  = ["C1", "C2"]
FIXTURES  = ["BOX-01", "BOX-02", "BOX-03"]

# Tests in each phase
FCT_TESTS = [
    "intercom_test",
    "buzzer_function_test",
    "relay_function_test",
    "test_cp_sense",
    "test_cp_pwm",
]
BOX_TESTS = [
    "led_comms_test",
    "voltage_accuracy_test",
    "current_accuracy_test",
    "charging_check_test",
]

TEST_PASS_RATES = {
    "intercom_test":         0.99,
    "buzzer_function_test":  0.97,
    "relay_function_test":   0.98,
    "test_cp_sense":         0.93,
    "test_cp_pwm":           0.98,
    "led_comms_test":        0.95,
    "voltage_accuracy_test": 0.99,
    "current_accuracy_test": 0.97,
    "charging_check_test":   0.96,
}

TEST_DURATIONS = {
    "intercom_test":         (4,   1),
    "buzzer_function_test":  (8,   2),
    "relay_function_test":   (12,  3),
    "test_cp_sense":         (20,  4),
    "test_cp_pwm":           (35,  8),
    "led_comms_test":        (30,  5),
    "voltage_accuracy_test": (15,  3),
    "current_accuracy_test": (45, 10),
    "charging_check_test":   (75, 10),
}

# Per-fixture bias on value readings (simulates calibration offsets)
FIXTURE_BIAS = {
    "BOX-01":  0.00,
    "BOX-02": +0.18,
    "BOX-03": -0.22,
}

# Measurements per test: (metric_name, nominal_target, tolerance, unit)
MEASUREMENTS = {
    "voltage_accuracy_test": [
        ("voltage_rms_240v", 240.0, 0.5, "V"),
    ],
    "current_accuracy_test": [
        ("current_rms_40a",  40.0, 0.40, "A"),
        ("current_rms_6a",    6.0, 0.06, "A"),
    ],
    "buzzer_function_test": [
        ("dominant_freq_hz", 2500.0, 250.0, "Hz"),
    ],
    "test_cp_pwm": [
        ("duty_cycle_6a",   10.0, 0.5, "%"),
        ("duty_cycle_32a",  53.3, 0.5, "%"),
        ("duty_cycle_48a",  80.0, 0.5, "%"),
    ],
}


# ── Data generation ──────────────────────────────────────────────────────────

def _make_measurements(test_name, passed, fixture_id):
    specs = MEASUREMENTS.get(test_name, [])
    bias_pct = FIXTURE_BIAS.get(fixture_id, 0.0)
    out = []
    for metric, target, tol, unit in specs:
        nominal = round(target + random.gauss(0, tol * 0.05), 4)
        bias    = target * bias_pct / 100.0
        if passed:
            value = round(nominal + random.gauss(bias, tol * 0.25), 4)
        else:
            value = round(target + tol * random.uniform(1.2, 2.5) * random.choice([-1, 1]), 4)
        out.append({
            "metric":        metric,
            "value":         value,
            "nominal":       nominal,
            "unit":          unit,
            "tolerance_min": round(nominal - tol, 4),
            "tolerance_max": round(nominal + tol, 4),
            "passed":        abs(value - nominal) <= tol,
        })
    return out


def make_run(product="C1", fixture_id="BOX-01", phase="fct", fail_rate=0.05):
    tests    = FCT_TESTS if phase == "fct" else BOX_TESTS
    now      = datetime.now(timezone.utc)
    elapsed  = 0.0
    results  = []
    overall  = True
    first_fail = None

    for test_name in tests:
        mean, std = TEST_DURATIONS.get(test_name, (10, 2))
        dur    = max(1.0, random.gauss(mean, std))
        # Apply global fail_rate as a floor — if uniform < fail_rate, force a failure
        base_pass = TEST_PASS_RATES.get(test_name, 0.95)
        effective  = max(0.0, base_pass - fail_rate)
        passed     = random.random() < effective

        t_start = now.replace(microsecond=0)  # simplified; real harness would track this
        results.append({
            "test_name":      test_name,
            "started_at":     (now.isoformat()),
            "ended_at":       (now.isoformat()),
            "duration_s":     round(dur, 2),
            "passed":         passed,
            "failure_reason": None if passed else f"{test_name} threshold exceeded",
            "measurements":   _make_measurements(test_name, passed, fixture_id),
        })

        if not passed and overall:
            overall    = False
            first_fail = test_name
        elapsed += dur

    serial = f"SN{random.randint(100000, 999999)}"
    started_at = now.isoformat()
    ended_at   = now.isoformat()  # feeder uses current time; real harness tracks wall time

    return {
        "serial_number":  serial,
        "product":        product,
        "fixture_id":     fixture_id,
        "phase":          phase,
        "started_at":     started_at,
        "ended_at":       ended_at,
        "duration_s":     round(elapsed, 2),
        "overall_pass":   overall,
        "failure_reason": first_fail,
        "results":        results,
    }


# ── MQTT publish ─────────────────────────────────────────────────────────────

def publish_run(client, run: dict):
    topic   = f"{TOPIC_ROOT}/{run['fixture_id']}"
    payload = json.dumps(run)
    result  = client.publish(topic, payload, qos=1)
    result.wait_for_publish()

    status = "PASS" if run["overall_pass"] else f"FAIL ({run['failure_reason']})"
    print(f"  → {topic}  {run['serial_number']}  {run['product']}  "
          f"{run['duration_s']:.0f}s  {status}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Publish synthetic test runs to MQTT")
    ap.add_argument("--host",       default=MQTT_HOST,  help="MQTT broker host")
    ap.add_argument("--port",       default=MQTT_PORT,  type=int)
    ap.add_argument("--fixture",    default=None,       help="Fixture ID (default: random)")
    ap.add_argument("--product",    default=None,       help="C1 or C2 (default: random)")
    ap.add_argument("--phase",      default=None,       choices=["fct", "box"],
                    help="Test phase (default: random)")
    ap.add_argument("--count",      default=1,          type=int,
                    help="Number of runs to publish (default: 1)")
    ap.add_argument("--continuous", action="store_true",
                    help="Run forever (ignores --count)")
    ap.add_argument("--delay",      default=5.0,        type=float,
                    help="Seconds between runs in continuous/multi mode (default: 5)")
    ap.add_argument("--fail-rate",  default=0.05,       type=float,
                    help="Extra failure probability 0-1 (default: 0.05)")
    args = ap.parse_args()

    client = mqtt.Client()
    client.connect(args.host, args.port, keepalive=60)
    client.loop_start()

    print(f"Connected to {args.host}:{args.port}  topic root: {TOPIC_ROOT}")
    print(f"Publishing {'continuously' if args.continuous else args.count} run(s)"
          f"  delay={args.delay}s  fail_rate={args.fail_rate}\n")

    i = 0
    try:
        while True:
            fixture = args.fixture or random.choice(FIXTURES)
            product = args.product or random.choices(PRODUCTS, weights=[0.7, 0.3])[0]
            phase   = args.phase   or random.choice(["fct", "box"])

            run = make_run(
                product=product,
                fixture_id=fixture,
                phase=phase,
                fail_rate=args.fail_rate,
            )
            publish_run(client, run)
            i += 1

            if not args.continuous and i >= args.count:
                break
            time.sleep(args.delay)

    except KeyboardInterrupt:
        print("\nStopped.")

    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
