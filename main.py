"""
NGA 举报通知监听脚本
定时抓取 NGA 举报数据，通过 Server酱 3 推送新增举报到手机。

Version: 1.0.1

## !! 注意事项 !!
1. 本脚本需要用户提供 NGA 的 Cookie，必须包含登录状态相关字段（如 `ngaPassportUid` 和 `ngaPassportCid`），否则无法获取举报数据。
2. 请确保 Server酱 3 的 SendKey 正确，并且已正确配置推送渠道（APP端）。
3. 抓取频率不宜过高，建议间隔至少 5 分钟以上，防止二哥服务器爆炸！
4. 本脚本仅供个人使用，切勿大规模分发或商用，避免引起不必要的法律风险。
5. 本脚本使用Deepseek-v4-pro模型进行开发，代码含有人工智能成分，非古法手搓代码。
"""

import json
import os
import re
import time
from datetime import datetime

import requests
import yaml
from serverchan_sdk import sc_send

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
CONFIG_FILE = "config.yaml"
CACHE_FILE = "cache.json"

USER_URL = "https://bbs.nga.cn/nuke.php?func=ucp&uid={}"
THREAD_URL = "https://bbs.nga.cn/read.php?tid={}"
REPLY_URL = "https://bbs.nga.cn/read.php?tid={}&pid={}&to=1"

FETCH_URL = "https://bbs.nga.cn/nuke.php?__lib=noti&raw=3"
FETCH_DATA = {"__act": "get_all", "time_limit": "1"}

HEADERS = {
    "User-Agent": (
        "HOMO-TEST-AGENT"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://bbs.nga.cn/nuke.php?__lib=noti",
    "Origin": "https://bbs.nga.cn",
    "Connection": "keep-alive",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_cache():
    """返回 (seen_keys: set, pending_reports: list)。兼容旧格式。"""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # 旧格式：纯 key 列表
            return set(data), []
        return set(data.get("seen_keys", [])), data.get("pending_reports", [])
    return set(), []


def save_cache(seen_keys, pending_reports):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "seen_keys": list(seen_keys),
            "pending_reports": pending_reports
        }, f, ensure_ascii=False, indent=2)


def parse_cookie(cookie_str):
    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def extract_reports_from_html(html_text):
    m_assign = re.search(r'window\.script_muti_get_var_store\s*=\s*', html_text)
    if not m_assign:
        return None

    start = m_assign.end()
    brace = html_text.find('{', start)
    if brace == -1:
        return None

    # 括号匹配，定位完整 JSON 对象
    depth = 0
    for i in range(brace, len(html_text)):
        ch = html_text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                raw_json = html_text[brace:i + 1]
                break
    else:
        return None

    # NGA 部分内部对象使用 JS 风格未加引号的数字键（如 { 9: 123, 0: 1 }），
    # 先补引号修复为合法 JSON 再解析
    raw_json = re.sub(r'([{,])\s*(\d+)\s*:', r'\1"\2":', raw_json)

    try:
        root = json.loads(raw_json)
    except json.JSONDecodeError:
        return None

    return _extract_reports(root)


def fetch_reports(cookies_dict):
    """POST 请求 NGA 通知接口, 返回举报数据列表。"""
    resp = requests.post(FETCH_URL, data=FETCH_DATA,
                         cookies=cookies_dict, headers=HEADERS, timeout=30)
    resp.encoding = "gbk"
    text = resp.text

    # HTML 中提取内嵌 JSON
    if "window.script_muti_get_var_store" in text:
        reports = extract_reports_from_html(text)
        if reports is not None:
            return reports

    print("[警告] 解析策略失败，返回 0 条举报")
    return []


def _extract_reports(root):
    """从解析后的 JSON 对象中提取举报数组。"""
    # 路径: data -> "0" -> "1"
    try:
        reports = root["data"]["0"]["1"]
        if isinstance(reports, list):
            return reports
    except (KeyError, TypeError):
        pass

    # 备选路径: data -> "1"
    try:
        reports = root["data"]["1"]
        if isinstance(reports, list):
            return reports
    except (KeyError, TypeError):
        pass

    return None



# ---------------------------------------------------------------------------
# 推送相关
# ---------------------------------------------------------------------------

def cache_key(report):
    """生成唯一缓存键, 用于去重。"""
    return f"{report.get('9',0)}_{report.get('1',0)}_{report.get('6',0)}_{report.get('7',0)}"


def build_desp(report):
    """构造单条举报的推送行（Markdown 格式）。"""
    ts = report.get("9", 0)
    rtype = report.get("0", 0)
    uid = report.get("1", 0)
    nick = report.get("2", "")
    title = report.get("5", "")
    reason = report.get("11", "")
    forum = report.get("13", "")
    tid = report.get("6", 0)
    pid = report.get("7", 0)

    time_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")

    user_link = f"[{nick}]({USER_URL.format(uid)})"
    thread_link = f"[{title}]({THREAD_URL.format(tid)})"

    if rtype == 13:
        target_str = "的主题"
    elif rtype == 14:
        target_str = f"的【[回复]({REPLY_URL.format(tid, pid)})】"
    else:
        target_str = f"的未知类型({rtype})"

    return (
        f"【{time_str}】"
        f"【{user_link}】"
        f"举报了【{forum}】中"
        f"【{thread_link}】"
        f"{target_str}，"
        f"理由是【{reason}】。"
    )


