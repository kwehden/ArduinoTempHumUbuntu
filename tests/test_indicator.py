import json
import time
from pathlib import Path
from unittest.mock import patch

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
        assert ind.BAUD == 9600
        assert ind.SPIKE_THRESHOLD_C == 5.0
        assert ind.SPIKE_WINDOW_S == 120
        assert ind.RECONNECT_DELAY == 5
        assert ind._cfg['board'] == 'uno-q'
        assert ind._cfg['device'] == '/dev/ttyACM0'

    def test_overrides_from_file(self, tmp_path):
        conf = tmp_path / 'ardtemp.conf'
        conf.write_text(
            '[ardtemp]\nbaud = 115200\nspike_threshold_c = 3.0\nspike_window_s = 60\n'
        )
        ind.CONFIG_PATH = str(conf)
        ind.load_config()
        assert ind.BAUD == 115200
        assert ind.SPIKE_THRESHOLD_C == 3.0
        assert ind.SPIKE_WINDOW_S == 60

    def test_partial_override_keeps_other_defaults(self, tmp_path):
        conf = tmp_path / 'ardtemp.conf'
        conf.write_text('[ardtemp]\nspike_threshold_c = 2.5\n')
        ind.CONFIG_PATH = str(conf)
        ind.load_config()
        assert ind.SPIKE_THRESHOLD_C == 2.5
        assert ind.BAUD == 9600  # unchanged default


# ---------------------------------------------------------------------------
# detect_device
# ---------------------------------------------------------------------------

class TestDetectDevice:
    def setup_method(self):
        ind.CONFIG_PATH = '/nonexistent'
        ind.load_config()

    def test_empty_ardconfig_path_returns_device_fallback(self):
        ind._cfg['ardconfig_path'] = ''
        ind._cfg['device'] = '/dev/ttyACM0'
        assert ind.detect_device() == '/dev/ttyACM0'

    def test_finds_matching_board(self):
        ind._cfg['ardconfig_path'] = '/fake/ardconfig'
        ind._cfg['board'] = 'uno-q'
        ind._cfg['device'] = '/dev/ttyFALLBACK'
        with patch('ardtemp_indicator.subprocess.run') as mock_run:
            mock_run.return_value.stdout = FIXTURE.read_text()
            assert ind.detect_device() == '/dev/ttyACM0'

    def test_falls_back_when_board_id_not_in_output(self):
        ind._cfg['ardconfig_path'] = '/fake/ardconfig'
        ind._cfg['board'] = 'nonexistent-board'
        ind._cfg['device'] = '/dev/ttyFALLBACK'
        with patch('ardtemp_indicator.subprocess.run') as mock_run:
            mock_run.return_value.stdout = FIXTURE.read_text()
            assert ind.detect_device() == '/dev/ttyFALLBACK'

    def test_falls_back_on_subprocess_error(self):
        ind._cfg['ardconfig_path'] = '/fake/ardconfig'
        ind._cfg['board'] = 'uno-q'
        ind._cfg['device'] = '/dev/ttyFALLBACK'
        with patch('ardtemp_indicator.subprocess.run', side_effect=FileNotFoundError):
            assert ind.detect_device() == '/dev/ttyFALLBACK'

    def test_falls_back_on_invalid_json(self):
        ind._cfg['ardconfig_path'] = '/fake/ardconfig'
        ind._cfg['board'] = 'uno-q'
        ind._cfg['device'] = '/dev/ttyFALLBACK'
        with patch('ardtemp_indicator.subprocess.run') as mock_run:
            mock_run.return_value.stdout = 'not json'
            assert ind.detect_device() == '/dev/ttyFALLBACK'


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
        old_time = time.monotonic() - 200  # outside the 120 s window
        ind.temp_history.append((old_time, 10.0))  # would spike if counted
        assert ind.check_spike(25.0) is False       # pruned → only 1 reading left

    def test_spike_does_not_fire_after_window_expires(self):
        ind.check_spike(20.0)
        # Manually age all history past the window
        aged = [(ts - 200, t) for ts, t in ind.temp_history]
        ind.temp_history.clear()
        ind.temp_history.extend(aged)
        assert ind.check_spike(25.0) is False  # 20.0 is pruned


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
