import asyncio
import pandas as pd
import numpy as np
import logging
from backtester import OfflineBacktester
from analysis import ProbabilityDeviationStrategy

logging.basicConfig(level=logging.INFO)

async def test_backtest():
    print("=== Running Offline Backtester Test ===")
    backtester = OfflineBacktester()
    strategy = ProbabilityDeviationStrategy(min_edge=0.03)
    
    # 1. Fetch data (will generate mock data since REST call fails offline/without active live event)
    df = await backtester.fetch_historical_data(event_id="test_event")
    
    # Generate mock model probability series (oscillating around actual price with some edge anomalies)
    np.random.seed(100)
    true_probs = np.clip(df["price"] + np.random.normal(0, 0.08, len(df)), 0.1, 0.9)
    true_prob_series = pd.Series(true_probs)
    
    # 2. Run Backtest
    result = backtester.run_backtest(df, strategy, true_prob_series)
    
    print("\nBacktest Results:")
    print(f"Initial Capital: {result['initial_capital']:.2f} NGN")
    print(f"Final Value:     {result['final_portfolio_value']:.2f} NGN")
    print(f"Total Return:    {result['total_return_pct']:.2f}%")
    print(f"Total Trades:    {result['number_of_trades']}")
    print("=======================================")

if __name__ == "__main__":
    import unittest
    # Run unittest suite first
    suite = unittest.TestLoader().loadTestsFromName('test_bot')
    runner = unittest.TextTestRunner(verbosity=2)
    unittest_result = runner.run(suite)
    
    if unittest_result.wasSuccessful():
        # Run Backtester test
        asyncio.run(test_backtest())
    else:
        print("Unit tests failed! Skipping backtest run.")
