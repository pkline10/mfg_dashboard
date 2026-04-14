#!/usr/bin/env python3
"""
MQTT → Dashboard ingest bridge.

Subscribes to mfg/results/# and POSTs each message to /api/ingest.
Runs as a long-lived service (see mqtt_bridge.service).

Environment variables (all optional):
  MQTT_HOST        MQTT broker host        (default: localhost)
  MQTT_PORT        MQTT broker port        (default: 1883)
  MQTT_TOPIC_ROOT  Root topic to subscribe (default: mfg/results)
  INGEST_URL       Dashboard ingest URL    (default: http://localhost:5001/api/ingest)
"""
import json
import logging
import os
import time
import urllib.request
import urllib.error

import paho.mqtt.client as mqtt

MQTT_HOST   = os.environ.get("MQTT_HOST",   "localhost")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", "1883"))
TOPIC_ROOT  = os.environ.get("MQTT_TOPIC_ROOT", "mfg/results")
INGEST_URL  = os.environ.get("INGEST_URL",  "http://localhost:5001/api/ingest")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mqtt_bridge")


def post_ingest(payload: dict, topic: str):
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        INGEST_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            log.info(
                "INGESTED  run_id=%-5s  sn=%-14s  fixture=%-8s  product=%s  pass=%s  topic=%s",
                data.get("run_id", "?"),
                payload.get("serial_number", "?"),
                payload.get("fixture_id", "?"),
                payload.get("product", "?"),
                payload.get("overall_pass"),
                topic,
            )
    except urllib.error.HTTPError as exc:
        log.error("Ingest HTTP %s: %s", exc.code, exc.read().decode(errors="replace"))
    except urllib.error.URLError as exc:
        log.error("Ingest unreachable: %s", exc.reason)
    except Exception as exc:
        log.error("Ingest error: %s", exc)


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
    post_ingest(payload, msg.topic)


def main():
    log.info("mqtt_bridge starting  broker=%s:%s  topic=%s/#  ingest=%s",
             MQTT_HOST, MQTT_PORT, TOPIC_ROOT, INGEST_URL)

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
