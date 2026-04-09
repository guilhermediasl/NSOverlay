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
static void drawTrendArrow(lgfx::LGFXBase& g, const String& dir,
                           int cx, int cy, uint16_t col) {
    const int SZ = 24;  // half-size of arrow bounding box (matches Font7 ~48px height)
    const int HW = 11;  // arrowhead half-width (perpendicular to direction)
    const int T  = 4;   // shaft half-thickness
    const int hw = 8;   // diagonal arrowhead half-width (~HW * 0.7)

    if (dir == "DoubleUp") {
        // Two stacked arrowheads pointing up + short shaft below
        g.fillRect(cx - T, cy, T * 2, SZ / 2, col);
        g.fillTriangle(cx - HW, cy,           cx, cy - SZ * 2 / 3,
                       cx + HW, cy,           col);
        g.fillTriangle(cx - HW, cy - SZ * 2 / 3, cx, cy - SZ * 4 / 3,
                       cx + HW, cy - SZ * 2 / 3, col);
    } else if (dir == "SingleUp") {
        g.fillRect(cx - T, cy, T * 2, SZ, col);
        g.fillTriangle(cx - HW, cy, cx, cy - SZ, cx + HW, cy, col);
    } else if (dir == "FortyFiveUp") {
        // Diagonal arrow pointing upper-right
        // Shaft parallelogram (perpendicular to direction (1,-1) is (1,1))
        g.fillTriangle(cx - SZ + T, cy + SZ + T,
                       cx - SZ - T, cy + SZ - T,
                       cx - T,      cy - T,      col);
        g.fillTriangle(cx - SZ + T, cy + SZ + T,
                       cx + T,      cy + T,
                       cx - T,      cy - T,      col);
        // Arrowhead pointing to upper-right
        g.fillTriangle(cx + SZ, cy - SZ, cx + hw, cy + hw, cx - hw, cy - hw, col);
    } else if (dir == "Flat") {
        g.fillRect(cx - SZ, cy - T, SZ, T * 2, col);
        g.fillTriangle(cx, cy - HW, cx + SZ, cy, cx, cy + HW, col);
    } else if (dir == "FortyFiveDown") {
        // Diagonal arrow pointing lower-right
        // Shaft parallelogram (perpendicular to direction (1,1) is (1,-1))
        g.fillTriangle(cx - SZ + T, cy - SZ - T,
                       cx - SZ - T, cy - SZ + T,
                       cx - T,      cy + T,      col);
        g.fillTriangle(cx - SZ + T, cy - SZ - T,
                       cx + T,      cy - T,
                       cx - T,      cy + T,      col);
        // Arrowhead pointing to lower-right
        g.fillTriangle(cx + SZ, cy + SZ, cx + hw, cy - hw, cx - hw, cy + hw, col);
    } else if (dir == "SingleDown") {
        g.fillRect(cx - T, cy - SZ, T * 2, SZ, col);
        g.fillTriangle(cx - HW, cy, cx, cy + SZ, cx + HW, cy, col);
    } else if (dir == "DoubleDown") {
        // Two stacked arrowheads pointing down + short shaft above
        g.fillRect(cx - T, cy - SZ / 2, T * 2, SZ / 2, col);
        g.fillTriangle(cx - HW, cy,           cx, cy + SZ * 2 / 3,
                       cx + HW, cy,           col);
        g.fillTriangle(cx - HW, cy + SZ * 2 / 3, cx, cy + SZ * 4 / 3,
                       cx + HW, cy + SZ * 2 / 3, col);
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
            delay(2000);
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

        HTTPClient http;
        String url = String(NIGHTSCOUT_URL) + "/api/v1/entries.json?count=2";
        http.begin(client, url);
        http.setTimeout(8000);

        if (strlen(API_SECRET) > 0) {
            http.addHeader("api-secret", sha1Hex(String(API_SECRET)));
        }

        int code = http.GET();
        if (code != HTTP_CODE_OK) {
            g_error = "HTTP " + String(code);
            Serial.print("[NS] HTTP error: ");
            Serial.println(code);
            http.end();
            continue;  // retry
        }

        String body = http.getString();
        http.end();

        StaticJsonDocument<2048> doc;
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
// Display rendering
// =================================================================
static void renderDisplay() {
    const int W = lcd.width();
    const int H = lcd.height();

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
        g_lastFetchMs = now;
        Serial.print("[Loop] Refresh at ");
        Serial.print(now / 1000);
        Serial.println("s");
        fetchNightscout();
    }

    // Re-render every second so the age label stays up-to-date
    renderDisplay();
    delay(1000);
}
