import asyncio
import logging
import signal
import sys
import time
from collections import deque
from datetime import datetime, timezone
import config
from config import BAYSE_PUBLIC_KEY, BAYSE_SECRET_KEY, DRY_RUN
from execution import ExecutionLayer
from observer import MarketObserver
from risk import RiskMeter
from analysis import ProbabilityDeviationStrategy
from price_feed import PriceFeedClient, extract_asset_symbol, estimate_binary_probability, parse_iso_datetime
from server import DashboardServer
from database import DatabaseManager
from ml_predictor import BitcoinMLPredictor

class DequeLogHandler(logging.Handler):
    def __init__(self, maxlen=100):
        super().__init__()
        self.log_buffer = deque(maxlen=maxlen)

    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_buffer.append(msg)
        except Exception:
            self.handleError(record)

# Configure Logging
log_handler = DequeLogHandler(maxlen=100)
log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log_handler.setFormatter(log_formatter)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        log_handler
    ]
)
logger = logging.getLogger("BayseBot.Main")

class BaysePredictorBot:
    def __init__(self):
        self.exec_layer = ExecutionLayer(BAYSE_PUBLIC_KEY, BAYSE_SECRET_KEY, dry_run=DRY_RUN)
        self.observer = MarketObserver(self.exec_layer, BAYSE_PUBLIC_KEY)
        self.exec_layer.observer = self.observer  # Enable dry run position updates
        self.risk_meter = RiskMeter(self.exec_layer, self.observer)
        self.strategy = ProbabilityDeviationStrategy()
        
        self.db = DatabaseManager()
        self.ml = BitcoinMLPredictor()
        self.price_feed = PriceFeedClient(ml_predictor=self.ml, db_manager=self.db)
        
        self.dashboard = None
        if config.ENABLE_DASHBOARD:
            self.dashboard = DashboardServer(self)
            
        self.latest_evaluations = {}
        self.is_running = False
        self.tasks = []

    async def start(self):
        logger.info("Starting Bayse Predictor Bot...")
        self.is_running = True
        
        # Initialize Database Manager
        await self.db.initialize()
        
        # 1. Initialize Execution Layer
        await self.exec_layer.initialize()
        
        # 2. Start Data Ingestion Observer
        await self.observer.start()
        
        # 3. Initialize Risk baseline
        await self.risk_meter.initialize()
        
        # 4. Start Dashboard Server if enabled
        if self.dashboard:
            await self.dashboard.start()
        
        # 5. Schedule loops
        self.tasks.append(asyncio.create_task(self._strategy_loop()))
        self.tasks.append(asyncio.create_task(self._risk_neutralizer_loop()))
        self.tasks.append(asyncio.create_task(self._database_resolver_loop()))
        self.tasks.append(asyncio.create_task(self._ml_retraining_loop()))
        
        logger.info("All services started and running.")

    async def stop(self):
        logger.info("Stopping Bayse Predictor Bot and cleaning up...")
        self.is_running = False
        
        # Cancel tasks
        for task in self.tasks:
            task.cancel()
        
        # Stop observer
        await self.observer.stop()
        
        # Stop Dashboard Server if enabled
        if self.dashboard:
            await self.dashboard.stop()
        
        # Close connection pool
        await self.exec_layer.close()
        
        # Close database pool
        await self.db.close()
        
        # Close price feed
        await self.price_feed.close()
        logger.info("Bot stopped successfully.")

    async def _discover_active_markets(self):
        """
        Dynamically fetches active markets/events from the Bayse REST API.
        Filters by category and skips long-term events (>7 days).
        Falls back to a simulated active market for local dry-run if the fetch fails or is empty.
        """
        try:
            # Hit REST API for active events, limiting to a larger page size to filter
            events_data = await self.exec_layer.request("GET", "/v1/pm/events?page=1&size=100")
            discovered = []
            for event in events_data.get("events", []):
                # 1. Category check (strictly crypto category)
                category = event.get("category", "").lower()
                if category != "crypto":
                    continue
                
                # Force strictly Bitcoin markets
                symbol = extract_asset_symbol(event)
                normalized_symbol = symbol.upper().replace("/", "").replace("-", "") if symbol else ""
                if normalized_symbol not in ["BTC", "BTCUSDT", "BTCUSD"]:
                    continue
                
                # 2. Date check (skip if resolution/closing is > 7 days out or not parseable)
                dt_str = event.get("closingDate") or event.get("resolutionDate")
                if not dt_str:
                    continue
                dt = parse_iso_datetime(dt_str)
                if not dt:
                    continue
                
                now = datetime.now(timezone.utc)
                diff = dt - now
                if diff.total_seconds() < 0 or diff.total_seconds() > 7 * 24 * 3600:
                    continue
                
                event_id = event.get("id")
                for market in event.get("markets", []):
                    market_id = market.get("id")
                    yes_id, no_id = None, None
                    
                    # Flat outcome mapping used in Bayse API
                    label1 = market.get("outcome1Label", "").upper()
                    label2 = market.get("outcome2Label", "").upper()
                    
                    if label1 in ["YES", "UP", "ABOVE", "OVER", "HIGHER"]:
                        yes_id = market.get("outcome1Id")
                    elif label1 in ["NO", "DOWN", "BELOW", "UNDER", "LOWER"]:
                        no_id = market.get("outcome1Id")
                        
                    if label2 in ["YES", "UP", "ABOVE", "OVER", "HIGHER"]:
                        yes_id = market.get("outcome2Id")
                    elif label2 in ["NO", "DOWN", "BELOW", "UNDER", "LOWER"]:
                        no_id = market.get("outcome2Id")
                        
                    if event_id and market_id and yes_id and no_id:
                        discovered.append({
                            "eventId": event_id,
                            "marketId": market_id,
                            "yesId": yes_id,
                            "noId": no_id,
                            "category": event.get("category"),
                            "assetSymbolPair": event.get("assetSymbolPair"),
                            "title": event.get("title"),
                            "slug": event.get("slug"),
                            "eventThreshold": event.get("eventThreshold"),
                            "closingDate": event.get("closingDate"),
                            "resolutionDate": event.get("resolutionDate"),
                            "supportedCurrencies": event.get("supportedCurrencies", [])
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
                "noId": "sim-no-uuid-1",
                "category": "crypto",
                "title": "Simulated BTC Market",
                "slug": "sim-btc-market",
                "eventThreshold": 60000.0,
                "supportedCurrencies": ["NGN"]
            }]
            
        logger.warning("No active markets found to trade on live exchange.")
        return []

    async def _strategy_loop(self):
        """
        Periodically runs the strategy evaluation across dynamically discovered markets.
        """
        while self.is_running:
            try:
                # Check if wallet has minimum balance to trade
                has_balance = await self.risk_meter.has_minimum_trading_balance()
                if not has_balance:
                    logger.warning("Insufficient wallet balance to place any trades (needs at least 100 NGN or 1 USD). Pausing strategy evaluation.")
                    await asyncio.sleep(15)
                    continue

                # Query wallet assets once before the loop to size Kelly portfolio balance smartly
                available_balances = {}
                try:
                    if not self.exec_layer.dry_run:
                        assets_data = await self.exec_layer.request("GET", "/v1/wallet/assets", authenticated=True)
                        for asset in assets_data.get("assets", []):
                            cur = (asset.get("symbol") or asset.get("currency", "")).upper()
                            val = float(asset.get("availableBalance", asset.get("available", 0.0)))
                            available_balances[cur] = val
                except Exception as e:
                    logger.debug(f"Could not retrieve dynamic wallet balances: {e}")

                markets = await self._discover_active_markets()
                self.latest_evaluations.clear()
                for m in markets:
                    target_market_id = m["marketId"]
                    target_event_id = m["eventId"]
                    outcome_yes_id = m["yesId"]
                    outcome_no_id = m["noId"]
                    
                    # Ensure book is loaded from REST if not already cached in memory
                    await self.observer.ensure_book_loaded(target_market_id, [outcome_yes_id, outcome_no_id])
                    
                    # Get currency and multiplier
                    currency = "NGN"
                    if m.get("supportedCurrencies") and "NGN" not in m.get("supportedCurrencies"):
                        currency = m.get("supportedCurrencies")[0]
                    multiplier = 100.0 if currency == "NGN" else 1.0

                    smart_balance = available_balances.get(currency.upper(), self.risk_meter.starting_daily_equity)

                    # Check current order book
                    best_bid, best_ask = self.observer.get_best_bid_ask(target_market_id)
                    
                    # For fallback simulated market, mock a book state if empty to trigger simulated trades
                    if best_bid is None or best_ask is None:
                        if target_market_id.startswith("sim-"):
                            best_bid_scaled = 65.0
                            best_ask_scaled = 70.0
                        else:
                            logger.info(f"Market {target_market_id} ({m.get('title')}): Skipping - empty order book (bids/asks not found).")
                            continue
                    else:
                        best_bid_scaled = best_bid * multiplier
                        best_ask_scaled = best_ask * multiplier
                            
                    # Calculate true probability dynamically
                    if target_market_id.startswith("sim-"):
                        true_probability = 0.85
                        symbol = "SIM"
                        current_price = 0.0
                        remaining_seconds = 0.0
                        threshold = 0.0
                        ml_model_used = "Black-Scholes (Normal CDF)"
                    else:
                        symbol = extract_asset_symbol(m)
                        if not symbol:
                            logger.warning(f"Market {target_market_id}: Skipping - could not extract asset symbol.")
                            continue
                        
                        current_price = await self.price_feed.get_price(symbol)
                        if current_price is None:
                            logger.warning(f"Market {target_market_id} ({symbol}): Skipping - could not fetch real-time price.")
                            continue
                        
                        dt_str = m.get("closingDate") or m.get("resolutionDate")
                        dt = parse_iso_datetime(dt_str)
                        if not dt:
                            logger.warning(f"Market {target_market_id}: Skipping - could not parse resolution date.")
                            continue
                        
                        now = datetime.now(timezone.utc)
                        remaining_seconds = (dt - now).total_seconds()
                        
                        threshold = m.get("eventThreshold")
                        if not threshold:
                            threshold = current_price
                        
                        # Volatility map
                        volatility = 0.50
                        if symbol in ["BTC", "BTCUSDT", "BTCUSD"]:
                            volatility = 0.50
                        elif symbol in ["ETH", "ETHUSDT", "ETHUSD"]:
                            volatility = 0.60
                        elif symbol in ["SOL", "SOLUSDT", "SOLUSD"]:
                            volatility = 0.80
                        elif symbol in ["GOLD", "XAU", "PAXG"]:
                            volatility = 0.15
                        elif symbol in ["USDNGN", "NGN", "USD/NGN", "EURUSD", "GBPUSD"]:
                            volatility = 0.15
                            
                        # Use estimate_probability which selects ML model if available
                        true_probability, ml_model_used = await self.price_feed.estimate_probability(
                            symbol=symbol,
                            current_price=current_price,
                            threshold=threshold,
                            time_remaining_seconds=remaining_seconds,
                            volatility=volatility
                        )

                    # Log evaluation details
                    norm_bid = best_bid_scaled / multiplier
                    norm_ask = best_ask_scaled / multiplier
                    yes_edge = true_probability - norm_ask
                    no_edge = (1.0 - true_probability) - (1.0 - norm_bid)
                    
                    logger.info(
                        f"Evaluating {m.get('title')} ({symbol}): "
                        f"Price={current_price}, Threshold={threshold}, T={remaining_seconds/3600:.2f}h | "
                        f"True Prob={true_probability:.2%}, Market Ask={norm_ask:.2%}, Bid={norm_bid:.2%} | "
                        f"Edge (YES/NO): {yes_edge:+.2%} / {no_edge:+.2%} (Min Edge: {self.strategy.min_edge:.2%}) | "
                        f"Model={ml_model_used}"
                    )

                    # Log evaluation to PostgreSQL database
                    if not target_market_id.startswith("sim-"):
                        await self.db.log_evaluation(
                            market_id=target_market_id,
                            asset=symbol,
                            spot_price=current_price,
                            threshold=threshold,
                            predicted_probability=true_probability,
                            best_bid=norm_bid,
                            best_ask=norm_ask,
                            time_remaining=remaining_seconds
                        )

                    # Store evaluation metrics for dashboard
                    self.latest_evaluations[target_market_id] = {
                        "title": m.get("title"),
                        "symbol": symbol,
                        "current_price": current_price,
                        "threshold": threshold,
                        "true_prob": true_probability,
                        "ask": norm_ask,
                        "bid": norm_bid,
                        "yes_edge": yes_edge,
                        "no_edge": no_edge,
                        "timestamp": time.time(),
                        "model_used": ml_model_used
                    }

                    signals = self.strategy.generate_signals(
                        event_id=target_event_id,
                        market_id=target_market_id,
                        outcome_yes_id=outcome_yes_id,
                        outcome_no_id=outcome_no_id,
                        true_probability=true_probability,
                        best_bid=best_bid_scaled,
                        best_ask=best_ask_scaled,
                        currency=currency,
                        portfolio_balance=smart_balance
                    )
                    
                    for sig in signals:
                        outcome_id = sig["outcomeId"]
                        
                        # PREVENT BALANCE DRAIN: Check if we have an active or recent trade in the database
                        has_db_trade = await self.db.has_recent_trade(sig['marketId'], outcome_id)
                        if has_db_trade:
                            logger.info(f"Skipping {sig['side']} for {outcome_id}: Active or recent trade exists in database.")
                            continue

                        # PREVENT BALANCE DRAIN: Check if we already hold a position here
                        current_position = self.observer.positions.get(outcome_id, 0.0)
                        if current_position > 0:
                            logger.info(f"Skipping {sig['side']} for {outcome_id}: Already holding {current_position} shares.")
                            continue

                        # PREVENT BALANCE DRAIN: Check if we already have an open order for this outcome
                        if self.exec_layer.dry_run:
                            has_open_order = any((o.get("outcomeId") == outcome_id or o.get("outcome") == outcome_id) and o.get("status") == "OPEN" for o in self.exec_layer.dry_run_orders.values())
                        else:
                            has_open_order = any(o.get("outcomeId") == outcome_id or o.get("outcome") == outcome_id for o in self.observer.open_orders.values())
                            
                        if has_open_order:
                            logger.info(f"Skipping {sig['side']} for {outcome_id}: Already have an open order on the book.")
                            continue

                        # Audit through risk filter
                        approved, audited_amount = await self.risk_meter.audit_order(
                            event_id=sig["eventId"],
                            market_id=sig["marketId"],
                            outcome_id=sig["outcomeId"],
                            side=sig["side"],
                            amount=sig["amount"],
                            price=sig["price"],
                            order_type=sig["type"],
                            currency=currency,
                            outcome_yes_id=outcome_yes_id
                        )
                        
                        if approved:
                            # Log signal to PostgreSQL database
                            if not target_market_id.startswith("sim-"):
                                await self.db.log_signal(
                                    market_id=sig["marketId"],
                                    outcome_id=sig["outcomeId"],
                                    trade_side=sig["side"],
                                    price=sig["price"],
                                    amount=audited_amount,
                                    reason=sig.get("reason", "")
                                )

                            logger.info(f"Signal Approved by RiskMeter. Executing {sig['side']} order...")
                            try:
                                resp = await self.exec_layer.create_order(
                                    event_id=sig["eventId"],
                                    market_id=sig["marketId"],
                                    outcome_id=sig["outcomeId"],
                                    side=sig["side"],
                                    amount=audited_amount,
                                    price=sig["price"],
                                    order_type=sig["type"],
                                    currency=currency
                                )
                                
                                # Optimistically track the open order immediately to prevent race conditions
                                order_id = resp.get("orderId") or resp.get("id") or resp.get("order", {}).get("id")
                                if order_id and not self.exec_layer.dry_run:
                                    self.observer.open_orders[order_id] = {
                                        "id": order_id,
                                        "marketId": sig["marketId"],
                                        "outcomeId": sig["outcomeId"],
                                        "side": sig["side"],
                                        "amount": audited_amount,
                                        "price": sig["price"],
                                        "status": "OPEN"
                                    }
                                
                                # Log executed trade to PostgreSQL database
                                if not target_market_id.startswith("sim-"):
                                    dt_str = m.get("closingDate") or m.get("resolutionDate")
                                    resolution_time = parse_iso_datetime(dt_str)
                                    outcome_type = "YES" if sig["outcomeId"] == m["yesId"] else "NO"
                                    
                                    await self.db.log_trade(
                                        market_id=sig["marketId"],
                                        event_id=sig["eventId"],
                                        outcome_id=sig["outcomeId"],
                                        outcome_type=outcome_type,
                                        asset=symbol,
                                        trade_side=sig["side"],
                                        predicted_probability=true_probability,
                                        execution_price=sig["price"],
                                        amount=audited_amount,
                                        currency=currency,
                                        ml_model_used=ml_model_used,
                                        resolution_time=resolution_time,
                                        threshold=threshold
                                    )
                            except Exception as e:
                                logger.error(f"Failed to execute order: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in strategy loop: {e}")
                
            # Sleep interval for evaluation
            await asyncio.sleep(5)

    async def _database_resolver_loop(self):
        """
        Periodically checks for unresolved trades in the database and updates their PnL
        once the resolution date has passed.
        """
        while self.is_running:
            try:
                unresolved = await self.db.fetch_unresolved_trades()
                now = datetime.now(timezone.utc)
                for trade in unresolved:
                    res_time = trade["resolution_time"]
                    if not res_time:
                        continue
                    if res_time.tzinfo is None:
                        res_time = res_time.replace(tzinfo=timezone.utc)
                        
                    if now > res_time:
                        # Trade has resolved! Fetch spot price at resolution time from database
                        query = """
                            SELECT spot_price FROM evaluations
                            WHERE asset = $1 AND timestamp >= $2::timestamptz - interval '2 minutes' AND timestamp <= $2::timestamptz + interval '2 minutes'
                            ORDER BY ABS(EXTRACT(EPOCH FROM (timestamp - $2::timestamptz))) ASC
                            LIMIT 1
                        """
                        spot_price = None
                        if self.db.pool:
                            async with self.db.pool.acquire() as conn:
                                row = await conn.fetchrow(query, trade["asset"], res_time)
                                if row:
                                    spot_price = float(row["spot_price"])
                                    
                        if spot_price is None:
                            # Fallback: get current spot price if database has no entry
                            spot_price = await self.price_feed.get_price(trade["asset"])
                            
                        if spot_price is not None:
                            threshold = float(trade["threshold"])
                            outcome_type = trade["outcome_type"]
                            amount = float(trade["amount"])
                            price = float(trade["execution_price"])
                            currency = trade["currency"]
                            multiplier = 100.0 if currency == "NGN" else 1.0
                            
                            # Determine if won
                            if outcome_type == "YES":
                                won = (spot_price >= threshold)
                            else:
                                won = (spot_price < threshold)
                                
                            # Calculate P&L
                            if won:
                                shares = amount / price if price else 0.0
                                pnl = (shares * multiplier) - amount
                            else:
                                pnl = -amount
                                
                            await self.db.update_trade_resolution(trade["id"], pnl)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in database resolver loop: {e}")
                
            await asyncio.sleep(60)

    async def _ml_retraining_loop(self):
        """
        Periodically triggers ML model retraining.
        """
        while self.is_running:
            try:
                # Wait 10 seconds initially for database to initialize/collect data
                await asyncio.sleep(10)
                await self.ml.train(self.db)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in ML retraining loop: {e}")
                
            # Retrain model every 10 minutes
            await asyncio.sleep(600)

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

    async def get_bot_status(self) -> dict:
        """
        Gathers status info about the bot for the dashboard API.
        """
        available_balance = 0.0
        currency = "NGN"
        daily_pnl = 0.0
        positions_held_count = 0
        
        try:
            # Run REST API calls concurrently
            assets_task = self.exec_layer.request("GET", "/v1/wallet/assets", authenticated=True)
            portfolio_task = self.exec_layer.request("GET", "/v1/pm/portfolio", authenticated=True)
            assets_data, portfolio_data = await asyncio.gather(assets_task, portfolio_task, return_exceptions=True)
            
            # Parse assets
            if not isinstance(assets_data, Exception):
                for asset in assets_data.get("assets", []):
                    # Check if NGN is available, or get the first currency
                    cur = (asset.get("symbol") or asset.get("currency", "")).upper()
                    if cur == "NGN":
                        available_balance = float(asset.get("availableBalance", asset.get("available", 0.0)))
                        currency = "NGN"
                        break
                    elif cur:
                        available_balance = float(asset.get("availableBalance", asset.get("available", 0.0)))
                        currency = cur
            else:
                available_balance = self.risk_meter.starting_daily_equity
            
            # Parse portfolio and PnL
            if not isinstance(portfolio_data, Exception):
                unrealized_pnl = float(portfolio_data.get("unrealizedPnl", 0.0))
                realized_pnl = float(portfolio_data.get("realizedPnl", 0.0))
                daily_pnl = unrealized_pnl + realized_pnl
                
                # count active positions (shares > 0)
                positions = portfolio_data.get("outcomeBalances", [])
                positions_held_count = sum(1 for p in positions if float(p.get("balance", 0.0)) > 0)
        except Exception as e:
            logger.warning(f"Error fetching status details from API: {e}")
            available_balance = self.risk_meter.starting_daily_equity
            
        # Get sorted evaluations list
        sorted_evals = sorted(
            self.latest_evaluations.values(),
            key=lambda x: max(x["yes_edge"], x["no_edge"]),
            reverse=True
        )
        
        # Get logs from buffer
        logs = list(log_handler.log_buffer)
        
        return {
            "dry_run": self.exec_layer.dry_run,
            "kill_switch_active": self.risk_meter.is_kill_switch_active,
            "available_balance": available_balance,
            "starting_equity": self.risk_meter.starting_daily_equity,
            "daily_pnl": daily_pnl,
            "currency": currency,
            "active_markets_count": len(self.latest_evaluations),
            "positions_held_count": positions_held_count,
            "evaluations": sorted_evals,
            "logs": logs,
            "ml_status": {
                "is_trained": self.ml.is_trained,
                "model_type": type(self.ml.model).__name__ if self.ml.is_trained and self.ml.model else "None (Black-Scholes Fallback)"
            }
        }


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
