import unittest

from app.crc import crc8_remainder, crc8_bits, crc_step, crc_finish


class CrcTests(unittest.TestCase):
    def test_known_check_value(self):
        # CRC-8 with polynomial 0x07, init 0: check of "123456789" is F4.
        bits = "".join(format(ord(c), "08b") for c in "123456789")
        self.assertEqual(crc8_remainder(bits), 0xF4)

    def test_zero_message_zero_crc(self):
        self.assertEqual(crc8_bits("00000000", "0" * 16), "00000000")

    def test_step_finish_matches_remainder(self):
        bits = "10110011" + "0" * 24
        reg = 0
        for ch in bits:
            reg = crc_step(reg, int(ch))
        self.assertEqual(format(crc_finish(reg), "08b"),
                         crc8_bits(bits[:8], bits[8:]))

    def test_frame_self_check(self):
        sync = "10110011"
        payload = "01010101" * 3
        crc = crc8_bits(sync, payload)
        self.assertEqual(crc8_remainder(sync + payload), int(crc, 2))


if __name__ == "__main__":
    unittest.main()
