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
        real = Path.home() / ".config" / "opennollie" / "calibration.toml"
        before = real.read_bytes() if real.exists() else None
        self.addCleanup(lambda: self.assertEqual(real.read_bytes() if real.exists() else None, before,
                                                 "a test changed the user's real color balance file"))

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

    # --- color balance / LED counts changed after saving ------------------------------------

    WHITE = '[[layer]]\nports = "4,5,9"\neffect = "static"\ncolor = "white"\nbrightness = 0.6'

    def gpu_color(self):
        layout, params = self.dev.canvases()
        return next(prm.color1 for chans, prm in zip(layout.canvases, params) if p.PORT_TO_CH[8] in chans)

    def test_new_balance_updates_saved_static_colors(self):
        calibration.save({9: (0.5, 1.0, 1.0)})
        engine.save_profile(self.dev, profile.loads(self.WHITE))
        self.assertEqual(self.gpu_color(), (128, 255, 255))
        calibration.save({9: (0.15, 0.2, 1.0)})
        self.assertIn("hardware canvas", engine.update_saved(self.dev))
        self.assertEqual(self.gpu_color(), (38, 51, 255))
        self.assertEqual(engine.update_saved(self.dev), "already up to date")

    def test_update_leaves_other_lighting_alone(self):
        engine.save_profile(self.dev, profile.loads(self.WHITE))
        self.cli("static", "4,5,9", "red")                 # set up some other way since
        calibration.save({9: (0.5, 1.0, 1.0)})
        n = len(self.fake.log)
        self.assertIsNone(engine.update_saved(self.dev))
        self.assertFalse([x for x in self.fake.log[n:] if x[0] == p.Cmd.SET_EFFECT and x[1] != p.EffectItem.MODE])
        self.assertEqual(self.gpu_color(), (255, 0, 0))

    def test_new_balance_updates_saved_animation(self):
        anim = SMALL.replace('ports = "4,5"', 'ports = "4,5,9"')
        engine.save_profile(self.dev, profile.loads(anim))
        before = bytes(self.fake.flash[FSEQ_BASE:FSEQ_BASE + 60000])
        self.assertEqual(engine.update_saved(self.dev), "already up to date")
        calibration.save({9: (0.5, 1.0, 1.0)})
        self.assertIn("stored as animation", engine.update_saved(self.dev))
        self.assertNotEqual(bytes(self.fake.flash[FSEQ_BASE:FSEQ_BASE + 60000]), before)
        self.assertTrue(self.fake.playing)
        n = len(self.fake.log)
        self.assertEqual(engine.update_saved(self.dev), "already up to date")
        self.assertFalse([x for x in self.fake.log[n:] if x[0] in (p.Cmd.FLASH, p.Cmd.REC_CTRL)])

    def test_cli_balance_and_leds_update_the_saved_profile(self):
        self.cli("save", "groups-static")
        out = self.cli("balance", "9", "50", "100", "100")
        self.assertIn("updated", out)
        self.assertIn("updated", self.cli("balance", "9", "--reset"))
        self.assertIn("up to date", self.cli("leds", "4=10"))

    def test_old_state_file_still_read(self):
        engine.STATE_FILE.write_text(json.dumps({self.dev.serial: {"crc": 1, "size": 2, "led_map": [0] * 16}}))
        self.assertEqual(engine._state(self.dev.serial), {"animation": {"crc": 1, "size": 2, "led_map": [0] * 16}})
        self.assertIsNone(engine.update_saved(self.dev))

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
