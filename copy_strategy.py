import logging
import time
from datetime import datetime, timezone
import config
from price_feed import parse_iso_datetime

logger = logging.getLogger("BayseBot.CopyStrategy")

class BayseCopyTrader:
    def __init__(self, execution_layer, observer, risk_meter):
        self.exec_layer = execution_layer
        self.observer = observer
        self.risk_meter = risk_meter

    async def process_public_trade(self, trade_event: dict):
        """
        Process a public trade event and copy it if it matches criteria and risk rules.
        """
        user_id = trade_event.get("userId")
        if not user_id or user_id not in config.TARGET_TRADERS:
            # Not a target trader we want to follow
            return

        logger.info(f"Target trade detected for user {user_id}: {trade_event}")

        # Extract payload
        event_id = trade_event.get("eventId")
        market_id = trade_event.get("marketId")
        outcome_id = trade_event.get("outcomeId") or trade_event.get("outcome")
        side = trade_event.get("side")
        price = trade_event.get("price")
        amount = trade_event.get("amount")
        currency = trade_event.get("currency", "NGN")

        if not all([event_id, market_id, outcome_id, side, price, amount]):
            logger.warning("Missing required fields in trade event payload. Skipping.")
            return

        # Latency check: Reject trades delayed by > 2 seconds
        event_ts = trade_event.get("timestamp") or trade_event.get("createdAt")
        latency = 0.0
        now = time.time()
        
        if event_ts:
            if isinstance(event_ts, (int, float)):
                # If timestamp is in milliseconds
                if event_ts > 1e11:
                    event_ts = event_ts / 1000.0
                latency = now - event_ts
            elif isinstance(event_ts, str):
                dt = parse_iso_datetime(event_ts)
                if dt:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    latency = now - dt.timestamp()
            
            logger.info(f"Trade event latency: {latency:.4f} seconds")
            if latency > 2.0:
                logger.warning(f"Trade rejected: Latency ({latency:.2f}s) exceeded 2 second limit.")
                return
        else:
            logger.warning("No timestamp found in event. Skipping copy to ensure execution safety.")
            return

        # Calculate trade size: size cleanly against the 2% maximum constraint
        # Determine current balance
        balance = self.risk_meter.starting_daily_equity
        if not self.exec_layer.dry_run:
            try:
                assets_data = await self.exec_layer.request("GET", "/v1/wallet/assets", authenticated=True)
                for asset in assets_data.get("assets", []):
                    symbol = (asset.get("symbol") or asset.get("currency", "")).upper()
                    if symbol == currency.upper():
                        balance = float(asset.get("availableBalance", asset.get("available", 0.0)))
                        break
            except Exception as e:
                logger.warning(f"Failed to query wallet balance for sizing: {e}. Using starting equity.")

        max_risk_amount = balance * config.COPY_TRADE_MAX_ALLOCATION_PCT
        # Size proportionally or cap at max risk amount
        target_amount = float(amount)
        trade_amount = min(target_amount, max_risk_amount)
        
        # Ensure we meet minimum requirement
        if trade_amount < 100.0 if currency.upper() == "NGN" else 1.0:
            logger.warning(f"Calculated trade size {trade_amount} {currency} is below minimal trade limits. Skipping.")
            return

        # Send abstract signal to risk_meter.audit_order() to verify safety
        # We pass the outcome_id as outcome_yes_id to let the RiskMeter evaluate correctly
        approved, audited_amount = await self.risk_meter.audit_order(
            event_id=event_id,
            market_id=market_id,
            outcome_id=outcome_id,
            side=side,
            amount=trade_amount,
            price=price,
            order_type="LIMIT",
            currency=currency
        )

        if not approved:
            logger.warning("Trade rejected by RiskMeter audit.")
            return

        # Execute order using Time-In-Force (TIF) set to "FAK" (Fill and Kill)
        logger.info(f"Executing copy trade: market={market_id}, outcome={outcome_id}, side={side}, amount={audited_amount}, TIF=FAK")
        try:
            resp = await self.exec_layer.create_order(
                event_id=event_id,
                market_id=market_id,
                outcome_id=outcome_id,
                side=side,
                amount=audited_amount,
                price=price,
                order_type="LIMIT",
                max_slippage=config.MAX_COPY_SLIPPAGE,
                currency=currency,
                time_in_force="FAK"
            )
            logger.info(f"Copy trade execution response: {resp}")
        except Exception as e:
            logger.error(f"Failed to execute copy trade order: {e}")
