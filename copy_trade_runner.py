import asyncio
import logging
import signal
import sys
import os
import json
import time
import aiohttp
import websockets

import config
from config import BAYSE_PUBLIC_KEY, BAYSE_SECRET_KEY, DRY_RUN
from execution import ExecutionLayer
from observer import MarketObserver
from risk import RiskMeter
from copy_strategy import BayseCopyTrader

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("BayseBot.CopyTradeRunner")

# Resolve target trader username tags to UUIDs
async def resolve_target_trader(session, username_tag: str) -> str:
    # Clean up the '@' if the user included it
    clean_tag = username_tag.lstrip('@')
    
    url = f"{config.BASE_REST_URL}/v1/user/lookup?username={clean_tag}"
    logger.info(f"Resolving trader username tag '{username_tag}' via URL: {url}")
    async with session.get(url) as resp:
        if resp.status == 200:
            user_profile = await resp.json()
            # Extract the raw permanent UUID
            resolved_id = user_profile.get("id")
            if resolved_id:
                logger.info(f"Successfully resolved username tag '{username_tag}' to UUID '{resolved_id}'")
                return resolved_id
    raise ValueError(f"Could not resolve target user tag: {username_tag}")

class CopyTradeRunner:
    def __init__(self):
        self.exec_layer = ExecutionLayer(BAYSE_PUBLIC_KEY, BAYSE_SECRET_KEY, dry_run=DRY_RUN)
        self.observer = MarketObserver(self.exec_layer, BAYSE_PUBLIC_KEY)
        self.exec_layer.observer = self.observer
        self.risk_meter = RiskMeter(self.exec_layer, self.observer)
        self.copy_trader = BayseCopyTrader(self.exec_layer, self.observer, self.risk_meter)
        
        self.is_running = False
        self.tasks = []
        self.ws_connection = None

    async def start(self):
        logger.info("Initializing Copy Trade Runner...")
        self.is_running = True
        
        # 1. Initialize execution layer & risk meter
        await self.exec_layer.initialize()
        await self.observer._sync_state() # Sync initial positions/orders
        await self.risk_meter.initialize()
        
        # 2. Resolve username tags to user IDs using a one-time startup lookup
        # Create temp aiohttp session for startup calls
        async with aiohttp.ClientSession() as startup_session:
            resolved_traders = []
            for trader in config.TARGET_TRADERS:
                # If it looks like a username tag (non-UUID format, containing letters or starting with @)
                # Let's perform a one-time lookup.
                # A standard UUID has 36 characters with hyphens.
                if len(trader) != 36 or "-" not in trader or trader.startswith("@"):
                    try:
                        resolved_id = await resolve_target_trader(startup_session, trader)
                        resolved_traders.append(resolved_id)
                    except Exception as e:
                        logger.error(f"Error resolving trader '{trader}': {e}. Using raw entry.")
                        resolved_traders.append(trader)
                else:
                    resolved_traders.append(trader)
            # Update config TARGET_TRADERS with resolved UUIDs
            config.TARGET_TRADERS = resolved_traders
            logger.info(f"Copy trade targets successfully configured/resolved: {config.TARGET_TRADERS}")

        # 3. Start connection to public WS and consume activity channel
        self.tasks.append(asyncio.create_task(self._websocket_consumer_loop()))
        logger.info("Copy Trade Runner started successfully and is now listening.")

    async def stop(self):
        logger.info("Stopping Copy Trade Runner and cleaning up...")
        self.is_running = False
        
        # Close Websocket connection if active
        if self.ws_connection:
            try:
                await self.ws_connection.close()
            except Exception:
                pass
                
        # Cancel tasks
        for task in self.tasks:
            task.cancel()
            
        await self.exec_layer.close()
        logger.info("Copy Trade Runner stopped successfully.")

    async def _websocket_consumer_loop(self):
        attempt = 0
        base_delay = 3.0
        max_delay = 60.0
        
        while self.is_running:
            try:
                url = config.PUBLIC_WS_URL
                logger.info(f"Connecting to public WebSocket channel: {url}")
                async with websockets.connect(url) as ws:
                    self.ws_connection = ws
                    attempt = 0
                    
                    # Subscribe to activity and trades channels
                    subscriptions = [
                        {"action": "subscribe", "channel": "activity"},
                        {"action": "subscribe", "channel": "trades"}
                    ]
                    
                    for sub in subscriptions:
                        await ws.send(json.dumps(sub))
                        await asyncio.sleep(0.1)
                        
                    logger.info("Subscribed to activity & trades streams. Consuming events...")
                    
                    async for message in ws:
                        if not self.is_running:
                            break
                        
                        data = json.loads(message)
                        event_type = data.get("event")
                        
                        # Process public trades or activity trades
                        # Check both the event name and contents for a trade structure
                        if event_type in ("trade", "trade_executed", "activity") or "userId" in data:
                            # Route directly to the copy strategy logic
                            # Fire-and-forget or await the logic
                            asyncio.create_task(self.copy_trader.process_public_trade(data))
                            
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                if not self.is_running:
                    break
                logger.error(f"WebSocket error in consumer loop: {e}")
                
                # Exponential backoff with jitter
                attempt += 1
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                logger.warning(f"Reconnecting WebSocket in {delay:.2f} seconds...")
                await asyncio.sleep(delay)

async def main():
    runner = CopyTradeRunner()
    
    # Handle OS termination signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(runner.stop()))
        
    try:
        await runner.start()
        # Keep main running until stopped
        while runner.is_running:
            await asyncio.sleep(1)
    except Exception as e:
        logger.critical(f"Unhandled exception in copy trade runner: {e}")
    finally:
        if runner.is_running:
            await runner.stop()

if __name__ == "__main__":
    asyncio.run(main())
