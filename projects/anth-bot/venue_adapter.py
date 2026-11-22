"""Unified DEX Venue Adapter interface & multi-venue spread matrix engine.

To onboard a new DEX (Hyperliquid, Lighter, dYdX, Paradex, EdgeX, Aster, etc.):
1. Subclass VenueAdapter and implement the async methods below.
2. Register the adapter instance in the venue list.
3. The UI automatically renders its venue card and all pairwise net-spread opportunities.
"""
from abc import ABC, abstractmethod
from itertools import combinations


class VenueAdapter(ABC):
    venue_id: str = ""
    label: str = ""
    taker_fee_pct: float = 0.03

    @abstractmethod
    async def get_book(self, symbol: str):
        """Return dict(bid=float, ask=float, ts=int_ms) or None."""

    @abstractmethod
    async def get_balance(self):
        """Return dict(equity=float, avail=float, hold=float, ts=int_ms)."""

    @abstractmethod
    async def get_position(self, symbol: str):
        """Return dict(qty=float, entryPx=float|None, liq=float|None)."""

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int, cross: bool = True) -> bool:
        """Apply leverage on the venue. Return True on official confirmation."""

    @abstractmethod
    async def place_ioc(self, symbol: str, side: int, qty: float, limit_px: float, reduce_only: bool = False):
        """Submit an IOC order (side: +1 buy, -1 sell) and return official order/fill evidence."""

    @abstractmethod
    async def close_all(self, symbol: str) -> bool:
        """Reduce-only close all open exposure on this venue for symbol."""


def _fnum(v):
    try:
        x = float(v)
        return x if x == x and abs(x) != float("inf") else None
    except Exception:
        return None


def build_multi_venue_view(raw_venues, open_bp=40.0):
    """Build normalized venues[] and pairwise cross-venue spread opportunities pairs[]."""
    venues = []
    for v in raw_venues or []:
        bid = _fnum(v.get("bid"))
        ask = _fnum(v.get("ask"))
        mid = (bid + ask) / 2.0 if (bid and ask and bid > 0 and ask > 0) else None
        venues.append({
            "id": str(v.get("id", "")),
            "label": str(v.get("label") or v.get("id", "")),
            "bid": bid,
            "ask": ask,
            "mid": round(mid, 4) if mid is not None else None,
            "equity": _fnum(v.get("equity")),
            "avail": _fnum(v.get("avail")),
            "pos": _fnum(v.get("pos")),
            "liq": _fnum(v.get("liq")),
            "feePct": _fnum(v.get("feePct")) or 0.0,
            "keyOk": v.get("keyOk"),
            "online": bool(v.get("online")) and mid is not None,
        })

    pairs = []
    for a, b in combinations(venues, 2):
        rt_fee_bp = round((a["feePct"] + b["feePct"]) * 100.0 * 2.0, 2)
        if not (a["bid"] and a["ask"] and b["bid"] and b["ask"]):
            pairs.append({
                "pair": f"{a['label']} × {b['label']}",
                "a": a["id"], "b": b["id"],
                "bestDir": "数据等待", "execBp": None, "feeBp": rt_fee_bp,
                "netBp": None, "ready": False,
            })
            continue
        bp_buy_a_sell_b = (b["bid"] / a["ask"] - 1.0) * 10000.0
        bp_sell_a_buy_b = (a["bid"] / b["ask"] - 1.0) * 10000.0
        if bp_buy_a_sell_b >= bp_sell_a_buy_b:
            best_dir = f"买 {a['label']} / 卖 {b['label']}"
            exec_bp = bp_buy_a_sell_b
        else:
            best_dir = f"卖 {a['label']} / 买 {b['label']}"
            exec_bp = bp_sell_a_buy_b
        net_bp = exec_bp - rt_fee_bp
        pairs.append({
            "pair": f"{a['label']} × {b['label']}",
            "a": a["id"], "b": b["id"],
            "bestDir": best_dir,
            "execBp": round(exec_bp, 2),
            "feeBp": rt_fee_bp,
            "netBp": round(net_bp, 2),
            "ready": bool(exec_bp >= float(open_bp or 0)),
        })

    pairs.sort(key=lambda x: x["netBp"] if x["netBp"] is not None else -1e9, reverse=True)
    return {"venues": venues, "pairs": pairs}
