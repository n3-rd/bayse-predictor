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

if __name__ == "__main__":
    unittest.main()
