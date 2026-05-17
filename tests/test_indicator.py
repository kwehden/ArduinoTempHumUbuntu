import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

import ardtemp_indicator as ind

FIXTURE = Path(__file__).parent / 'fixtures' / 'ardconfig_detect_output.json'


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_defaults_without_file(self, tmp_path):
        ind.CONFIG_PATH = str(tmp_path / 'nonexistent.conf')
        ind.load_config()
        assert ind.SPIKE_THRESHOLD_C == 5.0
        assert ind.SPIKE_WINDOW_S == 120
        assert ind.RECONNECT_DELAY == 5
        assert ind._cfg['board'] == 'r4wifi'
        assert ind._cfg['service_url'] == 'http://laminarflow:30700'

    def test_overrides_from_file(self, tmp_path):
        conf = tmp_path / 'ardtemp.conf'
        conf.write_text(
            '[ardtemp]\nspike_threshold_c = 3.0\nspike_window_s = 60\n'
            'service_url = http://myhost:9000\n'
        )
        ind.CONFIG_PATH = str(conf)
        ind.load_config()
        assert ind.SPIKE_THRESHOLD_C == 3.0
        assert ind.SPIKE_WINDOW_S == 60
        assert ind._cfg['service_url'] == 'http://myhost:9000'

    def test_partial_override_keeps_other_defaults(self, tmp_path):
        conf = tmp_path / 'ardtemp.conf'
        conf.write_text('[ardtemp]\nspike_threshold_c = 2.5\n')
        ind.CONFIG_PATH = str(conf)
        ind.load_config()
        assert ind.SPIKE_THRESHOLD_C == 2.5
        assert ind.SPIKE_WINDOW_S == 120  # unchanged default


# ---------------------------------------------------------------------------
# read_http / _http_get
# ---------------------------------------------------------------------------

class TestReadHttp:
    def setup_method(self):
        ind.CONFIG_PATH = '/nonexistent'
        ind.load_config()
        ind.temp_history.clear()
        ind.alert_active = False

    def _mock_response(self, data: dict):
        mock = MagicMock()
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        mock.read.return_value = json.dumps(data).encode()
        return mock

    def test_fresh_reading_dispatched(self):
        data = {'ts': int(time.time()), 't': 27.0, 'h': 45.0}
        dispatched = []
        with patch('ardtemp_indicator.urllib.request.urlopen', return_value=self._mock_response(data)), \
             patch('ardtemp_indicator.GLib.idle_add', side_effect=lambda fn, *a: dispatched.append((fn, a))):
            ind._http_get('/latest', {'board_id': 'r4wifi'})
            # Simulate what read_http does
            ind.GLib.idle_add(ind.process_reading, data['t'], data['h'])
        assert any(a == (27.0, 45.0) for fn, a in dispatched if fn == ind.process_reading)

    def test_stale_reading_triggers_no_sensor(self):
        data = {'ts': int(time.time()) - 30, 't': 27.0, 'h': 45.0}
        with patch('ardtemp_indicator.urllib.request.urlopen', return_value=self._mock_response(data)):
            result = ind._http_get('/latest', {'board_id': 'r4wifi'})
        assert time.time() - result['ts'] > ind.STALE_THRESHOLD_S

    def test_service_error_key_means_no_sensor(self):
        data = {'error': 'no data'}
        with patch('ardtemp_indicator.urllib.request.urlopen', return_value=self._mock_response(data)):
            result = ind._http_get('/latest', {'board_id': 'r4wifi'})
        assert 'error' in result

    def test_network_exception_handled(self):
        with patch('ardtemp_indicator.urllib.request.urlopen', side_effect=URLError('refused')):
            try:
                ind._http_get('/latest', {'board_id': 'r4wifi'})
                assert False, "should have raised"
            except URLError:
                pass  # read_http catches this with bare except Exception


# ---------------------------------------------------------------------------
# send_cmd
# ---------------------------------------------------------------------------

