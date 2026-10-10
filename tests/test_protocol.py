"""Protocol tests. Expected bytes come from captures/fw_update_2.3.0.pcapng (NollieRGB 2.3.0 talking to
fw 2.3.0). Run: python3 -m unittest discover tests"""

import unittest

from opennollie import protocol as p
from opennollie.cli import live_frame, parse_color, parse_ports
from opennollie.protocol import CanvasLayout, CanvasParams


def hx(s: str) -> bytes:
    """Captured packet prefix, zero-padded to a full report."""
    b = bytes.fromhex(s)
    return b + bytes(p.REPORT_LEN - len(b))


class CapturedPackets(unittest.TestCase):
    def test_canvas_params(self):
        params = CanvasParams(effect=2, brightness=100, step=1, size=1, ch_count=16, vmap_process=0, delay=0,
                              color1=(0x0D, 0, 0xFF), color2=(0, 0, 0xFF), color3=(0xFF, 0, 0xEE))
        self.assertEqual(params.encode(0), hx("fa0200026401011000000d00ff0000ffff00ee"))

    def test_boot_canvas_params(self):
        params = CanvasParams(effect=1, brightness=100, step=1, size=1, ch_count=5)  # default colors
        self.assertEqual(params.encode(14), hx("fa020e0164010105000000ff00ffff0000ffff"))

    def test_led_num(self):
        counts = [8] + [256] * 15
        self.assertEqual(p.set_led_num(counts), hx("fa0808000001" + "0001" * 14))

    def test_boot_canvas_map(self):
        flat = [3, 2, 1, 0, 8, 9, 10, 11, 4, 5, 6, 7, 15, 14, 13, 12]
        self.assertEqual(p.set_canvas_map(flat, boot=True), hx("fa0b000302010008090a0b040506070f0e0d0c"))

    def test_vmap_name(self):
        self.assertEqual(p.set_vmap_name(0, "LED Strip"), hx("f60700124c00450044002000530074007200690070"))

    def test_led_index(self):
        chunk = [7, 6, 5, 4, 3, 2, 1, 0] + [i % 256 for i in range(8, 255)]
        self.assertEqual(p.set_led_index(0, chunk)[:12], hx("f80000ff0706050403020100")[:12])
        self.assertEqual(p.get_led_index(0x0FF0, 16), hx("f7f00f10"))

    def test_ol_idle_and_mode(self):
        self.assertEqual(p.set_ol_idle(30), hx("f6161e00"))
        self.assertEqual(p.set_mode(p.Mode.HARDWARE), hx("fa0101"))

    def test_gets(self):
        self.assertEqual(p.get_effect(p.EffectItem.PALETTE, 3), hx("f90d03"))
        self.assertEqual(p.get_core(p.CoreItem.VMAP_NAME, 5), hx("f50705"))
        self.assertEqual(p.get_config(p.ConfigItem.FW_VERSION), hx("fc02"))

    def test_live(self):
        pkt = p.live_channel(3, [(0xFF, 0xFF, 0xFF), (0, 0, 0x28)])
        self.assertEqual(pkt[:10], bytes.fromhex("03000000ffffff000028"))
        self.assertEqual(p.show(), hx("ff"))


class CapturedResponses(unittest.TestCase):
    def test_version(self):
        self.assertEqual(p.parse_version(hx("0013a5")), (19, 165))
        self.assertEqual(p.parse_fw_version(hx("020300")), "2.3.0")

    def test_canvas_params(self):
        c = CanvasParams.decode(hx("0000000264010110000000ff000000ffff00"))
        self.assertEqual((c.effect, c.brightness, c.step, c.size, c.ch_count), (2, 100, 1, 1, 16))
        self.assertEqual((c.color1, c.color2, c.color3), ((0, 255, 0), (0, 0, 255), (255, 0, 0)))

    def test_palette_is_grb(self):
        pal = p.parse_palette(hx("00ff0060ff00"))
        self.assertEqual(pal[:2], [(255, 0, 0), (255, 96, 0)])
        self.assertEqual(p.set_palette(0, pal)[3:9], bytes.fromhex("00ff0060ff00"))

    def test_led_counts(self):
        self.assertEqual(p.parse_u16le(hx("0001" * 16), 16), [256] * 16)


class Layout(unittest.TestCase):
    def test_round_trip(self):
        layout = CanvasLayout.from_device([3, 2, 1, 0, 8, 9, 10] + list(range(16)), [4, 3])
        self.assertEqual(layout.canvases, [[3, 2, 1, 0], [8, 9, 10]])
        self.assertEqual(layout.flat()[:7], [3, 2, 1, 0, 8, 9, 10])
        self.assertEqual(sorted(layout.flat()), list(range(16)))

    def test_rejects_duplicates(self):
        with self.assertRaises(ValueError):
            CanvasLayout([[1, 2], [2]]).flat()

    def test_port_map_is_a_permutation(self):
        self.assertEqual(sorted(p.PORT_TO_CH), list(range(16)))
        self.assertEqual(p.CH_TO_PORT[p.PORT_TO_CH[8]], 9)     # GPU: port 9 <-> fw ch 4


class Cli(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(parse_ports("1,3-4"), [1, 3, 4])
        self.assertEqual(parse_color("#ff8000"), (255, 128, 0))

    def test_live_frame_maps_ports(self):
        frame = live_frame({9: (1, 2, 3)}, leds=2)
        self.assertEqual(frame[4], [(1, 2, 3)] * 2)
        self.assertEqual(frame[0], [(0, 0, 0)] * 2)


if __name__ == "__main__":
    unittest.main()
