import os
import re
import time
import requests
from datetime import datetime

# ─────────────────────────────────────────────
# 配置区（从 GitHub Secrets 读取）
# ─────────────────────────────────────────────
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]       # Discord Token
WXPUSHER_TOKEN = os.environ["WXPUSHER_TOKEN"]     # WxPusher appToken
WXPUSHER_UID = os.environ["WXPUSHER_UID"]         # WxPusher 接收者UID
EXPIRE_DATE = "22.05.2026"                        # 服务到期日期（可改为从页面动态读取）

# ─────────────────────────────────────────────
# WxPusher 推送函数
# ─────────────────────────────────────────────
def wxpusher_send(title: str, content: str):
    url = "https://wxpusher.zjiecode.com/api/send/message"
    payload = {
        "appToken": WXPUSHER_TOKEN,
        "content": content,
        "summary": title,
        "contentType": 3,           # 3 = Markdown
        "uids": [WXPUSHER_UID],
    }
    resp = requests.post(url, json=payload, timeout=15)
    print(f"[WxPusher] 推送结果: {resp.json()}")


# ─────────────────────────────────────────────
# Step 1: 用 Discord Token 走 OAuth 拿 OptikLink session
# ─────────────────────────────────────────────
def discord_oauth_login() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    })

    # 1-a. 访问登录页，拿到 Discord OAuth 授权链接
    print("[Step 1] 获取 OAuth 授权链接 ...")
    r = session.get("https://optiklink.net/auth", timeout=15)
    # 从页面 HTML 中提取 discord authorize URL
    match = re.search(r'href="(https://discord\.com/oauth2/authorize[^"]+)"', r.text)
    if not match:
        # 备用：某些页面通过 JS 跳转，直接构造
        match = re.search(r"(https://discord\.com/api/oauth2/authorize[^'\"]+)", r.text)
    if not match:
        raise RuntimeError("未找到 Discord OAuth 链接，页面结构可能已变更")

    oauth_url = match.group(1).replace("&amp;", "&")
    print(f"[Step 1] OAuth URL: {oauth_url[:80]}...")

    # 1-b. 用 Discord Token 授权，拿到回调 code
    print("[Step 2] 向 Discord 提交授权 ...")
    # 解析 client_id / redirect_uri / scope / state
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    parsed = urlparse(oauth_url)
    params = parse_qs(parsed.query)

    authorize_api = "https://discord.com/api/v10/oauth2/authorize"
    auth_headers = {
        "Authorization": DISCORD_TOKEN,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Referer": oauth_url,
    }
    auth_payload = {
        "authorize": True,
        "permissions": "0",
    }
    # 将原始参数透传
    r2 = requests.post(
        authorize_api,
        params={
            "client_id": params.get("client_id", [""])[0],
            "redirect_uri": params.get("redirect_uri", [""])[0],
            "response_type": params.get("response_type", ["code"])[0],
            "scope": params.get("scope", ["identify email"])[0],
            "state": params.get("state", [""])[0],
        },
        json=auth_payload,
        headers=auth_headers,
        timeout=15,
        allow_redirects=False,
    )
    data = r2.json()
    print(f"[Step 2] Discord 响应: {data}")

    if "location" not in data:
        raise RuntimeError(f"Discord 授权失败: {data}")

    callback_url = data["location"]
    print(f"[Step 3] 回调 URL: {callback_url[:80]}...")

    # 1-c. 让 OptikLink 处理回调，完成登录
    r3 = session.get(callback_url, timeout=15, allow_redirects=True)
    print(f"[Step 3] 回调响应状态: {r3.status_code}, 最终URL: {r3.url}")

    return session


# ─────────────────────────────────────────────
# Step 2: 访问 Dashboard，确认登录成功，解析关键信息
# ─────────────────────────────────────────────
def check_dashboard(session: requests.Session) -> dict:
    print("[Step 4] 访问 Dashboard ...")
    r = session.get("https://optiklink.net", timeout=15)

    info = {
        "logged_in": False,
        "username": "",
        "expire_date": EXPIRE_DATE,
        "running_servers": "",
    }

    if "Dashboard" in r.text or "DASHBOARD" in r.text:
        info["logged_in"] = True
        # 提取用户名
        m = re.search(r'Welcome\s+<[^>]+>([^<]+)</[^>]+>\s+to your Dashboard', r.text)
        if m:
            info["username"] = m.group(1).strip()
        # 提取服务器数量
        m2 = re.search(r'(\d+)\s+servers?', r.text)
        if m2:
            info["running_servers"] = m2.group(1)
        # 尝试动态读取到期日
        m3 = re.search(r'(\d{2}\.\d{2}\.\d{4})', r.text)
        if m3:
            info["expire_date"] = m3.group(1)

    print(f"[Step 4] 信息: {info}")
    return info


# ─────────────────────────────────────────────
# Step 3: 计算距到期天数，组装推送消息
# ─────────────────────────────────────────────
def build_message(info: dict) -> tuple[str, str]:
    today = datetime.now()
    expire_dt = datetime.strptime(info["expire_date"], "%d.%m.%Y")
    days_left = (expire_dt - today).days

    status = "✅ 登录成功" if info["logged_in"] else "❌ 登录失败"
    warning = ""
    if days_left <= 7:
        warning = f"\n\n> ⚠️ **服务即将到期！还剩 {days_left} 天，请及时续期！**"
    elif days_left <= 30:
        warning = f"\n\n> 📅 服务到期剩余 **{days_left}** 天"

    title = f"OptikLink 每日签到 | {status}"
    content = f"""## OptikLink 每日自动登录报告

| 项目 | 内容 |
|------|------|
| 状态 | {status} |
| 用户名 | {info.get('username', 'N/A')} |
| 运行服务器 | {info.get('running_servers', 'N/A')} 个 |
| 服务到期 | {info['expire_date']} |
| 距今天数 | {days_left} 天 |
| 执行时间 | {today.strftime('%Y-%m-%d %H:%M:%S')} |
{warning}
"""
    return title, content


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────
def main():
    print("=" * 50)
    print("OptikLink 自动登录脚本启动")
    print("=" * 50)

    try:
        session = discord_oauth_login()
        info = check_dashboard(session)
        title, content = build_message(info)
        wxpusher_send(title, content)

        if not info["logged_in"]:
            raise RuntimeError("Dashboard 访问失败，登录可能未成功")

        print("✅ 全部完成！")

    except Exception as e:
        err_msg = str(e)
        print(f"❌ 出错: {err_msg}")
        wxpusher_send(
            "OptikLink 自动登录 ❌ 失败",
            f"## 执行失败\n\n**错误信息：**\n```\n{err_msg}\n```\n\n请检查 Token 或页面结构是否变更。"
        )
        raise


if __name__ == "__main__":
    main()
