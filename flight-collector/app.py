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

If FR24_API_KEY is set, Flightradar24 is used as a fallback source for
polls where airplanes.live's crowdsourced coverage sees nothing in range,
throttled to at most one FR24 call per FR24_FALLBACK_MIN_INTERVAL_SECONDS
so credit usage stays bounded regardless of coverage gaps. FR24 already
includes route data, so sightings sourced from it skip the adsbdb lookup.
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

FR24_API_KEY = os.environ.get("FR24_API_KEY", "")
FR24_FALLBACK_MIN_INTERVAL_SECONDS = float(os.environ.get("FR24_FALLBACK_MIN_INTERVAL_SECONDS", "60"))

AIRPLANES_LIVE_URL = "https://api.airplanes.live/v2/point/{lat}/{lon}/{radius}"
FR24_URL = "https://fr24api.flightradar24.com/api/live/flight-positions/full"
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
    source TEXT NOT NULL DEFAULT 'airplanes.live',
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
    flight_iata TEXT,
    fetched_utc TEXT NOT NULL
)
""")
db.commit()

# hex -> {"row_id": int, "last_seen": monotonic float}
active = {}
last_fr24_call_mono = 0.0


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
        "SELECT orig_iata, dest_iata, flight_iata, fetched_utc FROM routes WHERE callsign = ?",
        (callsign,),
    ).fetchone()
    if not row:
        return None
    orig_iata, dest_iata, flight_iata, fetched_utc = row
    fetched = datetime.fromisoformat(fetched_utc)
    if (datetime.now(timezone.utc) - fetched).total_seconds() > ROUTE_CACHE_MAX_AGE_SECONDS:
        return None
    return {"orig_iata": orig_iata or "", "dest_iata": dest_iata or "", "flight_iata": flight_iata or ""}


async def lookup_route(client, callsign):
    """Look up route + IATA flight number for an ICAO callsign via adsbdb.

    airplanes.live reports callsigns in ICAO format (e.g. "DAL123"), but the
    Tronbyt app's logo lookup and airline stats expect IATA format (e.g.
    "DL123") to match FR24's convention. adsbdb's callsign_iata field
    supplies that conversion alongside the route.
    """
    cached = cached_route(callsign)
    if cached is not None:
        return cached

    orig_iata = ""
    dest_iata = ""
    flight_iata = ""
    try:
        resp = await client.get(
            ADSBDB_CALLSIGN_URL.format(callsign=callsign),
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code == 200:
            route = (resp.json().get("response") or {}).get("flightroute") or {}
            orig_iata = ((route.get("origin") or {}).get("iata_code")) or ""
            dest_iata = ((route.get("destination") or {}).get("iata_code")) or ""
            flight_iata = route.get("callsign_iata") or ""
    except Exception as exc:
        print(f"adsbdb lookup failed for {callsign}: {exc}")

    db.execute(
        """INSERT INTO routes (callsign, orig_iata, dest_iata, flight_iata, fetched_utc)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(callsign) DO UPDATE SET
             orig_iata = excluded.orig_iata,
             dest_iata = excluded.dest_iata,
             flight_iata = excluded.flight_iata,
             fetched_utc = excluded.fetched_utc""",
        (callsign, orig_iata, dest_iata, flight_iata, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    return {"orig_iata": orig_iata, "dest_iata": dest_iata, "flight_iata": flight_iata}


def normalize_airplaneslive(ac):
    hex_id = ac.get("hex")
    lat = ac.get("lat")
    lon = ac.get("lon")
    if not hex_id or lat is None or lon is None:
        return None
    alt_raw = ac.get("alt_baro")
    return {
        "hex": hex_id,
        "lat": lat,
        "lon": lon,
        "flight": (ac.get("flight") or "").strip(),
        "alt_ft": 0 if alt_raw in (None, "ground") else int(alt_raw),
        "gspeed": int(ac.get("gs") or 0),
        "aircraft_type": ac.get("t") or "???",
        "orig_iata": "",
        "dest_iata": "",
    }


def normalize_fr24(ac):
    hex_id = ac.get("hex")
    lat = ac.get("lat")
    lon = ac.get("lon")
    if not hex_id or lat is None or lon is None:
        return None
    return {
        "hex": hex_id,
        "lat": lat,
        "lon": lon,
        "flight": (ac.get("flight") or ac.get("callsign") or "").strip(),
        "alt_ft": int(ac.get("alt") or 0),
        "gspeed": int(ac.get("gspeed") or 0),
        "aircraft_type": ac.get("type") or "???",
        "orig_iata": ac.get("orig_iata") or "",
        "dest_iata": ac.get("dest_iata") or "",
    }


async def fetch_fr24(client):
    d_lat = RADIUS_NM * 1.852 / 111.0
    d_lon = RADIUS_NM * 1.852 / 85.0
    bounds = "%f,%f,%f,%f" % (LAT + d_lat, LAT - d_lat, LON - d_lon, LON + d_lon)
    resp = await client.get(
        FR24_URL,
        params={"bounds": bounds, "limit": 50},
        headers={
            "Authorization": "Bearer %s" % FR24_API_KEY,
            "Accept-Version": "v1",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    resp.raise_for_status()
    return resp.json().get("data") or []


async def poll_once(client):
    global last_fr24_call_mono

    url = AIRPLANES_LIVE_URL.format(lat=LAT, lon=LON, radius=RADIUS_NM)
    resp = await client.get(url, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    raw_aircraft = resp.json().get("ac") or []
    aircraft = [a for a in (normalize_airplaneslive(x) for x in raw_aircraft) if a]
    source = "airplanes.live"

    now_mono = time.monotonic()
    fallback_due = now_mono - last_fr24_call_mono >= FR24_FALLBACK_MIN_INTERVAL_SECONDS
    if not aircraft and FR24_API_KEY and fallback_due:
        last_fr24_call_mono = now_mono
        try:
            raw_fr24 = await fetch_fr24(client)
            fr24_aircraft = [a for a in (normalize_fr24(x) for x in raw_fr24) if a]
            if fr24_aircraft:
                aircraft = fr24_aircraft
                source = "fr24"
        except Exception as exc:
            print(f"fr24 fallback error: {exc}")

    now_utc = datetime.now(timezone.utc).isoformat()
    today = local_date()
    seen_hexes = set()

    for ac in aircraft:
        hex_id = ac["hex"]
        seen_hexes.add(hex_id)

        if hex_id in active:
            active[hex_id]["last_seen"] = now_mono
            db.execute(
                "UPDATE sightings SET last_seen_utc = ?, alt_ft = ?, gspeed = ?, lat = ?, lon = ? WHERE id = ?",
                (now_utc, ac["alt_ft"], ac["gspeed"], ac["lat"], ac["lon"], active[hex_id]["row_id"]),
            )
            db.commit()
        else:
            orig_iata = ac["orig_iata"]
            dest_iata = ac["dest_iata"]
            flight = ac["flight"]
            if not orig_iata and not dest_iata and flight:
                route = await lookup_route(client, flight)
                orig_iata, dest_iata = route["orig_iata"], route["dest_iata"]
                flight = route["flight_iata"] or flight

            cur = db.execute(
                """INSERT INTO sightings
                   (hex, flight, airline_iata, aircraft_type, orig_iata, dest_iata,
                    alt_ft, gspeed, lat, lon, source, first_seen_utc, last_seen_utc, local_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    hex_id, flight, iata_from_flight(flight), ac["aircraft_type"],
                    orig_iata, dest_iata, ac["alt_ft"], ac["gspeed"], ac["lat"], ac["lon"],
                    source, now_utc, now_utc, today,
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
    return {
        "ok": True,
        "active_aircraft": len(active),
        "fr24_fallback_enabled": bool(FR24_API_KEY),
    }