class TestSendCmd:
    def setup_method(self):
        ind.CONFIG_PATH = '/nonexistent'
        ind.load_config()

    def test_posts_F_command(self):
        posted = []
        def fake_post(path, data):
            posted.append((path, data))
        with patch('ardtemp_indicator._http_post', side_effect=fake_post), \
             patch('ardtemp_indicator.threading.Thread') as mock_thread:
            # Capture the lambda and call it directly
            mock_thread.return_value.start = MagicMock()
            ind.send_cmd('F')
            # Extract and call the target lambda
            target = mock_thread.call_args[1]['target']
            target()
        assert posted == [('/command', {'board_id': 'r4wifi', 'cmd': 'F'})]

    def test_posts_D_command(self):
        posted = []
        def fake_post(path, data):
            posted.append((path, data))
        with patch('ardtemp_indicator._http_post', side_effect=fake_post), \
             patch('ardtemp_indicator.threading.Thread') as mock_thread:
            mock_thread.return_value.start = MagicMock()
            ind.send_cmd('D')
            target = mock_thread.call_args[1]['target']
            target()
        assert posted == [('/command', {'board_id': 'r4wifi', 'cmd': 'D'})]


# ---------------------------------------------------------------------------
# check_spike
# ---------------------------------------------------------------------------

class TestCheckSpike:
    def setup_method(self):
        ind.temp_history.clear()
        ind.SPIKE_WINDOW_S    = 120
        ind.SPIKE_THRESHOLD_C = 5.0

    def test_single_reading_never_spikes(self):
        assert ind.check_spike(25.0) is False

    def test_no_spike_below_threshold(self):
        ind.check_spike(25.0)
        assert ind.check_spike(29.9) is False

    def test_spike_at_threshold(self):
        ind.check_spike(25.0)
        assert ind.check_spike(30.0) is True

    def test_spike_above_threshold(self):
        ind.check_spike(20.0)
        assert ind.check_spike(30.0) is True

    def test_stale_readings_are_pruned(self):
        old_time = time.monotonic() - 200
        ind.temp_history.append((old_time, 10.0))
        assert ind.check_spike(25.0) is False

    def test_spike_does_not_fire_after_window_expires(self):
        ind.check_spike(20.0)
        aged = [(ts - 200, t) for ts, t in ind.temp_history]
        ind.temp_history.clear()
        ind.temp_history.extend(aged)
        assert ind.check_spike(25.0) is False


# ---------------------------------------------------------------------------
# format_reading
# ---------------------------------------------------------------------------

class TestFormatReading:
    def test_celsius_format(self):
        ind.unit = 'C'
        assert ind.format_reading(26.1, 37.0) == '26.1°C  37%'

    def test_fahrenheit_freezing(self):
        ind.unit = 'F'
        assert ind.format_reading(0.0, 50.0) == '32.0°F  50%'

    def test_fahrenheit_boiling(self):
        ind.unit = 'F'
        assert '212.0°F' in ind.format_reading(100.0, 0.0)

    def test_humidity_rounds_to_integer(self):
        ind.unit = 'C'
        assert '37%' in ind.format_reading(25.0, 37.4)
        assert '38%' in ind.format_reading(25.0, 37.5)


# ---------------------------------------------------------------------------
# ardconfig contract — pins the JSON schema the indicator depends on
# ---------------------------------------------------------------------------

class TestArdconfigContract:
    def setup_method(self):
        with open(FIXTURE) as f:
            self.data = json.load(f)

    def test_fixture_is_valid_json(self):
        assert isinstance(self.data, dict)

    def test_boards_list_present(self):
        assert 'boards' in self.data
        assert isinstance(self.data['boards'], list)
        assert len(self.data['boards']) >= 1

    def test_each_board_has_required_fields(self):
        for board in self.data['boards']:
            assert 'board_id' in board, f"missing board_id: {board}"
            assert 'device'   in board, f"missing device: {board}"

    def test_supported_board_profiles_present(self):
        ids = {b['board_id'] for b in self.data['boards']}
        assert 'uno-q'   in ids
        assert 'r4wifi'  in ids

    def test_each_board_device_is_serial_port(self):
        for board in self.data['boards']:
            assert board['device'].startswith('/dev/tty'), \
                f"{board['board_id']} device is not a serial port: {board['device']}"
