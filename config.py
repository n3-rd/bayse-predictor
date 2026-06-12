import os

# Load .env file manually if it exists to populate os.environ
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                if key not in os.environ:
                    os.environ[key] = val.strip()

# API Keys (inject via environment or default to placeholder/dry-run configurations)
# Note: In production, do not commit raw keys to git.
BAYSE_PUBLIC_KEY = os.getenv("BAYSE_PUBLIC_KEY", "pk_live_placeholder")
BAYSE_SECRET_KEY = os.getenv("BAYSE_SECRET_KEY", "sk_live_placeholder")

# URLs
BASE_REST_URL = os.getenv("BASE_REST_URL", "https://relay.bayse.markets")
PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL", "wss://socket.bayse.markets/ws/v1/markets")
PRIVATE_WS_URL = os.getenv("PRIVATE_WS_URL", "wss://socket.bayse.markets/ws/v1/user")

# Execution Mode
DRY_RUN = os.getenv("DRY_RUN", "True").lower() in ("true", "1", "yes")

# Rate Limits (Based on Bayse Docs)
READ_RATE_LIMIT = 30   # req/sec
WRITE_RATE_LIMIT = 20  # req/sec

# Risk Management parameters
MAX_POSITION_SIZE_PCT = 0.02  # Maximum 2% risk of total balance per trade
DAILY_LOSS_LIMIT_PCT = 0.05   # Global daily loss limit of 5%

# Copy Trading settings
TARGET_TRADERS = ["@zorodelavega", "@habibti", "@jimmycooks"]
COPY_TRADE_MAX_ALLOCATION_PCT = 0.30  # Restrict to maximum 30% per copy event
MAX_COPY_SLIPPAGE = 0.01             # Maximum 1% price slippage boundary for following copies
MAX_TOTAL_ALLOCATION_PCT = 0.30      # Limit total allocated balance to 30% at any time



# Strategy settings
MIN_EDGE_PCT = 0.0001  # minimum probability deviation to trigger an order (e.g. 5%)
DEFAULT_SLIPPAGE = 0.02  # 2% max slippage boundary

# Dashboard Settings
ENABLE_DASHBOARD = os.getenv("ENABLE_DASHBOARD", "True").lower() in ("true", "1", "yes")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
