load("render.star", "render")
load("http.star", "http")
load("schema.star", "schema")

GRAPHQL_URL = "https://api.monarchmoney.com/graphql"
LOGIN_URL = "https://api.monarchmoney.com/auth/login/"

NET_WORTH_QUERY = "query GetAggregateSnapshots($filters: AggregateSnapshotFilters) { aggregateSnapshots(filters: $filters) { date balance } }"

def get_schema():
    return schema.Schema(
        version = "1",
        fields = [
            schema.Text(
                id = "email",
                name = "Email",
                desc = "Your Monarch Money email address",
                icon = "envelope",
                default = "",
            ),
            schema.Text(
                id = "password",
                name = "Password",
                desc = "Your Monarch Money password. Not needed if API Token is set below.",
                icon = "lock",
                default = "",
            ),
            schema.Text(
                id = "api_token",
                name = "API Token (optional)",
                desc = "Use instead of email/password, or if you have MFA enabled. Get it via: pip install monarchmoney && python -c \"import asyncio; from monarchmoney import MonarchMoney; mm = MonarchMoney(); asyncio.run(mm.login()); print(mm._token)\"",
                icon = "key",
                default = "",
            ),
            schema.Text(
                id = "months",
                name = "Months of history",
                desc = "Number of months to show in chart (default: 12)",
                icon = "calendar",
                default = "12",
            ),
        ],
    )

def add_commas(s):
    n = len(s)
    result = ""
    for i in range(n):
        if i > 0 and (n - i) % 3 == 0:
            result += ","
        result += s[i]
    return result

def fmt_networth(val):
    negative = val < 0
    val = -val if negative else val
    result = "$" + add_commas(str(int(val + 0.5)))
    return ("-" if negative else "") + result

def fmt_pct(pct):
    sign = "+" if pct >= 0 else "-"
    abs_pct = pct if pct >= 0 else -pct
    tenths = int(abs_pct * 10 + 0.5)
    return "%s%d.%d%%" % (sign, tenths // 10, tenths % 10)

def get_token(email, password):
    rep = http.post(
        LOGIN_URL,
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json_body = {
            "username": email,
            "password": password,
            "supports_mfa": True,
            "trusted_device": False,
        },
        ttl_seconds = 3600,
    )
    if rep.status_code != 200:
        return None, "Login failed (%d)" % rep.status_code
    body = rep.json()
    token = body.get("token")
    if not token:
        return None, "MFA required - use api_token"
    return token, None

def main(config):
    api_token = config.get("api_token") or ""
    email = config.get("email") or ""
    password = config.get("password") or ""
    months = int(config.get("months") or "12")

    if not api_token:
        if not email or not password:
            return error_screen("Set email+password or api_token")
        api_token, err = get_token(email, password)
        if err:
            return error_screen(err)

    rep = http.post(
        GRAPHQL_URL,
        headers = {
            "Authorization": "Token %s" % api_token,
            "Content-Type": "application/json",
        },
        json_body = {
            "query": NET_WORTH_QUERY,
            "variables": {"filters": {"startDate": "2020-01-01"}},
        },
        ttl_seconds = 3600,
    )

    if rep.status_code != 200:
        return error_screen("HTTP %d" % rep.status_code)

    body = rep.json()
    if body.get("errors"):
        return error_screen("API error")

    snapshots = (body.get("data") or {}).get("aggregateSnapshots") or []
    if not snapshots:
        return error_screen("No data")

    # Limit to requested months (~30 days each)
    limit = months * 30
    if len(snapshots) > limit:
        snapshots = snapshots[len(snapshots) - limit:]

    n = len(snapshots)
    current = snapshots[n - 1]["balance"]

    # 30-day change vs current (or start of data if < 31 points)
    ref_idx = n - 31 if n > 31 else 0
    prev = snapshots[ref_idx]["balance"]
    change = current - prev
    pct = (change / prev * 100) if prev != 0 else 0.0

    change_color = "#00DD55" if change >= 0 else "#FF4444"
    fill_color = "#004418" if change >= 0 else "#3D0000"

    plot_data = []
    y_min = None
    y_max = None
    for i in range(n):
        bal = snapshots[i]["balance"]
        plot_data.append((i, bal))
        if y_min == None or bal < y_min:
            y_min = bal
        if y_max == None or bal > y_max:
            y_max = bal

    margin = (y_max - y_min) * 0.05
    if margin == 0:
        margin = max(abs(y_min) * 0.01, 1.0)

    if n > 1:
        chart = render.Plot(
            data = plot_data,
            width = 64,
            height = 24,
            color = change_color,
            fill_color = fill_color,
            x_lim = (0, n - 1),
            y_lim = (y_min - margin, y_max + margin),
            fill = True,
        )
    else:
        chart = render.Box(width = 64, height = 24)

    return render.Root(
        child = render.Column(
            children = [
                render.Row(
                    expanded = True,
                    main_align = "space_between",
                    children = [
                        render.Text(content = fmt_networth(current), color = "#FFFFFF", font = "tb-8"),
                        render.Text(content = fmt_pct(pct), color = change_color, font = "tb-8"),
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
                render.Text(content = "error", color = "#FF4444", font = "tb-8"),
                render.Text(content = msg, color = "#888888", font = "tb-8"),
            ],
        ),
    )
