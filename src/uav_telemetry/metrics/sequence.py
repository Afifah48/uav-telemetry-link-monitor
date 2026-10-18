from dataclasses import dataclass, field


@dataclass
class SequenceTracker:
    """Modulo-256 inference with a bounded reorder window (<128).

    Gaps are tentative until aged out; late arrivals within the window repair them.
    More than 127 missed messages, sender reboot and long outages are ambiguous.
    No packet-delivery ratio is inferred from this sequence heuristic.
    """
    window: int = 64
    high: int | None = None
    seen: set[int] = field(default_factory=set)
    pending: set[int] = field(default_factory=set)
    confirmed_missing: int = 0
    received_unique: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    too_old: int = 0
    epochs: int = 0

    def reset(self) -> None:
        self.confirmed_missing += len(self.pending)
        self.pending.clear()
        self.seen.clear()
        self.high = None
        self.epochs += 1

    def update(self, seq: int) -> None:
        if not 0 <= seq < 256:
            raise ValueError("Sequence must fit uint8")
        if self.high is None:
            self.high = seq
            self.seen.add(seq)
            self.received_unique += 1
            return
        delta = (seq - self.high % 256 + 128) % 256 - 128
        absolute = self.high + delta
        if absolute in self.seen:
            self.duplicates += 1
            return
        if absolute < self.high - self.window:
            self.too_old += 1
            return
        if delta > 0:
            self.pending.update(range(self.high + 1, absolute))
            self.high = absolute
        else:
            self.out_of_order += 1
            self.pending.discard(absolute)
        self.seen.add(absolute)
        self.received_unique += 1
        expired = {s for s in self.pending if s < self.high - self.window}
        self.confirmed_missing += len(expired)
        self.pending.difference_update(expired)
        self.seen = {s for s in self.seen if s >= self.high - self.window}

    def snapshot(self) -> dict:
        return {"received_unique": self.received_unique, "duplicates": self.duplicates,
                "out_of_order": self.out_of_order, "too_old_or_ambiguous": self.too_old,
                "confirmed_missing_estimate": self.confirmed_missing,
                "pending_gap_estimate": len(self.pending), "reset_epochs": self.epochs,
                "missing_estimate": self.confirmed_missing + len(self.pending)}
