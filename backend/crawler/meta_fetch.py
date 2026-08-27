from curl_cffi.requests import AsyncSession
import asyncio
import json
import random
import os
import logging
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, quote_plus, unquote
from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)
#WARN:几乎无法使用，只是实验性！

# 配置导入，如果不存在（以防止我作死删掉了）就用默认值
try:
    from backend.config import (
        SEARCH_TIMEOUT,
        RETRIES_PER_PAGE,
        MAX_CONSECUTIVE_FAILURES,
        MIN_DELAY_BETWEEN_PAGES,
        MAX_DELAY_BETWEEN_PAGES,
        BASE_RETRY_DELAY,
        BING_SEARCH_URL,
        BING_HOME_URL,
        DEFAULT_PAGES,
    )
except ImportError:
    SEARCH_TIMEOUT = 10.0
    RETRIES_PER_PAGE = 2
    MAX_CONSECUTIVE_FAILURES = 2
    MIN_DELAY_BETWEEN_PAGES = 1.0
    MAX_DELAY_BETWEEN_PAGES = 3.0
    BASE_RETRY_DELAY = 1.0
    BING_SEARCH_URL = "https://www.bing.com/search"
    BING_HOME_URL = "https://www.bing.com"
    DEFAULT_PAGES = 3

try:
    from backend.crawler.check_anti_crawler import check_anti as _default_check_anti
except ImportError:
    def _default_check_anti(html_text: str) -> bool:
        anti_keywords = ["unusual traffic", "captcha", "verify you are human", "access denied"]
        lower_text = html_text.lower()
        return not any(kw in lower_text for kw in anti_keywords)

#自定义异常类
class SearchEngineFailure(Exception):
    pass

def build_headers() -> dict:
    accept = random.choice([
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
    ])
    return {
        'Accept': accept,
        'Referer': BING_HOME_URL + '/',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
    }

def _get_cookie_file() -> str:
    return os.path.join(os.path.dirname(__file__), 'bing_cookies.json')

def _load_cookies(session: AsyncSession) -> None:
    cookie_file = _get_cookie_file()
    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                session.cookies.update(json.load(f))
        except Exception as e:
            logger.warning(f"加载 cookies 失败: {e}")
#保存cokkies
def _save_cookies(session: AsyncSession) -> None:
    cookie_file = _get_cookie_file()
    try:
        with open(cookie_file, 'w', encoding='utf-8') as f:
            json.dump(session.cookies.get_dict(), f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"保存 cookies 失败: {e}")

def normalize_url(url: str) -> str:
    if 'bing.com/ck/a' in url:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if 'u' in query and query['u']:
            real_url = unquote(query['u'][0])
            if real_url.startswith('http'):
                url = real_url

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if (scheme == 'http' and netloc.endswith(':80')) or (scheme == 'https' and netloc.endswith(':443')):
        netloc = netloc.rsplit(':', 1)[0]

    tracking_params = {
        'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
        'fbclid', 'gclid', 'msclkid', 'yclid', 'igshid', 'ref', 'source',
        'qs', 'form', 'pq', 'sc', 'sk', 'cvid', 'ghsh', 'ghacc', 'ghpl',
        'ensearch', 'qid', 'mkt', 'setlang', 'sid', 'rd', 'adlt',
        'safe', 'scene', 'o', 'cb', 'udm', 'mstn', 'pl'
    }
    query = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in query.items() if k.lower() not in tracking_params}
    new_query = urlencode(filtered, doseq=True)
    return urlunparse((scheme, netloc, parsed.path, parsed.params, new_query, parsed.fragment))

def parse_bing_results(html_text: str) -> list[dict]:
    tree = HTMLParser(html_text)
    results = []
    seen = set()

    container_selectors = [
        'li.b_algo', 'div.b_algo', '.b_algo', '.b_title',
        '.b_attribution', 'li.b_ans', 'div.b_ans'
    ]

    for selector in container_selectors:
        items = tree.css(selector)
        if items:
            for item in items:
                a = item.css_first('h2 a') or item.css_first('a')
                if a:
                    title = a.text(strip=True)
                    link = a.attributes.get('href')
                    if title and link and link not in seen:
                        seen.add(link)
                        results.append({'title': title, 'url': link})
            if results:
                return results

    logger.debug("容器选择器未命中，尝试全局 h2 a")
    for a in tree.css('h2 a'):
        title = a.text(strip=True)
        link = a.attributes.get('href')
        if title and link and link not in seen:
            seen.add(link)
            results.append({'title': title, 'url': link})

    return results

async def warm_up_engine(session: AsyncSession, check_anti_func) -> None:
    headers = build_headers()
    response = await session.get(BING_HOME_URL, impersonate='chrome', headers=headers, timeout=SEARCH_TIMEOUT)
    if response.status_code != 200:
        raise SearchEngineFailure(f"Bing 预热状态码 {response.status_code}")
    try:
        if not check_anti_func(response.text):
            raise SearchEngineFailure("Bing 预热触发反爬")
    except SearchEngineFailure:
        raise
    except Exception as e:
        logger.error(f"check_anti 执行异常: {e}")
        raise SearchEngineFailure(f"check_anti 异常: {e}")

