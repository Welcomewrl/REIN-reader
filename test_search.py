import asyncio
from backend.crawler.meta_fetch import fetch_multiple_pages 

async def main():
    try:
        results = await fetch_multiple_pages("Python", pages=3) 
        print(f"拿到 {len(results)} 条结果\n")
        for i, r in enumerate(results, 1):
            print(f"[{i}] {r.get('title', '无标题')}")
            print(f"    {r.get('url', '无链接')}")
            if r.get('snippet'):
                print(f"    {r['snippet'][:80]}")
            print()
    except Exception as e:
        print(f"测试失败: {e}")

asyncio.run(main())