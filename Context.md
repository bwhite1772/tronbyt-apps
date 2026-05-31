# Tronbyt DIY Pixel Display — Project Context

## What This Is

A DIY version of the Tidbyt smart pixel display (now defunct). The goal is a self-hosted,
fully hackable desk display running custom apps — stock tickers, Spotify now playing,
weather, calendar, sports scores, etc.

The commercial equivalent is The Flight Wall, but that's locked to one app. This build
gives full control over app development.

---

## Hardware (already purchased)

| Component | Part | Notes |
|---|---|---|
| LED Panel | Adafruit #2279 — 64×32 RGB LED Matrix, 3mm pitch, HUB75 | ~$45 |
| Controller | Adafruit #5778 — MatrixPortal S3 (ESP32-S3, 8MB flash, 2MB PSRAM, WiFi) | ~$25 |
| Power | USB-C adapter, 3A+ required | Panel draws up to 12W |

The MatrixPortal S3 plugs **directly into the back of the panel** via HUB75 connector —
no soldering, no wiring.

---

## Software Stack

### Tronbyt
Open-source successor to Tidbyt's cloud backend. Fully self-hosted.
- GitHub org: https://github.com/tronbyt
- Repos of interest:
  - `tronbyt/server` — the scheduling server (Docker)
  - `tronbyt/firmware-esp32` — ESP32 firmware for the MatrixPortal
  - `tronbyt/pixlet` — maintained fork of Pixlet (app renderer)
  - `tronbyt/apps` — community app library (hard fork of Tidbyt's)

The firmware repo has a dedicated config for this exact hardware combo:
`sdkconfig.defaults.matrixportal-s3` — confirming the Adafruit MatrixPortal S3
is a first-class supported target.

### Pixlet
The app development framework. Apps are written in **Starlark** (Python-like).
- Each app is a `.star` file with a `main()` function that returns a `render.Root`
- Pixlet renders Starlark → WebP frames → served to the display over WiFi
- `pixlet serve myapp.star` gives a live browser preview at localhost:8080
- Install on Mac: `brew install tidbyt/tidbyt/pixlet`

### Architecture
```
[Mac: Tronbyt Server (Docker)] ──WiFi──▶ [MatrixPortal S3] ──▶ [64×32 LED Panel]
       ▲
[Pixlet] renders .star apps into WebP frames and pushes to server
```

The MatrixPortal polls the server for the next frame on a configurable interval.
The server handles app scheduling and rotation.

---

## Setup Steps (not yet done)

1. **Install Pixlet** on Mac via Homebrew
2. **Run Tronbyt server** via Docker (`tronbyt/server-docker-compose`)
3. **Flash firmware** onto MatrixPortal S3:
   - Plug in via USB-C
   - Create device in Tronbyt server UI at `http://localhost:8000`
   - Server generates pre-configured firmware binary
   - Flash via web flasher: https://espressif.github.io/esptool-js (Chrome/Edge only)
4. **Verify** panel connects and displays frames
5. **Build custom apps**

---

## App Ideas (priority order)

1. **SPOT stock ticker** — Spotify (SPOT) stock price + day change, pulling from a finance API
2. **Spotify Now Playing** — current track + artist via Spotify Web API
3. **Weather** — Atlanta forecast (zip 30316), high/low/conditions
4. **Google Calendar** — next event or today's agenda
5. **Sports scores** — live scores for relevant games
6. **Home stats** — whatever surfaces from Home Assistant if set up later

---

## Minimal App Template

```python
load("render.star", "render")
load("http.star", "http")

def main():
    # fetch data from any API here
    res = http.get("https://api.example.com/data")
    value = res.json()["value"]

    return render.Root(
        child = render.Column(
            children = [
                render.Text(content = "Label", color = "#AAAAAA"),
                render.Text(content = str(value), color = "#FFFFFF"),
            ]
        )
    )
```

Key render primitives: `Text`, `Row`, `Column`, `Box`, `Image`, `Animation`, `Marquee`

---

## Repo Structure (suggested)

```
tronbyt-apps/
├── CONTEXT.md          # this file
├── README.md
├── apps/
│   ├── spotify_now_playing.star
│   ├── spot_stock.star
│   ├── weather_atlanta.star
│   └── calendar.star
├── scripts/
│   └── push.sh         # helper to render + push an app to the server
└── docker-compose.yml  # Tronbyt server (copied from tronbyt/server-docker-compose)
```

---

## Key Links

- Tronbyt GitHub: https://github.com/tronbyt
- Pixlet docs: https://tidbyt.dev
- Starlark language reference: https://github.com/google/starlark-go/blob/master/doc/spec.md
- Tronbyt Discord: https://discord.gg/nKDErHGmU7
- Adafruit MatrixPortal S3 guide: https://learn.adafruit.com/adafruit-matrixportal-s3
- Web flasher: https://espressif.github.io/esptool-js
