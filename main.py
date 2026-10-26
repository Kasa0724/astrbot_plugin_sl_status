"""运行状态卡（astrbot_plugin_sl_status）v1.7

- 命令 #状态（别名 sl / 运行状态 / st）
- 输出：整幅插画背景 + 磨砂面板；CPU / RAM / DISK 进度条；TOKENS / CONTEXT / CPU 型号等
- 排版：显式分行（每行 1 或 2 组），行距固定 44px，绝不叠字；高度不够时先缩字号再缩行距
- 协议端自适应：SnowLuma / NapCat / 其它 OneBot v11
"""

import asyncio
import os
import random
import sqlite3
import uuid
import time
from datetime import datetime, timedelta, timezone

import aiohttp
import astrbot
import psutil
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except Exception:  # pragma: no cover
    Image = None

TZ = timezone(timedelta(hours=8))

try:
    psutil.cpu_percent(interval=None)  # 预热，psutil 首调恒 0
except Exception:  # noqa: BLE001
    pass

FONT_REGULAR = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
FONT_BOLD = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
]

ENV_ENDPOINTS = ("SNOWLUMA_API", "NAPCAT_API", "ONEBOT_API")
ENV_TOKENS = ("SNOWLUMA_TOKEN", "NAPCAT_TOKEN", "ONEBOT_TOKEN")
DEFAULT_ENDPOINTS = (
    "http://snowluma:3000",
    "http://napcat:3000",
    "http://host.docker.internal:3000",
    "http://127.0.0.1:3000",
)

W, H = 1000, 712
PANEL = (44, 44, 656, 668)
BG = (10, 21, 36)
ACCENT = (127, 212, 255)
WARN = (255, 204, 128)
DANGER = (255, 138, 128)
LABEL = (244, 249, 255)
VALUE = (223, 236, 250)
TRACK = (32, 52, 78)


