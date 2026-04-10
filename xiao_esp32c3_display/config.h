#pragma once

// =============================================================
// NSOverlay – XIAO ESP32C3 Nightscout Display
// User Configuration
// =============================================================
// Copy this file as-is; fill in every value marked with  <--
// =============================================================

// ---- WiFi -------------------------------------------------------
// Your home/office WiFi network credentials.
#define WIFI_SSID       "YourWiFiSSID"       // <-- change
#define WIFI_PASSWORD   "YourWiFiPassword"   // <-- change

// ---- Nightscout -------------------------------------------------
// Full URL of your Nightscout site, NO trailing slash.
// Examples:
//   "https://mysite.fly.dev"
//   "https://yourname.ns.10be.de"
#define NIGHTSCOUT_URL  "https://your-site.fly.dev"  // <-- change

// Plain-text API secret.  It will be SHA-1 hashed on the device
// before being sent, matching Nightscout's own behaviour.
// Leave as "" if your site has no API secret (TOKEN auth not
// currently supported — use a public or secret-protected site).
#define API_SECRET      ""   // <-- change or leave empty

// ---- Glucose targets (mg/dL) ------------------------------------
#define TARGET_LOW   70
#define TARGET_HIGH  180

// ---- Refresh interval -------------------------------------------
// How often to fetch new data from Nightscout (milliseconds).
// Nightscout typically stores a new reading every 5 minutes, so
// 60 000 ms (60 s) is a sensible polling interval.
#define REFRESH_INTERVAL_MS  60000UL

// ---- NTP / timezone ---------------------------------------------
// GMT offset in SECONDS for your timezone.
// Examples:
//   Brazil – Brasília (UTC-3): -3 * 3600 = -10800
//   Portugal / UK (UTC+0):       0
//   Central Europe (UTC+1):  3600
#define NTP_GMT_OFFSET_SEC   (-3L * 3600L)  // UTC-3 (Brasília)
#define NTP_DST_OFFSET_SEC   0L             // Brazil has no DST
#define NTP_SERVER           "pool.ntp.org"

// ---- Display hardware -------------------------------------------
// These defaults match the Waveshare 1.69" IPS LCD (SKU 27057,
// ST7789 controller, 240×280).  Adjust only if you use a
// different panel.
//
// Pin numbers below are ESP32C3 GPIO numbers.
// XIAO ESP32C3 silk-screen → GPIO mapping:
//   D0  → GPIO2    D1  → GPIO3    D2  → GPIO4
//   D3  → GPIO5    D4  → GPIO6    D5  → GPIO7
//   D6  → GPIO21   D7  → GPIO20
//   D8  → GPIO8 (SCK)             D9  → GPIO9
//   D10 → GPIO10 (MOSI)
#define LCD_PIN_SCLK   8   // D8
#define LCD_PIN_MOSI  10   // D10
#define LCD_PIN_DC     4   // D2
#define LCD_PIN_CS     3   // D1
#define LCD_PIN_RST    2   // D0
#define LCD_PIN_BL     5   // D3  (set to -1 if BL tied to 3.3 V)

#define LCD_WIDTH    240
#define LCD_HEIGHT   280
// ST7789 panels often need a small address-window offset.
// The Waveshare 1.69" module requires offset_y = 20 in portrait.
#define LCD_OFFSET_X   0
#define LCD_OFFSET_Y  20

// BL PWM brightness 0-255 (255 = full brightness)
#define LCD_BRIGHTNESS  200

// ---- Glucose history graph --------------------------------------
// Set GRAPH_ENABLED to 1 to show a scrolling glucose history graph
// at the bottom of the display.  The main-reading area is compacted
// automatically to make room.  Set to 0 to restore the original
// full-screen layout with no graph.
#define GRAPH_ENABLED         1

// Show axis tick labels on the graph (glucose values on the Y-axis,
// relative time on the X-axis).  Setting to 0 hides all labels and
// extends the plot area into the margin space.
#define GRAPH_SHOW_LABELS     1

// How many minutes of glucose history to show on the graph.
// Each Nightscout reading covers ~5 minutes, so 120 min ≈ 24 dots.
#define GRAPH_HISTORY_MINUTES 120

// Y-axis range of the graph (mg/dL).  Readings outside this range
// are clamped to the nearest edge.
#define GRAPH_Y_MIN  40
#define GRAPH_Y_MAX  400
