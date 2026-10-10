"""Case layout model. Run: python3 -m unittest discover tests"""

import tempfile
import unittest
from pathlib import Path

from opennollie import caselayout
from opennollie.caselayout import CaseLayout, Item


class CaseLayoutModel(unittest.TestCase):
    def test_daisy_chained_fans_share_a_port(self):
        wire = [(i, 0, 0) for i in range(24)]
        a, b, c = Item(4, first=1, count=8), Item(4, first=9, count=8), Item(4, first=17)
        self.assertEqual([x[0] for x in a.colors(wire, 24)], list(range(0, 8)))
        self.assertEqual([x[0] for x in b.colors(wire, 24)], list(range(8, 16)))
        self.assertEqual(c.leds(24), 8)                        # "rest of the port"

    def test_auto_skips_unset_ports(self):
        layout = caselayout.auto({1: 256, 4: 8, 9: 6, 10: 0})
        self.assertEqual(sorted(layout.ports()), [4, 9])

    def test_save_load_round_trip_and_sanitising(self):
        path = Path(tempfile.mkdtemp()) / "layout.json"
        caselayout.save(CaseLayout([Item(9, "strip", x=0.4, y=0.6, angle=30, label="GPU")]), path)
        self.assertEqual(caselayout.load(path).items[0], Item(9, "strip", x=0.4, y=0.6, angle=30, label="GPU"))
        path.write_text('{"items": [{"port": 99}, {"port": 5, "x": 7, "kind": "fan", "bogus": 1}]}')
        items = caselayout.load(path).items
        self.assertEqual((len(items), items[0].x), (1, 1.0))   # invalid port dropped, x clamped, unknown key ignored


if __name__ == "__main__":
    unittest.main()
