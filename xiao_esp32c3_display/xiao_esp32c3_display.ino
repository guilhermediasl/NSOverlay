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
 * Wiring (see config.h for GPIO numbers)
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
 * 1. Open config.h and fill in WIFI_SSID, WIFI_PASSWORD,
 *    NIGHTSCOUT_URL, and API_SECRET.
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

// ---- Project config ---------------------------------------------
#include "config.h"

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
            cfg.freq_write  = 40000000;
            cfg.freq_read   = 16000000;
            cfg.spi_3wire   = true;   // write-only (no MISO)
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

#if GRAPH_ENABLED
// Maximum number of historical readings held in RAM.
// +2 gives a small safety margin beyond the requested window.
#define GRAPH_MAX_POINTS  ((GRAPH_HISTORY_MINUTES) / 5 + 2)
static GlucoseReading g_history[GRAPH_MAX_POINTS];
static int            g_historyCount = 0;
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
static const char* trendArrow(const String& dir) {
    if (dir == "DoubleUp")      return "^^";
    if (dir == "SingleUp")      return "^";
    if (dir == "FortyFiveUp")   return "/";
    if (dir == "Flat")          return "->";
    if (dir == "FortyFiveDown") return "\\";
    if (dir == "SingleDown")    return "v";
    if (dir == "DoubleDown")    return "vv";
    return "?";
}

