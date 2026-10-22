# -*- coding: utf-8 -*-
"""
EasyBrowser 本地 API 的可复用 SDK(公共部分抽出来放在外面)。

从示例脚本抽出的「与业务无关」的公共能力,供各例子直接 import:
  - EasyBrowserAPI : 环境 create/update、浏览器 start/stop、标签页 new_tab/close_tab
  - probe_proxy    : 本地探测代理出口 IP/时区,产出 create/update 用的 ip_info
  - connect_browser_with_retry / find_page_by_target / wait_for_page_ready
                   : patchright 通过 CDP 接入,并按 target_id 精确定位页面
  - HumanBehavior  : 贝塞尔鼠标轨迹 + 逐字输入 + 随机停顿的拟人操作
  - click_first / fill_first / visible_selector / get_visible_text : 通用选择器辅助

依赖:
    pip install requests loguru patchright
    EasyLauncher:
        https://easybrowser.pages.dev/
本模块只用 loguru 打日志、不配置 handler;handler 由使用方(示例脚本)配置。
"""

import asyncio
import random
import time
from urllib.parse import urlsplit

import requests
from loguru import logger
from patchright.async_api import async_playwright

# EasyBrowser 客户端默认监听的本地 API 地址
DEFAULT_BASE = "http://127.0.0.1:50325"
EASYLAUNCHER_URL = "https://easybrowser.pages.dev/"

# ASCII banner shown when the local API is missing.
EASYBROWSER_BANNER = r"""
EEEEEE   AAAAA   SSSSS   Y   Y  BBBBB   RRRRR   OOOOO   W   W  SSSSS   EEEEE  RRRRR
E       A   A   S        Y Y   B   B   R   R  O   O   W   W  S       E      R   R
EEE     AAAAA   SSS      Y     BBBBB   RRRRR  O   O   W W W  SSS     EEE    RRRRR
E       A   A      S     Y     B   B   R  R   O   O   W W W     S    E      R  R
EEEEEE  A   A  SSSSS     Y     BBBBB   R   R   OOOOO   W   W  SSSSS  EEEEE   R   R
"""

# EasyBrowser 客户端探测代理出口 IP/时区用的接口(见 localapi/server.js)
VISITOR_IP_URL = "https://ip-scan.browserscan.net/sys/config/ip/get-visitor-ip"


def _print_startup_banner():
    for line in (
        "========================================",
        "EasyBrowser",
        f"Website: {EASYLAUNCHER_URL}",
        "Download EasyLauncher to use the local API.",
        "Free VIP activation is available inside EasyLauncher.",
        "========================================",
    ):
        print(line)


_print_startup_banner()


def mask_secret(value, keep=2):
    """把密码等敏感串打码,只留首尾各 keep 位,用于日志。"""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"


