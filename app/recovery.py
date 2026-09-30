"""Joint bit-slip recovery for a stream of fixed-length telemetry frames.

Original uplink: ``frame_count`` concatenated frames of
``sync_word || payload || crc8``.  The received string may contain sporadic
single-bit insertions and deletions ("slips").  The whole stream is solved
jointly:

1. minimise the number of slips;
2. among minimum-slip corrections, take the lexicographically smallest
   corrected bit string (``"0" < "1"``);
3. report whether that optimum is unique.

Implementation
--------------
A layered reachability DP over the slip budget on a compact structural
state space::

    (nf, p, q, d)
      nf : number of completed frames
      p  : position inside the current frame (0..L-1)
      q  : running CRC-8 register (a sentinel marks the frame boundary)
      d  : i - (nf*L + p), the current input/corrected offset

Zero-cost moves (a received bit becomes the next corrected bit) are taken to
a fixpoint inside each layer; insertions/deletions cost one and open the
next layer.  Sync bits must match the sync word, payload bits clock the CRC
register, and CRC bits are *forced* from the register (a mismatch dies
immediately); hence reachability depends solely on the structural state,
and a sync word embedded in payload data can never start a frame.

Forward reachability gives the minimum slip count; backward reachability
(together with the precomputed emission-predecessor table) lets a greedy
walk emit the lexicographically smallest stream and decide uniqueness.
"""

from array import array
from dataclasses import dataclass
from typing import Optional

from .crc import crc_step, crc_finish

SYNC_MIN = 6
SYNC_MAX = 12
FRAMES_MIN = 3
FRAMES_MAX = 8
PAYLOAD_MIN = 16
PAYLOAD_MAX = 48
SLIPS_MAX = 6
CRC_LEN = 8

QS = 256  # sentinel register value at a frame boundary


@dataclass(frozen=True)
class Slip:
    """A single slip event.

    insertion: the link added a bit.  ``index`` is its 0-based position in
        the received stream; ``corrected_index`` is the corresponding
        insertion point in the corrected stream.
    deletion: the link dropped a bit.  ``index`` is the 0-based position of
        the lost bit in the corrected (original) stream;
        ``received_index`` is the gap position in the received stream.
    """

    kind: str
    index: int
    bit: Optional[str] = None
    corrected_index: Optional[int] = None
    received_index: Optional[int] = None

    def to_dict(self):
        if self.kind == "insertion":
            d = {"type": "insertion", "index": self.index,
                 "stream": "received", "bit": self.bit}
            if self.corrected_index is not None:
                d["corrected_index"] = self.corrected_index
        else:
            d = {"type": "deletion", "index": self.index,
                 "stream": "corrected", "bit": self.bit}
            if self.received_index is not None:
                d["received_index"] = self.received_index
        return d


@dataclass
class RecoveryResult:
    recoverable: bool
    min_slips: Optional[int] = None
    unique: Optional[bool] = None
    corrected_bits: Optional[str] = None
    frames: Optional[list] = None
    slips: Optional[list] = None
    max_slips: Optional[int] = None
    lower_bound_slips: Optional[int] = None
    lower_bound_tight: Optional[bool] = None
    searched_slips: Optional[int] = None
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Emission tables (successors and predecessors per frame position)
# ---------------------------------------------------------------------------

