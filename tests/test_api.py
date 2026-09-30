import json
import threading
import unittest
import urllib.error
import urllib.request

from app.crc import crc8_bits
from app.server import build_server

_SERVER = None
_BASE = None


def setUpModule():
    global _SERVER, _BASE
    _SERVER = build_server(port=0)
    port = _SERVER.server_address[1]
    _BASE = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=_SERVER.serve_forever, daemon=True)
    thread.start()


def tearDownModule():
    _SERVER.shutdown()
    _SERVER.server_close()


def post(path, payload):
    req = urllib.request.Request(
        _BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def get(path):
    with urllib.request.urlopen(_BASE + path, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


class ApiTests(unittest.TestCase):
    def test_healthz(self):
        status, body = get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_recover_roundtrip(self):
        sync = "10110011"
        pl = 24
        payload = "110011001100110011001100"
        stream = "".join(
            sync + payload + crc8_bits(sync, payload) for _ in range(3))
        received = stream[:40] + stream[41:]  # one deletion
        status, body = post("/api/v1/recover", {
            "received_bits": received,
            "frame_count": 3,
            "sync_word": sync,
            "payload_length": pl,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "recovered")
        self.assertEqual(body["min_slips"], 1)
        self.assertEqual(body["corrected_bits"], stream)
        self.assertEqual(len(body["frames"]), 3)

    def test_pseudo_sync_in_payload(self):
        sync = "1100110011"
        pl = 20
        payload = sync + "0101111100"
        self.assertEqual(len(payload), pl)
        stream = "".join(
            sync + payload + crc8_bits(sync, payload) for _ in range(4))
        received = stream[:25] + "1" + stream[25:]
        status, body = post("/api/v1/recover", {
            "received_bits": received,
            "frame_count": 4,
            "sync_word": sync,
            "payload_length": pl,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "recovered")
        self.assertEqual(body["corrected_bits"], stream)
        self.assertTrue(all(
            f["sync_word"] == sync and f["payload"] == payload
            and f["crc_valid"] for f in body["frames"]))

    def test_validation_errors_are_field_specific(self):
        status, body = post("/api/v1/recover", {
            "received_bits": "abc",
            "frame_count": 99,
            "sync_word": "12",
            "payload_length": 7,
        })
        self.assertEqual(status, 422)
        self.assertEqual(body["error"], "validation_failed")
        self.assertEqual(
            {f["field"] for f in body["fields"]},
            {"received_bits", "frame_count", "sync_word",
             "payload_length"})

    def test_unrecoverable_has_no_partial_output(self):
        sync = "10110011"
        pl = 24
        payload = "10101010" * 3
        stream = "".join(
            sync + payload + crc8_bits(sync, payload) for _ in range(3))
        bad = list(stream)
        bad[10] = "1" if bad[10] == "0" else "0"
        bad[90] = "1" if bad[90] == "0" else "0"
        status, body = post("/api/v1/recover", {
            "received_bits": "".join(bad),
            "frame_count": 3,
            "sync_word": sync,
            "payload_length": pl,
            "max_slips": 2,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "unrecoverable")
        self.assertGreaterEqual(body["lower_bound_slips"], 3)
        self.assertNotIn("frames", body)
        self.assertNotIn("corrected_bits", body)

    def test_bad_json(self):
        req = urllib.request.Request(
            _BASE + "/api/v1/recover", data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
