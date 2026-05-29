import asyncio
import time
from typing import Optional
import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter()

# ── In-memory cache ──────────────────────────────────────────────────────────
_cache: dict = {}
CACHE_TTL = 60  # seconds

def _get_cache(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None

def _set_cache(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


# ── External API helpers ──────────────────────────────────────────────────────
GECKO_URL = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"
GOPLUS_URL = "https://api.gopluslabs.io/api/v1/solana/token_security"

HEADERS = {"Accept": "application/json"}
TIMEOUT = 10.0


async def fetch_new_pools(client: httpx.AsyncClient) -> list:
    try:
        r = await client.get(
            GECKO_URL,
            params={"page": 1},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception:
        return []


async def fetch_security(client: httpx.AsyncClient, address: str) -> dict:
    try:
        r = await client.get(
            GOPLUS_URL,
            params={"contract_addresses": address},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        # GoPlus returns a dict keyed by lowercased address
        return result.get(address.lower(), result.get(address, {}))
    except Exception:
        return {}


# ── Scoring ───────────────────────────────────────────────────────────────────
def safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def safe_int(val, default=0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def compute_score(pool_attrs: dict, security: dict) -> dict:
    volume_1h  = safe_float(pool_attrs.get("volume_usd", {}).get("h1"))
    volume_6h  = safe_float(pool_attrs.get("volume_usd", {}).get("h6"))
    liquidity  = safe_float(pool_attrs.get("reserve_in_usd"))
    fdv        = safe_float(pool_attrs.get("fdv_usd"))
    price_pct  = safe_float(pool_attrs.get("price_change_percentage", {}).get("h1"))
    txns_buy   = safe_int(pool_attrs.get("transactions", {}).get("h1", {}).get("buys"))
    txns_sell  = safe_int(pool_attrs.get("transactions", {}).get("h1", {}).get("sells"))

    # Token age in hours
    created_at = pool_attrs.get("pool_created_at", "")
    age_hours = 999.0
    if created_at:
        try:
            import datetime
            created = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            now = datetime.datetime.now(datetime.timezone.utc)
            age_hours = (now - created).total_seconds() / 3600
        except Exception:
            pass

    # Security fields
    is_honeypot    = safe_int(security.get("is_honeypot")) == 1
    is_mintable    = safe_int(security.get("is_mintable")) == 1
    freeze_auth    = security.get("freeze_authority") not in (None, "", "0", 0)
    owner_renounce = safe_int(security.get("owner_address") in ("", None, "0x0000000000000000000000000000000000000000"))

    # Holder concentration (top10 holders %)
    top10_pct = safe_float(security.get("top_10_holder_percent", 0)) * 100
    # GoPlus sometimes returns as fraction 0-1, sometimes 0-100
    if top10_pct <= 1.0:
        top10_pct *= 100

    # ── Base scores ──────────────────────────────────────────────────────────
    # Volume momentum (25pts)
    if volume_1h >= 100_000:
        vol_score = 25
    elif volume_1h >= 50_000:
        vol_score = 20
    elif volume_1h >= 20_000:
        vol_score = 15
    elif volume_1h >= 5_000:
        vol_score = 10
    else:
        vol_score = 0

    # Buy pressure / tx momentum (20pts)
    total_txns = txns_buy + txns_sell
    if total_txns > 0:
        buy_ratio = txns_buy / total_txns
    else:
        buy_ratio = 0.5
    tx_score = round(buy_ratio * 20)

    # Liquidity strength (20pts)
    if liquidity >= 50_000:
        liq_score = 20
    elif liquidity >= 20_000:
        liq_score = 15
    elif liquidity >= 10_000:
        liq_score = 10
    elif liquidity >= 5_000:
        liq_score = 5
    else:
        liq_score = 0

    # Price momentum (15pts)
    if price_pct >= 20:
        price_score = 15
    elif price_pct >= 10:
        price_score = 12
    elif price_pct >= 5:
        price_score = 8
    elif price_pct >= 0:
        price_score = 5
    else:
        price_score = 0

    # Token age freshness (10pts) – fresher is better
    if age_hours <= 1:
        age_score = 10
    elif age_hours <= 6:
        age_score = 8
    elif age_hours <= 12:
        age_score = 5
    elif age_hours <= 24:
        age_score = 2
    else:
        age_score = 0

    # Low FDV attractiveness (10pts)
    if fdv <= 5_000:
        fdv_score = 10
    elif fdv <= 10_000:
        fdv_score = 8
    elif fdv <= 15_000:
        fdv_score = 5
    elif fdv <= 20_000:
        fdv_score = 2
    else:
        fdv_score = 0

    base = vol_score + tx_score + liq_score + price_score + age_score + fdv_score

    # ── Penalties ────────────────────────────────────────────────────────────
    penalties = 0
    penalty_flags = []

    if is_mintable:
        penalties += 30
        penalty_flags.append("Mintable")
    if freeze_auth:
        penalties += 20
        penalty_flags.append("Freeze Authority")
    if not owner_renounce and security:
        penalties += 20
        penalty_flags.append("Ownership Not Renounced")
    if top10_pct >= 80:
        penalties += 20
        penalty_flags.append("High Holder Concentration")

    final_score = max(0, min(100, base - penalties))

    return {
        "score": final_score,
        "breakdown": {
            "volume_momentum": vol_score,
            "buy_pressure": tx_score,
            "liquidity_strength": liq_score,
            "price_momentum": price_score,
            "age_freshness": age_score,
            "fdv_attractiveness": fdv_score,
            "penalties": -penalties,
        },
        "security_flags": {
            "is_honeypot": is_honeypot,
            "is_mintable": is_mintable,
            "freeze_authority": freeze_auth,
            "owner_renounced": bool(owner_renounce),
            "top10_holder_pct": round(top10_pct, 1),
            "penalty_flags": penalty_flags,
        },
        "age_hours": round(age_hours, 1),
    }


# ── Filtering ─────────────────────────────────────────────────────────────────
def passes_filter(pool_attrs: dict, score_data: dict) -> bool:
    fdv       = safe_float(pool_attrs.get("fdv_usd"))
    liquidity = safe_float(pool_attrs.get("reserve_in_usd"))
    volume_1h = safe_float(pool_attrs.get("volume_usd", {}).get("h1"))
    age_hours = score_data["age_hours"]
    is_honeypot = score_data["security_flags"]["is_honeypot"]

    if fdv > 20_000:         return False
    if age_hours > 24:       return False
    if volume_1h < 5_000:    return False
    if liquidity < 5_000:    return False
    if is_honeypot:          return False
    if score_data["security_flags"]["is_mintable"]: return False
    if score_data["security_flags"]["top10_holder_pct"] >= 80: return False
    if score_data["score"] < 60:                    return False
    return True


# ── Token shape builder ───────────────────────────────────────────────────────
def build_token_dict(pool: dict, security: dict) -> Optional[dict]:
    attrs = pool.get("attributes", {})
    rels  = pool.get("relationships", {})

    base_token = rels.get("base_token", {}).get("data", {})
    token_id   = base_token.get("id", "")  # e.g. "solana_<address>"
    address    = token_id.replace("solana_", "") if "solana_" in token_id else token_id

    score_data = compute_score(attrs, security)

    if not passes_filter(attrs, score_data):
        return None

    name   = attrs.get("name", "Unknown")
    # name often "TOKEN / SOL" – extract token part
    if " / " in name:
        name = name.split(" / ")[0].strip()

    symbol = name  # GeckoTerminal often doesn't separate symbol; use name as fallback
    # Try to get symbol from included data if present
    dex_id = attrs.get("dex_id", "")
    pool_address = attrs.get("address", pool.get("id", "").replace("solana_", ""))

    dex_link = f"https://dexscreener.com/solana/{pool_address}" if pool_address else "#"

    return {
        "address": address,
        "pool_address": pool_address,
        "name": name,
        "symbol": symbol,
        "fdv": round(safe_float(attrs.get("fdv_usd")), 2),
        "liquidity": round(safe_float(attrs.get("reserve_in_usd")), 2),
        "volume_1h": round(safe_float(attrs.get("volume_usd", {}).get("h1")), 2),
        "price_usd": attrs.get("base_token_price_usd", "0"),
        "price_change_1h": round(safe_float(attrs.get("price_change_percentage", {}).get("h1")), 2),
        "score": score_data["score"],
        "breakdown": score_data["breakdown"],
        "security_flags": score_data["security_flags"],
        "age_hours": score_data["age_hours"],
        "dex_link": dex_link,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────
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

    # Batch security checks (limit concurrency)
    tokens = []
    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(5)

        async def process(pool):
            attrs = pool.get("attributes", {})
            # Quick pre-filter before security call
            fdv       = safe_float(attrs.get("fdv_usd"))
            liquidity = safe_float(attrs.get("reserve_in_usd"))
            volume_1h = safe_float(attrs.get("volume_usd", {}).get("h1"))
            if fdv > 20_000 or liquidity < 5_000 or volume_1h < 5_000:
                return None

            rels       = pool.get("relationships", {})
            token_id   = rels.get("base_token", {}).get("data", {}).get("id", "")
            address    = token_id.replace("solana_", "") if "solana_" in token_id else token_id

            async with sem:
                security = await fetch_security(client, address) if address else {}

            return build_token_dict(pool, security)

        results = await asyncio.gather(*[process(p) for p in pools])
        tokens = [r for r in results if r is not None]

    # Sort by score desc, top 10
    tokens.sort(key=lambda t: t["score"], reverse=True)
    tokens = tokens[:10]

    _set_cache("tokens", tokens)
    return {"source": "live", "tokens": tokens}


@router.get("/track/{address}")
async def track_token(address: str):
    cache_key = f"track_{address}"
    cached = _get_cache(cache_key)
    if cached:
        return {"source": "cache", **cached}

    async with httpx.AsyncClient() as client:
        # Fetch security data
        security = await fetch_security(client, address)

        # Try to find pool data for this token
        pools = await fetch_new_pools(client)

    # Find matching pool
    pool_attrs = {}
    pool_address = ""
    for pool in pools:
        rels     = pool.get("relationships", {})
        token_id = rels.get("base_token", {}).get("data", {}).get("id", "")
        addr     = token_id.replace("solana_", "") if "solana_" in token_id else token_id
        if addr.lower() == address.lower():
            pool_attrs = pool.get("attributes", {})
            pool_address = pool_attrs.get("address", "")
            break

    score_data = compute_score(pool_attrs, security)

    name = pool_attrs.get("name", "Unknown Token")
    if " / " in name:
        name = name.split(" / ")[0].strip()

    dex_link = f"https://dexscreener.com/solana/{pool_address}" if pool_address else \
               f"https://dexscreener.com/solana/{address}"

    result = {
        "address": address,
        "name": name,
        "fdv": round(safe_float(pool_attrs.get("fdv_usd")), 2),
        "liquidity": round(safe_float(pool_attrs.get("reserve_in_usd")), 2),
        "volume_1h": round(safe_float(pool_attrs.get("volume_usd", {}).get("h1")), 2),
        "price_usd": pool_attrs.get("base_token_price_usd", "N/A"),
        "price_change_1h": round(safe_float(pool_attrs.get("price_change_percentage", {}).get("h1")), 2),
        "score": score_data["score"],
        "breakdown": score_data["breakdown"],
        "security_flags": score_data["security_flags"],
        "age_hours": score_data["age_hours"],
        "dex_link": dex_link,
        "raw_security": security,
    }

    _set_cache(cache_key, result)
    return {"source": "live", **result}
