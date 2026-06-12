import asyncio
import json
import logging
import random
import time
from typing import Dict, Any, List, Optional
import websockets
import config

logger = logging.getLogger("BayseBot.Observer")

class MarketObserver:
    def __init__(self, execution_layer, public_key: str):
        self.exec_layer = execution_layer
        self.public_key = public_key
        
        # State tracking
        self.orderbooks: Dict[str, Dict[str, Any]] = {}  # marketId -> {"bids": {price: size}, "asks": {price: size}, "seq": int}
        self.portfolio: Dict[str, Any] = {}             # portfolio state
        self.positions: Dict[str, float] = {}           # outcomeId -> net shares balance
        self.open_orders: Dict[str, Dict[str, Any]] = {} # orderId -> order
        
        self.is_connected = False
        self.message_queue = asyncio.Queue()
        self.is_synchronizing = False
        
        # Tasks
        self.public_ws_task = None
        self.private_ws_task = None
        self.processing_task = None

    async def start(self):
        self.is_connected = True
        self.processing_task = asyncio.create_task(self._process_message_queue())
        self.public_ws_task = asyncio.create_task(self._ws_loop(config.PUBLIC_WS_URL, "public"))
        self.private_ws_task = asyncio.create_task(self._ws_loop(config.PRIVATE_WS_URL, "private"))
        logger.info("MarketObserver loops started")

    async def stop(self):
        self.is_connected = False
        if self.public_ws_task:
            self.public_ws_task.cancel()
        if self.private_ws_task:
            self.private_ws_task.cancel()
        if self.processing_task:
            self.processing_task.cancel()
        logger.info("MarketObserver stopped")

    async def _ws_loop(self, url: str, connection_type: str):
        """
        Main WebSocket loop with connection resilience using exponential backoff + jitter.
        """
        attempt = 0
        base_delay = 3.0
        max_delay = 60.0
        
        while self.is_connected:
            try:
                # Sign in or construct headers if private connection
                headers = {}
                if connection_type == "private":
                    # Private WebSocket requires authorization payload
                    # In a real API this might be passed as query param or initial message, 
                    # but here we'll simulate connection authentication headers or subscription auth message
                    headers["X-Public-Key"] = self.public_key
                    
                async with websockets.connect(url, additional_headers=headers) as ws:
                    logger.info(f"Connected to {connection_type} WebSocket")
                    attempt = 0  # Reset backoff on success
                    
                    # Trigger state synchronization upon reconnection
                    await self._sync_state()
                    
                    # Subscribe to channels (respecting rate limits)
                    await self._subscribe_channels(ws, connection_type)
                    
                    # Read loop
                    async for message in ws:
                        data = json.loads(message)
                        # Queue message for synchronization processing
                        await self.message_queue.put((connection_type, data, time.time()))
                        
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                if not self.is_connected:
                    break
                logger.error(f"{connection_type.capitalize()} WS error: {e}")
                
                # Exponential backoff with cryptographic jitter
                attempt += 1
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                jitter = random.uniform(0.0, 1.0)
                final_delay = delay + jitter
                logger.warning(f"Reconnecting {connection_type} WS in {final_delay:.2f} seconds (attempt {attempt})")
                await asyncio.sleep(final_delay)

    async def _subscribe_channels(self, ws, connection_type: str):
        """
        Subscribe to channels. WS rate limit: 10 subscriptions per second.
        """
        # Define subscriptions
        if connection_type == "public":
            subscriptions = [
                {"action": "subscribe", "channel": "orderbook"},
                {"action": "subscribe", "channel": "prices"},
                {"action": "subscribe", "channel": "activity"}
            ]
        else:
            subscriptions = [
                {"action": "subscribe", "channel": "orders"}
            ]
            
        for sub in subscriptions:
            await ws.send(json.dumps(sub))
            # Throttle to meet 10 msgs/sec limit (100ms sleep)
            await asyncio.sleep(0.1)
            
        logger.info(f"Subscribed to {connection_type} channels")

    async def _sync_state(self):
        """
        Performs the state synchronization protocol for portfolio, positions, and open orders.
        """
        if self.is_synchronizing:
            return
            
        self.is_synchronizing = True
        logger.info("Initializing State Synchronization Protocol...")
        
        try:
            # Fetch portfolio & positions
            try:
                portfolio_data = await self.exec_layer.request("GET", "/v1/pm/portfolio", authenticated=True)
                self.portfolio = portfolio_data
                for pos in portfolio_data.get("outcomeBalances", []):
                    o_id = pos.get("outcomeId") or pos.get("outcome")
                    if o_id:
                        self.positions[o_id] = float(pos.get("balance", 0.0))
            except Exception as e:
                logger.warning(f"Failed to fetch portfolio during sync: {e}. Starting with empty positions.")
                
            # Fetch open orders
            try:
                orders_data = await self.exec_layer.request("GET", "/v1/pm/orders", authenticated=True)
                orders_list = orders_data if isinstance(orders_data, list) else orders_data.get("orders", [])
                self.open_orders.clear()
                for o in orders_list:
                    status = o.get("status", "OPEN").upper()
                    if status in ("OPEN", "PARTIAL", "PARTIALLY_FILLED"):
                        self.open_orders[o["id"]] = o
                logger.info(f"Synchronized {len(self.open_orders)} open orders.")
            except Exception as e:
                logger.warning(f"Failed to fetch open orders during sync: {e}.")
                
            logger.info("State Synchronization complete. Ready to process WebSocket stream.")
        finally:
            self.is_synchronizing = False

    async def ensure_book_loaded(self, market_id: str, outcome_ids: List[str]):
        """
        Ensures the order book for the given market and outcomes is loaded into memory.
        Fetches snapshot from REST if not already cached.
        """
        if market_id in self.orderbooks:
            return
            
        try:
            params = "&".join([f"outcomeId[]={o_id}" for o_id in outcome_ids])
            books_data = await self.exec_layer.request("GET", f"/v1/pm/books?{params}")
            for book in books_data:
                m_id = book.get("marketId")
                if not m_id:
                    continue
                bids = {float(item["price"]): float(item["quantity"]) for item in book.get("bids", []) if "price" in item}
                asks = {float(item["price"]): float(item["quantity"]) for item in book.get("asks", []) if "price" in item}
                
                self.orderbooks[m_id] = {
                    "bids": bids,
                    "asks": asks,
                    "seq": book.get("sequence", 0),
                    "last_update": time.time()
                }
        except Exception as e:
            logger.warning(f"Failed to fetch order book snapshot for market {market_id}: {e}")

    async def _process_message_queue(self):
        """
        Consumes messages from queue and applies state synchronization rules.
        """
        while True:
            conn_type, msg, recv_time = await self.message_queue.get()
            try:
                # If synchronizing, keep queue filling up. We only process once synchronization finishes,
                # but since _sync_state is synchronous/concurrent, we can process here.
                # Discard messages older than the snapshot sequence
                event_type = msg.get("event")
                market_id = msg.get("marketId")
                msg_seq = msg.get("sequence", 0)
                
                if market_id and market_id in self.orderbooks:
                    snap_seq = self.orderbooks[market_id].get("seq", 0)
                    if msg_seq <= snap_seq:
                        # Skip stale websocket updates
                        continue
                
                if event_type == "orderbook_update":
                    self._apply_orderbook_update(market_id, msg)
                elif event_type == "order_updated":
                    # Private orders stream
                    outcome_id = msg.get("outcomeId") or msg.get("outcome")
                    balance_change = float(msg.get("filledQtyDelta", 0.0) or msg.get("filledQty", 0.0) or msg.get("qty", 0.0))
                    if outcome_id:
                        self.positions[outcome_id] = self.positions.get(outcome_id, 0.0) + balance_change
                    
                    # Update open_orders dict
                    order_id = msg.get("id") or msg.get("orderId")
                    if order_id:
                        status = msg.get("status", "").upper()
                        if status in ("FILLED", "CANCELLED", "REJECTED", "EXPIRED"):
                            self.open_orders.pop(order_id, None)
                        else:
                            self.open_orders[order_id] = msg
                elif event_type == "ticker":
                    # Apply ticker prices if necessary
                    pass
                    
            except Exception as e:
                logger.error(f"Error processing websocket message: {e}")
            finally:
                self.message_queue.task_done()

    def _apply_orderbook_update(self, market_id: str, msg: dict):
        """
        Updates in-memory order book state.
        """
        if market_id not in self.orderbooks:
            self.orderbooks[market_id] = {"bids": {}, "asks": {}, "seq": 0, "last_update": 0}
            
        book = self.orderbooks[market_id]
        book["seq"] = msg.get("sequence", book["seq"] + 1)
        book["last_update"] = time.time()
        
        # Format of bids/asks updates: [[price, size]]
        # A size of 0.0 clears that level.
        for side in ("bids", "asks"):
            for price_str, size_str in msg.get(side, []):
                price = float(price_str)
                size = float(size_str)
                if size == 0.0:
                    book[side].pop(price, None)
                else:
                    book[side][price] = size

        # Update Dry Run simulation if active
        best_bid = max(book["bids"].keys()) if book["bids"] else None
        best_ask = min(book["asks"].keys()) if book["asks"] else None
        self.exec_layer.process_dry_run_book_update(market_id, best_bid, best_ask)

    def get_best_bid_ask(self, market_id: str) -> tuple:
        if market_id not in self.orderbooks:
            return None, None
        book = self.orderbooks[market_id]
        best_bid = max(book["bids"].keys()) if book["bids"] else None
        best_ask = min(book["asks"].keys()) if book["asks"] else None
        return best_bid, best_ask
