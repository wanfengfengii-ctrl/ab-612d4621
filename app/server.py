"""HTTP API for telemetry frame recovery.

Endpoints
---------
GET  /healthz                 liveness/readiness probe
POST /api/v1/recover          recover a received bit stream

The listen port is configurable via the TELEMETRY_PORT environment
variable (default 8080).  Only the Python standard library is required.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .recovery import recover, CRC_LEN
from .validation import ValidationError, validate_request

API_PATH = "/api/v1/recover"
HEALTH_PATH = "/healthz"
MAX_BODY_BYTES = 1 << 20  # 1 MiB


def _result_payload(result, params):
    frame_count = params["frame_count"]
    sync_word = params["sync_word"]
    payload_len = params["payload_len"]
    base = {
        "frame_count": frame_count,
        "sync_word": sync_word,
        "payload_length": payload_len,
        "frame_length": len(sync_word) + payload_len + CRC_LEN,
        "received_length": len(params["received"]),
        "max_slips": params["max_slips"],
    }
    if result.recoverable:
        base.update({
            "status": "recovered",
            "recoverable": True,
            "min_slips": result.min_slips,
            "unique": bool(result.unique),
            "other_equal_cost_corrections": not bool(result.unique),
            "corrected_bits": result.corrected_bits,
            "corrected_length": len(result.corrected_bits),
            "frames": result.frames,
            "slips": [s.to_dict() for s in result.slips],
        })
    else:
        base.update({
            "status": "unrecoverable",
            "recoverable": False,
            "lower_bound_slips": result.lower_bound_slips,
            "lower_bound_tight": bool(result.lower_bound_tight),
            "searched_slips": result.searched_slips,
            "reason": result.reason,
        })
    return base


# Extra layers searched beyond the request budget to tighten the proven
# lower-bound slip count when no in-budget correction exists.
EXTRA_SEARCH = 2
HARD_CAP = 8


class Handler(BaseHTTPRequestHandler):
    server_version = "TelemetryRecovery/1.0"

    def log_message(self, fmt, *args):  # quiet by default
        if os.environ.get("TELEMETRY_LOG") == "1":
            super().log_message(fmt, *args)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] == HEALTH_PATH:
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not_found",
                                  "message": "unknown path"})

    def do_POST(self):
        if self.path.split("?", 1)[0] != API_PATH:
            self._send_json(404, {"error": "not_found",
                                  "message": "unknown path"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "bad_request",
                                  "fields": [{"field": "Content-Length",
                                              "message": "must be an integer"}]})
            return
        if length <= 0:
            self._send_json(400, {"error": "bad_request",
                                  "fields": [{"field": "_body",
                                              "message": "empty request body"}]})
            return
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "payload_too_large",
                                  "message": "request body exceeds limit"})
            return
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": "bad_request",
                                  "fields": [{"field": "_body",
                                              "message": f"invalid JSON: {exc}"}]})
            return

        try:
            params = validate_request(body)
        except ValidationError as exc:
            self._send_json(422, {"error": "validation_failed",
                                  "fields": exc.fields})
            return

        extended = min(params["max_slips"] + EXTRA_SEARCH, HARD_CAP)
        result = recover(
            params["received"], params["frame_count"], params["sync_word"],
            params["payload_len"], max_slips=params["max_slips"],
            extended_search=extended)
        self._send_json(200, _result_payload(result, params))


def build_server(port=None):
    port = int(port if port is not None
                else os.environ.get("TELEMETRY_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    return server


def main():
    server = build_server()
    port = server.server_address[1]
    print(f"telemetry recovery API listening on 0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
