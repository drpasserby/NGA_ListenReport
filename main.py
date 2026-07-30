"""
NGA 举报通知监听脚本
定时抓取 NGA 举报数据，通过 Server酱 3 推送举报通知到手机。

支持模式（各模式独立开关与推送间隔，在 config.yaml 的 modes 段配置）：
  1. 常规举报通知 — 检测新举报并推送提醒
  2. 高频举报检测（模式1）— 同一帖子在时间窗口内被举报次数超阈值时推送告警

Version: 2.0.1

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
import sys
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


_PRINT_LOG = True  # 运行时由 main_loop 根据 config 覆盖


def log(*args, **kwargs):
    """条件打印：仅当 _PRINT_LOG 为 True 时输出到终端。"""
    if not _PRINT_LOG:
        return
    sep = kwargs.pop("sep", " ")
    end = kwargs.pop("end", "\n")
    fl = kwargs.pop("file", sys.stdout)
    fl.write(sep.join(str(a) for a in args) + end)
    fl.flush()


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------

def load_cache():
    """
    加载缓存文件，兼容旧格式。
    返回 (seen_keys: set, pending_reports: list, frequent_data: dict)。

    缓存结构：
    {
        "seen_keys": [...],          // 常规通知：已处理举报的去重键
        "pending_reports": [...],    // 常规通知：免打扰期间暂存的举报
        "frequent": {                // 高频检测：模式1 滑动窗口与冷却记录
            "mode1_reports": [
                {"tid": 123, "ts": 1700000000, "nick": "...", ...}
            ],
            "mode1_alerted": {
                "123": 1700000000
            }
        }
    }
    """
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data), [], {"mode1_reports": [], "mode1_alerted": {}}
        seen = set(data.get("seen_keys", []))
        pending = data.get("pending_reports", [])
        freq = data.get("frequent", {})
        freq.setdefault("mode1_reports", [])
        freq.setdefault("mode1_alerted", {})
        return seen, pending, freq
    return set(), [], {"mode1_reports": [], "mode1_alerted": {}}


def save_cache(seen_keys, pending_reports, frequent_data):
    """保存缓存文件。"""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "seen_keys": list(seen_keys),
            "pending_reports": pending_reports,
            "frequent": frequent_data
        }, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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

    if "window.script_muti_get_var_store" in text:
        reports = extract_reports_from_html(text)
        if reports is not None:
            return reports

    log("[警告] 解析策略失败，返回 0 条举报")
    return []


def _extract_reports(root):
    """从解析后的 JSON 对象中提取举报数组。"""
    try:
        reports = root["data"]["0"]["1"]
        if isinstance(reports, list):
            return reports
    except (KeyError, TypeError):
        pass

    try:
        reports = root["data"]["1"]
        if isinstance(reports, list):
            return reports
    except (KeyError, TypeError):
        pass

    return None


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
            if now >= start or now <= end:
                return True
    return False


# ---------------------------------------------------------------------------
# 模式1：常规举报通知
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


# ---------------------------------------------------------------------------
# 模式2：高频举报检测（模式1 — 同帖高频举报）
# ---------------------------------------------------------------------------

def push_mode1_alert(sendkey, tid, title_text, count, window_minutes, recent_reports):
    """推送模式1 高频举报告警到 Server酱 3。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    thread_link = f"[{title_text or '查看帖子'}]({THREAD_URL.format(tid)})"

    desp_lines = [
        f"[告警时间：{now_str}]",
        "",
        f"## ⚠️ 高频举报告警",
        "",
        f"帖子 {thread_link}（TID: {tid}）",
        f"在最近 **{window_minutes}** 分钟内被举报 **{count}** 次，超过阈值。",
        "",
        "### 近期举报详情：",
    ]

    for r in recent_reports[-10:]:
        ts = r.get("ts", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        nick = r.get("nick", "?")
        uid = r.get("uid", 0)
        reason = r.get("reason", "")
        forum = r.get("forum", "")
        rtype = r.get("rtype", 0)
        pid = r.get("pid", 0)

        user_link = f"[{nick}]({USER_URL.format(uid)})"
        if rtype == 13:
            target_str = "的主题"
        elif rtype == 14:
            target_str = f"的【[回复]({REPLY_URL.format(tid, pid)})】"
        else:
            target_str = f"的未知类型({rtype})"

        desp_lines.append(
            f"- 【{time_str}】【{user_link}】"
            f"举报了【{forum}】中{target_str}，理由是【{reason}】"
        )

    if len(recent_reports) > 10:
        desp_lines.append(f"- ... 还有 {len(recent_reports) - 10} 条举报")

    desp = "\n".join(desp_lines)
    title = f"⚠️ NGA 高频举报告警 - TID:{tid} ({count}次/{window_minutes}分钟)"
    return sc_send(sendkey, title, desp, {"tags": "NGA高频监测"})


def check_mode1(reports, frequent_data, mode1_cfg, sendkey, dnd):
    """
    模式1：检测同一帖子在时间窗口内的举报次数是否超过阈值。

    1. 将本轮新举报追加到滑动窗口（同时保存详情字段供推送使用）
    2. 清理超出时间窗口的旧记录
    3. 按 tid 分组统计举报次数
    4. 对超过阈值且不在冷却期内的 tid 推送告警
    5. 记录告警时间用于冷却控制

    参数:
        reports: 本轮举报列表（原始格式，可为空列表）
        frequent_data: 缓存中的 frequent 子对象
        mode1_cfg: 模式1配置 {window_minutes, threshold, alert_cooldown_minutes}
        sendkey: Server酱 sendkey
        dnd: 当前是否处于免打扰时段
    返回:
        更新后的 frequent_data
    """
    window_seconds = mode1_cfg["window_minutes"] * 60
    threshold = mode1_cfg["threshold"]
    cooldown_seconds = mode1_cfg.get("alert_cooldown_minutes", 60) * 60

    now_ts = time.time()
    cutoff_ts = now_ts - window_seconds

    mode1_reports = frequent_data.get("mode1_reports", [])
    mode1_alerted = frequent_data.get("mode1_alerted", {})

    # 1. 追加本轮新举报到滑动窗口
    for r in reports:
        tid = r.get("6", 0)
        ts = r.get("9", 0)
        if tid and ts:
            mode1_reports.append({
                "tid": tid,
                "ts": ts,
                "nick": r.get("2", ""),
                "title": r.get("5", ""),
                "reason": r.get("11", ""),
                "forum": r.get("13", ""),
                "uid": r.get("1", 0),
                "pid": r.get("7", 0),
                "rtype": r.get("0", 0),
            })

    # 2. 清理过期记录
    prev_count = len(mode1_reports)
    mode1_reports = [r for r in mode1_reports if r["ts"] >= cutoff_ts]
    if prev_count > len(mode1_reports):
        log(f"[高频-模式1] 清理 {prev_count - len(mode1_reports)} 条过期窗口记录，"
            f"当前窗口内 {len(mode1_reports)} 条")

    # 3. 按 tid 分组统计
    tid_counts = {}
    for r in mode1_reports:
        tid = r["tid"]
        tid_counts[tid] = tid_counts.get(tid, 0) + 1

    # 4. 检查阈值触发
    alerted_count = 0
    for tid, count in tid_counts.items():
        if count < threshold:
            continue

        # 冷却检查
        last_alert_ts = mode1_alerted.get(str(tid), 0)
        if now_ts - last_alert_ts < cooldown_seconds:
            remaining = cooldown_seconds - (now_ts - last_alert_ts)
            log(f"[高频-模式1] TID:{tid} 窗口内被举报 {count} 次，"
                f"冷却剩余 {int(remaining / 60)} 分钟，跳过告警")
            continue

        tid_reports = [r for r in mode1_reports if r["tid"] == tid]
        title_text = next((r["title"] for r in tid_reports if r["title"]), "")

        log(f"[高频-模式1] ⚠️ TID:{tid} 在 {mode1_cfg['window_minutes']} 分钟内"
            f"被举报 {count} 次（阈值 {threshold}），触发告警！")

        if dnd:
            log(f"[高频-模式1] 当前处于免打扰时段，告警推送已抑制（数据已记录）")
            continue

        try:
            resp = push_mode1_alert(sendkey, tid, title_text, count,
                                    mode1_cfg["window_minutes"], tid_reports)
            log(f"  [推送] 返回: {resp}")
            mode1_alerted[str(tid)] = now_ts
            alerted_count += 1
        except Exception as e:
            log(f"  [推送] 失败: {e}")

    if alerted_count > 0:
        log(f"[高频-模式1] 本轮共推送 {alerted_count} 条高频告警")

    frequent_data["mode1_reports"] = mode1_reports
    frequent_data["mode1_alerted"] = mode1_alerted
    return frequent_data


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

def main_loop():
    global _PRINT_LOG
    config = load_config()
    _PRINT_LOG = config.get("print_log", True)

    # ---- 全局配置 ----
    sendkey = config["serverchan"]["sendkey"]
    cookies = parse_cookie(config["cookie"])

    # ---- 模式配置 ----
    modes_cfg = config.get("modes", {})

    # 常规举报通知
    regular_cfg = modes_cfg.get("regular", {})
    regular_enabled = regular_cfg.get("enabled", True)
    regular_interval = regular_cfg.get("interval_minutes", 5)
    regular_dnd = regular_cfg.get("dnd_hours", [])
    regular_forums = regular_cfg.get("monitor_forums", [])

    # 高频举报检测
    frequent_cfg = modes_cfg.get("frequent", {})
    mode1_cfg = frequent_cfg.get("mode1", {})
    mode1_enabled = mode1_cfg.get("enabled", False)
    mode1_interval = mode1_cfg.get("interval_minutes", 5)

    # ---- 共用抓取间隔（取所有启用模式间隔的最小值）----
    enabled_intervals = []
    if regular_enabled:
        enabled_intervals.append(regular_interval)
    if mode1_enabled:
        enabled_intervals.append(mode1_interval)
    fetch_interval = min(enabled_intervals) if enabled_intervals else 10

    # ---- 各模式上次执行时间戳（0 = 首次总是执行）----
    last_regular_ts = 0.0
    last_mode1_ts = 0.0

    seen_keys, pending_reports, frequent_data = load_cache()

    # ---- 启动日志 ----
    log(f"[启动] NGA 举报监听脚本")
    if regular_enabled:
        log(f"[启动] 常规通知: 间隔 {regular_interval} 分钟 | "
            f"{'限定版面 ' + str(regular_forums) if regular_forums else '全部版面'}"
            + (f" | 免打扰 {regular_dnd}" if regular_dnd else ""))
    else:
        log(f"[启动] 常规通知: 未启用")
    if mode1_enabled:
        log(f"[启动] 高频检测-模式1: 间隔 {mode1_interval} 分钟 | "
            f"窗口 {mode1_cfg.get('window_minutes', 30)} 分钟 | "
            f"阈值 {mode1_cfg.get('threshold', 3)} 次 | "
            f"冷却 {mode1_cfg.get('alert_cooldown_minutes', 60)} 分钟")
    else:
        log(f"[启动] 高频检测-模式1: 未启用")
    log(f"[启动] 共用抓取间隔: {fetch_interval} 分钟, 已缓存: {len(seen_keys)} 条")
    if pending_reports:
        log(f"[启动] 有待推送的暂存举报: {len(pending_reports)} 条")
    log("=" * 60)

    while True:
        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 判断本轮哪些模式需要执行
        do_regular = regular_enabled and (now_ts - last_regular_ts >= regular_interval * 60)
        do_mode1 = mode1_enabled and (now_ts - last_mode1_ts >= mode1_interval * 60)

        if not do_regular and not do_mode1:
            time.sleep(15)
            continue

        mode_tags = []
        if do_regular:
            mode_tags.append("常规通知")
        if do_mode1:
            mode_tags.append("高频检测")
        log(f"\n[{now_str}] === 开始抓取 ({', '.join(mode_tags)}) ===")

        try:
            reports = fetch_reports(cookies)
            total_count = len(reports)

            # 版面过滤（共享：来自 regular 模式的 monitor_forums 配置）
            if regular_forums:
                reports = [r for r in reports if any(
                    kw in r.get("13", "") for kw in regular_forums
                )]
                skipped = total_count - len(reports)
                if skipped:
                    log(f"[过滤] 忽略 {skipped} 条非监测版面的举报")

            log(f"[信息] 获取到 {len(reports)} 条举报 (共抓取 {total_count} 条)")

            # ================================================================
            # 模式：常规举报通知
            # ================================================================
            if do_regular:
                dnd = is_dnd_time(regular_dnd)
                if dnd:
                    log("[免打扰] 当前处于免打扰时段，常规举报将延迟推送")

                new_ones = []
                for r in reports:
                    ck = cache_key(r)
                    if ck not in seen_keys:
                        new_ones.append(r)
                        seen_keys.add(ck)

                if new_ones:
                    log(f"[常规] 新增 {len(new_ones)} 条 (本次共抓取 {len(reports)} 条):")
                    for i, r in enumerate(new_ones, 1):
                        rtype = "主题" if r.get("0") == 13 else "回复"
                        nick = r.get("2", "?")
                        title = r.get("5", "?")
                        reason = r.get("11", "")
                        forum = r.get("13", "")
                        log(f"  [{i:02d}] [{rtype}] [{forum}] {nick} - {title}")
                        log(f"       理由: {reason}")
                    log("-" * 60)

                    if dnd:
                        pending_reports.extend(new_ones)
                        log(f"[免打扰] {len(new_ones)} 条举报已暂存，累计 {len(pending_reports)} 条")
                    else:
                        to_push = pending_reports + new_ones
                        pushed_ok = False
                        log(f"[推送] 正在推送 {len(to_push)} 条..."
                            + (f" (含免打扰暂存 {len(pending_reports)} 条)" if pending_reports else ""))
                        try:
                            resp = push_new_reports(sendkey, to_push)
                            log(f"  [推送] 返回: {resp}")
                            pushed_ok = True
                        except Exception as e:
                            log(f"  [推送] 失败: {e}，暂存保留")

                        if pushed_ok:
                            pending_reports = []
                        else:
                            pending_reports.extend(new_ones)
                else:
                    if pending_reports and not dnd:
                        log(f"[推送] 免打扰已结束，推送暂存的 {len(pending_reports)} 条举报...")
                        pushed_ok = False
                        try:
                            resp = push_new_reports(sendkey, pending_reports)
                            log(f"  [推送] 返回: {resp}")
                            pushed_ok = True
                        except Exception as e:
                            log(f"  [推送] 失败: {e}，暂存保留")

                        if pushed_ok:
                            pending_reports = []
                    else:
                        log(f"[常规] 无新增举报 (本次共抓取 {len(reports)} 条)"
                            + (f", 待推送暂存 {len(pending_reports)} 条" if pending_reports else ""))

                last_regular_ts = now_ts

            # ================================================================
            # 模式：高频举报检测（模式1）
            # ================================================================
            if do_mode1:
                # 高频检测使用常规模式的 DND 设置（可后续独立配置）
                dnd = is_dnd_time(regular_dnd)
                if dnd:
                    log("[免打扰] 当前处于免打扰时段，高频告警将延迟推送")

                frequent_data = check_mode1(
                    reports, frequent_data, mode1_cfg, sendkey, dnd
                )
                last_mode1_ts = now_ts

            # ================================================================
            # 统一保存缓存
            # ================================================================
            save_cache(seen_keys, pending_reports, frequent_data)
            cache_parts = [f"[缓存] 已更新, 共 {len(seen_keys)} 条记录"]
            if pending_reports:
                cache_parts.append(f"待推送暂存 {len(pending_reports)} 条")
            if mode1_enabled:
                cache_parts.append(f"窗口内 {len(frequent_data.get('mode1_reports', []))} 条高频记录")
            log(", ".join(cache_parts))

        except requests.Timeout:
            log("[错误] 请求超时")
        except Exception as e:
            log(f"[错误] {e}")

        # 动态计算下次等待时间
        elapsed = time.time() - now_ts
        log(f"\n[等待] {fetch_interval} 分钟后下一轮 ...")
        log("=" * 60)
        time.sleep(max(0, fetch_interval * 60 - elapsed))


if __name__ == "__main__":
    main_loop()
