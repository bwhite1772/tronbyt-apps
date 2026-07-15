"""
Overhead-flights collector + read API.

Polls airplanes.live on a timer, logs each aircraft pass ("sighting") to
SQLite, and exposes two read endpoints for the overhead_flights.star
Tronbyt app:

  GET /current      -> the aircraft currently overhead (or {"flight": None})
  GET /stats/today   -> milestone + summary stats for today's sightings

Route (origin/destination) data isn't part of ADS-B position feeds, so new
sightings are enriched with a one-time lookup against adsbdb.com, cached in
the `routes` table since a given callsign's route rarely changes.
"""

import asyncio
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI

LAT = float(os.environ["LAT"])
LON = float(os.environ["LON"])
RADIUS_NM = float(os.environ.get("RADIUS_NM", "15"))
POLL_INTERVAL_SECONDS = float(os.environ.get("POLL_INTERVAL_SECONDS", "15"))
STALE_AFTER_SECONDS = float(os.environ.get("STALE_AFTER_SECONDS", "90"))
ROUTE_CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600
DB_PATH = os.environ.get("DB_PATH", "/data/flights.db")

AIRPLANES_LIVE_URL = "https://api.airplanes.live/v2/point/{lat}/{lon}/{radius}"
ADSBDB_CALLSIGN_URL = "https://api.adsbdb.com/v0/callsign/{callsign}"
USER_AGENT = "overhead-flights-collector/1.0"

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("PRAGMA journal_mode=WAL")
db.execute("""
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hex TEXT NOT NULL,
    flight TEXT,
    airline_iata TEXT,
    aircraft_type TEXT,
    orig_iata TEXT,
    dest_iata TEXT,
    alt_ft INTEGER,
    gspeed INTEGER,
    lat REAL,
    lon REAL,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL,
    local_date TEXT NOT NULL
)
""")
db.execute("CREATE INDEX IF NOT EXISTS idx_sightings_date ON sightings(local_date)")
db.execute("CREATE INDEX IF NOT EXISTS idx_sightings_date_type ON sightings(local_date, aircraft_type)")
db.execute("""
CREATE TABLE IF NOT EXISTS routes (
    callsign TEXT PRIMARY KEY,
    orig_iata TEXT,
    dest_iata TEXT,
    fetched_utc TEXT NOT NULL
)
""")
db.commit()

# hex -> {"row_id": int, "last_seen": monotonic float}
active = {}


def local_date():
    return datetime.now().astimezone().date().isoformat()


def iata_from_flight(flight_num):
    code = ""
    for c in flight_num or "":
        if c.isdigit():
            break
        code += c
    return code


def cached_route(callsign):
    row = db.execute(
        "SELECT orig_iata, dest_iata, fetched_utc FROM routes WHERE callsign = ?",
        (callsign,),
    ).fetchone()
    if not row:
        return None
    orig_iata, dest_iata, fetched_utc = row
    fetched = datetime.fromisoformat(fetched_utc)
    if (datetime.now(timezone.utc) - fetched).total_seconds() > ROUTE_CACHE_MAX_AGE_SECONDS:
        return None
    return {"orig_iata": orig_iata or "", "dest_iata": dest_iata or ""}


