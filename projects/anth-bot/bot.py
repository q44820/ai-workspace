#!/usr/bin/env python3
"""Archived ANTH spread engine: offline simulation only, no live startup."""
if __name__ == "__main__":
    raise SystemExit("公开归档副本禁止启动服务或实盘；只运行离线测试。")
import asyncio, json, os, sys, time, math, statistics, logging, secrets, traceback, re, collections
from pathlib import Path
import aiohttp
from aiohttp import web

HERE = Path(__file__).resolve().parent
HL_INFO = "https://api.hyperliquid.xyz/info"
HL_WS = "wss://api.hyperliquid.xyz/ws"
COIN = "io:ANTH"
HL_SZ_DEC, LT_SZ_DEC, LT_PX_DEC = 3, 5, 1   # Lighter mainnet 193: size_decimals=5, price_decimals=1, min 0.0025 / 10U

# Never load .env; ignore all account/credential environment fields.
_ENV = os.environ.get
PRIVATE_FIELDS = frozenset({'HL_ACCOUNT_ADDRESS', 'HL_API_PRIVATE_KEY', 'LIGHTER_ACCOUNT_INDEX', 'LIGHTER_API_KEY_INDEX', 'LIGHTER_API_PRIVATE_KEY', 'CONTROL_TOKEN', 'STATUS_PUSH_TOKEN'})
def ENV(name, default=None):
    return default if name in PRIVATE_FIELDS else _ENV(name, default)
RUNTIME = Path(ENV("ANTH_RUNTIME", str(HERE / "runtime")))
STATE_F = Path(ENV("STATE_FILE", str(RUNTIME / "state.json")))

LT_URL = ENV("LIGHTER_URL", "https://mainnet.zklighter.elliot.ai").rstrip("/")
LT_API = LT_URL + "/api/v1"
LT_WS = LT_URL.replace("https://", "wss://") + "/stream?readonly=true"
MID = int(ENV("LIGHTER_MARKET_ID", "193"))
LEVERAGE = 1  # Generic offline fixture, not an account configuration.
NOTIONAL = 100.0  # Generic synthetic notional for public simulation only.
CONTROL_TOKEN = ENV("CONTROL_TOKEN", "")
BIND = ENV("BIND_HOST", "127.0.0.1"); PORT = int(ENV("PORT", "8787"))
LIVE_ENABLED = ENV("ANTH_OFFLINE_SIM", "0") == "1"  # Offline harness only; never enables real execution.
DRY_RUN = True  # Public archive: no real orders, regardless of environment.            # 实盘逻辑走一遍，但不发单（模拟按限价成交）
PUBLIC_PATH = ""  # No production status export.            # 只读状态页的随机路径，不写日志
PUBLIC_BIND = ENV("PUBLIC_BIND", "0.0.0.0")
PUBLIC_PORT = int(ENV("PUBLIC_PORT", "8788"))
STATUS_PUSH_URL = ""  # External publishing disabled.    # Production endpoint intentionally omitted.
STATUS_PUSH_TOKEN = ""

RUNTIME.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(ENV("LOG_FILE", str(RUNTIME / "bot.log")))])
log = logging.getLogger("anth")

DEF_CFG = dict(margin=NOTIONAL, lev=1, maxNotional=NOTIONAL, openBp=25, medMin=120, maxHoldMin=240, stopBp=100,
               feeIo=0.009, feeLt=0.028, slipCapBp=30, retries=2, cooldownSec=60,
               confirmSec=0, minProfitBp=3, minHoldSec=30)
# Historical fee model retained for offline tests only; no current account fee verification.
TICK_SEC = float(ENV("TICK_SEC", "0.25"))      # 引擎判断频率
DRY_SLIP = dict(io=0.0, lt=0.0)                 # DRY_RUN 模拟成交的不利滑点 bp（测试用）
DRY_CASH = dict(io=float(ENV("DRY_CASH_IO", "100")), lt=float(ENV("DRY_CASH_LT", "100")))   # DRY_RUN 模拟余额

def now_ms(): return int(time.time() * 1000)
def bj(ms): return time.strftime("%m-%d %H:%M:%S", time.gmtime(ms / 1000 + 8 * 3600))

def official_evidence(evidence):
    return isinstance(evidence, dict) and evidence.get("source") in ("official-submission", "official-order")

def order_outcome_unknown(intent):
    """True only when a submission may exist and no official terminal evidence does.

    terminal_wait with official evidence means the order outcome is known and the
    later position snapshot does not match that order's old pre-trade expectation.
    That is not an in-flight unknown submission.
    """
    if not isinstance(intent, dict):
        return True
    if intent.get("status") in ("rejected", "terminal_dry", "terminal"):
        return False
    if intent.get("status") == "terminal_wait" and official_evidence(intent.get("terminal_evidence")):
        return False
    return True

LOSS_LIMIT_DEFAULT = 5.0
LOSS_LIMIT_MAX = 100000.0
EQUITY_SKEW_MS = 2000
EQUITY_MAX_AGE_MS = 15000

def finite_num(value):
    if isinstance(value, bool) or isinstance(value, (list, dict)) or value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value):
        return None
    return value

def default_loss_state():
    return dict(enabled=True, limitUsd=LOSS_LIMIT_DEFAULT, baseline=None, triggered=False,
                triggeredAt=None, triggeredLoss=None, resetAt=None, ready=False, anomaly="亏损基线未初始化",
                netLoss=None, last=None)

def normalize_loss_state(raw):
    """Reload a persisted loss latch without treating a restart as a new baseline."""
    state = default_loss_state()
    if not isinstance(raw, dict):
        return state
    limit = finite_num(raw.get("limitUsd"))
    if limit is not None and 0 < limit <= LOSS_LIMIT_MAX:
        state["limitUsd"] = limit
    base = raw.get("baseline")
    if (isinstance(base, dict) and all(finite_num(base.get(k)) is not None for k in ("io", "lt", "total"))
            and isinstance(base.get("t"), (int, float)) and math.isfinite(base["t"])):
        state["baseline"] = dict(io=float(base["io"]), lt=float(base["lt"]), total=float(base["total"]), t=int(base["t"]))
        state["anomaly"] = "权益尚未刷新"
    state["triggered"] = raw.get("triggered") is True
    if state["triggered"]:
        state["triggeredAt"] = int(raw["triggeredAt"]) if isinstance(raw.get("triggeredAt"), (int, float)) and math.isfinite(raw["triggeredAt"]) else None
        state["triggeredLoss"] = finite_num(raw.get("triggeredLoss"))
    if isinstance(raw.get("resetAt"), (int, float)) and math.isfinite(raw["resetAt"]):
        state["resetAt"] = int(raw["resetAt"])
    state["ready"] = False
    return state

# ---------------- 状态 ----------------
class State:
    def __init__(self):
        d = json.loads(STATE_F.read_text()) if STATE_F.exists() else {}
        self.mode = d.get("mode", "paper")
        if self.mode == "live" and not LIVE_ENABLED: self.mode = "paper"
        self.cfg = {**DEF_CFG, **d.get("cfg", {})}
        self.pos = d.get("pos"); self.trades = d.get("trades", []); self.olog = d.get("olog", [])
        self.buckets = {int(k): v for k, v in d.get("buckets", {}).items()}
        self.alert = d.get("alert"); self.cool = 0; self.busy = False
        self.exit_pending = d.get("exit_pending")
        self.intents = d.get("intents", [])
        if self.exit_pending and self.exit_pending.get("submitted") and not self.intents:
            self.intents.append(dict(id=secrets.token_hex(16), venue="legacy", direction="unknown", qty=None, purpose="legacy pending", status="unknown"))
        self.last_actual = d.get("last_actual")
        self.actual = None  # restart invalidates current knowledge, retains last observation
        self.recovery = bool(d.get("recovery") or (not self.pos and (self.exit_pending or
                             any(order_outcome_unknown(i) for i in self.intents))))
        self.recovery_reduce_allowed = d.get("recovery_reduce_allowed") is True
        self.loss = normalize_loss_state(d.get("loss"))
        if self.recovery: self.mode = "stopped"
    def save(self):
        lim = now_ms() - 8 * 3600e3
        self.buckets = {k: v for k, v in self.buckets.items() if k > lim}
        tmp = STATE_F.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(mode=self.mode, cfg=self.cfg, pos=self.pos, trades=self.trades[-500:],
                                       olog=self.olog[-500:], buckets=self.buckets, alert=self.alert,
                                       exit_pending=self.exit_pending, intents=self.intents, recovery=self.recovery,
                                       recovery_reduce_allowed=self.recovery_reduce_allowed,
                                       last_actual=self.last_actual, loss=self.loss), allow_nan=False))
        with tmp.open("rb") as f: os.fsync(f.fileno())
        tmp.replace(STATE_F)
        fd = os.open(str(STATE_F.parent), os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    def note(self, kind, msg, **kw):
        e = dict(t=now_ms(), kind=kind, msg=msg, **kw); self.olog.append(e); log.info("[%s] %s %s", kind, msg, kw or "")
S = State()

# ---------------- 行情 ----------------
def px_key(p):
    """Canonical Lighter book key so '100' and '100.0' map to the same level."""
    return format(float(p), ".10g")

class Books:
    def __init__(self):
        self.io = dict(bids=[], asks=[], t=0); self.lt_b = {}; self.lt_a = {}; self.lt_t = 0
    def lt(self):
        return dict(bids=sorted(((float(p), s) for p, s in self.lt_b.items()), key=lambda x: -x[0]),
                    asks=sorted(((float(p), s) for p, s in self.lt_a.items()), key=lambda x: x[0]), t=self.lt_t)
B = Books()

async def ws_io():
    while True:
        try:
            async with aiohttp.ClientSession() as s, s.ws_connect(HL_WS, heartbeat=30) as ws:
                await ws.send_json({"method": "subscribe", "subscription": {"type": "l2Book", "coin": COIN}})
                async for m in ws:
                    d = json.loads(m.data)
                    if d.get("channel") == "l2Book":
                        b, a = d["data"]["levels"]
                        B.io = dict(bids=[(float(x["px"]), float(x["sz"])) for x in b],
                                    asks=[(float(x["px"]), float(x["sz"])) for x in a], t=now_ms())
        except Exception as e:
            log.warning("HL ws: %s", e)
        await asyncio.sleep(3)

async def ws_lt():
    while True:
        try:
            async with aiohttp.ClientSession() as s, s.ws_connect(LT_WS, heartbeat=30) as ws:
                await ws.send_json({"type": "subscribe", "channel": f"order_book/{MID}"})
                B.lt_b, B.lt_a = {}, {}
                async for m in ws:
                    d = json.loads(m.data)
                    if d.get("type") == "ping": await ws.send_json({"type": "pong"}); continue
                    ob = d.get("order_book")
                    if not ob: continue
                    if d.get("type", "").startswith("subscribed"): B.lt_b, B.lt_a = {}, {}
                    for x in ob.get("bids", []):
                        k = px_key(x["price"]); sz = float(x["size"])
                        (B.lt_b.__setitem__(k, sz) if sz > 0 else B.lt_b.pop(k, None))
                    for x in ob.get("asks", []):
                        k = px_key(x["price"]); sz = float(x["size"])
                        (B.lt_a.__setitem__(k, sz) if sz > 0 else B.lt_a.pop(k, None))
                    B.lt_t = now_ms()
                    if B.lt_b and B.lt_a and max(map(float, B.lt_b)) >= min(map(float, B.lt_a)):
                        log.warning("Lighter 盘口交叉，重连"); break
        except Exception as e:
            log.warning("Lighter ws: %s", e)
        await asyncio.sleep(2)

FUND = dict(io=None, lt=None)
async def poll_funding():
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(HL_INFO, json={"type": "metaAndAssetCtxs", "dex": "io"}) as r:
                    d = await r.json(); i = [u["name"] for u in d[0]["universe"]].index(COIN); FUND["io"] = float(d[1][i]["funding"])
                async with s.get(LT_API + "/funding-rates") as r:
                    d = await r.json()
                    for f in d.get("funding_rates", []):
                        if f["market_id"] == MID and f["exchange"] == "lighter": FUND["lt"] = float(f["rate"])
        except Exception as e:
            log.warning("funding: %s", e)
        await asyncio.sleep(60)

ACCOUNT_READ_TIMEOUT = 4.0  # Finite transport timeout; requests is not an exact wall-clock deadline.
BAL = {}
async def hl_post(s, body):
    async with s.post(HL_INFO, json=body) as r: return await r.json()

def lighter_usdc_asset_id():
    """USDC asset id published by the installed SignerClient. Missing constant fails closed."""
    try:
        import lighter
        value = getattr(lighter.SignerClient, "ASSET_ID_USDC", None)
        if type(value) is int and value > 0:
            return value
    except Exception:
        return None
    return None

LIGHTER_PAGE_CAP = 8
EXPLORER_LOG_LIMIT = 100
EXPLORER_LOGS = "https://explorer.elliot.ai/api/accounts/{account}/logs"

def _history_page(payload, key):
    if hasattr(payload, "to_dict"):
        payload = payload.to_dict()
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        return None
    code = payload.get("code")
    if code is not None and code != 200:
        return None
    rows = []
    for row in payload[key]:
        if hasattr(row, "to_dict"):
            row = row.to_dict()
        if not isinstance(row, dict):
            return None
        rows.append(row)
    cursor = payload.get("cursor")
    if cursor is None:
        cursor = ""
    if not isinstance(cursor, str):
        return None
    return rows, cursor

def assemble_cursor_pages(pages, key):
    """Fold cursor pages. Complete only when a page returns cursor "".

    The Lighter OpenAPI requires cursor and says to pass it back unchanged.
    It does not define an empty withdraws list plus a repeated cursor as the end
    of an empty ledger. Deposit and transfer reads have been observed to end with
    cursor "". A repeated non-empty cursor stops the walk and stays unverified.
    """
    if not isinstance(pages, list) or not pages:
        return dict(rows=None, complete=False, reason="no-pages", pages_used=0, empty_pages=False)
    seen, rows, ids = [], [], {}
    empty_pages = True
    for index, payload in enumerate(pages):
        if index >= LIGHTER_PAGE_CAP:
            return dict(rows=None, complete=False, reason="page-cap", pages_used=LIGHTER_PAGE_CAP, empty_pages=False)
        parsed = _history_page(payload, key)
        if parsed is None:
            return dict(rows=None, complete=False, reason="malformed", pages_used=index + 1, empty_pages=False)
        chunk, cursor = parsed
        if chunk:
            empty_pages = False
        for row in chunk:
            row_id = row.get("id")
            if row_id is None:
                rows.append(row)
                continue
            previous = ids.get(row_id)
            if previous is None:
                ids[row_id] = row
                rows.append(row)
            elif previous != row:
                return dict(rows=None, complete=False, reason="id-conflict", pages_used=index + 1, empty_pages=False)
        if cursor == "":
            return dict(rows=rows, complete=True, reason="empty-cursor", pages_used=index + 1, empty_pages=empty_pages)
        if cursor in seen:
            return dict(rows=None, complete=False, reason="cursor-cycle", pages_used=index + 1, empty_pages=empty_pages)
        seen.append(cursor)
    return dict(rows=None, complete=False, reason="page-cap", pages_used=len(pages), empty_pages=False)

READ_TOKEN_TTL = 10 * 60
_READONLY = {"client": None, "lock": None}
_HISTORY_METHODS = ("deposit_history", "withdraw_history", "transfer_history")

class ReadonlyHistory:
    """TransactionApi plus a short-lived read token. It has no order or leverage methods."""
    def __init__(self, api_client, tx_api, token, deadline):
        self.api_client = api_client
        self.tx_api = tx_api
        self.token = token
        self.deadline = deadline
    def valid(self):
        return isinstance(self.token, str) and bool(self.token) and time.time() < self.deadline - 30
    async def close(self):
        client = self.api_client
        self.api_client = None
        self.tx_api = None
        self.token = None
        if client is not None:
            await client.close()

async def open_readonly_lighter():
    """Open a read-only Lighter history client.

    The long-lived object is ApiClient plus TransactionApi. A signer is constructed
    only when no read-only token is configured, and only long enough to mint the
    documented 10-minute auth token. It is closed before this function returns.
    update_leverage, send, and cancel are not called. V.lt is not assigned.
    """
    signer = None
    api_client = None
    try:
        import lighter
        account = int(ENV("LIGHTER_ACCOUNT_INDEX", "0") or 0)
        configuration = lighter.Configuration(host=LT_URL)
        api_client = lighter.ApiClient(configuration)
        tx_api = lighter.TransactionApi(api_client)
        stored = ENV("LIGHTER_READ_ONLY_TOKEN", "")
        if isinstance(stored, str) and stored.startswith("ro:"):
            return ReadonlyHistory(api_client, tx_api, stored, time.time() + READ_TOKEN_TTL)
        key_index, private = ENV("LIGHTER_API_KEY_INDEX", ""), ENV("LIGHTER_API_PRIVATE_KEY", "")
        if not key_index or not private:
            await api_client.close()
            api_client = None
            return None
        signer = lighter.SignerClient(url=LT_URL, account_index=account, api_private_keys={int(key_index): private})
        auth, err = signer.create_auth_token_with_expiry(READ_TOKEN_TTL)
        if err or not isinstance(auth, str) or not auth:
            await api_client.close()
            api_client = None
            return None
        return ReadonlyHistory(api_client, tx_api, auth, time.time() + READ_TOKEN_TTL)
    except Exception:
        if api_client is not None:
            try: await api_client.close()
            except Exception: pass
        return None
    finally:
        if signer is not None:
            try: await signer.close()
            except Exception: pass

async def readonly_lighter():
    """Reuse the read client. A stopped process can call this without V.init."""
    if _READONLY["lock"] is None:
        _READONLY["lock"] = asyncio.Lock()
    async with _READONLY["lock"]:
        client = _READONLY["client"]
        if client is not None and client.valid():
            return client
        if client is not None:
            try: await client.close()
            except Exception: pass
        client = await open_readonly_lighter()
        _READONLY["client"] = client
        return client

async def _lighter_history(method, key, **kwargs):
    if method not in _HISTORY_METHODS:
        return None
    reader = await readonly_lighter()
    tx_api = getattr(reader, "tx_api", None)
    if reader is None or tx_api is None or not hasattr(tx_api, method) or not reader.token:
        return None
    pages, seen, cursor = [], [], None
    for _ in range(LIGHTER_PAGE_CAP):
        call = dict(kwargs)
        if cursor:
            call["cursor"] = cursor
        call["authorization"] = reader.token
        try:
            payload = await asyncio.wait_for(getattr(tx_api, method)(**call), ACCOUNT_READ_TIMEOUT)
        except Exception:
            return None
        parsed = _history_page(payload, key)
        if parsed is None:
            return dict(rows=None, complete=False, reason="malformed", pages_used=len(pages) + 1, empty_pages=False)
        _chunk, cursor = parsed
        pages.append(payload if isinstance(payload, dict) else {"code": 200, key: _chunk, "cursor": cursor})
        if cursor == "" or (cursor and cursor in seen):
            break
        if cursor:
            seen.append(cursor)
    return assemble_cursor_pages(pages, key)

def ledger_time_ms(value):
    number = finite_num(value)
    if number is None or number < 1_000_000_000:
        return None
    return number * 1000 if number < 10_000_000_000 else number

def assemble_explorer_withdrawals(pages, account_index, usdc_asset_id, since_ms):
    """Second source: explorer account logs for Withdraw and WithdrawV2.

    A short page (fewer than the requested limit) is the documented offset end.
    A full page with no shorter successor is truncated and stays unverified.
    This does not turn a withdraw_history cursor cycle into zero by itself.
    """
    if not isinstance(pages, list) or not pages:
        return dict(rows=None, complete=False, reason="no-pages")
    account_index = int(account_index)
    usdc_asset_id = int(usdc_asset_id)
    rows, seen = [], set()
    for index, page in enumerate(pages):
        if index >= LIGHTER_PAGE_CAP or not isinstance(page, list):
            return dict(rows=None, complete=False, reason="malformed")
        for log_row in page:
            if not isinstance(log_row, dict):
                return dict(rows=None, complete=False, reason="malformed")
            kind = log_row.get("pubdata_type")
            if kind not in ("Withdraw", "WithdrawV2"):
                return dict(rows=None, complete=False, reason="unexpected-log")
            when = ledger_time_ms(log_row.get("time"))
            if when is None:
                return dict(rows=None, complete=False, reason="bad-time")
            if when < since_ms:
                continue
            if log_row.get("status") != "executed":
                return dict(rows=None, complete=False, reason="unsettled-withdraw")
            pub = log_row.get("pubdata") if isinstance(log_row.get("pubdata"), dict) else {}
            if kind == "Withdraw":
                body = pub.get("withdraw_pubdata") if isinstance(pub.get("withdraw_pubdata"), dict) else None
                amount = finite_num(body.get("usdc_amount")) if body else None
                asset = usdc_asset_id
                owner = body.get("from_account_index") if body else None
            else:
                body = pub.get("withdraw_pubdata_v2") if isinstance(pub.get("withdraw_pubdata_v2"), dict) else None
                amount = finite_num(body.get("amount")) if body else None
                asset = finite_num(body.get("asset_index")) if body else None
                owner = body.get("from_account_index") if body else None
            if amount is None or amount < 0 or asset is None or int(asset) != usdc_asset_id:
                return dict(rows=None, complete=False, reason="bad-amount")
            if owner is not None and str(owner) != str(account_index):
                return dict(rows=None, complete=False, reason="account-mismatch")
            row_id = log_row.get("hash") or (kind, when, amount)
            if row_id in seen:
                continue
            seen.add(row_id)
            rows.append(dict(id=str(row_id), asset_id=usdc_asset_id, timestamp=when, status="completed", amount=str(amount)))
        if len(page) < EXPLORER_LOG_LIMIT:
            return dict(rows=rows, complete=True, reason="short-page")
    return dict(rows=None, complete=False, reason="page-cap")

def verified_lighter_withdrawals(primary, explorer):
    """Use withdraw_history when its cursor ends. A sticky empty cursor needs the explorer log."""
    if not isinstance(primary, dict):
        return None
    if primary.get("complete") is True and isinstance(primary.get("rows"), list):
        return primary["rows"]
    if (primary.get("reason") == "cursor-cycle" and primary.get("empty_pages") is True
            and isinstance(explorer, dict) and explorer.get("complete") is True and isinstance(explorer.get("rows"), list)):
        return explorer["rows"]
    return None

async def _explorer_withdraw_pages(session, account_index):
    pages = []
    for page_index in range(LIGHTER_PAGE_CAP):
        params = [("pub_data_type", "Withdraw"), ("pub_data_type", "WithdrawV2"),
                  ("limit", str(EXPLORER_LOG_LIMIT)), ("offset", str(page_index * EXPLORER_LOG_LIMIT))]
        async with session.get(EXPLORER_LOGS.format(account=account_index), params=params) as response:
            if response.status != 200:
                return None
            payload = await response.json(content_type=None)
        pages.append(payload)
        if isinstance(payload, list) and len(payload) < EXPLORER_LOG_LIMIT:
            return pages
        if not isinstance(payload, list):
            return None
    return pages

async def external_inflow(session, since_ms, l1_address):
    """Signed external cash since the baseline. None means the ledger could not be verified."""
    try:
        addr = ENV("HL_ACCOUNT_ADDRESS", "")
        if not (isinstance(addr, str) and addr.startswith("0x") and len(addr) == 42):
            return None
        hl = await hl_post(session, {"type": "userNonFundingLedgerUpdates", "user": addr, "startTime": int(since_ms)})
        hl_flow = classify_hl_cashflow(hl if isinstance(hl, list) else None, addr, since_ms)
        asset = lighter_usdc_asset_id()
        if hl_flow is None or asset is None or not isinstance(l1_address, str) or not l1_address:
            return None
        account = int(ENV("LIGHTER_ACCOUNT_INDEX", "0") or 0)
        deposits = await _lighter_history("deposit_history", "deposits", account_index=account, l1_address=l1_address)
        transfers = await _lighter_history("transfer_history", "transfers", account_index=account)
        withdraws = await _lighter_history("withdraw_history", "withdraws", account_index=account)
        if not deposits or not deposits.get("complete") or not transfers or not transfers.get("complete"):
            return None
        explorer = None
        if isinstance(withdraws, dict) and withdraws.get("reason") == "cursor-cycle" and withdraws.get("empty_pages") is True:
            log.warning("Lighter withdraw_history cursor cycle with empty pages; withdrawal coverage unverified until explorer logs end")
            explorer = assemble_explorer_withdrawals(await _explorer_withdraw_pages(session, account), account, asset, since_ms)
        withdraw_rows = verified_lighter_withdrawals(withdraws, explorer)
        lt_flow = classify_lt_cashflow(deposits.get("rows"), withdraw_rows, transfers.get("rows"), account, asset, since_ms)
        if withdraw_rows is None or lt_flow is None:
            return None
        return hl_flow + lt_flow
    except Exception:
        return None

async def note_official_equity(session):
    """Arm or refresh the loss baseline from the balances just read. Fail closed on a bad pair or ledger."""
    io, lt = BAL.get("io") or {}, BAL.get("lt") or {}
    io_eq = hl_equity_value(io.get("mode"), io.get("mainValue"), io.get("ioValue"), io.get("usdc"))
    since = (S.loss.get("baseline") or {}).get("t")
    inflow = 0.0 if not since else await external_inflow(session, since, lt.get("l1"))
    observe_loss_sample(io_eq, lt.get("equity"), io.get("t") or 0, lt.get("t") or 0, inflow)

async def poll_balances():
    """只读：两边余额 + 密钥是否匹配。统一账户的 USDC 在 spot 里，io dex 显示 0 是正常的。"""
    keychk = False
    while True:
        try:
            addr = ENV("HL_ACCOUNT_ADDRESS", ""); li = ENV("LIGHTER_ACCOUNT_INDEX", "")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=ACCOUNT_READ_TIMEOUT)) as s:
                if addr.startswith("0x") and len(addr) == 42:
                    sp = await hl_post(s, {"type": "spotClearinghouseState", "user": addr})
                    io = await hl_post(s, {"type": "clearinghouseState", "user": addr, "dex": "io"})
                    ab = await hl_post(s, {"type": "userAbstraction", "user": addr})
                    usdc = next((b for b in sp.get("balances", []) if b["coin"] == "USDC"), {"total": "0", "hold": "0"})
                    ap = next((a["position"] for a in io.get("assetPositions", []) if a["position"]["coin"] == COIN), {})
                    pos = float(ap.get("szi", 0) or 0); liqIo = float(ap["liquidationPx"]) if ap.get("liquidationPx") else None
                    main_value = None
                    try:
                        main = await hl_post(s, {"type": "clearinghouseState", "user": addr})
                        main_value = main["marginSummary"]["accountValue"]
                    except Exception:
                        main_value = None
                    BAL["io"] = dict(usdc=float(usdc["total"]), hold=float(usdc["hold"]), ioValue=float(io["marginSummary"]["accountValue"]),
                                     mainValue=None if finite_num(main_value) is None else float(main_value),
                                     mode=ab if isinstance(ab, str) else None, pos=pos, liq=liqIo, t=now_ms())
                    if not keychk and ENV("HL_API_PRIVATE_KEY"):
                        from eth_account import Account
                        ag = Account.from_key(ENV("HL_API_PRIVATE_KEY")).address.lower()
                        ex = await hl_post(s, {"type": "extraAgents", "user": addr})
                        BAL["ioKey"] = "OK" if any(a["address"].lower() == ag and a.get("validUntil", 0) > now_ms() for a in ex) else "API 钱包未授权或已过期"
                if li:
                    async with s.get(f"{LT_API}/account", params=dict(by="index", value=li)) as r: a = (await r.json())["accounts"][0]
                    lp = next((p for p in a.get("positions", []) if p["market_id"] == MID), {})
                    pos = float(lp.get("position", 0) or 0) * int(lp.get("sign", 1) or 1)
                    liqLt = float(lp["liquidation_price"]) if pos and float(lp.get("liquidation_price") or 0) > 0 else None
                    BAL["lt"] = dict(collateral=float(a.get("collateral", 0)), avail=float(a.get("available_balance", 0)),
                                     equity=finite_num(a.get("total_asset_value")), l1=a.get("l1_address") if isinstance(a.get("l1_address"), str) else None,
                                     pos=pos, liq=liqLt, t=now_ms())
            if not keychk and li and ENV("LIGHTER_API_PRIVATE_KEY") and ENV("LIGHTER_API_KEY_INDEX"):
                import lighter
                c = lighter.SignerClient(url=LT_URL, account_index=int(li), api_private_keys={int(ENV("LIGHTER_API_KEY_INDEX")): ENV("LIGHTER_API_PRIVATE_KEY")})
                err = c.check_client()   # 只读：比对私钥推出的公钥与链上登记的公钥
                try: await c.api_client.close()
                except Exception: pass
                BAL["ltKey"] = "OK" if err is None else f"私钥与 key index {ENV('LIGHTER_API_KEY_INDEX')} 登记的公钥不匹配"
                keychk = True
                S.note("sys" if err is None else "err", f"密钥自检：io={BAL.get('ioKey')}  Lighter={BAL['ltKey']}")
            if not DRY_RUN and BAL.get("io") and BAL.get("lt"):
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=ACCOUNT_READ_TIMEOUT)) as equity_session:
                    await note_official_equity(equity_session)
        except Exception as e:
            log.warning("balances: %s", e)
        await asyncio.sleep(10 if S.pos else 30)

