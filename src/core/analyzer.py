import numpy as np
import pandas as pd
from typing import List, Dict, Any, Tuple
import logging

logger = logging.getLogger(__name__)

class MarketAnalyzer:
    """
    Computes Bollinger Bands, RSI, Moving Averages and Market Dip/Pump Scores
    inspired by bybit-ws 9-metric scoring logic.
    """

    @staticmethod
    def calculate_indicators(klines: List[Dict[str, Any]], bb_window: int = 20, bb_std: float = 2.0, rsi_window: int = 14) -> Dict[str, Any]:
        """
        Calculates:
        - Bollinger Bands (Upper, Middle/SMA, Lower)
        - RSI (14)
        - Current Close Price
        - 24h/Window Price Change %
        """
        if not klines or len(klines) < max(bb_window, rsi_window) + 2:
            return {}

        closes = np.array([k["close"] for k in klines], dtype=float)
        current_price = closes[-1]

        # 1. Bollinger Bands
        series = pd.Series(closes)
        sma = series.rolling(window=bb_window).mean()
        std = series.rolling(window=bb_window).std(ddof=0)
        upper_bb = sma + (std * bb_std)
        lower_bb = sma - (std * bb_std)

        curr_sma = float(sma.iloc[-1])
        curr_upper = float(upper_bb.iloc[-1])
        curr_lower = float(lower_bb.iloc[-1])

        # % distance from lower and upper bands
        # bb_pos: 0.0 = at lower band, 1.0 = at upper band, < 0 = below lower band
        band_width = curr_upper - curr_lower
        bb_pos = (current_price - curr_lower) / band_width if band_width > 0 else 0.5

        # 2. RSI Calculation (Wilder's Smoothing)
        deltas = np.diff(closes)
        gains = np.maximum(deltas, 0)
        losses = np.abs(np.minimum(deltas, 0))

        if len(gains) < rsi_window:
            rsi = 50.0
        else:
            avg_gain = np.mean(gains[-rsi_window:])
            avg_loss = np.mean(losses[-rsi_window:])
            if avg_loss == 0:
                rsi = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100.0 - (100.0 / (1.0 + rs))

        # 3. Short-term Price Momentum (% from start of window)
        price_change_pct = ((current_price - closes[0]) / closes[0]) * 100.0

        return {
            "price": current_price,
            "sma": curr_sma,
            "upper_bb": curr_upper,
            "lower_bb": curr_lower,
            "bb_pos": float(bb_pos),
            "rsi": float(rsi),
            "price_change_pct": float(price_change_pct)
        }

    @staticmethod
    def evaluate_dip(analysis: Dict[str, Any], rsi_threshold: float = 38.0) -> Tuple[bool, str, float]:
        """
        Evaluates whether a symbol is in a DIP condition and suggests an order multiplier.
        Returns: (is_dip, dip_tier, multiplier)
        Tiers:
        - 'extreme': Price <= lower_bb * 0.98 and RSI < 25 -> 4.0x
        - 'deep': (Price <= lower_bb or bb_pos < 0.15) and RSI < 32 -> 2.5x
        - 'mild': (bb_pos < 0.25 and RSI < rsi_threshold) -> 1.5x
        - 'none': 1.0x
        """
        if not analysis:
            return False, "none", 1.0

        price = analysis["price"]
        lower_bb = analysis["lower_bb"]
        bb_pos = analysis["bb_pos"]
        rsi = analysis["rsi"]

        # Extreme Dip
        if price <= (lower_bb * 0.98) and rsi < 25.0:
            return True, "extreme", 4.0

        # Deep Dip
        if (price <= lower_bb or bb_pos < 0.15) and rsi < 32.0:
            return True, "deep", 2.5

        # Mild Dip
        if bb_pos < 0.25 and rsi < rsi_threshold:
            return True, "mild", 1.5

        return False, "none", 1.0

    @staticmethod
    def evaluate_harvest(analysis: Dict[str, Any], avg_buy_price: float, min_profit_pct: float = 12.0, rsi_overbought: float = 75.0) -> Tuple[bool, str, float]:
        """
        Evaluates whether we should harvest (take partial profit into USDT reserve).
        Conditions:
        - Price has gained at least `min_profit_pct` over the asset's average acquisition price
        - Market is overextended: RSI > rsi_overbought OR Price >= upper_bb
        Returns: (should_harvest, reason, harvest_pct)
        """
        if not analysis or avg_buy_price <= 0:
            return False, "no_data", 0.0

        price = analysis["price"]
        upper_bb = analysis["upper_bb"]
        rsi = analysis["rsi"]

        current_profit_pct = ((price - avg_buy_price) / avg_buy_price) * 100.0

        # Must meet minimum profit requirement
        if current_profit_pct < min_profit_pct:
            return False, "profit_below_target", 0.0

        # Super Pump / Extreme Overbought
        if price >= upper_bb and rsi >= rsi_overbought:
            return True, f"Extreme pump: profit={current_profit_pct:.1f}%, RSI={rsi:.1f}, above upper BB", 20.0

        # Overbought condition
        if rsi >= rsi_overbought:
            return True, f"RSI overbought: profit={current_profit_pct:.1f}%, RSI={rsi:.1f}", 15.0

        # Pierce through Upper Bollinger Band with solid profit
        if price >= (upper_bb * 1.01):
            return True, f"Bollinger breakout: profit={current_profit_pct:.1f}%, BB upper pierced", 15.0

        return False, "normal_conditions", 0.0
