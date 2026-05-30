import asyncio
import time
import datetime
from typing import Optional

import httpx
from fastapi import APIRouter

router = APIRouter()

# ── In-memory cache ───────────────────────────────────────────────────────────
_cache: dict = {}
CACHE_TTL = 180  # seconds


def _get_cache(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


# ── External endpoints ────────────────────────────────────────────────────────
GECKO_NEW_URL  = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"
GECKO_ADDR_URL = "https://api.geckoterminal.com/api/v2/networks/solana/tokens/{address}/pools"
GOPLUS_URL     = "https://api.gopluslabs.io/api/v1/solana/token_security"
DEX_URL        = "https://api.dexscreener.com/latest/dex/tokens/{address}"

HEADERS = {"Accept": "application/json"}
TIMEOUT = 12.0


# ── Safe converters ───────────────────────────────────────────────────────────
def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def safe_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ── External API helpers ──────────────────────────────────────────────────────
async def fetch_new_pools(client: httpx.AsyncClient) -> list:
    """Fetch latest new Solana pools from GeckoTerminal."""
    try:
        r = await client.get(
            GECKO_NEW_URL,
            params={"page": 1},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as exc:
        print(f"[GeckoTerminal new_pools error] {exc}")
        return []


async def fetch_pools_for_token(client: httpx.AsyncClient, address: str) -> list:
    """Fetch all pools for a specific token address from GeckoTerminal."""
    try:
        url = GECKO_ADDR_URL.format(address=address)
        r = await client.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as exc:
        print(f"[GeckoTerminal token pools error] {exc}")
        return []


async def fetch_dexscreener(client: httpx.AsyncClient, address: str) -> dict:
    """Fetch token data from DexScreener as a fallback."""
    try:
        url = DEX_URL.format(address=address)
        r = await client.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        pairs = r.json().get("pairs") or []
        # Return the first Solana pair with the most liquidity
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not sol_pairs:
            return {}
        sol_pairs.sort(key=lambda p: safe_float(p.get("liquidity", {}).get("usd")), reverse=True)
        return sol_pairs[0]
    except Exception as exc:
        print(f"[DexScreener error] {exc}")
        return {}


async def fetch_security(client: httpx.AsyncClient, address: str) -> dict:
    """Fetch token security data from GoPlus."""
    try:
        r = await client.get(
            GOPLUS_URL,
            params={"contract_addresses": address},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        return result.get(address.lower(), result.get(address, {}))
    except Exception as exc:
        print(f"[GoPlus error] {exc}")
        return {}


# ── Build normalised pool_attrs from DexScreener pair ────────────────────────
def dex_pair_to_attrs(pair: dict) -> dict:
    """Convert a DexScreener pair dict into the same shape as GeckoTerminal attrs."""
    if not pair:
        return {}
        liquidity = safe_float(pair.get("liquidity", {}).get("usd"))
        volume_1h = safe_float(pair.get("volume", {}).get("h1"))
        fdv       = safe_float(pair.get("fdv"))
        price     = pair.get("priceUsd", "0")
        price_1h  = safe_float(pair.get("priceChange", {}).get("h1"))
        name      = pair.get("baseToken", {}).get("name", "Unknown Token")
        created   = pair.get("pairCreatedAt")  # epoch ms

    created_iso = ""
    if created:
        try:
            dt = datetime.datetime.fromtimestamp(int(created) / 1000, tz=datetime.timezone.utc)
            created_iso = dt.isoformat()
        except Exception:
            pass

    # buys/sells
    txns_1h = pair.get("txns", {}).get("h1", {})

    return {
        "name":            name,
        "fdv_usd":         fdv,
        "reserve_in_usd":  liquidity,
        "base_token_price_usd": price,
        "pool_created_at": created_iso,
        "volume_usd":      {"h1": volume_1h},
        "price_change_percentage": {"h1": price_1h},
        "transactions":    {"h1": {"buys": safe_int(txns_1h.get("buys")), "sells": safe_int(txns_1h.get("sells"))}},
        "address":         pair.get("pairAddress", ""),
    }


# ── Scoring ───────────────────────────────────────────────────────────────────
def compute_score(pool_attrs: dict, security: dict) -> dict:
    volume_1h = safe_float(pool_attrs.get("volume_usd", {}).get("h1"))
    liquidity = safe_float(pool_attrs.get("reserve_in_usd"))
    fdv       = safe_float(pool_attrs.get("fdv_usd"))
    price_pct = safe_float(pool_attrs.get("price_change_percentage", {}).get("h1"))
    txns_buy  = safe_int(pool_attrs.get("transactions", {}).get("h1", {}).get("buys"))
    txns_sell = safe_int(pool_attrs.get("transactions", {}).get("h1", {}).get("sells"))

    # Token age in hours
    age_hours  = 999.0
    created_at = pool_attrs.get("pool_created_at", "")
    if created_at:
        try:
            created   = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            now       = datetime.datetime.now(datetime.timezone.utc)
            age_hours = (now - created).total_seconds() / 3600
        except Exception:
            pass

    # Security flags
    is_honeypot = safe_int(security.get("is_honeypot")) == 1
    is_mintable = safe_int(security.get("is_mintable")) == 1
    freeze_auth = security.get("freeze_authority") not in (None, "", "0", 0)
    owner_ok    = security.get("owner_address") in (
        "", None, "0x0000000000000000000000000000000000000000"
    )

    top10_pct = safe_float(security.get("top_10_holder_percent", 0)) * 100
    if top10_pct <= 1.0:
        top10_pct *= 100

    # ── Base scores ───────────────────────────────────────────────────────────
    if   volume_1h >= 100_000: vol_score = 25
    elif volume_1h >= 50_000:  vol_score = 20
    elif volume_1h >= 20_000:  vol_score = 15
    elif volume_1h >= 5_000:   vol_score = 10
    else:                      vol_score = 0

    total_txns = txns_buy + txns_sell
    buy_ratio  = (txns_buy / total_txns) if total_txns > 0 else 0.5
    tx_score   = round(buy_ratio * 20)

    if   liquidity >= 50_000: liq_score = 20
    elif liquidity >= 20_000: liq_score = 15
    elif liquidity >= 10_000: liq_score = 10
    elif liquidity >= 5_000:  liq_score = 5
    else:                     liq_score = 0

    if   price_pct >= 20: price_score = 15
    elif price_pct >= 10: price_score = 12
    elif price_pct >= 5:  price_score = 8
    elif price_pct >= 0:  price_score = 5
    else:                 price_score = 0

    if   age_hours <= 1:  age_score = 10
    elif age_hours <= 6:  age_score = 8
    elif age_hours <= 12: age_score = 5
    elif age_hours <= 24: age_score = 2
    else:                 age_score = 0

    if   fdv <= 5_000:  fdv_score = 10
    elif fdv <= 10_000: fdv_score = 8
    elif fdv <= 15_000: fdv_score = 5
    elif fdv <= 20_000: fdv_score = 2
    else:               fdv_score = 0

    base = vol_score + tx_score + liq_score + price_score + age_score + fdv_score
    # ── Penalties ─────────────────────────────────────────────────────────────
    penalties     = 0
    penalty_flags = []

    if is_mintable:
        penalties += 30
        penalty_flags.append("Mintable")
    if freeze_auth:
        penalties += 20
        penalty_flags.append("Freeze Authority")
    if not owner_ok and security:
        penalties += 20
        penalty_flags.append("Ownership Not Renounced")
    if top10_pct >= 80:
        penalties += 20
        penalty_flags.append("High Holder Concentration")

    final_score = max(0, min(100, base - penalties))

    return {
        "score": final_score,
        "breakdown": {
            "volume_momentum":    vol_score,
            "buy_pressure":       tx_score,
            "liquidity_strength": liq_score,
            "price_momentum":     price_score,
            "age_freshness":      age_score,
            "fdv_attractiveness": fdv_score,
            "penalties":          -penalties,
        },
        "security_flags": {
            "is_honeypot":      is_honeypot,
            "is_mintable":      is_mintable,
            "freeze_authority": freeze_auth,
            "owner_renounced":  bool(owner_ok),
            "top10_holder_pct": round(top10_pct, 1),
            "penalty_flags":    penalty_flags,
        },
        "age_hours": round(age_hours, 1),
    }


# ── Filter (dashboard only) ───────────────────────────────────────────────────
def passes_filter(pool_attrs: dict, score_data: dict) -> bool:
    fdv       = safe_float(pool_attrs.get("fdv_usd"))
    liquidity = safe_float(pool_attrs.get("reserve_in_usd"))
    volume_1h = safe_float(pool_attrs.get("volume_usd", {}).get("h1"))
    age_hours = score_data["age_hours"]
    flags     = score_data["security_flags"]

    if fdv       > 20_000: return False
    if age_hours > 24:     return False
    if volume_1h < 5_000:  return False
    if liquidity < 5_000:  return False
    if flags["is_honeypot"]:            return False
    if flags["is_mintable"]:            return False
    if flags["top10_holder_pct"] >= 80: return False
    if score_data["score"] < 60:        return False
    return True


# ── Token dict builder (dashboard) ───────────────────────────────────────────
def build_token_dict(pool: dict, security: dict) -> Optional[dict]:
    attrs = pool.get("attributes", {})
    rels  = pool.get("relationships", {})

    token_id     = rels.get("base_token", {}).get("data", {}).get("id", "")
    address      = token_id.replace("solana_", "") if "solana_" in token_id else token_id
    pool_address = attrs.get("address", pool.get("id", "").replace("solana_", ""))

    score_data = compute_score(attrs, security)
    if not passes_filter(attrs, score_data):
        return None

    name = attrs.get("name", "Unknown")
    if " / " in name:
        name = name.split(" / ")[0].strip()

    dex_link = (
        f"https://dexscreener.com/solana/{pool_address}"
        if pool_address else "#"
    )

    return {
        "address":         address,
        "pool_address":    pool_address,
        "name":            name,
        "symbol":          name,
        "fdv":             round(safe_float(attrs.get("fdv_usd")), 2),
        "liquidity":       round(safe_float(attrs.get("reserve_in_usd")), 2),
        "volume_1h":       round(safe_float(attrs.get("volume_usd", {}).get("h1")), 2),
        "price_usd":       attrs.get("base_token_price_usd", "0"),
        "price_change_1h": round(safe_float(attrs.get("price_change_percentage", {}).get("h1")), 2),
        "score":           score_data["score"],
        "breakdown":       score_data["breakdown"],
        "security_flags":  score_data["security_flags"],
        "age_hours":       score_data["age_hours"],
        "dex_link":        dex_link,
    }


# ══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/tokens")
async def get_tokens():
    cached = _get_cache("tokens")
    if cached:
        return {"source": "cache", "tokens": cached}

    async with httpx.AsyncClient() as client:
        pools = await fetch_new_pools(client)

    if not pools:
        fallback = _cache.get("tokens", {}).get("data")
        if fallback:
            return {"source": "stale_cache", "tokens": fallback}
        return {"source": "empty", "tokens": []}

    tokens: list = []
    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(5)

        async def process(pool):
            attrs     = pool.get("attributes", {})
            fdv       = safe_float(attrs.get("fdv_usd"))
            liquidity = safe_float(attrs.get("reserve_in_usd"))
            volume_1h = safe_float(attrs.get("volume_usd", {}).get("h1"))
            if fdv > 20_000 or liquidity < 5_000 or volume_1h < 5_000:
                return None
            token_id = (
                pool.get("relationships", {})
                    .get("base_token", {})
                    .get("data", {})
                    .get("id", "")
            )
            address = token_id.replace("solana_", "") if "solana_" in token_id else token_id
            async with sem:
                security = await fetch_security(client, address) if address else {}
            return build_token_dict(pool, security)

        results = await asyncio.gather(*[process(p) for p in pools])
        tokens  = [r for r in results if r is not None]

    tokens.sort(key=lambda t: t["score"], reverse=True)
    tokens = tokens[:10]

    _set_cache("tokens", tokens)
    return {"source": "live", "tokens": tokens}


@router.get("/track/{address}")
async def track_token(address: str):
    """
    Track any Solana token by mint address.
    Strategy:
      1. Check cache
      2. Fetch GoPlus security data
      3. Try GeckoTerminal new_pools (last 24h)
      4. If not found → try GeckoTerminal token-specific pools endpoint
      5. If still not found → fall back to DexScreener
      6. Compute score from whatever pool data we have
    """
    cache_key = f"track_{address}"
    cached    = _get_cache(cache_key)
    if cached:
        return {"source": "cache", **cached}

    async with httpx.AsyncClient() as client:
        # Always fetch security data first
        security = await fetch_security(client, address)

        # 1. Search GeckoTerminal new pools
        pools      = await fetch_new_pools(client)
        pool_attrs = {}
        pool_address = ""

        for pool in pools:
            token_id = (
                pool.get("relationships", {})
                    .get("base_token", {})
                    .get("data", {})
                    .get("id", "")
            )
            addr = token_id.replace("solana_", "") if "solana_" in token_id else token_id
            if addr.lower() == address.lower():
                pool_attrs   = pool.get("attributes", {})
                pool_address = pool_attrs.get("address", "")
        break

            # 2. Not in new pools → try GeckoTerminal token-specific pools
            if not pool_attrs:
            token_pools = await fetch_pools_for_token(client, address)
            if token_pools:
                # Use the most liquid pool
                token_pools.sort(
                    key=lambda p: safe_float(p.get("attributes", {}).get("reserve_in_usd")),
                    reverse=True,
                )
                pool_attrs   = token_pools[0].get("attributes", {})
                pool_address = pool_attrs.get("address", "")

        # 3. Still nothing → fall back to DexScreener
        dex_source = False
        if not pool_attrs:
            dex_pair   = await fetch_dexscreener(client, address)
            pool_attrs = dex_pair_to_attrs(dex_pair)
            pool_address = pool_attrs.get("address", "")
            dex_source = bool(pool_attrs)

    score_data = compute_score(pool_attrs, security)

    # Get token name
    name = pool_attrs.get("name", "Unknown Token")
    if " / " in name:
        name = name.split(" / ")[0].strip()

    dex_link = (
        f"https://dexscreener.com/solana/{pool_address}"
        if pool_address else
        f"https://dexscreener.com/solana/{address}"
    )

    result = {
        "address":         address,
        "name":            name,
        "fdv":             round(safe_float(pool_attrs.get("fdv_usd")), 2),
        "liquidity":       round(safe_float(pool_attrs.get("reserve_in_usd")), 2),
        "volume_1h":       round(safe_float(pool_attrs.get("volume_usd", {}).get("h1")), 2),
        "price_usd":       str(pool_attrs.get("base_token_price_usd", "0") or "0"),
        "price_change_1h": round(safe_float(pool_attrs.get("price_change_percentage", {}).get("h1")), 2),
        "score":           score_data["score"],
        "breakdown":       score_data["breakdown"],
        "security_flags":  score_data["security_flags"],
        "age_hours":       score_data["age_hours"],
        "dex_link":        dex_link,
        "data_source":     "dexscreener" if dex_source else "geckoterminal",
    }

    _set_cache(cache_key, result)
    return {"source": "live", **result}
