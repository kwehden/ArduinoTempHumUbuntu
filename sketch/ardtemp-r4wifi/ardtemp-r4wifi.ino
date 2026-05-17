#include <Wire.h>
#include <Arduino_LED_Matrix.h>

#define HS300X_ADDR 0x44

ArduinoLEDMatrix matrix;

// Wait ms milliseconds, return true immediately if 'D' (dismiss) is received.
static bool waitOrDismiss(unsigned long ms) {
  unsigned long start = millis();
  while (millis() - start < ms) {
    if (Serial.available() > 0 && (char)Serial.read() == 'D') return true;
    delay(5);
  }
  return false;
}

static void flashMatrix() {
  uint8_t frame[8 * 12];  // R4 WiFi matrix: 8 rows x 12 cols

  while (true) {
    memset(frame, 0, sizeof(frame));
    for (int row = 0; row < 8; row++) {
      for (int col = 0; col < 12; col++) frame[row * 12 + col] = 1;
      matrix.loadPixels(frame, sizeof(frame));
      if (waitOrDismiss(50)) { matrix.clear(); return; }
    }
    if (waitOrDismiss(100)) { matrix.clear(); return; }

    for (int row = 7; row >= 0; row--) {
      for (int col = 0; col < 12; col++) frame[row * 12 + col] = 0;
      matrix.loadPixels(frame, sizeof(frame));
      if (waitOrDismiss(50)) { matrix.clear(); return; }
    }
    if (waitOrDismiss(100)) { matrix.clear(); return; }
  }
}

static void idleWait(unsigned long ms) {
  unsigned long start = millis();
  while (millis() - start < ms) {
    if (Serial.available() > 0) {
      char cmd = (char)Serial.read();
      if (cmd == 'F') flashMatrix();
    }
    delay(10);
  }
}

void setup() {
  matrix.begin();
  Wire1.begin();
  Wire1.setClock(100000);
  Serial.begin(9600);
  // Wait up to 5 s for USB CDC host; proceed without it so the board
  // doesn't hang if powered on before the indicator is running.
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 5000);
  delay(50);
}

void loop() {
  // Measurement trigger: 0-byte write (address only, per HS300x datasheet)
  Wire1.beginTransmission(HS300X_ADDR);
  if (Wire1.endTransmission() != 0) {
    Serial.println("{\"error\":\"trigger_failed\"}");
    idleWait(2000);
    return;
  }

  delay(40);

  byte n = Wire1.requestFrom((uint8_t)HS300X_ADDR, (uint8_t)4);
  if (n != 4) {
    Serial.println("{\"error\":\"read_failed\"}");
    idleWait(2000);
    return;
  }

  uint16_t hRaw = ((uint16_t)Wire1.read() << 8) | Wire1.read();
  uint16_t tRaw = ((uint16_t)Wire1.read() << 8) | Wire1.read();

  hRaw &= 0x3FFF;
  tRaw >>= 2;

  // Integer arithmetic to avoid float print issues; units = tenths
  int32_t t10 = (int32_t)tRaw * 1650 / 16383 - 400;
  int32_t h10 = (int32_t)hRaw * 1000 / 16383;

  Serial.print("{\"t\":");
  Serial.print(t10 / 10);
  Serial.print(".");
  Serial.print(abs(t10 % 10));
  Serial.print(",\"h\":");
  Serial.print(h10 / 10);
  Serial.print(".");
  Serial.print(h10 % 10);
  Serial.println("}");

  idleWait(2000);
}
