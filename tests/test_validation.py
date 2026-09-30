import unittest

from app.validation import validate_request, ValidationError


def err_fields(body):
    try:
        validate_request(body)
    except ValidationError as exc:
        return {f["field"] for f in exc.fields}
    raise AssertionError("validation unexpectedly passed")


VALID = {
    "received_bits": "10110011" * 5,
    "frame_count": 3,
    "sync_word": "10110011",
    "payload_length": 16,
}


class ValidationTests(unittest.TestCase):
    def test_valid_request(self):
        out = validate_request(dict(VALID))
        self.assertEqual(out["frame_count"], 3)
        self.assertEqual(out["max_slips"], 6)

    def test_missing_fields_all_reported(self):
        fields = err_fields({})
        self.assertIn("received_bits", fields)
        self.assertIn("frame_count", fields)
        self.assertIn("sync_word", fields)
        self.assertIn("payload_length", fields)

    def test_received_must_be_bit_string(self):
        self.assertIn("received_bits",
                      err_fields(dict(VALID, received_bits="10ab1")))

    def test_frame_count_bounds(self):
        for bad_n in (2, 9, 0, -3, "3", 3.0, True):
            self.assertIn("frame_count",
                          err_fields(dict(VALID, frame_count=bad_n)))

    def test_sync_word_bounds(self):
        self.assertIn("sync_word",
                      err_fields(dict(VALID, sync_word="10110")))
        self.assertIn("sync_word",
                      err_fields(dict(VALID, sync_word="1" * 13)))
        self.assertIn("sync_word",
                      err_fields(dict(VALID, sync_word="abcdef")))
        out = validate_request(dict(VALID, sync_word="1" * 12))
        self.assertEqual(out["sync_word"], "1" * 12)

    def test_payload_length_bounds(self):
        for bad_pl in (15, 49, -1, "16", 16.0):
            self.assertIn("payload_length",
                          err_fields(dict(VALID, payload_length=bad_pl)))

    def test_max_slips_bounds(self):
        self.assertIn("max_slips",
                      err_fields(dict(VALID, max_slips=7)))
        self.assertIn("max_slips",
                      err_fields(dict(VALID, max_slips=-1)))
        self.assertEqual(
            validate_request(dict(VALID, max_slips=0))["max_slips"], 0)

    def test_body_must_be_object(self):
        with self.assertRaises(ValidationError):
            validate_request([1, 2, 3])

    def test_empty_received(self):
        self.assertIn("received_bits",
                      err_fields(dict(VALID, received_bits="")))


if __name__ == "__main__":
    unittest.main()