def push_new_reports(sendkey, new_reports):
    """通过 Server酱 3 推送新增举报（多条合并为一条消息）。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"[本次运行时间：{now_str}]"]

    for r in new_reports:
        lines.append(f"- {build_desp(r)}")

    desp = "\n\n".join(lines)
    title = f"NGA 举报提醒 ({len(new_reports)} 条)"
    return sc_send(sendkey, title, desp, {"tags": "NGA监测"})


def is_dnd_time(dnd_hours):
    """检查当前时间是否处于免打扰时段。"""
    if not dnd_hours:
        return False
    now = datetime.now().strftime("%H:%M")
    for period in dnd_hours:
        parts = period.split("-")
        if len(parts) != 2:
            continue
        start, end = parts[0].strip(), parts[1].strip()
        if start <= end:
            if start <= now <= end:
                return True
        else:
            # 跨天时段，如 23:00-07:00
            if now >= start or now <= end:
                return True
    return False


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

def main_loop():
    config = load_config()
    sendkey = config["serverchan"]["sendkey"]
    interval_minutes = config.get("interval_minutes", 10)
    dnd_hours = config.get("dnd_hours", [])
    monitor_forums = config.get("monitor_forums", [])
    cookies = parse_cookie(config["cookie"])
    seen_keys, pending_reports = load_cache()

    print(f"[启动] 抓取间隔: {interval_minutes} 分钟, 已缓存: {len(seen_keys)} 条")
    if monitor_forums:
        print(f"[启动] 限定监测版面: {monitor_forums}")
    if dnd_hours:
        print(f"[启动] 免打扰时段: {dnd_hours}")
    if pending_reports:
        print(f"[启动] 有待推送的暂存举报: {len(pending_reports)} 条")
    print("=" * 60)

    while True:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dnd = is_dnd_time(dnd_hours)
        print(f"\n[{now_str}] === 开始抓取 ===")
        if dnd:
            print("[免打扰] 当前处于免打扰时段，举报将延迟推送")

        try:
            reports = fetch_reports(cookies)
            total_count = len(reports)

            # 版面过滤
            if monitor_forums:
                reports = [r for r in reports if any(
                    kw in r.get("13", "") for kw in monitor_forums
                )]
                skipped = total_count - len(reports)
                if skipped:
                    print(f"[过滤] 忽略 {skipped} 条非监测版面的举报")

            print(f"[信息] 获取到 {len(reports)} 条举报 (共抓取 {total_count} 条)")

            new_ones = []
            for r in reports:
                ck = cache_key(r)
                if ck not in seen_keys:
                    new_ones.append(r)
                    seen_keys.add(ck)

            if new_ones:
                print(f"[新增] {len(new_ones)} 条 (本次共抓取 {len(reports)} 条):")
                for i, r in enumerate(new_ones, 1):
                    rtype = "主题" if r.get("0") == 13 else "回复"
                    nick = r.get("2", "?")
                    title = r.get("5", "?")
                    reason = r.get("11", "")
                    forum = r.get("13", "")
                    print(f"  [{i:02d}] [{rtype}] [{forum}] {nick} - {title}")
                    print(f"       理由: {reason}")
                print("-" * 60)

                if dnd:
                    pending_reports.extend(new_ones)
                    save_cache(seen_keys, pending_reports)
                    print(f"[免打扰] {len(new_ones)} 条举报已暂存，累计待推送: {len(pending_reports)} 条")
                else:
                    to_push = pending_reports + new_ones
                    pending_reports = []
                    save_cache(seen_keys, pending_reports)

                    print(f"[推送] 正在推送 {len(to_push)} 条举报...")
                    try:
                        resp = push_new_reports(sendkey, to_push)
                        print(f"  [推送] 返回: {resp}")
                    except Exception as e:
                        print(f"  [推送] 失败: {e}")
                    print(f"[缓存] 已更新, 共 {len(seen_keys)} 条")
            else:
                # 无新增，但免打扰刚结束，pending 需要推送
                if pending_reports and not dnd:
                    print(f"[推送] 免打扰已结束，推送暂存的 {len(pending_reports)} 条举报...")
                    try:
                        resp = push_new_reports(sendkey, pending_reports)
                        print(f"  [推送] 返回: {resp}")
                    except Exception as e:
                        print(f"  [推送] 失败: {e}")
                    pending_reports = []
                    save_cache(seen_keys, pending_reports)
                else:
                    print(f"[信息] 无新增举报 (本次共抓取 {len(reports)} 条)")

        except requests.Timeout:
            print("[错误] 请求超时")
        except Exception as e:
            print(f"[错误] {e}")

        print(f"\n[等待] {interval_minutes} 分钟后下一轮 ...")
        print("=" * 60)
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    main_loop()