class _Tables:
    """Per-position emission outcomes for one frame layout.

    nxt[b][p][q] encodes the cell state after emitting bit b (0/1) at
    frame position p with register q: np_*257+nq (np_==L means the frame
    closes), or -1 if the bit is illegal there.

    pred[(np_, nq)] lists (p, qpre, bit) predecessors within a frame;
    closing predecessors are stored separately in close_pred.
    """

    __slots__ = ("L", "S", "P", "sync", "nxt", "pred", "close_pred")

    def __init__(self, sync_word: str, payload_len: int):
        S = len(sync_word)
        P = payload_len
        L = S + P + CRC_LEN

        nxt = [[array("i", [-1]) * 257 for _ in range(L)] for _ in range(2)]
        for p in range(L):
            q_values = (QS,) if p == 0 else range(256)
            for q in q_values:
                if p < S:
                    reg = 0 if q == QS else q
                    b = int(sync_word[p])
                    nxt[b][p][q] = _enc(p, crc_step(reg, b))
                elif p < S + P:
                    nxt[0][p][q] = _enc(p, crc_step(q, 0))
                    nxt[1][p][q] = _enc(p, crc_step(q, 1))
                else:
                    b = int(format(crc_finish(q), "08b")[p - S - P])
                    nxt[b][p][q] = _enc(p, q)

        # Predecessors, built once by scanning the successor table.
        pred = {}   # (np_, nq) -> list[(qpre, bit)]
        close_pred = []  # qpre values whose forced final bit closes frame
        for p in range(L):
            for qpre in range(257):
                for b in (0, 1):
                    e = nxt[b][p][qpre]
                    if e < 0:
                        continue
                    np_, nq = e // 257, e % 257
                    if np_ == L:
                        if p == L - 1:
                            close_pred.append((qpre, b))
                    else:
                        pred.setdefault((np_, nq), []).append((qpre, b))

        self.L = L
        self.S = S
        self.P = P
        self.sync = sync_word
        self.nxt = nxt
        self.pred = pred
        self.close_pred = close_pred


def _enc(p, nq):
    return (p + 1) * 257 + nq


# ---------------------------------------------------------------------------
# Layered structural DP
# ---------------------------------------------------------------------------

