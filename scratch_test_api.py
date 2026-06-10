import asyncio
import aiohttp
import os
import json

async def test_api():
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
    url = "https://relay.bayse.markets/v1/pm/events"
    
    headers = {
        "X-Public-Key": public_key
    }
    
    print(f"Requesting {url} with public key: {public_key}")
    
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
    asyncio.run(test_api())
