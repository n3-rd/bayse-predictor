import os
import sys
import logging
import asyncio
import json
from aiohttp import web

import config
from main import BaysePredictorBot, log_handler
from copy_trade_runner import CopyTradeRunner

# Add the log_handler from main to capture all BayseBot logs in the shared buffer
logging.getLogger().addHandler(log_handler)
logger = logging.getLogger("BayseBot.WebManager")

class WebManager:
    def __init__(self):
        self.predictor_bot = BaysePredictorBot()
        self.copy_trade_runner = CopyTradeRunner()
        self.port = config.DASHBOARD_PORT
        self.runner = None
        self.site = None

    async def start_server(self):
        app = web.Application()
        
        # API Control Endpoints
        app.router.add_post("/api/control/predictor/start", self.handle_start_predictor)
        app.router.add_post("/api/control/predictor/stop", self.handle_stop_predictor)
        app.router.add_post("/api/control/copytrader/start", self.handle_start_copytrader)
        app.router.add_post("/api/control/copytrader/stop", self.handle_stop_copytrader)
        app.router.add_get("/api/status", self.handle_status)
        
        # Serve frontend dashboard static files
        static_path = os.path.join(os.path.dirname(__file__), "dashboard")
        if os.path.exists(static_path):
            async def handle_index(request):
                return web.FileResponse(os.path.join(static_path, "index.html"))
            app.router.add_get("/", handle_index)
            app.router.add_static("/", static_path)
            logger.info(f"Serving dashboard static files from {static_path}")
        else:
            logger.warning(f"Dashboard directory not found at {static_path}.")

        self.runner = web.AppRunner(app)
        await self.runner.setup()
        # Bind to 0.0.0.0 so it is accessible on VPS
        self.site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await self.site.start()
        logger.info(f"Unified Web Manager started and listening on http://0.0.0.0:{self.port}")

    async def stop_server(self):
        logger.info("Stopping all bots and Web Manager server...")
        if self.predictor_bot.is_running:
            await self.predictor_bot.stop()
        if self.copy_trade_runner.is_running:
            await self.copy_trade_runner.stop()
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("Web Manager stopped.")

    async def handle_start_predictor(self, request):
        if self.predictor_bot.is_running:
            return web.json_response({"status": "already_running"})
        logger.info("[USER TRIGGER] Starting Prediction Bot...")
        asyncio.create_task(self.predictor_bot.start())
        return web.json_response({"status": "starting"})

    async def handle_stop_predictor(self, request):
        if not self.predictor_bot.is_running:
            return web.json_response({"status": "already_stopped"})
        logger.info("[USER TRIGGER] Stopping Prediction Bot...")
        await self.predictor_bot.stop()
        return web.json_response({"status": "stopped"})

    async def handle_start_copytrader(self, request):
        if self.copy_trade_runner.is_running:
            return web.json_response({"status": "already_running"})
        logger.info("[USER TRIGGER] Starting Copy Trading Bot...")
        asyncio.create_task(self.copy_trade_runner.start())
        return web.json_response({"status": "starting"})

    async def handle_stop_copytrader(self, request):
        if not self.copy_trade_runner.is_running:
            return web.json_response({"status": "already_stopped"})
        logger.info("[USER TRIGGER] Stopping Copy Trading Bot...")
        await self.copy_trade_runner.stop()
        return web.json_response({"status": "stopped"})

    async def handle_status(self, request):
        try:
            # Gather base bot status
            status_data = await self.predictor_bot.get_bot_status()
            
            # Enrich with running states of both modules
            status_data["predictor_running"] = self.predictor_bot.is_running
            status_data["copytrader_running"] = self.copy_trade_runner.is_running
            
            # Enrich with copy trading target list
            status_data["target_traders"] = config.TARGET_TRADERS
            
            return web.json_response(status_data)
        except Exception as e:
            logger.error(f"Error handling status request: {e}")
            return web.json_response({"error": str(e)}, status=500)

async def main():
    manager = WebManager()
    
    # Handle OS signals
    loop = asyncio.get_running_loop()
    for sig in (asyncio.subprocess.signal.SIGINT, asyncio.subprocess.signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(manager.stop_server()))
        
    await manager.start_server()
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await manager.stop_server()

if __name__ == "__main__":
    asyncio.run(main())
