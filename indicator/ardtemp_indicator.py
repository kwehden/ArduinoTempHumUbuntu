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
STALE_THRESHOLD_S = 20        # readings older than this are treated as no-sensor

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
    temp_history.clear()
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


# --- history window ---------------------------------------------------------

class HistoryWindow:
    _instance = None

    def __init__(self):
        self.minutes = 30
        self._data   = []

        self.win = Gtk.Window(title="Temperature History")
        self.win.set_default_size(720, 340)
        self.win.connect("destroy", self._on_destroy)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for margin in ('top', 'bottom', 'start', 'end'):
            getattr(vbox, f'set_margin_{margin}')(8)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hbox.pack_start(Gtk.Label(label="Show last:"), False, False, 0)
        self.slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 30, 360, 30)
        self.slider.set_value(30)
        self.slider.set_hexpand(True)
        self.slider.set_draw_value(False)
        for m, lbl in [(30,"30m"),(60,"1h"),(120,"2h"),(180,"3h"),(240,"4h"),(300,"5h"),(360,"6h")]:
            self.slider.add_mark(m, Gtk.PositionType.BOTTOM, lbl)
        self.slider.connect("value-changed", self._on_slider)
        hbox.pack_start(self.slider, True, True, 0)

        self.da = Gtk.DrawingArea()
        self.da.set_vexpand(True)
        self.da.set_hexpand(True)
        self.da.connect("draw", self._draw)

        vbox.pack_start(hbox, False, False, 0)
        vbox.pack_start(self.da, True, True, 0)
        self.win.add(vbox)
        self.win.show_all()
        self._fetch()

    def _on_destroy(self, _win):
        HistoryWindow._instance = None

    def _on_slider(self, slider):
        snapped = round(slider.get_value() / 30) * 30
        snapped = max(30, min(360, int(snapped)))
        if int(slider.get_value()) != snapped:
            slider.set_value(snapped)
            return
        self.minutes = snapped
        self._fetch()

    def _fetch(self):
        threading.Thread(target=self._do_fetch, daemon=True).start()

    def _do_fetch(self):
        try:
            since = int(time.time()) - self.minutes * 60
            self._data = _http_get('/readings', {'board_id': _board_id(), 'since': since, 'limit': 11000})
        except Exception:
            self._data = []
        GLib.idle_add(self.da.queue_draw)

    def _draw(self, widget, cr):
        a = widget.get_allocation()
        W, H = a.width, a.height
        ML, MR, MT, MB = 56, 16, 16, 36
        PW, PH = W - ML - MR, H - MT - MB

        # dark background
        cr.set_source_rgb(0.13, 0.13, 0.16)
        cr.rectangle(0, 0, W, H)
        cr.fill()

        if not self._data:
            cr.set_source_rgb(0.55, 0.55, 0.60)
            cr.set_font_size(13)
            cr.move_to(W / 2 - 30, H / 2)
            cr.show_text("no data")
            return

        def disp(t_c):
            return c_to_f(t_c) if unit == 'F' else t_c

        unit_lbl = '°F' if unit == 'F' else '°C'
        now  = time.time()
        span = self.minutes * 60
        ts_list = [r['ts'] for r in self._data]
        t_list  = [disp(r['t']) for r in self._data]
        lo = min(t_list) - 0.5
        hi = max(t_list) + 0.5
        rng = hi - lo or 1

        def xp(ts): return ML + (ts - (now - span)) / span * PW
        def yp(t):  return MT + (1 - (t - lo) / rng) * PH

        # horizontal grid + Y labels
        for i in range(6):
            t = lo + i * rng / 5
            y = yp(t)
            cr.set_source_rgba(0.28, 0.28, 0.33, 1)
            cr.set_line_width(0.5)
            cr.move_to(ML, y); cr.line_to(W - MR, y); cr.stroke()
            cr.set_source_rgb(0.65, 0.65, 0.70)
            cr.set_font_size(10)
            cr.move_to(2, y + 4)
            cr.show_text(f"{t:.1f}{unit_lbl}")

        # vertical grid + X labels every 30 min
        tick = 1800
        t0 = int((now - span) / tick + 1) * tick
        for ts in range(int(t0), int(now) + 1, tick):
            x = xp(ts)
            if x < ML or x > W - MR:
                continue
            cr.set_source_rgba(0.28, 0.28, 0.33, 1)
            cr.set_line_width(0.5)
            cr.move_to(x, MT); cr.line_to(x, H - MB); cr.stroke()
            cr.set_source_rgb(0.65, 0.65, 0.70)
            cr.set_font_size(10)
            cr.move_to(x - 14, H - MB + 14)
            cr.show_text(time.strftime("%H:%M", time.localtime(ts)))

        # plot border
        cr.set_source_rgb(0.75, 0.75, 0.75)
        cr.set_line_width(1)
        cr.rectangle(ML, MT, PW, PH)
        cr.stroke()

        # temperature line
        cr.set_source_rgb(0.25, 0.85, 0.65)
        cr.set_line_width(1.5)
        started = False
        for ts, t in zip(ts_list, t_list):
            x, y = xp(ts), yp(t)
            if not started:
                cr.move_to(x, y); started = True
            else:
                cr.line_to(x, y)
        cr.stroke()


def show_history():
    if HistoryWindow._instance is None:
        HistoryWindow._instance = HistoryWindow()
    else:
        HistoryWindow._instance.win.present()


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

    history_item = Gtk.MenuItem(label="Show history…")
    history_item.connect("activate", lambda _: show_history())
    menu.append(history_item)

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