MMR_IO, MMR_LT = 1 / 30, 0.12   # io:ANTH 维持保证金≈1/(2×15)；Lighter 193 maintenance_margin_fraction=1200/10000
def risk_entry(p, leg):
    """Only observed entry prices may support price-dependent risk evidence."""
    value = p.get("pxIo" if leg == "io" else "pxLt")
    return value if valid_price(value) else None

def liq_prices(p):
    """强平价：实盘优先用交易所返回值，否则按入场价/杠杆/维持保证金率估算"""
    if not all(valid_price(risk_entry(p, leg)) for leg in ("io", "lt")): return None, None
    si = 1 if p["dir"] == "L" else -1   # L = io 多 / Lighter 空
    eIo = risk_entry(p, "io") * (1 - si * (1 / LEVERAGE - MMR_IO)); eLt = risk_entry(p, "lt") * (1 + si * (1 / LEVERAGE - MMR_LT))
    if p.get("live") and not DRY_RUN:
        eIo = (BAL.get("io") or {}).get("liq") or eIo; eLt = (BAL.get("lt") or {}).get("liq") or eLt
    return eIo, eLt

async def backfill():
    end = now_ms(); start = end - 3 * 3600e3
    async with aiohttp.ClientSession() as s:
        async with s.post(HL_INFO, json={"type": "candleSnapshot", "req": {"coin": COIN, "interval": "1m", "startTime": int(start), "endTime": end}}) as r:
            io = {c["t"]: float(c["c"]) for c in await r.json()}
        async with s.get(f"{LT_API}/candles", params=dict(market_id=MID, resolution="1m", start_timestamp=int(start / 1000), end_timestamp=int(end / 1000), count_back=500)) as r:
            lt = {c["t"]: float(c["c"]) for c in (await r.json()).get("c", [])}
    a = b = None; cur = end // 60000 * 60000; n = 0
    for t in range(int(start) // 60000 * 60000, cur, 60000):
        a = io.get(t, a); b = lt.get(t, b)
        if a and b and t not in S.buckets: S.buckets[t] = (a / b - 1) * 1e4; n += 1
    log.info("回填 %d 个分钟价差点", n)

def vwap(levels, notional):
    if not valid_price(notional): return None, False
    if any(not valid_price(p) or not valid_price(s) for p, s in levels): return None, False
    q = c = 0.0
    for p, s in levels:
        take = min(s * p, notional - c); q += take / p; c += take
        if c >= notional - 1e-9: break
    return (c / q if q else None), c >= notional - 1e-6

def valid_price(p):
    return isinstance(p, (int, float)) and math.isfinite(p) and p > 0

def quantity_vwap(levels, quantity):
    """实际持仓数量全部可成交才返回价格；部分深度不能证明收益达标。"""
    if not valid_price(quantity): return None
    q = c = 0.0
    for price, size in levels:
        if not valid_price(price) or not positions_known(size) or size < 0: return None
        take = min(size, quantity - q)
        q += take; c += take * price
        if q >= quantity - 1e-12:
            result = c / q
            return result if valid_price(result) else None
    return None

def median_now(t):
    lo = t - S.cfg["medMin"] * 60000
    v = [x for k, x in S.buckets.items() if lo < k <= t]
    return (statistics.median(v) if v else None), len(v)

# ---------------- 交易所执行（实盘） ----------------
class Venues:
    def __init__(self):
        self.ready = False; self.err = None; self.hl = None; self.lt = None; self.track = None
        self._send_intent = None; self._lookup_at = {}; self.read_timeout = ACCOUNT_READ_TIMEOUT; self.lookup_timeout = 4.0; self.exposure_timeout = 4.0; self.exposure_interval = 2.0; self._exposure_at = None
        # Order-terminal wait. Independent of cfg confirmSec, which only gates entry signals.
        self.ack_timeout = 6.0; self.ack_interval = 0.25; self.last_confirm_exposure = None
        self.hl_addr = ENV("HL_ACCOUNT_ADDRESS", ""); self.lt_idx = int(ENV("LIGHTER_ACCOUNT_INDEX", "0") or 0)
    async def init(self):
        if DRY_RUN:
            self.ready = True; self.err = None; S.note("sys", "DRY_RUN：不连接交易账户、不发任何订单，按限价模拟成交")
            await self.set_leverage(); return
        raise RuntimeError("PUBLIC_ARCHIVE_LIVE_EXECUTION_DISABLED")
        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils import constants
            import lighter
            need = ["HL_ACCOUNT_ADDRESS", "HL_API_PRIVATE_KEY", "LIGHTER_ACCOUNT_INDEX", "LIGHTER_API_KEY_INDEX", "LIGHTER_API_PRIVATE_KEY"]
            miss = [k for k in need if not ENV(k)]
            if miss: raise RuntimeError("缺少 .env 配置: " + ", ".join(miss))
            self.hl_addr = ENV("HL_ACCOUNT_ADDRESS")
            self.hl = await asyncio.to_thread(Exchange, Account.from_key(ENV("HL_API_PRIVATE_KEY")), constants.MAINNET_API_URL,
                                              account_address=self.hl_addr, perp_dexs=["", "io"], timeout=self.read_timeout)
            self.lt_idx = int(ENV("LIGHTER_ACCOUNT_INDEX")); kidx = int(ENV("LIGHTER_API_KEY_INDEX"))
            self.lt = lighter.SignerClient(url=LT_URL, account_index=self.lt_idx, api_private_keys={kidx: ENV("LIGHTER_API_PRIVATE_KEY")})
            err = self.lt.check_client()
            if err: raise RuntimeError(f"Lighter API key 校验失败（私钥和 key index {kidx} 不匹配）")
            if not await self.set_leverage(): raise RuntimeError("设置杠杆失败，见日志")
            self.ready = True; self.err = None; S.note("sys", "实盘交易所客户端就绪" + ("（DRY_RUN 不发单）" if DRY_RUN else ""))
        except Exception as e:
            self.ready = False; self.err = str(e); S.note("err", "实盘客户端初始化失败: " + str(e))

    async def set_leverage(self):
        """两边都设成 LEVERAGE 倍。io 先试全仓，被拒再用逐仓；Lighter 全仓。失败返回 False（调用方拒绝开仓）。"""
        if DRY_RUN:
            S.note("sys", f"DRY_RUN：将设置杠杆 io:ANTH {LEVERAGE}x 全仓(失败则逐仓)、Lighter {MID} {LEVERAGE}x 全仓 —— 未发送"); return True
        ok = True
        try:
            r = await asyncio.to_thread(self.hl.update_leverage, LEVERAGE, COIN, True)
            if not (isinstance(r, dict) and r.get("status") == "ok"):
                S.note("sys", f"io 全仓 {LEVERAGE}x 被拒，改用逐仓", resp=str(r)[:200])
                r = await asyncio.to_thread(self.hl.update_leverage, LEVERAGE, COIN, False)
            if isinstance(r, dict) and r.get("status") == "ok": S.note("sys", f"io:ANTH 杠杆已设 {LEVERAGE}x")
            else: ok = False; S.note("err", "io 设置杠杆失败", resp=str(r)[:300])
        except Exception as e:
            ok = False; S.note("err", f"io 设置杠杆异常: {e}")
        try:
            _, resp, err = await self.lt.update_leverage(MID, self.lt.CROSS_MARGIN_MODE, LEVERAGE)
            if err or (resp is not None and getattr(resp, "code", 200) != 200): ok = False; S.note("err", "Lighter 设置杠杆失败", error=str(err), resp=str(resp)[:300])
            else: S.note("sys", f"Lighter {MID} 杠杆已设 {LEVERAGE}x 全仓")
        except Exception as e:
            ok = False; S.note("err", f"Lighter 设置杠杆异常: {e}")
        return ok

    async def positions(self):
        """返回 (io 持仓带符号, lighter 持仓带符号, io 入场价, lighter 入场价)"""
        if DRY_RUN:
            p = S.pos or {}; return p.get("dryIo", 0.0), p.get("dryLt", 0.0), p.get("pxIo"), p.get("pxLt")
        st = await asyncio.to_thread(self.hl.info.user_state, self.hl_addr, "io")
        if st.get("status") not in (None, "ok"): raise ValueError("HL account error")
        pio, eio = 0.0, None
        if not isinstance(st.get("assetPositions"), list): raise ValueError("unknown HL positions")
        for ap in st["assetPositions"]:
            if ap["position"]["coin"] == COIN:
                pio = float(ap["position"]["szi"])
                try: eio = float(ap["position"].get("entryPx"))
                except (TypeError, ValueError): eio = None
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.read_timeout)) as s:
            async with s.get(f"{LT_API}/account", params=dict(by="index", value=self.lt_idx)) as r: d = await r.json()
        if d.get("code", 200) != 200: raise ValueError("LT account error")
        plt, elt = 0.0, None
        account = d["accounts"][0]
        if not isinstance(account.get("positions"), list): raise ValueError("unknown LT positions")
        for p in account["positions"]:
            if p["market_id"] == MID:
                size, sign = float(p["position"]), int(p["sign"])
                if not math.isfinite(size) or size < 0 or sign not in (-1, 1): raise ValueError("invalid LT position")
                plt = size * sign
                try: elt = float(p.get("avg_entry_price")) if plt else None
                except (TypeError, ValueError): elt = None
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (pio, plt)): raise ValueError("invalid positions")
        return pio, plt, eio, elt

    # ---- 成交追踪：drive_to 期间记录每笔订单的 oid / tx_hash，事后去交易所查真实成交价和手续费 ----
    def _dry_fill(self, leg, qty_signed, ref_px):
        """DRY_RUN：按盘口价 + 不利滑点模拟成交，同时更新模拟余额"""
        is_buy = qty_signed > 0
        px = ref_px * (1 + DRY_SLIP[leg] / 1e4) if is_buy else ref_px * (1 - DRY_SLIP[leg] / 1e4)
        fee = abs(qty_signed) * px * S.cfg["feeIo" if leg == "io" else "feeLt"] / 100
        DRY_CASH[leg] += -qty_signed * px - fee
        if self.track is not None: self.track[leg + "Dry"].append(dict(q=qty_signed, px=px, fee=fee))
        return px

    async def order_io(self, qty, is_buy, ref_px, slip_bp, reduce):
        px = ref_px * (1 + slip_bp / 1e4) if is_buy else ref_px * (1 - slip_bp / 1e4)
        px = round(float(f"{px:.5g}"), 6 - HL_SZ_DEC); sz = round(abs(qty), HL_SZ_DEC)
        S.note("order", f"io {'买' if is_buy else '卖'} {sz} IOC 限价 {px}" + (" 只减仓" if reduce else ""))
        if DRY_RUN:
            S.pos["dryIo"] = round(S.pos.get("dryIo", 0) + (sz if is_buy else -sz), 6)
            fpx = self._dry_fill("io", sz if is_buy else -sz, ref_px); return dict(dry=True, px=fpx)
        intent = self._require_intent("io", sz, is_buy, reduce)
        from hyperliquid.utils.types import Cloid
        r = await asyncio.wait_for(asyncio.to_thread(self.hl.order, COIN, is_buy, sz, px,
            {"limit": {"tif": "Ioc"}}, reduce, cloid=Cloid.from_str(intent["official_id"])), 8.0)
        S.note("fill", "io 回报", resp=r)
        try:
            for st in r["response"]["data"]["statuses"]:
                f = st.get("filled")
                if f and self.track is not None:
                    self.track["ioOids"].add(int(f["oid"]))
                    self.track["ioResp"].append(dict(q=float(f["totalSz"]) * (1 if is_buy else -1), px=float(f["avgPx"])))
        except Exception:
            pass
        return r

    async def order_lt(self, qty, is_buy, ref_px, slip_bp, reduce):
        px = ref_px * (1 + slip_bp / 1e4) if is_buy else ref_px * (1 - slip_bp / 1e4)
        pint = int(math.floor(px * 10 ** LT_PX_DEC)) if not is_buy else int(math.ceil(px * 10 ** LT_PX_DEC))
        bint = int(round(abs(qty) * 10 ** LT_SZ_DEC))
        S.note("order", f"Lighter {'买' if is_buy else '卖'} {bint / 10 ** LT_SZ_DEC} 市价单 最差价 {pint / 10 ** LT_PX_DEC}" + (" 只减仓" if reduce else ""))
        if DRY_RUN and ENV("DRY_FAIL_LT") == "1":
            S.note("err", "DRY_FAIL_LT：模拟 Lighter 这条腿没成交"); return dict(dry=True)
        if DRY_RUN:
            S.pos["dryLt"] = round(S.pos.get("dryLt", 0) + (abs(qty) if is_buy else -abs(qty)), 6)
            fpx = self._dry_fill("lt", abs(qty) if is_buy else -abs(qty), ref_px); return dict(dry=True, px=fpx)
        coi = self._require_intent("lt", bint / 10 ** LT_SZ_DEC, is_buy, reduce)["official_id"]
        tx, resp, err = await asyncio.wait_for(self.lt.create_market_order(market_index=MID, client_order_index=coi, base_amount=bint,
                                                          avg_execution_price=pint, is_ask=not is_buy, reduce_only=reduce), 8.0)
        # 注意：Lighter 发单回报只有 tx_hash，没有成交价；成交价要事后用 tx_hash 去 /trades 查
        h = getattr(resp, "tx_hash", None) if resp is not None else None
        if self.track is not None:
            if h: self.track["ltHashes"].add(str(h))
            self.track["ltCoi"].add(int(coi))
        outcome = lighter_submission(tx, resp, err)
        S.note("err" if outcome["rejected"] else "fill",
               "Lighter submission rejected" if outcome["rejected"] else "Lighter submission outcome unconfirmed",
               code=outcome.get("code"), accepted=outcome["accepted"], rejected=outcome["rejected"],
               txHashPresent=bool(outcome.get("tx_hash")))
        return outcome

    def _require_intent(self, venue, qty, buy, reduce):
        i = self._send_intent
        if not i or i["venue"] != venue or i["qty"] != qty or i["direction"] != ("buy" if buy else "sell") or i["reduce"] != reduce:
            raise ValueError("Missing durable mapped send intent")
        return i

    async def lookup_order(self, i):
        """One bounded read; no retries, no transaction-to-terminal inference."""
        if i["venue"] == "io":
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.lookup_timeout)) as session:
                async with session.post(HL_INFO, json=dict(type="orderStatus", user=self.hl_addr,
                                                          oid=i["official_id"])) as response:
                    response.raise_for_status()
                    return await response.json()
        # SignerClient 1.1.8 owns this documented OrderApi and existing api_client.
        pair = self.lt.create_auth_token_with_expiry()
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ValueError("Invalid read auth contract")
        auth, error = pair
        if error or not isinstance(auth, str) or not auth:
            raise ValueError("Read auth unavailable")
        result = await self.lt.order_api.account_orders(authorization=auth,
            client_order_indexes=str(i["official_id"]), account_index=self.lt_idx,
            _request_timeout=float(self.lookup_timeout))
        return result.to_dict()

    def _submission_resolved(self, intent):
        if intent.get("status") in ("terminal", "terminal_dry", "rejected"):
            return True
        return official_evidence(intent.get("terminal_evidence"))

    async def confirm_submission(self, intent):
        """Poll the official order read until it is visible or ack_timeout elapses.

        cfg confirmSec is an entry-signal window and is intentionally not consulted.
        sendTx code 200 and a transaction hash are acceptance only, never a fill.
        """
        deadline = time.monotonic() + self.ack_timeout
        while True:
            self._lookup_at.pop(intent.get("id"), None)
            try:
                exposure = await read_exposure(self)
            except Exception:
                exposure = (None, None)
            self.last_confirm_exposure = exposure
            await self.reconcile_orders(exposure)
            if self._submission_resolved(intent):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                intent["ack_timed_out"] = True
                if not official_evidence(intent.get("terminal_evidence")):
                    intent.pop("terminal_evidence", None)
                    if intent.get("status") not in ("terminal", "terminal_dry", "rejected"):
                        intent["status"] = "unknown"
                exposure = self.last_confirm_exposure or (None, None)
                if unresolved_exposure_guard(exposure[0], exposure[1], not intent.get("reduce")):
                    S.note("err", S.alert)
                return
            await asyncio.sleep(min(self.ack_interval, remaining))

    async def reconcile_orders(self, exposure):
        """Map official order evidence without reopening a later-settled historical order.

        The current net position is the expected position only for the latest intent on
        that venue. An older filled order stays done after a later order; it is not
        rewritten to terminal_wait just because the account is now flat. Official
        evidence already stored is not erased. A local terminal flag with no official
        evidence is not trusted when that intent is still the latest one and the
        official read does not confirm it. Lookup misses do not invent fills.
        """
        if DRY_RUN or not positions_known(*exposure[:2]): return
        for i in list(S.intents):
            if i.get("status") == "terminal_dry":
                continue
            previous_status = i.get("status")
            prior = i.get("terminal_evidence") if isinstance(i.get("terminal_evidence"), dict) else None
            historical = later_intent_on_venue(i)
            if previous_status in ("terminal", "rejected") and official_evidence(prior):
                continue
            if historical and previous_status == "terminal_wait" and official_evidence(prior):
                i["status"] = "terminal"
                i["position_note"] = "later-order-supersedes-pre-fill"
                try: S.save()
                except BaseException:
                    i["status"] = previous_status
                    raise
                continue
            if historical and previous_status in ("terminal", "rejected"):
                # Expired history queries must not downgrade or wipe a settled record.
                continue
            venue = i.get("venue")
            if venue not in ("io", "lt") or not i.get("official_id") or "pre" not in i: continue
            account = self.hl_addr if venue == "io" else self.lt_idx
            if i.get("account") != account or i.get("market") != (COIN if venue == "io" else MID): continue
            evidence = prior if official_evidence(prior) else None
            if evidence is None:
                t = time.monotonic()
                if t < self._lookup_at.get(i["id"], 0): continue
                self._lookup_at[i["id"]] = t + 2.0
                try:
                    result = await asyncio.wait_for(self.lookup_order(i), self.lookup_timeout)
                    evidence = order_evidence(i, result)
                except Exception:
                    continue
                if evidence is None:
                    if previous_status in ("terminal", "rejected") and not historical:
                        if settled_flat_close(i, exposure):
                            continue
                        if prior is not None: i["prior_evidence"] = prior
                        i["status"] = "unknown"
                        i.pop("terminal_evidence", None)
                        try: S.save()
                        except BaseException:
                            i["status"] = previous_status
                            if prior is not None: i["terminal_evidence"] = prior
                            raise
                    continue
                i["terminal_evidence"] = evidence
            if evidence.get("oid") is not None: i["oid"] = evidence["oid"]
            if historical:
                i["status"] = "rejected" if evidence.get("state") in ("rejected", "iocCancelRejected") and not evidence.get("filled") else "terminal"
                try: S.save()
                except BaseException:
                    i["status"] = previous_status
                    if prior is None: i.pop("terminal_evidence", None)
                    else: i["terminal_evidence"] = prior
                    raise
                continue
            signed = evidence["filled"] * (1 if i["direction"] == "buy" else -1)
            actual = exposure[0 if venue == "io" else 1]
            expected = i["pre"][venue] + signed
            i["status"] = "terminal" if math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9) else "terminal_wait"
            try: S.save()
            except BaseException:
                i["status"] = previous_status
                if prior is None: i.pop("terminal_evidence", None)
                else: i["terminal_evidence"] = prior
                raise

    def track_start(self):
        self.track = dict(t0=now_ms(), ioOids=set(), ioResp=[], ltHashes=set(), ltCoi=set(), ioDry=[], ltDry=[])
        return self.track

    async def fills(self, tr, q_io=None, q_lt=None):
        """查本次 drive_to 的真实成交。返回 dict(io=leg, lt=leg)，leg = dict(q 带符号, px 均价, fee, pnl(交易所给的已实现盈亏, 不含费), src)。
        q_io / q_lt：期望的带符号成交量（用来判断交易所成交记录是否已经全部出来）。"""
        self.track = None
        def agg(rows, src):
            if any(not positions_known(r.get("q")) or r["q"] == 0 or not valid_price(r.get("px")) or
                   (r.get("fee") is not None and not positions_known(r["fee"])) or
                   (r.get("pnl") is not None and not positions_known(r["pnl"])) for r in rows) or (rows and any(r["q"] * rows[0]["q"] <= 0 for r in rows)):
                return dict(q=None, px=None, fee=None, pnl=None, n=len(rows), src="invalid")
            q = sum(r["q"] for r in rows); notional = sum(r["q"] * r["px"] for r in rows)
            gq = sum(abs(r["q"]) for r in rows); gn = sum(abs(r["q"]) * r["px"] for r in rows)
            px = (gn / gq) if gq else None
            return dict(q=round(q, 8), px=px, fee=(sum(r["fee"] for r in rows) if rows and all(positions_known(r.get("fee")) for r in rows) else None),
                        pnl=(sum(r["pnl"] for r in rows) if rows and all(positions_known(r.get("pnl")) for r in rows) else None),
                        n=len(rows), src=src, notional=notional)
        if DRY_RUN:
            return dict(io=agg(tr["ioDry"], "dry"), lt=agg(tr["ltDry"], "dry"))
        out = {}
        # ---- Hyperliquid：userFillsByTime（px / sz / fee / closedPnl），按 oid 过滤 ----
        rows = []
        want = sum(abs(r["q"]) for r in tr["ioResp"]) if q_io is None else abs(q_io)
        for i in range(6):
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.read_timeout)) as s:
                    f = await hl_post(s, {"type": "userFillsByTime", "user": self.hl_addr, "startTime": tr["t0"] - 5000})
                if any(x.get("side") not in ("B", "A") or not valid_price(float(x["sz"])) for x in f if x.get("coin") == COIN and int(x.get("oid", -1)) in tr["ioOids"]):
                    raise ValueError("invalid HL fill direction/quantity")
                rows = [dict(q=float(x["sz"]) * (1 if x["side"] == "B" else -1), px=float(x["px"]), fee=float(x["fee"]) if x.get("fee") is not None else None,
                             pnl=float(x["closedPnl"]) if x.get("closedPnl") is not None else None) for x in f if x.get("coin") == COIN and int(x.get("oid", -1)) in tr["ioOids"]]
                if sum(abs(r["q"]) for r in rows) >= want - 10 ** -HL_SZ_DEC / 2: break
            except Exception as e:
                rows = []
                log.warning("HL fills: %s", e)
            await asyncio.sleep(0.7)
        if rows and sum(abs(r["q"]) for r in rows) >= want - 10 ** -HL_SZ_DEC / 2: out["io"] = agg(rows, "userFills")
        else:
            rr = [dict(q=r["q"], px=r["px"], fee=None) for r in tr["ioResp"]]
            out["io"] = agg(rr, "下单回报avgPx(手续费估算)")
        # ---- Lighter：/api/v1/trades?account_index=…&market_id=…，按 tx_hash 过滤（发单回报里没有成交价） ----
        me = int(ENV("LIGHTER_ACCOUNT_INDEX", "0")); rows = []
        want = None if q_lt is None else abs(q_lt)
        for i in range(8):
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.read_timeout)) as s:
                    async with s.get(f"{LT_API}/trades", params=dict(account_index=me, market_id=MID, sort_by="timestamp", sort_dir="desc", limit=50)) as r:
                        d = await r.json()
                rows = []
                for x in d.get("trades", []):
                    hit = x.get("tx_hash") in tr["ltHashes"] or (x.get("timestamp", 0) >= tr["t0"] - 2000 and
                          (int(x.get("bid_client_id", -1)) in tr["ltCoi"] and x.get("bid_account_id") == me or
                           int(x.get("ask_client_id", -1)) in tr["ltCoi"] and x.get("ask_account_id") == me))
                    if not hit: continue
                    if (x.get("bid_account_id") == me) == (x.get("ask_account_id") == me) or not isinstance(x.get("is_maker_ask"), bool) or not valid_price(float(x["size"])):
                        raise ValueError("invalid Lighter fill direction/quantity")
                    buy = x.get("bid_account_id") == me
                    taker = (buy and x.get("is_maker_ask")) or ((not buy) and not x.get("is_maker_ask"))
                    rate = x.get("taker_fee" if taker else "maker_fee")
                    usd = float(x["usd_amount"]); pnl = x.get("bid_account_pnl" if buy else "ask_account_pnl")
                    rows.append(dict(q=float(x["size"]) * (1 if buy else -1), px=float(x["price"]), fee=usd * float(rate) / 1e6 if rate is not None else None,
                                     pnl=float(pnl) if pnl is not None else None))
                got = sum(abs(r["q"]) for r in rows)
                if rows and (want is None or got >= want - 10 ** -LT_SZ_DEC / 2): break
            except Exception as e:
                rows = []
                log.warning("Lighter trades: %s", e)
            await asyncio.sleep(0.8)
        if rows: out["lt"] = agg(rows, "trades")
        else: out["lt"] = dict(q=q_lt or 0, px=None, fee=None, pnl=None, n=0, src="未查到")
        return out

    async def balances(self):
        """当前余额（只读）：HL 统一账户 USDC 总额、Lighter 抵押品。DRY_RUN 返回模拟余额。"""
        if DRY_RUN: return dict(io=round(DRY_CASH["io"], 6), lt=round(DRY_CASH["lt"], 6), t=now_ms())
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.read_timeout)) as s:
                sp = await hl_post(s, {"type": "spotClearinghouseState", "user": self.hl_addr})
                async with s.get(f"{LT_API}/account", params=dict(by="index", value=self.lt_idx)) as r: a = (await r.json())["accounts"][0]
            usdc = next((b for b in sp.get("balances", []) if b["coin"] == "USDC"), {"total": "0"})
            return dict(io=float(usdc["total"]), lt=float(a.get("collateral", 0)), t=now_ms())
        except Exception as e:
            log.warning("balances snapshot: %s", e); return None

    async def drive_to(self, t_io, t_lt, reduce, tag, close_reason=None):
        """单次尝试目标持仓；未知订单只允许人工核实，不凭延迟仓位重发。
        任一侧仍需下单却没有可信新鲜盘口时，两腿都不发单（避免单边先平）。不在此函数里发明无报价市价策略。"""
        self.last_drive = dict(submitted=False, ok=False)
        if not S.pos:
            S.recovery = True
            if not reduce:
                S.recovery_reduce_allowed = False
                S.save()
                return False
            S.save()  # represent orphan exposure before any account read or submission
        try:
            pio, plt, _, _ = await read_exposure(self, paced=S.recovery)
        except Exception:
            if not unknown_orders(): raise
            pio = plt = None
        await self.reconcile_orders((pio, plt))
        if unknown_orders():
            S.alert = "Unknown order outcome: manual review required; resend prohibited"
            # This return is before any new submission. Do not label the attempt as sent
            # just because an older intent is unresolved.
            since = (S.pos or {}).get("t") or 0
            sent_this = any(isinstance(i.get("t"), (int, float)) and i["t"] >= since and i.get("submitted") for i in S.intents)
            if sent_this:
                S.exit_pending = dict(submitted=True, why=tag, phase="submitted_unknown", t=(S.exit_pending or {}).get("t") or now_ms())
            else:
                S.exit_pending = dict(submitted=False, why=tag, phase="not_sent", t=now_ms())
            S.save()
            return False
        if not reduce and S.mode == "stopped":
            S.alert = "策略已停止，禁止新开仓"
            S.save()
            return False
        if not reduce and (S.recovery or (S.pos and (
                S.pos.get("opening_failed") or S.pos.get("close_requested") or S.pos.get("rescue_pending")))):
            S.alert = "本轮开仓已中止或处于恢复状态，禁止新开腿"
            S.save()
            return False
        if reduce and not S.pos and not S.recovery_reduce_allowed:
            S.alert = "Monitor-only exposure recovery; reduction requires explicit kill"
            S.save()
            return False
        if not positions_known(pio, plt):
            S.alert = "Unknown account exposure; no orders sent"
            S.save()
            return False
        deltas = (round(t_io-pio, HL_SZ_DEC), round(t_lt-plt, LT_SZ_DEC))
        needed = [(leg, delta, book) for leg, delta, book, dec in
                  (("io", deltas[0], B.io, HL_SZ_DEC), ("lt", deltas[1], B.lt(), LT_SZ_DEC))
                  if abs(delta) >= 10 ** -dec / 2]
        if any(not book_tradable(book, now_ms()) or
               (not reduce and quantity_vwap(book["asks" if delta > 0 else "bids"], abs(delta)) is None)
               for _, delta, book in needed):
            S.alert = "Needed leg has no reliable fresh quote; exposure retained"
            S.save()
            return False
        if not reduce:
            blocked, why = loss_blocks_open()
            if blocked:
                S.alert = why
                self.last_drive["phase"] = "not_sent"
                S.save()
                return False
        # One submission per leg only. Account snapshots cannot establish order finality.
        sent_ids = []
        for leg, delta, book in needed:
            # Never reuse the pre-first-leg book after a venue round trip.
            book = B.io if leg == "io" else B.lt()
            if not book_tradable(book, now_ms()) or (not reduce and
                    quantity_vwap(book["asks" if delta > 0 else "bids"], abs(delta)) is None):
                S.alert = "Needed quote expired or lacks size before order; exposure retained"
                S.save()
                return False
            intent = dict(id=secrets.token_hex(16), venue=leg, direction="buy" if delta > 0 else "sell",
                          qty=abs(delta), purpose=tag, status="unknown", t=now_ms(),
                          official_id=("0x" + secrets.token_hex(16) if leg == "io" else new_client_index()),
                          account=self.hl_addr if leg == "io" else self.lt_idx,
                          market=COIN if leg == "io" else MID, reduce=bool(reduce),
                          pre=dict(io=pio, lt=plt), submitted=True, accepted=False)
            S.intents.append(intent)
            if leg == "io" and not reduce and tag == "开仓" and S.pos:
                S.pos["first_open_intent"] = intent["id"]
            S.exit_pending = dict(submitted=True, why=tag, t=now_ms())
            # Conditional strategy exits become durable only with the first send intent.
            prior_close = S.pos.get("close_requested") if S.pos else None
            if close_reason and S.pos:
                S.pos["close_requested"] = prior_close or close_reason
            try:
                S.save()  # reason and barrier must be durable before side effect
            except BaseException:
                if close_reason and S.pos and not prior_close:
                    S.pos.pop("close_requested", None)
                raise
            self.last_drive["submitted"] = True
            self._send_intent = intent
            try:
                result = await getattr(self, "order_" + leg)(delta, delta > 0,
                    book["asks" if delta > 0 else "bids"][0][0], S.cfg["slipCapBp"], reduce)
            except BaseException:
                S.alert = "Order outcome unknown; manual review required"
                if not reduce and S.pos:
                    S.pos["opening_failed"] = True
                S.save()
                raise
            finally:
                self._send_intent = None
            # Interpret official single-order submission evidence; acknowledgements alone are not terminal.
            rejected = leg == "lt" and isinstance(result, dict) and result.get("rejected") is True
            ioc_miss = False
            if leg == "io" and isinstance(result, dict):
                statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                rejected = isinstance(statuses, list) and len(statuses) == 1 and all(isinstance(x, dict) and set(x) == {"error"} and isinstance(x["error"], str) and bool(x["error"].strip()) for x in statuses)
                ioc_miss = (rejected and result.get("status") == "ok" and
                            result["response"].get("type") == "order" and
                            statuses[0]["error"].strip().partition(" asset=")[0] ==
                            "Order could not immediately match against any resting orders.")
            if DRY_RUN and isinstance(result, dict) and result.get("dry"):
                intent["status"] = "terminal_dry"
            elif rejected:
                intent["terminal_evidence"] = dict(state="iocCancelRejected" if ioc_miss else "rejected", filled=0.0, source="official-submission")
            elif isinstance(result, dict):
                intent["accepted"] = bool(result.get("accepted"))
                if result.get("tx_hash"): intent["tx_hash"] = str(result["tx_hash"])
                if leg == "io":
                    evidence, oid, accepted = submission_evidence(intent, result)
                    intent["accepted"] = accepted
                    if oid is not None: intent["oid"] = oid
                    if evidence: intent["terminal_evidence"] = evidence
            try:
                S.save()  # a failed terminal update must also stay unknown in memory
            except BaseException:
                intent["status"] = "unknown"
                intent.pop("terminal_evidence", None)
                raise
            sent_ids.append(intent["id"])
            if not reduce:
                # Resolve each opening leg independently before another open leg.
                # An acceptance or a missing receipt is not a rejection and is not a fill.
                await self.confirm_submission(intent)
                if not self._submission_resolved(intent):
                    if not (isinstance(S.alert, str) and "不会自动恢复" in S.alert):
                        S.alert = "Order outcome unknown after bounded read-only confirmation; resend prohibited"
                    S.save()
                    return False
                exposure = self.last_confirm_exposure or (None, None)
                pio, plt = exposure[0], exposure[1]
                evidence = intent.get("terminal_evidence", {})
                dec = HL_SZ_DEC if leg == "io" else LT_SZ_DEC
                tol = 10 ** -dec / 2
                before = intent["pre"][leg]
                actual = pio if leg == "io" else plt
                full = (intent["status"].startswith("terminal") and
                        positions_known(pio, plt) and
                        (DRY_RUN or (evidence.get("state") == "filled" and
                         positions_known(evidence.get("filled")) and
                         abs(evidence["filled"] - abs(delta)) <= tol)) and
                        abs(actual - (before + delta)) <= tol)
                if not full:
                    if S.pos: S.pos["opening_failed"] = True
                    S.alert = "开仓腿未足额确认，已中止后续开腿；仅允许核对及风险减仓"
                    if not unknown_orders(): S.exit_pending = None
                    S.save()
                    return False
        if reduce:
            # Known residuals may all be submitted once. Their receipts still have to
            # resolve before success or a protective stop; a 20ms acceptance is not final.
            for pending in S.intents:
                if pending.get("id") in sent_ids and not self._submission_resolved(pending):
                    await self.confirm_submission(pending)
        pio, plt, _, _ = await read_exposure(self)
        await self.reconcile_orders((pio, plt))
        ok = positions_known(pio, plt) and (pio == 0 if t_io == 0 else abs(t_io-pio) < 0.0005) and (plt == 0 if t_lt == 0 else abs(t_lt-plt) < 0.000005) and not unknown_orders()
        self.last_drive["ok"] = ok
        if not unknown_orders(): S.exit_pending = None
        else: S.alert = "Order outcome unknown; manual review required; resend prohibited"
        S.save()
        return ok

