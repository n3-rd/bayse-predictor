import logging
from typing import Dict, Any, Optional
import config

logger = logging.getLogger("BayseBot.Risk")

class RiskMeter:
    def __init__(self, execution_layer, observer):
        self.exec_layer = execution_layer
        self.observer = observer
        
        # P&L tracking
        self.starting_daily_equity: float = 0.0
        self.current_daily_loss: float = 0.0
        self.is_kill_switch_active = False

    async def initialize(self, currency: str = "NGN"):
        # Fetch initial wallet balance
        try:
            assets = await self.exec_layer.request("GET", "/v1/wallet/assets", authenticated=True)
            # Structure: {"assets": [{"currency": "NGN", "balance": float, "available": float}]}
            for asset in assets.get("assets", []):
                if asset.get("currency") == currency:
                    self.starting_daily_equity = float(asset.get("balance", 0.0))
                    logger.info(f"RiskMeter initialized. Starting daily equity: {self.starting_daily_equity} {currency}")
                    return
            # Fallback if no matching asset found
            self.starting_daily_equity = 10000.0  # default simulation amount
        except Exception as e:
            logger.warning(f"Could not retrieve live assets for risk baseline: {e}. Defaulting baseline to 1,000,000 NGN.")
            self.starting_daily_equity = 1000000.0
            
        logger.info(f"RiskMeter active. Starting daily equity baseline: {self.starting_daily_equity}")

    async def has_minimum_trading_balance(self) -> bool:
        """
        Quickly verifies if the wallet has the absolute minimum balance required to place any trade.
        Avoids running strategy thinking when there is no spendable capital.
        """
        if self.exec_layer.dry_run:
            return True
        try:
            assets_data = await self.exec_layer.request("GET", "/v1/wallet/assets", authenticated=True)
            for asset in assets_data.get("assets", []):
                currency = (asset.get("symbol") or asset.get("currency", "")).upper()
                available = float(asset.get("availableBalance", asset.get("available", 0.0)))
                # Minimum limit is 100 NGN or 1.00 USD
                if currency == "NGN" and available >= 100.0:
                    return True
                elif currency == "USD" and available >= 1.0:
                    return True
            return False
        except Exception as e:
            logger.warning(f"Error checking minimum trading balance: {e}")
            return True # Fallback to True to prevent lockup

    async def audit_order(self, event_id: str, market_id: str, outcome_id: str, side: str, 
                          amount: float, price: float, order_type: str = "LIMIT", currency: str = "NGN",
                          outcome_yes_id: Optional[str] = None) -> tuple[bool, float]:
        """
        Audits order specifications against size limits, slippage limit, and daily loss limits.
        Returns (is_approved, modified_amount).
        """
        if self.is_kill_switch_active:
            logger.error("Order rejected: Global Daily Loss Kill Switch is active!")
            return False, 0.0
            
        # 1. Enforce Daily Loss Limit
        await self.check_daily_loss_limit()
        if self.is_kill_switch_active:
            logger.error("Order rejected: Global Daily Loss Kill Switch was just triggered!")
            return False, 0.0

        # 1.5 Enforce 30% Max Total Portfolio Allocation Limit
        total_balance = self.starting_daily_equity
        available_balance = self.starting_daily_equity
        
        try:
            assets_data = await self.exec_layer.request("GET", "/v1/wallet/assets", authenticated=True)
            for asset in assets_data.get("assets", []):
                symbol = (asset.get("symbol") or asset.get("currency", "")).upper()
                if symbol == currency.upper():
                    total_balance = float(asset.get("balance", asset.get("total", 0.0)))
                    available_balance = float(asset.get("availableBalance", asset.get("available", 0.0)))
                    break
        except Exception:
            if self.exec_layer.dry_run:
                open_orders_val = sum(o.get("amount", 0.0) for o in self.exec_layer.dry_run_orders.values() if o.get("status") == "OPEN")
                positions_val = sum(pos_qty * 50.0 for pos_qty in self.observer.positions.values())
                allocated_balance = open_orders_val + positions_val
                available_balance = total_balance - allocated_balance
        
        allocated_balance = total_balance - available_balance
        max_total_allocation = total_balance * getattr(config, "MAX_TOTAL_ALLOCATION_PCT", 0.30)
        
        audited_amount = amount
        if allocated_balance + audited_amount > max_total_allocation:
            allowed_amount = max_total_allocation - allocated_balance
            min_limit = 100.0 if currency.upper() == "NGN" else 1.0
            if allowed_amount < min_limit:
                logger.error(f"Order rejected: Total allocated balance ({allocated_balance:.2f}) + order amount ({audited_amount:.2f}) exceeds the {getattr(config, 'MAX_TOTAL_ALLOCATION_PCT', 0.30):.0%} limit ({max_total_allocation:.2f}). No room for new trades.")
                return False, 0.0
            else:
                logger.warning(f"Order amount truncated from {audited_amount:.2f} to {allowed_amount:.2f} to satisfy total portfolio allocation limit ({max_total_allocation:.2f}).")
                audited_amount = allowed_amount

        # 2. Enforce 2% Max Position Sizing
        max_risk = self.starting_daily_equity * config.MAX_POSITION_SIZE_PCT
        if audited_amount > max_risk:
            logger.warning(f"Order size {audited_amount} exceeds 2% max risk limit ({max_risk}). Truncating to limit.")
            audited_amount = max_risk
            
        # Ensure minimum constraints (e.g. 100 NGN or 1.00 USD)
        if audited_amount < 100.0:
            logger.warning(f"Audited amount {audited_amount} is below the exchange minimum order limit. Rejecting order.")
            return False, 0.0
            
        # 3. Enforce Wallet Balance limit for live BUY orders
        if not self.exec_layer.dry_run and side.upper() == "BUY":
            try:
                available_balance = 0.0
                assets_data = await self.exec_layer.request("GET", "/v1/wallet/assets", authenticated=True)
                for asset in assets_data.get("assets", []):
                    symbol = (asset.get("symbol") or asset.get("currency", "")).upper()
                    if symbol == "NGN":
                        available_balance = float(asset.get("availableBalance", asset.get("available", 0.0)))
                        break
                
                if available_balance < audited_amount:
                    if available_balance >= 100.0:
                        logger.warning(f"Audited amount {audited_amount} exceeds available NGN balance ({available_balance}). Truncating to available balance.")
                        audited_amount = available_balance
                    else:
                        logger.error(f"Order rejected: Insufficient balance. Available: {available_balance} NGN, Required minimum: 100.0 NGN")
                        return False, 0.0
            except Exception as e:
                logger.warning(f"Error checking dynamic wallet balance: {e}. Proceeding with limits.")

        # 3. Liquidity Filtering & Slippage Check
        book = self.observer.orderbooks.get(market_id)
        if book:
            multiplier = 100.0 if currency.upper() == "NGN" else 1.0
            
            # Calculate total resting liquidity on both sides in shares
            total_bids = sum(book["bids"].values())
            total_asks = sum(book["asks"].values())
            total_liquidity = total_bids + total_asks
            
            # Scale to actual monetary value (e.g. 1 share represents up to 100 NGN)
            total_liquidity_value = total_liquidity * multiplier
            
            # Liquidity limit depends on currency
            min_liquidity = 500.0 if currency.upper() == "NGN" else 500.0
            
            if total_liquidity_value < min_liquidity:
                logger.error(f"Order rejected: Market is too illiquid. Total resting volume: {total_liquidity_value:.2f} {currency} (required: {min_liquidity} {currency})")
                return False, 0.0

            if order_type.upper() == "LIMIT":
                # Determine if we are dealing with YES or NO outcome
                is_yes = (outcome_yes_id is None) or (outcome_id == outcome_yes_id)
                
                # In a unified YES orderbook:
                # - Buying YES matches against YES asks. Execution price = rest_price.
                # - Buying NO matches against YES bids. Execution price = 1.0 - rest_price.
                # - Selling YES matches against YES bids. Execution price = rest_price.
                # - Selling NO matches against YES asks. Execution price = 1.0 - rest_price.
                if is_yes:
                    side_key = "asks" if side.upper() == "BUY" else "bids"
                else:
                    side_key = "bids" if side.upper() == "BUY" else "asks"
                    
                resting_orders = sorted(book[side_key].items(), key=lambda x: x[0], reverse=(side_key == "bids"))
                
                accumulated_size = 0.0
                average_price = 0.0
                needed_shares = audited_amount / price
                
                for rest_price, rest_size in resting_orders:
                    take_size = min(rest_size, needed_shares - accumulated_size)
                    
                    execution_price = rest_price if is_yes else (1.0 - rest_price)
                    average_price += take_size * execution_price
                    accumulated_size += take_size
                    if accumulated_size >= needed_shares:
                        break
                        
                if accumulated_size > 0:
                    avg_execution_price = average_price / accumulated_size
                    # Normalize input price to match order book scale
                    norm_price = price / multiplier
                    slippage = abs(avg_execution_price - norm_price) / norm_price
                    logger.info(f"[DEBUG SLIPPAGE] side={side}, is_yes={is_yes}, side_key={side_key}, price={price}, norm_price={norm_price}, needed_shares={needed_shares}")
                    logger.info(f"[DEBUG SLIPPAGE] resting_orders={resting_orders[:10]}")
                    logger.info(f"[DEBUG SLIPPAGE] accumulated_size={accumulated_size}, average_price={average_price}, avg_execution_price={avg_execution_price}, slippage={slippage:.2%}")
                    if slippage > config.DEFAULT_SLIPPAGE:
                        logger.error(f"Order rejected: Simulated slippage of {slippage:.2%} exceeds limit of {config.DEFAULT_SLIPPAGE:.2%}")
                        return False, 0.0
        return True, audited_amount

    async def check_daily_loss_limit(self):
        """
        Calculates cumulative daily loss. Triggers emergency shutdown if limit (>5%) is crossed.
        """
        if self.is_kill_switch_active:
            return
            
        try:
            # Fetch active portfolio P&L
            portfolio_data = await self.exec_layer.request("GET", "/v1/pm/portfolio", authenticated=True)
            # Sum unrealized and realized P&L
            unrealized_pnl = float(portfolio_data.get("unrealizedPnl", 0.0))
            realized_pnl = float(portfolio_data.get("realizedPnl", 0.0))
            total_pnl = unrealized_pnl + realized_pnl
            
            if total_pnl < 0:
                loss_pct = abs(total_pnl) / self.starting_daily_equity
                if loss_pct >= config.DAILY_LOSS_LIMIT_PCT:
                    logger.critical(f"CRITICAL: Daily loss of {loss_pct:.2%} crossed limit of {config.DAILY_LOSS_LIMIT_PCT:.2%}. TRIGGERING KILL SWITCH.")
                    self.is_kill_switch_active = True
                    await self.trigger_emergency_shutdown()
        except Exception as e:
            logger.warning(f"Error checking daily loss limit: {e}")

    async def trigger_emergency_shutdown(self):
        """
        Emergency routine: cancels all resting orders and disables execution.
        """
        logger.critical("EMERGENCY SHUTDOWN ROUTINE STARTED. Cancelling all open orders...")
        try:
            await self.exec_layer.cancel_all_orders()
            logger.info("All open orders have been successfully cancelled. Execution halted.")
        except Exception as e:
            logger.error(f"Failed to cancel orders during emergency shutdown: {e}")

    async def check_and_neutralize_exposure(self):
        """
        Exposure Neutralization via Minting and Burning.
        Scans portfolio for matching YES and NO shares in same market and burns them to recycle capital.
        """
        portfolio = self.observer.portfolio
        if not portfolio:
            return
            
        # Group holdings by marketId
        holdings_by_market: Dict[str, Dict[str, float]] = {}
        for pos in portfolio.get("outcomeBalances", []):
            m_id = pos.get("marketId")
            outcome_name = pos.get("outcomeName")  # e.g., "YES" or "NO"
            balance = float(pos.get("balance", 0.0))
            
            if not m_id or not outcome_name:
                continue
                
            if m_id not in holdings_by_market:
                holdings_by_market[m_id] = {"YES": 0.0, "NO": 0.0}
            if outcome_name.upper() in ("YES", "NO"):
                holdings_by_market[m_id][outcome_name.upper()] = balance

        for m_id, outcomes in holdings_by_market.items():
            yes_bal = outcomes["YES"]
            no_bal = outcomes["NO"]
            
            # Find matching pairs (the minimum of YES and NO balances)
            burnable_pairs = min(yes_bal, no_bal)
            if burnable_pairs > 0:
                logger.info(f"Delta-neutral pairs found in market {m_id}: {burnable_pairs} shares. Triggering burn...")
                try:
                    await self.exec_layer.burn_shares(m_id)
                    # Trigger state sync to update positions in memory
                    await self.observer._sync_state()
                except Exception as e:
                    logger.error(f"Failed to burn shares in market {m_id}: {e}")
