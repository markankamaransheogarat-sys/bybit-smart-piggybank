import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """
    Sends structured trade notifications, daily summaries, and alerts to Telegram.
    """
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id and "your_" not in bot_token)

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            logger.info(f"[TELEGRAM SIMULATION]: {text}")
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(url, json=payload)
                return res.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            return False

    async def notify_buy(self, symbol: str, strategy: str, amount_usd: float, qty: float, price: float, details: str = "", is_sim: bool = False):
        prefix = "🧪 [SIMULATED BUY]" if is_sim else "🟢 [PIGGYBANK BUY]"
        msg = (
            f"<b>{prefix}</b>\n"
            f"<b>Asset:</b> <code>{symbol}</code>\n"
            f"<b>Strategy:</b> <code>{strategy}</code>\n"
            f"<b>Amount:</b> <b>${amount_usd:.2f} USDT</b>\n"
            f"<b>Qty:</b> {qty:.6f}\n"
            f"<b>Price:</b> ${price:,.2f}\n"
            f"<i>{details}</i>"
        )
        await self.send_message(msg)

    async def notify_harvest(self, symbol: str, profit_pct: float, revenue_usd: float, qty: float, price: float, details: str = "", is_sim: bool = False):
        prefix = "🧪 [SIMULATED HARVEST]" if is_sim else "💰 [PROFIT HARVESTED!]"
        msg = (
            f"<b>{prefix}</b>\n"
            f"<b>Asset:</b> <code>{symbol}</code>\n"
            f"<b>Profit:</b> <b>+{profit_pct:.1f}%</b>\n"
            f"<b>Sold Qty:</b> {qty:.6f} (${revenue_usd:.2f} USDT added to reserve)\n"
            f"<b>Price:</b> ${price:,.2f}\n"
            f"<i>{details}</i>"
        )
        await self.send_message(msg)

    async def notify_alert(self, title: str, text: str):
        msg = f"⚠️ <b>{title}</b>\n{text}"
        await self.send_message(msg)
