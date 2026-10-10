"""LED layouts and ordered rendering. Run: python3 -m unittest discover tests"""

import json
import tempfile
import unittest

from opennollie import layouts
from opennollie import protocol as p
from opennollie.engine import Renderer, render_fseq
from opennollie.profile import loads


class Orders(unittest.TestCase):
    def test_strip(self):
        s = layouts.strip(4)
        self.assertEqual(layouts.order(s), [0, 1, 2, 3])
        self.assertEqual(layouts.order(s, reverse=True), [3, 2, 1, 0])
        self.assertEqual(layouts.order(s, offset=1), [1, 2, 3, 0])
        self.assertEqual(layouts.describe(layouts.order(s, reverse=True, offset=1), 4), "reversed, starts at LED 3")

    def test_dark_leds_stay_last(self):
        o = layouts.order(layouts.strip(3, zleds=2), reverse=True)
        self.assertEqual(o, [2, 1, 0, 3, 4])
        self.assertEqual(len(layouts.full_index(o)), 256)

    def test_component_file(self):
        comp = {"DisplayName": "Test fan", "LedCount": 4, "LedMapping": [0, 1, 2, 3],
                "LedCoordinates": [[0, 1], [1, 2], [2, 1], [1, 0]]}          # [y, x]: top, right, bottom, left
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps(comp)[:-1] + ",}")                           # vendor files have trailing commas
        L = layouts.load(f.name)
        self.assertEqual((L.name, L.leds, L.kind), ("Test fan", 4, "component"))
        self.assertEqual(layouts.order(L, "left → right"), [3, 0, 2, 1])
        self.assertEqual(layouts.order(L, "top → bottom"), [0, 1, 3, 2])

    def test_from_device(self):
        self.assertIsNone(layouts.from_device(list(range(256)), 8, 0))      # plain wiring
        self.assertEqual(layouts.from_device([7, 6, 5, 4, 3, 2, 1, 0] + list(range(8, 256)), 8, 0),
                         [7, 6, 5, 4, 3, 2, 1, 0])


class OrderedRendering(unittest.TestCase):
    def test_renderer_applies_order_and_dark_leds(self):
        prof = loads('[[layer]]\nports = "4"\neffect = "gradient"\ncolors = ["black", "#ff0000"]\n'
                     'interp = "linear"\nunit = "led"\nspan = "port"')
        prof = prof.with_device_leds({4: 3}, {4: [2, 1, 0, 3]})            # reversed + 1 dark LED
        wire = Renderer(prof).frame(0)[p.PORT_TO_CH[3]]
        self.assertEqual(len(wire), 4)
        self.assertEqual(wire[3], (0, 0, 0))                                 # dark LED
        self.assertGreater(wire[0][0], wire[2][0])                           # gradient runs the other way
        data, led_map = render_fseq(prof)
        self.assertEqual(led_map[p.PORT_TO_CH[3]], 4)                        # FSEQ covers the dark LED too

    def test_order_ignored_when_led_count_differs(self):
        prof = loads('leds = 5\n[[layer]]\neffect = "static"').with_device_leds({4: 3}, {4: [2, 1, 0]})
        self.assertNotIn(4, prof.port_orders)


if __name__ == "__main__":
    unittest.main()
