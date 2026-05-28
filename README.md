# NGA 举报监听

定时抓取 NGA 版务举报列表，通过 [Server酱 3](https://sc3.ft07.com/) 推送新举报到手机。

## 功能特点

1. **举报监测**: 定时抓取 NGA 版务提醒列表（限最新 50 条），自动识别新增举报
2. **去重推送**: 全量请求、增量推送，多条举报合并为一条 Server酱消息，无新增不推送
3. **免打扰模式**: 可设置免打扰时段，该时段内举报暂存，结束后统一推送
4. **版面过滤**: 支持按关键字限定监测版面，只关注指定版块的举报
5. **云端部署**: 配合 PM2 / Supervisor 在服务器长期运行，无需人工值守
6. **日志可控**: 支持开关终端日志输出，运行记录可通过 `cache.json` 查看

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制并填写配置
cp config.yaml.default config.yaml

# 3. 运行
python main.py
```

> Windows 用户可直接双击 `【Windows直接启动】NGA举报列表监测.bat`

## 配置说明

编辑 `config.yaml`，必填项：

| 字段 | 说明 |
|------|------|
| `cookie` | NGA 登录 Cookie，按 F12 → 网络 → 复制完整 Cookie 字符串 |
| `serverchan.sendkey` | Server酱 3 的 SendKey，在 [sc3.ft07.com](https://sc3.ft07.com/) 获取 |

可选配置：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `interval_minutes` | `5` | 抓取间隔（分钟） |
| `monitor_forums` | `[]` | 限定监测的版面关键字，如 `["原神", "崩坏"]`；留空监测全部 |
| `dnd_hours` | `[]` | 免打扰时段，如 `["23:00-07:00"]`；该时段内举报暂存，结束后推送 |
| `print_log` | `true` | 是否在终端输出日志 |

## 工作流程

```
定时抓取 → 解析举报数组 → 版面过滤 → 去重比对 → 聚合推送
                              │
                        免打扰时段？ → 暂存，结束后统一推送
```

- 全量请求，仅推送**新增**的举报（通过缓存 `cache.json` 去重）
- 多条新举报**合并为一条** Server酱消息推送
- 推送失败时举报自动暂存，下轮重试

## 云端部署

```bash
# PM2（推荐）
pm2 start main.py --name nga-listen --interpreter python3
pm2 save

# Supervisor
# 在 supervisord.conf 中添加：
# [program:nga-listen]
# command=python3 /path/to/main.py
# autorestart=true
```

## 许可证

[GNU General Public License v3.0](LICENSE)