async def lookup_route(client, callsign):
    cached = cached_route(callsign)
    if cached is not None:
        return cached

    orig_iata = ""
    dest_iata = ""
    try:
        resp = await client.get(
            ADSBDB_CALLSIGN_URL.format(callsign=callsign),
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code == 200:
            route = (resp.json().get("response") or {}).get("flightroute") or {}
            orig_iata = ((route.get("origin") or {}).get("iata_code")) or ""
            dest_iata = ((route.get("destination") or {}).get("iata_code")) or ""
    except Exception as exc:
        print(f"adsbdb lookup failed for {callsign}: {exc}")

    db.execute(
        """INSERT INTO routes (callsign, orig_iata, dest_iata, fetched_utc)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(callsign) DO UPDATE SET
             orig_iata = excluded.orig_iata,
             dest_iata = excluded.dest_iata,
             fetched_utc = excluded.fetched_utc""",
        (callsign, orig_iata, dest_iata, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    return {"orig_iata": orig_iata, "dest_iata": dest_iata}


async def poll_once(client):
    url = AIRPLANES_LIVE_URL.format(lat=LAT, lon=LON, radius=RADIUS_NM)
    resp = await client.get(url, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    aircraft = resp.json().get("ac") or []

    now_mono = time.monotonic()
    now_utc = datetime.now(timezone.utc).isoformat()
    today = local_date()
    seen_hexes = set()

    for ac in aircraft:
        hex_id = ac.get("hex")
        lat = ac.get("lat")
        lon = ac.get("lon")
        if not hex_id or lat is None or lon is None:
            continue
        seen_hexes.add(hex_id)

        flight = (ac.get("flight") or "").strip()
        alt_raw = ac.get("alt_baro")
        alt_ft = 0 if alt_raw in (None, "ground") else int(alt_raw)
        gspeed = int(ac.get("gs") or 0)
        aircraft_type = ac.get("t") or "???"

        if hex_id in active:
            active[hex_id]["last_seen"] = now_mono
            db.execute(
                "UPDATE sightings SET last_seen_utc = ?, alt_ft = ?, gspeed = ?, lat = ?, lon = ? WHERE id = ?",
                (now_utc, alt_ft, gspeed, lat, lon, active[hex_id]["row_id"]),
            )
            db.commit()
        else:
            route = await lookup_route(client, flight) if flight else {"orig_iata": "", "dest_iata": ""}
            cur = db.execute(
                """INSERT INTO sightings
                   (hex, flight, airline_iata, aircraft_type, orig_iata, dest_iata,
                    alt_ft, gspeed, lat, lon, first_seen_utc, last_seen_utc, local_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    hex_id, flight, iata_from_flight(flight), aircraft_type,
                    route["orig_iata"], route["dest_iata"],
                    alt_ft, gspeed, lat, lon, now_utc, now_utc, today,
                ),
            )
            db.commit()
            active[hex_id] = {"row_id": cur.lastrowid, "last_seen": now_mono}

    for hex_id in list(active.keys()):
        if hex_id not in seen_hexes and now_mono - active[hex_id]["last_seen"] > STALE_AFTER_SECONDS:
            del active[hex_id]


async def poll_loop():
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                await poll_once(client)
            except Exception as exc:
                print(f"poll error: {exc}")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/current")
def current():
    best_row = None
    best_dist = None

    for hex_id, entry in active.items():
        row = db.execute(
            """SELECT flight, aircraft_type, orig_iata, dest_iata, alt_ft, gspeed, lat, lon
               FROM sightings WHERE id = ?""",
            (entry["row_id"],),
        ).fetchone()
        if not row:
            continue
        dist = (row[6] - LAT) ** 2 + (row[7] - LON) ** 2
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_row = row

    if not best_row:
        return {"flight": None}

    flight, aircraft_type, orig_iata, dest_iata, alt_ft, gspeed, _, _ = best_row
    return {
        "flight": {
            "flight": flight or "???",
            "type": aircraft_type or "???",
            "orig_iata": orig_iata or "",
            "dest_iata": dest_iata or "",
            "alt": alt_ft or 0,
            "gspeed": gspeed or 0,
        }
    }


@app.get("/stats/today")
def stats_today():
    today = local_date()

    total = db.execute(
        "SELECT COUNT(*) FROM sightings WHERE local_date = ?", (today,)
    ).fetchone()[0]

    top_airlines = db.execute(
        """SELECT airline_iata, COUNT(*) c FROM sightings
           WHERE local_date = ? AND airline_iata != ''
           GROUP BY airline_iata ORDER BY c DESC LIMIT 5""",
        (today,),
    ).fetchall()

    latest = db.execute(
        """SELECT aircraft_type, first_seen_utc FROM sightings
           WHERE local_date = ? ORDER BY first_seen_utc DESC LIMIT 1""",
        (today,),
    ).fetchone()

    milestone = None
    if latest:
        aircraft_type, first_seen = latest
        count = db.execute(
            """SELECT COUNT(*) FROM sightings
               WHERE local_date = ? AND aircraft_type = ? AND first_seen_utc <= ?""",
            (today, aircraft_type, first_seen),
        ).fetchone()[0]
        milestone = {"type": aircraft_type, "count": count}

    return {
        "date": today,
        "total": total,
        "top_airlines": [{"iata": a, "count": c} for a, c in top_airlines],
        "milestone": milestone,
    }


@app.get("/health")
def health():
    return {"ok": True, "active_aircraft": len(active)}
