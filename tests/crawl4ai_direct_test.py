"""Direct Crawl4AI test script."""

import asyncio
from crawl4ai import AsyncWebCrawler


async def main() -> None:
    url = "https://sfi.ru/sveden/employees?search=%D0%98%D0%B2%D0%B0%D0%BD%D0%BE%D0%B2"
    async with AsyncWebCrawler(verbose=False) as crawler:
        result = await crawler.arun(url=url, wait_for="body", timeout=20)
        print("success", getattr(result, "success", None))
        html = getattr(result, "html", "")
        print("len", len(html))
        print("snippet", html[:300].replace("\n", " "))


if __name__ == "__main__":
    asyncio.run(main())
