import asyncio
import logging
import signal
import sys
from config import BAYSE_PUBLIC_KEY, BAYSE_SECRET_KEY, DRY_RUN
from execution import ExecutionLayer
from observer import MarketObserver
from risk import RiskMeter
from analysis import ProbabilityDeviationStrategy

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("BayseBot.Main")

class BaysePredictorBot:
    def __init__(self):
        self.exec_layer = ExecutionLayer(BAYSE_PUBLIC_KEY, BAYSE_SECRET_KEY, dry_run=DRY_RUN)
        self.observer = MarketObserver(self.exec_layer, BAYSE_PUBLIC_KEY)
        self.risk_meter = RiskMeter(self.exec_layer, self.observer)
        self.strategy = ProbabilityDeviationStrategy()
        
        self.is_running = False
        self.tasks = []

    async def start(self):
        logger.info("Starting Bayse Predictor Bot...")
        self.is_running = True
        
        # 1. Initialize Execution Layer
        await self.exec_layer.initialize()
        
        # 2. Start Data Ingestion Observer
        await self.observer.start()
        
        # 3. Initialize Risk baseline
        await self.risk_meter.initialize()
        
        # 4. Schedule loops
        self.tasks.append(asyncio.create_task(self._strategy_loop()))
        self.tasks.append(asyncio.create_task(self._risk_neutralizer_loop()))
        
        logger.info("All services started and running.")

    async def stop(self):
        logger.info("Stopping Bayse Predictor Bot and cleaning up...")
        self.is_running = False
        
        # Cancel tasks
        for task in self.tasks:
            task.cancel()
        
        # Stop observer
        await self.observer.stop()
        
        # Close connection pool
        await self.exec_layer.close()
        logger.info("Bot stopped successfully.")

    async def _discover_active_markets(self):
        """
        Dynamically fetches active markets/events from the Bayse REST API.
        Falls back to a simulated active market for local dry-run if the fetch fails or is empty.
        """
        try:
            # Hit REST API for active events, limiting to a small subset for loop efficiency
            events_data = await self.exec_layer.request("GET", "/v1/pm/events?page=1&size=5")
            discovered = []
            for event in events_data.get("events", []):
                event_id = event.get("id")
                for market in event.get("markets", []):
                    market_id = market.get("id")
                    yes_id, no_id = None, None
                    
                    # Flat outcome mapping used in Bayse API
                    label1 = market.get("outcome1Label", "").upper()
                    label2 = market.get("outcome2Label", "").upper()
                    
                    if label1 == "YES":
                        yes_id = market.get("outcome1Id")
                    elif label1 == "NO":
                        no_id = market.get("outcome1Id")
                        
                    if label2 == "YES":
                        yes_id = market.get("outcome2Id")
                    elif label2 == "NO":
                        no_id = market.get("outcome2Id")
                        
                    if event_id and market_id and yes_id and no_id:
                        discovered.append({
                            "eventId": event_id,
                            "marketId": market_id,
                            "yesId": yes_id,
                            "noId": no_id
                        })
            if discovered:
                logger.info(f"Dynamically discovered {len(discovered)} active markets.")
                return discovered
        except Exception as e:
            logger.debug(f"Failed to fetch live active markets: {e}. Using simulated fallback.")

        # Only fall back to simulated UUIDs if in DRY_RUN mode
        if DRY_RUN:
            return [{
                "eventId": "sim-event-uuid-1",
                "marketId": "sim-market-uuid-1",
                "yesId": "sim-yes-uuid-1",
                "noId": "sim-no-uuid-1"
            }]
            
        logger.warning("No active markets found to trade on live exchange.")
        return []

    async def _strategy_loop(self):
        """
        Periodically runs the strategy evaluation across dynamically discovered markets.
        """
        while self.is_running:
            try:
                markets = await self._discover_active_markets()
                for m in markets:
                    target_market_id = m["marketId"]
                    target_event_id = m["eventId"]
                    outcome_yes_id = m["yesId"]
                    outcome_no_id = m["noId"]
                    
                    # Ensure book is loaded from REST if not already cached in memory
                    await self.observer.ensure_book_loaded(target_market_id, [outcome_yes_id, outcome_no_id])
                    
                    # Check current order book
                    best_bid, best_ask = self.observer.get_best_bid_ask(target_market_id)
                    
                    # For fallback simulated market, mock a book state if empty to trigger simulated trades
                    if best_bid is None or best_ask is None:
                        if target_market_id.startswith("sim-"):
                            best_bid = 65.0
                            best_ask = 70.0
                        else:
                            continue
                            
                    # Example true probability prediction (e.g. 85%)
                    true_probability = 0.85
                    
                    signals = self.strategy.generate_signals(
                        event_id=target_event_id,
                        market_id=target_market_id,
                        outcome_yes_id=outcome_yes_id,
                        outcome_no_id=outcome_no_id,
                        true_probability=true_probability,
                        best_bid=best_bid,
                        best_ask=best_ask
                    )
                    
                    for sig in signals:
                        # Audit through risk filter
                        approved, audited_amount = await self.risk_meter.audit_order(
                            event_id=sig["eventId"],
                            market_id=sig["marketId"],
                            outcome_id=sig["outcomeId"],
                            side=sig["side"],
                            amount=sig["amount"],
                            price=sig["price"],
                            order_type=sig["type"]
                        )
                        
                        if approved:
                            logger.info(f"Signal Approved by RiskMeter. Executing {sig['side']} order...")
                            await self.exec_layer.create_order(
                                event_id=sig["eventId"],
                                market_id=sig["marketId"],
                                outcome_id=sig["outcomeId"],
                                side=sig["side"],
                                amount=audited_amount,
                                price=sig["price"],
                                order_type=sig["type"]
                            )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in strategy loop: {e}")
                
            # Sleep interval for evaluation
            await asyncio.sleep(5)

    async def _risk_neutralizer_loop(self):
        """
        Periodically checks for matched YES/NO pairs to burn and recycle capital.
        """
        while self.is_running:
            try:
                await self.risk_meter.check_and_neutralize_exposure()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in risk neutralization loop: {e}")
                
            # Sleep 60 seconds between audits
            await asyncio.sleep(60)


async def main():
    bot = BaysePredictorBot()
    
    # Handle OS termination signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))
        
    try:
        await bot.start()
        # Keep main running until stopped
        while bot.is_running:
            await asyncio.sleep(1)
    except Exception as e:
        logger.critical(f"Unhandled exception in bot process: {e}")
    finally:
        if bot.is_running:
            await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
