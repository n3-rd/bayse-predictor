import unittest
from auth import generate_signature, get_auth_headers
import config

class TestBayseBot(unittest.TestCase):
    def test_signature_generation(self):
        # Sample values to verify deterministic cryptographic signature
        secret_key = "sk_live_testkey"
        public_key = "pk_live_testkey"
        method = "POST"
        path = "/v1/pm/events/123/markets/456/orders"
        timestamp = 1625097600
        body_str = '{"side":"BUY","amount":100}'
        
        sig = generate_signature(secret_key, method, path, timestamp, body_str)
        self.assertIsNotNone(sig)
        self.assertTrue(len(sig) > 10)
        
        headers = get_auth_headers(public_key, secret_key, method, path, body_str, timestamp=timestamp)
        self.assertEqual(headers["X-Timestamp"], str(timestamp))
        self.assertEqual(headers["X-Public-Key"], public_key)
        self.assertEqual(headers["X-Signature"], sig)

    def test_signature_empty_body(self):
        secret_key = "sk_live_testkey"
        method = "DELETE"
        path = "/v1/pm/orders/abc"
        timestamp = 1625097600
        
        sig = generate_signature(secret_key, method, path, timestamp, "")
        self.assertIsNotNone(sig)

    def test_extract_asset_symbol(self):
        from price_feed import extract_asset_symbol
        self.assertEqual(extract_asset_symbol({"assetSymbolPair": "BTCUSDT"}), "BTCUSDT")
        self.assertEqual(extract_asset_symbol({"title": "Bitcoin Up or Down - 15 minutes?", "category": "CRYPTO"}), "BTC")
        self.assertEqual(extract_asset_symbol({"slug": "sol-price-prediction", "category": "CRYPTO"}), "SOL")
        self.assertEqual(extract_asset_symbol({"title": "Who will win the election?", "category": "POLITICS"}), None)

    def test_estimate_binary_probability(self):
        from price_feed import estimate_binary_probability
        # At the threshold, probability should be around 50%
        prob = estimate_binary_probability(current_price=100.0, threshold=100.0, time_remaining_seconds=3600.0, volatility=0.50)
        self.assertAlmostEqual(prob, 0.5, places=1)
        
        # Well above threshold, probability should be high
        prob_high = estimate_binary_probability(current_price=120.0, threshold=100.0, time_remaining_seconds=3600.0, volatility=0.50)
        self.assertTrue(prob_high > 0.8)
        
        # Well below threshold, probability should be low
        prob_low = estimate_binary_probability(current_price=80.0, threshold=100.0, time_remaining_seconds=3600.0, volatility=0.50)
        self.assertTrue(prob_low < 0.2)

    def test_bitcoin_category_filtering(self):
        # Test that _discover_active_markets properly filters out non-crypto and non-BTC events
        from main import BaysePredictorBot
        import asyncio
        
        bot = BaysePredictorBot()
        # Mock request method
        async def mock_request(*args, **kwargs):
            return {
                "events": [
                    {
                        "id": "event-1",
                        "category": "finance",
                        "title": "USD/NGN Exchange Rate",
                        "closingDate": "2026-06-12T14:34:47Z",
                        "markets": [{"id": "market-1", "outcome1Label": "YES", "outcome1Id": "yes-1", "outcome2Label": "NO", "outcome2Id": "no-1"}]
                    },
                    {
                        "id": "event-2",
                        "category": "crypto",
                        "title": "Ethereum closing price",
                        "slug": "eth-price-prediction",
                        "closingDate": "2026-06-12T14:34:47Z",
                        "markets": [{"id": "market-2", "outcome1Label": "YES", "outcome1Id": "yes-2", "outcome2Label": "NO", "outcome2Id": "no-2"}]
                    },
                    {
                        "id": "event-3",
                        "category": "crypto",
                        "title": "Bitcoin closing price",
                        "slug": "btc-price-prediction",
                        "closingDate": "2026-06-12T14:34:47Z",
                        "markets": [{"id": "market-3", "outcome1Label": "YES", "outcome1Id": "yes-3", "outcome2Label": "NO", "outcome2Id": "no-3"}]
                    }
                ]
            }
        bot.exec_layer.request = mock_request
        
        discovered = asyncio.run(bot._discover_active_markets())
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0]["eventId"], "event-3")
        self.assertEqual(discovered[0]["marketId"], "market-3")

    def test_ml_predictor_cold_start_fallback(self):
        # Verify that if the ML predictor is not trained, it falls back to Black-Scholes math formula
        from main import BaysePredictorBot
        import asyncio
        
        bot = BaysePredictorBot()
        self.assertFalse(bot.ml.is_trained)
        
        # Call estimate_probability on PriceFeedClient
        prob, model = asyncio.run(bot.price_feed.estimate_probability(
            symbol="BTC",
            current_price=65000.0,
            threshold=65000.0,
            time_remaining_seconds=3600.0,
            volatility=0.50
        ))
        self.assertEqual(model, "Black-Scholes (Normal CDF)")
        self.assertAlmostEqual(prob, 0.5, places=1)

    def test_copy_trader_filtration_and_latency(self):
        import asyncio
        import time
        from copy_strategy import BayseCopyTrader
        
        # Mock dependencies
        class MockExecutionLayer:
            def __init__(self):
                self.dry_run = True
                self.orders = []
            async def create_order(self, **kwargs):
                self.orders.append(kwargs)
                return {"status": "success", "orderId": "sim-123"}
            async def request(self, *args, **kwargs):
                return {"assets": [{"currency": "NGN", "balance": 10000.0, "available": 10000.0}]}
                
        class MockObserver:
            def __init__(self):
                self.positions = {}
                self.orderbooks = {}
                
        class MockRiskMeter:
            def __init__(self):
                self.starting_daily_equity = 10000.0
            async def audit_order(self, **kwargs):
                return True, kwargs.get("amount")

        exec_layer = MockExecutionLayer()
        observer = MockObserver()
        risk_meter = MockRiskMeter()
        
        # Mock config.TARGET_TRADERS for the test
        old_traders = config.TARGET_TRADERS
        config.TARGET_TRADERS = ["target-user-uuid-1"]
        
        copy_trader = BayseCopyTrader(exec_layer, observer, risk_meter)
        
        try:
            # Test 1: Non-target trader should be ignored
            event_ignored = {
                "userId": "some-other-user",
                "eventId": "event-1",
                "marketId": "market-1",
                "outcomeId": "yes-1",
                "side": "BUY",
                "price": 0.5,
                "amount": 100.0,
                "timestamp": time.time()
            }
            asyncio.run(copy_trader.process_public_trade(event_ignored))
            self.assertEqual(len(exec_layer.orders), 0)
            
            # Test 2: Target trader with high latency should be ignored
            event_latency = {
                "userId": "target-user-uuid-1",
                "eventId": "event-1",
                "marketId": "market-1",
                "outcomeId": "yes-1",
                "side": "BUY",
                "price": 0.5,
                "amount": 100.0,
                "timestamp": time.time() - 5.0 # 5 seconds delay
            }
            asyncio.run(copy_trader.process_public_trade(event_latency))
            self.assertEqual(len(exec_layer.orders), 0)
            
            # Test 3: Target trader valid trade should be copied
            event_valid = {
                "userId": "target-user-uuid-1",
                "eventId": "event-1",
                "marketId": "market-1",
                "outcomeId": "yes-1",
                "side": "BUY",
                "price": 0.5,
                "amount": 100.0,
                "timestamp": time.time()
            }
            asyncio.run(copy_trader.process_public_trade(event_valid))
            self.assertEqual(len(exec_layer.orders), 1)
            self.assertEqual(exec_layer.orders[0]["time_in_force"], "FAK")
            self.assertEqual(exec_layer.orders[0]["outcome_id"], "yes-1")
        finally:
            config.TARGET_TRADERS = old_traders

if __name__ == "__main__":
    unittest.main()

