"""Brute-force reference solver for differential testing.

Enumerates every edit script (match / delete / insert 0|1) of at most
``budget`` slips that turns the received stream into a corrected stream
parsing as exactly frame_count valid frames.  Returns {corrected: mincost}.
"""

from app.crc import crc_step, crc_finish


def _frame_ok_at(corr, t, sync_word, sync_len, payload_len):
    frame_len = sync_len + payload_len + 8
    base = t * frame_len
    if corr[base:base + sync_len] != sync_word:
        return False
    reg = 0
    for k in range(base, base + sync_len + payload_len):
        reg = crc_step(reg, 1 if corr[k] == "1" else 0)
    crc = format(crc_finish(reg), "08b")
    return corr[base + sync_len + payload_len:base + frame_len] == crc


def brute(received, frame_count, sync_word, payload_len, budget):
    sync_len = len(sync_word)
    frame_len = sync_len + payload_len + 8
    n = len(received)
    total = frame_count * frame_len
    results = {}

    def valid_prefix(corr):
        # If corr ends exactly on a frame boundary, all complete frames
        # must verify.
        done, rem = divmod(len(corr), frame_len)
        if rem == 0:
            for t in range(done):
                if not _frame_ok_at(corr, t, sync_word, sync_len,
                                    payload_len):
                    return False
        else:
            t = done
            base = t * frame_len
            # sync portion must still match
            upto = min(rem, sync_len)
            if corr[base:base + upto] != sync_word[:upto]:
                return False
        return True

    def rec(i, corr, cost):
        if len(corr) == total:
            if i == n:
                if valid_prefix(corr):
                    results[corr] = min(results.get(corr, 1 << 30), cost)
            return
        if cost > budget:
            return
        rem_corr = total - len(corr)
        if abs((n - i) - rem_corr) > budget - cost:
            return

        # match
        if i < n:
            c = corr + received[i]
            if valid_prefix(c):
                rec(i + 1, c, cost)
            # delete this received bit
            rec(i + 1, corr, cost + 1)
        # insert
        for bit in ("0", "1"):
            c = corr + bit
            if valid_prefix(c):
                rec(i, c, cost + 1)

    rec(0, "", 0)
    return results
