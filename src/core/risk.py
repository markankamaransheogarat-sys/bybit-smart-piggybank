import time
import logging
from typing import Dict, Any, Tuple
from src.core.database import Database

logger = logging.getLogger(__name__)

class RiskManager:
    """
    Guards the piggybank capital:
    1. Circuit Breaker (pauses buys if BTC/market is crashing precipitously, preventing falling knives).
    2. Daily Spend Limits (prevents depleting the reserve in a single day).
    3. Minimum Reserve Threshold (guarantees a floor of USDT stays liquid).
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.risk_cfg = config.get("risk_management", {})
        self.max_daily_spend = float(self.risk_cfg.get("max_daily_spend_usd", 150.0))
        self.min_usdt_reserve = float(self.risk_cfg.get("min_usdt_reserve_usd", 20.0))
        
        cb_cfg = self.risk_cfg.get("circuit_breaker", {})
        self.cb_enabled = cb_cfg.get("enabled", True)
        self.market_crash_threshold = float(cb_cfg.get("market_crash_threshold_pct", -10.0))
        self.pause_duration_sec = int(cb_cfg.get("pause_duration_hours", 12)) * 3600

        self.circuit_breaker_active = False
        self.circuit_breaker_until = 0

    def check_market_circuit_breaker(self, btc_analysis: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if Bitcoin has crashed beyond threshold in the analyzed window.
        """
        now = time.time()
        if self.circuit_breaker_active:
            if now < self.circuit_breaker_until:
                remaining_min = int((self.circuit_breaker_until - now) / 60)
                return False, f"Circuit Breaker ACTIVE (resumes in {remaining_min}m)"
            else:
                self.circuit_breaker_active = False
                logger.info("Circuit breaker expired. Resuming normal operations.")

        if not self.cb_enabled or not btc_analysis:
            return True, "OK"

        change_pct = btc_analysis.get("price_change_pct", 0.0)
        if change_pct <= self.market_crash_threshold:
            self.circuit_breaker_active = True
            self.circuit_breaker_until = now + self.pause_duration_sec
            msg = f"BLACK SWAN / CIRCUIT BREAKER TRIGGERED! BTC 24h change is {change_pct:.2f}%. Pausing buys for {self.pause_duration_sec//3600}h."
            logger.warning(msg)
            return False, msg

        return True, "OK"

    async def can_execute_buy(self, amount_usd: float, current_usdt_balance: float) -> Tuple[bool, str]:
        """
        Verify daily spend cap and reserve floor.
        """
        if self.circuit_breaker_active:
            return False, "Circuit Breaker is active"

        # Check reserve floor
        if (current_usdt_balance - amount_usd) < self.min_usdt_reserve:
            return False, f"Violates minimum USDT reserve floor (${self.min_usdt_reserve:.2f}). Balance: ${current_usdt_balance:.2f}"

        # Check daily spend
        daily_spent = await Database.get_daily_spent_usd()
        if (daily_spent + amount_usd) > self.max_daily_spend:
            return False, f"Exceeds max daily spend (${self.max_daily_spend:.2f}). Already spent today: ${daily_spent:.2f}"

        return True, "OK"
