#!/usr/bin/env python3
import gi
gi.require_version('AyatanaAppIndicator3', '0.1')
gi.require_version('Gtk', '3.0')
from gi.repository import AyatanaAppIndicator3 as AppIndicator3, Gtk, GLib

import json
import threading
import time
import serial
from collections import deque

DEVICE            = '/dev/ttyACM0'
BAUD              = 9600
RECONNECT_DELAY   = 5
GUIDE             = "-88.8°F  100%"
SPIKE_THRESHOLD_C = 5.0   # °C change to trigger alert
SPIKE_WINDOW_S    = 120   # look-back window in seconds

indicator    = None
toggle_item  = None
dismiss_item = None

unit          = 'C'
alert_active  = False
flash_state   = False
flash_timer   = None
current_t_c   = None
current_h     = None
temp_history  = deque()

_serial_port = None
_serial_lock = threading.Lock()


# --- unit helpers -----------------------------------------------------------

def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0

def format_reading(t_c, h):
    if unit == 'F':
        return f"{c_to_f(t_c):.1f}°F  {h:.0f}%"
    return f"{t_c:.1f}°C  {h:.0f}%"


# --- serial write -----------------------------------------------------------

def send_serial_cmd(cmd: bytes):
    with _serial_lock:
        if _serial_port and _serial_port.is_open:
            try:
                _serial_port.write(cmd)
            except Exception:
                pass


# --- alert ------------------------------------------------------------------

def flash_tick():
    global flash_state
    if not alert_active:
        return False
    flash_state = not flash_state
    if flash_state:
        indicator.set_label("⚠ TEMP SPIKE", GUIDE)
    elif current_t_c is not None:
        indicator.set_label(format_reading(current_t_c, current_h), GUIDE)
    else:
        indicator.set_label("⚠ TEMP SPIKE", GUIDE)
    return True

def start_alert():
    global alert_active, flash_timer, flash_state
    if alert_active:
        return
    alert_active = True
    flash_state  = False
    dismiss_item.set_sensitive(True)
    indicator.set_status(AppIndicator3.IndicatorStatus.ATTENTION)
    flash_timer = GLib.timeout_add(500, flash_tick)
    send_serial_cmd(b'F')

def dismiss_alert():
    global alert_active, flash_timer, flash_state
    alert_active = False
    flash_state  = False
    if flash_timer is not None:
        GLib.source_remove(flash_timer)
        flash_timer = None
    dismiss_item.set_sensitive(False)
    indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
    if current_t_c is not None:
        indicator.set_label(format_reading(current_t_c, current_h), GUIDE)
    send_serial_cmd(b'D')


# --- spike detection --------------------------------------------------------

def check_spike(t_c):
    now = time.monotonic()
    temp_history.append((now, t_c))
    cutoff = now - SPIKE_WINDOW_S
    while temp_history and temp_history[0][0] < cutoff:
        temp_history.popleft()
    if len(temp_history) < 2:
        return False
    temps = [t for _, t in temp_history]
    return (max(temps) - min(temps)) >= SPIKE_THRESHOLD_C


# --- reading pipeline (GLib main thread via idle_add) -----------------------

def process_reading(t_c, h):
    global current_t_c, current_h
    current_t_c = t_c
    current_h   = h
    if check_spike(t_c) and not alert_active:
        start_alert()
    if not alert_active:
        indicator.set_label(format_reading(t_c, h), GUIDE)
    return False

def set_no_sensor():
    global temp_history
    temp_history.clear()   # stale history would trigger spurious alerts on reconnect
    if alert_active:
        dismiss_alert()
    indicator.set_label("no sensor", GUIDE)
    return False


# --- serial -----------------------------------------------------------------

def read_serial():
    global _serial_port
    while True:
        try:
            with serial.Serial(DEVICE, BAUD, timeout=3) as ser:
                with _serial_lock:
                    _serial_port = ser
                time.sleep(2)
                while True:
                    raw = ser.readline()
                    if not raw:
                        continue
                    line = raw.decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        GLib.idle_add(process_reading, data['t'], data['h'])
                    except (json.JSONDecodeError, KeyError):
                        pass
        except (serial.SerialException, OSError):
            with _serial_lock:
                _serial_port = None
            GLib.idle_add(set_no_sensor)
            time.sleep(RECONNECT_DELAY)


# --- menu -------------------------------------------------------------------

def on_toggle_unit(_item):
    global unit
    unit = 'F' if unit == 'C' else 'C'
    toggle_item.set_label(f"Switch to °{'F' if unit == 'C' else 'C'}")
    if current_t_c is not None and not alert_active:
        indicator.set_label(format_reading(current_t_c, current_h), GUIDE)

def build_menu():
    global toggle_item, dismiss_item
    menu = Gtk.Menu()

    toggle_item = Gtk.MenuItem(label="Switch to °F")
    toggle_item.connect("activate", on_toggle_unit)
    menu.append(toggle_item)

    dismiss_item = Gtk.MenuItem(label="Dismiss alert")
    dismiss_item.set_sensitive(False)
    dismiss_item.connect("activate", lambda _: dismiss_alert())
    menu.append(dismiss_item)

    test_item = Gtk.MenuItem(label="Test flash")
    test_item.connect("activate", lambda _: start_alert())
    menu.append(test_item)

    menu.append(Gtk.SeparatorMenuItem())

    quit_item = Gtk.MenuItem(label="Quit")
    quit_item.connect("activate", lambda _: Gtk.main_quit())
    menu.append(quit_item)

    menu.show_all()
    return menu


# --- main -------------------------------------------------------------------

def main():
    global indicator
    indicator = AppIndicator3.Indicator.new(
        "ardtemp",
        "weather-clear-symbolic",
        AppIndicator3.IndicatorCategory.HARDWARE,
    )
    indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
    indicator.set_attention_icon("dialog-warning")
    indicator.set_label("starting…", GUIDE)
    indicator.set_menu(build_menu())

    threading.Thread(target=read_serial, daemon=True).start()
    Gtk.main()


if __name__ == "__main__":
    main()
