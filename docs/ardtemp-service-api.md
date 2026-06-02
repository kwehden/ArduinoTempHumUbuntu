# ardtemp-service API — Integration Guide for Monitoring Tools

## Service location

| | |
|---|---|
| Host | `laminarflow` |
| Port | `30700` (K8s NodePort, plain HTTP) |
| Base URL | `http://laminarflow:30700` |

The service is a FastAPI app backed by SQLite, running in the `ardtemp` namespace on the cluster. It is always up regardless of whether the Linux desktop is running.

---

## Endpoints

### `GET /health`

Liveness check.

```
GET /health
→ 200 {"ok": true}
```

### `GET /latest`

Most recent reading for a board. Use this for a live "current conditions" display.

```
GET /latest?board_id=r4wifi
→ 200 {"ts": 1747478400, "t": 27.3, "h": 51.0}
   or {"error": "no data"}   ← board has never posted
```

| Field | Type | Description |
|---|---|---|
| `ts` | integer | Unix timestamp (seconds) when the reading was stored |
| `t` | float | Temperature in °C |
| `h` | float | Relative humidity % |

**Staleness:** the board posts every ~2 seconds. A reading older than 10 seconds means the board is offline or WiFi is down. Treat it as "no sensor."

**Recommended polling rate:** every 5–30 seconds. There is no push/websocket interface; polling is the intended model. 5 s gives a near-live feel; 30 s is fine for a dashboard that also shows history.

---

### `GET /readings`

Time-series query — returns a list of readings in ascending timestamp order.

```
GET /readings?board_id=r4wifi&since=1747474800&limit=500
→ 200 [
    {"ts": 1747474802, "t": 26.1, "h": 48.0},
    {"ts": 1747474804, "t": 26.2, "h": 48.1},
    ...
  ]
```

| Query param | Type | Default | Max | Description |
|---|---|---|---|---|
| `board_id` | string | required | — | Board identifier |
| `since` | integer | `0` | — | Return only rows with `ts > since` (Unix seconds) |
| `limit` | integer | `1000` | `10000` | Max rows returned |

**Typical use:** on initial load, fetch the last N hours of history. For incremental updates, track the highest `ts` seen and pass it as `since` on subsequent calls.

```python
# Initial load — last 2 hours
since = int(time.time()) - 7200
data = requests.get(
    "http://laminarflow:30700/readings",
    params={"board_id": "r4wifi", "since": since, "limit": 3600}
).json()

# Incremental — poll every 30 s, append new points
last_ts = data[-1]["ts"] if data else 0
new_points = requests.get(
    "http://laminarflow:30700/readings",
    params={"board_id": "r4wifi", "since": last_ts}
).json()
```

**Recommended polling rate:** 30–60 seconds for incremental updates. History does not change retroactively; only new rows appear.

---

### `GET /health`

```
GET /health → {"ok": true}
```

Poll before drawing UI to surface a "service unreachable" state cleanly.

---

## Board IDs

| `board_id` | Hardware |
|---|---|
| `r4wifi` | Arduino Uno R4 WiFi with Modulino Thermo (HS3003) |
| `uno-q` | Arduino Uno Q (legacy, serial-only — not currently posting to service) |

---

## Units and precision

- Temperature: **°C**, one decimal place (e.g. `27.3`)
- Humidity: **%RH**, one decimal place (e.g. `51.0`)
- Convert to °F client-side: `f = c * 9.0 / 5.0 + 32.0`

---

## Error handling

| Scenario | Behavior |
|---|---|
| Board offline / WiFi down | `/latest` still returns last stored reading; check `ts` for staleness |
| Board has never posted | `/latest` returns `{"error": "no data"}` |
| Service unreachable | HTTP connection refused / timeout — show degraded state |
| Empty history window | `/readings` returns `[]` |

---

## Commands (optional)

A monitoring tool can trigger hardware alerts on the board:

```
POST /command
Content-Type: application/json

{"board_id": "r4wifi", "cmd": "F"}   ← start LED matrix flash alert
{"board_id": "r4wifi", "cmd": "D"}   ← dismiss / stop flash
```

Commands are held in memory and delivered to the board on its next `POST /reading` (within ~2 seconds). Only one pending command per board is queued at a time; a new `POST /command` overwrites any undelivered prior command.

This is used by the GNOME panel indicator for spike alerts. A graphical tool could use it to trigger the board LED as a visual alarm from the UI.

---

## Example: minimal Python poller

```python
import time
import requests

BASE = "http://laminarflow:30700"
BOARD = "r4wifi"
STALE_S = 10

def poll():
    try:
        r = requests.get(f"{BASE}/latest", params={"board_id": BOARD}, timeout=5)
        data = r.json()
        if "error" in data:
            return None  # no data yet
        age = time.time() - data["ts"]
        if age > STALE_S:
            return None  # board offline
        return data  # {"ts": ..., "t": ..., "h": ...}
    except requests.RequestException:
        return None  # service unreachable
```
