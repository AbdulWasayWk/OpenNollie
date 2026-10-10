"""Effects, profiles and rendering. Run: python3 -m unittest discover tests"""

import unittest

from opennollie import protocol as p
from opennollie.effects import EffectSpec, palette_at, parse_color
from opennollie.engine import Renderer, hardware_canvases, loop_frames, render_fseq
from opennollie.fseq import led_map_matches, parse_header
from opennollie.profile import loads, parse_ports

BASIC = """
name = "test"
fps = 10
leds = 4
[groups]
exhaust = "4,5"
[ports.9]
leds = 6
[[layer]]
ports = "all"
effect = "rainbow"
period = 2
spread = 1
[[layer]]
ports = "exhaust"
effect = "static"
color = "red"
"""


class Effects(unittest.TestCase):
    def test_every_effect_is_periodic(self):
        from opennollie.effects import EFFECTS
        for kind in EFFECTS:
            params = {"keyframes": [[0, "red"], [0.5, "blue"]]} if kind == "keyframes" else {}
            fn = EffectSpec(kind, colors=[(255, 0, 0), (0, 0, 255)], period=2, spread=0.5, params=params).make()
            for t in (0.0, 0.3, 1.1):
                for u in (0.0, 0.4):
                    a, b = fn(t, u), fn(t + 2, u)      # ±1 for float rounding at .5 boundaries
                    self.assertTrue(all(abs(x - y) <= 1 for x, y in zip(a, b)), f"{kind} not periodic: {a} {b}")

    def test_palette(self):
        self.assertEqual(palette_at([(0, 0, 0), (200, 0, 0)], 0.25, "linear"), (100, 0, 0))
        self.assertEqual(parse_color("#00ff80"), (0, 255, 128))

    def test_keyframes(self):
        fn = EffectSpec("keyframes", period=1, interp="linear",
                        params={"keyframes": [[0, "black"], [0.5, "#c80000"]]}).make()
        self.assertEqual(fn(0.25, 0), (100, 0, 0))
        self.assertEqual(fn(0.75, 0), (100, 0, 0))     # fades back to the first keyframe

    def test_brightness(self):
        self.assertEqual(EffectSpec("static", colors=[(200, 100, 0)], brightness=0.5).make()(0, 0), (100, 50, 0))


class Profiles(unittest.TestCase):
    def test_parse(self):
        prof = loads(BASIC)
        self.assertEqual(prof.groups["exhaust"], [4, 5])
        self.assertEqual(prof.leds_for(9), 6)
        self.assertEqual(prof.port_layers()[4].spec.kind, "static")    # later layer wins
        self.assertFalse(prof.is_hardware)

    def test_device_led_counts(self):
        prof = loads('[ports.9]\nleds = 12\n[[layer]]\neffect = "rainbow"')
        self.assertEqual(prof.leds_for(4), 32)                          # fallback before resolving
        resolved = prof.with_device_leds({4: 8, 9: 10})
        self.assertEqual((resolved.leds_for(4), resolved.leds_for(9)), (8, 12))   # profile wins
        fixed = loads('leds = 5\n[[layer]]\neffect = "rainbow"').with_device_leds({4: 8})
        self.assertEqual(fixed.leds_for(4), 5)

    def test_port_order_is_kept(self):
        self.assertEqual(parse_ports("16-14,9"), [16, 15, 14, 9])

    def test_toml_round_trip(self):
        import glob
        from opennollie import profile
        for path in glob.glob("profiles/*.toml"):
            a = profile.load(path)
            self.assertEqual(profile.to_dict(profile.loads(profile.dumps(a))), profile.to_dict(a), path)

    def test_effect_info_covers_all_effects(self):
        from opennollie.effects import EFFECT_INFO, EFFECTS
        self.assertEqual(set(EFFECT_INFO), set(EFFECTS))

    def test_rejects_unknown_effect(self):
        with self.assertRaises(ValueError):
            loads('[[layer]]\neffect = "nope"')


class Rendering(unittest.TestCase):
    def test_frame(self):
        r = Renderer(loads(BASIC))
        f = r.frame(0.0)
        self.assertEqual(f[p.PORT_TO_CH[3]], [(255, 0, 0)] * 4)        # port 4: static red
        self.assertEqual(len(f[p.PORT_TO_CH[8]]), 6)                   # port 9: 6 LEDs
        self.assertEqual(len(f), 16)

    def test_led_unit(self):
        prof = loads('leds = 4\n[[layer]]\nports = "1,2"\neffect = "gradient"\ncolors = ["black", "#ff0000"]\n'
                     'interp = "linear"\nunit = "led"\nspan = "group"')
        f = Renderer(prof).frame(0)
        reds = [c[0] for c in f[p.PORT_TO_CH[0]] + f[p.PORT_TO_CH[1]]]
        self.assertEqual(reds, sorted(reds))                            # ramps across both ports

    def test_loop_and_fseq(self):
        prof = loads(BASIC.replace("period = 2", "period = 0.6") + "\n[[layer]]\nports = \"9\"\neffect = \"breathe\"\nperiod = 0.4\n")
        n, periods = loop_frames(prof)
        self.assertEqual(n, 12)                                          # lcm(6, 4) frames at 10 fps
        data, led_map = render_fseq(prof)
        hdr = parse_header(data)
        self.assertEqual(hdr.frame_count, 12)
        self.assertIsNone(led_map_matches(hdr, led_map))

    def test_hardware_profile(self):
        prof = loads('[[layer]]\nports = "4,5"\neffect = "hw:Breath"\ncolors = ["red"]\nbrightness = 0.5\n'
                     '[[layer]]\nports = "9"\neffect = "static"\ncolor = "white"')
        self.assertTrue(prof.is_hardware)
        layout, params = hardware_canvases(prof)
        self.assertEqual(layout.canvases, [[0, 8], [4]])
        self.assertEqual((params[0].effect, params[0].brightness, params[0].color1), (2, 50, (255, 0, 0)))
        self.assertEqual(params[1].effect, p.EFFECTS.index("Solid"))
        with self.assertRaises(ValueError):
            Renderer(prof)


if __name__ == "__main__":
    unittest.main()
