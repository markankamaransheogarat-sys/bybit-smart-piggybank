import asyncio
import time
import logging
from typing import Dict, Any
from src.exchange.bybit_client import BybitSpotClient
from src.core.analyzer import MarketAnalyzer
from src.core.risk import RiskManager
from src.core.database import Database
from src.notifications.telegram_bot import TelegramBotManager

logger = logging.getLogger(__name__)

class PiggybankEngine:
    """
    Main Autonomous Accumulation Engine:
    - 30-60s fast loop for checks & execution
    - Periodic Scheduled DCA
    - AI / Technical Dip-Buying
    - Profit Harvester / Rebalancing into USDT
    """
    def __init__(self, config: Dict[str, Any], bybit: BybitSpotClient, notifier: TelegramBotManager):
        self.config = config
        self.bybit = bybit
        self.notifier = notifier
        self.risk_manager = RiskManager(config)

        self.is_running = False
        self.cycle_count = 0
        self.last_scheduled_dca = 0
        self.last_dip_buy_time: Dict[str, float] = {}
        self.last_harvest_time: Dict[str, float] = {}

        # Cache of latest market analysis
        self.latest_analysis: Dict[str, Dict[str, Any]] = {}

    async def start(self):
        self.is_running = True
        logger.info("Starting Piggybank Engine...")
        await Database.init_db()

        check_interval = int(self.config.get("app", {}).get("check_interval_sec", 60))

        while self.is_running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Error in engine tick: {e}", exc_info=True)

            await asyncio.sleep(check_interval)

    def stop(self):
        self.is_running = False
        logger.info("Piggybank Engine stopped.")

    async def _tick(self):
        self.cycle_count += 1
        now = time.time()
        heavy_cycles = int(self.config.get("app", {}).get("heavy_analysis_cycles", 5))

        # 1. Update Market Candles and Indicators
        await self._update_market_data()

        # 2. Check BTC Circuit Breaker
        btc_analysis = self.latest_analysis.get("BTCUSDT", {})
        cb_ok, cb_msg = self.risk_manager.check_market_circuit_breaker(btc_analysis)
        if not cb_ok:
            logger.warning(f"Engine tick skipped: {cb_msg}")
            return

        # 3. Strategy 3: Profit Harvester (Check every cycle for pump exits)
        await self._check_profit_harvesting(now)

        # 4. Strategy 2: Dip-Buying (Evaluate every heavy cycle, e.g. ~5 min)
        if self.cycle_count % heavy_cycles == 0:
            await self._check_smart_dip_buying(now)

        # 5. Strategy 1: Scheduled DCA (Periodic base accumulation)
        dca_interval_sec = int(self.config.get("scheduled_dca", {}).get("interval_hours", 24)) * 3600
        if self.config.get("scheduled_dca", {}).get("enabled", True):
            if (now - self.last_scheduled_dca) >= dca_interval_sec:
                await self._execute_scheduled_dca()
                self.last_scheduled_dca = now

    async def _update_market_data(self):
        assets = self.config.get("portfolio", {}).get("assets", {})
        timeframe = self.config.get("dip_buying", {}).get("timeframe", "1h")
        tf_map = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}
        interval = tf_map.get(timeframe, "60")

        for coin, details in assets.items():
            sym = details.get("symbol", f"{coin}USDT")
            klines = await self.bybit.get_klines(sym, interval=interval, limit=50)
            analysis = MarketAnalyzer.calculate_indicators(
                klines,
                bb_window=self.config.get("dip_buying", {}).get("bollinger_window", 20),
                bb_std=self.config.get("dip_buying", {}).get("bollinger_std", 2.0),
                rsi_window=self.config.get("dip_buying", {}).get("rsi_window", 14)
            )
            if analysis:
                self.latest_analysis[sym] = analysis

    async def _check_smart_dip_buying(self, now: float):
        if not self.config.get("dip_buying", {}).get("enabled", True):
            return

        assets = self.config.get("portfolio", {}).get("assets", {})
        cooldown_sec = int(self.config.get("dip_buying", {}).get("cooldown_hours", 4)) * 3600
        rsi_thresh = float(self.config.get("dip_buying", {}).get("rsi_dip_threshold", 38.0))

        wallet = await self.bybit.get_wallet_balance()
        current_usdt = wallet.get("USDT", 0.0)

        for coin, details in assets.items():
            sym = details.get("symbol", f"{coin}USDT")
            analysis = self.latest_analysis.get(sym)
            if not analysis:
                continue

            # Check asset dip cooldown
            if (now - self.last_dip_buy_time.get(sym, 0.0)) < cooldown_sec:
                continue

            is_dip, dip_tier, multiplier = MarketAnalyzer.evaluate_dip(analysis, rsi_threshold=rsi_thresh)
            if is_dip:
                base_usd = float(details.get("base_dca_usd", 10.0))
                buy_amount_usd = base_usd * multiplier

                # Validate with risk manager
                can_buy, reason = await self.risk_manager.can_execute_buy(buy_amount_usd, current_usdt)
                if not can_buy:
                    logger.info(f"Skipping dip buy for {sym}: {reason}")
                    continue

                logger.info(f"Triggering {dip_tier.upper()} dip buy for {sym} (${buy_amount_usd:.2f})")
                res = await self.bybit.execute_market_buy(sym, buy_amount_usd)
                if res.get("success"):
                    self.last_dip_buy_time[sym] = now
                    current_usdt -= buy_amount_usd
                    order_data = {
                        "order_id": res["order_id"],
                        "symbol": sym,
                        "side": "BUY",
                        "amount_usd": buy_amount_usd,
                        "qty": res["qty"],
                        "price": res["price"],
                        "strategy": f"DIP_BUY_{dip_tier.upper()}",
                        "details": f"Multiplier {multiplier}x | RSI {analysis['rsi']:.1f} | BB pos {analysis['bb_pos']:.2f}",
                        "is_simulated": res["is_simulated"]
                    }
                    await Database.record_order(order_data)
                    await self.notifier.notify_buy(
                        symbol=sym,
                        strategy=f"SMART DIP ({dip_tier.upper()})",
                        amount_usd=buy_amount_usd,
                        qty=res["qty"],
                        price=res["price"],
                        details=order_data["details"],
                        is_sim=res["is_simulated"]
                    )

    async def _check_profit_harvesting(self, now: float):
        if not self.config.get("profit_harvester", {}).get("enabled", True):
            return

        assets = self.config.get("portfolio", {}).get("assets", {})
        h_cfg = self.config.get("profit_harvester", {})
        min_profit = float(h_cfg.get("min_profit_pct", 12.0))
        rsi_overbought = float(h_cfg.get("rsi_overbought", 75.0))
        cooldown_sec = int(h_cfg.get("cooldown_hours", 8)) * 3600

        wallet = await self.bybit.get_wallet_balance()

        for coin, details in assets.items():
            sym = details.get("symbol", f"{coin}USDT")
            analysis = self.latest_analysis.get(sym)
            if not analysis:
                continue

            if (now - self.last_harvest_time.get(sym, 0.0)) < cooldown_sec:
                continue

            pos = await Database.get_position(coin)
            avg_price = pos.get("avg_buy_price", 0.0)
            curr_qty = wallet.get(coin, 0.0)

            # In simulation, fallback to DB qty if wallet has 0
            if self.bybit.dry_run and curr_qty <= 0:
                curr_qty = pos.get("total_qty", 0.0)

            if avg_price <= 0 or curr_qty <= 0:
                continue

            should_harvest, reason, harvest_pct = MarketAnalyzer.evaluate_harvest(
                analysis, avg_buy_price=avg_price, min_profit_pct=min_profit, rsi_overbought=rsi_overbought
            )

            if should_harvest:
                sell_qty = curr_qty * (harvest_pct / 100.0)
                current_price = analysis["price"]
                expected_usd = sell_qty * current_price

                if expected_usd < float(details.get("min_order_usd", 5.0)):
                    continue

                profit_pct = ((current_price - avg_price) / avg_price) * 100.0
                logger.info(f"Harvesting profit for {coin}: selling {sell_qty:.6f} {coin} (+{profit_pct:.1f}%)")

                res = await self.bybit.execute_market_sell(sym, sell_qty)
                if res.get("success"):
                    self.last_harvest_time[sym] = now
                    order_data = {
                        "order_id": res["order_id"],
                        "symbol": sym,
                        "side": "SELL",
                        "amount_usd": res["amount_usd"],
                        "qty": res["qty"],
                        "price": res["price"],
                        "strategy": "PROFIT_HARVEST",
                        "details": f"Profit +{profit_pct:.1f}% | {reason}",
                        "is_simulated": res["is_simulated"]
                    }
                    await Database.record_order(order_data)
                    await self.notifier.notify_harvest(
                        symbol=sym,
                        profit_pct=profit_pct,
                        revenue_usd=res["amount_usd"],
                        qty=res["qty"],
                        price=res["price"],
                        details=order_data["details"],
                        is_sim=res["is_simulated"]
                    )

    async def _execute_scheduled_dca(self):
        assets = self.config.get("portfolio", {}).get("assets", {})
        wallet = await self.bybit.get_wallet_balance()
        current_usdt = wallet.get("USDT", 0.0)

        for coin, details in assets.items():
            sym = details.get("symbol", f"{coin}USDT")
            base_usd = float(details.get("base_dca_usd", 10.0))

            can_buy, reason = await self.risk_manager.can_execute_buy(base_usd, current_usdt)
            if not can_buy:
                logger.warning(f"Scheduled DCA skipped for {sym}: {reason}")
                continue

            res = await self.bybit.execute_market_buy(sym, base_usd)
            if res.get("success"):
                current_usdt -= base_usd
                order_data = {
                    "order_id": res["order_id"],
                    "symbol": sym,
                    "side": "BUY",
                    "amount_usd": base_usd,
                    "qty": res["qty"],
                    "price": res["price"],
                    "strategy": "SCHEDULED_DCA",
                    "details": "Routine portfolio accumulation",
                    "is_simulated": res["is_simulated"]
                }
                await Database.record_order(order_data)
                await self.notifier.notify_buy(
                    symbol=sym,
                    strategy="SCHEDULED DCA",
                    amount_usd=base_usd,
                    qty=res["qty"],
                    price=res["price"],
                    details="Scheduled accumulation",
                    is_sim=res["is_simulated"]
                )
