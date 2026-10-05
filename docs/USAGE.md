# M5Stack AI 监视器

Core 一代通过局域网 Wi-Fi 显示当前正在运行的 Codex/Hermes 任务、命令审批和用量图表。

## 页面与按钮

| 页面 | 内容 |
|---|---|
| 运行列表 | 只显示运行中、等待审批、等待输入；C/H 分别代表 Codex/Hermes |
| 待审批 | 当前可处理的命令审批及命令内容 |
| 用量图表 | 近七日 Token 柱状图、输入/输出数、缓存命中率；可切换套餐额度进度条 |
| 来源 | Codex 与 Hermes 的在线情况和采集路径 |
| 主机状态 | CPU / 内存两分钟双曲线，网络 RX/TX 自动缩放曲线，磁盘占用、系统负载与开机时间；B 切换资源 / 网络 |
| 设置（独立入口） | 亮度、声音、重新配网、连接信息、返回监视 |

- A/C 短按：在五个监视页面之间前后切换，循环不包含设置。B：切换任务/审批；用量页切换 Codex/Hermes；主机页切换资源/网络。
- 运行页长按 C 0.8 秒：切换所选任务详情。
- 用量页长按 C 0.8 秒：切换 Token 图表与套餐额度。
- **待审批页长按 C 两秒：仅批准当前这一次；长按 A 两秒：拒绝。**
- 长按 B 两秒进入/返回设置，返回进入前的页面；设置中 A/C 选择，B 调整，也可选择“返回监视”后短按 B 退出。
- A+C 长按五秒重新开启配网热点。

审批不会开启整会话或永久放行，不启动、停止或发送 AI 任务。命令过长、内容不完整、数据过期或不支持的请求需在电脑上处理。输入问题、文件变更和权限扩展审批暂不提供设备响应。审批操作通过独立网络任务执行，不阻塞按钮。完成一声、等待两声、失败三声，可在设置中静音。

界面采用深色卡片、来源徽标、状态图标、带刻度的七日柱状图、缓存与额度进度条、按住审批的确认进度及切页动画。320×240 RGB565 画面通过 320×80 分块缓冲绘制（51,200 字节），仅推送变化的块；网络在另一任务执行。无需全屏大缓冲，保留 HTTP 与中文字体绘制所需的内存。

## 数据的含义

- **Codex 运行状态**来自当前桌面 IPC 快照/更新，历史轮次来自只读 SQLite。桌面异步问题不进入 IPC requests；另读 SQLite 的 async `agentMessage.questions`，按明确的问题 call ID 与用户回复 ID 判断等待输入和已回答，只保留问题短标题及 ID。仅监视本机 Desktop 根会话；内部 IPC 当前版本为 11。
- **Codex 输入/输出/缓存**是当前加载的桌面会话累计值；**每日 Token 和套餐额度**来自账户接口，包含该账户在其他设备上的使用，可能有更新延迟。Token 数不能直接换算套餐剩余百分比。
- Codex 缓存命中率为 `cachedInputTokens / inputTokens`，输入数包含缓存输入。
- **Hermes**读取默认配置目录的 `state.db`，补充已有会话；连接当前桌面的本地 WebSocket，使用 `session.active_list` 和只读 `session.events.since` 获取运行状态和当前待响应请求。不会调用 `session.resume`、`activate` 或改变会话所属前端。
- Hermes 插件继续采集 CLI/Gateway 生命周期；尚未加载插件的既有 CLI 不能仅凭会话未关闭就判断运行中。
- Hermes 累计输入为数据库的非缓存输入 + 缓存读取 + 缓存写入；命中率分母是这三项之和，分子是缓存读取。七日图按**会话开始日**归属整个会话用量，与逐调用日统计有区别。
- Hermes 提供商套餐额度当前没有可靠的结构化来源，显示“提供商未返回套餐额度”，不显示假零值。
- 来源离线不等于任务失败。无明确结果时显示未知；十秒没有有效快照，保留画面并标记数据过期。

## 本机安装与配网

服务 `ai-monitor.service` 监听 **8766**，随用户登录启动。可在本地 config.json 中调整端口。
私有 `~/.config/ai-monitor/pairing.txt` 保存服务地址与监视 Token；不需要重新输入 Codex/Hermes 的 API Key。

1. 手机连接屏幕上的 `AI-Monitor-XXXX` 热点，密码使用当前屏幕显示值。即使提示无互联网，也保持连接。
2. 打开 `http://192.168.4.1`，填写 2.4GHz Wi-Fi、服务 URL、监视 Token。远程审批优先使用 pairing.txt 中的 **LAN IP 地址**。
3. 新配置通过 Wi-Fi 与服务认证验证后保存到 NVS，关闭热点。失败可返回修改，保留旧配置。

设备是 ESP32-D0WDQ6 revision 1、CP2104、**4MB Flash**，支持 2.4GHz Wi-Fi。
`.local` 地址可用于状态查询；局域网不支持 mDNS 时使用 LAN IP。

