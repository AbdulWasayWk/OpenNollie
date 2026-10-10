"""FSEQ writer/reader and flash helpers. Run: python3 -m unittest discover tests"""

import unittest

from opennollie.flash import FlashOp, crc32_nollie, header
from opennollie.fseq import FseqError, build_fseq, led_map_matches, parse_header


class Crc(unittest.TestCase):
    # Reference values computed with NollieRGB 2.3.0's own nos_crc32 (cross-checked on 46 inputs).
    def test_vectors(self):
        self.assertEqual(crc32_nollie(b"123456789"), 0xBF99399C)
        self.assertEqual(crc32_nollie(b""), 0xFFFFFFFF)


class FlashHeader(unittest.TestCase):
    def test_layout(self):
        self.assertEqual(header(FlashOp.WRITE_RAW, 0x012345, 1016), bytes.fromhex("f406452301f80300"))


class Fseq(unittest.TestCase):
    def setUp(self):
        self.led_map = [0] * 16
        self.led_map[4], self.led_map[0] = 2, 3        # fw ch 0 (3 LEDs) and ch 4 (2 LEDs)
        frames = [{0: [(1, 2, 3)] * 3, 4: [(9, 9, 9)]}, {4: [(7, 8, 9), (4, 5, 6)]}]
        self.data = build_fseq(self.led_map, 33, frames)

    def test_round_trip(self):
        hdr = parse_header(self.data)
        self.assertEqual((hdr.frame_count, hdr.step_ms, hdr.frame_bytes), (2, 33, 15))
        self.assertEqual(hdr.ranges, [(0, 9), (9, 6)])          # ascending fw channel order
        self.assertIsNone(led_map_matches(hdr, self.led_map))

    def test_frame_data(self):
        hdr = parse_header(self.data)
        f0 = self.data[hdr.data_offset : hdr.data_offset + 15]
        self.assertEqual(f0, bytes([2, 1, 3] * 3 + [9, 9, 9, 0, 0, 0]))    # stored GRB, missing LEDs black
        f1 = self.data[hdr.data_offset + 15 : hdr.data_offset + 30]
        self.assertEqual(f1, bytes(9) + bytes([8, 7, 9, 5, 4, 6]))

    def test_mismatched_map(self):
        wrong = list(self.led_map)
        wrong[4] = 5
        self.assertIsNotNone(led_map_matches(parse_header(self.data), wrong))

    def test_rejects_compressed(self):
        bad = bytearray(self.data)
        bad[20] = 1
        with self.assertRaises(FseqError):
            parse_header(bytes(bad))


if __name__ == "__main__":
    unittest.main()
