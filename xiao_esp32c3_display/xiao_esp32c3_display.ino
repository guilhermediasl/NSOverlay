/*
 * NSOverlay – XIAO ESP32C3 Nightscout Glucose Display
 * =====================================================
 * Fetches the latest glucose reading from your Nightscout
 * instance and shows it on a Waveshare 1.69" IPS LCD.
 *
 * Hardware
 * --------
 * - Seeed Studio XIAO ESP32C3
 * - Waveshare 1.69" IPS LCD, ST7789, 240×280 (SKU 27057)
 *
 * Wiring (see config.h.example for GPIO numbers)
 * -------
 *   LCD VCC  ──► 3.3 V
 *   LCD GND  ──► GND
 *   LCD DIN  ──► D10  (GPIO10 / MOSI)
 *   LCD CLK  ──► D8   (GPIO8  / SCK)
 *   LCD CS   ──► D1   (GPIO3)
 *   LCD DC   ──► D2   (GPIO4)
 *   LCD RST  ──► D0   (GPIO2)
 *   LCD BL   ──► D3   (GPIO5)  or directly to 3.3 V
 *
 * Required libraries (install via the Arduino Library Manager)
 * ------------------------------------------------------------
 *   - LovyanGFX     (by lovyan03)
 *   - ArduinoJson   (by Benoit Blanchon)
 *
 * The WiFi, WiFiClientSecure, HTTPClient and mbedtls libraries
 * are bundled with the "esp32" board support package.
 *
 * Setup
 * -----
 * 1. Copy config.h.example to config.h and fill in WIFI_SSID,
 *    WIFI_PASSWORD, NIGHTSCOUT_URL, and API_SECRET.
 * 2. Select "XIAO_ESP32C3" as board in the Arduino IDE.
 * 3. Upload and enjoy.
 */

// ---- LovyanGFX --------------------------------------------------
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

// ---- Standard ESP32 libraries -----------------------------------
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <time.h>

// ---- Third-party ------------------------------------------------
#include <ArduinoJson.h>

// ---- mbedtls SHA-1 (bundled with ESP32 Arduino core) ------------
#include "mbedtls/md.h"

// ---- Compile-time helpers (must be defined before config.h) -----

// Convert 8-bit R, G, B to a 16-bit RGB-565 value at compile time.
// Use this macro in config.h for any COLOR_* constant that is not a TFT_
// colour name.  Example: #define COLOR_FOO  RGB565(100, 210, 230)
#define RGB565(r, g, b) \
    ((uint16_t)(((uint16_t)((r) & 0xF8u) << 8) | \
                ((uint16_t)((g) & 0xFCu) << 3) | \
                ((b) >> 3)))

// Font-family identifiers.  Set DISPLAY_FONT in config.h to one of these.
#define FONT_FAMILY_FREE_SANS_BOLD  1   // bold proportional sans-serif (default)
#define FONT_FAMILY_FREE_SANS       2   // regular proportional sans-serif
#define FONT_FAMILY_FREE_MONO       3   // fixed-width monospaced
#define FONT_FAMILY_FREE_SERIF      4   // traditional serif

// ---- Project config ---------------------------------------------
#include "config.h"

// Optional graph-hour line settings.
// Keep backward compatibility when older config.h files do not define these.
#ifndef GRAPH_HOUR_LINES_FULL_HEIGHT
#define GRAPH_HOUR_LINES_FULL_HEIGHT 0
#endif

#ifndef COLOR_GRAPH_HOUR_LINE
#define COLOR_GRAPH_HOUR_LINE COLOR_GRAPH_AXIS
#endif

#ifndef GRAPH_10MIN_DASH_LEN
#define GRAPH_10MIN_DASH_LEN 2
#endif

#ifndef GRAPH_10MIN_GAP_LEN
#define GRAPH_10MIN_GAP_LEN 7
#endif

#ifndef GRAPH_HOUR_DASH_LEN
#define GRAPH_HOUR_DASH_LEN 4
#endif

#ifndef GRAPH_HOUR_GAP_LEN
#define GRAPH_HOUR_GAP_LEN 8
#endif

#ifndef GRAPH_TARGET_DASH_LEN
#define GRAPH_TARGET_DASH_LEN 6
#endif

#ifndef GRAPH_TARGET_GAP_LEN
#define GRAPH_TARGET_GAP_LEN 8
#endif

// ---- Font size variants (resolved from DISPLAY_FONT in config.h) -
#if   DISPLAY_FONT == FONT_FAMILY_FREE_SANS_BOLD
#  define FONT_LARGE   lgfx::fonts::FreeSansBold18pt7b
#  define FONT_MEDIUM  lgfx::fonts::FreeSans12pt7b
#  define FONT_SMALL   lgfx::fonts::FreeSans9pt7b
#elif DISPLAY_FONT == FONT_FAMILY_FREE_SANS
#  define FONT_LARGE   lgfx::fonts::FreeSans18pt7b
#  define FONT_MEDIUM  lgfx::fonts::FreeSans12pt7b
#  define FONT_SMALL   lgfx::fonts::FreeSans9pt7b
#elif DISPLAY_FONT == FONT_FAMILY_FREE_MONO
#  define FONT_LARGE   lgfx::fonts::FreeMono18pt7b
#  define FONT_MEDIUM  lgfx::fonts::FreeMono12pt7b
#  define FONT_SMALL   lgfx::fonts::FreeMono9pt7b
#elif DISPLAY_FONT == FONT_FAMILY_FREE_SERIF
#  define FONT_LARGE   lgfx::fonts::FreeSerif18pt7b
#  define FONT_MEDIUM  lgfx::fonts::FreeSerif12pt7b
#  define FONT_SMALL   lgfx::fonts::FreeSerif9pt7b
#else
#  error "DISPLAY_FONT must be one of: FONT_FAMILY_FREE_SANS_BOLD, FONT_FAMILY_FREE_SANS, FONT_FAMILY_FREE_MONO, FONT_FAMILY_FREE_SERIF"
#endif

// =================================================================
// Display driver – Waveshare 1.69" ST7789, 240×280
// =================================================================
class LGFX : public lgfx::LGFX_Device {
    lgfx::Panel_ST7789 _panel;
    lgfx::Bus_SPI      _bus;
    lgfx::Light_PWM    _backlight;

public:
    LGFX() {
        // --- SPI bus ---------------------------------------------
        {
            auto cfg        = _bus.config();
            cfg.spi_host    = SPI2_HOST;
            cfg.spi_mode    = 0;
            cfg.freq_write  = 20000000;  // 20 MHz – safer for jumper-wire connections
            cfg.freq_read   = 16000000;
            cfg.spi_3wire   = false;  // 4-wire SPI: separate DC pin (not encoded in stream)
            cfg.use_lock    = true;
            cfg.dma_channel = SPI_DMA_CH_AUTO;
            cfg.pin_sclk    = LCD_PIN_SCLK;
            cfg.pin_mosi    = LCD_PIN_MOSI;
            cfg.pin_miso    = -1;
            cfg.pin_dc      = LCD_PIN_DC;
            _bus.config(cfg);
            _panel.setBus(&_bus);
        }
        // --- Panel -----------------------------------------------
        {
            auto cfg             = _panel.config();
            cfg.pin_cs           = LCD_PIN_CS;
            cfg.pin_rst          = LCD_PIN_RST;
            cfg.pin_busy         = -1;
            cfg.panel_width      = LCD_WIDTH;
            cfg.panel_height     = LCD_HEIGHT;
            cfg.offset_x         = LCD_OFFSET_X;
            cfg.offset_y         = LCD_OFFSET_Y;
            cfg.offset_rotation  = 0;
            cfg.dummy_read_pixel = 8;
            cfg.dummy_read_bits  = 1;
            cfg.readable         = false;
            cfg.invert           = true;   // required for Waveshare 1.69"
            cfg.rgb_order        = true;
            cfg.dlen_16bit       = false;
            cfg.bus_shared       = false;
            _panel.config(cfg);
        }
        // --- Backlight -------------------------------------------
        if (LCD_PIN_BL >= 0) {
            auto cfg        = _backlight.config();
            cfg.pin_bl      = LCD_PIN_BL;
            cfg.invert      = false;
            cfg.freq        = 44100;
            cfg.pwm_channel = 7;
            _backlight.config(cfg);
            _panel.setLight(&_backlight);
        }
        setPanel(&_panel);
    }
};