class _Solver:
    def __init__(self, received: str, frame_count: int, tab: _Tables,
                 budget: int):
        self.r = received
        self.n = len(received)
        self.N = frame_count
        self.t = tab
        self.L = tab.L
        self.S = tab.S
        self.B = budget
        self.D = 2 * budget + 1
        self.stride_q = self.D
        self.stride_p = 257 * self.D
        self.stride_nf = self.L * self.stride_p
        self.total = (frame_count + 1) * self.stride_nf

    def cell(self, nf, p, q, d):
        return nf * self.stride_nf + p * self.stride_p + q * self.D \
            + (d + self.B)

    def decode(self, c):
        d = c % self.D - self.B
        z = c // self.D
        q = z % 257
        z //= 257
        p = z % self.L
        nf = z // self.L
        return nf, p, q, d

    def _emit(self, nf, enc, d):
        np_, nq = enc // 257, enc % 257
        if np_ == self.L:
            return self.cell(nf + 1, 0, QS, d)
        return self.cell(nf, np_, nq, d)

    def _valid_i(self, nf, p, d):
        i = nf * self.L + p + d
        return 0 <= i <= self.n

    # --- forward zero-cost closure ----------------------------------------

    def match_closure(self, seen, active):
        """Expand zero-cost matches until fixpoint (in place)."""
        r, n, L, N, t = self.r, self.n, self.L, self.N, self.t
        head = 0
        while head < len(active):
            c = active[head]
            head += 1
            nf, p, q, d = self.decode(c)
            i = nf * L + p + d
            if i >= n or (p == 0 and nf == N):
                continue
            enc = t.nxt[ord(r[i]) - 48][p][q]
            if enc < 0:
                continue
            c2 = self._emit(nf, enc, d)
            if not seen[c2]:
                seen[c2] = 1
                active.append(c2)

    def cross(self, active_src):
        """All one-slip successors (deletion / insertion) of active cells."""
        r, n, L, N, t = self.r, self.n, self.L, self.N, self.t
        seeds = array("i")
        out = bytearray(self.total)
        for c in active_src:
            nf, p, q, d = self.decode(c)
            i = nf * L + p + d
            # Deletion: consume one received bit, emit nothing.  Allowed
            # even after all frames are emitted (trailing inserted bits).
            if i < n:
                nd = d + 1
                if nd <= self.B:
                    c2 = self.cell(nf, p, q, nd)
                    if not out[c2] and self._valid_i(nf, p, nd):
                        out[c2] = 1
                        seeds.append(c2)
            # Insertion: emit a corrected bit, consume nothing.
            if nf < N:
                nd = d - 1
                if nd >= -self.B:
                    for b in (0, 1):
                        enc = t.nxt[b][p][q]
                        if enc < 0:
                            continue
                        np_ = enc // 257
                        eff_p = 0 if np_ == L else np_
                        eff_nf = nf + 1 if np_ == L else nf
                        c2 = self._emit(nf, enc, nd)
                        if not out[c2] and self._valid_i(eff_nf, eff_p, nd):
                            out[c2] = 1
                            seeds.append(c2)
        return out, seeds

    def forward(self):
        """Layered forward search; return the minimum slip cost or None."""
        goal_d = self.n - self.N * self.L
        if abs(goal_d) > self.B:
            return None
        goal = self.cell(self.N, 0, QS, goal_d)

        seen = bytearray(self.total)
        start = self.cell(0, 0, QS, 0)
        seen[start] = 1
        active = array("i", [start])
        self.match_closure(seen, active)

        for cost in range(self.B + 1):
            if seen[goal]:
                return cost
            if cost == self.B:
                return None
            seed_seen, seeds = self.cross(active)
            if not seeds:
                return None
            self.match_closure(seed_seen, seeds)
            seen, active = seed_seen, seeds

    # --- backward ----------------------------------------------------------

    def reverse_closure(self, seen, active):
        """Expand reverse zero-cost matches until fixpoint (in place)."""
        r, n, L, N, t = self.r, self.n, self.L, self.N, self.t
        head = 0
        while head < len(active):
            c = active[head]
            head += 1
            nf, p, q, d = self.decode(c)
            i_after = nf * L + p + d
            i_pre = i_after - 1
            if i_pre < 0 or i_pre >= n:
                continue
            bit = ord(r[i_pre]) - 48
            if p > 0:
                for qpre, b in t.pred.get((p, q), ()):
                    if b != bit:
                        continue
                    c2 = self.cell(nf, p - 1, qpre, d)
                    if not seen[c2] and self._valid_i(nf, p - 1, d):
                        seen[c2] = 1
                        active.append(c2)
            elif nf > 0:
                # The matched bit was the closing CRC bit of previous frame.
                for qpre, b in t.close_pred:
                    if b != bit:
                        continue
                    c2 = self.cell(nf - 1, L - 1, qpre, d)
                    if not seen[c2] and self._valid_i(nf - 1, L - 1, d):
                        seen[c2] = 1
                        active.append(c2)

    def reverse_cross(self, active_src):
        """States from which one slip reaches a state in active_src."""
        n, L, N, t = self.n, self.L, self.N, self.t
        seeds = array("i")
        out = bytearray(self.total)

        def add(c2, nf, p, pd):
            if not out[c2] and self._valid_i(nf, p, pd):
                out[c2] = 1
                seeds.append(c2)

        for c in active_src:
            nf, p, q, d = self.decode(c)
            i_after = nf * L + p + d
            # Reverse deletion: a bit was dropped just before i_after.
            if i_after > 0:
                pd = d - 1
                if pd >= -self.B:
                    add(self.cell(nf, p, q, pd), nf, p, pd)
            # Reverse insertion: previous corrected position, same i.
            pd = d + 1
            if pd <= self.B and 0 <= i_after <= n:
                if p > 0:
                    for qpre, _b in t.pred.get((p, q), ()):
                        add(self.cell(nf, p - 1, qpre, pd), nf, p - 1, pd)
                elif nf > 0:
                    for qpre, _b in t.close_pred:
                        add(self.cell(nf - 1, L - 1, qpre, pd),
                            nf - 1, L - 1, pd)
        return out, seeds

    def backward(self):
        """Cumulative remaining-budget layers G[0..B].

        G[r] is the set of cells from which the goal is reachable using at
        most r more slips.  Each layer is an independent bytearray copy.
        """
        goal_d = self.n - self.N * self.L
        goal = self.cell(self.N, 0, QS, goal_d)

        seen = bytearray(self.total)
        seen[goal] = 1
        active = array("i", [goal])
        self.reverse_closure(seen, active)
        layers = [bytearray(seen)]
        for _ in range(self.B):
            seed_seen, seeds = self.reverse_cross(active)
            # `seeds` grows inside reverse_closure with every newly reached
            # cell, so afterwards it enumerates the full fresh layer.
            self.reverse_closure(seed_seen, seeds)
            fresh = array("i")
            for c in seeds:
                if not seen[c]:
                    seen[c] = 1
                    fresh.append(c)
            layers.append(bytearray(seen))
            active = fresh
        return layers


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------

