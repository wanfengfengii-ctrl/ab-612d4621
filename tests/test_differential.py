"""Differential tests against a brute-force edit-sequence reference.

Small parameters only (the brute solver is exponential).  These pin the
three optimality guarantees: minimum slips, lexicographically smallest
corrected string, and correct uniqueness verdict.
"""

import random
import unittest

from app.crc import crc8_bits
from app.recovery import recover

from .dbfuzz import brute

SYNC = "101101"
PL = 16
N = 3
B = 2


def make_stream(seed):
    rnd = random.Random(seed)
    out = ""
    for _ in range(N):
        p = "".join(rnd.choice("01") for _ in range(PL))
        out += SYNC + p + crc8_bits(SYNC, p)
    return out


def damage(stream, seed, k):
    rnd = random.Random(seed)
    r = list(stream)
    for _ in range(k):
        pos = rnd.randrange(len(r) + 1)
        if rnd.random() < 0.5:
            r.insert(pos, rnd.choice("01"))
        elif pos < len(r):
            del r[pos]
    return "".join(r)


class DifferentialTests(unittest.TestCase):
    def test_matches_brute_force(self):
        for trial in range(24):
            with self.subTest(trial=trial):
                original = make_stream(trial)
                received = damage(original, 3000 + trial, trial % (B + 1))
                result = recover(received, N, SYNC, PL, max_slips=B)
                reference = brute(received, N, SYNC, PL, B)
                if reference:
                    cost = min(reference.values())
                    optimal = {x for x, v in reference.items() if v == cost}
                    self.assertTrue(result.recoverable)
                    self.assertEqual(result.min_slips, cost)
                    self.assertEqual(result.corrected_bits, min(optimal))
                    self.assertIs(result.unique, len(optimal) == 1)
                else:
                    self.assertFalse(result.recoverable)
                    self.assertEqual(result.lower_bound_slips, B + 1)

    def test_random_garbage_against_brute_force(self):
        rnd = random.Random(4242)
        frame_len = len(SYNC) + PL + 8
        for trial in range(10):
            length = N * frame_len + rnd.randint(-B, B)
            received = "".join(rnd.choice("01") for _ in range(length))
            result = recover(received, N, SYNC, PL, max_slips=B)
            reference = brute(received, N, SYNC, PL, B)
            if reference:
                cost = min(reference.values())
                optimal = {x for x, v in reference.items() if v == cost}
                self.assertEqual(result.min_slips, cost)
                self.assertEqual(result.corrected_bits, min(optimal))
                self.assertIs(result.unique, len(optimal) == 1)
            else:
                self.assertFalse(result.recoverable)


if __name__ == "__main__":
    unittest.main()