V = Venues()

def new_client_index():
    # Retain all journal indices, including filled/canceled: venue protection is NOT permanent.
    used = {i.get("official_id") for i in S.intents if i.get("venue") == "lt"}
    for _ in range(32):
        value = secrets.randbelow((1 << 48) - 1) + 1
        if value not in used: return value
    raise RuntimeError("Cannot allocate unique client order index")

def order_number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)): raise ValueError("invalid quantity")
    value = float(value)
    if not math.isfinite(value) or value < 0: raise ValueError("invalid quantity")
    return value

def same_size(a, b):
    return math.isclose(order_number(a), order_number(b), rel_tol=0, abs_tol=1e-9)

HL_CANCEL_STATES = frozenset(("canceled", "marginCanceled", "vaultWithdrawalCanceled",
    "openInterestCapCanceled", "selfTradeCanceled", "reduceOnlyCanceled", "siblingFilledCanceled",
    "delistedCanceled", "liquidatedCanceled", "scheduledCancel"))
HL_REJECT_STATES = frozenset(("rejected", "tickRejected", "minTradeNtlRejected", "perpMarginRejected",
    "reduceOnlyRejected", "badAloPxRejected", "iocCancelRejected", "badTriggerPxRejected",
    "marketOrderNoLiquidityRejected", "positionIncreaseAtOpenInterestCapRejected",
    "positionFlipAtOpenInterestCapRejected", "tooAggressiveAtOpenInterestCapRejected",
    "openInterestIncreaseRejected", "insufficientSpotBalanceRejected", "oracleRejected", "perpMaxPositionRejected"))
