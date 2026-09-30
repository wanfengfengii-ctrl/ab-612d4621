"""CRC-8 used by the telemetry frames.

Polynomial: x^8 + x^2 + x + 1  (low coefficient byte ``0x07``)
- most significant bit first
- initial remainder zero
- no input/output reflection, no final xor
- the check value is the remainder of (sync_word || payload) followed by
  eight zero bits, divided by the polynomial.
"""

POLY_LOW = 0x07
CRC_BITS = 8


def crc8_remainder(bits: str) -> int:
    """Return the 8-bit remainder of ``bits``.

    Implemented literally as polynomial long division of
    ``bits + "0" * 8``: each bit is shifted in MSB-first with an initial
    zero register; when a bit is shifted out of the top position the low
    coefficient bits of the polynomial (``0x07``) are XORed in.
    """
    rem = 0
    for ch in bits + "0" * CRC_BITS:
        bit = 1 if ch == "1" else 0
        outgoing = (rem >> 7) & 1
        rem = ((rem << 1) | bit) & 0xFF
        if outgoing:
            rem ^= POLY_LOW
    return rem


def crc_step(rem: int, bit: int) -> int:
    """Clock one message bit through the remainder register (no appended 0)."""
    outgoing = (rem >> 7) & 1
    rem = ((rem << 1) | (bit & 1)) & 0xFF
    if outgoing:
        rem ^= POLY_LOW
    return rem


def crc_finish(rem: int) -> int:
    """Clock the eight appended zero bits and return the CRC remainder."""
    for _ in range(CRC_BITS):
        outgoing = (rem >> 7) & 1
        rem = (rem << 1) & 0xFF
        if outgoing:
            rem ^= POLY_LOW
    return rem


def crc8_bits(sync_bits: str, payload_bits: str) -> str:
    """Return the 8 CRC bits (MSB first) for sync + payload."""
    return format(crc8_remainder(sync_bits + payload_bits), "08b")


def frame_is_valid(sync_bits: str, payload_bits: str, crc_bits: str) -> bool:
    """Check an (sync, payload, crc) triple."""
    if len(crc_bits) != CRC_BITS:
        return False
    try:
        return int(crc_bits, 2) == crc8_remainder(sync_bits + payload_bits)
    except ValueError:
        return False
