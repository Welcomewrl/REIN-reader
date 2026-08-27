from curl_cffi.requests import AsyncSession
import asyncio
import json
import random
import os
import logging
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, quote_plus
from selectolax.parser import HTMLParser
from backend.config import SEARCH_TIMEOUT
from backend.crawler.check_anti_crawler import check_anti

# TODO:1.爬取浏览引擎的网页标题和网址提交给main.py 2.异步处理 3.仅支持Bing搜索引擎
# TODO:4.添加多页爬取功能 5.添加异常处理 6.所有响应内容均经过check_anti检测 7.添加可选择引擎(已固定Bing) 8.先访问一遍本地存个cookies
# TODO:9.添加日志记录 10.添加请求头随机化(仅Accept/Referer/Accept-Language) 11.添加请求间隔随机化(翻页时)
# TODO:12.会话复用 13.删除百度自动切换逻辑 14.提取百度真实URL(已删除) 15.请求头扩展随机化
# TODO:16.请求、解析、cookie保存拆开，别混在一起(已基本拆分)

#几乎百分百被禁止访问，不稳定。
#建议使用SearXNG或API
logger = logging.getLogger(__name__)

class SearchEngineFailure(Exception):
    pass

# 构建请求头
def build_headers():
    accept = random.choice([
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
    ])
    return {
        'Accept': accept,
        'Referer': 'https://www.bing.com/',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
    }

COOKIE_DIR = os.path.dirname(__file__)

def _get_cookie_file():
    return os.path.join(COOKIE_DIR, 'bing_cookies.json')

# 加载cookies
def _load_cookies(session):
    cookie_file = _get_cookie_file()
    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
                session.cookies.update(cookies)
        except Exception as e:
            logger.warning(f"加载cookies失败: {e}")

# 保存cookies
def _save_cookies(session):
    cookie_file = _get_cookie_file()
    try:
        cookies = session.cookies.get_dict()
        with open(cookie_file, 'w', encoding='utf-8') as f:
            json.dump(cookies, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"保存cookies失败: {e}")

# 解析必应结果，多选择器组合，去重
def parse_bing_results(html_text):
    tree = HTMLParser(html_text)
    results = []
    seen = set()

    # 候选容器选择器，覆盖不同版本的 Bing 结果结构
    container_selectors = [
        'li.b_algo',
        'div.b_algo',
        '.b_algo',
        '.b_title',
        '.b_attribution',
        'li.b_ans',
        'div.b_ans'
    ]

    # 先尝试从容器内提取
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

    # 若容器选择器均未命中，则退化为全局搜索 h2 下的链接
    logger.debug("容器选择器未命中，尝试全局 h2 a")
    for a in tree.css('h2 a'):
        title = a.text(strip=True)
        link = a.attributes.get('href')
        if title and link and link not in seen:
            seen.add(link)
            results.append({'title': title, 'url': link})

    return results

# 标准化URL
def normalize_url(url):
    parsed = urlparse(url)
    tracking_params = {
        'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
        'fbclid', 'gclid', 'msclkid', 'yclid', 'igshid', 'ref', 'source',
        # Bing常见跟踪参数
        'qs', 'form', 'pq', 'sc', 'sk', 'cvid', 'ghsh', 'ghacc', 'ghpl',
        'ensearch', 'qid', 'mkt', 'setlang', 'sid', 'rd', 'adlt',
        'safe', 'scene', 'o', 'cb', 'udm', 'mstn', 'pl'
    }
    query = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in query.items() if k.lower() not in tracking_params}
    new_query = urlencode(filtered, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

# 预热引擎
async def warm_up_engine(session):
    url = 'https://www.bing.com'
    headers = build_headers()
    response = await session.get(url, impersonate='chrome', headers=headers, timeout=SEARCH_TIMEOUT)
    if response.status_code != 200:
        raise SearchEngineFailure(f"Bing预热状态码 {response.status_code}")
    if not check_anti(response.text):
        raise SearchEngineFailure("Bing预热触发反爬")

# 执行单次搜索请求
async def fetch_bing(session, query, page, retries=2):
    encoded_query = quote_plus(query)
    url = f"https://www.bing.com/search?q={encoded_query}&first={(page-1)*10}"
    for attempt in range(retries + 1):
        headers = build_headers()
        try:
            response = await session.get(url, impersonate='chrome', headers=headers, timeout=SEARCH_TIMEOUT)
            if response.status_code != 200:
                raise SearchEngineFailure(f"Bing status {response.status_code}")
            if not check_anti(response.text):
                raise SearchEngineFailure("Bing搜索触发反爬")
            results = parse_bing_results(response.text)
            if not results:
                raise SearchEngineFailure("Bing解析结果为空")
            return results
        except SearchEngineFailure as e:
            logger.warning(f"Bing第{page}页第{attempt+1}次尝试失败: {e}")
            if attempt == retries:
                raise
            await asyncio.sleep(random.uniform(1, 3))
        except Exception as e:
            logger.error(f"Bing第{page}页第{attempt+1}次请求异常: {e}")
            if attempt == retries:
                raise SearchEngineFailure(f"Bing request error: {e}")
            await asyncio.sleep(random.uniform(1, 3))

# 主函数
async def fetch_multiple_pages(query, pages=3, min_delay=1.0, max_delay=3.0):
    all_results = []
    seen_urls = set()
    consecutive_failures = 0  # 连续失败计数

    async with AsyncSession() as session:
        _load_cookies(session)

        try:
            # 预热
            try:
                await warm_up_engine(session)
                logger.info("Bing预热成功")
            except Exception as e:
                logger.warning(f"Bing预热失败: {e}，继续尝试搜索")

         
            for page in range(1, pages + 1):
                if page > 1:
                    await asyncio.sleep(random.uniform(min_delay, max_delay))
                try:
                    results = await fetch_bing(session, query, page)
                    for item in results:
                        norm_url = normalize_url(item['url'])
                        if norm_url not in seen_urls:
                            seen_urls.add(norm_url)
                            all_results.append(item)
                    consecutive_failures = 0  # 成功重置连续失败
                except SearchEngineFailure as e:
                    logger.warning(f"Bing第{page}页最终失败: {e}")
                    consecutive_failures += 1
                except Exception as e:
                    logger.error(f"Bing第{page}页未知异常: {e}")
                    consecutive_failures += 1

                if consecutive_failures >= 2:
                    logger.warning("连续失败达到阈值，停止后续页抓取")
                    break
        finally:
            _save_cookies(session)

    return all_results