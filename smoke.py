"""End-to-end recovery smoke test, runnable against a live API server.

Covers the case the operator cares about most: a sync word embedded inside
payload data (a "pseudo sync") plus a real bit slip, which naive
sync-word scanning would misinterpret as a frame boundary.

Usage:
    python smoke.py [base_url]            # default http://127.0.0.1:$TELEMETRY_PORT
Exit code 0 on success, 1 on failure.
"""

import json
import os
import sys
import urllib.error
import urllib.request

from app.crc import crc8_bits


def _request(base, method, path, payload=None, timeout=30):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers,
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read())


def run_smoke(base):
    """Return a list of failure messages (empty on success)."""
    failures = []

    status, body = _request(base, "GET", "/healthz")
    if status != 200 or body.get("status") != "ok":
        failures.append(f"health check failed: {status} {body}")
        return failures  # nothing else can work

    sync = "1100110011"  # 10-bit sync word
    pl = 20
    n = 4
    # Payload literally starts with the sync word: a pseudo frame marker.
    payload = sync + "0101111100"
    assert len(payload) == pl
    frame = sync + payload + crc8_bits(sync, payload)
    stream = frame * n

    # Case 1: clean stream.
    status, body = _request(base, "POST", "/api/v1/recover", {
        "received_bits": stream,
        "frame_count": n,
        "sync_word": sync,
        "payload_length": pl,
    })
    if status != 200 or body.get("status") != "recovered":
        failures.append(f"clean stream not recovered: {status} {body}")
    elif body.get("corrected_bits") != stream or body.get("min_slips") != 0:
        failures.append(f"clean stream result wrong: {body.get('status')} "
                        f"slips={body.get('min_slips')}")
    elif not all(f["sync_word"] == sync and f["payload"] == payload
                 and f["crc_valid"] for f in body.get("frames", [])):
        failures.append("clean stream frames parsed incorrectly")

    # Case 2: one inserted bit right next to the in-payload pseudo sync.
    pos = len(frame) + len(sync)
    received = stream[:pos] + "1" + stream[pos:]
    status, body = _request(base, "POST", "/api/v1/recover", {
        "received_bits": received,
        "frame_count": n,
        "sync_word": sync,
        "payload_length": pl,
    })
    if status != 200 or body.get("status") != "recovered":
        failures.append(f"slip case not recovered: {status} {body}")
    elif body.get("corrected_bits") != stream:
        failures.append("slip case corrected stream differs from original")
    elif body.get("min_slips") != 1:
        failures.append(
            f"slip case expected 1 slip, got {body.get('min_slips')}")
    elif len(body.get("slips", [])) != 1:
        failures.append("slip case expected exactly one slip annotation")

    # Case 3: unrecoverable input reports a lower bound, never a guess.
    bad = list(stream)
    if bad:
        bad[5] = "1" if bad[5] == "0" else "0"
        bad[-5] = "1" if bad[-5] == "0" else "0"
    status, body = _request(base, "POST", "/api/v1/recover", {
        "received_bits": "".join(bad),
        "frame_count": n,
        "sync_word": sync,
        "payload_length": pl,
        "max_slips": 1,
    })
    if status != 200 or body.get("status") != "unrecoverable":
        failures.append(f"expected unrecoverable: {status} {body}")
    elif "frames" in body or "corrected_bits" in body:
        failures.append("unrecoverable response must not contain frames")
    elif not isinstance(body.get("lower_bound_slips"), int):
        failures.append("unrecoverable response lacks lower_bound_slips")

    return failures


def main(argv):
    port = os.environ.get("TELEMETRY_PORT", "8080")
    base = argv[1] if len(argv) > 1 else f"http://127.0.0.1:{port}"
    failures = run_smoke(base)
    if failures:
        for msg in failures:
            print(f"SMOKE FAIL: {msg}", file=sys.stderr)
        return 1
    print("smoke test passed (pseudo-sync + bit-slip recovery)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