def is_local_base_url(base_url):
    host = (urlsplit(base_url or "").hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def format_api_unavailable_error(base_url, exc=None):
    if is_local_base_url(base_url):
        message = (
            f"EasyBrowser local API is not available at {base_url}. "
            f"Please install and start EasyLauncher: {EASYLAUNCHER_URL}. "
            "Free VIP activation is available inside EasyLauncher."
        )
    else:
        message = f"Cannot connect to EasyBrowser API at {base_url}"
    if exc is not None:
        message = f"{message} ({exc})"
    return message


def probe_local_api(base_url=DEFAULT_BASE, headers=None, timeout=3):
    headers = headers or {}
    try:
        r = requests.get(
            f"{base_url.rstrip('/')}/api/v1/container/list",
            params={"page": 1, "page_size": 1},
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return {
            "ok": False,
            "reachable": False,
            "auth_required": False,
            "error": exc,
        }

    if r.status_code in (401, 403):
        return {
            "ok": False,
            "reachable": True,
            "auth_required": True,
            "status_code": r.status_code,
            "error": None,
        }

    try:
        payload = r.json()
    except ValueError as exc:
        return {
            "ok": False,
            "reachable": True,
            "auth_required": False,
            "status_code": r.status_code,
            "error": exc,
        }

    if r.status_code != 200 or payload.get("code") != 0:
        return {
            "ok": False,
            "reachable": True,
            "auth_required": False,
            "status_code": r.status_code,
            "payload": payload,
        }

    return {
        "ok": True,
        "reachable": True,
        "auth_required": False,
        "status_code": r.status_code,
        "payload": payload,
    }


def ensure_local_api_available(base_url=DEFAULT_BASE, headers=None, timeout=3):
    return EasyBrowserAPI(
        base_url=base_url,
        headers=headers,
        timeout=timeout,
    ).ensure_local_api_available(timeout=timeout)


# ------------------------- 本地代理测试 -------------------------
def probe_proxy(proxy_url, timeout=15):
    """通过代理(留空则直连)访问 browserscan,返回归一化后的 ip_info dict。

    返回的字段名与服务端 enrichIpInfo 读取的保持一致:
    ip / query / countryCode / country / state / regionName / city / timezone。
    代理不通会抛异常,交由调用方决定是否中止。
    """
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    try:
        r = requests.get(
            VISITOR_IP_URL,
            proxies=proxies,
            timeout=timeout,
            headers={"User-Agent": "EasyBrowser", "Accept-Encoding": "identity"},
        )
    except requests.exceptions.InvalidSchema as e:
        # socks 代理需要额外依赖:pip install "requests[socks]"
        raise RuntimeError(f"缺少 SOCKS 支持,请先 pip install \"requests[socks]\": {e}") from e
    r.raise_for_status()
    payload = r.json()
    if payload.get("code") != 0 or not payload.get("data", {}).get("ip_data"):
        raise RuntimeError(f"代理返回数据异常: {payload.get('msg') or payload}")

    data = payload["data"]
    ip_data = data.get("ip_data") or {}
    country_code = str(ip_data.get("country") or "").upper()
    return {
        "ip": data.get("ip") or "",
        "query": data.get("ip") or "",
        "countryCode": country_code,
        "country": country_code,
        "state": ip_data.get("region") or "",
        "regionName": ip_data.get("region") or "",
        "city": ip_data.get("city") or "",
        "timezone": ip_data.get("timezone") or "",
        "isp": ip_data.get("isp") or "",
    }
# ---------------------------------------------------------------


class EasyBrowserAPI:
    """EasyBrowser 本地 API 的最小封装。"""

    def __init__(self, base_url=DEFAULT_BASE, headers=None, timeout=30):
        self.base = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout

    def probe_local_api(self, timeout=None):
        return probe_local_api(
            base_url=self.base,
            headers=self.headers,
            timeout=self.timeout if timeout is None else timeout,
        )

    @staticmethod
    def _launcher_ad_message(detail=None):
        lines = [
            "========================================",
            "EasyBrowser local API is not available.",
            f"Download EasyLauncher: {EASYLAUNCHER_URL}",
            "Free VIP activation is available inside EasyLauncher.",
            "========================================",
        ]
        if detail:
            lines.extend(["", str(detail)])
        return "\n".join(lines)

    def ensure_local_api_available(self, timeout=None):
        status = self.probe_local_api(timeout=timeout)
        if status["ok"]:
            return status
        if status.get("auth_required"):
            raise RuntimeError("EasyBrowser local API is reachable but requires an API key.")
        raise RuntimeError(self._launcher_ad_message(status.get("error")))

    def _check(self, res):
        if res.get("code") != 0:
            raise RuntimeError(f"API error: {res.get('msg')}")
        return res.get("data")

    def _get(self, path, params=None):
        try:
            r = requests.get(
                f"{self.base}{path}",
                params=params,
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(format_api_unavailable_error(self.base, exc)) from exc
        try:
            return self._check(r.json())
        except ValueError as exc:
            raise RuntimeError(f"Invalid JSON response from {self.base}{path}: {exc}") from exc

    def _post(self, path, data=None):
        try:
            r = requests.post(
                f"{self.base}{path}",
                json=data,
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(format_api_unavailable_error(self.base, exc)) from exc
        try:
            return self._check(r.json())
        except ValueError as exc:
            raise RuntimeError(f"Invalid JSON response from {self.base}{path}: {exc}") from exc

    def create_container(self, name, **kw):
        return self._post("/api/v1/container/create", {"name": name, **kw})

    def ensure_container(self, name, **kw):
        def find_existing():
            containers = self.list_containers(page=1, page_size=1000)
            items = containers if isinstance(containers, list) else []
            if isinstance(containers, dict):
                for key in ("list", "items", "records", "rows", "data"):
                    value = containers.get(key)
                    if isinstance(value, list):
                        items = value
                        break
            for item in items:
                if isinstance(item, dict) and item.get("name") == name:
                    return item
            return None

        existing = find_existing()
        if existing:
            logger.info("Reusing container: {} ({})", name, existing.get("id"))
            return existing
        try:
            return self.create_container(name, **kw)
        except RuntimeError as exc:
            if "already exists" not in str(exc).lower():
                raise
            existing = find_existing()
            if existing:
                logger.info("Reusing container: {} ({})", name, existing.get("id"))
                return existing
            raise

    def update_container(self, env_id, **kw):
        return self._post("/api/v1/container/update", {"id": env_id, **kw})

    def delete_container(self, env_ids):
        """Delete one or more containers. Accepts a single id or a list of ids."""
        if isinstance(env_ids, str):
            ids = [env_ids]
        else:
            ids = list(env_ids)
        return self._post("/api/v1/container/delete", {"ids": ids})

    def restore_container(self, env_ids):
        """Restore one or more containers from the trash. Accepts a single id or a list of ids."""
        if isinstance(env_ids, str):
            ids = [env_ids]
        else:
            ids = list(env_ids)
        return self._post("/api/v1/container/restore", {"ids": ids})

    def delete_permanently(self, env_ids):
        """Permanently delete containers and their browser data (incl. cookies). Irreversible."""
        if isinstance(env_ids, str):
            ids = [env_ids]
        else:
            ids = list(env_ids)
        return self._post("/api/v1/container/delete_permanently", {"ids": ids})

    def trash_list(self):
        return self._get("/api/v1/container/trash")

    def list_containers(self, page=1, page_size=100):
        return self._get(
            "/api/v1/container/list",
            {"page": page, "page_size": page_size},
        )

    def browser_start(self):
        return self._get("/api/v1/browser/start")

    def browser_stop(self):
        return self._get("/api/v1/browser/stop")

    def new_tab(self, env_id, url=None):
        params = {"id": env_id}
        if url:
            params["url"] = url
        return self._get("/api/v1/browser/new_tab", params)

    def close_tab(self, env_id, target_id=None):
        params = {"id": env_id}
        if target_id:
            params["target_id"] = target_id
        return self._get("/api/v1/browser/close_tab", params)


# ------------------------- CDP 接入 / 页面定位 -------------------------
async def connect_browser_with_retry(debug_port, retries=30, delay=2.0,
                                     timeout_seconds=120):
    """启动 playwright 并用 connect_over_cdp 接入调试端口,带重试。

    返回 (playwright, browser);调用方负责最后 browser.close() + playwright.stop()。
    """
    last_error = None
    playwright = await async_playwright().start()
    try:
        deadline = time.monotonic() + timeout_seconds
        for attempt in range(1, retries + 1):
            if time.monotonic() >= deadline:
                break
            try:
                browser = await playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{debug_port}")
                return playwright, browser
            except Exception as e:
                last_error = e
                logger.warning("CDP 连接失败 {}/{}: {}", attempt, retries, e)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(delay * attempt, 5.0, remaining))
    except Exception:
        try:
            await playwright.stop()
        except Exception:
            pass
        raise
    try:
        await playwright.stop()
    except Exception:
        pass
    raise RuntimeError(f"CDP 连接失败,重试耗尽: {last_error}")


async def find_page_by_target(browser, target_id, timeout=30, poll_interval=0.5):
    """在已连接的浏览器里,按 target_id 匹配出对应的 page。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for ctx in browser.contexts:
            for pg in ctx.pages:
                cdp = None
                try:
                    cdp = await ctx.new_cdp_session(pg)
                    info = await cdp.send("Target.getTargetInfo")
                except Exception:
                    continue
                finally:
                    if cdp is not None:
                        try:
                            await cdp.detach()
                        except Exception:
                            pass
                if info.get("targetInfo", {}).get("targetId") == target_id:
                    return pg
        await asyncio.sleep(poll_interval)
    return None


async def wait_for_page_ready(page, timeout=30000):
    await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout // 2)
    except Exception:
        pass


async def get_visible_text(page, selector="body"):
    try:
        return (await page.inner_text(selector)).strip()
    except Exception:
        return ""


async def visible_selector(page, selectors):
    """依次尝试选择器,返回第一个可见的;都不可见返回 None。"""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return sel
        except Exception:
            continue
    return None
# ---------------------------------------------------------------------


# ------------------------- 拟人行为(鼠标轨迹/输入/停顿) -------------------------
class HumanBehavior:
    """把「像人一样操作」集中到一处:贝塞尔鼠标轨迹、逐字输入、随机停顿。

    - 维护一个虚拟鼠标坐标 (x, y),每次移动都从当前点用三次贝塞尔曲线
      平滑走到目标点(带缓入缓出 + 轻微抖动),而不是瞬移。
    - 点击前先「移动过去」,点击/按下都带毫秒级微停顿。
    - 输入逐字符敲,字符间、字段间都有随机停顿。
    参数都留了区间,方便统一调节整体节奏。
    """

    def __init__(self, page, key_delay=(90, 220), field_pause=(0.4, 0.9)):
        self.page = page
        self.key_delay = key_delay        # 字符之间停顿(毫秒)
        self.field_pause = field_pause    # 一个字段填完后的停顿(秒)
        # 初始鼠标位置未知,给个视口内的随机起点
        self.x = random.uniform(200, 700)
        self.y = random.uniform(200, 500)

    @staticmethod
    def _cubic(a, b, c, d, t):
        """三次贝塞尔在参数 t 处的取值(单维度)。"""
        mt = 1 - t
        return (mt ** 3) * a + 3 * (mt ** 2) * t * b + 3 * mt * (t ** 2) * c + (t ** 3) * d

    async def move_to(self, tx, ty):
        """从当前点沿贝塞尔曲线移动到 (tx, ty)。步数/弧度随距离变化。"""
        sx, sy = self.x, self.y
        dist = ((tx - sx) ** 2 + (ty - sy) ** 2) ** 0.5
        steps = max(12, min(60, int(dist / 6)))
        # 两个控制点:主方向上分别落在 ~30% / ~70% 处,再加垂直方向的随机偏移形成弧线
        off = min(dist * 0.25, 120)
        cx1 = sx + (tx - sx) * random.uniform(0.2, 0.4) + random.uniform(-off, off)
        cy1 = sy + (ty - sy) * random.uniform(0.2, 0.4) + random.uniform(-off, off)
        cx2 = sx + (tx - sx) * random.uniform(0.6, 0.8) + random.uniform(-off, off)
        cy2 = sy + (ty - sy) * random.uniform(0.6, 0.8) + random.uniform(-off, off)
        for i in range(1, steps + 1):
            t = i / steps
            te = t * t * (3 - 2 * t)  # smoothstep 缓入缓出
            x = self._cubic(sx, cx1, cx2, tx, te) + random.uniform(-0.7, 0.7)
            y = self._cubic(sy, cy1, cy2, ty, te) + random.uniform(-0.7, 0.7)
            await self.page.mouse.move(x, y)
            await self.page.wait_for_timeout(random.randint(6, 16))
        self.x, self.y = tx, ty
        return tx, ty

    async def move_to_element(self, el):
        """移动到元素内部一个随机点(避开正中心和边缘,更自然)。"""
        box = await el.bounding_box()
        if not box:
            return None
        tx = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        ty = box["y"] + box["height"] * random.uniform(0.35, 0.65)
        await self.move_to(tx, ty)
        return self.x, self.y

    async def click(self, el):
        """移动到元素上,停顿一下再按下-抬起(带微小按住时长)。"""
        moved = await self.move_to_element(el)
        await self.page.wait_for_timeout(random.randint(60, 180))
        if moved:
            await self.page.mouse.down()
            await self.page.wait_for_timeout(random.randint(40, 110))
            await self.page.mouse.up()
        else:
            # 拿不到坐标(隐藏/跨域等),退回元素自身点击
            await el.click()
        await self.page.wait_for_timeout(random.randint(120, 300))

    async def type_text(self, el, value):
        """聚焦后逐字符输入:字符间随机停顿,填完再停顿一个字段间隔。"""
        await self.click(el)  # 先拟人点击聚焦
        try:
            await el.fill("")  # 清掉预填,避免逐字追加到旧值后
        except Exception:
            pass
        for ch in value:
            await el.type(ch, delay=random.uniform(30, 80))
            await self.page.wait_for_timeout(int(random.uniform(*self.key_delay)))
        await self.page.wait_for_timeout(int(random.uniform(*self.field_pause) * 1000))

    async def pause(self, lo=0.3, hi=0.8):
        """一段随机停顿(秒),用于步骤之间。"""
        await self.page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))
# ---------------------------------------------------------------------------------


async def click_first(human, selectors, timeout=5000, require_enabled=False):
    """依次尝试选择器,拟人移动并点中第一个可见的就返回。"""
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for sel in selectors:
            try:
                el = await human.page.query_selector(sel)
                if not el:
                    continue
                if not await el.is_visible():
                    continue
                if require_enabled:
                    try:
                        if not await el.is_enabled():
                            continue
                    except Exception:
                        disabled = await el.get_attribute("disabled")
                        aria_disabled = await el.get_attribute("aria-disabled")
                        if disabled is not None or (aria_disabled or "").lower() == "true":
                            continue
                await human.click(el)
                logger.trace("click 命中选择器: {}", sel)
                return True
            except Exception:
                continue
        await human.page.wait_for_timeout(250)
    logger.trace("click 未命中任何选择器: {}", selectors)
    return False


async def fill_first(human, selectors, value):
    """依次尝试选择器,往第一个可见的输入框里拟人逐字输入。"""
    for sel in selectors:
        try:
            el = await human.page.wait_for_selector(sel, timeout=5000, state="visible")
            if not el:
                continue
            await human.type_text(el, value)
            logger.trace("type 命中选择器: {}", sel)
            return True
        except Exception:
            continue
    logger.trace("type 未命中任何选择器: {}", selectors)
    return False


def contains_any(value, needles):
    """value(允许 None)转小写后,是否包含 needles 里任意一个(大小写不敏感)。"""
    value = (value or "").lower()
    return any(n.lower() in value for n in needles)


# ------------------------- 下拉 / 选择 / 精确填写 -------------------------
async def pick_random_option(human, dropdown_sel):
    """Fluent 按钮下拉:拟人点开 -> 读真实 [role=option] -> 随机拟人点一个。

    不硬编码选项文本(月是「N月」、日是「N日」这类格式会变),
    直接从 DOM 现有选项里随机挑,返回被选中的文本。
    """
    page = human.page
    dropdown = await page.wait_for_selector(dropdown_sel, timeout=8000, state="visible")
    await human.click(dropdown)
    # 等选项浮层渲染出来
    await page.wait_for_selector("[role=option]", timeout=8000, state="visible")
    options = await page.query_selector_all("[role=option]")
    options = [o for o in options if await o.is_visible()]
    if not options:
        raise RuntimeError(f"{dropdown_sel} 展开后没有可见选项")
    choice = random.choice(options)
    text = (await choice.inner_text()).strip()
    await human.click(choice)
    logger.trace("下拉 {} 随机选中: {}", dropdown_sel, text)
    return text


async def pick_random_option_first(human, selectors):
    """多候选下拉:原生 select 直接随机 select_option;否则退回 pick_random_option。"""
    for sel in selectors:
        try:
            el = await human.page.wait_for_selector(sel, timeout=3000, state="visible")
            if not el:
                continue
            tag_name = (await el.evaluate("node => node.tagName")).lower()
            if tag_name == "select":
                options = await el.query_selector_all("option")
                candidates = []
                for option in options:
                    value = await option.get_attribute("value")
                    text = (await option.inner_text()).strip()
                    if value and text:
                        candidates.append((value, text))
                if not candidates:
                    continue
                value, text = random.choice(candidates)
                await el.select_option(value=value)
                logger.trace("select {} 随机选中: {}", sel, text)
                return text
            return await pick_random_option(human, sel)
        except Exception:
            continue
    raise RuntimeError(f"没有找到可用下拉: {selectors}")


async def set_select_value(page, selectors, value_text):
    """把下拉/按钮/输入框设置成指定文本;已是该值则直接返回 True。"""
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=3000, state="visible")
            if not el:
                continue
            tag_name = (await el.evaluate("node => node.tagName")).lower()
            if tag_name == "select":
                try:
                    selected = await el.query_selector("option:checked")
                    if selected:
                        current_text = " ".join((await selected.inner_text()).split())
                        if current_text == value_text or value_text in current_text:
                            return True
                except Exception:
                    pass
                options = await el.query_selector_all("option")
                for option in options:
                    text = (await option.inner_text()).strip()
                    if text == value_text or value_text in text:
                        value = await option.get_attribute("value")
                        if value is not None:
                            await el.select_option(value=value)
                            return True
            if tag_name == "button":
                current_text = " ".join((await el.inner_text()).split())
                if current_text == value_text or value_text in current_text:
                    return True
                await el.click()
                await page.wait_for_timeout(500)
                candidates = await page.query_selector_all("[role=option]")
                for opt in candidates:
                    try:
                        text = (await opt.inner_text()).strip()
                        if text == value_text or value_text in text:
                            await opt.click()
                            return True
                    except Exception:
                        continue
            if tag_name == "input":
                current = (await el.input_value()).strip()
                if current == value_text or value_text in current:
                    return True
                await el.fill(value_text)
                await el.press("Tab")
                return True
        except Exception:
            continue
    return False


async def fill_input_exact(page, selectors, value_text):
    """往第一个可见 input/textarea 精确填入 value_text 并回读校验。"""
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=3000, state="visible")
            if not el:
                continue
            tag_name = (await el.evaluate("node => node.tagName")).lower()
            if tag_name != "input" and tag_name != "textarea":
                continue
            await el.fill(value_text)
            await el.press("Tab")
            current = ""
            try:
                current = (await el.input_value()).strip()
            except Exception:
                pass
            if current == value_text or value_text in current:
                return True
        except Exception:
            continue
    return False
# ---------------------------------------------------------------------


# ------------------------- 按住(Press & Hold)人机验证 -------------------------
# Arkose / Cloudflare 等常有「按住直到通过」验证,按钮通常嵌在跨域 iframe 里。
# 不写死单一标记:候选选择器 + 遍历所有 frame 兜底(沿用「读真实 DOM」的原则)。
HOLD_BUTTON_SELECTORS = [
    "#game-core-bounding-box #hold-button",  # Arkose 经典结构
    "#hold-button",
    "button[aria-label*='hold' i]",
    "button[aria-label*='press' i]",
    "[id*='hold' i][role=button]",
    "button:has-text('按住')",
    "button:has-text('Press & Hold')",
    "button:has-text('Press and Hold')",
    "button:has-text('Hold')",
]
# 出现这些文案基本说明验证已通过 / 页面已跳过验证
HOLD_DONE_HINTS = ["成功", "success", "verified", "完成", "done"]


async def find_hold_button(browser_page, timeout=45000):
    """在所有 frame 里轮询查找「按住」按钮,拿到就返回元素句柄。

    按钮常在动态注入的跨域 iframe 内,主页 DOM 查不到,必须遍历 page.frames。
    用 query_selector 快速扫描(不阻塞),外层用总超时轮询,避免逐 selector 死等。
    """
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for frame in browser_page.frames:
            for sel in HOLD_BUTTON_SELECTORS:
                try:
                    el = await frame.query_selector(sel)
                except Exception:
                    continue  # frame 可能正在导航/被销毁
                if el and await el.is_visible():
                    logger.trace("找到按住按钮: frame={} selector={}",
                                 frame.url or "<main>", sel)
                    return el
        await browser_page.wait_for_timeout(800)
    return None


async def is_hold_solved(page, hold_el):
    """判断按住验证是否已通过:按钮消失/不可见,或页面出现成功文案。"""
    try:
        if hold_el and await hold_el.is_visible():
            return False
    except Exception:
        # 元素已从 DOM 脱离(frame 被回收),通常意味着验证已过
        return True
    try:
        body_text = (await page.inner_text("body")).lower()
        return any(h in body_text for h in HOLD_DONE_HINTS)
    except Exception:
        return True


async def press_and_hold(page, hold_seconds=(6.0, 9.0), max_attempts=3, human=None):
    """完成「按住」验证:移到按钮中心 -> 按下保持 -> 松开 -> 检测。

    - 传了 human 就沿贝塞尔轨迹移动过去(而不是瞬移到按钮),更像人手。
    - 按住时长在区间内随机,期间做几次微小抖动(±2px)模拟真人手抖,
      纯静止按压容易被判定为机器人。
    - 单次没过就重试,最多 max_attempts 次;全失败返回 False 交人工。
    """
    for attempt in range(1, max_attempts + 1):
        hold_el = await find_hold_button(page)
        if hold_el is None:
            logger.info("未发现「按住」验证按钮(可能本次没触发该验证)")
            return False

        try:
            await hold_el.scroll_into_view_if_needed()
        except Exception:
            pass
        box = await hold_el.bounding_box()
        if not box:
            logger.warning("按住按钮拿不到坐标,第 {} 次跳过", attempt)
            continue

        # bounding_box 已换算到主页面坐标系,可直接喂给 page.mouse
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        duration = random.uniform(*hold_seconds)
        logger.info("第 {}/{} 次尝试按住 {:.1f}s @ ({:.0f},{:.0f})",
                    attempt, max_attempts, duration, cx, cy)

        if human is not None:
            await human.move_to(cx, cy)  # 贝塞尔轨迹靠近
            await page.wait_for_timeout(random.randint(80, 200))
        else:
            await page.mouse.move(cx, cy)
        await page.mouse.down()
        try:
            # 分段保持,期间轻微抖动,更像人手
            elapsed = 0.0
            while elapsed < duration:
                step = min(random.uniform(0.4, 0.8), duration - elapsed)
                await page.wait_for_timeout(int(step * 1000))
                elapsed += step
                await page.mouse.move(
                    cx + random.uniform(-2, 2),
                    cy + random.uniform(-2, 2),
                )
        finally:
            await page.mouse.up()  # 无论如何都要松开,避免卡在按下态

        await page.wait_for_timeout(2500)  # 等验证结算/页面跳转
        if await is_hold_solved(page, hold_el):
            logger.success("按住验证已通过(第 {} 次)", attempt)
            return True
        logger.warning("第 {} 次按住后仍未通过,准备重试", attempt)

    logger.warning("按住验证多次未通过,请在浏览器里人工完成")
    return False
# -----------------------------------------------------------------------------
