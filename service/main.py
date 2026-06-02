from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from pydantic import BaseModel
import sqlite3
import time
import os

DB_PATH           = os.getenv("DB_PATH", "/data/ardtemp.db")
SPIKE_THRESHOLD_C = float(os.getenv("SPIKE_THRESHOLD_C", "5.0"))
SPIKE_WINDOW_S    = int(os.getenv("SPIKE_WINDOW_S", "120"))

# In-memory state per board_id
_pending_cmds: dict[str, str] = {}
_spike_active: dict[str, bool] = {}


def _check_spike(con, board_id: str) -> bool:
    cutoff = int(time.time()) - SPIKE_WINDOW_S
    rows = con.execute(
        "SELECT t FROM readings WHERE board_id = ? AND ts > ?",
        (board_id, cutoff),
    ).fetchall()
    if len(rows) < 2:
        return False
    temps = [r[0] for r in rows]
    return (max(temps) - min(temps)) >= SPIKE_THRESHOLD_C


def _db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def _init_db():
    con = _db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            ts       INTEGER NOT NULL,
            board_id TEXT    NOT NULL,
            t        REAL    NOT NULL,
            h        REAL    NOT NULL
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_board_ts ON readings (board_id, ts)"
    )
    con.commit()
    con.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    yield


app = FastAPI(lifespan=lifespan)


class Reading(BaseModel):
    board_id: str
    t: float
    h: float


class Command(BaseModel):
    board_id: str
    cmd: str


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/reading")
def post_reading(r: Reading):
    ts = int(time.time())
    con = _db()
    con.execute(
        "INSERT INTO readings (ts, board_id, t, h) VALUES (?, ?, ?, ?)",
        (ts, r.board_id, r.t, r.h),
    )
    con.commit()

    spike = _check_spike(con, r.board_id)
    con.close()

    if spike and not _spike_active.get(r.board_id):
        # Spike just started — alert the board
        _spike_active[r.board_id] = True
        _pending_cmds[r.board_id] = 'F'
    elif not spike and _spike_active.get(r.board_id):
        # Spike cleared — dismiss (covers Linux-not-running case)
        _spike_active[r.board_id] = False
        _pending_cmds[r.board_id] = 'D'

    # A 'D' from the Linux indicator (POST /command) takes effect here too;
    # _spike_active stays True until the window clears so we won't re-send 'F'.
    cmd = _pending_cmds.pop(r.board_id, None)
    return {"cmd": cmd}


@app.get("/latest")
def get_latest(board_id: str):
    con = _db()
    row = con.execute(
        "SELECT ts, t, h FROM readings WHERE board_id = ? ORDER BY ts DESC LIMIT 1",
        (board_id,),
    ).fetchone()
    con.close()
    if not row:
        return {"error": "no data"}
    return {"ts": row[0], "t": row[1], "h": row[2]}


@app.get("/readings")
def get_readings(
    board_id: str,
    since: int = Query(default=0),
    limit: int = Query(default=1000, le=10000),
):
    con = _db()
    rows = con.execute(
        "SELECT ts, t, h FROM readings WHERE board_id = ? AND ts > ? ORDER BY ts ASC LIMIT ?",
        (board_id, since, limit),
    ).fetchall()
    con.close()
    return [{"ts": r[0], "t": r[1], "h": r[2]} for r in rows]


@app.post("/command")
def post_command(c: Command):
    _pending_cmds[c.board_id] = c.cmd
    return {"ok": True}