// 16-bit colour based on glucose level.
static uint16_t glucoseColor(int sgv) {
    if (sgv > 0 && sgv < TARGET_LOW)   return lcd.color565(220,  60,  60);  // red
    if (sgv > TARGET_HIGH)             return lcd.color565(255, 150,   0);  // orange
    return                                    lcd.color565( 60, 210,  80);  // green
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

// =================================================================
// Nightscout fetch
// =================================================================
static bool fetchNightscout() {
    if (WiFi.status() != WL_CONNECTED) {
        g_error = "WiFi desconectado";
        return false;
    }

    WiFiClientSecure client;
    // TLS certificate verification is disabled for simplicity on a maker
    // device on a trusted home network.  For stronger security, remove
    // setInsecure() and instead call client.setCACert(<PEM string>) with
    // the root CA certificate of your Nightscout host.
    client.setInsecure();

    HTTPClient http;
#if GRAPH_ENABLED
    int count = GRAPH_MAX_POINTS;
#else
    int count = 2;
#endif
    String url = String(NIGHTSCOUT_URL) + "/api/v1/entries.json?count=" + String(count);
    http.begin(client, url);
    http.setTimeout(8000);

    if (strlen(API_SECRET) > 0) {
        http.addHeader("api-secret", sha1Hex(String(API_SECRET)));
    }

    int code = http.GET();
    if (code != HTTP_CODE_OK) {
        g_error = "HTTP " + String(code);
        http.end();
        return false;
    }

    String body = http.getString();
    http.end();

    // Each Nightscout entry is ~250-300 bytes of JSON.  Allocate on the
    // heap to avoid overflowing the stack.
    DynamicJsonDocument doc(count * 400);
    DeserializationError err = deserializeJson(doc, body);
    if (err || !doc.is<JsonArray>()) {
        g_error = "JSON invalido";
        return false;
    }

    JsonArray arr = doc.as<JsonArray>();
    if (arr.size() == 0) {
        g_error = "Sem leituras";
        return false;
    }

#if GRAPH_ENABLED
    // Store all returned readings (newest first, matching Nightscout order).
    g_historyCount = 0;
    for (int i = 0; i < (int)arr.size() && i < GRAPH_MAX_POINTS; i++) {
        GlucoseReading r;
        r.sgv       = arr[i]["sgv"]       | 0;
        r.direction = arr[i]["direction"].as<String>();
        r.dateMs    = arr[i]["date"]      | (int64_t)0;
        r.valid     = (r.sgv > 0);
        g_history[i] = r;
        g_historyCount++;
    }
    g_reading = g_history[0];
    if (g_historyCount >= 2) {
        g_reading.delta = g_history[0].sgv - g_history[1].sgv;
    }
#else
    GlucoseReading r;
    r.sgv       = arr[0]["sgv"]       | 0;
    r.direction = arr[0]["direction"].as<String>();
    r.dateMs    = arr[0]["date"]      | (int64_t)0;
    r.valid     = (r.sgv > 0);
    if (arr.size() >= 2) {
        r.delta = r.sgv - (int)(arr[1]["sgv"] | 0);
    }
    g_reading = r;
#endif

    g_error = "";
    return true;
}

// =================================================================
// Glucose history graph
// =================================================================
#if GRAPH_ENABLED

// Map a glucose value (mg/dL) to a Y pixel inside the plot area.
// Higher glucose → lower Y (higher on screen).
static int glucoseToY(int sgv, int plotY, int plotH) {
    if (sgv < GRAPH_Y_MIN) sgv = GRAPH_Y_MIN;
    if (sgv > GRAPH_Y_MAX) sgv = GRAPH_Y_MAX;
    float frac = (float)(sgv - GRAPH_Y_MIN) / (float)(GRAPH_Y_MAX - GRAPH_Y_MIN);
    return plotY + plotH - 1 - (int)(frac * (float)(plotH - 1));
}

// Draw the glucose history graph occupying [graphTop, graphBottom) px.
//
// Layout intent
// -------------
//   • Older readings sit to the LEFT, current time is anchored to the
//     RIGHT edge as a bright vertical line.
//   • Dots (readings) appear to the LEFT of that line, so the visual
//     gap between the last dot and the "now" line makes data staleness
//     immediately obvious.
//   • When GRAPH_SHOW_LABELS is 0 the axis margins collapse and the
//     plot area expands to fill the freed space.
static void drawGraph(int graphTop, int graphBottom) {
    if (!g_ntpSynced || g_historyCount == 0) return;

    const int W      = LCD_WIDTH;
    const int graphH = graphBottom - graphTop;

    // Margins – shrink when labels are suppressed to widen the plot.
#if GRAPH_SHOW_LABELS
    const int marginLeft   = 28;
    const int marginBottom = 14;
#else
    const int marginLeft   = 3;
    const int marginBottom = 3;
#endif
    const int marginTop   = 2;
    const int marginRight = 4;

    // Plot area (pixel coordinates inside the canvas)
    const int plotX = marginLeft;
    const int plotY = graphTop + marginTop;
    const int plotW = W - marginLeft - marginRight;
    const int plotH = graphH - marginTop - marginBottom;
    if (plotW <= 0 || plotH <= 0) return;

    // Dark background for the plot
    canvas.fillRect(plotX, plotY, plotW, plotH, canvas.color565(10, 10, 10));

    // Target-range band (subtle green fill)
    {
        int yLow  = glucoseToY(TARGET_LOW,  plotY, plotH);
        int yHigh = glucoseToY(TARGET_HIGH, plotY, plotH);
        if (yLow > yHigh) {   // sanity (yLow > yHigh because Y increases downward)
            canvas.fillRect(plotX, yHigh, plotW, yLow - yHigh,
                            canvas.color565(0, 35, 0));
        }
    }

    // Horizontal grid lines at the target boundaries
    canvas.drawFastHLine(plotX, glucoseToY(TARGET_LOW,  plotY, plotH),
                         plotW, canvas.color565(40, 80, 40));
    canvas.drawFastHLine(plotX, glucoseToY(TARGET_HIGH, plotY, plotH),
                         plotW, canvas.color565(40, 80, 40));

    // Time window: left edge = (now - history window), right edge = now
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    int64_t nowMs    = (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000LL;
    int64_t windowMs = (int64_t)GRAPH_HISTORY_MINUTES * 60000LL;
    int64_t startMs  = nowMs - windowMs;

    // Draw data dots (oldest → newest so newer ones paint on top)
    for (int i = g_historyCount - 1; i >= 0; i--) {
        if (!g_history[i].valid) continue;
        int64_t t = g_history[i].dateMs;
        if (t < startMs || t > nowMs) continue;

        int x = plotX + (int)((t - startMs) * (int64_t)plotW / windowMs);
        int y = glucoseToY(g_history[i].sgv, plotY, plotH);
        canvas.fillCircle(x, y, 2, glucoseColor(g_history[i].sgv));
    }

    // Vertical "now" line at the right edge of the plot
    canvas.drawFastVLine(plotX + plotW - 1, plotY, plotH,
                         canvas.color565(180, 180, 180));

    // ---- Axis labels (optional) ----------------------------------
#if GRAPH_SHOW_LABELS
    canvas.setFont(&lgfx::fonts::Font0);
    canvas.setTextSize(1);

    // Y-axis: glucose values, right-aligned just left of the plot
    canvas.setTextColor(canvas.color565(110, 110, 110));
    canvas.setTextDatum(lgfx::middle_right);
    canvas.drawString(String(TARGET_LOW),  plotX - 2,
                      glucoseToY(TARGET_LOW,  plotY, plotH));
    canvas.drawString(String(TARGET_HIGH), plotX - 2,
                      glucoseToY(TARGET_HIGH, plotY, plotH));
    if (GRAPH_Y_MAX >= 300) {
        canvas.drawString("300", plotX - 2, glucoseToY(300, plotY, plotH));
    }

    // X-axis: relative time ticks, top-centre below the plot
    int xAxisY = plotY + plotH + 1;
    canvas.setTextDatum(lgfx::top_center);

    // Helper: draw one X tick + label at `minutesAgo` minutes before now
    auto drawXTick = [&](int minutesAgo, const char* label) {
        int64_t t = nowMs - (int64_t)minutesAgo * 60000LL;
        if (t < startMs) return;
        int x = plotX + (int)((t - startMs) * (int64_t)plotW / windowMs);
        canvas.drawFastVLine(x, plotY + plotH, 3, canvas.color565(70, 70, 70));
        canvas.drawString(label, x, xAxisY);
    };

    if (GRAPH_HISTORY_MINUTES >= 120) drawXTick(120, "-2h");
    if (GRAPH_HISTORY_MINUTES >=  90) drawXTick( 90, "-90m");
    if (GRAPH_HISTORY_MINUTES >=  60) drawXTick( 60, "-1h");
    if (GRAPH_HISTORY_MINUTES >=  30) drawXTick( 30, "-30m");

    // "now" label at the right edge, right-aligned
    canvas.setTextDatum(lgfx::top_right);
    canvas.drawString("now", plotX + plotW + marginRight - 1, xAxisY);
#endif  // GRAPH_SHOW_LABELS
}
#endif  // GRAPH_ENABLED

// =================================================================
// Display rendering
// =================================================================
static void renderDisplay() {
    const int W = LCD_WIDTH;   // 240
    const int H = LCD_HEIGHT;  // 280

    canvas.fillSprite(TFT_BLACK);

    // When the graph is enabled we compact the info area upward to
    // make room.  The constants below switch between the two layouts.
#if GRAPH_ENABLED
    const int Y_HEADER  =   6;
    const int Y_GLUCOSE =  60;
    const int Y_UNITS   =  90;
    const int Y_DELTA   = 112;
    const int Y_AGE     = 134;
    const int Y_STALE   = 150;
    const int GRAPH_TOP = 163;
    const int GRAPH_BOT = 265;
#else
    const int Y_HEADER  =   8;
    const int Y_GLUCOSE = 105;
    const int Y_UNITS   = 135;
    const int Y_DELTA   = 180;
    const int Y_AGE     = 215;
    const int Y_STALE   = 240;
#endif

    // ---- Header -------------------------------------------------
    canvas.setTextColor(lcd.color565(120, 120, 120));
    canvas.setFont(&lgfx::fonts::Font2);
    canvas.setTextSize(1);
    canvas.setTextDatum(lgfx::top_center);
    canvas.drawString("GLICEMIA", W / 2, Y_HEADER);

    if (!g_reading.valid) {
        // ---- Error / loading state ------------------------------
        canvas.setTextColor(TFT_RED);
        canvas.setFont(&lgfx::fonts::Font4);
        canvas.setTextDatum(lgfx::middle_center);
        String msg = g_error.length() > 0 ? g_error : "Aguarde...";
        canvas.drawString(msg, W / 2, H / 2);
    } else {
        uint16_t col = glucoseColor(g_reading.sgv);

        // ---- Large glucose value (7-segment style) --------------
        canvas.setFont(&lgfx::fonts::Font7);   // 48-px 7-seg font
        canvas.setTextColor(col);
        canvas.setTextDatum(lgfx::middle_center);
        // Shift left to leave room for the trend arrow
        canvas.drawString(String(g_reading.sgv), W / 2 - 18, Y_GLUCOSE);

        // ---- Trend arrow ----------------------------------------
        canvas.setFont(&lgfx::fonts::Font4);   // 26-px
        canvas.setTextColor(col);
        canvas.setTextDatum(lgfx::middle_right);
        canvas.drawString(trendArrow(g_reading.direction), W - 6, Y_GLUCOSE);

        // ---- Units label ----------------------------------------
        canvas.setFont(&lgfx::fonts::Font2);
        canvas.setTextColor(lcd.color565(140, 140, 140));
        canvas.setTextDatum(lgfx::top_center);
        canvas.drawString("mg/dL", W / 2 - 18, Y_UNITS);

        // ---- Delta ----------------------------------------------
        String deltaStr = (g_reading.delta >= 0 ? "+" : "")
                          + String(g_reading.delta) + " mg/dL";
        canvas.setFont(&lgfx::fonts::Font4);
        canvas.setTextColor(lcd.color565(100, 210, 230));
        canvas.setTextDatum(lgfx::middle_center);
        canvas.drawString(deltaStr, W / 2, Y_DELTA);

        // ---- Age of reading -------------------------------------
        String age = ageLabel(g_reading.dateMs);
        if (age.length() > 0) {
            canvas.setFont(&lgfx::fonts::Font2);
            canvas.setTextColor(lcd.color565(120, 120, 120));
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
                canvas.setFont(&lgfx::fonts::Font2);
                canvas.setTextColor(TFT_YELLOW);
                canvas.setTextDatum(lgfx::middle_center);
                canvas.drawString("! DADO ANTIGO !", W / 2, Y_STALE);
            }
        }

#if GRAPH_ENABLED
        drawGraph(GRAPH_TOP, GRAPH_BOT);
#endif
    }

    // ---- Status bar (bottom) ------------------------------------
    bool wifiOk = (WiFi.status() == WL_CONNECTED);
    canvas.setFont(&lgfx::fonts::Font0);
    canvas.setTextSize(1);

    canvas.setTextDatum(lgfx::bottom_left);
    canvas.setTextColor(wifiOk ? TFT_GREEN : TFT_RED);
    canvas.drawString(wifiOk ? "WiFi OK" : "WiFi ERR", 4, H - 2);

    canvas.setTextDatum(lgfx::bottom_right);
    canvas.setTextColor(g_reading.valid ? TFT_GREEN : TFT_RED);
    canvas.drawString(g_reading.valid ? "NS: OK" : "NS: ERR", W - 4, H - 2);

    canvas.pushSprite(0, 0);
}

// Splash screen while booting.
static void showSplash(const String& msg) {
    canvas.fillSprite(TFT_BLACK);
    canvas.setFont(&lgfx::fonts::Font4);
    canvas.setTextColor(TFT_WHITE);
    canvas.setTextDatum(lgfx::middle_center);
    canvas.drawString("NSOverlay", LCD_WIDTH / 2, LCD_HEIGHT / 2 - 20);
    canvas.setFont(&lgfx::fonts::Font2);
    canvas.setTextColor(lcd.color565(100, 210, 230));
    canvas.drawString("XIAO ESP32C3", LCD_WIDTH / 2, LCD_HEIGHT / 2 + 12);
    canvas.setTextColor(lcd.color565(150, 150, 150));
    canvas.drawString(msg, LCD_WIDTH / 2, LCD_HEIGHT / 2 + 36);
    canvas.pushSprite(0, 0);
}

// =================================================================
// WiFi + NTP
// =================================================================
static void connectWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    showSplash("Conectando WiFi...");

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 20000UL) {
        delay(500);
    }
}

static void syncNTP() {
    configTime(NTP_GMT_OFFSET_SEC, NTP_DST_OFFSET_SEC, NTP_SERVER);
    struct tm timeinfo;
    unsigned long start = millis();
    while (!getLocalTime(&timeinfo) && millis() - start < 6000UL) {
        delay(200);
    }
    g_ntpSynced = getLocalTime(&timeinfo);
}

// =================================================================
// Arduino entry points
// =================================================================
void setup() {
    Serial.begin(115200);

    // --- Initialise display -------------------------------------
    lcd.init();
    lcd.setRotation(0);        // portrait, cable at bottom
    lcd.setBrightness(LCD_BRIGHTNESS);
    lcd.fillScreen(TFT_BLACK);

    // --- Create off-screen sprite (eliminates flicker) ----------
    canvas.setColorDepth(16);
    canvas.createSprite(LCD_WIDTH, LCD_HEIGHT);

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
        fetchNightscout();
    }

    // Re-render every second so the age label stays up-to-date
    renderDisplay();
    delay(1000);
}