def _canonical_marks(received, corrected, cost):
    """Edit alignment of received -> corrected with exactly ``cost`` slips.

    Returns the lexicographically smallest marks tuple producing the given
    corrected string.

    Marks:
      ("i", received_index, corrected_index, bit)
          a bit the link inserted (present in received, absent in
          corrected);
      ("d", corrected_index, received_index, bit)
          a bit the link dropped (present in corrected, absent in
          received).
    """
    n, m = len(received), len(corrected)

    def close(cur):
        """Take zero-cost matches to a fixpoint, keeping min marks."""
        stack = list(cur.items())
        while stack:
            (i, k), marks = stack.pop()
            if i < n and k < m and received[i] == corrected[k]:
                key = (i + 1, k + 1)
                old = cur.get(key)
                if old is None or marks < old:
                    cur[key] = marks
                    stack.append((key, marks))

    cur = {(0, 0): ()}
    close(cur)
    for c in range(cost + 1):
        if (n, m) in cur:
            return cur[(n, m)]
        if c == cost:
            break
        nxt = {}
        for (i, k), marks in cur.items():
            # Link insertion: received[i] has no corrected counterpart.
            if i < n:
                key = (i + 1, k)
                nm = marks + (("i", i, k, received[i]),)
                if key not in nxt or nm < nxt[key]:
                    nxt[key] = nm
            # Link deletion: corrected[k] was missing from received.
            if k < m:
                key = (i, k + 1)
                nm = marks + (("d", k, i, corrected[k]),)
                if key not in nxt or nm < nxt[key]:
                    nxt[key] = nm
        close(nxt)
        cur = nxt
    return None


def _reconstruct(solver: _Solver, G, min_cost):
    """Greedy lexicographically smallest completion + uniqueness check.

    G[r] is the cumulative "goal reachable with at most r more slips"
    backward layer.  A front of feasible (cell, slips_used) pairs is
    maintained: committing to a single state would fix one alignment and
    could drop equally good continuations.
    """
    r, n, L, N, t = solver.r, solver.n, solver.L, solver.N, solver.t
    total = N * L

    front = {(solver.cell(0, 0, QS, 0), 0)}
    bits = []
    unique = True

    for _ in range(total):
        per_bit = {0: set(), 1: set()}
        for cell, used in front:
            nf, p, q, d = solver.decode(cell)
            i0 = nf * L + p + d
            for b in (0, 1):
                enc = t.nxt[b][p][q]
                if enc < 0:
                    continue
                for j in range(min_cost - used + 1):
                    ij = i0 + j
                    u = used + j
                    if ij > n:
                        break
                    # Match of this corrected bit after j deletions.
                    if ij < n and ord(r[ij]) - 48 == b:
                        c2 = solver._emit(nf, enc, d + j)
                        rem = min_cost - u
                        if rem >= 0 and G[rem][c2]:
                            per_bit[b].add((c2, u))
                    # Insertion: corrected bit without consuming input.
                    if u + 1 <= min_cost:
                        c2 = solver._emit(nf, enc, d + j - 1)
                        rem = min_cost - (u + 1)
                        if G[rem][c2]:
                            per_bit[b].add((c2, u + 1))

        if not per_bit[0] and not per_bit[1]:
            raise RuntimeError("reconstruction failed")
        chosen = 0 if per_bit[0] else 1
        if per_bit[0] and per_bit[1]:
            unique = False
        bits.append(str(chosen))
        front = per_bit[chosen]

    return "".join(bits), unique


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _split_frames(corrected, frame_count, sync_len, payload_len):
    frame_len = sync_len + payload_len + CRC_LEN
    frames = []
    for idx in range(frame_count):
        base = idx * frame_len
        frames.append({
            "index": idx + 1,
            "sync_word": corrected[base:base + sync_len],
            "payload": corrected[base + sync_len:base + sync_len + payload_len],
            "crc": corrected[base + sync_len + payload_len:base + frame_len],
            "crc_valid": True,
        })
    return frames