static LGFX          lcd;
static LGFX_Sprite   canvas(&lcd);

// =================================================================
// Application state
// =================================================================
struct GlucoseReading {
    int     sgv       = 0;      // mg/dL
    int     delta     = 0;      // change vs previous reading
    String  direction = "";     // "Flat", "SingleUp", …
    int64_t dateMs    = 0;      // Nightscout timestamp (Unix ms)
    bool    valid     = false;
};

static GlucoseReading g_reading;
static String         g_error       = "";
static bool           g_ntpSynced   = false;
static unsigned long  g_lastFetchMs = 0;

// History ring for graph mode (populated only when SHOW_GRAPH = 1).
// Nightscout returns entries newest-first; we store them in the same
// order so g_graphHistory[0] is always the most-recent reading.
#if SHOW_GRAPH
    static const int GRAPH_MAX_POINTS = 200;  // buffer size; holds up to 200 min at 1-min intervals or 16+ h at 5-min intervals
    struct GraphPoint { int sgv; int64_t dateMs; };
    static GraphPoint g_graphHistory[GRAPH_MAX_POINTS];
    static int        g_graphHistoryLen = 0;
#endif

// =================================================================
// Helpers
// =================================================================

// SHA-1 hex digest of plain-text API secret.
// Nightscout v1 API requires the secret to be SHA-1 hashed — this is a
// deliberate compatibility choice to match Nightscout's own behaviour, not
// a security decision.  If your Nightscout instance supports token-based
// auth (NIGHTSCOUT_API_TOKEN), you can skip API_SECRET and pass the token
// as a query parameter instead: ?token=<your_token>
static String sha1Hex(const String& input) {
    uint8_t hash[20] = {};
    mbedtls_md_context_t ctx;
    mbedtls_md_init(&ctx);
    const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA1);
    mbedtls_md_setup(&ctx, info, 0);
    mbedtls_md_starts(&ctx);
    mbedtls_md_update(&ctx,
                      reinterpret_cast<const unsigned char*>(input.c_str()),
                      input.length());
    mbedtls_md_finish(&ctx, hash);
    mbedtls_md_free(&ctx);

    char hex[41];
    for (int i = 0; i < 20; i++) {
        sprintf(&hex[i * 2], "%02x", hash[i]);
    }
    return String(hex);
}

// Map Nightscout direction strings to readable ASCII arrows.
// Draw a real graphical trend arrow centred on (cx, cy).
// Works on any LovyanGFX drawable (LGFX_Device or LGFX_Sprite).
// sz controls the half-size of the arrow bounding box; defaults to 24
// (the size used in the main glucose row) but can be reduced to 14 for
// compact header use in graph mode.
static void drawTrendArrow(lgfx::LGFXBase& g, const String& dir,
                           int cx, int cy, uint16_t col, int sz = 24) {
    const int HW = sz * 11 / 24;  // arrowhead half-width (perpendicular to direction)
    const int T  = max(1, sz / 6); // shaft half-thickness
    const int hw = sz *  8 / 24;   // diagonal arrowhead half-width (~HW * 0.7)

    if (dir == "DoubleUp") {
        // Two stacked arrowheads pointing up + short shaft below
        g.fillRect(cx - T, cy, T * 2, sz / 2, col);
        g.fillTriangle(cx - HW, cy,           cx, cy - sz * 2 / 3,
                       cx + HW, cy,           col);
        g.fillTriangle(cx - HW, cy - sz * 2 / 3, cx, cy - sz * 4 / 3,
                       cx + HW, cy - sz * 2 / 3, col);
    } else if (dir == "SingleUp") {
        g.fillRect(cx - T, cy, T * 2, sz, col);
        g.fillTriangle(cx - HW, cy, cx, cy - sz, cx + HW, cy, col);
    } else if (dir == "FortyFiveUp") {
        // Diagonal arrow pointing upper-right
        // Shaft parallelogram (perpendicular to direction (1,-1) is (1,1))
        g.fillTriangle(cx - sz + T, cy + sz + T,
                       cx - sz - T, cy + sz - T,
                       cx - T,      cy - T,      col);
        g.fillTriangle(cx - sz + T, cy + sz + T,
                       cx + T,      cy + T,
                       cx - T,      cy - T,      col);
        // Arrowhead pointing to upper-right
        g.fillTriangle(cx + sz, cy - sz, cx + hw, cy + hw, cx - hw, cy - hw, col);
    } else if (dir == "Flat") {
        g.fillRect(cx - sz, cy - T, sz, T * 2, col);
        g.fillTriangle(cx, cy - HW, cx + sz, cy, cx, cy + HW, col);
    } else if (dir == "FortyFiveDown") {
        // Diagonal arrow pointing lower-right
        // Shaft parallelogram (perpendicular to direction (1,1) is (1,-1))
        g.fillTriangle(cx - sz + T, cy - sz - T,
                       cx - sz - T, cy - sz + T,
                       cx - T,      cy + T,      col);
        g.fillTriangle(cx - sz + T, cy - sz - T,
                       cx + T,      cy - T,
                       cx - T,      cy + T,      col);
        // Arrowhead pointing to lower-right
        g.fillTriangle(cx + sz, cy + sz, cx + hw, cy - hw, cx - hw, cy + hw, col);
    } else if (dir == "SingleDown") {
        g.fillRect(cx - T, cy - sz, T * 2, sz, col);
        g.fillTriangle(cx - HW, cy, cx, cy + sz, cx + HW, cy, col);
    } else if (dir == "DoubleDown") {
        // Two stacked arrowheads pointing down + short shaft above
        g.fillRect(cx - T, cy - sz / 2, T * 2, sz / 2, col);
        g.fillTriangle(cx - HW, cy,           cx, cy + sz * 2 / 3,
                       cx + HW, cy,           col);
        g.fillTriangle(cx - HW, cy + sz * 2 / 3, cx, cy + sz * 4 / 3,
                       cx + HW, cy + sz * 2 / 3, col);
    } else {
        g.fillCircle(cx, cy, T, col);
    }
}

