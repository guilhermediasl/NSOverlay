# NSOverlay – XIAO ESP32C3 Nightscout Display

A standalone glucose monitor for your desk or bedside table, built on the
**Seeed Studio XIAO ESP32C3** and a **Waveshare 1.69″ IPS LCD**.

Inspired by [Prospector](https://github.com/carrefinho/prospector) — which uses
the same Waveshare panel on an XIAO nRF52840 as a ZMK keyboard dongle display —
this project repurposes the same hardware with an ESP32C3 (for built-in Wi-Fi)
and fetches real-time CGM readings directly from your Nightscout instance.

---

## What it shows

### Simple mode (`SHOW_GRAPH 0`)

```
┌──────────────────────────────────────┐
│              16:30                   │  ← clock
│                                      │
│    133    →    +1                    │  ← glucose · arrow · delta (Font7)
│                                      │
│          2 min atras                 │  ← age of reading
│                                      │
│ WiFi OK                    NS: OK   │  ← status bar
└──────────────────────────────────────┘
```

### Graph mode (`SHOW_GRAPH 1`, default)

```
┌──────────────────────────────────────┐
│ 16:30                  WiFi NS:OK   │  ← clock + status (row 1)
│       133  →  +1       2 min atras  │  ← glucose + trend (row 2-3)
│ ─────────────────────────────────── │  ← separator
│                           300       │
│  ● ● ● ●    ● ● ●                  │  ← coloured scatter dots
│           ● ●      ● ● ● ● ● ● ●  │     green = in-range
│  ── ── ── ── ── ── ── ── ── ─ 180 │  ← TARGET_HIGH dashed line
│  [target zone shaded green]         │
│  ── ── ── ── ── ── ── ── ── ─  70 │  ← TARGET_LOW dashed line
│                              40     │
│       |           |           |     │  ← hour ticks
│     14:00        15:00      16:00   │
└──────────────────────────────────────┘
```

* **Green** dot = in target range · **Orange** = above target · **Red** = below target
* **Latest reading** is drawn with a larger dot and an outer ring
* **Stale-data** (≥ 15 min old): age label turns yellow; compact "! OLD" appears on the left
* Green shaded zone = target range; dim-red zone = below target; dim-orange zone = above target

---

## Hardware

| Part | Details |
|---|---|
| Seeed Studio XIAO ESP32C3 | [product page](https://www.seeedstudio.com/Seeed-XIAO-ESP32C3-p-5431.html) |
| Waveshare 1.69″ IPS LCD **with Touch** (SKU 27057) | [product page](https://www.waveshare.com/1.69inch-touch-lcd-module.htm) — the non-touch version uses a different mounting pattern |

> The non-touch version of the Waveshare 1.69″ LCD has a different pinout and
> will **not** fit the Prospector case if you are using that enclosure.

---

## Wiring

```
XIAO ESP32C3          Waveshare 1.69" LCD
─────────────         ───────────────────
3.3 V        ──────►  VCC
GND          ──────►  GND
D10 (GPIO10) ──────►  DIN  (MOSI)
D8  (GPIO8)  ──────►  CLK  (SCK)
D1  (GPIO3)  ──────►  CS
D2  (GPIO4)  ──────►  DC
D0  (GPIO2)  ──────►  RST
D3  (GPIO5)  ──────►  BL   ← or tie BL directly to 3.3 V for always-on backlight
```

---

## Software setup

### 1 · Install the ESP32 board package

In the Arduino IDE, open **File → Preferences** and add this URL to
*Additional Boards Manager URLs*:

```
https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
```

Then open **Tools → Board → Boards Manager**, search for **esp32**, and install
the package by Espressif Systems (≥ 3.0.0 recommended).

Select **XIAO_ESP32C3** as your board.

### 2 · Install required libraries

Open the **Arduino Library Manager** (Ctrl+Shift+I) and install:

| Library | Author | Notes |
|---|---|---|
| **LovyanGFX** | lovyan03 | Display driver |
| **ArduinoJson** | Benoit Blanchon | JSON parsing |

> `WiFi`, `WiFiClientSecure`, `HTTPClient`, and the `mbedtls` SHA-1 functions
> are all bundled with the ESP32 Arduino core — no extra installation needed.

### 3 · Configure the sketch

Copy `config.h.example` to `config.h`, then fill in:

| Setting | Description |
|---|---|
| `WIFI_SSID` | Your Wi-Fi network name |
| `WIFI_PASSWORD` | Your Wi-Fi password |
| `NIGHTSCOUT_URL` | Full URL of your Nightscout site, **no trailing slash** |
| `API_SECRET` | Plain-text API secret (leave `""` if your site has no secret) |
| `TARGET_LOW` / `TARGET_HIGH` | Your glucose target range in mg/dL |
| `NTP_GMT_OFFSET_SEC` | Your UTC offset in seconds (Brazil UTC-3 → `-10800`) |
| `REFRESH_INTERVAL_MS` | How often to poll Nightscout (default 60 000 ms) |
| `SHOW_GRAPH` | `1` (default) = graph mode · `0` = simple large-value layout |
| `GRAPH_MINUTES` | Time window shown in graph mode (default `180` = 3 h) |
| `DISPLAY_FONT` | Font family for UI labels — see [Compatible fonts](#compatible-fonts) below |
| `COLOR_*` | 16-bit colours for every UI element — see [Display colours](#display-colours) below |

---

## Compatible fonts

Set `DISPLAY_FONT` in `config.h` to one of the four families below.
The glucose number always uses the built-in 7-segment `Font7`; `DISPLAY_FONT`
controls every other text element (clock, age label, error message, splash screen).

All fonts are bundled with the **LovyanGFX** library — no additional installation needed.

| `DISPLAY_FONT` value | Appearance | Size variants used |
|---|---|---|
| `FONT_FAMILY_FREE_SANS_BOLD` *(default)* | Bold proportional sans-serif (Helvetica / Android style — best screen readability) | Bold 18 pt · Regular 12 pt · Regular 9 pt |
| `FONT_FAMILY_FREE_SANS` | Regular proportional sans-serif (lighter weight, more elegant) | Regular 18 pt · 12 pt · 9 pt |
| `FONT_FAMILY_FREE_MONO` | Fixed-width monospaced (digital / retro look, numbers align vertically) | Mono 18 pt · 12 pt · 9 pt |
| `FONT_FAMILY_FREE_SERIF` | Traditional serif (similar to Times New Roman) | Serif 18 pt · 12 pt · 9 pt |

> **Tip:** The full GNU FreeFont collection available in LovyanGFX also includes oblique /
> italic and bold-oblique variants such as `FreeSansOblique12pt7b`, but these are not
> wrapped in a family constant.  You can use individual sizes directly by assigning
> `FONT_LARGE`, `FONT_MEDIUM`, and `FONT_SMALL` yourself after the `#include "config.h"`
> line in the sketch.

---

## Display colours

All `COLOR_*` constants in `config.h` accept:

- Any **`TFT_*` constant** from LovyanGFX (e.g. `TFT_BLACK`, `TFT_WHITE`, `TFT_RED`, `TFT_GREEN`, `TFT_YELLOW`, `TFT_ORANGE`, `TFT_CYAN`, `TFT_BLUE`)
- The **`RGB565(r, g, b)`** macro (defined in the sketch) — converts 8-bit R, G, B components to a 16-bit value at compile time.

| Constant | Default | Used for |
|---|---|---|
| `COLOR_BACKGROUND` | `TFT_BLACK` | Screen background |
| `COLOR_GLUCOSE_LOW` | `RGB565(220, 60, 60)` | Glucose value when below `TARGET_LOW` |
| `COLOR_GLUCOSE_HIGH` | `RGB565(255, 150, 0)` | Glucose value when above `TARGET_HIGH` |
| `COLOR_GLUCOSE_OK` | `RGB565(60, 210, 80)` | Glucose value when in target range |
| `COLOR_CLOCK` | `TFT_WHITE` | Clock text (top row) |
| `COLOR_AGE_NORMAL` | `RGB565(210, 210, 210)` | Age-of-reading label when data is fresh |
| `COLOR_AGE_STALE` | `TFT_YELLOW` | Age-of-reading label when data is ≥ 15 min old |
| `COLOR_STALE_WARN` | `TFT_YELLOW` | `! DADO ANTIGO !` warning banner |
| `COLOR_ERROR` | `TFT_RED` | Error / loading message |
| `COLOR_STATUS_OK` | `TFT_GREEN` | `WiFi OK` / `NS: OK` in the status bar |
| `COLOR_STATUS_ERR` | `TFT_RED` | `WiFi ERR` / `NS: ERR` in the status bar |
| `COLOR_SPLASH_TITLE` | `TFT_WHITE` | "NSOverlay" title on the boot splash |
| `COLOR_SPLASH_ACCENT` | `RGB565(100, 210, 230)` | Cyan subtitle on the boot splash |
| `COLOR_SPLASH_DIM` | `RGB565(150, 150, 150)` | Grey status message on the boot splash |
| `COLOR_GRAPH_TARGET_FILL` | `RGB565(0, 40, 0)` | Dark green fill for the target zone in the graph |
| `COLOR_GRAPH_TARGET_LINE` | `RGB565(0, 130, 0)` | Bright green boundary lines at `TARGET_LOW` / `TARGET_HIGH` |
| `COLOR_GRAPH_LOW_FILL` | `RGB565(50, 0, 0)` | Dark red fill below the low-target zone in the graph |
| `COLOR_GRAPH_HIGH_FILL` | `RGB565(50, 25, 0)` | Dark orange fill above the high-target zone in the graph |
| `COLOR_GRAPH_AXIS` | `RGB565(90, 90, 90)` | Axis lines and tick marks |
| `COLOR_GRAPH_AXIS_LABEL` | `RGB565(120, 120, 120)` | X / Y axis text labels |
| `COLOR_GRAPH_BORDER` | `RGB565(50, 50, 50)` | Graph area border and separator line |

---

### 4 · Upload

Connect your XIAO ESP32C3 via USB, select the correct port, and click
**Upload**.  The first time the board boots you should see:

1. A brief "NSOverlay / XIAO ESP32C3" splash screen  
2. "Conectando WiFi…" while connecting  
3. "Sincronizando hora…" while contacting the NTP server  
4. "Buscando glicemia…" while fetching the first reading  
5. The live glucose display

---

## Security note

The firmware uses `WiFiClientSecure::setInsecure()` which **skips TLS
certificate verification**.  This is acceptable for a personal maker device on
a trusted home network, but means the connection is vulnerable to a
man-in-the-middle attack on untrusted networks.  If you need stronger security,
pin the root CA certificate of your Nightscout host in `fetchNightscout()`.

---

## Changing display orientation

Call `lcd.setRotation(n)` in `setup()` with:

| `n` | Orientation |
|---|---|
| `0` | Portrait, USB connector at bottom (default) |
| `1` | Landscape, USB connector on the right |
| `2` | Portrait, USB connector at top |
| `3` | Landscape, USB connector on the left |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| White / blank screen | **First check**: do you see a brief **blue flash** at boot? If not, re-check all SPI wires (MOSI, CLK, CS, DC, RST). If you see blue but then blank, try lowering `freq_write` in the LGFX constructor. Check serial output for `[DISPLAY] Sprite OK` vs `sprite alloc failed`. |
| Inverted colours | The ST7789 on the Waveshare 1.69″ requires `invert = true` (already set) |
| "WiFi ERR" on status bar / connection timeout | The ESP32C3 supports **2.4 GHz only** — if your router has a separate 5 GHz network (often named `MySSID_5G`), use the 2.4 GHz SSID instead. Also check password and signal strength. |
| "NS: ERR" on status bar | Verify `NIGHTSCOUT_URL` (no trailing slash) and `API_SECRET` |
| Stale data warning | Your CGM transmitter may have stopped sending; check Nightscout |
| Time is wrong | Set `NTP_GMT_OFFSET_SEC` correctly in your local `config.h` |

---

## File structure

```
xiao_esp32c3_display/
├── xiao_esp32c3_display.ino   ← main Arduino sketch
├── config.h.example            ← tracked template (copy to config.h)
├── config.h                   ← user configuration (WiFi, Nightscout, pins)
└── README.md                  ← this file
```

---

## Credits

* [Prospector by carrefinho](https://github.com/carrefinho/prospector) — display
  form-factor and hardware inspiration  
* [NSOverlay](https://github.com/guilhermediasl/NSOverlay) — Windows glucose
  overlay that this device complements  
* [LovyanGFX](https://github.com/lovyan03/LovyanGFX) — excellent display library

---

## Disclaimer

This software is for informational purposes only and is **not** a substitute for
professional medical advice.  Always consult your healthcare provider before
making diabetes management decisions.

## License

MIT
