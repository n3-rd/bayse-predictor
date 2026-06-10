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

    async def audit_order(self, event_id: str, market_id: str, outcome_id: str, side: str, 
                          amount: float, price: float, order_type: str = "LIMIT") -> tuple[bool, float]:
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

        # 2. Enforce 2% Max Position Sizing
        max_risk = self.starting_daily_equity * config.MAX_POSITION_SIZE_PCT
        audited_amount = amount
        if amount > max_risk:
            logger.warning(f"Order size {amount} exceeds 2% max risk limit ({max_risk}). Truncating to limit.")
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
        if book and order_type.upper() == "LIMIT":
            # Simulate execution to check price slippage
            side_key = "asks" if side.upper() == "BUY" else "bids"
            resting_orders = sorted(book[side_key].items(), key=lambda x: x[0], reverse=(side_key == "bids"))
            
            accumulated_size = 0.0
            average_price = 0.0
            needed_shares = audited_amount / price
            
            for rest_price, rest_size in resting_orders:
                take_size = min(rest_size, needed_shares - accumulated_size)
                average_price += take_size * rest_price
                accumulated_size += take_size
                if accumulated_size >= needed_shares:
                    break
                    
            if accumulated_size > 0:
                avg_execution_price = average_price / accumulated_size
                slippage = abs(avg_execution_price - price) / price
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