async def fetch_bing_page(session: AsyncSession, query: str, page: int, check_anti_func) -> list[dict]:
    encoded_query = quote_plus(query)
    url = f"{BING_SEARCH_URL}?q={encoded_query}&first={(page - 1) * 10}"

    for attempt in range(RETRIES_PER_PAGE + 1):
        headers = build_headers()
        try:
            response = await session.get(url, impersonate='chrome', headers=headers, timeout=SEARCH_TIMEOUT)
            if response.status_code != 200:
                raise SearchEngineFailure(f"Bing 状态码 {response.status_code}")

            try:
                anti_result = check_anti_func(response.text)
            except Exception as e:
                logger.error(f"check_anti 执行异常: {e}")
                raise SearchEngineFailure(f"check_anti 异常: {e}")

            if not anti_result:
                raise SearchEngineFailure("Bing 搜索触发反爬")

            results = parse_bing_results(response.text)
            if not results:
                raise SearchEngineFailure("Bing 解析结果为空")
            return results

        except SearchEngineFailure as e:
            logger.warning(f"Bing 第 {page} 页第 {attempt+1} 次尝试失败: {e}")
            if attempt == RETRIES_PER_PAGE:
                raise
            delay = BASE_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(delay)
        except Exception as e:
            logger.error(f"Bing 第 {page} 页第 {attempt+1} 次请求异常: {e}")
            if attempt == RETRIES_PER_PAGE:
                raise SearchEngineFailure(f"Bing 请求错误: {e}")
            delay = BASE_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(delay)

async def fetch_multiple_pages(
    query: str,
    pages: int = DEFAULT_PAGES,
    min_delay: float = MIN_DELAY_BETWEEN_PAGES,
    max_delay: float = MAX_DELAY_BETWEEN_PAGES,
    abort_on_warmup_fail: bool = False,
    check_anti_func=None
) -> dict:
    if check_anti_func is None:
        check_anti_func = _default_check_anti

    all_results = []
    seen_urls = set()
    errors = []

    if not query or not isinstance(query, str):
        return {
            "success": False, "query": query, "results": [],
            "error": "query 必须是非空字符串", "errors": []
        }
    if not isinstance(pages, int) or pages <= 0:
        return {
            "success": False, "query": query, "results": [],
            "error": "pages 必须是正整数", "errors": []
        }
    if min_delay > max_delay:
        min_delay, max_delay = max_delay, min_delay

    try:
        async with AsyncSession() as session:
            _load_cookies(session)

            try:
                await warm_up_engine(session, check_anti_func)
                logger.info("Bing 预热成功")
            except Exception as e:
                logger.warning(f"Bing 预热失败: {e}")
                errors.append(f"预热失败: {e}")
                if abort_on_warmup_fail:
                    return {
                        "success": False, "query": query, "results": [],
                        "error": f"预热失败且配置为终止: {e}", "errors": errors
                    }

            consecutive_failures = 0

            for page in range(1, pages + 1):
                if page > 1:
                    await asyncio.sleep(random.uniform(min_delay, max_delay))

                try:
                    page_results = await fetch_bing_page(session, query, page, check_anti_func)
                    for item in page_results:
                        norm_url = normalize_url(item['url'])
                        if norm_url not in seen_urls:
                            seen_urls.add(norm_url)
                            all_results.append({
                                'title': item['title'], 'url': item['url'], 'snippet': ''
                            })
                    consecutive_failures = 0
                    logger.info(f"Bing 第 {page} 页成功，获取 {len(page_results)} 条结果")
                except SearchEngineFailure as e:
                    logger.warning(f"Bing 第 {page} 页最终失败: {e}")
                    errors.append(f"第 {page} 页失败: {e}")
                    consecutive_failures += 1
                except Exception as e:
                    logger.error(f"Bing 第 {page} 页未知异常: {e}")
                    errors.append(f"第 {page} 页未知异常: {e}")
                    consecutive_failures += 1

                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.warning("连续失败达到阈值，停止后续页抓取")
                    errors.append("连续失败达到阈值，停止后续页抓取")
                    break

            _save_cookies(session)
    except Exception as e:
        logger.error(f"会话创建或整体异常: {e}")
        return {
            "success": False, "query": query, "results": [],
            "error": f"会话或整体异常: {e}", "errors": errors + [str(e)]
        }

    if all_results:
        return {
            "success": True, "query": query, "results": all_results,
            "error": None, "errors": errors
        }
    else:
        return {
            "success": False, "query": query, "results": [],
            "error": "未获取到任何结果", "errors": errors
        }

if __name__ == "__main__":
    async def demo():
        result = await fetch_multiple_pages("Python", pages=2)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    asyncio.run(demo())