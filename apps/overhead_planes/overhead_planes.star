load("render.star", "render")
load("http.star", "http")
load("schema.star", "schema")

ADSB_HOST = "adsbexchange-com1.p.rapidapi.com"
DEFAULT_LAT = "33.733695"
DEFAULT_LON = "-84.342482"
DEFAULT_RADIUS = "5"

GREEN = "#1DB954"
WHITE = "#FFFFFF"
GRAY = "#888888"
DIM = "#555555"

ICAO_TO_IATA = {
    "AAL": "AA",  "DAL": "DL",  "UAL": "UA",  "SWA": "WN",
    "ASA": "AS",  "JBU": "B6",  "FFT": "F9",  "NKS": "NK",
    "SKW": "OO",  "RPA": "YX",  "EDV": "9E",  "PDT": "9E",
    "FDX": "FX",  "UPS": "5X",  "BAW": "BA",  "DLH": "LH",
    "AFR": "AF",  "KLM": "KL",  "IBE": "IB",  "ACA": "AC",
    "WJA": "WS",  "QFA": "QF",  "SIA": "SQ",  "CPA": "CX",
    "JAL": "JL",  "ANA": "NH",  "KAL": "KE",  "UAE": "EK",
    "ETD": "EY",  "QTR": "QR",  "THY": "TK",  "AMX": "AM",
    "GTI": "GT",  "CLX": "CV",  "VOI": "Y4",  "ENY": "MQ",
}

def fmt_float(val, decimals):
    negative = val < 0
    val = -val if negative else val
    factor = 10 if decimals == 1 else 100
    rounded = int(val * factor + 0.5)
    whole = rounded // factor
    frac = rounded % factor
    frac_str = str(frac)
    if len(frac_str) < decimals:
        frac_str = "0" + frac_str
    if len(frac_str) < decimals:
        frac_str = "0" + frac_str
    result = "%d.%s" % (whole, frac_str)
    if negative:
        result = "-" + result
    return result

def heading_to_compass(degrees):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    ix = int((degrees + 22.5) / 45) % 8
    return dirs[ix]

def get_logo_widget(icao_prefix):
    iata = ICAO_TO_IATA.get(icao_prefix)
    if iata:
        url = "https://www.gstatic.com/flights/airline_logos/70px/%s.png" % iata
        rep = http.get(url, ttl_seconds = 86400)
        if rep.status_code == 200:
            return render.Image(src = rep.body(), width = 16, height = 16)

    abbrev = icao_prefix[:2] if len(icao_prefix) >= 2 else icao_prefix
    return render.Box(
        width = 16,
        height = 16,
        color = "#1A1A1A",
        child = render.Text(content = abbrev, color = DIM, font = "tom-thumb"),
    )

def main(config):
    api_key = config.get("api_key") or ""
    lat = config.get("latitude") or DEFAULT_LAT
    lon = config.get("longitude") or DEFAULT_LON
    radius = config.get("radius") or DEFAULT_RADIUS

    if not api_key:
        return error_screen("set API key")

    url = "https://%s/v2/lat/%s/lon/%s/dist/%s/" % (ADSB_HOST, lat, lon, radius)
    rep = http.get(
        url,
        headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": ADSB_HOST,
        },
        ttl_seconds = 30,
    )

    if rep.status_code != 200:
        return error_screen("HTTP %d" % rep.status_code)

    data = rep.json()
    aircraft_list = data.get("ac") or []

    airborne = []
    for ac in aircraft_list:
        alt = ac.get("alt_baro")
        if alt == None or alt == "ground":
            continue
        if alt <= 100:
            continue
        airborne.append(ac)

    if not airborne:
        return clear_skies_screen(radius)

    closest = airborne[0]
    min_dst = airborne[0].get("dst") or 9999
    for ac in airborne[1:]:
        dst = ac.get("dst") or 9999
        if dst < min_dst:
            min_dst = dst
            closest = ac

    return render_aircraft(closest)

def render_aircraft(ac):
    callsign = (ac.get("flight") or "").strip()
    if not callsign:
        callsign = ac.get("r") or "N/A"

    aircraft_type = ac.get("t") or "????"
    alt = ac.get("alt_baro") or 0
    speed = ac.get("gs") or 0
    track = ac.get("track") or 0
    dst = ac.get("dst") or 0

    orig = (ac.get("orig_iata") or "").strip()
    dest = (ac.get("dest_iata") or "").strip()
    if orig and dest:
        route_str = "%s -> %s" % (orig, dest)
    else:
        route_str = "hdg %s" % heading_to_compass(int(track))

    icao_prefix = callsign[:3].upper() if len(callsign) >= 3 else callsign.upper()
    logo = get_logo_widget(icao_prefix)

    return render.Root(
        child = render.Column(
            children = [
                render.Row(
                    children = [
                        logo,
                        render.Box(
                            width = 48,
                            child = render.Column(
                                children = [
                                    render.Row(
                                        expanded = True,
                                        main_align = "space_between",
                                        children = [
                                            render.Text(content = callsign, color = GREEN, font = "tb-8"),
                                            render.Text(content = aircraft_type, color = GRAY, font = "tb-8"),
                                        ],
                                    ),
                                    render.Marquee(
                                        width = 48,
                                        child = render.Text(content = route_str, color = WHITE, font = "tb-8"),
                                    ),
                                ],
                            ),
                        ),
                    ],
                ),
                render.Row(
                    expanded = True,
                    main_align = "space_between",
                    children = [
                        render.Text(content = "%dft" % int(alt), color = WHITE, font = "tb-8"),
                        render.Text(content = "%dkt" % int(speed), color = GRAY, font = "tb-8"),
                    ],
                ),
                render.Row(
                    expanded = True,
                    main_align = "space_between",
                    children = [
                        render.Text(content = "%snm" % fmt_float(dst, 1), color = DIM, font = "tb-8"),
                        render.Text(content = heading_to_compass(int(track)), color = DIM, font = "tb-8"),
                    ],
                ),
            ],
        ),
    )

def clear_skies_screen(radius):
    return render.Root(
        child = render.Column(
            expanded = True,
            main_align = "space_evenly",
            cross_align = "center",
            children = [
                render.Text(content = "clear skies", color = WHITE, font = "tb-8"),
                render.Text(content = "no aircraft", color = GRAY, font = "tb-8"),
                render.Text(content = "within %snm" % radius, color = DIM, font = "tb-8"),
            ],
        ),
    )

def error_screen(msg):
    return render.Root(
        child = render.Column(
            children = [
                render.Text(content = "overhead", color = GREEN, font = "tb-8"),
                render.Text(content = "error", color = "#FF4444", font = "tb-8"),
                render.Text(content = msg, color = GRAY, font = "tb-8"),
            ],
        ),
    )

def get_schema():
    return schema.Schema(
        version = "1",
        fields = [
            schema.Text(
                id = "api_key",
                name = "RapidAPI Key",
                desc = "ADS-B Exchange key from rapidapi.com",
                icon = "plane",
            ),
            schema.Text(
                id = "latitude",
                name = "Latitude",
                desc = "Your location latitude",
                icon = "mapPin",
                default = DEFAULT_LAT,
            ),
            schema.Text(
                id = "longitude",
                name = "Longitude",
                desc = "Your location longitude (negative for West)",
                icon = "mapPin",
                default = DEFAULT_LON,
            ),
            schema.Text(
                id = "radius",
                name = "Search radius (nm)",
                desc = "Nautical miles to search",
                icon = "expand",
                default = DEFAULT_RADIUS,
            ),
        ],
    )
