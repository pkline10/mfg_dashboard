#!/usr/bin/env python3
"""
MQTT → Dashboard ingest bridge.

Subscribes to mfg/results/# and on each message:
  1. POSTs to /api/ingest on the mfg dashboard
  2. POSTs a test_pass / test_fail event to the device tracker

Runs as a long-lived service (see mqtt_bridge.service).

Environment variables (all optional):
  MQTT_HOST            MQTT broker host            (default: localhost)
  MQTT_PORT            MQTT broker port            (default: 1883)
  MQTT_TOPIC_ROOT      Root topic to subscribe     (default: mfg/results)
  INGEST_URL           Dashboard ingest URL        (default: http://localhost:5001/api/ingest)
  DEVICE_TRACKER_URL   Device tracker base URL     (default: http://localhost:5000)
"""
import json
import logging
import os
import time
import urllib.request
import urllib.error

import paho.mqtt.client as mqtt

MQTT_HOST          = os.environ.get("MQTT_HOST",          "localhost")
MQTT_PORT          = int(os.environ.get("MQTT_PORT",       "1883"))
TOPIC_ROOT         = os.environ.get("MQTT_TOPIC_ROOT",    "mfg/results")
INGEST_URL         = os.environ.get("INGEST_URL",         "http://localhost:5001/api/ingest")
DEVICE_TRACKER_URL = os.environ.get("DEVICE_TRACKER_URL", "http://localhost:5000")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mqtt_bridge")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _post_json(url: str, payload: dict, timeout: int = 10):
    """POST JSON, return (status_code, response_dict). Raises on network error."""
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read())


# ── Step 1: mfg dashboard ingest ─────────────────────────────────────────────

def post_ingest(payload: dict, topic: str) -> int | None:
    """
    POST run to /api/ingest.
    Returns the new run_id on success, None on failure.
    """
    try:
        status, data = _post_json(INGEST_URL, payload)
        run_id = data.get("run_id")
        log.info(
            "DASHBOARD  run_id=%-5s  sn=%-14s  fixture=%-8s  product=%s  pass=%s",
            run_id,
            payload.get("serial_number", "?"),
            payload.get("fixture_id", "?"),
            payload.get("product", "?"),
            payload.get("overall_pass"),
        )
        return run_id
    except urllib.error.HTTPError as exc:
        log.error("Dashboard ingest HTTP %s: %s", exc.code, exc.read().decode(errors="replace"))
    except urllib.error.URLError as exc:
        log.error("Dashboard ingest unreachable: %s", exc.reason)
    except Exception as exc:
        log.error("Dashboard ingest error: %s", exc)
    return None


# ── Step 2: device tracker event ─────────────────────────────────────────────

def post_device_event(payload: dict, run_id: int | None):
    """
    Record a test_pass or test_fail event on the device tracker.
    Silently skips if the serial number isn't registered (404).
    """
    sn       = payload.get("serial_number", "")
    passed   = payload.get("overall_pass", False)
    fixture  = payload.get("fixture_id", "")
    phase    = payload.get("phase", "")
    product  = payload.get("product", "")
    duration = payload.get("duration_s")
    failure  = payload.get("failure_reason")

    category = "test_pass" if passed else "test_fail"

    phase_label = phase.upper() if phase else "MFG"
    if passed:
        summary = f"{phase_label} test PASS — {fixture}" + (f" — {duration:.0f}s" if duration else "")
    else:
        summary = f"{phase_label} test FAIL — {fixture} — {failure or 'unknown'}"

    metadata = {
        "product":    product,
        "phase":      phase,
        "fixture_id": fixture,
        "duration_s": duration,
    }
    if run_id is not None:
        metadata["mfg_run_id"] = run_id
    if not passed and failure:
        metadata["failure_reason"] = failure

    event_payload = {
        "category": category,
        "summary":  summary,
        "station":  fixture,
        "metadata": metadata,
    }

    url = f"{DEVICE_TRACKER_URL}/api/device/{sn}/events"
    try:
        status, data = _post_json(url, event_payload)
        log.info(
            "TRACKER    event_id=%-5s  sn=%-14s  category=%s",
            data.get("id", "?"), sn, category,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            log.warning("TRACKER    sn=%s not registered in device tracker — skipping event", sn)
        else:
            log.error("Tracker event HTTP %s for sn=%s: %s", exc.code, sn,
                      exc.read().decode(errors="replace"))
    except urllib.error.URLError as exc:
        log.error("Tracker unreachable: %s", exc.reason)
    except Exception as exc:
        log.error("Tracker event error for sn=%s: %s", sn, exc)


# ── MQTT callbacks ────────────────────────────────────────────────────────────

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("Connected to broker %s:%s", MQTT_HOST, MQTT_PORT)
        topic = f"{TOPIC_ROOT}/#"
        client.subscribe(topic, qos=1)
        log.info("Subscribed to %s", topic)
    else:
        log.error("MQTT connect failed rc=%s", rc)


def on_disconnect(client, userdata, rc):
    if rc != 0:
        log.warning("Unexpected disconnect rc=%s — will reconnect", rc)


def on_message(client, userdata, msg):
    log.debug("RX %s (%d bytes)", msg.topic, len(msg.payload))
    try:
        payload = json.loads(msg.payload.decode())
    except json.JSONDecodeError as exc:
        log.warning("Bad JSON on %s: %s", msg.topic, exc)
        return
    if not isinstance(payload, dict):
        log.warning("Unexpected payload type on %s: %s", msg.topic, type(payload))
        return

    run_id = post_ingest(payload, msg.topic)
    post_device_event(payload, run_id)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    log.info(
        "mqtt_bridge starting  broker=%s:%s  topic=%s/#\n"
        "  ingest=%s\n  tracker=%s",
        MQTT_HOST, MQTT_PORT, TOPIC_ROOT, INGEST_URL, DEVICE_TRACKER_URL,
    )

    client = mqtt.Client()
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message

    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            client.loop_forever()
        except ConnectionRefusedError:
            log.error("Broker refused connection — retrying in 10s")
            time.sleep(10)
        except OSError as exc:
            log.error("Network error: %s — retrying in 10s", exc)
            time.sleep(10)
        except KeyboardInterrupt:
            log.info("Shutting down")
            break


if __name__ == "__main__":
    main()
