import asyncio
import logging
from typing import Optional, Dict, Any, Callable
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from src.core.database import Database

logger = logging.getLogger(__name__)

class TelegramBotManager:
    """
    Interactive Telegram Manager:
    - Real-time formatted alerts with inline action buttons
    - Interactive commands: /status, /balance, /pause, /resume, /buy, /harvest
    - Callback query routing to Engine actions
    """
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None, engine = None):
        self.bot_token = bot_token
        self.chat_id = str(chat_id) if chat_id else None
        self.engine = engine
        self.enabled = bool(bot_token and chat_id and "your_" not in bot_token)
        self.app: Optional[Application] = None

    async def start(self):
        """Initializes and starts the interactive Telegram polling bot if token is present."""
        if not self.enabled:
            logger.info("Telegram bot not configured (set TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID in .env).")
            return

        try:
            self.app = Application.builder().token(self.bot_token).build()

            # Handlers
            self.app.add_handler(CommandHandler("start", self._cmd_start))
            self.app.add_handler(CommandHandler("status", self._cmd_status))
            self.app.add_handler(CommandHandler("pause", self._cmd_pause))
            self.app.add_handler(CommandHandler("resume", self._cmd_resume))
            self.app.add_handler(CallbackQueryHandler(self._handle_callback))

            await self.app.initialize()
            await self.app.start()
            await self.app.updater.start_polling(drop_pending_updates=True)
            logger.info("Interactive Telegram bot polling started successfully!")

            # Send greetings
            await self.send_message(
                "🐷 <b>Bybit Smart Piggybank Online!</b>\n"
                "Interactive management enabled. Use /status for live overview.",
                reply_markup=self._get_quick_actions_keyboard()
            )
        except Exception as e:
            logger.error(f"Failed to start interactive Telegram bot: {e}")

    async def stop(self):
        if self.app and self.app.updater:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    def _is_authorized(self, update: Update) -> bool:
        if not self.chat_id:
            return True
        user_chat_id = str(update.effective_chat.id) if update.effective_chat else ""
        return user_chat_id == self.chat_id

    def _get_quick_actions_keyboard(self) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("📊 Обзор портфеля", callback_data="action_status"),
                InlineKeyboardButton("⚡ DCA BTC $15", callback_data="action_buy_BTC")
            ],
            [
                InlineKeyboardButton("⚡ DCA ETH $10", callback_data="action_buy_ETH"),
                InlineKeyboardButton("⚡ DCA SOL $5", callback_data="action_buy_SOL")
            ],
            [
                InlineKeyboardButton("⏸️ Пауза", callback_data="action_pause"),
                InlineKeyboardButton("▶️ Старт", callback_data="action_resume")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        msg = (
            "🐷 <b>Bybit Smart Piggybank Control Panel</b>\n\n"
            "Доступные команды:\n"
            "• /status — текущий баланс, PnL и показания индикаторов\n"
            "• /pause — экстренная приостановка покупок\n"
            "• /resume — возобновление работы\n"
        )
        await update.message.reply_html(msg, reply_markup=self._get_quick_actions_keyboard())

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        status_text = await self._generate_status_text()
        await update.message.reply_html(status_text, reply_markup=self._get_quick_actions_keyboard())

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        if self.engine:
            self.engine.risk_manager.circuit_breaker_active = True
            self.engine.risk_manager.circuit_breaker_until = 9999999999
        await update.message.reply_html("⏸️ <b>Авто-покупки приостановлены оператором!</b>", reply_markup=self._get_quick_actions_keyboard())

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        if self.engine:
            self.engine.risk_manager.circuit_breaker_active = False
            self.engine.risk_manager.circuit_breaker_until = 0
        await update.message.reply_html("▶️ <b>Автономные закупки возобновлены!</b>", reply_markup=self._get_quick_actions_keyboard())

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not self._is_authorized(update):
            await query.answer("Не авторизован", show_alert=True)
            return

        await query.answer()
        data = query.data

        if data == "action_status":
            text = await self._generate_status_text()
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=self._get_quick_actions_keyboard())

        elif data.startswith("action_buy_"):
            coin = data.replace("action_buy_", "")
            sym = f"{coin}USDT"
            amounts = {"BTC": 15.0, "ETH": 10.0, "SOL": 5.0}
            amt = amounts.get(coin, 10.0)

            if not self.engine:
                await query.message.reply_html("Ошибка: Engine не привязан")
                return

            wallet = await self.engine.bybit.get_wallet_balance()
            can_buy, reason = await self.engine.risk_manager.can_execute_buy(amt, wallet.get("USDT", 0.0))
            if not can_buy:
                await query.message.reply_html(f"❌ <b>Покупка отклонена Risk Manager:</b>\n{reason}")
                return

            res = await self.engine.bybit.execute_market_buy(sym, amt)
            if res.get("success"):
                order_data = {
                    "order_id": res["order_id"],
                    "symbol": sym,
                    "side": "BUY",
                    "amount_usd": amt,
                    "qty": res["qty"],
                    "price": res["price"],
                    "strategy": "TELEGRAM_BUTTON_BUY",
                    "details": "Triggered via Telegram button",
                    "is_simulated": res["is_simulated"]
                }
                await Database.record_order(order_data)
                await query.message.reply_html(
                    f"✅ <b>Успешно куплен {coin}!</b>\n"
                    f"Сумма: ${amt:.2f} USDT\n"
                    f"Цена: ${res['price']:,.2f}\n"
                    f"Объем: {res['qty']:.6f}"
                )
            else:
                await query.message.reply_html(f"❌ Ошибка Bybit: {res.get('error')}")

        elif data == "action_pause":
            if self.engine:
                self.engine.risk_manager.circuit_breaker_active = True
                self.engine.risk_manager.circuit_breaker_until = 9999999999
            await query.message.reply_html("⏸️ <b>Авто-покупки приостановлены!</b>")

        elif data == "action_resume":
            if self.engine:
                self.engine.risk_manager.circuit_breaker_active = False
                self.engine.risk_manager.circuit_breaker_until = 0
            await query.message.reply_html("▶️ <b>Автономные закупки возобновлены!</b>")

    async def _generate_status_text(self) -> str:
        if not self.engine:
            return "Engine не инициализирован"

        wallet = await self.engine.bybit.get_wallet_balance()
        positions = await Database.get_all_positions()
        daily_spent = await Database.get_daily_spent_usd()
        tickers = await self.engine.bybit.get_tickers()

        mode = "🧪 Simulation" if self.engine.bybit.dry_run else "🟢 Live Spot"
        cb_status = "⚠️ PAUSED" if self.engine.risk_manager.circuit_breaker_active else "✅ Healthy"

        text = (
            f"🐷 <b>Bybit Smart Piggybank Status</b>\n"
            f"<b>Режим:</b> {mode}\n"
            f"<b>Защита:</b> {cb_status}\n"
            f"<b>Резерв USDT:</b> <code>${wallet.get('USDT', 0.0):.2f}</code>\n"
            f"<b>Потрачено за 24ч:</b> <code>${daily_spent:.2f} / ${self.engine.risk_manager.max_daily_spend:.2f}</code>\n\n"
            f"<b>Портфель:</b>\n"
        )

        for coin, details in self.engine.config.get("portfolio", {}).get("assets", {}).items():
            qty = wallet.get(coin, 0.0)
            price = tickers.get(f"{coin}USDT", 0.0)
            pos = positions.get(coin, {})
            avg_price = pos.get("avg_buy_price", 0.0)
            pnl_pct = ((price - avg_price) / avg_price * 100.0) if avg_price > 0 else 0.0
            pnl_sign = "+" if pnl_pct >= 0 else ""

            text += (
                f"• <b>{coin}:</b> {qty:.5f} (${qty*price:.2f})\n"
                f"  Цена: ${price:,.2f} | Ср: ${avg_price:,.2f} ({pnl_sign}{pnl_pct:.1f}%)\n"
            )

        return text

    async def send_message(self, text: str, reply_markup=None, parse_mode: str = "HTML") -> bool:
        if not self.enabled or not self.app:
            logger.info(f"[TELEGRAM LOG]: {text}")
            return True

        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def notify_buy(self, symbol: str, strategy: str, amount_usd: float, qty: float, price: float, details: str = "", is_sim: bool = False):
        prefix = "🧪 [SIMULATED BUY]" if is_sim else "🟢 [PIGGYBANK BUY]"
        coin = symbol.replace("USDT", "")
        msg = (
            f"<b>{prefix}</b>\n"
            f"<b>Актив:</b> <code>{symbol}</code>\n"
            f"<b>Стратегия:</b> <code>{strategy}</code>\n"
            f"<b>Сумма:</b> <b>${amount_usd:.2f} USDT</b>\n"
            f"<b>Количество:</b> {qty:.6f}\n"
            f"<b>Цена:</b> ${price:,.2f}\n"
            f"<i>{details}</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 Статус", callback_data="action_status"),
                InlineKeyboardButton(f"💰 Фикс 15% {coin}", callback_data=f"action_harvest_{coin}")
            ]
        ])
        await self.send_message(msg, reply_markup=keyboard)

    async def notify_harvest(self, symbol: str, profit_pct: float, revenue_usd: float, qty: float, price: float, details: str = "", is_sim: bool = False):
        prefix = "🧪 [SIMULATED HARVEST]" if is_sim else "💰 [PROFIT HARVESTED!]"
        msg = (
            f"<b>{prefix}</b>\n"
            f"<b>Актив:</b> <code>{symbol}</code>\n"
            f"<b>Профит:</b> <b>+{profit_pct:.1f}%</b>\n"
            f"<b>Продано:</b> {qty:.6f} (+${revenue_usd:.2f} в USDT резерв)\n"
            f"<b>Цена:</b> ${price:,.2f}\n"
            f"<i>{details}</i>"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Статус", callback_data="action_status")]])
        await self.send_message(msg, reply_markup=keyboard)

    async def notify_alert(self, title: str, text: str):
        msg = f"⚠️ <b>{title}</b>\n{text}"
        await self.send_message(msg)
