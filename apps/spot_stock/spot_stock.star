load("render.star", "render")
load("http.star", "http")

SYMBOL = "SPOT"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/SPOT?interval=5m&range=1d"
SPOTIFY_GREEN = "#1DB954"
CHART_WIDTH = 52
CHART_HEIGHT = 16
TRADING_MINUTES = 390  # 9:30 AM to 4:00 PM

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

def main(config):
    rep = http.get(URL, headers = {"User-Agent": "Mozilla/5.0"}, ttl_seconds = 300)
    if rep.status_code != 200:
        return error_screen("HTTP %d" % rep.status_code)

    data = rep.json()
    results = (data.get("chart") or {}).get("result") or []
    if not results:
        return error_screen("no data")

    result = results[0]
    meta = result["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("previousClose") or meta.get("chartPreviousClose") or price
    change = price - prev
    pct = (change / prev) * 100

    sign = "+" if change >= 0 else "-"
    abs_change = change if change >= 0 else -change
    abs_pct = pct if pct >= 0 else -pct
    change_color = "#00DD55" if change >= 0 else "#FF4444"
    fill_color = "#004418" if change >= 0 else "#3D0000"

    # Build intraday chart data
    timestamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quotes.get("close") or []

    # Anchor x-axis to the first bar (9:30 AM open)
    market_open_ts = timestamps[0] if timestamps else None

    plot_data = []
    if market_open_ts and timestamps and closes:
        for i in range(len(timestamps)):
            if i < len(closes) and closes[i] != None:
                x = (timestamps[i] - market_open_ts) / 60
                if x >= 0 and x <= TRADING_MINUTES:
                    plot_data.append((x, closes[i]))

    if len(plot_data) > 1:
        y_vals = [pt[1] for pt in plot_data]
        y_min_val = y_vals[0]
        y_max_val = y_vals[0]
        for v in y_vals:
            if v < y_min_val:
                y_min_val = v
            if v > y_max_val:
                y_max_val = v
        y_margin = (y_max_val - y_min_val) * 0.1
        if y_margin == 0:
            y_margin = y_min_val * 0.002
        y_lim = (y_min_val - y_margin, y_max_val + y_margin)

        chart = render.Row(
            children = [
                render.Box(width = 6, height = CHART_HEIGHT),
                render.Plot(
                    data = plot_data,
                    width = CHART_WIDTH,
                    height = CHART_HEIGHT,
                    color = change_color,
                    fill_color = fill_color,
                    x_lim = (0, TRADING_MINUTES),
                    y_lim = y_lim,
                    fill = True,
                ),
                render.Box(width = 6, height = CHART_HEIGHT),
            ],
        )
    else:
        chart = render.Box(width = 64, height = CHART_HEIGHT)

    return render.Root(
        child = render.Column(
            children = [
                render.Row(
                    expanded = True,
                    main_align = "space_between",
                    children = [
                        render.Text(content = SYMBOL, color = SPOTIFY_GREEN, font = "tb-8"),
                        render.Text(content = "%s$%s" % (sign, fmt_float(abs_change, 2)), color = change_color, font = "tb-8"),
                    ],
                ),
                render.Row(
                    expanded = True,
                    main_align = "space_between",
                    children = [
                        render.Text(content = "$%s" % fmt_float(price, 2), color = "#FFFFFF", font = "tb-8"),
                        render.Text(content = "%s%s%%" % (sign, fmt_float(abs_pct, 1)), color = change_color, font = "tb-8"),
                    ],
                ),
                chart,
            ],
        ),
    )

def error_screen(msg):
    return render.Root(
        child = render.Column(
            children = [
                render.Text(content = SYMBOL, color = SPOTIFY_GREEN, font = "tb-8"),
                render.Text(content = "error", color = "#FF4444", font = "tb-8"),
                render.Text(content = msg, color = "#888888", font = "tb-8"),
            ],
        ),
    )
