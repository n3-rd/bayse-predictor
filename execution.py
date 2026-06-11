import asyncio
import uuid
import logging
import time
from typing import Dict, Any, Optional
import aiohttp
from auth import get_auth_headers
import config

logger = logging.getLogger("BayseBot.Execution")

class ExecutionLayer:
    def __init__(self, public_key: str, secret_key: str, dry_run: bool = True):
        self.public_key = public_key
        self.secret_key = secret_key
        self.dry_run = dry_run
        self.session: Optional[aiohttp.ClientSession] = None
        self.observer: Optional[Any] = None
        
        # Track simulated (dry run) orders
        # key: order_id -> val: dict representing order
        self.dry_run_orders: Dict[str, Dict[str, Any]] = {}
        
    async def initialize(self):
        # Configure limit_per_host to prevent throttling
        # 20 writes/sec and 30 reads/sec. We'll bound it to 20 to be safe.
        connector = aiohttp.TCPConnector(limit_per_host=config.WRITE_RATE_LIMIT)
        self.session = aiohttp.ClientSession(connector=connector)
        logger.info(f"ExecutionLayer initialized (limit_per_host={config.WRITE_RATE_LIMIT}, DRY_RUN={self.dry_run})")
        
    async def close(self):
        if self.session:
            await self.session.close()
            logger.info("ExecutionLayer session closed")

    def _generate_trace_id(self) -> str:
        return f"{uuid.uuid4()}-{int(time.time())}"

    async def request(self, method: str, path: str, data: Optional[dict] = None, authenticated: bool = False) -> Dict[str, Any]:
        """
        Sends REST requests to the Bayse API with rate limit safety, trace headers, and 429 recovery.
        """
        if not self.session:
            raise RuntimeError("ExecutionLayer session not initialized. Call initialize() first.")
            
        url = f"{config.BASE_REST_URL}{path}"
        trace_id = self._generate_trace_id()
        
        # Prepare headers
        headers = {
            "x-trace-id": trace_id,
            "Accept": "application/json"
        }
        
        # Handle authentication if required
        body_str = ""
        if data is not None:
            import json
            # Ensure strict serialization (no extra spaces)
            body_str = json.dumps(data, separators=(',', ':'))
            
        if authenticated:
            auth_hdrs = get_auth_headers(self.public_key, self.secret_key, method, path, body_str)
            headers.update(auth_hdrs)
        else:
            headers["X-Public-Key"] = self.public_key
            if data:
                headers["Content-Type"] = "application/json"

        # Execution loop to handle retry-after
        while True:
            try:
                async with self.session.request(method, url, headers=headers, data=body_str if data else None) as resp:
                    if resp.status == 429:
                        # Extract retry header
                        retry_after = resp.headers.get("Retry-After")
                        if not retry_after:
                            try:
                                payload = await resp.json()
                                retry_after = payload.get("retryAfter", 1)
                            except Exception:
                                retry_after = 1
                        
                        logger.warning(f"HTTP 429 Rate Limited. Retrying after {retry_after} seconds (Trace-ID: {trace_id})")
                        await asyncio.sleep(float(retry_after))
                        continue
                        
                    if resp.status >= 400:
                        err_text = await resp.text()
                        logger.error(f"HTTP {resp.status} Error: {err_text} (Trace-ID: {trace_id})")
                        raise aiohttp.ClientResponseError(
                            request_info=resp.request_info,
                            history=resp.history,
                            status=resp.status,
                            message=err_text,
                            headers=resp.headers
                        )
                        
                    return await resp.json()
            except aiohttp.ClientError as e:
                logger.error(f"Network error during REST call: {e}")
                raise

    async def create_order(self, event_id: str, market_id: str, outcome_id: str, side: str, 
                           amount: float, price: Optional[float] = None, order_type: str = "LIMIT",
                           stp_mode: str = "CANCEL_OLDEST", max_slippage: Optional[float] = None, 
                           post_only: bool = False, currency: str = "NGN") -> Dict[str, Any]:
        """
        Submits an order. Respects DRY_RUN mode.
        """
        path = f"/v1/pm/events/{event_id}/markets/{market_id}/orders"
        
        payload = {
            "side": side.upper(),
            "outcomeId": outcome_id,
            "amount": amount,
            "currency": currency,
            "type": order_type.upper(),
            "stpMode": stp_mode
        }
        
        if price is not None:
            # The API always expects normalized price [0.01, 0.99]
            multiplier = 100.0 if currency == "NGN" else 1.0
            payload["price"] = price / multiplier
        if max_slippage is not None:
            payload["maxSlippage"] = max_slippage
        if post_only:
            payload["postOnly"] = post_only

        if self.dry_run:
            # Simulate placing order
            sim_order_id = f"sim-{uuid.uuid4()}"
            sim_order = {
                "id": sim_order_id,
                "eventId": event_id,
                "marketId": market_id,
                "outcomeId": outcome_id,
                "side": side.upper(),
                "amount": amount,
                "price": price,
                "type": order_type.upper(),
                "status": "OPEN",
                "timestamp": time.time(),
                "currency": currency
            }
            
            if market_id.startswith("sim-") or order_type.upper() == "MARKET":
                sim_order["status"] = "FILLED"
                self.dry_run_orders[sim_order_id] = sim_order
                logger.info(f"[DRY RUN] Order Created and Filled Instantly: {sim_order}")
                if self.observer is not None:
                    shares = amount / price if price else 0.0
                    self.observer.positions[outcome_id] = self.observer.positions.get(outcome_id, 0.0) + shares
                    logger.info(f"[DRY RUN POSITION UPDATE] Added {shares:.2f} shares for outcome {outcome_id}. New balance: {self.observer.positions[outcome_id]:.2f}")
            else:
                self.dry_run_orders[sim_order_id] = sim_order
                logger.info(f"[DRY RUN] Order Created: {sim_order}")
                
            return {"status": "success", "orderId": sim_order_id, "order": sim_order}
        else:
            return await self.request("POST", path, data=payload, authenticated=True)

    async def cancel_order(self, event_id: str, market_id: str, order_id: str) -> Dict[str, Any]:
        """
        Cancels an order. Respects DRY_RUN mode.
        """
        if self.dry_run:
            if order_id in self.dry_run_orders:
                self.dry_run_orders[order_id]["status"] = "CANCELLED"
                logger.info(f"[DRY RUN] Order Cancelled: {order_id}")
                return {"status": "success", "orderId": order_id}
            else:
                logger.error(f"[DRY RUN] Cancel failed: Order {order_id} not found")
                return {"status": "error", "message": "Order not found"}
        else:
            path = f"/v1/pm/events/{event_id}/markets/{market_id}/orders/{order_id}"
            return await self.request("DELETE", path, authenticated=True)

    async def cancel_all_orders(self) -> Dict[str, Any]:
        """
        Cancels all open orders (batch DELETE /v1/pm/orders).
        """
        if self.dry_run:
            for oid in self.dry_run_orders:
                if self.dry_run_orders[oid]["status"] == "OPEN":
                    self.dry_run_orders[oid]["status"] = "CANCELLED"
            logger.info("[DRY RUN] Cancelled all open orders")
            return {"status": "success"}
        else:
            path = "/v1/pm/orders"
            return await self.request("DELETE", path, authenticated=True)

    async def burn_shares(self, market_id: str) -> Dict[str, Any]:
        """
        Surrenders paired YES/NO shares for liquid capital.
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Burn shares request completed for market: {market_id}")
            return {"status": "success"}
        else:
            path = f"/v1/pm/markets/{market_id}/burn"
            return await self.request("POST", path, authenticated=True)

    def process_dry_run_book_update(self, market_id: str, best_bid: Optional[float], best_ask: Optional[float]):
        """
        Monitors live WebSocket orderbook updates to simulate executions of open dry-run limit orders.
        """
        if not self.dry_run:
            return
            
        for order_id, order in list(self.dry_run_orders.items()):
            if order["marketId"] != market_id or order["status"] != "OPEN":
                continue
                
            price = order["price"]
            if price is None:
                continue
                
            side = order["side"]
            currency = order.get("currency", "NGN")
            multiplier = 100.0 if currency == "NGN" else 1.0
            
            scaled_bid = best_bid * multiplier if best_bid is not None else None
            scaled_ask = best_ask * multiplier if best_ask is not None else None
            
            # Intersection rules for simulated fills
            # BUY order: fill if best ask is <= our buy limit price (we can buy from sellers)
            # SELL order: fill if best bid is >= our sell limit price (we can sell to buyers)
            fill = False
            if side == "BUY" and scaled_ask is not None and scaled_ask <= price:
                fill = True
            elif side == "SELL" and scaled_bid is not None and scaled_bid >= price:
                fill = True
                
            if fill:
                order["status"] = "FILLED"
                logger.info(f"[DRY RUN FILL ALERT] Order {order_id} filled: {order['side']} @ {price}")
                
                # Update observer positions in dry run mode to keep inventory tracking accurate
                if self.observer is not None:
                    outcome_id = order["outcomeId"]
                    amount = order["amount"]
                    shares = amount / price if price else 0.0
                    self.observer.positions[outcome_id] = self.observer.positions.get(outcome_id, 0.0) + shares
                    logger.info(f"[DRY RUN POSITION UPDATE] Added {shares:.2f} shares for outcome {outcome_id}. New balance: {self.observer.positions[outcome_id]:.2f}")
