import sys
from pathlib import Path
from unittest.mock import MagicMock

# Mock gi and serial before the indicator module is imported.
# The indicator imports these at module level; without hardware or a desktop
# session, they are unavailable in CI.
for mod in (
    'gi',
    'gi.repository',
    'gi.repository.AyatanaAppIndicator3',
    'gi.repository.Gtk',
    'gi.repository.GLib',
    'serial',
):
    sys.modules[mod] = MagicMock()

sys.path.insert(0, str(Path(__file__).parent.parent / 'indicator'))
