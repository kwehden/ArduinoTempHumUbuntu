#include <WiFiS3.h>
#include <Wire.h>
#include <ArduinoGraphics.h>
#include <Arduino_LED_Matrix.h>
#include <Arduino_Modulino.h>
#include "secrets.h"  // WIFI_SSID, WIFI_PASSWORD, SERVICE_HOST, SERVICE_PORT

#define HS300X_ADDR      0x44
#define BOARD_ID         "r4wifi"
#define QUEUE_SIZE       60        // ~2 minutes of readings buffered during outages
#define READ_INTERVAL_MS 300000UL  // 5 min — filament-storage mode

// PLA filament humidity thresholds (% RH)
#define PLA_OK_H      15.0f   // ideal storage
#define PLA_WARN_H    25.0f   // caution — matches HUMIDITY_HIGH_PCT service default
#define PLA_DANGER_H  40.0f   // significant degradation risk

ArduinoLEDMatrix matrix;
ModulinoPixels   leds;

// ---------------------------------------------------------------------------
// Circular buffer for readings queued during service outages
// ---------------------------------------------------------------------------

struct Reading { int32_t t10; int32_t h10; };
static Reading  _queue[QUEUE_SIZE];
static int      _qHead = 0, _qTail = 0, _qCount = 0;

static void enqueue(int32_t t10, int32_t h10) {
  if (_qCount >= QUEUE_SIZE) {            // drop oldest on overflow
    _qHead = (_qHead + 1) % QUEUE_SIZE;
    _qCount--;
  }
  _queue[_qTail] = {t10, h10};
  _qTail = (_qTail + 1) % QUEUE_SIZE;
  _qCount++;
}


// ---------------------------------------------------------------------------
// Non-blocking LED matrix wave animation (state machine)
// ---------------------------------------------------------------------------

enum AlertState { IDLE, WAVE_IN, WAVE_PAUSE_IN, WAVE_OUT, WAVE_PAUSE_OUT };
static AlertState  alertState  = IDLE;
static int         waveRow     = 0;
static uint8_t     waveFrame[8 * 12];
static unsigned long waveTimer = 0;

static void stepAlert() {
  if (alertState == IDLE) return;
  unsigned long stepMs =
    (alertState == WAVE_PAUSE_IN || alertState == WAVE_PAUSE_OUT) ? 100 : 50;
  if (millis() - waveTimer < stepMs) return;
  waveTimer = millis();

  switch (alertState) {
    case WAVE_IN:
      for (int c = 0; c < 12; c++) waveFrame[waveRow * 12 + c] = 1;
      matrix.loadPixels(waveFrame, sizeof(waveFrame));
      if (++waveRow >= 8) alertState = WAVE_PAUSE_IN;
      break;
    case WAVE_PAUSE_IN:
      waveRow = 7;
      alertState = WAVE_OUT;
      break;
    case WAVE_OUT:
      for (int c = 0; c < 12; c++) waveFrame[waveRow * 12 + c] = 0;
      matrix.loadPixels(waveFrame, sizeof(waveFrame));
      if (--waveRow < 0) alertState = WAVE_PAUSE_OUT;
      break;
    case WAVE_PAUSE_OUT:
      memset(waveFrame, 0, sizeof(waveFrame));
      waveRow = 0;
      alertState = WAVE_IN;
      break;
    default: break;
  }
}

static void startAlert() {
  memset(waveFrame, 0, sizeof(waveFrame));
  waveRow    = 0;
  waveTimer  = millis();
  alertState = WAVE_IN;
}

static void stopAlert() {
  alertState = IDLE;
  matrix.clear();
}

static void displayHumidity(float h) {
  if (alertState != IDLE) return;
  char buf[6];
  snprintf(buf, sizeof(buf), "%d%%", (int)(h + 0.5f));
  matrix.beginDraw();
  matrix.stroke(0xFFFFFFFF);
  matrix.textFont(Font_4x6);
  matrix.beginText(0, 1, 0xFFFFFF);
  matrix.print(buf);
  matrix.endText();
  matrix.endDraw();
}


// ---------------------------------------------------------------------------
// Modulino Pixels — humidity bar for PLA filament storage
// ---------------------------------------------------------------------------

static float _lastH         = 0.0f;
static bool  _humidityAlert = false;

static void updatePixels(float h) {
  if (_humidityAlert) {
    for (int i = 0; i < 8; i++) leds.set(i, RED, 25);
    leds.show();
    return;
  }
  // bar fill: 0–50% RH → 0–8 lit pixels
  int lit = (int)(h / 50.0f * 8.0f + 0.5f);
  if (lit > 8) lit = 8;
  ModulinoColor col =
      (h < PLA_OK_H)     ? GREEN :
      (h < PLA_WARN_H)   ? YELLOW :
      (h < PLA_DANGER_H) ? ModulinoColor(255, 80, 0) :
                           RED;
  for (int i = 0; i < 8; i++) {
    if (i < lit) leds.set(i, col, 25);
    else         leds.clear(i);
  }
  leds.show();
}


