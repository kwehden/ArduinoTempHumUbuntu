#!/usr/bin/env python3
import gi
gi.require_version('AyatanaAppIndicator3', '0.1')
gi.require_version('Gtk', '3.0')
from gi.repository import AyatanaAppIndicator3 as AppIndicator3, Gtk, GLib

import configparser
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

CONFIG_PATH = os.path.expanduser('~/.config/ardtemp/ardtemp.conf')
GUIDE       = "-88.8°F  100%"

# Overridden from config at startup via load_config()
BAUD              = 9600      # kept for reference; unused in HTTP mode
RECONNECT_DELAY   = 5
SPIKE_THRESHOLD_C = 5.0
SPIKE_WINDOW_S    = 120
STALE_THRESHOLD_S = 10        # readings older than this are treated as no-sensor

_cfg = None

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


# --- config -----------------------------------------------------------------

def load_config():
    global _cfg, BAUD, SPIKE_THRESHOLD_C, SPIKE_WINDOW_S, RECONNECT_DELAY
    cp = configparser.ConfigParser()
    cp.read_dict({'ardtemp': {
        'board':             'r4wifi',
        'service_url':       'http://laminarflow:30700',
        'baud':              '9600',
        'spike_threshold_c': '5.0',
        'spike_window_s':    '120',
        'reconnect_delay':   '5',
    }})
    cp.read(CONFIG_PATH)
    _cfg              = cp['ardtemp']
    BAUD              = int(_cfg['baud'])
    SPIKE_THRESHOLD_C = float(_cfg['spike_threshold_c'])
    SPIKE_WINDOW_S    = int(_cfg['spike_window_s'])
    RECONNECT_DELAY   = int(_cfg['reconnect_delay'])


def _service_url():
    return _cfg.get('service_url', 'http://laminarflow:30700').rstrip('/')

def _board_id():
    return _cfg.get('board', 'r4wifi')


# --- HTTP helpers -----------------------------------------------------------

def _http_get(path, params=None):
    url = _service_url() + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())

def _http_post(path, data):
    body = json.dumps(data).encode()
    req  = urllib.request.Request(
        _service_url() + path, data=body,
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


# --- unit helpers -----------------------------------------------------------

def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0

def format_reading(t_c, h):
    if unit == 'F':
        return f"{c_to_f(t_c):.1f}°F  {h:.0f}%"
    return f"{t_c:.1f}°C  {h:.0f}%"


# --- command dispatch -------------------------------------------------------

def send_cmd(cmd: str):
    threading.Thread(
        target=lambda: _http_post('/command', {'board_id': _board_id(), 'cmd': cmd}),
        daemon=True
    ).start()


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
    send_cmd('F')

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
    send_cmd('D')


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
    temp_history.clear()
    if alert_active:
        dismiss_alert()
    indicator.set_label("no sensor", GUIDE)
    return False


# --- HTTP polling loop ------------------------------------------------------

def read_http():
    while True:
        try:
            data = _http_get('/latest', {'board_id': _board_id()})
            if 'error' in data:
                GLib.idle_add(set_no_sensor)
            elif time.time() - data['ts'] > STALE_THRESHOLD_S:
                GLib.idle_add(set_no_sensor)
            else:
                GLib.idle_add(process_reading, data['t'], data['h'])
        except Exception:
            GLib.idle_add(set_no_sensor)
        time.sleep(2)


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
    load_config()
    indicator = AppIndicator3.Indicator.new(
        "ardtemp",
        "weather-clear-symbolic",
        AppIndicator3.IndicatorCategory.HARDWARE,
    )
    indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
    indicator.set_attention_icon("dialog-warning")
    indicator.set_label("starting…", GUIDE)
    indicator.set_menu(build_menu())

    threading.Thread(target=read_http, daemon=True).start()
    Gtk.main()


if __name__ == "__main__":
    main()
