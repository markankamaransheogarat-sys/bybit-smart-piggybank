import os
import sys
import yaml
import asyncio
import logging
import uvicorn
from dotenv import load_dotenv

from src.exchange.bybit_client import BybitSpotClient
from src.notifications.telegram_bot import TelegramBotManager
from src.core.engine import PiggybankEngine
from src.web.app import create_app
from src.mcp.server import PiggybankMCPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("PiggybankMain")

def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "config", "config.yaml")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

async def main():
    load_dotenv()
    config = load_config()

    api_key = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
    dry_run = config.get("app", {}).get("dry_run", True)

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    logger.info("==================================================")
    logger.info("   🚀 BYBIT SMART PIGGYBANK (AUTONOMOUS ENGINE)   ")
    logger.info(f"   Mode: {'🧪 SIMULATION / DRY-RUN' if dry_run else '🟢 LIVE TRADING'}")
    logger.info("==================================================")

    bybit = BybitSpotClient(api_key=api_key, api_secret=api_secret, testnet=testnet, dry_run=dry_run)
    notifier = TelegramBotManager(bot_token=tg_token, chat_id=tg_chat_id)
    engine = PiggybankEngine(config=config, bybit=bybit, notifier=notifier)
    notifier.engine = engine  # link engine for interactive bot commands
    mcp_server = PiggybankMCPServer(engine)

    # FastAPI Web Dashboard & REST/MCP endpoints
    app = create_app(engine)

    # Attach MCP endpoints to FastAPI for AI Agent interactions
    @app.get("/api/mcp/tools")
    async def get_mcp_tools():
        return mcp_server.get_tool_definitions()

    @app.post("/api/mcp/call")
    async def call_mcp_tool(call_data: dict):
        tool_name = call_data.get("name")
        args = call_data.get("arguments", {})
        return await mcp_server.handle_tool_call(tool_name, args)

    # Run Uvicorn server in background task
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    server_config = uvicorn.Config(app=app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(server_config)

    dashboard_task = asyncio.create_task(server.serve())
    engine_task = asyncio.create_task(engine.start())
    tg_task = asyncio.create_task(notifier.start())

    logger.info(f"Dashboard available at: http://{host}:{port}")

    try:
        await asyncio.gather(dashboard_task, engine_task, tg_task)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutting down...")
        engine.stop()
        await notifier.stop()

if __name__ == "__main__":
    asyncio.run(main())