// Draw a vertical dashed line with configurable dash and gap lengths.
static void drawDashedVLine(lgfx::LGFXBase& g, int x, int y, int h,
                            uint16_t col, int dashLen, int gapLen) {
    if (h <= 0) return;
    if (dashLen < 1) dashLen = 1;
    if (gapLen < 0) gapLen = 0;

    int yEnd = y + h;
    for (int cy = y; cy < yEnd; cy += (dashLen + gapLen)) {
        int seg = min(dashLen, yEnd - cy);
        g.drawFastVLine(x, cy, seg, col);
    }
}

// Draw a horizontal dashed line with configurable dash and gap lengths.
static void drawDashedHLine(lgfx::LGFXBase& g, int x, int y, int w,
                            uint16_t col, int dashLen, int gapLen) {
    if (w <= 0) return;
    if (dashLen < 1) dashLen = 1;
    if (gapLen < 0) gapLen = 0;

    int xEnd = x + w;
    for (int cx = x; cx < xEnd; cx += (dashLen + gapLen)) {
        int seg = min(dashLen, xEnd - cx);
        g.drawFastHLine(cx, y, seg, col);
    }
}

// 16-bit colour based on glucose level.
static uint16_t glucoseColor(int sgv) {
    if (sgv > 0 && sgv < TARGET_LOW)   return COLOR_GLUCOSE_LOW;
    if (sgv > TARGET_HIGH)             return COLOR_GLUCOSE_HIGH;
    return                                    COLOR_GLUCOSE_OK;
}

// Linear interpolation between two RGB-565 colours.
// factor / maxFactor in [0,1]: 0 → c0, maxFactor → c1.
static uint16_t lerpColor565(uint16_t c0, uint16_t c1, int factor, int maxFactor) {
    if (maxFactor <= 0 || factor >= maxFactor) return c1;
    if (factor <= 0) return c0;
    int r0 = (c0 >> 11) & 0x1F,  g0 = (c0 >> 5) & 0x3F,  b0 = c0 & 0x1F;
    int r1 = (c1 >> 11) & 0x1F,  g1 = (c1 >> 5) & 0x3F,  b1 = c1 & 0x1F;
    int r = r0 + (r1 - r0) * factor / maxFactor;
    int g = g0 + (g1 - g0) * factor / maxFactor;
    int b = b0 + (b1 - b0) * factor / maxFactor;
    return (uint16_t)((r << 11) | (g << 5) | b);
}

// Graph-dot colour matching the Python NSOverlay gradient:
//   in-range                → COLOR_GLUCOSE_OK (green)
//   just above TARGET_HIGH  → yellow, at sgvMax → red
//   just below TARGET_LOW   → yellow, at sgvMin → red
// sgvMin / sgvMax are the actual extremes visible in the dataset window.
static uint16_t glucoseColorGraph(int sgv, int sgvMin, int sgvMax) {
    const uint16_t YELLOW = RGB565(255, 255, 0);
    const uint16_t RED    = RGB565(255,   0, 0);
    if (sgv > TARGET_HIGH) {
        int fullRange = max(sgvMax - TARGET_HIGH, 1);
        return lerpColor565(YELLOW, RED, sgv - TARGET_HIGH, fullRange);
    }
    if (sgv > 0 && sgv < TARGET_LOW) {
        int fullRange = max(TARGET_LOW - sgvMin, 1);
        return lerpColor565(YELLOW, RED, TARGET_LOW - sgv, fullRange);
    }
    return COLOR_GLUCOSE_OK;
}

static bool glucoseNeedsPill(int sgv) {
    return (sgv > 0 && (sgv < TARGET_LOW || sgv > TARGET_HIGH));
}

static uint16_t glucosePillColor(int sgv) {
    if (sgv < TARGET_LOW)  return COLOR_GLUCOSE_LOW;
    if (sgv > TARGET_HIGH) return COLOR_GLUCOSE_HIGH;
    return COLOR_BACKGROUND;
}

