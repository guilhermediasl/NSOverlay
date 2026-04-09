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
            cfg.rgb_order        = false;
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
    static const int GRAPH_MAX_POINTS = 50;  // buffer size; comfortably holds up to ~4 h at 5-min intervals
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

// 16-bit colour based on glucose level.
static uint16_t glucoseColor(int sgv) {
    if (sgv > 0 && sgv < TARGET_LOW)   return COLOR_GLUCOSE_LOW;
    if (sgv > TARGET_HIGH)             return COLOR_GLUCOSE_HIGH;
    return                                    COLOR_GLUCOSE_OK;
}

// Human-readable age of the last reading (in Portuguese).
static String ageLabel(int64_t dateMs) {
    if (!g_ntpSynced || dateMs == 0) return "";

    struct timeval tv;
    gettimeofday(&tv, nullptr);
    int64_t nowMs   = (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000LL;
    int64_t ageMs   = nowMs - dateMs;

    if (ageMs < 0)           return "agora";
    int ageSec = (int)(ageMs / 1000LL);
    if (ageSec <  60)        return String(ageSec) + "s atras";
    int ageMin = ageSec / 60;
    if (ageMin <  60)        return String(ageMin) + " min atras";
    return String(ageMin / 60) + " h atras";
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
        // In graph mode request enough readings to fill the time window
        // (one reading every 5 min, plus a small buffer).
        // In simple mode only 2 readings are needed (current + previous delta).
#if SHOW_GRAPH
        const int fetchCount = min(GRAPH_MAX_POINTS, (GRAPH_MINUTES / 5) + 4);
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

        // In graph mode the response can be 36+ entries (~10 KB JSON).
        // Use DynamicJsonDocument (heap) to avoid overflowing the 8 KB task stack.
        // In simple mode 2 KB on the stack is fine.
#if SHOW_GRAPH
        DynamicJsonDocument doc(12288);
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
            r.delta = r.sgv - (int)(arr[1]["sgv"] | 0);
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
// Draws the compact header + glucose history scatter plot.
// Called from renderDisplay() when SHOW_GRAPH = 1.
// Accepts an LGFXBase& so it works with both the sprite canvas and
// direct-LCD fallback — the same pattern used by drawTrendArrow.
#if SHOW_GRAPH
static void renderGraphMode(lgfx::LGFXBase& g, int W, int H) {
    const int CORNER_MARGIN = 8;

    // ── Layout ─────────────────────────────────────────────────────
    // Row 1 (y≈10) : clock (left)  +  WiFi / NS status (right)
    // Row 2 (y≈38) : glucose number + trend arrow + delta (centred)
    // Row 3 (y≈60) : age of reading (right-aligned) + stale "!" (left)
    // Row 4        : separator line
    // Graph area   : remaining vertical space above X-axis labels
    // X-axis row   : hour labels at the very bottom
    const int Y_STATUS   = 11;   // middle datum for row 1
    const int Y_GLUCOSE  = 37;   // middle datum for row 2
    const int Y_AGE      = 59;   // middle datum for row 3
    const int GRAPH_TOP  = 70;   // top pixel of the plot area
    // Leave 14 px at the bottom for X-axis tick labels (Font0 ≈ 8 px)
    const int GRAPH_BOTTOM = H - 14;
    const int GRAPH_LEFT   = 26; // left margin for Y-axis labels ("180 " in Font0)
    const int GRAPH_RIGHT  = W - 3;
    const int GRAPH_H      = GRAPH_BOTTOM - GRAPH_TOP;
    const int GRAPH_W      = GRAPH_RIGHT - GRAPH_LEFT;

    // Y mapping: glucose value → pixel row inside the plot area.
    // We clamp to [SGV_MIN, SGV_MAX] so out-of-range readings still appear.
    const int SGV_MIN = 40;
    const int SGV_MAX = 320;
    auto sgvToY = [&](int sgv) -> int {
        if (sgv < SGV_MIN) sgv = SGV_MIN;
        if (sgv > SGV_MAX) sgv = SGV_MAX;
        return GRAPH_TOP + (SGV_MAX - sgv) * GRAPH_H / (SGV_MAX - SGV_MIN);
    };

    // Current time
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    int64_t nowMs    = g_ntpSynced
                       ? ((int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000LL)
                       : 0;
    int64_t windowMs = (int64_t)GRAPH_MINUTES * 60000LL;
    int64_t oldestMs = nowMs - windowMs;

    // X mapping: Unix-ms timestamp → pixel column inside the plot area.
    auto msToX = [&](int64_t ms) -> int {
        if (windowMs <= 0) return GRAPH_LEFT;
        return GRAPH_LEFT + (int)((ms - oldestMs) * (int64_t)GRAPH_W / windowMs);
    };

    // ── Row 1: clock (left) + WiFi/NS indicators (right) ──────────
    String clk = clockString();
    if (clk.length() > 0) {
        g.setFont(&FONT_SMALL);
        g.setTextSize(1);
        g.setTextColor(COLOR_CLOCK);
        g.setTextDatum(lgfx::middle_left);
        g.drawString(clk, CORNER_MARGIN, Y_STATUS);
    }
    {
        bool wifiOk = (WiFi.status() == WL_CONNECTED);
        g.setFont(&lgfx::fonts::Font0);
        g.setTextSize(1);
        // "NS:OK" or "NS:ERR" — rightmost
        g.setTextDatum(lgfx::middle_right);
        g.setTextColor(g_reading.valid ? COLOR_STATUS_OK : COLOR_STATUS_ERR);
        g.drawString(g_reading.valid ? "NS:OK" : "NS:ERR", W - CORNER_MARGIN, Y_STATUS);
        // "WiFi" indicator just to the left of NS label
        g.setTextColor(wifiOk ? COLOR_STATUS_OK : COLOR_STATUS_ERR);
        g.drawString(wifiOk ? "WiFi " : "WiFi!", W - CORNER_MARGIN - 42, Y_STATUS);
    }

    // ── Row 2: glucose value + trend arrow + delta ─────────────────
    if (!g_reading.valid) {
        g.setFont(&FONT_MEDIUM);
        g.setTextColor(COLOR_ERROR);
        g.setTextDatum(lgfx::middle_center);
        g.drawString(g_error.length() > 0 ? g_error : "Aguarde...", W / 2, Y_GLUCOSE);
    } else {
        uint16_t col = glucoseColor(g_reading.sgv);

        // Glucose: FONT_LARGE centred slightly left of midpoint so arrow + delta fit.
        // Group visual centre ≈ W/2.  Estimated widths at FONT_LARGE:
        //   glucose "XXX" ≈ 45 px → anchor middle_center at W/2 - 38
        //   arrow   sz=14 → 28 px wide → centre at W/2 + 3
        //   delta "+XX"   ≈ 30 px → middle_left at W/2 + 18
        g.setFont(&FONT_LARGE);
        g.setTextColor(col);
        g.setTextDatum(lgfx::middle_center);
        g.drawString(String(g_reading.sgv), W / 2 - 38, Y_GLUCOSE);

        drawTrendArrow(g, g_reading.direction, W / 2 + 3, Y_GLUCOSE, col, 14);

        String deltaStr = (g_reading.delta >= 0 ? "+" : "") + String(g_reading.delta);
        g.setFont(&FONT_SMALL);
        g.setTextColor(col);
        g.setTextDatum(lgfx::middle_left);
        g.drawString(deltaStr, W / 2 + 19, Y_GLUCOSE);
    }

    // ── Row 3: age of reading + stale alert ────────────────────────
    if (g_reading.valid && g_reading.dateMs > 0 && g_ntpSynced) {
        String age = ageLabel(g_reading.dateMs);
        int64_t ageMin = (nowMs - g_reading.dateMs) / 60000LL;
        bool stale = (ageMin >= 15);
        g.setFont(&lgfx::fonts::Font0);
        g.setTextSize(1);
        g.setTextColor(stale ? COLOR_AGE_STALE : COLOR_AGE_NORMAL);
        g.setTextDatum(lgfx::middle_right);
        g.drawString(age, W - CORNER_MARGIN, Y_AGE);
        if (stale) {
            // Compact stale alert on the left — no space for full banner in graph mode
            g.setTextColor(COLOR_STALE_WARN);
            g.setTextDatum(lgfx::middle_left);
            g.drawString("! OLD", CORNER_MARGIN, Y_AGE);
        }
    }

    // ── Separator between header and graph ─────────────────────────
    g.drawFastHLine(CORNER_MARGIN, GRAPH_TOP - 3,
                    W - CORNER_MARGIN * 2, COLOR_GRAPH_BORDER);

    // ── Graph: coloured zone fills (low / target / high) ───────────
    {
        int yHigh = sgvToY(TARGET_HIGH);
        int yLow  = sgvToY(TARGET_LOW);
        int yTop  = sgvToY(SGV_MAX);      // = GRAPH_TOP
        int yBot  = sgvToY(SGV_MIN);      // = GRAPH_BOTTOM

        // Low zone (below TARGET_LOW): faint red
        g.fillRect(GRAPH_LEFT, yLow, GRAPH_W, yBot - yLow, COLOR_GRAPH_LOW_FILL);
        // Target zone (TARGET_LOW … TARGET_HIGH): faint green
        g.fillRect(GRAPH_LEFT, yHigh, GRAPH_W, yLow - yHigh, COLOR_GRAPH_TARGET_FILL);
        // High zone (above TARGET_HIGH): faint orange
        g.fillRect(GRAPH_LEFT, yTop, GRAPH_W, yHigh - yTop, COLOR_GRAPH_HIGH_FILL);

        // Boundary lines — solid lines at this pixel scale are visually sufficient
        g.drawFastHLine(GRAPH_LEFT, yHigh, GRAPH_W, COLOR_GRAPH_TARGET_LINE);
        g.drawFastHLine(GRAPH_LEFT, yLow,  GRAPH_W, COLOR_GRAPH_TARGET_LINE);
    }

    // ── Graph: Y-axis labels ────────────────────────────────────────
    g.setFont(&lgfx::fonts::Font0);
    g.setTextSize(1);
    g.setTextDatum(lgfx::middle_right);
    // Target boundaries (brighter)
    g.setTextColor(COLOR_GRAPH_AXIS_LABEL);
    g.drawString(String(TARGET_HIGH), GRAPH_LEFT - 2, sgvToY(TARGET_HIGH));
    g.drawString(String(TARGET_LOW),  GRAPH_LEFT - 2, sgvToY(TARGET_LOW));
    // Top / bottom bounds (dimmer)
    g.setTextColor(COLOR_GRAPH_AXIS);
    g.drawString("300", GRAPH_LEFT - 2, sgvToY(300));
    g.drawString(" 40", GRAPH_LEFT - 2, sgvToY(40));

    // ── Graph: graph area border ────────────────────────────────────
    g.drawRect(GRAPH_LEFT, GRAPH_TOP, GRAPH_W, GRAPH_H, COLOR_GRAPH_BORDER);

    // ── Graph: X-axis hour ticks + labels ──────────────────────────
    if (g_ntpSynced && nowMs > 0) {
        g.setFont(&lgfx::fonts::Font0);
        g.setTextSize(1);
        g.setTextColor(COLOR_GRAPH_AXIS_LABEL);
        g.setTextDatum(lgfx::top_center);
        const int64_t hourMs = 3600000LL;
        // First whole hour after the left edge of the window
        int64_t firstHour = (oldestMs / hourMs + 1) * hourMs;
        for (int64_t t = firstHour; t <= nowMs + 60000LL; t += hourMs) {
            int x = msToX(t);
            if (x > GRAPH_LEFT + 2 && x < GRAPH_RIGHT - 2) {
                // Tick
                g.drawFastVLine(x, GRAPH_BOTTOM, 4, COLOR_GRAPH_AXIS);
                // Label
                time_t secs = (time_t)(t / 1000LL);
                struct tm ti;
                localtime_r(&secs, &ti);
                char tbuf[6];
                strftime(tbuf, sizeof(tbuf), "%H:%M", &ti);
                g.drawString(tbuf, x, GRAPH_BOTTOM + 2);
            }
        }
    }

    // ── Graph: glucose scatter dots ─────────────────────────────────
    // Iterate oldest → newest (index g_graphHistoryLen-1 → 0).
    // Paint older dots first so the latest reading renders on top.
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

        uint16_t dotColor = glucoseColor(sgv);
        if (i == 0) {
            // Latest reading: larger dot + outer ring for emphasis
            g.fillCircle(px, py, 5, dotColor);
            g.drawCircle(px, py, 7, dotColor);
        } else {
            g.fillCircle(px, py, 3, dotColor);
        }
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

            // ---- Large glucose value (Font7, shifted left) ----------
            lcd.setFont(&lgfx::fonts::Font7);
            lcd.setTextColor(col);
            lcd.setTextDatum(lgfx::middle_center);
            lcd.drawString(String(g_reading.sgv), GLUCOSE_X, Y_GLUCOSE);

            // ---- Trend arrow – graphical, centre of row -------------
            drawTrendArrow(lcd, g_reading.direction, ARROW_CX, Y_GLUCOSE, col);

            // ---- Delta – right of arrow, Font7 (same size as glucose) --
            String deltaStr = (g_reading.delta >= 0 ? "+" : "")
                              + String(g_reading.delta);
            lcd.setFont(&lgfx::fonts::Font7);
            lcd.setTextColor(col);
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

        // ---- Large glucose value (Font7, shifted left) ----------
        canvas.setFont(&lgfx::fonts::Font7);
        canvas.setTextColor(col);
        canvas.setTextDatum(lgfx::middle_center);
        canvas.drawString(String(g_reading.sgv), GLUCOSE_X, Y_GLUCOSE);

        // ---- Trend arrow – graphical, centre of row -------------
        drawTrendArrow(canvas, g_reading.direction, ARROW_CX, Y_GLUCOSE, col);

        // ---- Delta – right of arrow, Font7 (same size as glucose) --
        String deltaStr = (g_reading.delta >= 0 ? "+" : "")
                          + String(g_reading.delta);
        canvas.setFont(&lgfx::fonts::Font7);
        canvas.setTextColor(col);
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
