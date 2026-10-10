"""End-to-end tests against the simulated controller (tests/fakedevice.py): CLI commands, saving to the
device, flash upload/verify, color balance, backup/restore. Run: python3 -m unittest discover tests"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opennollie import calibration, cli, client, engine, model, profile
from opennollie import protocol as p
from opennollie.device import Nollie16
from opennollie.flash import FSEQ_BASE

from tests.fakedevice import FakeTransport

SMALL = """name = "Small"
fps = 5
[[layer]]
ports = "4,5"
effect = "breathe"
colors = ["#ff0000"]
period = 2
"""


class Offline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fake = FakeTransport()
        self.fake.led = [8] * 16
        self.dev = Nollie16(transport=self.fake)
        patches = [
            mock.patch.object(engine, "STATE_FILE", self.tmp / "stored.json"),
            mock.patch.object(calibration, "PATH", self.tmp / "calibration.toml"),
            mock.patch.object(profile, "USER_PROFILES", self.tmp / "profiles"),
            mock.patch.object(client, "connect", lambda: Nollie16(transport=self.fake)),
        ]
        for pt in patches:
            pt.start()
            self.addCleanup(pt.stop)

    def cli(self, *argv) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue()

    # --- saving ---------------------------------------------------------------------------

    def test_save_animation_then_unchanged_resave(self):
        prof = engine.prepare(self.dev, profile.loads(SMALL))
        msg = engine.save_to_device(self.dev, prof)
        self.assertIn("stored as animation", msg)
        self.assertEqual(self.fake.flash[FSEQ_BASE:FSEQ_BASE + 4], b"PSEQ")
        self.assertEqual(self.fake.playlist, (p.BootIntro.NONE, p.BootSteady.FSEQ_LOOP))
        self.assertTrue(self.fake.playing)
        n_packets = len(self.fake.log)
        msg = engine.save_to_device(self.dev, prof)
        self.assertIn("already stored", msg)
        flash_ops = [x for x in self.fake.log[n_packets:] if x[0] == p.Cmd.FLASH]
        self.assertEqual(flash_ops, [], "an unchanged animation must not touch the flash")

    def test_static_save_never_touches_flash_or_stop(self):
        prof = engine.prepare(self.dev, profile.load("groups-static"))
        engine.save_to_device(self.dev, prof)
        self.assertFalse([x for x in self.fake.log if x[0] in (p.Cmd.FLASH, p.Cmd.REC_CTRL)])
        self.assertEqual(self.fake.lens[False], 5)
        self.assertEqual(self.fake.mode, p.Mode.HARDWARE)

    def test_color_balance_applies_everywhere(self):
        calibration.save({9: (0.5, 1.0, 1.0)})
        prof = engine.prepare(self.dev, profile.loads(
            '[[layer]]\nports = "4,9"\neffect = "static"\ncolor = "white"'))
        frame = engine.Renderer(prof).frame(0)
        self.assertEqual(frame[p.PORT_TO_CH[3]][0], (255, 255, 255))     # fan untouched
        self.assertEqual(frame[p.PORT_TO_CH[8]][0], (128, 255, 255))     # GPU: red at 50 %
        layout, params = engine.static_canvases(prof)
        self.assertEqual(sorted(prm.color1 for prm in params), [(128, 255, 255), (255, 255, 255)])

    # --- CLI ------------------------------------------------------------------------------

    def test_cli_commands_run(self):
        self.assertIn("fw 2.3.0", self.cli("info"))
        self.assertIn("input_voltage", self.cli("power"))
        self.cli("leds", "4-8=8", "9=6")
        self.assertEqual(self.fake.led[p.PORT_TO_CH[8]], 6)
        self.cli("order", "4", "--reverse")
        self.assertEqual(list(self.fake.index[0:8]), [7, 6, 5, 4, 3, 2, 1, 0])     # port 4 = fw ch 0
        self.cli("static", "4,5", "red", "9", "white")
        self.assertEqual(self.fake.lens[False], 2)
        self.assertIn("stored as animation", self.cli("save", "airflow-rainbow"))
        self.assertIn("already stored", self.cli("save", "airflow-rainbow"))
        self.cli("balance", "9", "75", "80", "100")
        self.assertEqual(calibration.load()[9], (0.75, 0.8, 1.0))
        self.cli("balance", "9", "--reset")
        self.assertEqual(calibration.load(), {})
        self.cli("live", "calm-breathe", "--duration", "0.3")
        self.assertGreater(self.fake.frames, 0)

    def test_backup_restore_round_trip(self):
        path = self.tmp / "backup.json"
        self.cli("backup", str(path))
        self.fake.palettes[3] = bytes(range(48))
        self.fake.idle = 30
        out = self.cli("restore", str(path))
        self.assertIn("palette 4", out)
        self.assertEqual(self.fake.palettes[3], bytes(48))
        self.assertEqual(self.fake.idle, 0)
        self.assertIn("nothing to change", self.cli("restore", "--dry-run", str(path)))


if __name__ == "__main__":
    unittest.main()
