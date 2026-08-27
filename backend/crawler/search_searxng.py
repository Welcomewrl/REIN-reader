import asyncio
from curl_cffi import AsyncSession
from config import SEARCH_TIMEOUT

async def search_single(session: AsyncSession, base_url: str, query: str):
    try:
        resp = await session.get(
            base_url,
            params={"q": query, "format": "json"},
            timeout=SEARCH_TIMEOUT
        )
        data = resp.json()
        return {"query": query, "results": data.get("results", []), "success": True}
    except Exception as e:
        return {"query": query, "error": str(e), "success": False}

async def main(query, base_ip,port,base_url,pages=3):
    if base_ip is not None and port is not None:
        base_url = f"http://{base_ip}:{port}/search"
    elif base_url is not None:
        base_url = base_url

    # 这里模拟 Chrome 指纹，即使 SearXNG 不检查，但你将来换代理时可直接复用
    async with AsyncSession(impersonate="chrome120", timeout=SEARCH_TIMEOUT) as session:
        tasks = [search_single(session, base_url, query) for _ in range(pages)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for res in results:
            if res and res.get("success"):
                print(f"✅ {res['query']}: {len(res['results'])} 条")
                # 这里可以塞你之前说的去重逻辑
            else:
                print(f"❌ {res}")

if __name__ == "__main__":
    asyncio.run(main())