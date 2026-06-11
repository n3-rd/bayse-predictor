import math
import time
import logging
import aiohttp
from typing import Optional, Dict
from datetime import datetime, timezone

logger = logging.getLogger("BayseBot.PriceFeed")

def parse_iso_datetime(dt_str: str) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None

def extract_asset_symbol(market_or_event: dict) -> Optional[str]:
    symbol = market_or_event.get("assetSymbolPair")
    if symbol:
        return symbol
    
    title = market_or_event.get("title", "").upper()
    slug = market_or_event.get("slug", "").upper()
    category = market_or_event.get("category", "").upper()
    
    for word in ["BTC", "BITCOIN"]:
        if word in title or word in slug:
            return "BTC"
    for word in ["ETH", "ETHEREUM"]:
        if word in title or word in slug:
            return "ETH"
    for word in ["SOL", "SOLANA"]:
        if word in title or word in slug:
            return "SOL"
    for word in ["GOLD", "XAU", "PAXG"]:
        if word in title or word in slug:
            return "GOLD"
    for word in ["NGN", "NAIRA", "USDNGN", "USD/NGN"]:
        if word in title or word in slug:
            return "USDNGN"
    for word in ["EURUSD", "EUR/USD", "EUR"]:
        if word in title or word in slug:
            return "EURUSD"
    for word in ["GBPUSD", "GBP/USD", "GBP"]:
        if word in title or word in slug:
            return "GBPUSD"
            
    if category == "CRYPTO":
        return "BTC"
    elif category == "FINANCE":
        return "GOLD"
        
    return None

def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def estimate_binary_probability(current_price: float, threshold: float, time_remaining_seconds: float, volatility: float = 0.50) -> float:
    if time_remaining_seconds <= 0:
        return 1.0 if current_price >= threshold else 0.0
    
    t = time_remaining_seconds / (365.0 * 24.0 * 3600.0)
    if volatility <= 0:
        volatility = 0.01
        
    try:
        d2 = (math.log(current_price / threshold) - (volatility ** 2) * t / 2.0) / (volatility * math.sqrt(t))
        prob = normal_cdf(d2)
        return max(0.01, min(prob, 0.99))
    except Exception:
        return 0.5

class PriceFeedClient:
    def __init__(self, cache_ttl_seconds: float = 60.0, ml_predictor=None, db_manager=None):
        self.cache_ttl = cache_ttl_seconds
        self.price_cache: Dict[str, tuple[float, float]] = {}  # symbol -> (price, timestamp)
        self.session: Optional[aiohttp.ClientSession] = None
        self.ml_predictor = ml_predictor
        self.db_manager = db_manager

    async def estimate_probability(self, symbol: str, current_price: float, threshold: float, 
                                   time_remaining_seconds: float, volatility: float = 0.50) -> tuple[float, str]:
        """
        Estimates the probability using the ML model if trained and the symbol is Bitcoin.
        Otherwise, falls back to the mathematical Black-Scholes variation.
        Returns a tuple of (probability, model_name).
        """
        sym = symbol.upper()
        if sym in ["BTC", "BTCUSDT", "BTCUSD"] and self.ml_predictor and self.ml_predictor.is_trained:
            try:
                # Fetch recent prices from the evaluations logged in the DB
                recent_evals = await self.db_manager.fetch_recent_evaluations(sym, limit=20)
                recent_prices = [float(e["spot_price"]) for e in recent_evals]
                # Reverse to keep chronological order (fetch_recent_evaluations returns DESC)
                recent_prices.reverse()
                
                # Predict using ML model
                prob = self.ml_predictor.predict_probability(current_price, threshold, time_remaining_seconds, recent_prices)
                model_name = type(self.ml_predictor.model).__name__
                return prob, f"ML ({model_name})"
            except Exception as e:
                logger.error(f"Failed to estimate probability using ML model: {e}. Falling back to Black-Scholes.")
                
        # Fallback to Black-Scholes mathematical CDF
        prob = estimate_binary_probability(current_price, threshold, time_remaining_seconds, volatility)
        return prob, "Black-Scholes (Normal CDF)"

    async def get_price(self, asset_symbol: str) -> Optional[float]:
        symbol = asset_symbol.upper().replace("/", "").replace("-", "")
        
        now = time.time()
        if symbol in self.price_cache:
            price, ts = self.price_cache[symbol]
            if now - ts < self.cache_ttl:
                return price
                
        if not self.session:
            self.session = aiohttp.ClientSession()

        price = None
        try:
            if symbol in ["BTC", "BTCUSDT", "BTCUSD"]:
                price = await self._fetch_coingecko("bitcoin")
            elif symbol in ["ETH", "ETHUSDT", "ETHUSD"]:
                price = await self._fetch_coingecko("ethereum")
            elif symbol in ["SOL", "SOLUSDT", "SOLUSD"]:
                price = await self._fetch_coingecko("solana")
            elif symbol in ["GOLD", "XAU", "PAXG"]:
                price = await self._fetch_coingecko("pax-gold")
            elif symbol in ["USDNGN", "NGN"]:
                price = await self._fetch_er_api("NGN", base="USD")
            elif symbol in ["EURUSD", "EUR"]:
                eur_per_usd = await self._fetch_er_api("EUR", base="USD")
                if eur_per_usd:
                    price = 1.0 / eur_per_usd
            elif symbol in ["GBPUSD", "GBP"]:
                gbp_per_usd = await self._fetch_er_api("GBP", base="USD")
                if gbp_per_usd:
                    price = 1.0 / gbp_per_usd
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")

        if price is not None:
            self.price_cache[symbol] = (price, now)
            return price
            
        if symbol in self.price_cache:
            return self.price_cache[symbol][0]
            
        return None

    async def _fetch_coingecko(self, coin_id: str) -> Optional[float]:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
        async with self.session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return float(data[coin_id]["usd"])
        return None

    async def _fetch_er_api(self, target_currency: str, base: str = "USD") -> Optional[float]:
        url = f"https://open.er-api.com/v6/latest/{base}"
        async with self.session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                rates = data.get("rates", {})
                if target_currency in rates:
                    return float(rates[target_currency])
        return None

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
