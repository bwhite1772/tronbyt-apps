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

## Current Status

**Hardware is fully working.** The display is connected, the MatrixPortal S3 is polling the Tronbyt server, and apps are rendering and displaying. The only remaining issue is **incorrect color rendering** — the panel displays red-dominant colors instead of full RGB.

### What's been completed
- Tronbyt server running via Docker at `http://localhost:8000`
- `SINGLE_USER_AUTO_LOGIN=true` set in `.env` (required — device can't authenticate otherwise)
- MatrixPortal S3 flashed with Tronbyt firmware, polling `http://192.168.68.95:8000/desk-display/next`
- Device confirmed connecting and fetching frames (verified via tcpdump)
- Community apps working (arcade classics, etc.)

### The color problem
The panel shows everything red-dominant. Root cause: the Tronbyt-generated firmware has `SWAP_COLORS` misconfigured for this specific panel.

**What we know:**
- CircuitPython confirms the panel works correctly with standard RGB pin order
- `color_order="RBG"` is needed based on Adafruit docs for some panel batches
- The Tronbyt firmware source has `CONFIG_SWAP_COLORS=y` in `sdkconfig.defaults.matrixportal-s3`
- We need to build firmware from source with `CONFIG_SWAP_COLORS=n`

### Firmware build attempt
- Repo cloned at `~/Projects/firmware-esp32`
- ESP-IDF v5.3 cloned at `~/Projects/esp-idf`
- Custom config created at `sdkconfig.defaults.matrixportal-s3-adafruit` with `CONFIG_SWAP_COLORS=n`
- Build failed due to Python version conflicts (Conda's Python 3.8 vs needed 3.12+)
- **Next step: build using Docker** to avoid local Python environment issues:

```bash
docker run --rm -v ~/Projects/firmware-esp32:/firmware -w /firmware \
  espressif/idf:v5.3 \
  bash -c "idf.py -D SDKCONFIG_DEFAULTS='sdkconfig.defaults;sdkconfig.defaults.matrixportal-s3-adafruit' set-target esp32s3 && \
  idf.py build && \
  cd build && esptool.py --chip esp32s3 merge_bin -o merged_firmware.bin @flash_args"
```

Output binary will be at `~/Projects/firmware-esp32/build/merged_firmware.bin`. Flash with:

```bash
python3 -m esptool --chip esp32s3 --port /dev/cu.usbmodem* write_flash 0x0 ~/Projects/firmware-esp32/build/merged_firmware.bin
```

### Current firmware
Last known good firmware: `~/Downloads/Desk_Display-merged__7_.bin`
- SSID: `ormewood1373iot`
- Image URL: `http://192.168.68.95:8000/desk-display/next` (note: no port 8000 issue — check this)
- Connection type: HTTP
- API key baked in as query parameter

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
