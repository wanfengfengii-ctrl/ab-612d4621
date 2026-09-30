import random
import unittest

from app.crc import crc8_bits
from app.recovery import recover

SYNC = "10110011"
PL = 24
N = 4


def make_stream(seed=1, sync=SYNC, n=N, pl=PL):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        p = "".join(rnd.choice("01") for _ in range(pl))
        out.append(sync + p + crc8_bits(sync, p))
    return "".join(out)


def damage(stream, seed, ins=0, dele=0):
    rnd = random.Random(seed)
    r = list(stream)
    for _ in range(ins):
        r.insert(rnd.randrange(len(r) + 1), rnd.choice("01"))
    for _ in range(dele):
        del r[rnd.randrange(len(r))]
    return "".join(r)


def assert_valid_frames(testcase, result, sync=SYNC, n=N, pl=PL):
    testcase.assertTrue(result.recoverable)
    testcase.assertEqual(len(result.frames), n)
    for f in result.frames:
        testcase.assertEqual(f["sync_word"], sync)
        testcase.assertEqual(len(f["payload"]), pl)
        testcase.assertEqual(f["crc"], crc8_bits(sync, f["payload"]))
        testcase.assertIs(f["crc_valid"], True)


def assert_marks_reproduce(testcase, received, result):
    """Replay the alignment marks; they must rebuild the received stream."""
    corrected = result.corrected_bits
    marks = [(s.kind, s.index, s.corrected_index, s.received_index, s.bit)
             for s in result.slips]
    i = k = 0
    rebuilt = []
    for kind, idx, cidx, ridx, bit in marks:
        if kind == "insertion":
            a, b = idx, cidx  # received pos, corrected gap pos
            rebuilt.append(corrected[k:b])   # intervening matches
            rebuilt.append(bit)              # the inserted received bit
            i, k = a + 1, b
        else:
            a, b = idx, ridx  # corrected pos of dropped bit, received gap
            rebuilt.append(corrected[k:a])   # intervening matches
            i, k = b, a + 1                   # corrected[a] was dropped
    rebuilt.append(corrected[k:])
    testcase.assertEqual("".join(rebuilt), received)


class RecoveryTests(unittest.TestCase):
    def test_clean_stream(self):
        s = make_stream()
        r = recover(s, N, SYNC, PL)
        self.assertTrue(r.recoverable)
        self.assertEqual(r.min_slips, 0)
        self.assertEqual(r.corrected_bits, s)
        self.assertTrue(r.unique)

    def test_single_insertion(self):
        s = make_stream()
        received = s[:30] + "1" + s[30:]
        r = recover(received, N, SYNC, PL)
        assert_valid_frames(self, r)
        self.assertEqual(r.min_slips, 1)
        self.assertEqual(r.corrected_bits, s)
        self.assertEqual(len(r.slips), 1)
        self.assertEqual(r.slips[0].kind, "insertion")
        self.assertEqual(r.slips[0].index, 30)
        assert_marks_reproduce(self, received, r)

    def test_single_deletion(self):
        s = make_stream()
        received = s[:33] + s[34:]
        r = recover(received, N, SYNC, PL)
        assert_valid_frames(self, r)
        self.assertEqual(r.min_slips, 1)
        self.assertEqual(r.corrected_bits, s)
        self.assertEqual(r.slips[0].kind, "deletion")
        # Position 33 lies inside a zero run; any gap placement on that run
        # is an equivalent alignment.  Kind, bit and reproducibility are
        # what matter (assert_marks_reproduce below).
        self.assertEqual(r.slips[0].bit, "0")
        assert_marks_reproduce(self, received, r)

    def test_mixed_slips(self):
        s = make_stream(2)
        received = s[:10] + s[11:50] + "0" + s[50:70] + "1" + s[70:]
        r = recover(received, N, SYNC, PL)
        assert_valid_frames(self, r)
        self.assertEqual(r.min_slips, 3)
        # The lexicographically smallest correction may differ from the
        # original when an inserted/deleted bit sits on an equal bit run,
        # but the reported slips must exactly explain the received stream.
        assert_marks_reproduce(self, received, r)
        self.assertLessEqual(r.corrected_bits, s)

    def test_six_slips_budget(self):
        s = make_stream(3)
        received = damage(s, 99, ins=3, dele=3)
        r = recover(received, N, SYNC, PL, max_slips=6)
        assert_valid_frames(self, r)
        self.assertLessEqual(r.min_slips, 6)
        self.assertEqual(len(r.corrected_bits), len(s))

    def test_corrupted_bit_needs_two_slips(self):
        s = make_stream()
        flipped = s[:40] + ("1" if s[40] == "0" else "0") + s[41:]
        r = recover(flipped, N, SYNC, PL, max_slips=1, extended_search=3)
        self.assertFalse(r.recoverable)
        self.assertEqual(r.lower_bound_slips, 2)
        self.assertTrue(r.lower_bound_tight)

    def test_budget_exceeded_length(self):
        s = make_stream()
        r = recover(s + "00000000", N, SYNC, PL)
        self.assertFalse(r.recoverable)
        self.assertEqual(r.lower_bound_slips, 8)
        self.assertIsNone(r.frames)
        self.assertIsNone(r.corrected_bits)

    def test_unrecoverable_within_budget(self):
        s = make_stream(4)
        bad = list(s)
        bad[20] = "1" if bad[20] == "0" else "0"
        bad[130] = "1" if bad[130] == "0" else "0"
        r = recover("".join(bad), N, SYNC, PL, max_slips=2,
                    extended_search=4)
        self.assertFalse(r.recoverable)
        self.assertGreaterEqual(r.lower_bound_slips, 3)

    def test_no_partial_frames_or_guesses(self):
        s = make_stream()
        r = recover(s[:-5], N, SYNC, PL)
        if r.recoverable:
            self.assertEqual(len(r.corrected_bits),
                             N * (len(SYNC) + PL + 8))
            assert_valid_frames(self, r)
        else:
            self.assertIsNone(r.frames)
            self.assertIsNone(r.corrected_bits)

    def test_sync_word_inside_payload_is_not_frame_start(self):
        sync = "101101"
        pl = 16
        rnd = random.Random(5)
        payload = sync + "".join(
            rnd.choice("01") for _ in range(pl - len(sync)))
        stream = "".join(
            sync + payload + crc8_bits(sync, payload) for _ in range(3))
        r = recover(stream, 3, sync, pl)
        self.assertTrue(r.recoverable)
        self.assertEqual(r.min_slips, 0)
        self.assertTrue(all(f["payload"] == payload for f in r.frames))

    def test_lexicographically_minimal_on_zero_run(self):
        sync = "000000"
        pl = 16
        frame = sync + "0" * pl + crc8_bits(sync, "0" * pl)
        stream = frame * 3
        received = stream[:20] + "0" + stream[20:]
        r = recover(received, 3, sync, pl)
        self.assertTrue(r.recoverable)
        self.assertEqual(r.corrected_bits, min(r.corrected_bits, stream))

    def test_randomised_roundtrip(self):
        for seed in range(20):
            with self.subTest(seed=seed):
                s = make_stream(100 + seed)
                k = seed % 5
                received = damage(s, 1000 + seed,
                                  ins=(k + 1) // 2, dele=k // 2)
                r = recover(received, N, SYNC, PL, max_slips=6)
                assert_valid_frames(self, r)
                self.assertEqual(len(r.corrected_bits), len(s))
                self.assertLessEqual(r.min_slips, 6)


if __name__ == "__main__":
    unittest.main()
