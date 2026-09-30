"""Request validation with field-level error reporting."""

from .recovery import (FRAMES_MIN, FRAMES_MAX, SYNC_MIN, SYNC_MAX,
                       PAYLOAD_MIN, PAYLOAD_MAX, SLIPS_MAX)


class ValidationError(Exception):
    def __init__(self, fields):
        self.fields = fields
        super().__init__("invalid request")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_bit_string(v):
    return isinstance(v, str) and len(v) > 0 and set(v) <= {"0", "1"}


def validate_request(body):
    """Validate and normalise a recovery request.

    Returns a dict of keyword arguments for ``recovery.recover``.
    Raises ``ValidationError`` with a list of {field, message} entries.
    """
    fields = []
    if not isinstance(body, dict):
        raise ValidationError([{"field": "_body",
                                "message": "request body must be a JSON object"}])

    out = {}

    received = body.get("received_bits")
    if "received_bits" not in body:
        fields.append({"field": "received_bits",
                       "message": "field is required"})
    elif not isinstance(received, str):
        fields.append({"field": "received_bits",
                       "message": "must be a string"})
    elif len(received) == 0:
        fields.append({"field": "received_bits",
                       "message": "must not be empty"})
    elif not set(received) <= {"0", "1"}:
        fields.append({"field": "received_bits",
                       "message": "must contain only '0' and '1' characters"})
    else:
        out["received"] = received

    nf = body.get("frame_count")
    if "frame_count" not in body:
        fields.append({"field": "frame_count",
                       "message": "field is required"})
    elif not _is_int(nf):
        fields.append({"field": "frame_count",
                       "message": "must be an integer"})
    elif not FRAMES_MIN <= nf <= FRAMES_MAX:
        fields.append({"field": "frame_count",
                       "message": f"must be between {FRAMES_MIN} and "
                                  f"{FRAMES_MAX}"})
    else:
        out["frame_count"] = nf

    sync = body.get("sync_word")
    if "sync_word" not in body:
        fields.append({"field": "sync_word",
                       "message": "field is required"})
    elif not isinstance(sync, str):
        fields.append({"field": "sync_word",
                       "message": "must be a string"})
    elif not SYNC_MIN <= len(sync) <= SYNC_MAX:
        fields.append({"field": "sync_word",
                       "message": f"must be {SYNC_MIN}-{SYNC_MAX} bits long"})
    elif not set(sync) <= {"0", "1"}:
        fields.append({"field": "sync_word",
                       "message": "must contain only '0' and '1' characters"})
    else:
        out["sync_word"] = sync

    pl = body.get("payload_length")
    if "payload_length" not in body:
        fields.append({"field": "payload_length",
                       "message": "field is required"})
    elif not _is_int(pl):
        fields.append({"field": "payload_length",
                       "message": "must be an integer"})
    elif not PAYLOAD_MIN <= pl <= PAYLOAD_MAX:
        fields.append({"field": "payload_length",
                       "message": f"must be between {PAYLOAD_MIN} and "
                                  f"{PAYLOAD_MAX}"})
    else:
        out["payload_len"] = pl

    slips = body.get("max_slips", SLIPS_MAX)
    if not _is_int(slips):
        fields.append({"field": "max_slips",
                       "message": "must be an integer"})
    elif not 0 <= slips <= SLIPS_MAX:
        fields.append({"field": "max_slips",
                       "message": f"must be between 0 and {SLIPS_MAX}"})
    else:
        out["max_slips"] = slips

    if fields:
        raise ValidationError(fields)
    return out
