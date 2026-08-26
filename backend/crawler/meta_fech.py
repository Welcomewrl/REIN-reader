from curl_cffi.requests import AsyncSession
import asyncio
import html
import json
import random
import os
from urllib.parse import urlparse, parse_qs, unquote
from selectolax.parser import HTMLParser
from backend.config import SEARCH_TIMEOUT

# TODO:1.爬取浏览引擎的网页标题和网址提交给main.py 2.异步处理 3.添加百度/bing搜索引擎 
# TODO:4.添加多页爬取功能 5.添加异常处理 6.添加反爬虫特征检测(拆分到check_anti_crawler) 7.添加可选择引擎 8.先访问一遍本地存个cookies
# TODO:9.添加日志记录 10.添加请求头随机化 11.添加请求间隔随机化 
# TODO:12.会话复用 13.百度失效自动切换必应 14.提取百度真实URL 15.请求头扩展随机化
# TODO:16.请求、解析、cookie保存拆开，别混在一起

class SearchEngineFailure(Exception):
    pass

# 读取 UA 列表
def load_user_agents():
    ua_file = os.path.join(os.path.dirname(__file__), 'user_angent.json')
    with open(ua_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('desktop', []) + data.get('mobile', [])

UA_LIST = load_user_agents()

def get_random_ua():
    return random.choice(UA_LIST) if UA_LIST else None

# 随机请求头
def build_headers(engine):
    ua = get_random_ua()
    accept = random.choice([
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
    ])
    referer = 'https://www.bing.com/' if engine == 'bing' else 'https://www.baidu.com/'
    return {
        'User-Agent': ua,
        'Accept': accept,
        'Referer': referer,
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
    }

# 百度跳转链接转真实链接
def extract_baidu_real_url(href):
    parsed = urlparse(href)
    if 'baidu.com/link' in parsed.netloc or parsed.path.startswith('/link'):
        query = parse_qs(parsed.query)
        url_param = query.get('url', [None])[0]
        if url_param:
            return unquote(url_param)
    return href

COOKIE_DIR = os.path.dirname(__file__)

def _get_cookie_file(engine):
    return os.path.join(COOKIE_DIR, f'{engine}_cookies.json')

def _load_cookies(session, engine):
    cookie_file = _get_cookie_file(engine)
    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
                session.cookies.update(cookies)
        except Exception as e:
            print(f"加载 cookies 失败 ({engine}): {e}")

def _save_cookies(session, engine):
    cookie_file = _get_cookie_file(engine)
    try:
        cookies = session.cookies.get_dict()
        with open(cookie_file, 'w', encoding='utf-8') as f:
            json.dump(cookies, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"保存 cookies 失败 ({engine}): {e}")

# 反爬检测，有问题抛异常
def check_anti_crawler(response, engine):
    if response.status_code != 200:
        raise SearchEngineFailure(f"{engine} status {response.status_code}")
    text = response.text.lower()
    blocked_keywords = [
        '验证码', '安全验证', '异常流量', '访问过于频繁', '百度安全验证',
        'captcha', 'unusual traffic', 'verify', 'blocked'
    ]
    for kw in blocked_keywords:
        if kw in text:
            raise SearchEngineFailure(f"{engine} blocked: {kw}")
    if engine == 'bing' and 'b_algo' not in text:
        raise SearchEngineFailure("Bing no results")
    if engine == 'baidu' and 'result' not in text:
        raise SearchEngineFailure("Baidu no results")

# 解析必应结果
def parse_bing_results(html_text):
    tree = HTMLParser(html_text)
    results = []
    for li in tree.css('li.b_algo'):
        a = li.css_first('h2 a')
        if a:
            title = a.text(strip=True)
            link = a.attributes.get('href')
            if title and link:
                results.append({'title': title, 'url': link})
    return results

# 解析百度结果
def parse_baidu_results(html_text):
    tree = HTMLParser(html_text)
    results = []
    for item in tree.css('div.result, div.result-op'):
        tpl = item.attributes.get('tpl', '')
        if 'recommend_list' in tpl:
            continue
        a = item.css_first('h3 a')
        if not a:
            continue
        title = a.text(strip=True)
        real_url = None

        mu = item.attributes.get('mu')
        if mu and mu.startswith('http'):
            real_url = mu

        if not real_url:
            data_tools = item.attributes.get('data-tools')
            if data_tools:
                try:
                    decoded = html.unescape(data_tools)
                    data = json.loads(decoded)
                    url_from_tools = data.get('url')
                    if url_from_tools and url_from_tools.startswith('http'):
                        real_url = url_from_tools
                except Exception:
                    pass

        href = a.attributes.get('href', '')
        if not real_url and href:
            real_url = extract_baidu_real_url(href)

        link = real_url or href
        if title and link:
            results.append({'title': title, 'url': link})
    return results

# 请求必应
async def fetch_bing(session, query, page, headers):
    url = f"https://www.bing.com/search?q={query}&first={(page-1)*10}"
    try:
        response = await session.get(url, impersonate='chrome', headers=headers, timeout=SEARCH_TIMEOUT)
        check_anti_crawler(response, 'bing')
        results = parse_bing_results(response.text)
        if not results:
            raise SearchEngineFailure("Bing no results parsed")
        return {'query': query, 'results': results}
    except SearchEngineFailure:
        raise
    except Exception as e:
        raise SearchEngineFailure(f"Bing request error: {e}")
    finally:
        _save_cookies(session, 'bing')

# 请求百度
async def fetch_baidu(session, query, page, headers):
    url = f"https://www.baidu.com/s?wd={query}&pn={(page-1)*10}"
    try:
        response = await session.get(url, impersonate='chrome', headers=headers, timeout=SEARCH_TIMEOUT)
        check_anti_crawler(response, 'baidu')
        results = parse_baidu_results(response.text)
        if not results:
            raise SearchEngineFailure("Baidu no results parsed")
        return {'query': query, 'results': results}
    except SearchEngineFailure:
        raise
    except Exception as e:
        raise SearchEngineFailure(f"Baidu request error: {e}")
    finally:
        _save_cookies(session, 'baidu')

# 预热引擎，拿新鲜cookie
async def warm_up_engine(session, engine, headers):
    homepage = "https://www.bing.com" if engine == 'bing' else "https://www.baidu.com"
    response = await session.get(homepage, impersonate='chrome', headers=headers, timeout=SEARCH_TIMEOUT)
    if response.status_code != 200:
        raise SearchEngineFailure(f"{engine} warm-up status {response.status_code}")

# 主函数，多页爬取
async def fetch_multiple_pages(query, pages=3, engine='bing', allow_fallback=True, min_delay=1.0, max_delay=3.0):
    all_results = []
    warmed_up = {}

    async with AsyncSession() as session:
        engines_to_use = [engine]
        if allow_fallback:
            backup = 'baidu' if engine == 'bing' else 'bing'
            engines_to_use.append(backup)

        # 准备引擎，固定headers，加载cookie
        for eng in engines_to_use:
            warmed_up[eng] = build_headers(eng)
            _load_cookies(session, eng)

        # 预热所有引擎
        for eng, headers in list(warmed_up.items()):
            try:
                await warm_up_engine(session, eng, headers)
                _save_cookies(session, eng)
                print(f"{eng} 预热成功")
            except Exception as e:
                print(f"{eng} 预热失败: {e}")
                del warmed_up[eng]

        if not warmed_up:
            print("所有引擎预热失败")
            return all_results

        # 爬取每一页
        for page in range(1, pages + 1):
            await asyncio.sleep(random.uniform(min_delay, max_delay))

            page_success = False
            for eng, headers in warmed_up.items():
                try:
                    if eng == 'bing':
                        res = await fetch_bing(session, query, page, headers)
                    else:
                        res = await fetch_baidu(session, query, page, headers)

                    if 'results' in res and res['results']:
                        all_results.extend(res['results'])
                        page_success = True
                        break
                    else:
                        raise SearchEngineFailure(f"{eng} empty results")
                except SearchEngineFailure as e:
                    print(f"Engine {eng} failed on page {page}: {e}")

            if not page_success:
                print(f"Page {page}: all engines failed")

    return all_results