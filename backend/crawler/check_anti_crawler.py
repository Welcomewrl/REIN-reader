from selectolax.parser import HTMLParser
import os

_BLACK_LIST_FILE = os.path.join(os.path.dirname(__file__), 'BLACK_LIST.txt')

def _load_black_list():
    black_set = set()
    try:
        with open(_BLACK_LIST_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    black_set.add(line)
    except FileNotFoundError:
        pass
    return black_set

_BLACK_LIST = _load_black_list()

def check_anti(html_text, url=None, threshold=None):
    if not html_text:
        print("[anti] 空 html")
        return False

    if url and url in _BLACK_LIST:
        print(f"[anti] URL在黑名单: {url}")
        return False

    parser = HTMLParser(html_text)
    text = html_text.lower()
    stripped = ' '.join(text.split())

    if threshold is None:
        threshold = 8

    # 验证码输入框或 iframe
    if parser.css_first(
        'input[name="captcha"], input[name="g-recaptcha-response"], '
        'input[name="h-captcha-response"], iframe[src*="recaptcha"], '
        'iframe[src*="hcaptcha"]'
    ):
        print("[anti] 检测到验证码输入框/iframe")
        return False

    # 跳转外部验证码服务
    if any(d in text for d in [
        'google.com/recaptcha', 'hcaptcha.com', 'captcha.com',
        'challenges.cloudflare.com'
    ]):
        print("[anti] 包含外部验证码服务URL")
        return False

    # Cloudflare/JS 挑战特征
    if any(m in text for m in [
        'cf-challenge-running', 'challenge-platform',
        'window._cf_chl_opt', 'cf-chl-', 'just a moment',
        'checking your browser before accessing'
    ]):
        print("[anti] 检测到 Cloudflare/JS 挑战特征")
        return False

    # 极短页面包含禁止词
    if len(stripped) < 200 and any(w in stripped for w in [
        'access denied', 'forbidden', 'blocked', 'request blocked',
        '403 forbidden', '访问被拒绝', '访问拒绝'
    ]):
        print("[anti] 极短页面包含禁止词")
        return False

    # 标题提示
    title = parser.css_first('title')
    if title:
        title_text = title.text(strip=True).lower()
        if any(phrase in title_text for phrase in [
            'attention required', 'security challenge', 'please verify',
            'access denied', 'blocked', 'verify you are human'
        ]):
            print("[anti] 标题包含反爬提示")
            return False

    # meta refresh 指向验证码
    meta_refresh = parser.css_first('meta[http-equiv="refresh" i]')
    if meta_refresh:
        content = meta_refresh.attributes.get('content', '').lower()
        if any(kw in content for kw in ['captcha', 'verify', 'challenge']):
            print("[anti] meta refresh 指向验证码")
            return False

    score = 0

    # 验证码指令命中
    precise_attr_selector = (
        '[id="captcha"], [class="captcha"], '
        '[id="recaptcha"], [class="recaptcha"], '
        '[id="g-recaptcha"], [class="g-recaptcha"], '
        '[id="h-captcha"], [class="h-captcha"], '
        '[id="hcaptcha"], [class="hcaptcha"], '
        '[id="verify"], [class="verify"], '
        '[id="challenge"], [class="challenge"]'
    )
    if parser.css_first(precise_attr_selector):
        score += 4
        print(f"[anti] 验证码指令命中，加分 4，当前得分 {score}")

    # 验证码关键词
    captcha_keywords = ['captcha', 'recaptcha', 'hcaptcha', '验证码', '安全验证', '人机验证']
    captcha_hits = sum(1 for kw in captcha_keywords if kw in text)
    if captcha_hits >= 2:
        score += 3
        print(f"[anti] 验证码关键词命中 {captcha_hits} 次，加分 3，当前得分 {score}")
    elif captcha_hits == 1:
        score += 1
        print(f"[anti] 验证码关键词命中 1 次，加分 1，当前得分 {score}")

    # 反爬短语
    anti_bot_phrases = [
        'unusual traffic', 'access denied', 'too many requests',
        'request blocked', '访问过于频繁', '异常流量', '请求过于频繁',
        '访问受限', '禁止访问', 'ip has been blocked', 'temporarily blocked'
    ]
    phrase_hits = sum(1 for p in anti_bot_phrases if p in text)
    if phrase_hits >= 2:
        score += 2
        print(f"[anti] 反爬短语命中 {phrase_hits} 次，加分 2，当前得分 {score}")
    elif phrase_hits == 1:
        score += 1
        print(f"[anti] 反爬短语命中 1 次，加分 1，当前得分 {score}")

    # 高频禁止词
    if text.count('blocked') >= 3 or text.count('forbidden') >= 3:
        score += 2
        print(f"[anti] blocked/forbidden 出现多次，加分 2，当前得分 {score}")

    # 短页面且含禁止词
    if len(text) < 500 and any(w in text for w in ['forbidden', 'blocked', 'access denied']):
        score += 2
        print(f"[anti] 短页面且含禁止词，加分 2，当前得分 {score}")

    print(f"[anti] 最终得分 {score}，阈值 {threshold}")
    if score >= threshold:
        print("[anti] 得分超过阈值，判定为被阻止")
        return False
    return True