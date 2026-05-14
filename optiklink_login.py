"""
OptikLink 每日自动登录脚本 v2
原理：用 Discord Token 完成 OAuth2 授权，拿到 session 后访问 Dashboard
"""

import os
import re
import sys
import requests
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode

# ─────────────────────────────────────────────────────────────
# 配置区（全部从 GitHub Secrets 环境变量读取）
# ─────────────────────────────────────────────────────────────
DISCORD_TOKEN  = os.environ["DISCORD_TOKEN"]    # Discord Token
WXPUSHER_TOKEN = os.environ["WXPUSHER_TOKEN"]   # WxPusher appToken
WXPUSHER_UID   = os.environ["WXPUSHER_UID"]     # WxPusher 接收者 UID
EXPIRE_DATE    = "22.05.2026"                   # 服务到期日（兜底值）

# ── OptikLink Discord OAuth2 固定参数 ──────────────────────────
# 从浏览器 F12→Network 过滤 "discord.com/oauth2" 请求抓取
# 若下面的值失效，按文末说明重新抓取
DISCORD_CLIENT_ID    = "1005764586547838976"
DISCORD_REDIRECT_URI = "https://optiklink.net/callback"

# ─────────────────────────────────────────────────────────────
HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def wxpusher_send(title: str, content: str):
    resp = requests.post(
        "https://wxpusher.zjiecode.com/api/send/message",
        json={
            "appToken": WXPUSHER_TOKEN,
            "content": content,
            "summary": title,
            "contentType": 3,
            "uids": [WXPUSHER_UID],
        },
        timeout=15,
    )
    result = resp.json()
    print(f"[WxPusher] {result.get('msg')} | success={result.get('success')}")


# ─────────────────────────────────────────────────────────────
# Step A: 探测页面，尝试动态发现 OAuth 参数
# ─────────────────────────────────────────────────────────────
def discover_oauth_params(session: requests.Session) -> dict:
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify email guilds",
    }

    print("[A] 访问 /auth 探测页面结构 ...")
    r = session.get("https://optiklink.net/auth", timeout=15,
                    headers=HEADERS_BROWSER, allow_redirects=True)

    print(f"    状态码: {r.status_code}  最终URL: {r.url}")
    print(f"    响应前 800 字符:\n{r.text[:800]}\n{'─'*40}")

    # 尝试从 HTML/JS 中提取完整 discord oauth URL
    for pat in [
        r'https?://discord\.com(?:/api)?/oauth2/authorize[^\s\'"<>\\]+',
        r'https?://discord\.com/oauth2/authorize[^\s\'"<>\\]+',
    ]:
        m = re.search(pat, r.text)
        if m:
            raw_url = m.group(0).replace("&amp;", "&").rstrip("\\)\"'")
            print(f"    发现 OAuth URL: {raw_url[:120]}")
            parsed = urlparse(raw_url)
            qs = parse_qs(parsed.query)
            for key in ("client_id", "redirect_uri", "scope", "state"):
                if qs.get(key):
                    params[key] = qs[key][0]
            break
    else:
        print("    未从页面找到 OAuth URL，使用硬编码参数")

    # 若页面直接跳转到了 discord.com，从最终 URL 解析
    if "discord.com" in r.url:
        print(f"    页面直接跳转到 Discord: {r.url[:120]}")
        qs = parse_qs(urlparse(r.url).query)
        for key in ("client_id", "redirect_uri", "scope", "state"):
            if qs.get(key):
                params[key] = qs[key][0]

    print(f"    最终 OAuth 参数: {params}")
    return params


# ─────────────────────────────────────────────────────────────
# Step B: Discord Token 授权
# ─────────────────────────────────────────────────────────────
def discord_authorize(oauth_params: dict) -> str:
    print("[B] 向 Discord 提交 OAuth 授权 ...")
    post_params = {k: oauth_params[k]
                   for k in ("client_id", "redirect_uri", "response_type", "scope")
                   if k in oauth_params}
    if "state" in oauth_params:
        post_params["state"] = oauth_params["state"]

    r = requests.post(
        "https://discord.com/api/v10/oauth2/authorize",
        params=post_params,
        json={"authorize": True, "permissions": "0"},
        headers={
            "Authorization": DISCORD_TOKEN,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://discord.com/oauth2/authorize?" + urlencode(post_params),
            "X-Super-Properties": "eyJvcyI6IldpbmRvd3MiLCJicm93c2VyIjoiQ2hyb21lIn0=",
            "X-Discord-Locale": "en-US",
        },
        timeout=15,
        allow_redirects=False,
    )

    print(f"    Discord 状态: {r.status_code}")
    try:
        data = r.json()
    except Exception:
        data = {}
    print(f"    Discord body: {str(data)[:300]}")

    if r.status_code == 200 and "location" in data:
        return data["location"]

    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("Location", "")
        if loc:
            print(f"    重定向 Location: {loc[:100]}")
            return loc

    raise RuntimeError(
        f"Discord 授权失败 (HTTP {r.status_code}): {data}\n"
        "可能原因：①Token 失效或格式错误 ②账号被限制 ③client_id/redirect_uri 不匹配"
    )


