import json
import logging
import math
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# ============================================================
# CONFIG
# ============================================================
MQTT_HOST = os.getenv("MQTT_HOST", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "spoonsteady/telemetry")
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "spoonsteady-backend-reader")
DB_PATH = os.getenv("DB_PATH", "spoonsteady.db")

# Prototype thresholds only; not clinically validated.
CALM_LIMIT = float(os.getenv("CALM_LIMIT", "0.8"))
MILD_LIMIT = float(os.getenv("MILD_LIMIT", "2.0"))
MAX_RMS = float(os.getenv("MAX_RMS", "100"))
MAX_PITCH = float(os.getenv("MAX_PITCH", "180"))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("spoonsteady")

db_lock = threading.Lock()
state_lock = threading.Lock()
mqtt_client = None

state = {
    "total_scoops": 0,
    "is_in_scoop_motion": False,
    "last_data": None,
    "received_messages": 0,
    "invalid_messages": 0,
}


# ============================================================
# SQLITE
# ============================================================
def connect_db():
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        with connect_db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS sensor_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    rms REAL NOT NULL,
                    pitch REAL NOT NULL,
                    stability INTEGER NOT NULL,
                    scoop_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    source_topic TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            row = db.execute(
                "SELECT value FROM app_state WHERE key = 'total_scoops'"
            ).fetchone()
            if row:
                state["total_scoops"] = int(row["value"])
            else:
                latest = db.execute(
                    "SELECT scoop_count FROM sensor_logs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if latest:
                    state["total_scoops"] = int(latest["scoop_count"])
            db.execute(
                "INSERT OR REPLACE INTO app_state(key, value) VALUES('total_scoops', ?)",
                (str(state["total_scoops"]),),
            )


def save_result(result, topic):
    with db_lock:
        with connect_db() as db:
            db.execute("""
                INSERT INTO sensor_logs
                (timestamp, rms, pitch, stability, scoop_count, status, source_topic)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                result["timestamp"],
                result["rms"],
                result["pitch"],
                result["stability"],
                result["scoop_count"],
                result["status"],
                topic,
            ))
            db.execute(
                "INSERT OR REPLACE INTO app_state(key, value) VALUES('total_scoops', ?)",
                (str(result["scoop_count"]),),
            )


# ============================================================
# DATA PROCESSING
# ============================================================
def process_sensor_data(payload):
    """Validate and process the existing frontend's {rms, pitch} payload."""
    if not isinstance(payload, dict):
        raise ValueError("Payload JSON must be an object")

    try:
        rms = float(payload["rms"])
        pitch = float(payload["pitch"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Payload must contain numeric 'rms' and 'pitch'") from exc

    if not math.isfinite(rms) or not math.isfinite(pitch):
        raise ValueError("Sensor values must be finite numbers")
    if not 0 <= rms <= MAX_RMS:
        raise ValueError(f"RMS must be between 0 and {MAX_RMS}")
    if not -MAX_PITCH <= pitch <= MAX_PITCH:
        raise ValueError(f"Pitch must be between {-MAX_PITCH} and {MAX_PITCH}")

    stability = max(10, min(100, round(100 - rms * 28)))

    if rms < CALM_LIMIT:
        status = "calm"
    elif rms < MILD_LIMIT:
        status = "mild"
    else:
        status = "tremor"

    # Preserve the prototype's pitch-crossing scoop heuristic.
    with state_lock:
        if pitch > 40 and not state["is_in_scoop_motion"]:
            state["is_in_scoop_motion"] = True
        elif pitch < 20 and state["is_in_scoop_motion"]:
            state["is_in_scoop_motion"] = False
            state["total_scoops"] += 1

        scoop_count = state["total_scoops"]

    return {
        "timestamp": time.time(),
        "rms": round(rms, 4),
        "pitch": round(pitch, 2),
        "stability": stability,
        "scoop_count": scoop_count,
        "status": status,
    }


# ============================================================
# MQTT CALLBACKS
# ============================================================
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        logger.info("Connected to MQTT broker %s:%s", MQTT_HOST, MQTT_PORT)
        client.subscribe(MQTT_TOPIC, qos=0)
        logger.info("Subscribed to topic: %s", MQTT_TOPIC)
    else:
        logger.error("MQTT connection failed: %s", reason_code)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    logger.warning("MQTT disconnected (reason=%s); client will reconnect", reason_code)


def on_message(client, userdata, message):
    try:
        payload = json.loads(message.payload.decode("utf-8"))
        result = process_sensor_data(payload)
        save_result(result, message.topic)

        with state_lock:
            state["last_data"] = result
            state["received_messages"] += 1

        logger.info(
            "Saved telemetry: rms=%s pitch=%s status=%s scoops=%s",
            result["rms"], result["pitch"], result["status"], result["scoop_count"],
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, sqlite3.Error) as exc:
        with state_lock:
            state["invalid_messages"] += 1
        logger.warning("Ignored invalid MQTT message: %s", exc)
    except Exception:
        with state_lock:
            state["invalid_messages"] += 1
        logger.exception("Unexpected error while processing MQTT message")


def start_mqtt():
    global mqtt_client
    mqtt_client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=MQTT_CLIENT_ID,
        protocol=mqtt.MQTTv311,
    )
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
    mqtt_client.loop_start()


def stop_mqtt():
    global mqtt_client
    if mqtt_client is not None:
        mqtt_client.loop_stop()
        try:
            mqtt_client.disconnect()
        except Exception:
            logger.exception("Error while disconnecting MQTT client")


# ============================================================
# FASTAPI
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_mqtt()
    yield
    stop_mqtt()


app = FastAPI(
    title="SpoonSteady Backend",
    description="Independent MQTT telemetry processor and SQLite history API.",
    version="1.0.0",
    lifespan=lifespan,
)

# API is currently read-only. Restrict origins if a browser frontend later calls it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {
        "project": "SpoonSteady",
        "backend": "online",
        "mqtt_topic": MQTT_TOPIC,
        "database": "sqlite",
    }


@app.get("/health")
def health():
    with state_lock:
        received = state["received_messages"]
        invalid = state["invalid_messages"]
    return {
        "status": "ok",
        "mqtt_host": MQTT_HOST,
        "mqtt_topic": MQTT_TOPIC,
        "received_messages": received,
        "invalid_messages": invalid,
    }


@app.get("/api/latest")
def latest():
    with state_lock:
        current = state["last_data"]
    if current is not None:
        return current

    with db_lock:
        with connect_db() as db:
            row = db.execute(
                "SELECT * FROM sensor_logs ORDER BY id DESC LIMIT 1"
            ).fetchone()

    if row is None:
        return {"message": "No sensor data received yet"}
    return dict(row)


@app.get("/api/history")
def history(limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0)):
    with db_lock:
        with connect_db() as db:
            rows = db.execute("""
                SELECT * FROM sensor_logs
                ORDER BY id DESC
                LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/stats")
def stats():
    with db_lock:
        with connect_db() as db:
            row = db.execute("""
                SELECT
                    COUNT(*) AS total_records,
                    AVG(rms) AS average_rms,
                    AVG(stability) AS average_stability,
                    MAX(rms) AS maximum_rms,
                    MAX(scoop_count) AS latest_scoop_count
                FROM sensor_logs
            """).fetchone()

    data = dict(row)
    for key in ("average_rms", "average_stability", "maximum_rms"):
        if data[key] is not None:
            data[key] = round(data[key], 3)
    return data
