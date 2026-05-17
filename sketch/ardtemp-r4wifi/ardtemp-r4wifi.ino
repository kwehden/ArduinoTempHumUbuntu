#include <WiFiS3.h>
#include <Wire.h>
#include <Arduino_LED_Matrix.h>
#include "secrets.h"  // WIFI_SSID, WIFI_PASSWORD, SERVICE_HOST, SERVICE_PORT

#define HS300X_ADDR 0x44
#define BOARD_ID    "r4wifi"
#define QUEUE_SIZE  60   // ~2 minutes of readings buffered during outages

ArduinoLEDMatrix matrix;

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

static unsigned long lastReadingMs = 0;

void setup() {
  matrix.begin();
  Wire1.begin();
  Wire1.setClock(100000);
  // Wait up to 5 s for USB CDC host (debug serial); proceed without it.
  Serial.begin(9600);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 5000);
  ensureWiFi();
  delay(50);
}

void loop() {
  ensureWiFi();
  stepAlert();

  if (millis() - lastReadingMs < 2000) return;
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

  // Flush any queued readings first, then post current one
  int cmd = flushQueue();
  int c   = postReading(t10, h10);
  if (c == -1) {
    enqueue(t10, h10);   // service unreachable — buffer for later
  } else {
    if (c) cmd = c;
  }

  if      (cmd == 'F') startAlert();
  else if (cmd == 'D') stopAlert();
}
