import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import asyncpg

logger = logging.getLogger("BayseBot.Database")

class DatabaseManager:
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or os.getenv("DB_URL")
        self.pool: Optional[asyncpg.Pool] = None

    async def initialize(self):
        """
        Initializes the PostgreSQL connection pool and creates the required tables.
        """
        if not self.db_url:
            logger.error("DB_URL is not set. Database logging is disabled.")
            return

        logger.info("Initializing PostgreSQL Connection Pool...")
        try:
            self.pool = await asyncpg.create_pool(self.db_url, min_size=1, max_size=10)
            logger.info("Database pool created. Running migrations...")
            await self.create_tables()
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL database: {e}")
            self.pool = None

    async def close(self):
        """
        Closes the database connection pool.
        """
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL database pool closed.")

    async def create_tables(self):
        """
        Creates the tables required for bot logging if they do not exist.
        """
        if not self.pool:
            return

        queries = [
            """
            CREATE TABLE IF NOT EXISTS evaluations (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                market_id VARCHAR(255) NOT NULL,
                asset VARCHAR(50) NOT NULL,
                spot_price DECIMAL(15,4) NOT NULL,
                threshold DECIMAL(15,4) NOT NULL,
                predicted_probability DECIMAL(5,4) NOT NULL,
                best_bid DECIMAL(10,4),
                best_ask DECIMAL(10,4),
                time_remaining DECIMAL(15,2)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS signals (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                market_id VARCHAR(255) NOT NULL,
                outcome_id VARCHAR(255) NOT NULL,
                trade_side VARCHAR(10) NOT NULL,
                price DECIMAL(10,4) NOT NULL,
                amount DECIMAL(15,2) NOT NULL,
                reason TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                market_id VARCHAR(255) NOT NULL,
                event_id VARCHAR(255) NOT NULL,
                outcome_id VARCHAR(255) NOT NULL,
                outcome_type VARCHAR(10) NOT NULL,
                asset VARCHAR(50) NOT NULL,
                trade_side VARCHAR(10) NOT NULL,
                predicted_probability DECIMAL(5,4) NOT NULL,
                execution_price DECIMAL(10,4) NOT NULL,
                amount DECIMAL(15,2) NOT NULL,
                currency VARCHAR(10) DEFAULT 'NGN',
                ml_model_used VARCHAR(100) NOT NULL,
                pnl DECIMAL(15,2) DEFAULT 0.0,
                resolution_time TIMESTAMP WITH TIME ZONE,
                threshold DECIMAL(15,4),
                is_resolved BOOLEAN DEFAULT FALSE
            );
            """,
            # Create index on evaluations for quick history retrieval
            "CREATE INDEX IF NOT EXISTS idx_evaluations_asset_time ON evaluations(asset, timestamp DESC);"
        ]

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for q in queries:
                    await conn.execute(q)
        logger.info("Database migrations executed successfully.")

    async def log_evaluation(self, market_id: str, asset: str, spot_price: float, threshold: float,
                             predicted_probability: float, best_bid: Optional[float], best_ask: Optional[float],
                             time_remaining: float):
        if not self.pool:
            return
        try:
            query = """
                INSERT INTO evaluations (market_id, asset, spot_price, threshold, predicted_probability, best_bid, best_ask, time_remaining)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """
            async with self.pool.acquire() as conn:
                await conn.execute(query, market_id, asset, spot_price, threshold, predicted_probability, best_bid, best_ask, time_remaining)
        except Exception as e:
            logger.error(f"Error logging evaluation to database: {e}")

    async def log_signal(self, market_id: str, outcome_id: str, trade_side: str, price: float, amount: float, reason: str):
        if not self.pool:
            return
        try:
            query = """
                INSERT INTO signals (market_id, outcome_id, trade_side, price, amount, reason)
                VALUES ($1, $2, $3, $4, $5, $6)
            """
            async with self.pool.acquire() as conn:
                await conn.execute(query, market_id, outcome_id, trade_side, price, amount, reason)
        except Exception as e:
            logger.error(f"Error logging signal to database: {e}")

    async def log_trade(self, market_id: str, event_id: str, outcome_id: str, outcome_type: str, asset: str, trade_side: str,
                        predicted_probability: float, execution_price: float, amount: float, currency: str,
                        ml_model_used: str, resolution_time: Optional[datetime], threshold: float):
        if not self.pool:
            return
        try:
            # Format resolution_time to timezone-aware if needed
            res_time = resolution_time
            if res_time and res_time.tzinfo is None:
                res_time = res_time.replace(tzinfo=timezone.utc)

            query = """
                INSERT INTO trades (market_id, event_id, outcome_id, outcome_type, asset, trade_side, predicted_probability, 
                                    execution_price, amount, currency, ml_model_used, resolution_time, threshold, is_resolved)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, FALSE)
            """
            async with self.pool.acquire() as conn:
                await conn.execute(query, market_id, event_id, outcome_id, outcome_type, asset, trade_side, predicted_probability,
                                   execution_price, amount, currency, ml_model_used, res_time, threshold)
            logger.info(f"Logged trade to database: {trade_side} {amount} {currency} on {asset} ({market_id})")
        except Exception as e:
            logger.error(f"Error logging trade to database: {e}")

    async def fetch_recent_evaluations(self, asset: str, limit: int = 500) -> List[Dict[str, Any]]:
        """
        Fetches the most recent evaluations for a given asset to use as features for the ML model.
        """
        if not self.pool:
            return []
        try:
            query = """
                SELECT timestamp, spot_price, threshold, predicted_probability, best_bid, best_ask, time_remaining
                FROM evaluations
                WHERE asset = $1
                ORDER BY timestamp DESC
                LIMIT $2
            """
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, asset, limit)
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error fetching recent evaluations for {asset}: {e}")
            return []

    async def fetch_unresolved_trades(self) -> List[Dict[str, Any]]:
        if not self.pool:
            return []
        try:
            query = """
                SELECT id, market_id, event_id, outcome_id, outcome_type, asset, trade_side, predicted_probability, 
                       execution_price, amount, currency, resolution_time, threshold
                FROM trades
                WHERE is_resolved = FALSE
            """
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query)
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error fetching unresolved trades: {e}")
            return []

    async def update_trade_resolution(self, trade_id: int, pnl: float):
        if not self.pool:
            return
        try:
            query = """
                UPDATE trades
                SET pnl = $1, is_resolved = TRUE
                WHERE id = $2
            """
            async with self.pool.acquire() as conn:
                await conn.execute(query, pnl, trade_id)
            logger.info(f"Updated trade {trade_id} resolution: P&L = {pnl:+.2f}")
        except Exception as e:
            logger.error(f"Error updating trade {trade_id} resolution: {e}")

    async def fetch_training_data(self, asset: str) -> List[Dict[str, Any]]:
        """
        Fetches resolved trades and matching evaluation histories to use for retraining the ML model.
        """
        if not self.pool:
            return []
        try:
            # We want to select evaluations that correspond to resolved trades or general resolved events.
            # To make it simple, we can fetch all resolved trades for the asset.
            query = """
                SELECT timestamp, spot_price, threshold, predicted_probability, best_bid, best_ask, time_remaining
                FROM evaluations
                WHERE asset = $1
                ORDER BY timestamp ASC
            """
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, asset)
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error fetching training data: {e}")
            return []

    async def has_recent_trade(self, market_id: str, outcome_id: str, minutes_limit: int = 15) -> bool:
        if not self.pool:
            return False
        try:
            query = """
                SELECT EXISTS (
                    SELECT 1 FROM trades
                    WHERE market_id = $1 AND outcome_id = $2
                      AND (is_resolved = FALSE OR timestamp >= CURRENT_TIMESTAMP - $3 * interval '1 minute')
                )
            """
            async with self.pool.acquire() as conn:
                return await conn.fetchval(query, market_id, outcome_id, minutes_limit)
        except Exception as e:
            logger.error(f"Error checking recent trades in database: {e}")
            return False