// Human-readable age of the last reading (in Portuguese).
static String ageLabel(int64_t dateMs) {
    if (!g_ntpSynced || dateMs == 0) return "";

    struct timeval tv;
    gettimeofday(&tv, nullptr);
    int64_t nowMs   = (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000LL;
    int64_t ageMs   = nowMs - dateMs;

    if (ageMs < 0)           return "now";
    int ageSec = (int)(ageMs / 1000LL);
    if (ageSec <  60)        return String(ageSec) + " s ago";
    int ageMin = ageSec / 60;
    if (ageMin <  60)        return String(ageMin) + " min ago";
    return String(ageMin / 60) + " h ago";
}

// Current time as "HH:MM" string, or "" when NTP not yet synced.
static String clockString() {
    if (!g_ntpSynced) return "";
    struct tm t;
    if (!getLocalTime(&t)) return "";
    char buf[6];
    strftime(buf, sizeof(buf), "%H:%M", &t);
    return String(buf);
}

// Match NSOverlay Python delta logic:
// interpolate the glucose value exactly 5 minutes before the latest reading,
// then compute current - interpolated_value.
static bool interpolateGlucose5MinAgo(JsonArray arr, int currentSgv,
                                      int64_t currentDateMs, int& deltaOut) {
    if (arr.size() < 2) return false;

    const int64_t fiveMinMs = 5LL * 60LL * 1000LL;
    int64_t targetTime = currentDateMs - fiveMinMs;

    int64_t oldestTime = arr[arr.size() - 1]["date"] | (int64_t)0;
    if (targetTime < oldestTime) return false;

    if (targetTime >= currentDateMs) {
        deltaOut = 0;
        return true;
    }

    // Nightscout returns newest-first, so walk from oldest -> newest to find
    // the interval that brackets the target time.
    for (size_t i = arr.size() - 1; i > 0; --i) {
        int64_t t1 = arr[i]["date"] | (int64_t)0;
        int64_t t2 = arr[i - 1]["date"] | (int64_t)0;
        if (t1 <= targetTime && targetTime <= t2) {
            int v1 = arr[i]["sgv"] | 0;
            int v2 = arr[i - 1]["sgv"] | 0;

            if (t2 == t1) {
                deltaOut = currentSgv - v1;
                return true;
            }

            double ratio = (double)(targetTime - t1) / (double)(t2 - t1);
            double interpolatedValue = v1 + ratio * (v2 - v1);
            int glucose5MinAgo = (int)(interpolatedValue >= 0.0
                                        ? interpolatedValue + 0.5
                                        : interpolatedValue - 0.5);
            deltaOut = currentSgv - glucose5MinAgo;
            return true;
        }
    }

    return false;
}

// =================================================================
// Nightscout fetch
// =================================================================
static bool fetchNightscout() {
    Serial.println("[NS] Fetching data...");
    if (WiFi.status() != WL_CONNECTED) {
        g_error = "WiFi desconectado";
        Serial.println("[NS] Aborted: WiFi not connected");
        return false;
    }

    const int MAX_RETRIES = 3;
    for (int attempt = 0; attempt < MAX_RETRIES; attempt++) {
        if (attempt > 0) {
            Serial.print("[NS] Retry ");
            Serial.println(attempt);
            delay(5000);  // give the network more time to recover between retries
            if (WiFi.status() != WL_CONNECTED) {
                g_error = "WiFi desconectado";
                return false;
            }
        }

        WiFiClientSecure client;
        // TLS certificate verification is disabled for simplicity on a maker
        // device on a trusted home network.  For stronger security, remove
        // setInsecure() and instead call client.setCACert(<PEM string>) with
        // the root CA certificate of your Nightscout host.
        client.setInsecure();
        // Cap the TLS handshake so a stalled connection does not block for
        // the 60-90 s lwIP default.  http.setTimeout() only covers the HTTP
        // response-read phase; setHandshakeTimeout() covers the TLS negotiation.
        client.setHandshakeTimeout(10);  // seconds

        HTTPClient http;
        // In graph mode request enough readings to fill the time window.
        // GRAPH_ENTRY_INTERVAL is the expected gap between readings in minutes
        // (5 for standard CGM, 1 for Nightscout instances that log every minute).
        // Requesting too many entries just means a slightly larger HTTP payload;
        // the sketch will store at most GRAPH_MAX_POINTS entries.
#if SHOW_GRAPH
        const int fetchCount = min(GRAPH_MAX_POINTS, GRAPH_MINUTES / GRAPH_ENTRY_INTERVAL + 4);
        String url = String(NIGHTSCOUT_URL) + "/api/v1/entries.json?count=" + String(fetchCount);
#else
        String url = String(NIGHTSCOUT_URL) + "/api/v1/entries.json?count=2";
#endif
        http.begin(client, url);
        http.setTimeout(10000);  // ms – response read timeout

        if (strlen(API_SECRET) > 0) {
            http.addHeader("api-secret", sha1Hex(String(API_SECRET)));
        }

        int code = http.GET();
        if (code != HTTP_CODE_OK) {
            g_error = "HTTP " + String(code);
            Serial.print("[NS] HTTP error: ");
            Serial.print(code);
            if (code < 0) {
                Serial.print(" (");
                Serial.print(http.errorToString(code));
                Serial.print(")");
            }
            Serial.println();
            http.end();
            continue;  // retry
        }

        String body = http.getString();
        http.end();

        // In graph mode the response can be many entries.
        // Use DynamicJsonDocument (heap) to avoid overflowing the 8 KB task stack.
        // Capacity is scaled to the actual fetch count (~200 bytes per entry in
        // ArduinoJson's internal representation covers a typical Nightscout entry).
        // In simple mode 2 KB on the stack is fine.
#if SHOW_GRAPH
        DynamicJsonDocument doc(fetchCount * 200 + 1024);
#else
        StaticJsonDocument<2048> doc;
#endif
        DeserializationError err = deserializeJson(doc, body);
        if (err || !doc.is<JsonArray>()) {
            g_error = "JSON invalido";
            Serial.print("[NS] JSON parse error: ");
            Serial.println(err.c_str());
            continue;
        }

        JsonArray arr = doc.as<JsonArray>();
        if (arr.size() == 0) {
            g_error = "Sem leituras";
            Serial.println("[NS] No readings in response");
            continue;
        }

        GlucoseReading r;
        r.sgv       = arr[0]["sgv"]       | 0;
        r.direction = arr[0]["direction"].as<String>();
        r.dateMs    = arr[0]["date"]      | (int64_t)0;
        r.valid     = (r.sgv > 0);

        if (arr.size() >= 2) {
            int delta = 0;
            if (interpolateGlucose5MinAgo(arr, r.sgv, r.dateMs, delta)) {
                r.delta = delta;
            }
        }

        g_reading = r;
        g_error   = "";

#if SHOW_GRAPH
        // Populate history array (newest first, matching Nightscout order).
        g_graphHistoryLen = 0;
        for (size_t i = 0; i < arr.size() && g_graphHistoryLen < GRAPH_MAX_POINTS; i++) {
            int     sv  = arr[i]["sgv"]  | 0;
            int64_t dms = arr[i]["date"] | (int64_t)0;
            if (sv > 0 && dms > 0) {
                g_graphHistory[g_graphHistoryLen].sgv    = sv;
                g_graphHistory[g_graphHistoryLen].dateMs = dms;
                g_graphHistoryLen++;
            }
        }
        Serial.print("[NS] History points: ");
        Serial.println(g_graphHistoryLen);
#endif

        Serial.print("[NS] sgv=");
        Serial.print(r.sgv);
        Serial.print(" delta=");
        Serial.print(r.delta);
        Serial.print(" dir=");
        Serial.println(r.direction);
        return true;
    }

    Serial.println("[NS] All retries failed");
    return false;
}

// =================================================================
// Graph-mode rendering helper
// =================================================================
// Draws an NSOverlay-inspired layout:
//   Header: left column (clock + age + WiFi/NS) | vertical rule | right (glucose + arrow + delta)
//   Graph:  full-width scatter plot with dynamic Y scaling
// Called from renderDisplay() when SHOW_GRAPH = 1.
// Accepts an LGFXBase& so it works with both the sprite canvas and
// direct-LCD fallback — the same pattern used by drawTrendArrow.
#if SHOW_GRAPH
static void renderGraphMode(lgfx::LGFXBase& g, int W, int H) {

    // ── Layout constants ───────────────────────────────────────────
    // Header occupies the top HEADER_H pixels.  Everything below is graph.
    //
    // Header structure:
    //   Left column  [MARGIN … SEP_X-1] : clock (FONT_LARGE) + age (FONT_SMALL)
    //   Vertical rule at SEP_X
    //   Right column [SEP_X+5 … W-MARGIN]: glucose + slim arrow + (delta) — centred
    const int HEADER_H   = 68;   // total header height (clock + age rows need ~68 px)
    const int SEP_X      = 100;  // x of vertical rule (wider left column for FONT_LARGE clock)
    const int MARGIN     = 6;    // horizontal inset from screen edges
    const int L_X        = 16;   // left edge of left column text (extra inset avoids corner clipping)
    const int R_X        = SEP_X + 5;   // left edge of right column
    const int ARROW_SZ   = 11;          // slim arrow; at sz=11, T=1 so shaft is 2 px wide

    // Graph area (below header separator)
    const int GRAPH_TOP    = HEADER_H + 3;
    const int GRAPH_BOTTOM = H - 13;          // 13 px for X-axis labels
    const int GRAPH_LEFT   = 24;              // left margin for Y-axis labels
    const int GRAPH_RIGHT  = W - 2;
    const int GRAPH_H      = GRAPH_BOTTOM - GRAPH_TOP;
    const int GRAPH_W      = GRAPH_RIGHT - GRAPH_LEFT;

    // ── Dynamic Y-axis range (mirrors NSOverlay _compute_y_range) ──
    // Anchor: TARGET_LOW and TARGET_HIGH must always be visible.
    // Include all historical readings so any out-of-range value scrolls in.
    int dataMin = TARGET_HIGH;
    int dataMax = TARGET_LOW;
    for (int i = 0; i < g_graphHistoryLen; i++) {
        int sv = g_graphHistory[i].sgv;
        if (sv > 0) {
            if (sv < dataMin) dataMin = sv;
            if (sv > dataMax) dataMax = sv;
        }
    }
    // Expand to always include the target boundaries
    if (TARGET_LOW  < dataMin) dataMin = TARGET_LOW;
    if (TARGET_HIGH > dataMax) dataMax = TARGET_HIGH;

    // 15 % padding (minimum 20 units) so values don't sit on the edge
    int glucRange = dataMax - dataMin;
    int pad       = glucRange * 15 / 100;
    if (pad < 20) pad = 20;

    int yAxisMin = dataMin - pad;
    int yAxisMax = dataMax + pad;

    // Clamp to physiological limits
    if (yAxisMin < 40)  yAxisMin = 40;
    if (yAxisMax > 400) yAxisMax = 400;

    // Ensure at least a 100-unit window so the graph is never too zoomed in
    if (yAxisMax - yAxisMin < 100) {
        int center = (yAxisMin + yAxisMax) / 2;
        yAxisMin = center - 50;
        yAxisMax = center + 50;
        if (yAxisMin < 40)  { yAxisMin = 40;  yAxisMax = 140; }
        if (yAxisMax > 400) { yAxisMax = 400; yAxisMin = 300; }
    }

    // Y mapping: glucose value → pixel row (clamped to axis range)
    auto sgvToY = [&](int sgv) -> int {
        if (sgv < yAxisMin) sgv = yAxisMin;
        if (sgv > yAxisMax) sgv = yAxisMax;
        return GRAPH_TOP + (yAxisMax - sgv) * GRAPH_H / (yAxisMax - yAxisMin);
    };

    // Current time & X mapping
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    int64_t nowMs    = g_ntpSynced
                       ? ((int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000LL)
                       : 0;
    int64_t windowMs = (int64_t)GRAPH_MINUTES * 60000LL;
    int64_t oldestMs = nowMs - windowMs;

    auto msToX = [&](int64_t ms) -> int {
        if (windowMs <= 0) return GRAPH_LEFT;
        return GRAPH_LEFT + (int)((ms - oldestMs) * (int64_t)GRAPH_W / windowMs);
    };

    // ── Header: right column — glucose + arrow + (delta) ──────────
    // The entire group is centred horizontally inside the right column.
    // Arrow centre is placed at startX + glucoseW + ARROW_SZ + 4 so its
    // left edge (arrowCX - ARROW_SZ) always lands 4 px to the right of
    // the glucose text, preventing overlap for all arrow types.
    {
        const int hCY      = HEADER_H / 2;  // vertical centre of header = 28
        const int rightColW = (W - MARGIN) - R_X;

        if (!g_reading.valid) {
            g.setFont(&FONT_MEDIUM);
            g.setTextSize(1);
            g.setTextColor(COLOR_ERROR);
            g.setTextDatum(lgfx::middle_center);
            g.drawString(g_error.length() > 0 ? g_error : "Aguarde...",
                         R_X + rightColW / 2, hCY);
        } else {
            uint16_t col = glucoseColor(g_reading.sgv);
            bool pill = glucoseNeedsPill(g_reading.sgv);
            const int rowPadX = 10;
            const int rowPadY = 6;

            // Measure element widths so we can center the whole group.
            g.setFont(&FONT_LARGE);
            g.setTextSize(1);
            String glucoseStr = String(g_reading.sgv);
            String deltaStr   = String(g_reading.delta >= 0 ? "+" : "") +
                                String(g_reading.delta);
            int glucoseW = g.textWidth(glucoseStr);
            int deltaW   = g.textWidth(deltaStr);
            // Group: glucose | 4px gap | arrow (2*ARROW_SZ) | 4px gap | delta
            int totalW   = glucoseW + 4 + ARROW_SZ * 2 + 4 + deltaW;
            int startX   = R_X + max(0, (rightColW - totalW) / 2);

            if (pill) {
                int textH = g.fontHeight();
                int groupH = max(textH, ARROW_SZ * 2);
                int pillX = startX - rowPadX;
                int pillY = hCY - groupH / 2 - rowPadY;
                int pillW = totalW + rowPadX * 2;
                int pillH = groupH + rowPadY * 2;
                g.fillRoundRect(pillX, pillY, pillW, pillH, 10, glucosePillColor(g_reading.sgv));
            }

            // Glucose number
            g.setTextColor(pill ? TFT_WHITE : col);
            g.setTextDatum(lgfx::middle_left);
            g.drawString(glucoseStr, startX, hCY);

            // Trend arrow — centre placed so left edge is 4 px right of glucose
            int arrowCX = startX + glucoseW + 4 + ARROW_SZ;
            drawTrendArrow(g, g_reading.direction, arrowCX, hCY,
                           pill ? TFT_WHITE : col, ARROW_SZ);

            // Delta (signed), same FONT_LARGE as glucose
            g.setFont(&FONT_LARGE);
            g.setTextSize(1);
            g.setTextColor(pill ? TFT_WHITE : col);
            g.setTextDatum(lgfx::middle_left);
            g.drawString(deltaStr, arrowCX + ARROW_SZ + 4, hCY);
        }
    }

    // ── Header: left column — clock + age ────────────────────────
    // Row 1 (y≈26): Clock in FONT_LARGE (bold, highly readable)
    // y=26 with middle_left places the text top at ~8 px, clearing the
    // display's rounded-corner clip zone (was y=18 → top at ~0 → cutout).
    // Row 2 (y≈56): Age of reading in FONT_SMALL; stale prefix "! " when ≥ 15 min
    // (y=56 ensures no overlap with the ~36 px tall FONT_LARGE clock above it).
    {
        String clk = clockString();
        if (clk.length() > 0) {
            g.setFont(&FONT_LARGE);
            g.setTextSize(1);
            g.setTextColor(COLOR_CLOCK);
            g.setTextDatum(lgfx::middle_left);
            g.drawString(clk, L_X, 26);
        }
    }
    if (g_reading.valid && g_reading.dateMs > 0 && g_ntpSynced) {
        int64_t ageMin = (nowMs - g_reading.dateMs) / 60000LL;
        bool stale = (ageMin >= 15);
        String age = (stale ? "! " : "") + ageLabel(g_reading.dateMs);
        g.setFont(&FONT_SMALL);
        g.setTextSize(1);
        g.setTextColor(stale ? COLOR_AGE_STALE : COLOR_AGE_NORMAL);
        g.setTextDatum(lgfx::middle_left);
        g.drawString(age, L_X, 56);
    }

    // ── Header: vertical rule ──────────────────────────────────────
    g.drawFastVLine(SEP_X, 4, HEADER_H - 8, COLOR_GRAPH_BORDER);

    // ── Header: horizontal separator ──────────────────────────────
    g.drawFastHLine(0, HEADER_H, W, COLOR_GRAPH_BORDER);

    // ── Graph: optional coloured zone fills ────────────────────────
    // Disable by setting GRAPH_ZONE_FILLS 0 in config.h.
#if GRAPH_ZONE_FILLS
    {
        int yHigh = sgvToY(TARGET_HIGH);
        int yLow  = sgvToY(TARGET_LOW);

        // Low zone (below TARGET_LOW): only if TARGET_LOW is above the axis minimum
        if (TARGET_LOW > yAxisMin) {
            g.fillRect(GRAPH_LEFT, yLow, GRAPH_W, GRAPH_BOTTOM - yLow, COLOR_GRAPH_LOW_FILL);
        }
        // Target zone
        g.fillRect(GRAPH_LEFT, yHigh, GRAPH_W, yLow - yHigh, COLOR_GRAPH_TARGET_FILL);
        // High zone (above TARGET_HIGH): only if TARGET_HIGH is below the axis maximum
        if (TARGET_HIGH < yAxisMax) {
            g.fillRect(GRAPH_LEFT, GRAPH_TOP, GRAPH_W, yHigh - GRAPH_TOP, COLOR_GRAPH_HIGH_FILL);
        }
    }
#endif  // GRAPH_ZONE_FILLS

    // ── Graph: horizontal glucose grid lines + Y-axis labels ───────
    // Draw a faint horizontal line every GRAPH_HGRID_STEP mg/dL across the
    // visible Y range, with the glucose value printed to the left.
    // The target boundary lines (orange/red) are drawn afterwards so they
    // always appear on top of the grid.
    {
        const int step = GRAPH_HGRID_STEP;
        // Round up to the first multiple of step at or above yAxisMin
        int firstGrid = ((yAxisMin + step - 1) / step) * step;
        g.setFont(&lgfx::fonts::Font0);
        g.setTextSize(1);
        g.setTextDatum(lgfx::middle_right);
        g.setTextColor(COLOR_GRAPH_AXIS_LABEL);
        for (int v = firstGrid; v <= yAxisMax; v += step) {
            int y = sgvToY(v);
            if (y >= GRAPH_TOP && y <= GRAPH_BOTTOM) {
                g.drawFastHLine(GRAPH_LEFT, y, GRAPH_W, COLOR_GRAPH_HGRID_LINE);
                g.drawString(String(v), GRAPH_LEFT - 1, y);
            }
        }
    }

    // ── Graph: target boundary lines ──────────────────────────────
    // High line = orange (approaching high risk), Low line = red (low risk)
    drawDashedHLine(g, GRAPH_LEFT, sgvToY(TARGET_HIGH), GRAPH_W,
                    COLOR_GRAPH_HIGH_LINE,
                    GRAPH_TARGET_DASH_LEN,
                    GRAPH_TARGET_GAP_LEN);
    drawDashedHLine(g, GRAPH_LEFT, sgvToY(TARGET_LOW), GRAPH_W,
                    COLOR_GRAPH_LOW_LINE,
                    GRAPH_TARGET_DASH_LEN,
                    GRAPH_TARGET_GAP_LEN);

    // ── Graph: X-axis 10-minute grid lines + time labels ──────────
    if (g_ntpSynced && nowMs > 0) {
        // Draw 10-minute vertical lines and label them as HH:MM.
        // Labels are culled when too close to keep the axis readable.
        const int64_t tenMinMs = 600000LL;
        // Start at the latest 10-minute boundary before "now"
        // (e.g. 19:37 -> 19:30), then step backward by 10 minutes.
        int64_t firstTenMin = (nowMs / tenMinMs) * tenMinMs;

        g.setFont(&lgfx::fonts::Font0);
        g.setTextSize(1);
        g.setTextColor(COLOR_GRAPH_AXIS_LABEL);
        g.setTextDatum(lgfx::top_center);

        int lastLabelX = GRAPH_RIGHT + 1000;
        const int minLabelSpacingPx = 28;

        for (int64_t t = firstTenMin; t >= oldestMs; t -= tenMinMs) {
            int x = msToX(t);
            if (x > GRAPH_LEFT && x < GRAPH_RIGHT) {
                drawDashedVLine(g, x, GRAPH_TOP, GRAPH_H,
                                COLOR_GRAPH_10MIN_LINE,
                                GRAPH_10MIN_DASH_LEN,
                                GRAPH_10MIN_GAP_LEN);

                if ((lastLabelX - x) >= minLabelSpacingPx &&
                    x > GRAPH_LEFT + 8 && x < GRAPH_RIGHT - 8) {
                    time_t secs = (time_t)(t / 1000LL);
                    struct tm ti;
                    localtime_r(&secs, &ti);
                    char tbuf[6];
                    strftime(tbuf, sizeof(tbuf), "%H:%M", &ti);
                    g.drawString(tbuf, x, GRAPH_BOTTOM + 2);
                    lastLabelX = x;
                }
            }
        }
    }

    // ── Graph: scatter dots (oldest → newest so latest renders on top) ─
    for (int i = g_graphHistoryLen - 1; i >= 0; i--) {
        int     sgv  = g_graphHistory[i].sgv;
        int64_t msTs = g_graphHistory[i].dateMs;
        if (sgv <= 0) continue;
        if (nowMs > 0 && msTs < oldestMs) continue;

        int px = (nowMs > 0) ? msToX(msTs)
                             : (GRAPH_LEFT + i * GRAPH_W / max(1, g_graphHistoryLen));
        int py = sgvToY(sgv);
        if (px < GRAPH_LEFT || px > GRAPH_RIGHT) continue;
        if (py < GRAPH_TOP  || py > GRAPH_BOTTOM) continue;

        uint16_t dotColor = glucoseColorGraph(sgv, dataMin, dataMax);
        g.fillCircle(px, py, 4, 0x0000);  // black border ring
        g.fillCircle(px, py, 3, dotColor);
    }
}
#endif  // SHOW_GRAPH

// =================================================================
// Display rendering
// =================================================================
static void renderDisplay() {
    const int W = lcd.width();
    const int H = lcd.height();

#if SHOW_GRAPH
    // Graph mode: compact header + history chart.
    // Both the sprite path and the direct-LCD fallback call the same
    // renderGraphMode() helper to avoid duplicating the drawing code.
    if (canvas.width() == 0) {
        lcd.fillScreen(COLOR_BACKGROUND);
        renderGraphMode(lcd, W, H);
        return;
    }
    canvas.fillSprite(COLOR_BACKGROUND);
    renderGraphMode(canvas, W, H);
    canvas.pushSprite(0, 0);
    return;
#endif  // SHOW_GRAPH

    // ── Simple mode (SHOW_GRAPH = 0): original large-value layout ──

    // Layout constants – all corner elements use CORNER_MARGIN from each edge
    // so that rounded-corner clipping on the physical display does not cut text.
    //   Row 1 – clock (top-center, FreeSansBold18pt)        y = 22
    //   Row 2 – [glucose | arrow | delta] all at y = 95, all Font7
    //           Full group width ≈ 75 + 48 + 75 = 198 px → centred on 240 px
    //           glucose: middle_center at GLUCOSE_X (55)
    //           arrow:   centre at ARROW_CX (130) — 10 px gap after 3-digit glucose
    //           delta:   middle_left at DELTA_X (158)
    //   Row 3 – age of reading (FreeSans12pt, centre)        y = 175
    //   Row 4 – stale-data warning (FreeSans9pt, if any)     y = 207
    //   Row 5 – WiFi / NS status bar (Font0, bottom)         y = H-10
    const int CORNER_MARGIN = 16;   // horizontal inset from left/right edges
    const int Y_CLOCK   = 22;
    const int Y_GLUCOSE = 95;
    const int Y_AGE     = 175;
    const int Y_STALE   = 207;
    const int Y_STATUS  = H - 10;
    // Horizontal positions for the inline glucose row
    const int GLUCOSE_X = 55;    // middle_center x for glucose number (Font7 ~75 px wide)
    const int ARROW_CX  = 130;   // centre of trend arrow (SZ=24 → spans 106-154); extra gap after glucose
    const int DELTA_X   = 158;   // middle_left x for delta text (Font7, after arrow)

    String clk = clockString();

    // If the off-screen sprite could not be allocated, fall back to
    // rendering directly on the LCD (with some flicker accepted).
    if (canvas.width() == 0) {
        lcd.fillScreen(COLOR_BACKGROUND);

        // ---- Clock (top-center, prominent) -------------------------
        if (clk.length() > 0) {
            lcd.setFont(&FONT_LARGE);
            lcd.setTextSize(1);
            lcd.setTextColor(COLOR_CLOCK);
            lcd.setTextDatum(lgfx::top_center);
            lcd.drawString(clk, W / 2, Y_CLOCK);
        }

        if (!g_reading.valid) {
            lcd.setFont(&FONT_LARGE);
            lcd.setTextColor(COLOR_ERROR);
            lcd.setTextDatum(lgfx::middle_center);
            String msg = g_error.length() > 0 ? g_error : "Aguarde...";
            lcd.drawString(msg, W / 2, H / 2);
        } else {
            uint16_t col = glucoseColor(g_reading.sgv);
            bool pill = glucoseNeedsPill(g_reading.sgv);
            uint16_t fgCol = pill ? TFT_WHITE : col;

            // ---- Large glucose value (Font7, shifted left) ----------
            lcd.setFont(&lgfx::fonts::Font7);
            String glucoseStr = String(g_reading.sgv);
            String deltaStr = (g_reading.delta >= 0 ? "+" : "")
                              + String(g_reading.delta);
            int glucoseW = lcd.textWidth(glucoseStr);
            int deltaW = lcd.textWidth(deltaStr);
            int groupLeft = min(GLUCOSE_X - glucoseW / 2, ARROW_CX - 24);
            int groupRight = max(max(GLUCOSE_X + glucoseW / 2, ARROW_CX + 24), DELTA_X + deltaW);
            if (pill) {
                int textH = lcd.fontHeight();
                int groupH = max(textH, 48);
                lcd.fillRoundRect(groupLeft - 12, Y_GLUCOSE - groupH / 2 - 6,
                                  (groupRight - groupLeft) + 30, groupH + 12,
                                  10, glucosePillColor(g_reading.sgv));
            }
            lcd.setTextColor(fgCol);
            lcd.setTextDatum(lgfx::middle_center);
            lcd.drawString(glucoseStr, GLUCOSE_X, Y_GLUCOSE);

            // ---- Trend arrow – graphical, centre of row -------------
            drawTrendArrow(lcd, g_reading.direction, ARROW_CX, Y_GLUCOSE, fgCol);

            // ---- Delta – right of arrow, Font7 (same size as glucose) --
            lcd.setFont(&lgfx::fonts::Font7);
            lcd.setTextColor(fgCol);
            lcd.setTextDatum(lgfx::middle_left);
            lcd.drawString(deltaStr, DELTA_X, Y_GLUCOSE);

            // ---- Age of reading (FreeSans12pt, alert colour when stale) ----
            String age = ageLabel(g_reading.dateMs);
            if (age.length() > 0) {
                bool stale = false;
                if (g_ntpSynced && g_reading.dateMs > 0) {
                    struct timeval tv;
                    gettimeofday(&tv, nullptr);
                    int64_t nowMs  = (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000LL;
                    stale = ((nowMs - g_reading.dateMs) / 60000LL) >= 15;
                }
                lcd.setFont(&FONT_MEDIUM);
                lcd.setTextColor(stale ? COLOR_AGE_STALE : COLOR_AGE_NORMAL);
                lcd.setTextDatum(lgfx::middle_center);
                lcd.drawString(age, W / 2, Y_AGE);
            }

            if (g_ntpSynced && g_reading.dateMs > 0) {
                struct timeval tv;
                gettimeofday(&tv, nullptr);
                int64_t nowMs  = (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000LL;
                int64_t ageMin = (nowMs - g_reading.dateMs) / 60000LL;
                if (ageMin >= 15) {
                    lcd.setFont(&FONT_SMALL);
                    lcd.setTextColor(COLOR_STALE_WARN);
                    lcd.setTextDatum(lgfx::middle_center);
                    lcd.drawString("! DADO ANTIGO !", W / 2, Y_STALE);
                }
            }
        }

        bool wifiOk = (WiFi.status() == WL_CONNECTED);
        lcd.setFont(&lgfx::fonts::Font0);
        lcd.setTextSize(1);
        lcd.setTextDatum(lgfx::bottom_left);
        lcd.setTextColor(wifiOk ? COLOR_STATUS_OK : COLOR_STATUS_ERR);
        lcd.drawString(wifiOk ? "WiFi OK" : "WiFi ERR", CORNER_MARGIN, Y_STATUS);
        lcd.setTextDatum(lgfx::bottom_right);
        lcd.setTextColor(g_reading.valid ? COLOR_STATUS_OK : COLOR_STATUS_ERR);
        lcd.drawString(g_reading.valid ? "NS: OK" : "NS: ERR", W - CORNER_MARGIN, Y_STATUS);
        return;
    }

    canvas.fillSprite(COLOR_BACKGROUND);

    // ---- Clock (top-center, prominent) --------------------------
    if (clk.length() > 0) {
        canvas.setFont(&FONT_LARGE);
        canvas.setTextSize(1);
        canvas.setTextColor(COLOR_CLOCK);
        canvas.setTextDatum(lgfx::top_center);
        canvas.drawString(clk, W / 2, Y_CLOCK);
    }

    if (!g_reading.valid) {
        // ---- Error / loading state ------------------------------
        canvas.setTextColor(COLOR_ERROR);
        canvas.setFont(&FONT_LARGE);
        canvas.setTextDatum(lgfx::middle_center);
        String msg = g_error.length() > 0 ? g_error : "Aguarde...";
        canvas.drawString(msg, W / 2, H / 2);
    } else {
        uint16_t col = glucoseColor(g_reading.sgv);
        bool pill = glucoseNeedsPill(g_reading.sgv);
        uint16_t fgCol = pill ? TFT_WHITE : col;

        // ---- Large glucose value (Font7, shifted left) ----------
        canvas.setFont(&lgfx::fonts::Font7);
        String glucoseStr = String(g_reading.sgv);
        String deltaStr = (g_reading.delta >= 0 ? "+" : "")
                          + String(g_reading.delta);
        int glucoseW = canvas.textWidth(glucoseStr);
        int deltaW = canvas.textWidth(deltaStr);
        int groupLeft = min(GLUCOSE_X - glucoseW / 2, ARROW_CX - 24);
        int groupRight = max(max(GLUCOSE_X + glucoseW / 2, ARROW_CX + 24), DELTA_X + deltaW);
        if (pill) {
            int textH = canvas.fontHeight();
            int groupH = max(textH, 48);
            canvas.fillRoundRect(groupLeft - 12, Y_GLUCOSE - groupH / 2 - 6,
                                 (groupRight - groupLeft) + 30, groupH + 12,
                                 10, glucosePillColor(g_reading.sgv));
        }
        canvas.setTextColor(fgCol);
        canvas.setTextDatum(lgfx::middle_center);
        canvas.drawString(glucoseStr, GLUCOSE_X, Y_GLUCOSE);

        // ---- Trend arrow – graphical, centre of row -------------
        drawTrendArrow(canvas, g_reading.direction, ARROW_CX, Y_GLUCOSE, fgCol);

        // ---- Delta – right of arrow, Font7 (same size as glucose) --
        canvas.setFont(&lgfx::fonts::Font7);
        canvas.setTextColor(fgCol);
        canvas.setTextDatum(lgfx::middle_left);
        canvas.drawString(deltaStr, DELTA_X, Y_GLUCOSE);

        // ---- Age of reading (FreeSans12pt, alert colour when stale) ----
        String age = ageLabel(g_reading.dateMs);
        if (age.length() > 0) {
            bool stale = false;
            if (g_ntpSynced && g_reading.dateMs > 0) {
                struct timeval tv;
                gettimeofday(&tv, nullptr);
                int64_t nowMs  = (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000LL;
                stale = ((nowMs - g_reading.dateMs) / 60000LL) >= 15;
            }
            canvas.setFont(&FONT_MEDIUM);
            canvas.setTextColor(stale ? COLOR_AGE_STALE : COLOR_AGE_NORMAL);
            canvas.setTextDatum(lgfx::middle_center);
            canvas.drawString(age, W / 2, Y_AGE);
        }

        // ---- Stale-data warning (reading older than 15 min) -----
        if (g_ntpSynced && g_reading.dateMs > 0) {
            struct timeval tv;
            gettimeofday(&tv, nullptr);
            int64_t nowMs  = (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000LL;
            int64_t ageMin = (nowMs - g_reading.dateMs) / 60000LL;
            if (ageMin >= 15) {
                canvas.setFont(&FONT_SMALL);
                canvas.setTextColor(COLOR_STALE_WARN);
                canvas.setTextDatum(lgfx::middle_center);
                canvas.drawString("! DADO ANTIGO !", W / 2, Y_STALE);
            }
        }
    }

    // ---- Status bar (bottom) ------------------------------------
    bool wifiOk = (WiFi.status() == WL_CONNECTED);
    canvas.setFont(&lgfx::fonts::Font0);
    canvas.setTextSize(1);

    canvas.setTextDatum(lgfx::bottom_left);
    canvas.setTextColor(wifiOk ? COLOR_STATUS_OK : COLOR_STATUS_ERR);
    canvas.drawString(wifiOk ? "WiFi OK" : "WiFi ERR", CORNER_MARGIN, Y_STATUS);

    canvas.setTextDatum(lgfx::bottom_right);
    canvas.setTextColor(g_reading.valid ? COLOR_STATUS_OK : COLOR_STATUS_ERR);
    canvas.drawString(g_reading.valid ? "NS: OK" : "NS: ERR", W - CORNER_MARGIN, Y_STATUS);

    canvas.pushSprite(0, 0);
}

// Splash screen while booting.
static void showSplash(const String& msg) {
    // Direct-LCD fallback when sprite is unavailable.
    if (canvas.width() == 0) {
        lcd.fillScreen(COLOR_BACKGROUND);
        lcd.setFont(&FONT_LARGE);
        lcd.setTextColor(COLOR_SPLASH_TITLE);
        lcd.setTextDatum(lgfx::middle_center);
        lcd.drawString("NSOverlay", lcd.width() / 2, lcd.height() / 2 - 20);
        lcd.setFont(&FONT_SMALL);
        lcd.setTextColor(COLOR_SPLASH_ACCENT);
        lcd.drawString(msg, lcd.width() / 2, lcd.height() / 2 + 20);
        return;
    }
    canvas.fillSprite(COLOR_BACKGROUND);
    canvas.setFont(&FONT_LARGE);
    canvas.setTextColor(COLOR_SPLASH_TITLE);
    canvas.setTextDatum(lgfx::middle_center);
    canvas.drawString("NSOverlay", lcd.width() / 2, lcd.height() / 2 - 20);
    canvas.setFont(&FONT_SMALL);
    canvas.setTextColor(COLOR_SPLASH_ACCENT);
    canvas.drawString("XIAO ESP32C3", lcd.width() / 2, lcd.height() / 2 + 12);
    canvas.setTextColor(COLOR_SPLASH_DIM);
    canvas.drawString(msg, lcd.width() / 2, lcd.height() / 2 + 36);
    canvas.pushSprite(0, 0);
}

// =================================================================
// WiFi + NTP
// =================================================================
static void connectWiFi() {
    Serial.print("[WiFi] Connecting to ");
    Serial.println(WIFI_SSID);

    WiFi.mode(WIFI_STA);
    WiFi.disconnect(true);  // reset any in-progress connection attempt
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    showSplash("Conectando WiFi...");

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 20000UL) {
        delay(500);
        Serial.print('.');
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.print("[WiFi] Connected, IP: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("[WiFi] Connection failed (timeout)");
    }
}

static void syncNTP() {
    Serial.println("[NTP] Syncing time...");
    configTime(NTP_GMT_OFFSET_SEC, NTP_DST_OFFSET_SEC, NTP_SERVER);
    struct tm timeinfo;
    unsigned long start = millis();
    while (!getLocalTime(&timeinfo) && millis() - start < 6000UL) {
        delay(200);
    }
    g_ntpSynced = getLocalTime(&timeinfo);
    if (g_ntpSynced) {
        char buf[32];
        strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &timeinfo);
        Serial.print("[NTP] Synced: ");
        Serial.println(buf);
    } else {
        Serial.println("[NTP] Sync failed");
    }
}

// =================================================================
// Arduino entry points
// =================================================================
void setup() {
    Serial.begin(115200);
    Serial.println("\n[NSOverlay] Booting...");

    // --- Initialise display -------------------------------------
    lcd.init();
    lcd.setRotation(1);        // landscape 280×240; corrects the −90° physical offset
    lcd.setBrightness(LCD_BRIGHTNESS);

    // Hardware sanity check: paint the panel blue briefly.
    // If you never see a blue flash, the LCD wiring needs checking
    // (SPI pins, CS, DC or RST not connected correctly).
    lcd.fillScreen(lcd.color565(0, 100, 255));
    delay(300);
    lcd.fillScreen(COLOR_BACKGROUND);

    // --- Create off-screen sprite (eliminates flicker) ----------
    canvas.setColorDepth(8);   // 8-bit saves ~67 KB vs 16-bit ~134 KB; fits ESP32C3 heap after WiFi
    void* spriteBuf = canvas.createSprite(lcd.width(), lcd.height());
    if (!spriteBuf) {
        Serial.println("[DISPLAY] WARNING: sprite alloc failed – check free heap");
    } else {
        Serial.println("[DISPLAY] Sprite OK");
    }

    showSplash("Iniciando...");
    delay(800);

    // --- WiFi ---------------------------------------------------
    connectWiFi();

    // --- NTP ----------------------------------------------------
    if (WiFi.status() == WL_CONNECTED) {
        showSplash("Sincronizando hora...");
        syncNTP();
    }

    // --- First data fetch ---------------------------------------
    if (WiFi.status() == WL_CONNECTED) {
        showSplash("Buscando glicemia...");
        fetchNightscout();
    }

    renderDisplay();
    g_lastFetchMs = millis();
}

void loop() {
    unsigned long now = millis();

    // Reconnect if WiFi dropped
    if (WiFi.status() != WL_CONNECTED) {
        connectWiFi();
        if (WiFi.status() == WL_CONNECTED && !g_ntpSynced) {
            syncNTP();
        }
    }

    // Periodic data refresh
    if (now - g_lastFetchMs >= REFRESH_INTERVAL_MS) {
        Serial.print("[Loop] Refresh at ");
        Serial.print(now / 1000);
        Serial.println("s");
        fetchNightscout();
        // Stamp AFTER the fetch completes so that a slow or failed request
        // (e.g. a 2-minute TLS hang) does not cause the next loop iteration
        // to immediately trigger another fetch because the elapsed time already
        // exceeds REFRESH_INTERVAL_MS.
        g_lastFetchMs = millis();
    }

    // Re-render every second so the age label stays up-to-date
    renderDisplay();
    delay(1000);
}
