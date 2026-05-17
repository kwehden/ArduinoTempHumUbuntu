# Arduino UNO Q Temperature & Humidity Monitor for Ubuntu GNOME

A temperature and humidity monitor that reads from a Modulino Thermo sensor via an Arduino UNO Q and displays live readings in the Ubuntu GNOME top panel. Includes a rapid-change alert system that flashes both the panel indicator and the board's 8×13 LED matrix until dismissed.

**Example use case:** Monitoring the input air temperature of a CPU water cooler radiator.

---

## How it works

```
Modulino Thermo (HS3003)
        │ Qwiic
Arduino UNO Q  ──── USB ────  Python indicator  ───  GNOME top panel
  - reads I²C                  - parses JSON            - label display
  - prints JSON                - spike detection        - C/F toggle
  - drives LED matrix          - sends F / D cmds       - dismiss alert
```

1. The Arduino sketch reads temperature and humidity from the HS3003 sensor every 2 seconds and prints a JSON line over the RouterBridge serial port (`{"t":26.1,"h":37.0}`).
2. The Python indicator reads that serial stream, displays the reading in the GNOME top panel, and detects rapid temperature changes.
3. When a ±5 °C change is detected within a 2-minute window, the indicator flashes the panel label and sends an `'F'` byte to the Arduino, which starts a continuous wave animation on the 8×13 LED matrix.
4. Clicking **Dismiss alert** in the indicator menu stops the panel flash and sends `'D'` to the Arduino, which clears the matrix and resumes normal operation.

---

## Hardware

