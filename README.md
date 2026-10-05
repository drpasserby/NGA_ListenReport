# NGA 举报监听

定时抓取 NGA 版务举报列表，通过 [Server酱 3](https://sc3.ft07.com/) 推送新举报到手机。

---

## ✨ 项目亮点

- **两套监测模式，各自独立开关与间隔**
  - `regular`（常规举报通知）：发现新举报即推送。
  - `frequent.mode1`（高频举报告警）：同一帖子在滑动时间窗口内被举报次数达到阈值时告警，用于发现"被集中围攻"的帖子。
- **全量请求 + 增量推送**：每轮抓全量列表，用 `cache.json` 去重，只推送本轮新增；无新增时完全不打扰。
- **推送合并**：一轮里的多条举报合并成**一条**消息，避免手机被刷屏；正文逐条带用户主页、帖子、回复的直达链接。
- **免打扰时段**：支持 `23:00-07:00` 这类跨天区间；时段内的新举报先暂存，时段结束后统一补推。
- **版面过滤**：按关键字匹配版面名（如 `"原神"` 可命中 `原神>剑斗绮谭`），只关注你负责的版块。
- **推送失败自动重试**：推送异常时举报进入暂存，下一轮继续尝试，不丢事件。
- **告警冷却**：同一帖子的高频告警在冷却期内只推一次，避免重复轰炸。
- **日志可控**：`print_log: false` 一键静默终端输出，适合交给 PM2 / Supervisor 托管。

---

## 📦 安装

需要 Python 3.8 或更高版本（开发环境为 Python 3.13）。

```bash
git clone <你的仓库地址> NGA_ListenReport
cd NGA_ListenReport
pip install -r requirements.txt
```

依赖只有三个：

| 依赖 | 用途 |
|------|------|
| `requests` | 请求 NGA 通知接口 |
| `pyyaml` | 读取 `config.yaml` |
| `serverchan-sdk` | 调用 Server酱 3 推送 |

---

## 🚀 快速开始

```bash
# 1. 从模板生成配置文件
cp config.yaml.default config.yaml      # Windows: copy config.yaml.default config.yaml

# 2. 编辑 config.yaml，至少填好 cookie 和 serverchan.sendkey

# 3. 运行
python main.py
```

> Windows 用户也可以直接双击 `【Windows直接启动】NGA举报列表监测.bat`（脚本内容即 `python main.py` + `pause`）。
> 启动后终端会打印当前生效的模式、间隔、过滤与免打扰设置；之后进入常驻循环，按 Ctrl+C 停止。

---

## ⚙️ 配置说明

配置文件为 `config.yaml`，模板见 `config.yaml.default`。

### 必填项

| 字段 | 说明 |
|------|------|
| `cookie` | NGA 登录 Cookie。F12 → 网络 → 随便点一个 `bbs.nga.cn` 请求 → 复制完整 Cookie 字符串。**必须包含登录态字段**（如 `ngaPassportUid`、`ngaPassportCid`），否则拿不到举报数据 |
| `serverchan.sendkey` | Server酱 3 的 SendKey，在 [sc3.ft07.com](https://sc3.ft07.com/) 获取，并确认已绑定推送渠道（APP） |

### 全局项

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `print_log` | `true` | 是否输出终端日志；`false` 时完全静默 |

### `modes.regular` — 常规举报通知

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 是否启用 |
| `interval_minutes` | `5` | 检测与推送间隔（分钟），建议 ≥ 5 |
| `dnd_hours` | `[]` | 免打扰时段，支持多段与跨天，如 `["23:00-07:00", "12:00-13:30"]` |
| `monitor_forums` | `[]` | 版面关键字白名单，命中任意一个即监测；留空表示监测全部 |

### `modes.frequent.mode1` — 高频举报告警

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false`（模板中给的是 `true`） | 是否启用 |
| `interval_minutes` | `5` | 检测与推送间隔（分钟），建议 ≥ 5 |
| `window_minutes` | `30` | 滑动统计窗口长度（分钟） |
| `threshold` | `3` | 窗口内被举报次数 ≥ 此值即告警 |
| `alert_cooldown_minutes` | `60` | 同一帖子的告警冷却时间（分钟） |

### 几个容易踩的点

1. **抓取间隔是共用的**。程序取所有已启用模式 `interval_minutes` 的**最小值**作为实际抓取周期，各模式再按自己的间隔决定这一轮是否执行。例如常规 5 分钟、高频 10 分钟，则每 5 分钟抓一次，高频模式每隔一轮才处理。
2. **`monitor_forums` 是共享的**。它挂在 `modes.regular` 下，但版面过滤在两种模式之前统一执行，所以高频检测同样只统计监测版面内的举报。
3. **高频模式沿用常规模式的免打扰设置**。目前高频告警没有独立的 `dnd_hours`；免打扰期间检测照常进行、数据照常记录，只是不推送。

---

## 🧭 推送消息长什么样

**常规举报提醒** — 标题 `NGA 举报提醒 (2 条)`：

```markdown
[本次运行时间：2025-06-01 21:30:12]

- 【06-01 21:27】【[张三](https://bbs.nga.cn/nuke.php?func=ucp&uid=12345)】举报了【原神>剑斗绮谭】中【[某标题](https://bbs.nga.cn/read.php?tid=46600000)】的主题，理由是【人身攻击】。

- 【06-01 21:28】【[李四](https://bbs.nga.cn/nuke.php?func=ucp&uid=67890)】举报了【原神>剑斗绮谭】中【[某标题](https://bbs.nga.cn/read.php?tid=46600000)】的【[回复](https://bbs.nga.cn/read.php?tid=46600000&pid=46727951&to=1)】，理由是【引战】。
```

**高频举报告警** — 标题 `⚠️ NGA 高频举报告警 - TID:46600000 (5次/30分钟)`：

```markdown
[告警时间：2025-06-01 21:30:12]

## ⚠️ 高频举报告警

帖子 [某标题](https://bbs.nga.cn/read.php?tid=46600000)（TID: 46600000）
在最近 **30** 分钟内被举报 **5** 次，超过阈值。

### 近期举报详情：
- 【06-01 21:27】【[张三](https://bbs.nga.cn/nuke.php?func=ucp&uid=12345)】举报了【原神>剑斗绮谭】中的主题，理由是【人身攻击】
- ...
```

单条告警最多展示最近 10 条，超出部分以 `... 还有 N 条举报` 收尾。

---

## 🧠 工作流程

```
        ┌──────────────────────────────────────────────┐
        │  主循环 main_loop()                          │
        │  每 15s 检查一次：哪个模式到点了？           │
        └──────────────────┬───────────────────────────┘
                           │  取最小间隔，抓取一次
                           ▼
      POST bbs.nga.cn/nuke.php?__lib=noti&raw=3  (__act=get_all)
                           │
                           ▼
   解析 window.script_muti_get_var_store
   → 大括号配对取出 JSON 对象 → 修复无引号数字键 → json.loads
                           │
                           ▼
                  版面过滤 monitor_forums
                           │
            ┌──────────────┴───────────────┐
            ▼                              ▼
  【常规】去重键比对 seen_keys      【高频】追加进滑动窗口
   新增 → 免打扰？                 按 tid 统计窗口内次数
     是 → 暂存 pending_reports      超阈值 且 不在冷却期？
     否 → 合并推送                   ├ 免打扰 → 仅记录，不推送
  （推送失败 → 暂存下轮重试）        └ 否则 → 推送告警并记录冷却
            │                              │
            └──────────────┬───────────────┘
                           ▼
                  统一写回 cache.json
```

### 技术细节

- **解析策略**：NGA 把举报数据塞在页面内联脚本 `window.script_muti_get_var_store = {...}` 里。程序用大括号配对的方式截出这段 JSON（而非贪婪正则），再补上 JSON 规范要求的键引号，最后 `json.loads`。比直接正则匹配数组更耐受页面变动。
- **举报数据是数字键数组**，脚本中的映射关系：

  | 键 | 含义 | 键 | 含义 |
  |----|------|----|------|
  | `0` | 举报类型（`13`=主题，`14`=回复） | `7` | 回复 `pid` |
  | `1` | 举报人 `uid` | `9` | 举报时间戳 |
  | `2` | 举报人昵称 | `11` | 举报理由 |
  | `5` | 帖子标题 | `13` | 所属版面 |
  | `6` | 帖子 `tid` | | |

- **去重键**：`{时间戳}_{uid}_{tid}_{pid}`，四元组唯一确定一条举报。
- **缓存结构** `cache.json`：

  ```json
  {
    "seen_keys": ["1778147234_42904144_46727951_0"],
    "pending_reports": [],
    "frequent": {
      "mode1_reports": [{"tid": 46600000, "ts": 1778147234, "nick": "…"}],
      "mode1_alerted": {"46600000": 1778147234}
    }
  }
  ```

  代码兼容早期的"裸数组"格式，升级时会自动平滑迁移。
- **免打扰判定**：按 `HH:MM` 字符串比较；`start > end` 时视为跨天区间，命中条件为 `now >= start or now <= end`。

---

## 📁 项目结构

```
NGA_ListenReport/
├─ main.py                                 # 全部逻辑：抓取 / 解析 / 去重 / 高频统计 / 推送
├─ config.yaml.default                     # 配置模板（当前 v2 结构）
├─ config.yaml                             # 你的私有配置（已在 .gitignore 中忽略）
├─ cache.json                              # 运行时去重与暂存状态（已在 .gitignore 中忽略）
├─ requirements.txt                        # 依赖清单
├─ 【Windows直接启动】NGA举报列表监测.bat  # Windows 双击启动
├─ TODO.md                                 # 待办 / 已完成
├─ LICENSE                                 # GPL-3.0
└─ README.md
```


---

## 🖥️ 云端部署

```bash
# PM2（推荐）
pm2 start main.py --name nga-listen --interpreter python3
pm2 save
pm2 startup

# Supervisor
# 在 supervisord.conf 中加入：
# [program:nga-listen]
# command=python3 /path/to/NGA_ListenReport/main.py
# directory=/path/to/NGA_ListenReport
# autorestart=true
# stdout_logfile=/var/log/nga-listen.log
```

注意事项：

- `main.py` 使用**相对路径**读写 `config.yaml` 与 `cache.json`，所以必须保证工作目录是项目根目录（Supervisor 用 `directory=`，PM2 用 `--cwd`）。
- 程序本身是常驻循环、不退出，交给 PM2 / Supervisor 托管时**不要**配置过短的自动重启间隔，否则会反复重置内存中的计时器；日志交给 `print_log: false` + 进程管理器收集即可。
- 抓取频率建议 ≥ 5 分钟，别把 NGA 服务器打爆。
- 🔒 **`config.yaml` 含真实 Cookie / SendKey，不要把它提交进 Git，也不要粘贴到公开 Gist、在线 YAML 编辑器、网盘或任何第三方网站。** 该文件已在 `.gitignore` 中被忽略；传到服务器时请走 `scp` / SFTP 等加密通道单独传送，或者干脆先在服务器上 `cp config.yaml.default config.yaml` 再手动填写真实值。
- 🔒 Cookie 或 SendKey 一旦泄露到公开渠道，请立刻到 NGA 重新登录（旧 Cookie 自动失效）并在 Server酱 后台重置 SendKey。

---

## ❓ 已知限制与注意事项

- **`seen_keys` 不会自动清理**，会随运行时间线性增长。长期运行后 `cache.json` 会变大；条数过多时可手动删掉 `cache.json`（会重新推送一次当前列表里的所有举报）。
- **推送成功与否以"是否抛异常"为判据**。`serverchan-sdk` 正常返回错误码时不会抛异常，此时暂存仍会被清空。如果需要严格的送达确认，需要自行检查 `sc_send` 的返回内容。
- **高频统计依赖缓存连续性**。删除 `cache.json` 会同时清空滑动窗口，重启后需要重新积累窗口内的举报才能触发告警。
- **Cookie 会过期**，失效后表现为日志里"解析策略失败，返回 0 条举报"。重新登录抓一次 Cookie 即可。
- 本项目仅供**个人自用**，请勿大规模分发或商用。

---

## 📝 License

[GNU General Public License v3.0](LICENSE)