## 构建与更新

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r host/requirements.txt
.venv/bin/python tools/install-host.py
pio run
.venv/bin/python tools/check-firmware-stack.py
pio run -t upload
```

固件依赖 PlatformIO Espressif32 6.7.0、Arduino ESP32 2.0.16、M5Unified 0.2.25、M5GFX 0.2.32、ArduinoJson 6.21.5。
`huge_app.csv` 在 4MB Flash 中提供 3MB 程序分区；烧录速度 115200。USB 更新不会覆盖 NVS，无双镜像 OTA。
升级前运行 `tools/backup-device.sh` 保存完整 4MB Flash；备份保存在被 Git 忽略的 `backups/`。备份可能包含 NVS 私有配置，应妥善保管。

Codex 账户数据使用独立 `codex app-server` 子进程，只调用初始化、`account/rateLimits/read` 和 `account/usage/read`；它**不是**现有桌面任务的状态来源。额度一分钟刷新，每日统计五分钟刷新。该进程随服务退出。
Hermes 桌面连接只读取其本地桥接 Token，保留在主机内存中，不向设备传输提供商账号凭据。

## 接口与审批边界

所有接口使用 `Authorization: Bearer <monitor token>`，供可信局域网访问。

- `GET /api/v1/snapshot?source=all|codex|hermes&cursor=...&view=all|active`：版本 1；默认兼容历史视图，设备使用 active；每页最多十个任务、二十个事件，响应上限 16KB。
- 响应增加 `metrics` 与 `approvals`。套餐百分比来自原始 quota 窗口；未知值保持未知。
- `GET /api/v1/host`：认证后的独立主机快照，返回 CPU、内存、根磁盘、load、uptime、默认出口网卡 RX/TX 字节速率及 60 个历史点。设备仅在主机页（或从主机页进入设置时）额外请求该接口，不挤占 AI 快照的 16KB 上限；两者复用同一个 JSON 解析区。`snapshot?host=1` 也可选附带主机数据，仍受原 16KB 上限约束。
- `POST /api/v1/approvals/respond`：JSON `{"id":"<opaque approval id>","choice":"once|deny"}`。目标来自服务当前待审批表，客户端不能指定任意任务或命令。
- 后端再次检查原始请求 ID，按精确请求响应，拒绝过期与重复请求；不接受 session/always。
- Codex 通过原 Desktop IPC owner 转发 command approval；Hermes 通过原 Desktop backend 转发 `approval.respond`。不提供通用 RPC 转发接口。

## 验证与诊断

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/verify-live.py
.venv/bin/python tools/check-firmware-stack.py
.venv/bin/python tools/device.py info
.venv/bin/python tools/device.py screen --output .private/device-screen.ppm
```

USB 只读界面诊断支持 `TAB 0..4`、`SETTINGS`、`BACK`、`NEXT_SETTING`、`SELECT_BACK`、`DETAIL`、`METRIC`、`MODE` 、`HOST_MODE` 和 `PERF`，不触发审批决定。`PERF` 返回实际整帧绘制时间与分块缓冲启用情况。

SDK 调试日志已关闭，避免 Arduino 2.0.16 在长串口截图期间遇到网络错误时忙等 TX-idle 并触发看门狗；连接错误仍通过屏幕和 INFO 报告。

INFO 含网络任务最低剩余栈 `network_stack_free`。部分串口驱动在打开连接时会重启设备，避免配网过程中反复打开诊断工具。
LCD 截图可能包含临时热点密码，应保存到私有目录。测试不替用户批准真实请求，也不额外发送模型消息。

长时间观察工具 `tools/soak.py` 仅供手动诊断，本次八小时观察已按用户要求取消。短测结果与实机验证边界见 [验证记录](../VALIDATION.md)。

响铃后 JSON 解析使用启动时预留的 32KB 堆缓冲并复用，避免播放声音后再申请大块内存。INFO 同时报告 `max_heap_block`，解析失败显示具体错误类型。

## 主机曲线的数据范围

采集 Linux `/proc/stat`、`meminfo`、`uptime`、`loadavg` 和网卡计数，不采集进程命令行或文件内容。CPU 使用相邻总计数之差，guest 已计入 user/nice，不重复累加；idle 与 iowait 视为空闲。内存使用量为 MemTotal - MemAvailable，磁盘对应 `/`。默认按最低 metric 的 IPv4 默认路由选择网卡，可在私有 config.json 中用 `host_interface` 覆盖，不叠加虚拟网卡。网络单位为 B/s、KiB/s、MiB/s。

历史只在服务内存中保留最近 60 点，每两秒采样一次，图表横轴显示最近两分钟。服务重启从新样本开始；主机重启或出口网卡变化会清空旧曲线。首次样本、计数重置或缺失网卡显示未知；采集失败保留上次值及原始时间并明确标记失败。