LT_CANCEL_STATES = frozenset(("canceled", "canceled-post-only", "canceled-reduce-only",
    "canceled-position-not-allowed", "canceled-margin-not-allowed", "canceled-too-much-slippage",
    "canceled-not-enough-liquidity", "canceled-self-trade", "canceled-expired", "canceled-oco",
    "canceled-child", "canceled-liquidation", "canceled-invalid-balance"))

def submission_evidence(i, result):
    """Single-order official HL ack; contextual identity is the persisted exact request."""
    try:
        if result.get("status") != "ok": return None, None, False
        rows = result["response"]["data"]["statuses"]
        if len(rows) != 1 or not isinstance(rows[0], dict): return None, None, False
        row = rows[0]
        if set(row) == {"filled"}:
            f = row["filled"]; q = order_number(f["totalSz"])
            oid = f["oid"]
            if type(oid) is not int or oid < 0 or q > i["qty"] or q <= 0 or not valid_price(float(f["avgPx"])):
                return None, None, False
            return dict(state="filled", filled=q, source="official-submission"), oid, True
        if set(row) == {"resting"} and type(row["resting"]["oid"]) is int:
            return None, row["resting"]["oid"], True
    except (KeyError, TypeError, ValueError, OverflowError): pass
    return None, None, False

def order_evidence(i, result):
    """Parse verified public schemas only. Invalid/active/absent is never terminal."""
    try:
        if i["venue"] == "io":
            if result.get("status") != "order": return None
            record = result["order"]; row = record["order"]; state = record["status"]
            if row.get("coin") != i["market"] or row.get("side") != ("B" if i["direction"] == "buy" else "A"): return None
            if type(row.get("oid")) is not int or row["oid"] < 0: return None
            if row.get("cloid") is not None and row["cloid"] != i["official_id"]: return None
            if row.get("cloid") is None and (i.get("oid") is None or row["oid"] != i["oid"]): return None
            if i.get("oid") is not None and row["oid"] != i["oid"]: return None
            if row.get("reduceOnly") is not i["reduce"] or not same_size(row["origSz"], i["qty"]): return None
            remaining = order_number(row["sz"])
            if remaining > i["qty"]: return None
            filled = i["qty"] - remaining
            if state == "filled":
                if remaining != 0: return None
            elif state in HL_REJECT_STATES:
                if filled != 0: return None
            elif state not in HL_CANCEL_STATES: return None
        else:
            if result.get("code") != 200 or not isinstance(result.get("orders"), list): return None
            rows = [r for r in result["orders"] if type(r.get("client_order_index")) is int and r["client_order_index"] == i["official_id"]]
            if len(rows) != 1: return None
            row = rows[0]; state = row["status"]
            if type(row.get("order_index")) is not int or row["order_index"] < 0: return None
            if i.get("oid") is not None and row["order_index"] != i["oid"]: return None
            if type(row.get("owner_account_index")) is not int or row["owner_account_index"] != i["account"]: return None
            if type(row.get("market_index")) is not int or row["market_index"] != i["market"]: return None
            if row.get("is_ask") is not (i["direction"] == "sell") or row.get("reduce_only") is not i["reduce"]: return None
            if not same_size(row["initial_base_amount"], i["qty"]): return None
            filled = order_number(row["filled_base_amount"]); remaining = order_number(row["remaining_base_amount"])
            if not same_size(filled + remaining, i["qty"]): return None
            if state == "filled":
                if remaining != 0: return None
            elif state not in LT_CANCEL_STATES: return None
        return dict(state=state, filled=filled, oid=row["oid"] if i["venue"] == "io" else row["order_index"], source="official-order")
    except (KeyError, TypeError, ValueError, OverflowError): return None


def unresolved_exposure_guard(pio, plt, opening):
    """Stop new opens when an order is still unknown and exposure is unverified.

    One-sided exposure gets an explicit alarm. Nothing here flattens, resends,
    marks a fill, or returns the strategy to live. Permanent unknown stays protected.
    """
    if not positions_known(pio, plt):
        return False
    one_sided = (pio == 0) != (plt == 0)
    if not (one_sided or (opening and (pio != 0 or plt != 0))):
        return False
    S.mode = "stopped"
    S.alert = ("⚠️ 单边敞口订单结果未知，已停止新开仓；持续只读对账，不重复发单，不把未知当成成交，不会自动恢复"
               if one_sided else
               "⚠️ 开仓结果仍未知且账户有敞口，已停止新开仓；持续只读对账，不重复发单，不会自动恢复")
    return True

def lighter_submission(tx, resp, err):
    """Classify a SignerClient create_market_order tuple. Code 200 is accepted, not filled."""
    h = getattr(resp, "tx_hash", None) if resp is not None else None
    code = getattr(resp, "code", None)
    # A transport/API error that never produced a transaction is a rejection.
    # Anything that may already have been accepted stays unconfirmed, not rejected.
    rejected = ((tx is None and resp is None and isinstance(err, str) and bool(err.strip()))
                or (err is None and type(code) is int and code != 200))
    return dict(rejected=rejected, tx_hash=h, accepted=err is None and type(code) is int and code == 200, code=code)

def later_intent_on_venue(intent):
    """True when a newer intent on the same venue supersedes this order's immediate position."""
    if not isinstance(intent, dict):
        return False
    venue = intent.get("venue")
    t = intent.get("t") or 0
    return any(other is not intent and isinstance(other, dict) and other.get("venue") == venue
               and (other.get("t") or 0) > t for other in S.intents)

def settled_flat_close(intent, exposure):
    """A completed close whose venue is already flat, with an earlier same-size terminal.

    An aged-out official read must not turn that settled close back into an unknown
    order. A latest open, a non-flat account, or a close with no matching earlier
    fill is still untrusted when the official read is empty.
    """
    if not isinstance(intent, dict) or not (intent.get("reduce") or intent.get("purpose") == "平仓"):
        return False
    venue = intent.get("venue")
    index = 0 if venue == "io" else 1 if venue == "lt" else None
    if index is None or not positions_known(*exposure[:2]) or not math.isclose(exposure[index], 0, rel_tol=0, abs_tol=1e-9):
        return False
    evidence = intent.get("terminal_evidence") if isinstance(intent.get("terminal_evidence"), dict) else None
    filled = finite_num(evidence.get("filled")) if evidence else None
    if filled is None or filled <= 0:
        return False
    t = intent.get("t") or 0
    return any(other is not intent and isinstance(other, dict) and other.get("venue") == venue
               and other.get("status") in ("terminal", "rejected") and (other.get("t") or 0) < t
               and math.isclose(finite_num((other.get("terminal_evidence") or {}).get("filled")) or -1, filled, rel_tol=0, abs_tol=1e-9)
               for other in S.intents)

def positions_known(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)

def unknown_orders():
    return any(i.get("status") not in ("rejected", "terminal_dry", "terminal") for i in S.intents)

ORDER_ALERTS_CLEARED_WHEN_RESOLVED = (
    "Unknown account/order outcome; manual review required",
    "Unknown order outcome; manual review required",
)

def release_resolved_order_alert():
    """Clear only the alert whose order-unknown cause is gone.

    A stopped flat book with no remaining non-terminal intent can drop that
    exact text. Any other alert, including exposure, loss, or a still-unknown
    order, stays where it is. This does not change mode.
    """
    if S.mode != "stopped" or S.pos or S.exit_pending or S.recovery or unknown_orders():
        return False
    if S.alert not in ORDER_ALERTS_CLEARED_WHEN_RESOLVED:
        return False
    S.alert = None
    S.save()
    return True

def exposure_from_official(io_state, lt_state):
    """Signed positions from the public clearinghouse and account reads. None if the payload is not usable."""
    if not isinstance(io_state, dict) or not isinstance(lt_state, dict) or lt_state.get("code", 200) != 200:
        return None
    if not isinstance(io_state.get("assetPositions"), list):
        return None
    accounts = lt_state.get("accounts")
    if not isinstance(accounts, list) or not accounts or not isinstance(accounts[0], dict):
        return None
    if not isinstance(accounts[0].get("positions"), list):
        return None
    pio = 0.0
    for item in io_state["assetPositions"]:
        position = item.get("position") if isinstance(item, dict) else None
        if isinstance(position, dict) and position.get("coin") == COIN:
            value = finite_num(position.get("szi"))
            if value is None:
                return None
            pio = value
    plt = 0.0
    for item in accounts[0]["positions"]:
        if not isinstance(item, dict) or item.get("market_id") != MID:
            continue
        size, sign = finite_num(item.get("position")), item.get("sign")
        if size is None or size < 0 or sign not in (-1, 1, "-1", "1"):
            return None
        plt = size * int(sign)
    if not positions_known(pio, plt):
        return None
    return pio, plt

