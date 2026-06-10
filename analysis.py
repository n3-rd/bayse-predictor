import logging
from typing import Dict, Any, List, Optional
import config

logger = logging.getLogger("BayseBot.Analysis")

class ProbabilityDeviationStrategy:
    def __init__(self, min_edge: float = config.MIN_EDGE_PCT):
        self.min_edge = min_edge

    def generate_signals(self, event_id: str, market_id: str, outcome_yes_id: str, outcome_no_id: str,
                         true_probability: float, best_bid: Optional[float], best_ask: Optional[float], 
                         currency: str = "NGN") -> List[Dict[str, Any]]:
        """
        Calculates mathematical edge against current market quotes.
        Returns a list of proposed trade signals.
        """
        signals = []
        if best_bid is None or best_ask is None:
            return signals

        # Implied probabilities from book:
        # Ask represents the cost to BUY YES, Bid represents what we can SELL YES for.
        # Bayse binary markets pricing maps directly to probabilities (0.01 - 0.99)
        # NGN uses 100x multiplier, meaning price is 1.0 to 99.0 NGN. Let's normalize it to 0.0-1.0
        multiplier = 100.0 if currency == "NGN" else 1.0
        norm_bid = best_bid / multiplier
        norm_ask = best_ask / multiplier

        # Edge analysis for YES
        # If true_probability is higher than ask, YES is undervalued. Buy YES.
        yes_edge = true_probability - norm_ask
        # If true_probability is lower than bid, YES is overvalued. Sell YES (or Buy NO).
        no_edge = (1.0 - true_probability) - (1.0 - norm_bid)

        logger.debug(f"True Prob: {true_probability:.2f}, Market Bid/Ask: {norm_bid:.2f}/{norm_ask:.2f}, YES Edge: {yes_edge:.2%}, NO Edge: {no_edge:.2%}")

        if yes_edge >= self.min_edge:
            signals.append({
                "eventId": event_id,
                "marketId": market_id,
                "outcomeId": outcome_yes_id,
                "side": "BUY",
                "price": best_ask,  # Cross spread to hit the ask
                "amount": 2000.0,   # raw size (RiskMeter will truncate if necessary)
                "type": "LIMIT",
                "reason": f"YES undervalued: True prob {true_probability:.2f} > market ask {norm_ask:.2f}"
            })
        elif no_edge >= self.min_edge:
            # Undervalued NO (or overvalued YES)
            signals.append({
                "eventId": event_id,
                "marketId": market_id,
                "outcomeId": outcome_no_id,
                "side": "BUY",
                "price": (1.0 - norm_bid) * multiplier,  # Buying NO costs (1 - YES_bid)
                "amount": 2000.0,
                "type": "LIMIT",
                "reason": f"NO undervalued: True prob {1.0 - true_probability:.2f} > market ask {1.0 - norm_bid:.2f}"
            })

        return signals


class MarketMakerStrategy:
    def __init__(self, spread_pct: float = 0.02, inventory_limit: float = 100.0):
        self.spread_pct = spread_pct
        self.inventory_limit = inventory_limit

    def generate_quotes(self, event_id: str, market_id: str, outcome_yes_id: str, outcome_no_id: str,
                        yes_inventory: float, best_bid: Optional[float], best_ask: Optional[float],
                        currency: str = "NGN") -> List[Dict[str, Any]]:
        """
        Generates bid and ask quotes centered around mid-price, skewed by inventory to maintain delta neutrality.
        """
        signals = []
        if best_bid is None or best_ask is None:
            return signals

        multiplier = 100.0 if currency == "NGN" else 1.0
        mid_price = (best_bid + best_ask) / 2.0
        
        # Calculate skew based on inventory. 
        # If we have too many YES shares (yes_inventory > 0), we want to lower our bid (buy less YES) 
        # and lower our ask (sell more YES) to encourage inventory reduction.
        # Skew parameter shifts quotes downward.
        skew = (yes_inventory / self.inventory_limit) * (0.05 * multiplier)
        skewed_mid = mid_price - skew

        half_spread = (self.spread_pct * multiplier) / 2.0
        
        target_bid = round(skewed_mid - half_spread, 2)
        target_ask = round(skewed_mid + half_spread, 2)

        # Bounds checks (prices must be between 0.01 and 0.99 normalized)
        min_price = 0.02 * multiplier
        max_price = 0.98 * multiplier
        
        target_bid = max(min_price, min(target_bid, max_price))
        target_ask = max(min_price, min(target_ask, max_price))

        # We quote bid for YES, and ask for YES
        # Quote Bid (Buying YES)
        signals.append({
            "eventId": event_id,
            "marketId": market_id,
            "outcomeId": outcome_yes_id,
            "side": "BUY",
            "price": target_bid,
            "amount": 1000.0,
            "type": "LIMIT",
            "postOnly": True,
            "reason": f"MM Bid Quote. Skewed Mid: {skewed_mid/multiplier:.2f}"
        })

        # Quote Ask (Selling YES) - In Bayse, selling YES can be done via placing a SELL order on YES
        signals.append({
            "eventId": event_id,
            "marketId": market_id,
            "outcomeId": outcome_yes_id,
            "side": "SELL",
            "price": target_ask,
            "amount": 1000.0,
            "type": "LIMIT",
            "postOnly": True,
            "reason": f"MM Ask Quote. Skewed Mid: {skewed_mid/multiplier:.2f}"
        })

        return signals
