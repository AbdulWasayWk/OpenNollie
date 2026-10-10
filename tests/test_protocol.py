"""Protocol encoding tests. Run: python3 -m unittest discover tests"""

import unittest

from opennollie import protocol
from opennollie.cli import parse_color, parse_ports


class ChannelPacket(unittest.TestCase):
    def test_header_and_grb_order(self):
        pkt = protocol.channel_packet(19, [(0x11, 0x22, 0x33), (0xAA, 0xBB, 0xCC)])
        self.assertEqual(len(pkt), protocol.OUT_REPORT_LEN)
        self.assertEqual(pkt[:4], bytes([19, 0, 0, 2]))
        self.assertEqual(pkt[4:10], bytes([0x22, 0x11, 0x33, 0xBB, 0xAA, 0xCC]))  # G, R, B
        self.assertEqual(pkt[10:], bytes(len(pkt) - 10))

    def test_count_is_big_endian(self):
        pkt = protocol.channel_packet(16, [(0, 0, 0)] * 256)
        self.assertEqual(pkt[2:4], bytes([1, 0]))

    def test_rejects_too_many_leds(self):
        with self.assertRaises(ValueError):
            protocol.channel_packet(16, [(0, 0, 0)] * 257)


class FramePackets(unittest.TestCase):
    def test_empty_frame_is_just_latch(self):
        (pkt,) = protocol.frame_packets({})
        self.assertEqual(pkt[:4], bytes([31, 2, 0, 0]))

    def test_sorted_by_wire_channel_latch_last(self):
        # Port 1 -> 19, port 5 -> 24, port 4 -> 16
        pkts = protocol.frame_packets({0: [(1, 2, 3)], 4: [(1, 2, 3)], 3: [(1, 2, 3)]})
        self.assertEqual([p[0] for p in pkts], [16, 19, 24, 31])
        self.assertEqual([p[1] for p in pkts], [0, 0, 0, 2])

    def test_latch_port_carries_its_colors(self):
        # Port 13 is wire channel 31: sent once, with its LEDs and the latch flag.
        pkts = protocol.frame_packets({12: [(9, 9, 9)] * 3})
        self.assertEqual(len(pkts), 1)
        self.assertEqual(pkts[0][:4], bytes([31, 2, 0, 3]))

    def test_port_map_is_a_permutation_of_16_to_31(self):
        self.assertEqual(sorted(protocol.PORT_TO_HW), list(range(16, 32)))


class CliParsing(unittest.TestCase):
    def test_ports(self):
        self.assertEqual(parse_ports("1,3-4"), [0, 2, 3])
        self.assertEqual(parse_ports("all"), list(range(16)))

    def test_colors(self):
        self.assertEqual(parse_color("#ff8000"), (255, 128, 0))
        self.assertEqual(parse_color("red"), (255, 0, 0))


if __name__ == "__main__":
    unittest.main()
