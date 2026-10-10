"""Backup format round trip. Run: python3 -m unittest discover tests"""

import json
import unittest

from opennollie.model import CanvasSet, DeviceState
from opennollie.protocol import CanvasLayout, CanvasParams, Mode


class Backup(unittest.TestCase):
    def test_json_round_trip(self):
        s = DeviceState(serial="x", name="n", mode=Mode.FSEQ,
                        loop=CanvasSet(CanvasLayout([[0, 8], [4]]),
                                       [CanvasParams(effect=1), CanvasParams(effect=2, color1=(1, 2, 3))], 33),
                        boot=CanvasSet(CanvasLayout([]), [], 16), boot_playlist=(1, 1), ol_idle_s=5,
                        led_counts=[8] * 16, zled_counts=[0] * 16, palettes=[[(1, 2, 3)] * 16] * 8,
                        dynamic_1=[0] * 16, dynamic_2=[0] * 16, frame_delay=[33] * 16, vmap_names=[""] * 16,
                        led_index=[list(range(256))] * 16)
        self.assertEqual(DeviceState.from_dict(json.loads(json.dumps(s.to_dict()))), s)


if __name__ == "__main__":
    unittest.main()
