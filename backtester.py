import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Any
import aiohttp
import config

logger = logging.getLogger("BayseBot.Backtester")

class OfflineBacktester:
    def __init__(self, fee_pct: float = 0.005, default_slippage: float = 0.01):
        self.fee_pct = fee_pct
        self.default_slippage = default_slippage

    async def fetch_historical_data(self, event_id: str) -> pd.DataFrame:
        """
        Fetches price history from the Bayse REST API endpoint.
        """
        url = f"{config.BASE_REST_URL}/v1/pm/events/{event_id}/price-history"
        logger.info(f"Fetching historical price data from {url}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Assume data format: {"history": [{"timestamp": int, "price": float, "volume": float}]}
                        history = data.get("history", [])
                        if history:
                            df = pd.DataFrame(history)
                            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
                            return df
        except Exception as e:
            logger.warning(f"Failed to fetch real price history: {e}. Generating mock backtest dataframe.")
            
        # Fallback to generating mock historical series for testing backtester integrity
        dates = pd.date_range(start="2026-06-01", periods=100, freq="h")
        np.random.seed(42)
        prices = np.clip(np.cumsum(np.random.normal(0, 0.03, len(dates))) + 0.5, 0.1, 0.9)
        volumes = np.random.exponential(1000, len(dates))
        df = pd.DataFrame({"timestamp": dates, "price": prices, "volume": volumes})
        return df

    def run_backtest(self, df: pd.DataFrame, strategy, true_prob_series: pd.Series) -> Dict[str, Any]:
        """
        Executes offline backtesting of a strategy.
        true_prob_series represents the model's computed probability corresponding to each timestamp.
        """
        if len(df) != len(true_prob_series):
            # Align lengths
            min_len = min(len(df), len(true_prob_series))
            df = df.iloc[:min_len].copy()
            true_prob_series = true_prob_series.iloc[:min_len]

        df["true_prob"] = true_prob_series.values
        
        capital = 100000.0  # Starting capital in NGN
        initial_capital = capital
        position = 0.0      # Number of YES shares held
        trades_log = []
        
        for idx, row in df.iterrows():
            market_price = row["price"] * 100.0  # NGN price
            true_prob = row["true_prob"]
            volume = row["volume"]
            
            # Simple simulation of best bid/ask around the transaction price
            best_bid = market_price - 0.5
            best_ask = market_price + 0.5
            
            # Run strategy signals
            signals = strategy.generate_signals(
                event_id="backtest_event",
                market_id="backtest_market",
                outcome_yes_id="yes_id",
                outcome_no_id="no_id",
                true_probability=true_prob,
                best_bid=best_bid,
                best_ask=best_ask,
                currency="NGN"
            )
            
            for sig in signals:
                side = sig["side"]
                price = sig["price"]
                amount = sig["amount"]
                
                # Check 2% risk limit
                max_risk = capital * config.MAX_POSITION_SIZE_PCT
                trade_amount = min(amount, max_risk)
                
                # Apply slippage penalty if volume is low
                slippage_penalty = 0.0
                if volume < 500:
                    slippage_penalty = self.default_slippage * price
                    
                if side == "BUY" and sig["outcomeId"] == "yes_id":
                    execution_price = price + slippage_penalty
                    shares_to_buy = trade_amount / execution_price
                    cost = trade_amount + (trade_amount * self.fee_pct)
                    
                    if capital >= cost:
                        capital -= cost
                        position += shares_to_buy
                        trades_log.append({
                            "timestamp": row["timestamp"],
                            "side": "BUY",
                            "price": execution_price,
                            "shares": shares_to_buy,
                            "capital": capital
                        })
                        
                elif side == "BUY" and sig["outcomeId"] == "no_id":
                    # Buying NO is equivalent to selling YES (shorting or unwinding)
                    execution_price = price - slippage_penalty
                    if position > 0:
                        shares_to_sell = min(position, trade_amount / execution_price)
                        revenue = shares_to_sell * execution_price
                        fee = revenue * self.fee_pct
                        capital += (revenue - fee)
                        position -= shares_to_sell
                        trades_log.append({
                            "timestamp": row["timestamp"],
                            "side": "SELL",
                            "price": execution_price,
                            "shares": shares_to_sell,
                            "capital": capital
                        })

        # Final portfolio valuation
        final_price = df.iloc[-1]["price"] * 100.0
        portfolio_value = capital + (position * final_price)
        total_return = (portfolio_value - initial_capital) / initial_capital
        
        return {
            "initial_capital": initial_capital,
            "final_portfolio_value": portfolio_value,
            "total_return_pct": total_return * 100.0,
            "number_of_trades": len(trades_log),
            "trades": trades_log
        }