async def fetch_official_positions():
    addr = ENV("HL_ACCOUNT_ADDRESS", "")
    account = ENV("LIGHTER_ACCOUNT_INDEX", "")
    if not (isinstance(addr, str) and addr.startswith("0x") and len(addr) == 42 and account):
        return None
    timeout = aiohttp.ClientTimeout(total=ACCOUNT_READ_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        io_state = await hl_post(session, {"type": "clearinghouseState", "user": addr, "dex": "io"})
        async with session.get(f"{LT_API}/account", params=dict(by="index", value=account)) as response:
            lt_state = await response.json(content_type=None)
    return io_state, lt_state

async def reconcile_stopped_flat():
    """Read-only reconcile while stopped with no local position.

    Does not call V.init, set leverage, send, or cancel, and does not change mode.
    A failed read leaves intents and alerts alone.
    """
    if DRY_RUN or S.mode != "stopped" or S.pos or S.busy:
        return
    try:
        fetched = await asyncio.wait_for(fetch_official_positions(), ACCOUNT_READ_TIMEOUT)
    except Exception:
        return
    if not isinstance(fetched, tuple) or len(fetched) != 2:
        return
    exposure = exposure_from_official(*fetched)
    if exposure is None:
        return
    S.actual = dict(io=exposure[0], lt=exposure[1], t=now_ms())
    await V.reconcile_orders(exposure)
    release_resolved_order_alert()

async def monitor_stopped_book():
    while True:
        try:
            await reconcile_stopped_flat()
        except Exception as e:
            log.warning("stopped reconcile: %s", e)
        await asyncio.sleep(10)

def loss_blocks_open():
    """Block a new entry when the combined-equity limit is latched, stale, or not initialized.

    The public DRY_RUN simulator has no official bilateral equity, so it does not
    arm this gate. A production process with DRY_RUN off does.
    """
    if DRY_RUN:
        return False, None
    if latch_loss_trigger():
        return True, "两所合计净亏损已达上限，已停止新开仓；已有持仓仍按止损、回归和到时退出"
    if not S.loss.get("ready") or S.loss.get("anomaly") or not S.loss.get("baseline"):
        return True, S.loss.get("anomaly") or "两所权益基线未就绪，禁止新开仓"
    return False, None

def hl_equity_value(mode, main_value, io_value, spot_usdc):
    """Official equity, never a sum of unrelated balances.

    unifiedAccount / portfolioMargin: Hyperliquid documents the spot clearinghouse
    as the source of truth and says perp dex states are not meaningful.
    disabled / default: perp marginSummary.accountValue on each dex, spot not added.
    Any other mode fails closed.
    """
    if mode in ("unifiedAccount", "portfolioMargin"):
        return finite_num(spot_usdc)
    if mode in ("disabled", "default"):
        main, io = finite_num(main_value), finite_num(io_value)
        if main is None or io is None:
            return None
        return main + io
    return None

def fresh_equity_pair(io_eq, lt_eq, io_t, lt_t, now):
    io_eq, lt_eq = finite_num(io_eq), finite_num(lt_eq)
    if None in (io_eq, lt_eq) or not all(isinstance(t, (int, float)) and math.isfinite(t) for t in (io_t, lt_t, now)):
        return None
    if abs(io_t - lt_t) > EQUITY_SKEW_MS or now - io_t > EQUITY_MAX_AGE_MS or now - lt_t > EQUITY_MAX_AGE_MS:
        return None
    return dict(io=io_eq, lt=lt_eq, total=io_eq + lt_eq, t=int(min(io_t, lt_t)))

def observe_loss_sample(io_eq, lt_eq, io_t, lt_t, inflow, now=None):
    """Record official bilateral equity. inflow is signed external cash since the baseline, or None if unverified."""
    now = now_ms() if now is None else now
    pair = fresh_equity_pair(io_eq, lt_eq, io_t, lt_t, now)
    if pair is None or finite_num(inflow) is None:
        S.loss["anomaly"] = "权益或现金流数据异常，需人工核对基线"
        S.loss["ready"] = False
        S.loss["last"] = None
        return S.loss
    inflow = float(inflow)
    S.loss["anomaly"] = None
    S.loss["last"] = dict(io=pair["io"], lt=pair["lt"], total=pair["total"], t=int(now), inflow=inflow)
    if not S.loss.get("baseline"):
        S.loss["baseline"] = dict(io=pair["io"], lt=pair["lt"], total=pair["total"], t=int(now))
        S.loss["netLoss"] = 0.0
        S.loss["ready"] = True
        S.save()
        return S.loss
    # Loss = baseline equity minus current equity, plus signed deposits (withdrawals negative).
    S.loss["netLoss"] = (S.loss["baseline"]["total"] + inflow) - pair["total"]
    S.loss["ready"] = True
    latch_loss_trigger(now)
    return S.loss

def latch_loss_trigger(now=None):
    if S.loss.get("triggered"):
        return True
    net = finite_num(S.loss.get("netLoss"))
    if not S.loss.get("ready") or S.loss.get("anomaly") or net is None:
        return False
    if net + 1e-9 < float(S.loss["limitUsd"]):
        return False
    now = now_ms() if now is None else now
    S.loss["triggered"] = True
    S.loss["triggeredAt"] = int(now)
    S.loss["triggeredLoss"] = float(net)
    S.save()
    return True

def parse_loss_limit(value):
    number = finite_num(value)
    if number is None or number <= 0 or number > LOSS_LIMIT_MAX:
        raise ValueError("金额必须是大于 0 且不超过 100000 的有限数字")
    return float(number)

def set_loss_limit(limit, now=None):
    """Persist the amount. A lower limit latches immediately. A higher limit does not unlock or change mode."""
    mode = S.mode
    S.loss["limitUsd"] = float(limit)
    if not S.loss.get("triggered"):
        latch_loss_trigger(now)
    S.save()
    S.mode = mode
    return S.loss

def reset_loss_baseline(now=None):
    """Explicit baseline reset. Does not change mode and refuses without a fresh verified sample."""
    clock = now_ms()
    now = clock if now is None else now
    last = S.loss.get("last")
    if S.loss.get("anomaly") or not isinstance(last, dict) or clock - last.get("t", 0) > EQUITY_MAX_AGE_MS:
        return False
    mode = S.mode
    S.loss["baseline"] = dict(io=last["io"], lt=last["lt"], total=last["total"], t=int(now))
    S.loss["triggered"] = False
    S.loss["triggeredAt"] = None
    S.loss["triggeredLoss"] = None
    S.loss["netLoss"] = 0.0
    S.loss["resetAt"] = int(now)
    S.loss["ready"] = True
    S.loss["anomaly"] = None
    S.save()
    S.mode = mode
    return True

def loss_view():
    last = S.loss.get("last") or {}
    base = S.loss.get("baseline") or {}
    return dict(limitUsd=S.loss.get("limitUsd"), triggered=bool(S.loss.get("triggered")),
                ready=bool(S.loss.get("ready")) and not S.loss.get("anomaly"), anomaly=S.loss.get("anomaly"),
                netLoss=S.loss.get("netLoss"), baselineAt=base.get("t"), baselineTotal=base.get("total"),
                equityIo=last.get("io"), equityLt=last.get("lt"), equityTotal=last.get("total"), equityAt=last.get("t"),
                resetAt=S.loss.get("resetAt"))

def classify_hl_cashflow(entries, address, since_ms=None):
    """Signed external USDC flow. Unknown ledger types fail closed. None means unverified.

    Events strictly before since_ms are outside the baseline window and are ignored,
    including types this function does not classify. `send` stays outside the allowlist:
    its documented fields are amount and usdcValue, not usdc, and sourceDex/destinationDex
    can move value between perp and spot for the same user. That is not the same as an
    external deposit, and it is not proven to be disjoint from internalTransfer.
    """
    if not isinstance(entries, list) or not isinstance(address, str):
        return None
    total = 0.0
    address = address.lower()
    for entry in entries:
        delta = entry.get("delta") if isinstance(entry, dict) else None
        if not isinstance(delta, dict):
            return None
        if since_ms is not None:
            when = ledger_time_ms(entry.get("time"))
            if when is None:
                return None
            if when < since_ms:
                continue
        kind = delta.get("type")
        if kind == "send":
            return None
        amount = finite_num(delta.get("usdc"))
        if amount is None:
            return None
        if kind == "deposit":
            total += amount
        elif kind == "withdraw":
            total -= amount
        elif kind in ("internalTransfer", "subAccountTransfer"):
            user, dest = str(delta.get("user") or "").lower(), str(delta.get("destination") or "").lower()
            if not user or not dest or user == dest:
                return None
            if dest == address and user != address:
                total += amount
            elif user == address and dest != address:
                total -= amount
            else:
                return None
        else:
            return None
    return total

def classify_lt_cashflow(deposits, withdraws, transfers, account_index, usdc_asset_id, since_ms):
    """Signed Lighter USDC flow since baseline. Pending or unknown rows fail closed."""
    try:
        return _classify_lt_cashflow(deposits, withdraws, transfers, account_index, usdc_asset_id, since_ms)
    except (TypeError, ValueError):
        return None

def _classify_lt_cashflow(deposits, withdraws, transfers, account_index, usdc_asset_id, since_ms):
    account_index = int(account_index)
    usdc_asset_id = int(usdc_asset_id)
    total = 0.0
    def stamp(value):
        return ledger_time_ms(value)
    for row in deposits or []:
        if not isinstance(row, dict) or row.get("asset_id") is None:
            return None
        if int(row.get("asset_id")) != usdc_asset_id:
            continue
        when = stamp(row.get("timestamp"))
        if when is None or when < since_ms:
            continue
        status, amount = row.get("status"), finite_num(row.get("amount"))
        if amount is None:
            return None
        if status == "completed":
            total += amount
        elif status == "failed":
            continue
        else:
            return None
    for row in withdraws or []:
        if not isinstance(row, dict) or row.get("asset_id") is None:
            return None
        if int(row.get("asset_id")) != usdc_asset_id:
            continue
        when = stamp(row.get("timestamp"))
        if when is None or when < since_ms:
            continue
        status, amount = row.get("status"), finite_num(row.get("amount"))
        if amount is None:
            return None
        if status == "completed":
            total -= amount
        elif status in ("failed", "refunded"):
            continue
        else:
            return None
    for row in transfers or []:
        if not isinstance(row, dict) or row.get("asset_id") is None:
            return None
        if int(row.get("asset_id")) != usdc_asset_id:
            continue
        when = stamp(row.get("timestamp"))
        if when is None or when < since_ms:
            continue
        if finite_num(row.get("fee")) not in (0, 0.0):
            return None
        kind, amount = row.get("type"), finite_num(row.get("amount"))
        if not isinstance(kind, str) or amount is None or kind == "L2SelfTransfer":
            return None
        if kind.endswith("Inflow") and int(row.get("to_account_index", -1)) == account_index:
            total += amount
        elif kind.endswith("Outflow") and int(row.get("from_account_index", -1)) == account_index:
            total -= amount
        else:
            return None
    return total

def _authorized_collection(payload):
    collection = payload.get("collection") if isinstance(payload, dict) else None
    if not isinstance(collection, dict) or collection.get("chain") != "ops-readonly-authorized":
        return None
    reader, queries = collection.get("reader"), collection.get("queries")
    if not isinstance(reader, str) or not reader.strip():
        return None
    if not isinstance(queries, list) or not queries or not all(isinstance(item, str) and item.strip() for item in queries):
        return None
    return collection

def _trade_size_sum(trades):
    if not isinstance(trades, list) or not trades:
        return None
    total = 0.0
    for trade in trades:
        if not isinstance(trade, dict):
            return None
        size = finite_num(trade.get("size"))
        if size is None or size <= 0:
            return None
        total += size
    return total

def audit_restore_intent(current, backup, official=None):
    """Propose one restored intent. Does not write state.

    A source string is not proof. official must be the authorized read-only
    collection record: order status filled, filled_base_amount, and trade sizes
    that sum to that amount and to the backup terminal evidence. A bare number,
    or a source string without those fields, is refused.
    """
    if not isinstance(current, dict) or not isinstance(backup, dict) or not isinstance(official, dict):
        return None
    if _authorized_collection(official) is None:
        return None
    if official.get("source") not in (None, "official-order", "official-submission"):
        return None
    order = official.get("order")
    if not isinstance(order, dict) or order.get("status") != "filled":
        return None
    if current.get("official_id") != backup.get("official_id") or current.get("venue") != backup.get("venue"):
        return None
    official_id = order.get("client_order_index", official.get("official_id"))
    if str(official_id) != str(current.get("official_id")) or official.get("venue") != current.get("venue"):
        return None
    if current.get("status") != "unknown" or official_evidence(current.get("terminal_evidence")):
        return None
    if backup.get("status") != "terminal":
        return None
    evidence = backup.get("terminal_evidence") if isinstance(backup.get("terminal_evidence"), dict) else None
    backed = finite_num(evidence.get("filled")) if evidence else None
    queried = finite_num(order.get("filled_base_amount"))
    summed = _trade_size_sum(official.get("trades"))
    typed = official.get("filled")
    if typed is not None and (finite_num(typed) is None or not math.isclose(finite_num(typed), queried or -1, rel_tol=0, abs_tol=1e-9)):
        return None
    if None in (backed, queried, summed) or backed <= 0 or not math.isclose(queried, backed, rel_tol=0, abs_tol=1e-9):
        return None
    if not math.isclose(summed, queried, rel_tol=0, abs_tol=1e-9):
        return None
    restored = dict(current)
    restored["status"] = "terminal"
    restored["terminal_evidence"] = dict(source="official-order", filled=float(queried), state="filled",
                                         audit_reader=official["collection"]["reader"])
    if order.get("order_index") is not None:
        restored["terminal_evidence"]["oid"] = order.get("order_index")
    restored["audit_note"] = "proposal-only-from-authorized-readonly-fixture"
    restored.pop("prior_evidence", None)
    return restored

def _same_id(left, right):
    return str(left.get("official_id")) == str(right.get("official_id")) and left.get("venue") == right.get("venue")

def propose_polluted_state(current, backup, official, window=None):
    """Review-only proposal. Does not write state and does not delete intents.

    Restores unknown rows only when the authorized fixture matches the backup.
    terminal_wait rows are left for the new reconciler. The 03:37 local marker
    is cleared only when no unknown remains and the window evidence says both
    venues had no new order or fill. The returned object is not a loadable state.
    """
    refused = dict(ok=False, dry_run=True, applied_to_input=False, wrote_inputs=False,
                   loadable_state=False, cryptographic_proof=False)
    if not isinstance(current, dict) or not isinstance(backup, dict) or not isinstance(official, dict):
        return dict(refused, reason="inputs-must-be-objects")
    current_intents, backup_intents = current.get("intents"), backup.get("intents")
    if not isinstance(current_intents, list) or not isinstance(backup_intents, list) or not current_intents:
        return dict(refused, reason="missing-intents")
    if len(current_intents) != len(backup_intents):
        return dict(refused, reason="intent-count-changed")
    if _authorized_collection(official) is None or not isinstance(official.get("records"), list):
        return dict(refused, reason="official-collection-refused")
    records = {}
    for record in official["records"]:
        if not isinstance(record, dict):
            return dict(refused, reason="official-record-refused")
        record = dict(record, collection=official["collection"])
        key = str(record.get("order", {}).get("client_order_index", record.get("official_id")))
        records[key] = record
    known = {str(row.get("official_id")) for row in current_intents}
    if any(key not in known for key in records):
        return dict(refused, reason="official-id-not-in-intents")
    proposed, restored = [], []
    for cur, bak in zip(current_intents, backup_intents):
        if not isinstance(cur, dict) or not isinstance(bak, dict) or not _same_id(cur, bak):
            return dict(refused, reason="intent-identity-mismatch")
        if cur.get("status") == "unknown" and not official_evidence(cur.get("terminal_evidence")):
            record = records.get(str(cur.get("official_id")))
            if record is None:
                proposed.append(json.loads(json.dumps(cur)))
                continue
            one = audit_restore_intent(cur, bak, record)
            if one is None:
                return dict(refused, reason="official-record-mismatch", official_id=cur.get("official_id"))
            proposed.append(one)
            restored.append(str(cur.get("official_id")))
        else:
            proposed.append(json.loads(json.dumps(cur)))
    if [str(row.get("official_id")) for row in proposed] != [str(row.get("official_id")) for row in current_intents]:
        return dict(refused, reason="intent-set-changed")
    marker = _marker_decision(current, proposed, window)
    pos = None if marker["cleared"] else json.loads(json.dumps(current.get("pos")))
    exit_pending = None if marker["cleared"] else json.loads(json.dumps(current.get("exit_pending")))
    before = dict(intents=current_intents, pos=current.get("pos"), exit_pending=current.get("exit_pending"), mode=current.get("mode"))
    after = dict(intents=proposed, pos=pos, exit_pending=exit_pending, mode=current.get("mode"))
    import difflib
    diff = "".join(difflib.unified_diff(
        json.dumps(before, ensure_ascii=False, sort_keys=True, indent=2).splitlines(True),
        json.dumps(after, ensure_ascii=False, sort_keys=True, indent=2).splitlines(True),
        fromfile="current", tofile="proposal"))
    return dict(ok=True, dry_run=True, applied_to_input=False, wrote_inputs=False, loadable_state=False,
                cryptographic_proof=False, kind="anth-restore-proposal", mode=current.get("mode"),
                proposed_intents=proposed, proposed_pos=pos, proposed_exit_pending=exit_pending,
                restored_ids=restored, marker=marker, intent_count=len(proposed), diff=diff,
                note="The source string is not a signature. This proposal trusts the supplied fixture only as the named authorized read-only collection.")

def _marker_decision(current, intents, window):
    reasons = []
    pending = current.get("exit_pending") if isinstance(current.get("exit_pending"), dict) else None
    pos = current.get("pos") if isinstance(current.get("pos"), dict) else None
    if pending is None or pending.get("submitted") is not True or pending.get("why") != "开仓":
        reasons.append("exit-marker-shape")
    marker_t = finite_num(pending.get("t")) if pending else None
    if marker_t is None:
        reasons.append("exit-marker-time")
    if pos is None or pos.get("opening_pending") is not True or pos.get("entryAccountingUnknown") is not True:
        reasons.append("opening-marker-shape")
    if pos is not None and pos.get("first_open_intent") not in (None, "", []):
        reasons.append("marker-has-intent-pointer")
    if any(row.get("status") == "unknown" for row in intents):
        reasons.append("unknown-remains")
    if marker_t is not None and any(finite_num(row.get("t")) is not None and finite_num(row.get("t")) >= marker_t for row in intents):
        reasons.append("intent-at-or-after-marker")
    if not isinstance(window, dict) or _authorized_collection(window) is None:
        reasons.append("window-collection-missing")
    else:
        start, end = finite_num(window.get("start_ms")), finite_num(window.get("end_ms"))
        positions = window.get("positions") if isinstance(window.get("positions"), dict) else {}
        venues = window.get("venues") if isinstance(window.get("venues"), dict) else {}
        if None in (start, end, marker_t) or not (start <= marker_t <= end):
            reasons.append("window-does-not-cover-marker")
        if finite_num(positions.get("io")) != 0 or finite_num(positions.get("lt")) != 0:
            reasons.append("official-position-not-flat")
        for venue in ("io", "lt"):
            side = venues.get(venue) if isinstance(venues.get(venue), dict) else {}
            if side.get("code") != 200 or side.get("orders") != 0 or side.get("fills") != 0:
                reasons.append("window-" + venue + "-not-empty")
    return dict(cleared=not reasons, reasons=reasons)

async def read_exposure(venue=None, paced=False):
    """Unknown current exposure never reuses a stale (possibly zero) observation."""
    try:
        v = venue or V
        now = time.monotonic()
        if paced and v._exposure_at is not None and now - v._exposure_at < v.exposure_interval:
            S.actual = None
            return None, None, None, None
        if paced: v._exposure_at = now
        result = await asyncio.wait_for(v.positions(), v.exposure_timeout)
        if positions_known(*result[:2]):
            S.actual = dict(io=result[0], lt=result[1], t=now_ms())
            S.last_actual = dict(S.actual)
        else:
            S.actual = None
        return result
    except BaseException:
        S.actual = None
        S.alert = "Unknown account exposure; exposure retained"
        raise

async def recover_exposure():
    """Orphan exposure has no strategy prices, quantities or PNL. Never erase unknown orders."""
    S.recovery = True
    S.mode = "stopped"
    S.save()
    try:
        if S.recovery_reduce_allowed:
            ok = await V.drive_to(0, 0, True, "exposure recovery")
        else:
            exposure = await read_exposure(paced=True)
            await V.reconcile_orders(exposure)
            S.alert = ("Unknown order outcome; manual review required; monitor-only recovery" if unknown_orders()
                       else "Monitor-only exposure recovery; no orders authorized")
            ok = False
    except Exception:
        S.alert = "Unknown exposure/order outcome; recovery retained; manual review required"
        S.save()
        return False
    if ok and not unknown_orders() and S.actual and S.actual["io"] == S.actual["lt"] == 0:
        S.recovery = False
        S.recovery_reduce_allowed = False
        S.exit_pending = None
        S.alert = None
    S.save()
    return not S.recovery

# ---------------- 策略 ----------------
DIRN = {"L": "买io / 卖Lighter", "S": "卖io / 买Lighter"}

def book_exec(dir_, closing, N):
    io, lt = B.io, B.lt()
    buy_io = (dir_ == "L") != closing
    pio, fio = vwap(io["asks"] if buy_io else io["bids"], N)
    plt, flt = vwap(lt["bids"] if buy_io else lt["asks"], N)
    return pio, plt, fio and flt

def exec_spread(dir_, closing, N):
    """按可成交价算价差 (bp)：开 S = io 买一侧VWAP(卖给bid) / Lighter 卖一侧VWAP(从ask买)；其余同理。"""
    pio, plt, ok = book_exec(dir_, closing, N)
    if not (pio and plt): return None, pio, plt, False
    return (pio / plt - 1) * 1e4, pio, plt, ok

def entry_dev(dir_, spr, med):
    """开仓方向上的偏离（正数 = 对我们有利）"""
    return (spr - med) if dir_ == "S" else (med - spr)

def round_pnl(p, exio, exlt):
    """按实际入场成交价 + 当前可成交出场价估算本轮净收益（扣两边手续费和资金费）"""
    si = 1 if p["dir"] == "L" else -1
    gross = si * (exio - risk_entry(p, "io")) * p["qIo"] - si * (exlt - risk_entry(p, "lt")) * p["qLt"]
    fin = p.get("feesIn")
    if fin is None: fin = p["N"] * (S.cfg["feeIo"] + S.cfg["feeLt"]) / 100
    fees = fin + (p["qIo"] * exio * S.cfg["feeIo"] + p["qLt"] * exlt * S.cfg["feeLt"]) / 100
    return gross - fees - p.get("fund", 0), fees

def free_margin():
    """两边可用保证金（余额超过 90 秒没刷新返回 None）"""
    bi, bl = BAL.get("io") or {}, BAL.get("lt") or {}
    fio = bi.get("usdc", 0) - bi.get("hold", 0) if now_ms() - bi.get("t", 0) < 90000 else None
    flt = bl.get("avail") if now_ms() - bl.get("t", 0) < 90000 else None
    return fio, flt

def plan_notional():
    return min(S.cfg["margin"] * S.cfg["lev"], S.cfg["maxNotional"])

def finish_ioc_miss(p, t, pio, plt):
    """Only a proven zero-fill first-leg IOC with fresh flat accounts may skip an entry."""
    if (S.recovery or not p.get("opening_pending") or not positions_known(pio, plt) or
            pio != 0 or plt != 0 or unknown_orders() or not S.intents):
        return False
    i = S.intents[-1]
    e = i.get("terminal_evidence", {})
    if (i.get("id") != p.get("first_open_intent") or i.get("venue") != "io" or
            i.get("purpose") != "开仓" or i.get("reduce") is not False or
            i.get("status") != "terminal" or i.get("pre") != {"io": 0, "lt": 0} or
            e.get("state") != "iocCancelRejected" or not positions_known(e.get("filled")) or e["filled"] != 0):
        return False
    S.pos = None; S.exit_pending = None
    S.cool = max(S.cool, t + S.cfg["cooldownSec"] * 1000)
    if S.mode == "live": S.alert = None
    S.note("sys", "Hyperliquid 首腿 IOC 零成交且双边仓位已确认归零，跳过本次信号，不重发、不记实盘交易")
    S.save()
    return True

async def open_round(dir_, t, spr, med, mid_spr=None):
    """spr = 信号时的可成交价差；入场后改用两边真实成交均价"""
    if S.recovery or unknown_orders() or (S.exit_pending and S.exit_pending.get("submitted")):
        S.alert = "Pending order reconciliation prohibits opening"
        return
    blocked, why = loss_blocks_open()
    if blocked:
        S.alert = why
        return
    N = plan_notional()
    if S.mode == "live":
        fio, flt = free_margin()
        if fio is None or flt is None:
            S.note("err", "余额未刷新，拒绝开仓"); S.cool = now_ms() + S.cfg["cooldownSec"] * 1000; return
        free = min(fio, flt)
        if free < N / LEVERAGE * 1.03:
            n2 = math.floor(free * LEVERAGE * 0.95)
            S.note("sys", f"保证金不够开 {N:.0f}U（需每边 ≥ {N / LEVERAGE * 1.03:.1f}U，io 可用 {fio:.2f}，Lighter 可用 {flt:.2f}），名义缩到 {n2}U")
            N = n2
        if N < 12:
            S.note("err", f"可用保证金太少（名义只剩 {N}U），拒绝开仓"); S.cool = now_ms() + S.cfg["cooldownSec"] * 1000; return
    pio, plt, ok = book_exec(dir_, False, N)
    if not ok: S.note("sys", "盘口深度不足，跳过开仓"); return
    qty = math.floor(N / ((pio + plt) / 2) * 10 ** HL_SZ_DEC) / 10 ** HL_SZ_DEC
    si = 1 if dir_ == "L" else -1
    S.pos = dict(dir=dir_, t=t, spread=spr, sigSpread=spr, sigMid=mid_spr, med=med, N=N, qIo=qty, qLt=qty, pxIo=pio, pxLt=plt,
                 quoteIo=pio, quoteLt=plt, fund=0, lastHr=t // 3600000, live=S.mode == "live")
    if S.mode == "live" and not DRY_RUN:
        S.pos.update(pxIo=None, pxLt=None, opening_pending=True, entryAccountingUnknown=True,
                     entryFeesKnown=False, feesIn=None)
    S.note("sig", f"开仓信号 {DIRN[dir_]} 可成交价差 {spr:+.1f}bp（中间价 {mid_spr:+.1f}bp）"
                  f" 中位数 {med:+.1f}bp 偏离 {entry_dev(dir_, spr, med):+.1f}bp 已持续 ≥{S.cfg['confirmSec']:g}s 数量 {qty}"
           if mid_spr is not None else f"开仓信号 {DIRN[dir_]} 可成交价差 {spr:+.1f}bp 中位数 {med:+.1f}bp 数量 {qty}")
    if S.mode == "live":
        try:
            lev_ok, s0, bal0 = await asyncio.gather(V.set_leverage(), read_exposure(), V.balances())
        except Exception:
            S.recovery = True; S.recovery_reduce_allowed = False; S.pos = None
            S.mode = "stopped"; S.alert = "Opening precheck failed; monitor-only exposure recovery"
            S.save(); return
        if not lev_ok:
            S.note("err", "设置杠杆失败，拒绝开仓")
            S.recovery = True; S.recovery_reduce_allowed = False; S.pos = None
            S.alert = "Leverage precheck failed; monitor-only exposure recovery"
            S.mode = "stopped"; S.cool = now_ms() + S.cfg["cooldownSec"] * 1000; S.save(); return
        if not positions_known(s0[0], s0[1]) or abs(s0[0]) > 0 or abs(s0[1]) > 0:
            S.recovery = True; S.recovery_reduce_allowed = False; S.pos = None
            S.alert = f"开仓前账户已有 ANTH 持仓 io={s0[0]} Lighter={s0[1]}，为安全不开新仓"; S.note("err", S.alert); S.mode = "stopped"; S.save(); return
        # 下单前最后复核：设杠杆/查持仓花了时间，价差可能已经没了
        spr2, _, _, ok2 = exec_spread(dir_, False, N)
        if quote_problem(now_ms()) or spr2 is None or not ok2 or entry_dev(dir_, spr2, med) < S.cfg["openBp"]:
            S.note("sys", f"下单前复核：可成交偏离只剩 {'—' if spr2 is None else f'{entry_dev(dir_, spr2, med):+.1f}'}bp < {S.cfg['openBp']:g}bp，放弃开仓")
            S.pos = None; S.cool = now_ms() + 5000; return
        blocked, why = loss_blocks_open()
        if blocked:
            S.pos = None
            S.alert = why
            S.save()
            return
        S.pos["bal0"] = bal0
        S.pos.update(opening_pending=True, opening_failed=False)
        S.save()  # crash/restart must never turn an incomplete entry into normal holding
        tr = V.track_start()
        intents_before = len(S.intents)
        ok = await V.drive_to(si * qty, -si * qty, False, "开仓")
        sent = len(S.intents) > intents_before or bool((getattr(V, "last_drive", None) or {}).get("submitted"))
        if not sent:
            # The gate returned before any new order. Drop the local open marker.
            # Do not record a submitted exit, and do not delete older intents.
            S.pos = None
            S.exit_pending = None
            if any(order_outcome_unknown(i) for i in S.intents):
                S.mode = "stopped"
                S.alert = "开仓未发单。仍有订单缺少官方终态，已停止新开仓；不重复发单，不平未知腿，不会自动恢复"
            else:
                S.alert = "本次开仓在发单前中止，未创建订单，不自动重发"
                S.cool = max(S.cool, now_ms() + 5000)
            S.save()
            return
        if not ok:
            if V.track is tr: V.track = None
            # Unknown is not a failed fill. A later official hedge must not be rescued
            # just because this flag was latched during the confirmation gap.
            if not unknown_orders():
                S.pos["opening_failed"] = True
            S.save()
        try:
            pi, pl, ei, el = await read_exposure()
        except Exception:
            pi = pl = ei = el = None
        if not ok and finish_ioc_miss(S.pos, now_ms(), pi, pl):
            return
        if unknown_orders() or not positions_known(pi, pl):
            S.pos["opening_pending"] = True
            S.pos["entryFeesKnown"] = False
            S.pos["entryAccountingUnknown"] = True
            S.pos["entryPxIoActual"] = S.pos["entryPxLtActual"] = None
            S.pos["pxIo"] = S.pos["pxLt"] = None
            S.pos["feesIn"] = None
            if not (S.mode == "stopped" and isinstance(S.alert, str) and "不会自动恢复" in S.alert):
                if not unresolved_exposure_guard(pi, pl, True):
                    S.alert = "Opening orders pending terminal/exposure reconciliation"
                else:
                    S.note("err", S.alert)
            # submitted is true only because this attempt already appended an intent above.
            S.exit_pending = dict(submitted=True, why="开仓", phase="submitted_unknown", t=now_ms())
            S.save(); return
        if not ok:
            S.mode = "stopped"
            S.note("err", f"开仓两腿不匹配(io={pi}, Lighter={pl})，全部平掉")
            tr2 = V.track_start()
            flat = await V.drive_to(0, 0, True, "残腿全平")
            fl2 = await V.fills(tr2)
            if not flat:
                S.alert = "⚠️ 残腿未能全平，已停止机器人，请立刻手动处理"; S.mode = "stopped"; S.note("err", S.alert)
            bal1 = await V.balances() if flat else None
            ub = None if not (bal0 and bal1) else (bal1["io"] - bal0["io"]) + (bal1["lt"] - bal0["lt"])
            if flat:
                S.trades.append(dict(t=t, dir=dir_, hold=now_ms() - t, inS=None, outS=None, sigS=spr, bp=None, u=None, uBal=ub,
                                     fees=None, reason="开仓单腿失败·全平",
                                     vol=0, live=True, real=False, status="unreconciled", note="余额变化不能归因于实际收益"))
            if flat and not unknown_orders():
                S.pos = None
                S.exit_pending = None
                S.alert = "开仓未完成，双边已核实归零；策略保持停止，待人工确认"
            S.cool = now_ms() + S.cfg["cooldownSec"] * 1000; S.save(); return
        fl = await V.fills(tr, si * qty, -si * qty)
        S.pos.pop("opening_pending", None)
        S.pos.pop("opening_failed", None)
        fio_, flt_ = fl["io"], fl["lt"]
        S.pos["entryPxIoActual"] = fio_.get("px") if valid_price(fio_.get("px")) else (ei if valid_price(ei) else None)
        S.pos["entryPxLtActual"] = flt_.get("px") if valid_price(flt_.get("px")) else (el if valid_price(el) else None)
        S.pos["pxIo"] = S.pos["entryPxIoActual"]
        S.pos["pxLt"] = S.pos["entryPxLtActual"]
        S.pos["srcIo"] = fio_.get("src"); S.pos["srcLt"] = flt_.get("src") if flt_.get("px") else ("持仓均价" if el else "盘口估算")
        feeIoIn = fio_.get("fee"); feeLtIn = flt_.get("fee")
        S.pos["entryFeesKnown"] = feeIoIn is not None and feeLtIn is not None
        S.pos["entryAccountingUnknown"] = not (valid_price(fio_.get("px")) or valid_price(ei)) or not (valid_price(flt_.get("px")) or valid_price(el))
        S.pos["feesIn"] = feeIoIn + feeLtIn if S.pos["entryFeesKnown"] else None; S.pos["feeIoIn"] = feeIoIn; S.pos["feeLtIn"] = feeLtIn
    else:
        S.pos["feesIn"] = None
    p = S.pos
    estimated_spread = (risk_entry(p, "io") / risk_entry(p, "lt") - 1) * 1e4 if all(valid_price(risk_entry(p, leg)) for leg in ("io", "lt")) else None
    p["entrySpread"] = None if p.get("entryAccountingUnknown") else estimated_spread
    p["entryDev"] = None if p.get("entryAccountingUnknown") else entry_dev(dir_, estimated_spread, med)
    p["spread"] = p["entrySpread"]
    p["weak"] = estimated_spread is not None and entry_dev(dir_, estimated_spread, med) < S.cfg["openBp"] / 2
    if p.get("entryAccountingUnknown"):
        S.note("sys", "Opening exposure confirmed; entry accounting unknown, price-dependent risk unavailable without actual entry prices")
    else:
        S.note("sys", f"开仓完成 io {si * qty:+} @ {p['pxIo']:.2f} Lighter {-si * qty:+} @ {p['pxLt']:.2f}")
    if p["feesIn"] is None: S.note("sys", "Entry fees unknown")
    S.save()

async def close_round(t, spr, why):
    """退出：先核对真实仓位，再用成交/可归因余额做收益对账。缺报价不发单；未知收益不标 real、不把占位价写成成交价。"""
    p = S.pos
    if not p:
        return
    # Exact control/time/rescue reasons retain authority even without a send.
    # Profit, stop and liquidation-distance signals remain conditional preflight.
    if why in ("一键全平", "到时强平", "残腿全平"):
        p["close_requested"] = p.get("close_requested") or why
        S.save()
    if S.exit_pending and S.exit_pending.get("submitted"):
        await reconcile_exit(t)
        return

    exio = exlt = uEst = feesEst = None
    try:
        qio, qlt, ok = book_exec(p["dir"], True, p["N"])
        if ok and valid_price(qio) and valid_price(qlt):
            exio, exlt = qio, qlt
            uEst, feesEst = round_pnl(p, exio, exlt)
            if not all(math.isfinite(v) for v in (uEst, feesEst)):
                uEst = feesEst = None
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        exio = exlt = uEst = feesEst = None

    if p.get("live"):
        V.last_drive = dict(submitted=False, ok=False)
        actual = await read_exposure()
        if not positions_known(*actual[:2]):
            S.alert = "Unknown account exposure; position retained"
            return
        p["exitActualio"], p["exitActuallt"] = abs(actual[0]), abs(actual[1])
        tr = V.track_start()
        ok = await V.drive_to(0, 0, True, "平仓", close_reason=why)
        submitted = bool((getattr(V, "last_drive", None) or {}).get("submitted"))
        if not ok:
            if submitted and unknown_orders():
                S.exit_pending = dict(submitted=True, why=why, t=t)
                if S.mode == "live" and why == "回归中位数":
                    S.alert = "正常平仓待核对订单终态，暂不新开仓、不会重复发单"
                else:
                    S.alert = "⚠️ 平仓未完成，已停止机器人，请手动检查两边持仓"
                    S.mode = "stopped"
            else:
                S.alert = f"⚠️ {why} 待处理：双腿预检未通过或未发单，本地仓位保留"
            S.note("err", S.alert); S.save(); return
        try:
            fl = await asyncio.wait_for(V.fills(tr, - (1 if p["dir"] == "L" else -1) * p["qIo"],
                                                (1 if p["dir"] == "L" else -1) * p["qLt"]), V.read_timeout)
        except Exception as exc:
            S.note("err", f"平仓成交明细暂不可用（{type(exc).__name__}），以真实持仓和订单终态为准")
            fl = {}
        rec = await _record_exit(p, t, why, fl, exio, exlt, uEst, feesEst, spr)
        _finalize_flat(rec, t)
        return

    rec = _paper_exit_record(p, t, why, exio, exlt, uEst, feesEst, spr)
    _finalize_flat(rec, t)

def _leg_known(fill):
    if not isinstance(fill, dict):
        return False
    if fill.get("src") in (None, "未查到"):
        return False
    return fill.get("px") is not None or fill.get("pnl") is not None

def _leg_record(p, fill, side, si):
    """Do not substitute entry/quote placeholders for missing fill px/pnl."""
    src = (fill or {}).get("src")
    px = (fill or {}).get("px")
    pnl = (fill or {}).get("pnl")
    fee_out = (fill or {}).get("fee")
    expected = (-si if side == "io" else si) * p.get("exitActual" + side, p["qIo" if side == "io" else "qLt"])
    known = (abs(expected) == p["qIo" if side == "io" else "qLt"] and _leg_known(fill) and p.get("dir") in ("L", "S") and
             positions_known(fill.get("q"), fill.get("fee")) and (fill.get("src") == "dry" or positions_known(fill.get("pnl"))) and
             abs(fill["q"] - expected) < 1e-8 and valid_price(fill.get("px")) and
             fill.get("src") in ("userFills", "trades", "dry") and
             p.get("entryFeesKnown", False) and not p.get("entryAccountingUnknown", False))
    entry = p["pxIo"] if side == "io" else p["pxLt"]
    q = p["qIo"] if side == "io" else p["qLt"]
    fee_cfg = S.cfg["feeIo"] if side == "io" else S.cfg["feeLt"]
    if not known:
        return dict(entry=entry, exit=None, gross=None, fees=None, net=None, src=src or "未查到", known=False)
    if side == "io":
        gross = pnl if (src == "userFills" and pnl is not None) else (si * (px - p["pxIo"]) * p["qIo"] if valid_price(px) else None)
    else:
        gross = pnl if (src == "trades" and pnl is not None) else (-si * (px - p["pxLt"]) * p["qLt"] if valid_price(px) else None)
    fee_in = p.get("feeIoIn" if side == "io" else "feeLtIn")
    fees = (fee_in + fee_out) if (fee_in is not None and fee_out is not None) else None
    net = (gross - fees) if positions_known(gross, fees) else None
    if not positions_known(net): known = False; net = None
    return dict(entry=entry, exit=px, gross=gross, fees=fees, net=net, src=src, known=known)

async def _record_exit(p, t, why, fl, exio, exlt, uEst, feesEst, spr):
    si = 1 if p["dir"] == "L" else -1
    fio, flt = (fl or {}).get("io") or {}, (fl or {}).get("lt") or {}
    legs = dict(io=_leg_record(p, fio, "io", si), lt=_leg_record(p, flt, "lt", si))
    uFill = fees = outS = None
    if legs["io"]["known"] and legs["lt"]["known"] and legs["io"]["net"] is not None and legs["lt"]["net"] is not None:
        uFill = legs["io"]["net"] + legs["lt"]["net"]
        if legs["io"]["fees"] is not None and legs["lt"]["fees"] is not None:
            fees = legs["io"]["fees"] + legs["lt"]["fees"]
        if valid_price(legs["io"]["exit"]) and valid_price(legs["lt"]["exit"]):
            outS = (legs["io"]["exit"] / legs["lt"]["exit"] - 1) * 1e4
    uBal = None
    await asyncio.sleep(0 if DRY_RUN else 1.5)
    # Optional diagnostics must not indefinitely delay an otherwise verified exit.
    try:
        bal1 = await asyncio.wait_for(V.balances(), V.read_timeout)
    except Exception:
        bal1 = None
    bal0 = p.get("bal0")
    if bal0 and bal1 and all(k in bal0 and k in bal1 and bal0[k] is not None and bal1[k] is not None for k in ("io", "lt")):
        try:
            d_io = bal1["io"] - bal0["io"]; d_lt = bal1["lt"] - bal0["lt"]
            if all(math.isfinite(v) for v in (d_io, d_lt)):
                uBal = d_io + d_lt
                legs["io"]["balDelta"] = d_io; legs["lt"]["balDelta"] = d_lt
        except (TypeError, ValueError):
            uBal = None
    # Only complete verified fills may establish realized PNL; balances are diagnostic.
    # Do not sum a missing leg as 0 to complete uFill.
    u = uFill
    status = "reconciled" if uFill is not None else "unreconciled"
    real = uFill is not None
    note = "按两边实际成交价计算（含两边手续费）" if real else "收益未核实：成交或账户变动不完整，未知字段为 None"
    bp = (u / p["N"] * 1e4) if (u is not None and p.get("N")) else None
    rec = dict(t=p["t"], dir=p["dir"], hold=t - p["t"], inS=p.get("entrySpread", p["spread"]), outS=outS, sigS=p.get("sigSpread"),
               bp=bp, u=u, uFill=uFill, uBal=uBal, uEst=uEst, fees=fees, feesEst=feesEst,
               legs=legs, reason=why, live=p.get("live", False), real=real, status=status, weak=p.get("weak", False), note=note)
    return rec

def _paper_exit_record(p, t, why, exio, exlt, uEst, feesEst, spr):
    outS = (exio / exlt - 1) * 1e4 if valid_price(exio) and valid_price(exlt) else spr
    bp = (uEst / p["N"] * 1e4) if (uEst is not None and p.get("N")) else None
    return dict(t=p["t"], dir=p["dir"], hold=t - p["t"], inS=p.get("entrySpread", p["spread"]), outS=outS, sigS=p.get("sigSpread"),
                bp=bp, u=uEst, uFill=None, uBal=None, uEst=uEst, fees=feesEst, legs=None, reason=why, live=False,
                real=False, status="paper", weak=p.get("weak", False),
                note="模拟：按盘口估算" if uEst is not None else "模拟：盘口不足，收益未知")

def _finalize_flat(rec, t):
    if unknown_orders() or S.recovery or (S.exit_pending and not rec.get("live")) or (rec.get("live") and (not S.actual or S.actual["io"] != 0 or S.actual["lt"] != 0)):
        S.alert = "Exposure or order outcome remains unresolved; local position retained"
        S.save()
        return
    S.trades.append(rec)
    u, why, fees, uBal, uEst = rec.get("u"), rec["reason"], rec.get("fees"), rec.get("uBal"), rec.get("uEst")
    if u is None:
        S.note("sig", f"平仓 {why} ｜ 收益未核实 status={rec.get('status')}"
                      + (f" ｜ 余额变化 {uBal:+.4f}U" if uBal is not None else "")
                      + (f" ｜ 盘口估算 {uEst:+.4f}U" if uEst is not None else ""))
    else:
        bp = rec.get("bp")
        S.note("sig", f"平仓 {why} ｜ 收益 {u:+.4f}U ({'' if bp is None else f'{bp:+.1f}'}bp)"
                      + (f" ｜ 余额变化 {uBal:+.4f}U" if uBal is not None else "")
                      + (f" ｜ 盘口估算 {uEst:+.4f}U" if uEst is not None else "")
                      + (f" ｜ 手续费 {fees:.4f}U" if fees is not None else ""))
    S.pos = None; S.exit_pending = None
    if S.mode == "live" and rec.get("live") and why == "回归中位数":
        S.alert = None
    if S.mode == "stopped" and S.alert and (
            "平仓未完成" in S.alert or "残腿未能全平" in S.alert or
            "Unknown account/order" in S.alert or "Order outcome unknown" in S.alert or
            "Opening orders pending" in S.alert):
        S.alert = "双边持仓已核实归零；策略保持停止，待人工确认"
    S.cool = now_ms() + S.cfg["cooldownSec"] * 1000; S.save()

async def reconcile_exit(t):
    """Read-only: if both venues are already flat, clear local pos without sending orders. PnL may stay unreconciled."""
    p = S.pos
    if not p or not (S.exit_pending and S.exit_pending.get("submitted")):
        return
    try:
        pio, plt, ei, el = await read_exposure(paced=True)
    except Exception:
        limited_note("reconcile", t, 5000, "risk", "Account reconciliation unavailable; exposure retained", time=t)
        return
    await V.reconcile_orders((pio, plt))
    if positions_known(pio, plt): S.actual = dict(io=pio, lt=plt, t=t)
    LIVE.update(posU=None, posBp=None)
    if not positions_known(pio, plt) or unknown_orders():
        if unresolved_exposure_guard(pio, plt, True):
            limited_note("naked-unknown", t, 15000, "err", S.alert)
        else:
            S.alert = "Unknown account/order outcome; manual review required"
        return
    if finish_ioc_miss(p, t, pio, plt):
        return
    if pio != 0 or plt != 0:
        # Proven terminal orders permit strategy/risk management of actual residuals.
        if S.intents and all(i.get("status") in ("terminal", "terminal_dry", "rejected") for i in S.intents):
            S.exit_pending = None
            opening = p.pop("opening_pending", False)
            if not opening:
                p["close_requested"] = p.get("close_requested") or "平仓对账"
            managed_hedge = False
            if opening:
                si = 1 if p["dir"] == "L" else -1
                hedge = (same_size(abs(pio), p["qIo"]) and same_size(abs(plt), p["qLt"])
                         and pio * si > 0 and plt * si < 0)
                # A completed official hedge is a position, even if an earlier ack timed out.
                p["rescue_pending"] = not hedge
                if hedge:
                    p.pop("opening_failed", None)
                    managed_hedge = True
                    # Official hedge resumes management of this position only.
                    # A stopped strategy is not switched back to live and cannot open a new round.
                    S.alert = "官方已确认完整对冲，继续管理这笔持仓；不因先前未知自动新开仓"
                if p["rescue_pending"]:
                    p["close_requested"] = "残腿全平"
                    S.mode = "stopped"
                p["entryAccountingUnknown"] = True
                p["entryFeesKnown"] = False
                p["qIo"], p["qLt"] = abs(pio), abs(plt)
                if not p["rescue_pending"]:
                    for leg, value in (("Io", ei), ("Lt", el)):
                        if valid_price(value):
                            p["px" + leg] = value
                            p["entryPx" + leg + "Actual"] = value
                            p["src" + leg] = "account"
                    p["entryAccountingUnknown"] = not (valid_price(p.get("pxIo")) and valid_price(p.get("pxLt")))
            if not managed_hedge:
                S.alert = "Terminal orders reconciled; actual exposure retained for strategy management"
            S.save()
        return
    why = p.get("close_requested") or (S.exit_pending or {}).get("why") or "平仓对账"
    if p.get("opening_pending") and p.get("opening_failed"):
        S.mode = "stopped"
    rec = dict(t=p["t"], dir=p["dir"], hold=t - p["t"], inS=p.get("entrySpread", p["spread"]), outS=None, sigS=p.get("sigSpread"),
               bp=None, u=None, uFill=None, uBal=None, uEst=None, fees=None, legs=None, reason=why,
               live=p.get("live", False), real=False, status="unreconciled", weak=p.get("weak", False),
               note="交易所已确认双腿为空，收益记录未核实")
    _finalize_flat(rec, t)


LIVE = {}
CONF = dict(dir=None, t0=0, n=0)   # 开仓信号持续性确认

_EVENT_LAST = {}

def limited_note(key, t, interval, kind, msg, **fields):
    if t - _EVENT_LAST.get(key, -float("inf")) >= interval:
        _EVENT_LAST[key] = t
        S.note(kind, msg, **fields)

def confirm_event(event, t, reason=None, dev=None):
    fields = dict(dir=CONF["dir"], durationMs=max(0, t - CONF["t0"]), n=CONF["n"],
                  devBp=dev if dev is not None else CONF.get("dev"), openBp=S.cfg["openBp"],
                  reason=reason, ioAgeMs=t - B.io["t"], ltAgeMs=t - B.lt_t, time=t)
    if event == "complete":
        S.note("confirm", event, **fields)
    else:
        interval = 30000 if reason == "invalid_price" else 5000
        limited_note(("confirm", reason if event == "reset" else "start"), t, interval, "confirm", event, **fields)

def reset_confirm(reason, t):
    if CONF["dir"] is not None: confirm_event("reset", t, reason)
    CONF.clear(); CONF.update(dir=None, t0=0, n=0)
    LIVE["confirm"] = None

def book_tradable(book, t):
    """Top-of-book exists, prices finite and >0, and not older than 15s."""
    if not book or not book.get("bids") or not book.get("asks"):
        return False
    try:
        return (positions_known(book.get("t")) and 0 <= t - book["t"] <= 15000 and
                all(valid_price(p) and valid_price(q) for side in ("bids", "asks") for p, q in book[side]) and
                book["bids"][0][0] <= book["asks"][0][0])
    except (TypeError, IndexError, KeyError, ValueError):
        return False

def quote_problem(t):
    if not B.io.get("bids") or not B.io.get("asks") or not B.lt().get("bids") or not B.lt().get("asks"): return "empty_book"
    return None if all(book_tradable(b, t) for b in (B.io, B.lt())) else "stale"

async def official_quote_snapshot():
    """Independent official REST snapshot for observation only. Never writes B (does not mix with WS)."""
    snap = dict(io=None, lt=None, t=now_ms())
    async with aiohttp.ClientSession() as session:
        async def fetch_io():
            async with session.post(HL_INFO, json={"type": "l2Book", "coin": COIN}) as r:
                d = await r.json()
            bids, asks = d["levels"]
            return dict(bids=[(float(x["px"]), float(x["sz"])) for x in bids],
                        asks=[(float(x["px"]), float(x["sz"])) for x in asks], t=now_ms(), src="rest")
        async def fetch_lt():
            async with session.get(LT_API + "/orderBookOrders", params=dict(market_id=MID, limit=100)) as r:
                d = await r.json()
            out_b, out_a = {}, {}
            for side, dest in (("bids", out_b), ("asks", out_a)):
                for x in d.get(side) or []:
                    px, sz = x.get("price"), x.get("remaining_base_amount", x.get("size"))
                    if px is None or sz is None: continue
                    px, sz = float(px), float(sz)
                    if not valid_price(px) or not math.isfinite(sz) or sz <= 0: continue
                    k = px_key(px); dest[k] = dest.get(k, 0.0) + sz
            return dict(bids=out_b, asks=out_a, t=now_ms(), src="rest")
        io_r, lt_r = await asyncio.gather(fetch_io(), fetch_lt(), return_exceptions=True)
        if not isinstance(io_r, Exception): snap["io"] = io_r
        if not isinstance(lt_r, Exception): snap["lt"] = lt_r
    return snap

def hold_decision(p, t, mi, ml):
    """持仓时的出场判断。返回 (why 或 None, 说明)。止损/强平保护立刻执行；回归平仓要满足最短持仓 + 实际收益≥手续费+minProfitBp。"""
    if p.get("close_requested"): return p["close_requested"], "Requested close remains active until both venues are flat"
    if p.get("rescue_pending"): return "残腿全平", "Terminal opening orders left unmatched actual exposure"
    # 沿用原墙钟语义：tick 的 t、now_ms()=time.time()*1000 与 p["t"] 相减。
    if quote_problem(t):
        LIVE.update(posU=None, posBp=None, exitSpr=None, liqDistIo=None, liqDistLt=None)
        limited_note("hold_quotes", t, 5000, "risk", "行情不可靠：价格风控不可用，仍检查到时强平", time=t)
        if t - p["t"] >= S.cfg["maxHoldMin"] * 60000: return "到时强平", ""
        return None, "行情不可靠，等待可信新鲜报价"
    if not all(valid_price(risk_entry(p, leg)) for leg in ("io", "lt")):
        LIVE.update(posU=None, posBp=None, exitSpr=None, liqIo=None, liqLt=None, liqDistIo=None, liqDistLt=None)
        if t - p["t"] >= S.cfg["maxHoldMin"] * 60000: return "到时强平", ""
        return None, "Actual entry price unavailable; price-dependent risk unavailable"
    io, lt = B.io, B.lt()
    buy_io = p["dir"] == "S"
    exio = quantity_vwap(io["asks"] if buy_io else io["bids"], p["qIo"])
    exlt = quantity_vwap(lt["bids"] if buy_io else lt["asks"], p["qLt"])
    complete = exio is not None and exlt is not None
    # 缺深度时旧名义 N 的有限价格仅供止损/展示，绝不用于回归/minProfit。
    if not complete:
        try: exio, exlt, _ = book_exec(p["dir"], True, p["N"])
        except (ValueError, ZeroDivisionError, OverflowError): exio = exlt = None
        limited_note("exit_estimate", t, 30000, "estimate", "完整数量收益估计不可用", time=t)
    u = bp = xs = None
    if valid_price(exio) and valid_price(exlt):
        u, fees = round_pnl(p, exio, exlt)
        bp = u / p["N"] * 1e4
        xs = (exio / exlt - 1) * 1e4
        if not all(math.isfinite(v) for v in (u, bp, xs)): u = bp = xs = None
    lio, llt = liq_prices(p); dio, dlt = abs(mi - lio) / mi * 100, abs(ml - llt) / ml * 100
    LIVE.update(posU=u, posBp=bp, exitSpr=xs, liqIo=lio, liqLt=llt, liqDistIo=dio, liqDistLt=dlt)
    held = t - p["t"]
    reverted = xs is not None and ((xs <= p["med"]) if p["dir"] == "S" else (xs >= p["med"]))
    if min(dio, dlt) < 8: return f"强平距离保护(io {dio:.1f}% / Lighter {dlt:.1f}%)", ""
    if bp is not None and bp <= -S.cfg["stopBp"]: return "止损", ""
    if held >= S.cfg["maxHoldMin"] * 60000: return "到时强平", ""
    if not complete or bp is None: return None, "完整数量收益估计不可用：深度不足或价格非法，继续持有"
    if reverted:
        if held < S.cfg["minHoldSec"] * 1000: return None, f"已回归，但最短持仓 {S.cfg['minHoldSec']:g}s 未到"
        if bp >= S.cfg["minProfitBp"]: return "回归中位数", ""
        return None, f"已回归，但扣费后只有 {bp:+.1f}bp < {S.cfg['minProfitBp']:g}bp，继续持有"
    return None, ("入场偏离偏小，只在有利润时平仓" if p.get("weak") else "等待回归")

async def tick(t):
    if S.recovery and not S.busy:
        S.busy = True
        try:
            await recover_exposure()
            LIVE.update(signal="Exposure recovery", holdNote=S.alert, stale=bool(quote_problem(t)), posU=None, posBp=None)
        finally:
            S.busy = False
        return
    problem = quote_problem(t)
    if problem:
        reset_confirm(problem, t)
        if S.pos and not S.busy:
            S.busy = True
            try:
                if S.exit_pending and S.exit_pending.get("submitted"):
                    await reconcile_exit(t)
                    LIVE.update(signal="持仓中", holdNote=S.alert or "平仓待对账，不重复发单", stale=True)
                    return
                why, note = hold_decision(S.pos, t, None, None)
                # Actual residual exits check only the needed venue quote in drive_to.
                if S.pos.get("live") and not why:
                    try:
                        actual = await read_exposure()
                        if positions_known(*actual[:2]):
                            S.actual = dict(io=actual[0], lt=actual[1], t=t)
                            if (actual[0] == 0) != (actual[1] == 0): why = "残腿全平"
                    except Exception:
                        S.alert = "Unknown account exposure; position retained"
                    if S.actual is None:
                        S.alert = "Unknown account exposure; position retained"
                        note = S.alert
                if why and S.pos.get("live"):
                    await close_round(t, None, why)
                    LIVE.update(signal="持仓中" if S.pos else "已平仓", holdNote=S.alert, stale=True, posU=None, posBp=None)
                    return
                if why:
                    limited_note("exit_noquote", t, 5000, "risk",
                                 f"{why} 待处理：行情不可靠（{problem}），未发单、不宣称风控已完成", time=t, reason=why, problem=problem)
                    S.alert = f"⚠️ {why} 待处理：无可信新鲜双腿报价，未发单"
                    note = S.alert
                LIVE.update(signal="持仓中", holdNote=note, stale=True)
                return
            finally:
                S.busy = False
        else:
            LIVE["signal"] = "数据中断"
            return
    io, lt = B.io, B.lt()
    stale = False
    mi = (io["bids"][0][0] + io["asks"][0][0]) / 2; ml = (lt["bids"][0][0] + lt["asks"][0][0]) / 2
    spr = (mi / ml - 1) * 1e4
    if not stale: S.buckets[t // 60000 * 60000] = spr
    med, n = median_now(t)
    N = S.pos["N"] if S.pos else plan_notional()
    xS = xL = None
    if med is not None:
        try:
            sS, _, _, okS = exec_spread("S", False, N); sL, _, _, okL = exec_spread("L", False, N)
        except (ValueError, ZeroDivisionError, OverflowError):
            sS = sL = None; okS = okL = False
        xS = entry_dev("S", sS, med) if okS and sS is not None and math.isfinite(sS) else None
        xL = entry_dev("L", sL, med) if okL and sL is not None and math.isfinite(sL) else None
    LIVE.update(t=t, io=dict(bid=io["bids"][0][0], ask=io["asks"][0][0]), lt=dict(bid=lt["bids"][0][0], ask=lt["asks"][0][0]),
                spread=spr, med=med, n=n, dev=None if med is None else spr - med, stale=stale, xDevS=xS, xDevL=xL)
    if not S.pos: LIVE.update(exitSpr=None, holdNote=None)
    if stale or S.busy or (S.mode == "stopped" and not S.pos):
        LIVE["signal"] = "数据中断" if stale else ("已停止" if S.mode == "stopped" else "执行中"); reset_confirm("stale" if stale else ("stopped" if S.mode == "stopped" else "busy"), t); return
    if not S.pos and n < S.cfg["medMin"] * 0.9: LIVE["signal"] = "预热中"; reset_confirm("warmup", t); return
    S.busy = True
    try:
        p = S.pos
        if p:
            reset_confirm("position", t)
            if S.exit_pending and S.exit_pending.get("submitted"):
                await reconcile_exit(t)
                LIVE["signal"] = "持仓中"; LIVE["holdNote"] = S.alert or "平仓待对账，不重复发单"
            else:
                hr = t // 3600000
                if all(valid_price(risk_entry(p, leg)) for leg in ("io", "lt")) and hr > p["lastHr"] and FUND["io"] is not None and FUND["lt"] is not None:
                    si = 1 if p["dir"] == "L" else -1
                    p["fund"] += (hr - p["lastHr"]) * (si * FUND["io"] * p["qIo"] * risk_entry(p, "io") - si * FUND["lt"] * p["qLt"] * risk_entry(p, "lt")); p["lastHr"] = hr
                why, note = hold_decision(p, t, mi, ml)
                LIVE["signal"] = "持仓中"; LIVE["holdNote"] = note
                if why: await close_round(t, spr, why)
        elif t >= S.cool:
            LIVE["signal"] = "观望"
            if S.mode == "live" and not V.ready: LIVE["signal"] = "实盘客户端未就绪"; reset_confirm("client_not_ready", t); return
            ob = S.cfg["openBp"]
            cand = "S" if (xS is not None and xS >= ob) else ("L" if (xL is not None and xL >= ob) else None)
            if S.cfg["confirmSec"] <= 0: reset_confirm("immediate", t)
            if cand != CONF["dir"] or cand is None:
                reset_confirm("candidate_gone" if cand is None else "direction_change", t)
                if cand:
                    CONF.update(dir=cand, t0=t, n=1, dev=xS if cand == "S" else xL)
                    confirm_event("start", t)
            else:
                CONF["n"] += 1
                CONF["dev"] = xS if cand == "S" else xL
            LIVE["confirm"] = None
            if cand:
                el = (t - CONF["t0"]) / 1000; LIVE["confirm"] = dict(dir=cand, sec=el, n=CONF["n"], need=S.cfg["confirmSec"])
                LIVE["signal"] = f"信号确认中 {el:.1f}/{S.cfg['confirmSec']:g}s"
                if S.cfg["confirmSec"] <= 0 or (el >= S.cfg["confirmSec"] and CONF["n"] >= 3):
                    xd = xS if cand == "S" else xL
                    confirm_event("complete", t)
                    CONF.clear(); CONF.update(dir=None, t0=0, n=0); LIVE["confirm"] = None
                    await open_round(cand, t, med + xd if cand == "S" else med - xd, med, spr)
        else:
            LIVE["signal"] = "冷却中"; reset_confirm("cooldown", t)
    finally:
        S.busy = False

async def engine():
    last_save = 0
    while True:
        await asyncio.sleep(TICK_SEC)
        try:
            t = now_ms(); await tick(t)
            if t - last_save > 10000: S.save(); last_save = t
        except Exception:
            log.error(traceback.format_exc()); S.busy = False

async def kill_all():
    """一键全平 + 停止"""
    S.mode = "stopped"; S.note("sys", "触发一键全平")
    while S.busy: await asyncio.sleep(0.2)
    S.busy = True
    try:
        if S.pos:
            await close_round(now_ms(), LIVE.get("spread"), "一键全平")
            if S.pos and not S.alert: S.alert = "⚠️ 一键全平未完成，请到交易所手动平仓"; S.note("err", S.alert)
        else:
            S.recovery = True
            S.recovery_reduce_allowed = True  # explicit user kill; persisted before any reduction
            S.save()
            ok = await recover_exposure()
            if not ok: S.alert = "⚠️ 一键全平未完成，请到交易所手动平仓"; S.note("err", S.alert)
        S.mode = "stopped"
    finally:
        S.busy = False; S.save()

# ---------------- 只读公开状态（无密钥、无控制能力） ----------------
def _sanitize(s):
    if s is None: return None
    s = str(s)
    for x in (CONTROL_TOKEN, PUBLIC_PATH, STATUS_PUSH_TOKEN, ENV("HL_API_PRIVATE_KEY", ""), ENV("LIGHTER_API_PRIVATE_KEY", ""), ENV("HL_ACCOUNT_ADDRESS", "")):
        if x and len(x) >= 6: s = s.replace(x, "***")
    s = re.sub(r"0x[0-9a-fA-F]{64}", "0x***", s)
    return s

def _key_stat(v):
    if v == "OK": return "OK"
    if not v: return None
    return "FAIL"

def public_snapshot():
    """给看板用的只读快照：不含密钥、令牌、钱包地址，也不能用来下单。"""
    L = LIVE or {}
    trades = []
    for r in S.trades[-200:]:
        lg = r.get("legs") or {}
        trades.append(dict(t=r.get("t"), dir=r.get("dir"), hold=r.get("hold"), inS=r.get("inS"), outS=r.get("outS"), sigS=r.get("sigS"),
                           bp=r.get("bp"), u=r.get("u"), uFill=r.get("uFill"), uBal=r.get("uBal"), uEst=r.get("uEst"),
                           status=r.get("status", "unknown"), real=bool(r.get("real")), fees=r.get("fees"), reason=_sanitize(r.get("reason")), note=_sanitize(r.get("note")),
                           legs={k: {kk: v.get(kk) for kk in ("entry", "exit", "gross", "fees", "net", "balDelta")} for k, v in lg.items() if isinstance(v, dict)},
                           live=bool(r.get("live"))))
    resolved = [r for r in S.trades if r.get("real") and r.get("status") == "reconciled" and positions_known(r.get("u"))]
    net = sum(r["u"] for r in resolved)
    wins = sum(r["u"] > 0 for r in resolved)
    n = len(resolved)
    pos = None
    p = S.pos
    if p:
        pos = dict(dir=p.get("dir"), dirLabel=DIRN.get(p.get("dir"), p.get("dir")), live=bool(p.get("live")),
                   t=p.get("t"), heldMs=max(0, now_ms() - (p.get("t") or now_ms())),
                   qIo=p.get("qIo"), qLt=p.get("qLt"), pxIo=None if p.get("entryAccountingUnknown") else p.get("pxIo"),
                   pxLt=None if p.get("entryAccountingUnknown") else p.get("pxLt"),
                   entryAccountingUnknown=bool(p.get("entryAccountingUnknown")),
                   N=p.get("N"), spread=p.get("spread"), target=p.get("med"),
                   entrySpread=p.get("entrySpread"), sigSpread=p.get("sigSpread"), entryDev=p.get("entryDev"), weak=bool(p.get("weak")),
                   feesIn=p.get("feesIn"), exitSpr=L.get("exitSpr"), holdNote=L.get("holdNote"),
                   uPnL=None if S.exit_pending or p.get("entryAccountingUnknown") else L.get("posU"),
                   bp=None if S.exit_pending or p.get("entryAccountingUnknown") else L.get("posBp"),
                   liqIo=L.get("liqIo"), liqLt=L.get("liqLt"),
                   liqDistIo=L.get("liqDistIo"), liqDistLt=L.get("liqDistLt"))
    olog = []
    for e in (S.olog or [])[-40:]:
        if not isinstance(e, dict): continue
        olog.append(dict(t=e.get("t"), kind=e.get("kind"), msg=_sanitize(e.get("msg"))))
    ks = sorted(S.buckets); lo = now_ms() - 6 * 3600e3
    series = [[k, S.buckets[k]] for k in ks if k >= lo]
    bi, bl = BAL.get("io") or {}, BAL.get("lt") or {}
    return dict(
        ok=True, ts=now_ms(),
        mode=S.mode, dryRun=DRY_RUN, liveEnabled=LIVE_ENABLED,
        venuesReady=bool(V.ready), venuesErr=_sanitize(V.err),
        alert=_sanitize(S.alert), signal=L.get("signal"), stale=bool(L.get("stale")),
        prices=dict(io=L.get("io"), lt=L.get("lt")),
        spread=L.get("spread"), median=L.get("med"), dev=L.get("dev"), nMed=L.get("n"),
        cfg=dict(margin=S.cfg.get("margin"), maxNotional=S.cfg.get("maxNotional"), openBp=S.cfg.get("openBp"),
                 stopBp=S.cfg.get("stopBp"), maxHoldMin=S.cfg.get("maxHoldMin"), medMin=S.cfg.get("medMin"),
                 confirmSec=S.cfg.get("confirmSec"), minProfitBp=S.cfg.get("minProfitBp"), minHoldSec=S.cfg.get("minHoldSec"),
                 feeIo=S.cfg.get("feeIo"), feeLt=S.cfg.get("feeLt"), lev=LEVERAGE),
        xDevS=L.get("xDevS"), xDevL=L.get("xDevL"), confirm=L.get("confirm"),
        position=pos, recoveryExposure=S.recovery, recoveryReduceAllowed=S.recovery_reduce_allowed, actualExposure=S.actual, lastKnownExposure=S.last_actual,
        exit_pending=(dict(status="unknown" if unknown_orders() else "pending", manualReview=unknown_orders()) if S.exit_pending else None),
        totals=dict(count=n, wins=wins, losses=max(0, n - wins), net=net, winRate=(wins / n if n else None)),
        trades=trades, olog=olog, series=series,
        balances=dict(
            io=dict(usdc=bi.get("usdc"), hold=bi.get("hold"), pos=bi.get("pos"), liq=bi.get("liq"), mode=bi.get("mode")),
            lt=dict(collateral=bl.get("collateral"), avail=bl.get("avail"), pos=bl.get("pos"), liq=bl.get("liq")),
        ),
        keys=dict(io=_key_stat(BAL.get("ioKey")), lt=_key_stat(BAL.get("ltKey"))),
        fund=FUND, venue=dict(mid=MID, lev=LEVERAGE),
    )

_rate = collections.defaultdict(list)
@web.middleware
async def _cors_mw(req, handler):
    if req.method == "OPTIONS":
        r = web.Response(status=204)
    else:
        ip = req.remote or "0"
        now = time.time(); q = _rate[ip]; q[:] = [x for x in q if now - x < 60]
        if len(q) >= 60:
            r = web.json_response(dict(ok=False, err="rate limited"), status=429)
        else:
            q.append(now); r = await handler(req)
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    r.headers["Cache-Control"] = "no-store"
    return r

async def h_public(req):
    return web.json_response(public_snapshot())

async def start_public():
    if not PUBLIC_PATH:
        log.info("未配置 PUBLIC_PATH，不开启只读状态端口")
        return None
    app = web.Application(middlewares=[_cors_mw])
    app.router.add_get(f"/{PUBLIC_PATH}", h_public)
    app.router.add_get(f"/{PUBLIC_PATH}/public", h_public)
    run = web.AppRunner(app); await run.setup()
    await web.TCPSite(run, PUBLIC_BIND, PUBLIC_PORT).start()
    log.info("只读状态端口已开 %s:%d（路径不记录）", PUBLIC_BIND, PUBLIC_PORT)
    return run

PUSH_HEARTBEAT_SEC = float(ENV("STATUS_PUSH_HEARTBEAT_SEC", "10800"))   # 心跳：3 小时一次
PUSH_MIN_GAP_SEC = 600     # 非交易类变化（模式/报警）最少间隔 10 分钟

def _push_key():
    """只在这些东西变化时推送：交易笔数、持仓开/平、模式、场馆就绪、报警、参数。"""
    p = S.pos or {}
    c = S.cfg or {}
    trade_key = (len(S.trades), bool(S.pos), p.get("dir"), p.get("t"))
    other_key = (S.mode, bool(V.ready), str(S.alert or "")[:80],
                 tuple(c.get(k) for k in ("margin", "maxNotional", "openBp", "stopBp", "feeLt", "feeIo")))
    return trade_key, other_key

async def push_status():
    """看板推送：仅在开/平仓、模式变化时推送，外加低频心跳。失败只记日志，绝不影响交易。"""
    if not STATUS_PUSH_URL or not STATUS_PUSH_TOKEN:
        return
    last_err = 0; last_push = 0.0; last_fail = 0.0; last_tk = None; last_ok_key = None
    await asyncio.sleep(20)   # 等行情和余额先起来再推
    while True:
        try:
            tk, ok_ = _push_key()
            now = time.time()
            due = (last_ok_key is None or tk != last_tk
                   or (ok_ != last_ok_key and now - last_push >= PUSH_MIN_GAP_SEC)
                   or now - last_push >= PUSH_HEARTBEAT_SEC)
            if due and now - last_fail >= 60:
                async with aiohttp.ClientSession() as s:
                    async with s.post(STATUS_PUSH_URL, json=public_snapshot(),
                                      headers={"Authorization": "Bearer " + STATUS_PUSH_TOKEN, "Content-Type": "application/json"},
                                      timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status >= 300:
                            raise RuntimeError("HTTP %s" % r.status)
                last_push = now; last_tk = tk; last_ok_key = ok_
        except Exception as e:
            last_fail = time.time()   # 失败后 60 秒再试，避免狂刷
            if now_ms() - last_err > 600000:
                log.warning("status push failed: %s", e); last_err = now_ms()
        await asyncio.sleep(5)

# ---------------- HTTP 控制面板 ----------------

def auth(req):
    if CONTROL_TOKEN and not secrets.compare_digest(req.headers.get("X-Token", ""), CONTROL_TOKEN): raise web.HTTPUnauthorized()

async def h_index(req): return web.FileResponse(HERE / "control.html")
async def h_state(req):
    auth(req)
    ks = sorted(S.buckets); lo = now_ms() - 6 * 3600e3
    series = [[k, S.buckets[k]] for k in ks if k >= lo]
    return web.json_response(dict(mode=S.mode, cfg=S.cfg, pos=S.pos, trades=S.trades[-200:], olog=S.olog[-150:], live=LIVE, alert=S.alert,
                                  liveEnabled=LIVE_ENABLED, dryRun=DRY_RUN, venuesReady=V.ready, venuesErr=V.err, fund=FUND, series=series, bal=BAL,
                                  loss=loss_view(), venue=dict(ltUrl=LT_URL, mid=MID, lev=LEVERAGE)))
async def h_cfg(req):
    auth(req); d = await req.json()
    for k in DEF_CFG:
        if k in d: S.cfg[k] = float(d[k])
    S.note("sys", "参数已更新"); S.save(); return web.json_response(dict(ok=True))
async def h_mode(req):
    auth(req); m = (await req.json()).get("mode")
    if m not in ("paper", "live", "stopped"): raise web.HTTPBadRequest()
    if m == "live":
        if not LIVE_ENABLED: return web.json_response(dict(ok=False, err="服务器 .env 未设置 LIVE_ENABLED=1"))
        if S.pos and not S.pos.get("live"): return web.json_response(dict(ok=False, err="先平掉模拟持仓再切实盘"))
        if not DRY_RUN and (not S.loss.get("ready") or S.loss.get("anomaly") or not S.loss.get("baseline")):
            return web.json_response(dict(ok=False, err=S.loss.get("anomaly") or "亏损基线未就绪，不能切换 live"))
        if unknown_orders() or S.recovery or (S.exit_pending and S.exit_pending.get("submitted")):
            return web.json_response(dict(ok=False, err="未确认订单或退出尚未完成，不能切换 live"))
        if not V.ready: await V.init()
        if not V.ready: return web.json_response(dict(ok=False, err=V.err))
    if m == "paper" and (S.recovery or unknown_orders() or S.exit_pending or (S.pos and S.pos.get("live"))): return web.json_response(dict(ok=False, err="有实盘持仓，先一键全平"))
    S.mode = m; S.note("sys", f"切换模式 → {m}"); S.save(); return web.json_response(dict(ok=True))
async def h_kill(req): auth(req); await kill_all(); return web.json_response(dict(ok=True, alert=S.alert))
async def h_clear(req):
    auth(req)
    if S.pos: return web.json_response(dict(ok=False, err="有持仓时不能清空"))
    S.trades = []; S.alert = None; S.save(); return web.json_response(dict(ok=True))
async def h_loss_limit(req):
    auth(req)
    mode = S.mode
    try:
        body = await req.json()
        limit = parse_loss_limit(body.get("limitUsd") if isinstance(body, dict) else None)
    except Exception:
        return web.json_response(dict(ok=False, err="金额必须是大于 0 且不超过 100000 的有限数字"))
    set_loss_limit(limit)
    S.mode = mode
    return web.json_response(dict(ok=True, loss=loss_view(), mode=S.mode))
async def h_loss_reset(req):
    auth(req)
    mode = S.mode
    try:
        body = await req.json()
    except Exception:
        body = {}
    if not isinstance(body, dict) or body.get("confirm") is not True:
        return web.json_response(dict(ok=False, err="需要确认重置亏损基线"))
    if not reset_loss_baseline():
        return web.json_response(dict(ok=False, err="双边权益未就绪或数据异常，不能重置"))
    S.mode = mode
    return web.json_response(dict(ok=True, loss=loss_view(), mode=S.mode))

async def main():
    raise RuntimeError("PUBLIC_ARCHIVE_SERVICE_START_DISABLED")
    try: await backfill()
    except Exception as e: log.warning("回填失败: %s", e)
    log.info("Lighter=%s market=%d 杠杆=%dx", LT_URL, MID, LEVERAGE)
    if S.mode == "live" or (S.pos and S.pos.get("live")): await V.init()   # stopped restarts must not change leverage or initialize signing clients
    app = web.Application(); app.add_routes([web.get("/", h_index), web.get("/api/state", h_state), web.post("/api/cfg", h_cfg),
                                             web.post("/api/mode", h_mode), web.post("/api/kill", h_kill), web.post("/api/clear", h_clear),
                                             web.post("/api/loss-limit", h_loss_limit), web.post("/api/loss-baseline/reset", h_loss_reset)])
    run = web.AppRunner(app); await run.setup(); await web.TCPSite(run, BIND, PORT).start()
    log.info("控制面板: http://%s:%d  模式=%s LIVE_ENABLED=%s DRY_RUN=%s", BIND, PORT, S.mode, LIVE_ENABLED, DRY_RUN)
    await start_public()
    await asyncio.gather(ws_io(), ws_lt(), poll_funding(), poll_balances(), monitor_stopped_book(), engine(), push_status())

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import selftest; selftest.run(); sys.exit(0)
    asyncio.run(main())