def _solve_full(received, frame_count, sync_word, payload_len, budget):
    tab = _Tables(sync_word, payload_len)
    solver = _Solver(received, frame_count, tab, budget)
    cost = solver.forward()
    if cost is None:
        return None
    G = solver.backward()
    corrected, unique = _reconstruct(solver, G, cost)
    marks = _canonical_marks(received, corrected, cost)
    return cost, corrected, unique, marks


def recover(received: str, frame_count: int, sync_word: str,
            payload_len: int, max_slips: int = SLIPS_MAX,
            extended_search: int = 0):
    """Recover the frame stream.

    ``extended_search`` (a slip count above max_slips) is only used to
    tighten the proven lower bound when no in-budget correction exists.
    """
    sync_len = len(sync_word)
    frame_len = sync_len + payload_len + CRC_LEN
    expected_len = frame_count * frame_len
    length_diff = abs(len(received) - expected_len)

    # Each slip changes the length gap by exactly one; a gap beyond the
    # budget proves impossibility without any search.
    if length_diff > max_slips:
        result = RecoveryResult(
            recoverable=False,
            max_slips=max_slips,
            lower_bound_slips=length_diff,
            lower_bound_tight=False,
            searched_slips=max_slips,
            reason=(
                "stream length differs from the expected frame stream by "
                f"{length_diff} bits, exceeding the slip budget "
                f"({max_slips}); at least {length_diff} slips are required"
            ),
        )
        return result

    answer = _solve_full(received, frame_count, sync_word, payload_len,
                         max_slips)
    if answer is None:
        result = RecoveryResult(
            recoverable=False,
            max_slips=max_slips,
            lower_bound_slips=max_slips + 1,
            lower_bound_tight=False,
            searched_slips=max_slips,
        )
        if extended_search > max_slips:
            more = _solve_full(received, frame_count, sync_word,
                               payload_len, extended_search)
            result.searched_slips = extended_search
            if more is not None:
                result.lower_bound_slips = more[0]
                result.lower_bound_tight = True
                result.reason = (
                    f"no correction within {max_slips} slips; a correction "
                    f"exists at {more[0]} slips"
                )
            else:
                result.reason = (
                    f"no correction exists within {extended_search} "
                    f"slips; at least {max_slips + 1} slips are required"
                )
        else:
            result.reason = (
                f"no frame-consistent correction within {max_slips} slips; "
                f"at least {max_slips + 1} slips are required"
            )
        return result

    cost, corrected, unique, marks = answer
    slips = []
    for tag, a, bpos, bit in (marks or ()):
        if tag == "i":
            slips.append(Slip("insertion", a, bit, corrected_index=bpos))
        else:
            slips.append(Slip("deletion", a, bit, received_index=bpos))
    return RecoveryResult(
        recoverable=True,
        min_slips=cost,
        unique=unique,
        corrected_bits=corrected,
        frames=_split_frames(corrected, frame_count, sync_len, payload_len),
        slips=slips,
    )