| Component | Description |
|-----------|-------------|
| [Arduino UNO Q](https://store.arduino.cc/products/uno-q) | STM32U585 (Cortex-M33) + ESP32-S3 RouterBridge. FQBN: `arduino:zephyr:unoq` |
| [Modulino Thermo](https://store.arduino.cc/products/modulino-thermo) | HS3003 temperature/humidity sensor, I²C address `0x44`, product code ABX00103 |
| Qwiic cable | JST-SH 4-pin, connects Modulino Thermo to the UNO Q's Qwiic connector |

The UNO Q's Qwiic connector maps to `Wire1` (`i2c4`, pins PD12/PD13) in the Zephyr device tree.

---

## Software dependencies

### Arduino side

- [Arduino CLI](https://arduino.github.io/arduino-cli/) with the `arduino:zephyr` core (version 0.55.0+)
- `Arduino_LED_Matrix` library — bundled with the Zephyr core, no separate install needed

### Python side (Ubuntu)

```bash
sudo apt install python3-serial python3-gi gir1.2-ayatanaappindicator3-0.1
```

---

## Build & Flash

```bash
arduino-cli compile \
  --fqbn arduino:zephyr:unoq \
  --output-dir /tmp/ardtemp-build \
  sketch/ardtemp

# Flash via remoteocd (UNO Q uses ADB-over-USB, not standard serial upload)
REMOTEOCD=~/.arduino15/packages/arduino/tools/remoteocd/0.0.4-rc.4/remoteocd
ADB=~/.arduino15/packages/arduino/tools/adb/32.0.0/adb
CFG=~/.arduino15/packages/arduino/hardware/zephyr/0.55.0/variants/arduino_uno_q_stm32u585xx/flash_sketch.cfg

$REMOTEOCD upload \
  --adb-path $ADB \
  -s <ADB_SERIAL> \
  -f $CFG \
  /tmp/ardtemp-build/ardtemp.ino.elf-zsk.bin
```

Find your ADB serial number:
```bash
~/.arduino15/packages/arduino/tools/adb/32.0.0/adb devices
```

---

## Install the indicator

### Configuration

The indicator reads `~/.config/ardtemp/ardtemp.conf` at startup. Copy the example and edit as needed:

```bash
mkdir -p ~/.config/ardtemp
cp conf/ardtemp.conf.example ~/.config/ardtemp/ardtemp.conf
```

Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `board` | `uno-q` | `board_id` from ardconfig — used to auto-detect the serial port |
| `ardconfig_path` | _(empty)_ | Path to your [ardconfig](https://github.com/kwehden/ardconfig) installation. When set, the indicator runs `ardconfig-detect --json` on every reconnect to find the board's current `/dev/ttyACM*` port automatically. Leave empty to use the `device` fallback instead. |
| `device` | `/dev/ttyACM0` | Fallback serial device if auto-detection is disabled or finds nothing |
| `baud` | `9600` | Must match `Serial.begin()` in the sketch |
| `spike_threshold_c` | `5.0` | °C swing within `spike_window_s` to trigger the alert |
| `spike_window_s` | `120` | Look-back window for spike detection, in seconds |
| `reconnect_delay` | `5` | Seconds between reconnect attempts after a disconnect |

All settings are optional — the indicator runs with defaults if no config file exists.

### Run manually

```bash
python3 indicator/ardtemp_indicator.py
```

### Autostart on login (GNOME)

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/ardtemp.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=ArdTemp Indicator
Exec=/usr/bin/python3 /path/to/indicator/ardtemp_indicator.py
X-GNOME-Autostart-enabled=true
NoDisplay=false
EOF
```

---

## Usage

Click the temperature reading in the panel to open the menu:

| Menu item | Action |
|-----------|--------|
| **Switch to °F / °C** | Toggle display unit. The board always reports in °C; conversion is done in the indicator. |
| **Dismiss alert** | Grayed out when no alert is active. Stops the panel flash and sends `'D'` to the board to clear the LED matrix. |
| **Test flash** | Trigger the alert manually to verify the LED matrix wave animation and panel flash without waiting for a real spike. |
| **Quit** | Exit the indicator. |

### Alert logic

An alert fires when the max minus the min temperature across the **last 2 minutes** of readings reaches **±5 °C**. Threshold and window are constants at the top of `ardtemp_indicator.py`:

```python
SPIKE_THRESHOLD_C = 5.0   # degrees C
SPIKE_WINDOW_S    = 120   # seconds
```

---

## Support matrix

| Component | Tested | Notes |
|-----------|--------|-------|
| Arduino UNO Q | ✅ | Zephyr core 0.55.0 |
| Modulino Thermo (HS3003) | ✅ | Address 0x44, Qwiic |
| Ubuntu 24.04 LTS | ✅ | GNOME Shell 50.x |
| Ubuntu 22.04 LTS | ✅ | GNOME Shell 43.x |
| Python 3.10 | ✅ | |
| Python 3.12 | ✅ | |
| AyatanaAppIndicator3 0.5.94 | ✅ | GTK3 bindings |

Not tested (but likely works):

| Component | Notes |
|-----------|-------|
| Generic HS300x breakout | Any board with HS3003 at 0x44 on Wire1 should work |
| Arduino UNO R4 WiFi | Different serial / upload mechanism; LED matrix API is identical |
| Pop!\_OS / Fedora (GNOME) | AppIndicator extension required |

---

## Known issues & hard-won lessons

These are the non-obvious problems encountered building this project.

### 1. Inspect your Qwiic connector pins

The Modulino Thermo's JST-SH connector pins are 1 mm pitch and bend easily. A bent pin produces *intermittent* I²C failures — the sensor scans fine sometimes, fails unpredictably at others. Before debugging software, visually inspect both ends of the cable and both connectors on the board under good light. Straighten with fine-point tweezers.

### 2. Both Qwiic connectors on Modulino Thermo are electrically identical

The board silkscreen labels one end `HS3003` and the other `ABX00103`. These are just component/product labels — both connectors are electrically identical pass-through Qwiic ports. Either one connects to the UNO Q.

### 3. The Arduino_HS300x library sends the wrong measurement trigger

The official `Arduino_HS300x` library's `_measurementReq()` sends a 1-byte write of `0x00` to the sensor. On the Zephyr I²C driver, this data byte causes the HS3003 to enter **command mode**, making it unresponsive to subsequent reads.

The correct trigger per the HS3003 datasheet is a **0-byte write** (address only — START + address + STOP, no data bytes). The fix is to remove the `_wire->write((uint8_t)0)` line from `_measurementReq()` in the library:

```cpp
// In Arduino_HS300x/src/HS300x.cpp, _measurementReq():
_wire->beginTransmission(HS300X_ADR);
// _wire->write((uint8_t)0);  ← remove this line
if (_wire->endTransmission(true) != 0) { ... }
```

This sketch avoids the library entirely and implements the trigger directly.

### 4. `Serial.print(float)` outputs `"ovf"` on Zephyr for valid floats

The Arduino Zephyr core's `Print::printFloat()` outputs `"ovf"` when a float value exceeds ~4.29 billion. A bug or ABI mismatch in the Zephyr build causes valid room-temperature floats (~26.0) to be passed as astronomically large numbers to `printFloat()`.

**Workaround:** Use integer arithmetic throughout and format manually:

```cpp
int32_t t10 = (int32_t)tRaw * 1650 / 16383 - 400;  // tenths of °C
Serial.print(t10 / 10);
Serial.print(".");
Serial.print(abs(t10 % 10));
```

### 5. `Serial.begin()` must be called in `loop()`, not `setup()`

On the UNO Q, `Serial` is a `BridgeMonitor` — an RPC channel through the ESP32-S3 RouterBridge. `Serial.begin(baud)` makes an RPC call to check whether a USB CDC host is connected, and returns `false` if not. It does not block. Calling it in `setup()` before the host is ready returns `false` and leaves `Serial` non-functional for the rest of the sketch lifetime.

**Pattern:**
```cpp
void loop() {
  if (!Serial.begin(9600)) { delay(500); return; }
  // safe to use Serial here
}
```

### 6. Opening `/dev/ttyACM0` from Python asserts DTR, resetting the board

Standard `pyserial.Serial()` opens the port with DTR asserted by default. On the UNO Q this triggers MCUboot, which reverts to the last confirmed sketch if the new one hasn't been confirmed — you end up running old firmware.

**Workaround:** Use `pyserial` normally (it works fine) but be aware that the board resets on port open. The indicator adds a 2-second sleep after opening to let the RouterBridge establish the serial bridge before reading.

### 7. The AyatanaAppIndicator3 deprecation warning is benign

```
libayatana-appindicator-WARNING: libayatana-appindicator is deprecated.
Please use libayatana-appindicator-glib in newly written code.
```

This warning targets C library authors, not Python users. The GTK3 Python bindings (`gir1.2-ayatanaappindicator3-0.1`) continue to work correctly on Ubuntu 22.04 and 24.04. The GLib-based replacement (`libayatana-appindicator-glib`) does not yet support `com.canonical.dbusmenu`, which is required by Ubuntu's GNOME Shell AppIndicator extension for menus to function.

### 8. Wire1 is the Qwiic connector on UNO Q, not Wire

The UNO Q device tree maps I²C buses as:
- `Wire` → `i2c2` (standard Arduino header pins A4/A5)
- `Wire1` → `i2c4` (Qwiic connector, pins PD12/PD13) ← **use this**
- `Wire2` → `i2c3`

Using `Wire` instead of `Wire1` will find nothing at 0x44.

### 9. remoteocd, not arduino-cli upload

The UNO Q does not support `arduino-cli upload` via serial. It uses `remoteocd` (OpenOCD over ADB) for flashing. The upload command pattern is:

```bash
remoteocd upload --adb-path $ADB -s $SERIAL -f flash_sketch.cfg binary.elf-zsk.bin
```

Note the binary extension is `.elf-zsk.bin` (not `.bin` or `.hex`).

---

## License

MIT
