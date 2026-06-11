import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from database import DatabaseManager

logger = logging.getLogger("BayseBot.MLPredictor")

class BitcoinMLPredictor:
    def __init__(self):
        self.model: Optional[Any] = None
        self.is_trained = False
        self.min_training_samples = 100
        self.features_columns = [
            "spot_price", "threshold", "time_remaining_hours", 
            "distance_pct", "normalized_diff", 
            "momentum_5", "momentum_15", "volatility_15"
        ]

    def _extract_features(self, spot_price: float, threshold: float, time_remaining_seconds: float,
                          recent_prices: List[float]) -> np.ndarray:
        """
        Extracts mathematical and statistical features for prediction.
        """
        time_remaining_hours = max(0.0, time_remaining_seconds / 3600.0)
        distance_pct = (spot_price - threshold) / (threshold + 1e-8)
        
        # Volatility approximation using Black-Scholes normalized diff
        # standard daily volatility assumed 0.50 (50%)
        t_years = max(1e-6, time_remaining_seconds / (365.0 * 24.0 * 3600.0))
        vol = 0.50
        normalized_diff = (spot_price - threshold) / (vol * spot_price * np.sqrt(t_years) + 1e-8)

        # Lags and rolling metrics from recent prices
        prices = np.array(recent_prices) if recent_prices else np.array([spot_price])
        if len(prices) < 2:
            prices = np.array([spot_price] * 20)

        # Pad prices to length of at least 20 if needed
        if len(prices) < 20:
            prices = np.pad(prices, (20 - len(prices), 0), mode='edge')

        # Momentum calculations
        momentum_5 = (spot_price - prices[-5]) / (prices[-5] + 1e-8)
        momentum_15 = (spot_price - prices[-15]) / (prices[-15] + 1e-8)
        
        # Volatility over last 15 ticks
        volatility_15 = np.std(prices[-15:]) / (np.mean(prices[-15:]) + 1e-8)

        feat_vector = np.array([
            spot_price, threshold, time_remaining_hours,
            distance_pct, normalized_diff,
            momentum_5, momentum_15, volatility_15
        ])
        return feat_vector

    async def train(self, db_manager: DatabaseManager):
        """
        Queries the database for historical evaluations, self-labels them using the resolution query,
        and trains a RandomForestClassifier.
        """
        if not db_manager.pool:
            logger.warning("Database not initialized. Skipping ML training.")
            return

        logger.info("Starting ML model retraining process...")
        try:
            # Query for labeled historical evaluation records
            query = """
                SELECT 
                    e1.spot_price,
                    e1.threshold,
                    e1.time_remaining,
                    e1.timestamp,
                    -- Subquery to find spot price at resolution timestamp
                    COALESCE(
                        (SELECT CASE WHEN e2.spot_price >= e1.threshold THEN 1 ELSE 0 END
                         FROM evaluations e2
                         WHERE e2.asset = e1.asset 
                           AND e2.timestamp >= e1.timestamp + (e1.time_remaining * interval '1 second') - interval '1 minute'
                           AND e2.timestamp <= e1.timestamp + (e1.time_remaining * interval '1 second') + interval '1 minute'
                         ORDER BY ABS(EXTRACT(EPOCH FROM (e2.timestamp - (e1.timestamp + (e1.time_remaining * interval '1 second'))))) ASC
                         LIMIT 1),
                        -1
                    ) AS label
                FROM evaluations e1
                WHERE e1.asset IN ('BTC', 'BTCUSDT', 'BTCUSD')
                  AND e1.time_remaining > 0
                  AND e1.timestamp + (e1.time_remaining * interval '1 second') < CURRENT_TIMESTAMP
            """
            
            async with db_manager.pool.acquire() as conn:
                rows = await conn.fetch(query)

            # Filter out records where resolution could not be labeled (label = -1)
            valid_rows = [r for r in rows if r["label"] != -1]
            logger.info(f"Retrieved {len(rows)} raw records. Valid labeled training samples: {len(valid_rows)}")

            if len(valid_rows) < self.min_training_samples:
                logger.info(f"Insufficient training data. Need at least {self.min_training_samples} samples (current: {len(valid_rows)}). Model cold-starting (falling back to math formula).")
                self.is_trained = False
                return

            # Construct features and target arrays
            X_list = []
            y_list = []

            # We need historical price feeds to compute rolling lags. 
            # To do this accurately, we fetch all evaluations in chronological order.
            eval_query = """
                SELECT timestamp, spot_price 
                FROM evaluations 
                WHERE asset IN ('BTC', 'BTCUSDT', 'BTCUSD') 
                ORDER BY timestamp ASC
            """
            async with db_manager.pool.acquire() as conn:
                price_rows = await conn.fetch(eval_query)
            
            prices_df = pd.DataFrame(price_rows)
            prices_df["timestamp"] = pd.to_datetime(prices_df["timestamp"])
            
            for row in valid_rows:
                row_time = pd.to_datetime(row["timestamp"])
                # Extract prices prior to this evaluation timestamp
                prior_prices = prices_df[prices_df["timestamp"] < row_time].tail(20)["spot_price"].tolist()
                
                feat = self._extract_features(
                    float(row["spot_price"]), 
                    float(row["threshold"]), 
                    float(row["time_remaining"]), 
                    prior_prices
                )
                X_list.append(feat)
                y_list.append(row["label"])

            X = np.array(X_list)
            y = np.array(y_list)

            # Try to train XGBoost, fall back to RandomForest
            try:
                from xgboost import XGBClassifier
                logger.info("Training XGBoost Classifier...")
                model = XGBClassifier(
                    n_estimators=100,
                    max_depth=4,
                    learning_rate=0.05,
                    random_state=42,
                    eval_metric="logloss"
                )
                model.fit(X, y)
                self.model = model
                logger.info("XGBoost Classifier trained successfully.")
            except Exception as e:
                logger.warning(f"Failed to import/train XGBoost ({e}). Falling back to RandomForestClassifier.")
                from sklearn.ensemble import RandomForestClassifier
                model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
                model.fit(X, y)
                self.model = model
                logger.info("RandomForestClassifier trained successfully.")

            self.is_trained = True
            logger.info("Machine Learning model training completed and active.")

        except Exception as e:
            logger.error(f"Error during ML model training loop: {e}")
            self.is_trained = False

    def predict_probability(self, spot_price: float, threshold: float, time_remaining_seconds: float,
                            recent_prices: List[float]) -> float:
        """
        Calculates price directional probability using the trained classifier.
        Returns a probability value strictly between 0.01 and 0.99.
        """
        if not self.is_trained or self.model is None:
            raise RuntimeError("Model is not trained.")

        feat = self._extract_features(spot_price, threshold, time_remaining_seconds, recent_prices)
        feat_reshaped = feat.reshape(1, -1)

        # Get probability of class 1 (UP)
        probs = self.model.predict_proba(feat_reshaped)[0]
        # Class 1 probability is index 1
        prob_up = float(probs[1])

        # Clamp between 0.01 and 0.99
        return max(0.01, min(prob_up, 0.99))
