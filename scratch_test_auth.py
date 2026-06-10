import asyncio
import aiohttp
import os
import json
from auth import get_auth_headers

async def test_auth_api():
    # Load .env file
    env_path = ".env"
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()

    public_key = os.getenv("BAYSE_PUBLIC_KEY")
    secret_key = os.getenv("BAYSE_SECRET_KEY")
    
    url = "https://relay.bayse.markets/v1/wallet/assets"
    path = "/v1/wallet/assets"
    method = "GET"
    
    headers = get_auth_headers(public_key, secret_key, method, path)
    
    print(f"Requesting {url} with headers: {json.dumps(headers, indent=2)}")
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            print(f"Status: {resp.status}")
            text = await resp.text()
            print("Response:")
            try:
                print(json.dumps(json.loads(text), indent=2))
            except Exception:
                print(text)

if __name__ == "__main__":
    asyncio.run(test_auth_api())
