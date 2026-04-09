# NSOverlay – XIAO ESP32C3 Nightscout Display

A standalone glucose monitor for your desk or bedside table, built on the
**Seeed Studio XIAO ESP32C3** and a **Waveshare 1.69″ IPS LCD**.

Inspired by [Prospector](https://github.com/carrefinho/prospector) — which uses
the same Waveshare panel on an XIAO nRF52840 as a ZMK keyboard dongle display —
this project repurposes the same hardware with an ESP32C3 (for built-in Wi-Fi)
and fetches real-time CGM readings directly from your Nightscout instance.

---

## What it shows

```
┌────────────────────────┐
│        GLICEMIA        │   ← title
│                        │
│       128    ^         │   ← glucose value (mg/dL) + trend arrow
│            mg/dL       │   ← unit label
│        +5 mg/dL        │   ← delta vs previous reading
│        há 3 min        │   ← age of reading
│                        │
│ WiFi OK        NS: OK  │   ← status bar
└────────────────────────┘
```

* **Green** value = in target range  
* **Orange** value = above target  
* **Red** value = below target (low glucose)  
* A **"! DADO ANTIGO !"** warning appears when the reading is ≥ 15 min old

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

Open `config.h` and fill in:

| Setting | Description |
|---|---|
| `WIFI_SSID` | Your Wi-Fi network name |
| `WIFI_PASSWORD` | Your Wi-Fi password |
| `NIGHTSCOUT_URL` | Full URL of your Nightscout site, **no trailing slash** |
| `API_SECRET` | Plain-text API secret (leave `""` if your site has no secret) |
| `TARGET_LOW` / `TARGET_HIGH` | Your glucose target range in mg/dL |
| `NTP_GMT_OFFSET_SEC` | Your UTC offset in seconds (Brazil UTC-3 → `-10800`) |
| `REFRESH_INTERVAL_MS` | How often to poll Nightscout (default 60 000 ms) |

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
| White / blank screen | Check all SPI wires; verify `LCD_OFFSET_Y = 20` in `config.h` |
| Inverted colours | The ST7789 on the Waveshare 1.69″ requires `invert = true` (already set) |
| "WiFi ERR" on status bar | Check SSID/password; move closer to the router |
| "NS: ERR" on status bar | Verify `NIGHTSCOUT_URL` (no trailing slash) and `API_SECRET` |
| Stale data warning | Your CGM transmitter may have stopped sending; check Nightscout |
| Time is wrong | Set `NTP_GMT_OFFSET_SEC` correctly in `config.h` |

---

## File structure

```
xiao_esp32c3_display/
├── xiao_esp32c3_display.ino   ← main Arduino sketch
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
