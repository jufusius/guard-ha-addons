"""Rate limity na zákazníka (cid) v klouzavém okně. Stav se ukládá přes GuardApi.load/save
pod vlastním klíčem, takže limit platí napříč konverzacemi i instancemi enginu.

Doplňuje limity v Guard write-gateway (viz docs/GuardApi.md), nenahrazuje je.
"""
from __future__ import annotations

RATE_KEY = "tv_optimize_rate"

# druh → (max. počet, okno v sekundách)
LIMITS = {
    "adb": (30, 60),
    "service": (10, 60),
    "config_flow": (3, 3600),
    "probe": (10, 60),        # port_status, check_dot
}


class RateLimited(Exception):
    def __init__(self, kind: str, retry_after: int):
        super().__init__(f"limit {kind} překročen, zkusit za {retry_after} s")
        self.kind = kind
        self.retry_after = retry_after


def hit(api, cid: int, kind: str, now: float, limits: dict | None = None) -> None:
    """Započítá jedno volání. Když je limit vyčerpaný, vyhodí RateLimited a nic nezapočítá."""
    limit, window = (limits or LIMITS)[kind]
    st = api.load(cid, RATE_KEY) or {}
    recent = [t for t in st.get(kind, []) if t > now - window]
    if len(recent) >= limit:
        raise RateLimited(kind, max(1, int(recent[0] + window - now) + 1))
    recent.append(now)
    st[kind] = recent
    api.save(cid, RATE_KEY, st)