# ─────────────────────────────────────────────────────────────
# Step C: 回调
# ─────────────────────────────────────────────────────────────
def optiklink_callback(session: requests.Session, callback_url: str):
    print(f"[C] 访问回调 URL: {callback_url[:100]} ...")
    r = session.get(callback_url, timeout=15,
                    headers=HEADERS_BROWSER, allow_redirects=True)
    print(f"    状态码: {r.status_code}  最终URL: {r.url}")
    if r.status_code >= 400:
        raise RuntimeError(f"回调失败，HTTP {r.status_code}")


# ─────────────────────────────────────────────────────────────
# Step D: Dashboard
# ─────────────────────────────────────────────────────────────
def check_dashboard(session: requests.Session) -> dict:
    print("[D] 访问 Dashboard ...")
    r = session.get("https://optiklink.net", timeout=15,
                    headers=HEADERS_BROWSER, allow_redirects=True)
    print(f"    状态码: {r.status_code}  最终URL: {r.url}")

    info = {"logged_in": False, "username": "N/A",
            "expire_date": EXPIRE_DATE, "running_servers": "N/A"}
    html = r.text

    if "DASHBOARD" in html.upper():
        info["logged_in"] = True
        for pat in [
            r'Welcome\s+<[^>]+>([^<]+)</[^>]+>\s+to your Dashboard',
            r'"username"\s*:\s*"([^"]+)"',
            r'simeter\w*',          # 你的用户名前缀，可按实际修改
        ]:
            m = re.search(pat, html, re.I)
            if m:
                info["username"] = m.group(1) if m.lastindex else m.group(0)
                break
        m2 = re.search(r'(\d+)\s+servers?', html, re.I)
        if m2:
            info["running_servers"] = m2.group(1)
        m3 = re.search(r'(\d{2}\.\d{2}\.\d{4})', html)
        if m3:
            info["expire_date"] = m3.group(1)

    print(f"    信息: {info}")
    return info


# ─────────────────────────────────────────────────────────────
# 推送消息
# ─────────────────────────────────────────────────────────────
def build_message(info: dict) -> tuple[str, str]:
    today = datetime.now()
    expire_dt = datetime.strptime(info["expire_date"], "%d.%m.%Y")
    days_left = (expire_dt - today).days
    status = "✅ 登录成功" if info["logged_in"] else "❌ 登录失败"
    warning = ""
    if days_left <= 7:
        warning = f"\n\n> ⚠️ **服务即将到期！还剩 {days_left} 天，请立即续期！**"
    elif days_left <= 30:
        warning = f"\n\n> 📅 服务到期还剩 **{days_left}** 天"

    title = f"OptikLink 签到 | {status}"
    content = f"""## OptikLink 每日自动登录报告

| 项目 | 内容 |
|------|------|
| 状态 | {status} |
| 用户名 | {info['username']} |
| 运行服务器 | {info['running_servers']} 个 |
| 服务到期 | {info['expire_date']} |
| 剩余天数 | {days_left} 天 |
| 执行时间 | {today.strftime('%Y-%m-%d %H:%M:%S')} UTC |
{warning}
"""
    return title, content


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("  OptikLink 自动登录脚本  v2")
    print("=" * 55)
    session = requests.Session()
    try:
        oauth_params   = discover_oauth_params(session)
        callback_url   = discord_authorize(oauth_params)
        optiklink_callback(session, callback_url)
        info           = check_dashboard(session)
        title, content = build_message(info)
        wxpusher_send(title, content)
        if not info["logged_in"]:
            raise RuntimeError("Dashboard 未出现，登录可能失败，请查看日志")
        print("\n✅ 全部完成！")
    except Exception as e:
        err_msg = str(e)
        print(f"\n❌ 出错: {err_msg}")
        try:
            wxpusher_send(
                "OptikLink 签到 ❌ 失败",
                f"## 执行失败\n\n**错误：**\n```\n{err_msg}\n```\n"
                f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC",
            )
        except Exception as pe:
            print(f"WxPusher 推送失败: {pe}")
        sys.exit(1)


if __name__ == "__main__":
    main()

# ─────────────────────────────────────────────────────────────
# 如何重新抓取 client_id / redirect_uri（页面改版后使用）
# ─────────────────────────────────────────────────────────────
# 1. 浏览器打开 optiklink.net/auth（已退出登录状态）
# 2. F12 → Network → 过滤 "discord"
# 3. 点击页面上的 Discord 登录按钮
# 4. 找到跳转到 discord.com/oauth2/authorize 的请求
# 5. 复制完整 URL，从中提取 client_id 和 redirect_uri
# 6. 更新脚本顶部 DISCORD_CLIENT_ID / DISCORD_REDIRECT_URI