// ---------------------------------------------------------------------------
// WiFi
// ---------------------------------------------------------------------------

static void ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long deadline = millis() + 15000;
  while (WiFi.status() != WL_CONNECTED && millis() < deadline) delay(200);
}


// ---------------------------------------------------------------------------
// HTTP POST /reading  →  returns pending command char, 0 if none, -1 on fail
// ---------------------------------------------------------------------------

static int postReading(int32_t t10, int32_t h10) {
  WiFiClient client;
  if (!client.connect(SERVICE_HOST, SERVICE_PORT)) return -1;

  char body[80];
  snprintf(body, sizeof(body),
    "{\"board_id\":\"" BOARD_ID "\",\"t\":%ld.%d,\"h\":%ld.%d}",
    (long)(t10 / 10), abs((int)(t10 % 10)),
    (long)(h10 / 10), (int)(h10 % 10));

  client.print("POST /reading HTTP/1.1\r\n");
  client.print("Host: "); client.print(SERVICE_HOST); client.print("\r\n");
  client.print("Content-Type: application/json\r\n");
  client.print("Content-Length: "); client.print(strlen(body)); client.print("\r\n");
  client.print("Connection: close\r\n\r\n");
  client.print(body);

  // Read response (wait up to 3 s)
  char resp[192] = {};
  int pos = 0;
  unsigned long deadline = millis() + 3000;
  while (millis() < deadline && pos < 191) {
    while (client.available() && pos < 191) resp[pos++] = client.read();
    if (!client.connected() && !client.available()) break;
  }
  client.stop();

  char* p = strstr(resp, "\"cmd\":\"");
  if (p && (p[7] == 'F' || p[7] == 'D')) return p[7];
  return 0;
}


// ---------------------------------------------------------------------------
// Flush queued readings; return last command received (0 if none)
// ---------------------------------------------------------------------------

static int flushQueue() {
  int cmd = 0;
  while (_qCount > 0) {
    int c = postReading(_queue[_qHead].t10, _queue[_qHead].h10);
    if (c == -1) return cmd;   // still unreachable — stop flushing
    if (c)       cmd = c;
    _qHead = (_qHead + 1) % QUEUE_SIZE;
    _qCount--;
  }
  return cmd;
}


// ---------------------------------------------------------------------------
// Setup / loop
// ---------------------------------------------------------------------------

static unsigned long lastReadingMs = -READ_INTERVAL_MS; // fire immediately on first loop
static int           _postFailures  = 0;

void setup() {
  matrix.begin();
  // Modulino.begin() initialises Wire1 at 100 kHz and unlocks the bus
  Modulino.begin();
  leds.begin();
  leds.clear();
  leds.show();
  // Wait up to 5 s for USB CDC host (debug serial); proceed without it.
  Serial.begin(9600);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 5000);
  ensureWiFi();
  delay(50);
  displayHumidity(0);
}

void loop() {
  ensureWiFi();
  stepAlert();

  if (millis() - lastReadingMs < READ_INTERVAL_MS) return;
  lastReadingMs = millis();

  // Measurement trigger: 0-byte write (address only, per HS300x datasheet)
  Wire1.beginTransmission(HS300X_ADDR);
  if (Wire1.endTransmission() != 0) {
    enqueue(-9999, 0);   // sentinel for failed read; service will store it
    return;
  }
  delay(40);

  if (Wire1.requestFrom((uint8_t)HS300X_ADDR, (uint8_t)4) != 4) {
    enqueue(-9999, 0);
    return;
  }

  uint16_t hRaw = ((uint16_t)Wire1.read() << 8) | Wire1.read();
  uint16_t tRaw = ((uint16_t)Wire1.read() << 8) | Wire1.read();
  hRaw &= 0x3FFF;
  tRaw >>= 2;
  int32_t t10 = (int32_t)tRaw * 1650 / 16383 - 400;
  int32_t h10 = (int32_t)hRaw * 1000 / 16383;
  _lastH = h10 / 10.0f;

  // Flush any queued readings first, then post current one
  int cmd = flushQueue();
  int c   = postReading(t10, h10);
  if (c == -1) {
    enqueue(t10, h10);   // service unreachable — buffer for later
    if (++_postFailures >= 5) {
      WiFi.disconnect();   // force real reconnect on next ensureWiFi()
      _postFailures = 0;
    }
  } else {
    _postFailures = 0;
    if (c) cmd = c;
  }

  if      (cmd == 'F') startAlert();
  else if (cmd == 'D') stopAlert();
  else if (cmd == 'H') _humidityAlert = true;
  else if (cmd == 'N') _humidityAlert = false;
  updatePixels(_lastH);
  displayHumidity(_lastH);
}