# ---------------- 小工具 ----------------
def _hum(seconds: float) -> str:
    seconds = int(max(0, seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    return f"{m}m"


def _gb(n: float) -> float:
    return n / 1024 ** 3


def _k(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _pick(paths):
    for p in paths:
        if os.path.isfile(p):
            return p
    return None


def _color_for(pct: float):
    if pct >= 85:
        return DANGER
    if pct >= 60:
        return WARN
    return ACCENT


def _txt(dd, xy, text, font, fill, shadow=(0, 2), shadow_alpha=190, highlight=False):
    """带投影的立体文字：先画暗影，再画一层顶部高光，最后盖正文。"""
    x, y = xy
    dd.text((x + shadow[0], y + shadow[1]), text, font=font, fill=(3, 9, 16, shadow_alpha))
    if highlight:
        dd.text((x, y + 1), text, font=font, fill=(255, 255, 255, 30))
    dd.text((x, y), text, font=font, fill=fill)


def _cgroup_cpu_usec():
    with open("/sys/fs/cgroup/cpu.stat") as f:
        for line in f:
            if line.startswith("usage_usec"):
                return int(line.split()[1])
    raise RuntimeError("no usage_usec")


async def _cpu_pct(delay: float = 0.4) -> float:
    try:
        u1 = _cgroup_cpu_usec()
        t1 = time.time()
        await asyncio.sleep(delay)
        u2 = _cgroup_cpu_usec()
        t2 = time.time()
        cores = psutil.cpu_count() or 1
        return max(0.0, (u2 - u1) / 1e6 / ((t2 - t1) * cores) * 100)
    except Exception:  # noqa: BLE001
        try:
            return float(psutil.cpu_percent(interval=delay))
        except Exception:  # noqa: BLE001
            return 0.0


def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return " ".join(line.split(":", 1)[1].split())
    except Exception:  # noqa: BLE001
        pass
    try:
        import platform
        return platform.processor() or "未知"
    except Exception:  # noqa: BLE001
        return "未知"


@register(
    "astrbot_plugin_sl_status",
    "小喷菇",
    "发送 #状态（别名 sl / 运行状态 / st）获取运行状态卡：插画背景 + 磨砂面板，CPU / RAM / DISK 进度条，"
    "含 CPU 型号、今日 token 消耗、会话上下文规模、运行时长、插件数、协议端版本。"
    "纯本地 Pillow 渲染，失败回退纯文本；协议端自动探测 SnowLuma / NapCat 等 OneBot v11 实现。",
    "2.1.0",
)
class SlStatus(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.plugin_root = os.path.dirname(os.path.abspath(__file__))
        self._cached_base: str | None = None
        self._cached_token: str = ""

    # ---------------- 配置 ----------------
    def _cfg(self, key, default):
        try:
            if self.config is None:
                return default
            if hasattr(self.config, "get"):
                v = self.config.get(key, default)
            else:
                v = getattr(self.config, key, default)
            return default if v in (None, "") else v
        except Exception:  # noqa: BLE001
            return default

    def _flag(self, key, default=True) -> bool:
        v = self._cfg(key, default)
        if isinstance(v, str):
            return v.strip().lower() not in ("false", "0", "no", "off", "none", "")
        return bool(v)

    def _candidates(self):
        out = []
        for key in ENV_ENDPOINTS:
            v = os.environ.get(key, "").strip()
            if v:
                out.append(v.rstrip("/"))
        cfg_url = str(self._cfg("api_base", "") or "").strip()
        if cfg_url:
            out.append(cfg_url.rstrip("/"))
        out.extend(DEFAULT_ENDPOINTS)
        seen, uniq = set(), []
        for u in out:
            if u and u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq

    def _token(self):
        for key in ENV_TOKENS:
            v = os.environ.get(key, "").strip()
            if v:
                return v
        return str(self._cfg("access_token", "") or "").strip()

    # ---------------- 协议端 ----------------
    async def _call(self, action: str) -> dict:
        order = list(self._candidates())
        if self._cached_base and self._cached_base in order:
            order.remove(self._cached_base)
            order.insert(0, self._cached_base)
        for base in order:
            token = self._cached_token if base == self._cached_base else self._token()
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                timeout = aiohttp.ClientTimeout(total=4)
                async with aiohttp.ClientSession(timeout=timeout) as s:
                    async with s.post(f"{base}/{action}", json={}, headers=headers) as r:
                        if r.status == 401:
                            continue
                        data = await r.json(content_type=None)
                        if data.get("retcode") == 0:
                            self._cached_base, self._cached_token = base, token
                            return data.get("data") or {}
            except Exception:  # noqa: BLE001
                continue
        return {}

    # ---------------- 本地统计 ----------------
    def _db_stats(self, umo: str) -> dict:
        out: dict = {}
        db = "/AstrBot/data/data_v4.db"
        if not os.path.isfile(db):
            return out
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
            today0 = time.mktime(datetime.now(TZ).replace(hour=0, minute=0, second=0,
                                                          microsecond=0).timetuple())
            row = con.execute(
                "select coalesce(sum(token_input_other),0), coalesce(sum(token_output),0), count(*) "
                "from provider_stats where start_time >= ?", (today0,)).fetchone()
            out.update(tok_in=int(row[0]), tok_out=int(row[1]), calls=int(row[2]))
            if umo:
                r2 = con.execute(
                    "select content, token_usage from conversations where user_id=? "
                    "order by updated_at desc limit 1", (umo,)).fetchone()
                if r2:
                    out["msgs"] = (r2[0] or "").count('"role"')
                    out["ctx_tok"] = int(r2[1] or 0)
            con.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"sl_status: 读取库统计失败 {e}")
        return out

    # ---------------- 采集 ----------------
    async def _gather(self, umo: str = "") -> dict:
        bars = []
        info: list[tuple[str, str]] = []
        d: dict = {"bars": bars, "info": info}

        try:
            cpu = await _cpu_pct(0.4)
            bars.append(("CPU", f"{cpu:.1f}%", cpu))
        except Exception:  # noqa: BLE001
            pass

        try:
            cur = mx = None
            try:
                with open("/sys/fs/cgroup/memory.current") as f:
                    cur = int(f.read().strip())
                with open("/sys/fs/cgroup/memory.max") as f:
                    raw = f.read().strip()
                mx = int(raw) if raw.isdigit() else None
            except Exception:  # noqa: BLE001
                pass
            if cur and mx:
                pct = cur * 100 / mx
                bars.append(("RAM", f"{_gb(cur):.1f} / {_gb(mx):.1f} GB  ({pct:.0f}%)", pct))
            else:
                vm = psutil.virtual_memory()
                bars.append(("RAM", f"{_gb(vm.total - vm.available):.1f} / {_gb(vm.total):.1f} GB  "
                                    f"({vm.percent:.0f}%)", vm.percent))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"sl_status: 读取内存失败 {e}")

        try:
            du = psutil.disk_usage("/")
            bars.append(("DISK", f"{_gb(du.used):.1f} / {_gb(du.total):.1f} GB  ({du.percent:.0f}%)",
                         du.percent))
        except Exception:  # noqa: BLE001
            pass

        # 运行时长
        try:
            d["uptime"] = _hum(time.time() - psutil.Process().create_time())
        except Exception:  # noqa: BLE001
            d["uptime"] = "?"

        # 模型
        try:
            cfg = self.context.get_config()
            d["model"] = str(((cfg.get("provider") or [{}])[0]).get("model", "未知"))
        except Exception:  # noqa: BLE001
            d["model"] = ""

        # 插件数
        try:
            parent = os.path.dirname(self.plugin_root)
            d["plugins"] = len([x for x in os.listdir(parent) if os.path.isdir(os.path.join(parent, x))])
        except Exception:  # noqa: BLE001
            d["plugins"] = 0

        # CPU 型号
        try:
            model = _cpu_model()
            for junk in ("(R)", "(TM)", "CPU ", "Processor", "with Radeon Graphics"):
                model = model.replace(junk, "")
            d["cpu_model"] = " ".join(model.split())
            d["cores"] = psutil.cpu_count(logical=True) or 0
        except Exception:  # noqa: BLE001
            d["cpu_model"] = ""

        # token / 上下文
        stats = self._db_stats(umo)
        d["tok_in"] = stats.get("tok_in", 0)
        d["tok_out"] = stats.get("tok_out", 0)
        d["calls"] = stats.get("calls", 0)
        d["msgs"] = stats.get("msgs", 0)
        d["ctx_tok"] = stats.get("ctx_tok", 0)

        # 协议端
        vinfo = await self._call("get_version_info")
        status = await self._call("get_status")
        if vinfo or status:
            app = vinfo.get("app_name") or "OneBot"
            app_ver = (vinfo.get("app_version") or "?").split("-")[0]
            proto = vinfo.get("protocol_version") or "v11"
            live = "" if status.get("online", True) else "（掉线）"
            d["proto"] = f"{app} {app_ver}{live}"
            d["proto_full"] = f"{app} {app_ver}  ·  OneBot {proto}{live}"
        else:
            d["proto"] = "未连接"
            d["proto_full"] = "连接失败（协议端容器是否在跑？）"

        d["ver"] = getattr(astrbot, "__version__", "?")
        try:
            d["arch"] = os.uname().machine
        except Exception:  # noqa: BLE001
            d["arch"] = "?"
        d["stamp"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        return d

    # ---------------- 渲染 ----------------
    def _bg_dir(self):
        return os.path.join(self.plugin_root, "assets", "bg")

    def _list_bgs(self):
        d = self._bg_dir()
        if not os.path.isdir(d):
            return []
        exts = (".png", ".jpg", ".jpeg", ".webp")
        return sorted(x for x in os.listdir(d) if x.lower().endswith(exts))

    def _bg_choice(self):
        """背景选择：bg_mode=random 每次随机；fixed 用 bg_fixed；都没有则退回 assets/bg.png。"""
        names = self._list_bgs()
        if names:
            mode = str(self._cfg("bg_mode", "random")).strip().lower()
            if mode in ("fixed", "fix", "指定", "固定"):
                want = str(self._cfg("bg_fixed", "")).strip()
                name = want if want in names else names[0]
            else:
                name = random.choice(names)
            return os.path.join(self._bg_dir(), name)
        for name in ("bg.png", "bg.jpg"):
            p = os.path.join(self.plugin_root, "assets", name)
            if os.path.isfile(p):
                return p
        return None

    def _fit_bg(self, path):
        art = Image.open(path).convert("RGB")
        scale = max(W / art.width, H / art.height)
        art = art.resize((int(art.width * scale) + 1, int(art.height * scale) + 1), Image.LANCZOS)
        left = (art.width - W) // 2
        top = (art.height - H) // 2
        return art.crop((left, top, left + W, top + H))

    def _render(self, d: dict):
        if Image is None:
            return None
        f_reg = _pick([os.path.join(self.plugin_root, "assets", "fonts", n) for n in
                       ("MPLUSRounded1c-Regular.ttf", "MPLUSRounded1c-Medium.ttf")] + FONT_REGULAR)
        if not f_reg:
            return None
        f_bold = _pick([os.path.join(self.plugin_root, "assets", "fonts", n) for n in
                        ("MPLUSRounded1c-Bold.ttf", "MPLUSRounded1c-ExtraBold.ttf")] +
                       FONT_BOLD) or f_reg
        try:
            bg_path = self._bg_choice()
            img = self._fit_bg(bg_path).convert("RGBA") if bg_path else Image.new("RGBA", (W, H), BG + (255,))

            px0, py0, px1, py1 = PANEL
            region = img.crop(PANEL).filter(ImageFilter.GaussianBlur(14))
            region = Image.alpha_composite(region.convert("RGBA"),
                                           Image.new("RGBA", (px1 - px0, py1 - py0), (9, 20, 35, 216)))
            mask = Image.new("L", (px1 - px0, py1 - py0), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, px1 - px0 - 1, py1 - py0 - 1), radius=26, fill=255)
            img.paste(region, (px0, py0), mask)

            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dd = ImageDraw.Draw(layer)
            dd.rounded_rectangle(PANEL, radius=26, outline=(60, 102, 144, 150), width=1)
            dd.rounded_rectangle((px0, py0, px0 + 6, py1), radius=3, fill=ACCENT + (255,))

            # M PLUS Rounded 1c 是日文字体，缺部分简体字形（菇/喷），标题改回 Noto CJK 保证不出豆腐块
            title_src = _pick(FONT_BOLD) or f_bold
            title_f = ImageFont.truetype(title_src, 42, index=0)
            sub_f = ImageFont.truetype(f_reg, 20, index=0)
            bar_f = ImageFont.truetype(f_reg, 24, index=0)
            bar_lbl = ImageFont.truetype(f_bold, 26, index=0)
            k_f = ImageFont.truetype(f_bold, 19, index=0)
            v_f = ImageFont.truetype(f_reg, 22, index=0)

            x0, x1 = px0 + 34, px1 - 34

            _txt(dd, (x0, py0 + 28), str(self._cfg("title", "运行状态卡")), title_f, ACCENT + (255,), (0, 3), 200, True)
            _txt(dd, (x0 + 2, py0 + 84),
                 f"AstrBot {d.get('ver')}  ·  {d.get('arch')}  ·  {d['stamp']}",
                 sub_f, (144, 168, 194, 255), (0, 1), 150)

            # 进度条
            y = py0 + 140
            show_bar = {"CPU": self._flag("show_cpu", True), "RAM": self._flag("show_ram", True),
                        "DISK": self._flag("show_disk", True)}
            for name, text, pct in d["bars"]:
                if not show_bar.get(name, True):
                    continue
                pct = max(0.0, min(100.0, float(pct)))
                _txt(dd, (x0, y), name, bar_lbl, LABEL + (255,), (0, 2), 170)
                vf = bar_f
                if dd.textlength(text, font=vf) > (x1 - x0) - 150:
                    vf = ImageFont.truetype(f_bold, 20, index=0)
                tw = dd.textlength(text, font=vf)
                _txt(dd, (x1 - tw, y - 2), text, vf, VALUE + (255,), (0, 2), 170)
                by = y + 36
                dd.rounded_rectangle([x0, by, x1, by + 12], radius=6, fill=TRACK + (255,))
                fw = max(10, int((x1 - x0) * pct / 100))
                dd.rounded_rectangle([x0, by, x0 + fw, by + 12], radius=6, fill=_color_for(pct) + (255,))
                y = by + 34

            # 信息区：显式分行，每行 1 或 2 组，行距固定，先保证不叠
            rows: list[list[tuple[str, str]]] = []
            if self._flag("show_tokens", True) and (d.get("tok_in") or d.get("calls")):
                rows.append([("TOK", f"in {_k(d.get('tok_in', 0))}  ·  out {_k(d.get('tok_out', 0))}"
                                     f"  ·  {d.get('calls', 0)} calls")])
            if self._flag("show_context", True) and d.get("msgs"):
                rows.append([("CTX", f"{d.get('msgs')} msgs  ·  {_k(d.get('ctx_tok', 0))} tok")])
            if self._flag("show_cpu_model", True) and d.get("cpu_model"):
                rows.append([("CPU", f"{d['cpu_model']}  ·  {d.get('cores')}T")])
            pair: list[tuple[str, str]] = []
            if self._flag("show_model", True) and d.get("model"):
                pair.append(("MODEL", d["model"].replace("deepseek-", "")))
            if self._flag("show_plugins", True) and d.get("plugins"):
                pair.append(("PLG", f"{d.get('plugins')}"))
            if pair:
                rows.append(pair)
            tail: list[tuple[str, str]] = []
            if self._flag("show_protocol", True):
                tail.append(("PROTO", d.get("proto", "?")))
            if self._flag("show_uptime", True) and d.get("uptime"):
                tail.append(("UPTIME", d["uptime"]))
            if tail:
                rows.append(tail)

            dd.line([x0, y + 4, x1, y + 4], fill=(48, 78, 112, 200), width=1)
            y += 26

            avail = (py1 - 24) - y
            row_h = 44
            if rows and row_h * len(rows) > avail:
                row_h = max(34, int(avail / len(rows)))
            col_w = (x1 - x0) // 2
            for row in rows:
                for i, (k, v) in enumerate(row):
                    cx = x0 + i * col_w
                    _txt(dd, (cx, y), k, k_f, (150, 190, 216, 255), (0, 1), 150)
                    _txt(dd, (cx + 88, y), v, v_f, VALUE + (255,), (0, 1), 150)
                y += row_h

            out_img = Image.alpha_composite(img, layer).convert("RGB")
            out = os.path.join("/tmp", f"sl_status_{uuid.uuid4().hex[:12]}.png")
            out_img.save(out, "PNG", optimize=True)
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning(f"sl_status: 渲染状态卡失败，回退文本 {e}")
            return None

    # ---------------- 命令 ----------------
    @filter.command("状态图", alias={"slbg"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_bg(self, event: AstrMessageEvent):
        """列出可用背景图与当前背景模式（管理员）"""
        names = self._list_bgs()
        mode = str(self._cfg("bg_mode", "random")).strip().lower()
        fixed = str(self._cfg("bg_fixed", "")).strip()
        lines = [f"背景模式：{mode}（fixed 时用 {fixed or '目录第一张'}）"]
        if names:
            lines.append(f"assets/bg/ 共 {len(names)} 张：")
            lines += [f"· {n}" for n in names]
            lines.append("换图：把图片丢进 assets/bg/；随机模式每次 #状态 自动换一张。")
        else:
            lines.append("assets/bg/ 里还没有图片，当前用 assets/bg.png 或纯色底。")
        yield event.plain_result("\n".join(lines))

    @filter.command("状态", alias={"sl", "运行状态", "st"})
    async def cmd_status(self, event: AstrMessageEvent):
        """查看小喷菇运行状态（可视化状态卡，失败回退文字）"""
        umo = getattr(event, "unified_msg_origin", "") or ""
        d = await self._gather(umo)
        if self._cfg("send_image", True):
            path = self._render(d)
            if path:
                try:
                    yield event.image_result(path)
                    return
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"sl_status: 发送图片失败，回退文本 {e}")
        lines = [str(self._cfg("title", "运行状态卡")), f"AstrBot {d.get('ver')} · {d.get('arch')} · {d['stamp']}"]
        for name, text, pct in d["bars"]:
            lines.append(f"· {name}: {text}")
        lines.append(f"TOK   in {_k(d.get('tok_in', 0))} · out {_k(d.get('tok_out', 0))} · "
                     f"{d.get('calls', 0)} calls")
        if d.get("msgs"):
            lines.append(f"CTX   {d.get('msgs')} msgs · {_k(d.get('ctx_tok', 0))} tok")
        if d.get("cpu_model"):
            lines.append(f"CPU   {d['cpu_model']} · {d.get('cores')}T")
        if d.get("model"):
            lines.append(f"MODEL {d['model']}")
        lines.append(f"PLG   {d.get('plugins', 0)}")
        lines.append(f"PROTO {d.get('proto_full', '-')}")

        yield event.plain_result("\n".join(lines))
