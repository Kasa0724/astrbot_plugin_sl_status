# 运行状态卡 · 使用说明书（astrbot_plugin_sl_status v2.0.0）

一条 `#状态`，回一张整幅插画的状态卡：CPU / RAM / DISK 进度条、今日 token 消耗、会话上下文、CPU 型号、模型、插件数、协议端版本，全部叠在磨砂面板上。纯本地 Pillow 渲染，不依赖 t2i 或浏览器。

- 作者：小喷菇
- 版本：v2.0.0
- 适配：AstrBot >= 4.0，平台 `aiocqhttp`（OneBot v11）
- 协议端：SnowLuma / NapCat / 其它 OneBot v11 实现，自动探测

---

## 一、安装

把整个插件目录放进 AstrBot 的插件目录后重启 AstrBot：

```
<AstrBot 数据目录>/plugins/astrbot_plugin_sl_status/
├── main.py
├── metadata.yaml
├── _conf_schema.json      # 面板配置项定义
├── README.md
└── assets/
    ├── bg/                # 背景图放这里（可多张）
    ├── fonts/             # 圆体，随插件走
    └── preview.png        # 预览图，不参与渲染
```

日志出现 `Plugin astrbot_plugin_sl_status (v2.0.0) by 小喷菇` 即加载成功。

从仓库安装：

```bash
cd <AstrBot 数据目录>/plugins
git clone <仓库地址> astrbot_plugin_sl_status
```

---

## 二、命令

| 命令 | 别名 | 权限 | 说明 |
|---|---|---|---|
| `状态` | `sl`、`st`、`运行状态` | 所有人 | 输出状态卡（图片，失败自动回退文字） |

群里要带唤醒前缀 `#` 或 `/`，也可以 @ 机器人；私聊直接发 `状态`。

---

## 三、面板配置项

AstrBot 管理面板 → 插件 → 运行状态卡 → 配置，共 16 项：

### 输出

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `send_image` | bool | `true` | 关掉只发纯文本 |
| `title` | string | `小喷菇 运行状态` | 卡片标题 |

### 背景

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `bg_mode` | `random` / `fixed` | `random` | `random`：每次 `#状态` 随机换一张；`fixed`：固定用 `bg_fixed` 那张 |
| `bg_fixed` | string | 空 | 固定模式下用的文件名（留空 = 用目录里第一张） |

### 显示 / 隐藏（都可单独开关）

| 键 | 默认 | 控制内容 |
|---|---|---|
| `show_cpu` | `true` | CPU 进度条 |
| `show_ram` | `true` | RAM 进度条 |
| `show_disk` | `true` | DISK 进度条 |
| `show_tokens` | `true` | TOK：今日 token 消耗 |
| `show_context` | `true` | CTX：当前会话上下文 |
| `show_cpu_model` | `true` | CPU 型号与线程数 |
| `show_model` | `true` | MODEL：当前对话模型 |
| `show_plugins` | `true` | PLG：插件数 |
| `show_protocol` | `true` | PROTO：协议端名称与版本 |
| `show_uptime` | `true` | UPTIME：运行时长 |

关掉某项后，那一行不占位置，下面的行自动上移，行距仍按固定 44px 排。

### 协议端（一般不用动）

| 键 | 默认 | 说明 |
|---|---|---|
| `api_base` | 空 | 手动指定 OneBot HTTP 地址；留空自动探测 |
| `access_token` | 空 | 手动指定 token；一般用环境变量传 |

---

## 四、换图 / 随机换图

1. 把图片放进插件目录的 `assets/bg/`，支持 `.png` / `.jpg` / `.jpeg` / `.webp`，可以放任意多张
2. `bg_mode` 保持 `random`（默认）：每次发 `#状态` 随机挑一张
3. 想固定某张：`bg_mode` 改 `fixed`，`bg_fixed` 填文件名（例如 `01_mushroom_night.png`）

图片建议：

- 横图，比例接近 1000:712（如 1216×832），代码按 cover 等比裁剪居中，不会变形
- 主体偏右下、左侧留暗部，因为信息面板压在左侧
- 命名随便，按文件名排序显示在配置提示里

没有 `assets/bg/` 或目录为空时，退回用旧版的 `assets/bg.png`，删升级不会白屏。

---

## 五、卡片内容

- 标题区：`标题` + `AstrBot 版本 · 架构 · 更新时间`
- 三条进度条：`CPU` / `RAM` / `DISK`，百分比决定填充长度，颜色分级 `<60%` 冰蓝、`60~85%` 橙、`>85%` 红
- 信息区：`TOK` / `CTX` / `CPU` / `MODEL` / `PLG` / `PROTO` / `UPTIME`
- 版式：画布 1000×712；面板 `(44,44,656,668)`；信息区显式分行、行距 44px，内容过多先缩字号再压行距（下限 34px），不会两行贴在一起
- 配色：标签 `#F4F9FF` 加粗、数值 `#DFECFA` 常规，靠字重分主次
- 字体：正文 M PLUS Rounded 1c 圆体（随插件带）；标题 Noto Sans CJK（圆体缺「菇」「喷」简体字形会出豆腐块）
- 字效：暗影 + 正文两层，标题额外加底部高光

## 六、数据来源

| 指标 | 来源 |
|---|---|
| CPU | cgroup `cpu.stat` 的 `usage_usec` 差值 ÷ 时间 ÷ 核数 |
| RAM | cgroup `memory.current` / `memory.max`（取不到退回系统内存） |
| DISK | `psutil.disk_usage("/")` |
| CPU 型号 | `cpuinfo` 的 `model name`，清理厂商后缀 |
| TOK / CTX | AstrBot 自身数据库，`mode=ro` 只读打开 |
| PROTO | 协议端 `get_version_info` / `get_status` |

网络累计流量没做：容器网卡计数器基本恒为 0（流量走宿主），显示只会误导。

## 七、协议端自适应

按顺序探测，首个可用的缓存：

1. 环境变量 `SNOWLUMA_API` / `NAPCAT_API` / `ONEBOT_API`
2. 配置 `api_base`
3. `http://snowluma:3000` → `http://napcat:3000` → `http://host.docker.internal:3000` → `http://127.0.0.1:3000`

token 取 `SNOWLUMA_TOKEN` / `NAPCAT_TOKEN` / `ONEBOT_TOKEN` / 配置 `access_token`；401 直接跳下一个地址。

---

## 八、排错

**`PROTO` 显示未连接**
确认协议端容器在跑；连不上是网络问题，401 是 token 问题。

**发出来是文字不是图片**
看日志 `渲染状态卡失败` / `发送图片失败`，或 `send_image` 被关了。

**CPU 一直 0.0%**
容器空闲时 cgroup 差值接近 0，不是 bug。

**换了图没生效**
确认图在 `assets/bg/` 里、后缀是支持的格式；`fixed` 模式下文件名要和 `bg_fixed` 完全一致。

**想少几行信息**
面板里关掉对应的 `show_*`，不需要改代码。

## 九、隐私

- 卡片只显示数值与版本号，不显示 QQ 号、昵称、群号、好友列表、文件路径、主机名
- 所有指标来自本机与 AstrBot 本地数据库，不向外发送
- 数据库只读打开，只做聚合统计
