"""Tray autostart switch (temporary folder; systemctl isn't touched). Run: python3 -m unittest discover tests"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opennollie import autostart


class TrayAutostart(unittest.TestCase):
    def test_toggle(self):
        d = Path(tempfile.mkdtemp())
        with mock.patch.object(autostart, "TRAY_ENTRY", d / "opennollie-tray.desktop"), \
             mock.patch.object(autostart, "SYSTEM_TRAY_ENTRY", d / "none.desktop"):
            self.assertFalse(autostart.tray_enabled())
            autostart.set_tray(True)
            self.assertTrue(autostart.tray_enabled())
            autostart.set_tray(False)
            self.assertFalse(autostart.tray_enabled())
            self.assertIn("Hidden=true", (d / "opennollie-tray.desktop").read_text())

    def test_user_entry_overrides_package_entry(self):
        d = Path(tempfile.mkdtemp())
        (d / "system.desktop").write_text("[Desktop Entry]\nExec=opennollie-tray\n")
        with mock.patch.object(autostart, "TRAY_ENTRY", d / "user.desktop"), \
             mock.patch.object(autostart, "SYSTEM_TRAY_ENTRY", d / "system.desktop"):
            self.assertTrue(autostart.tray_enabled())
            autostart.set_tray(False)
            self.assertFalse(autostart.tray_enabled())


if __name__ == "__main__":
    unittest.main()
