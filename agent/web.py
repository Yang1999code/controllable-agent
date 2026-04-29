"""agent/web.py — IWebAutomation 实现。

URL 获取 + Web 搜索 + 浏览器自动化。

V1：httpx（fetch）+ 可配置搜索后端 + Playwright（browser）。

参考：CCB WebFetchTool/WebBrowserTool / Hermes web_tools/browser_tool
"""

import logging
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger(__name__)


# ── ABC ───────────────────────────────────────────────

class IWebAutomation(ABC):
    """Web 自动化接口。

    V1：httpx（fetch）+ 可配置搜索后端 + Playwright（browser）。
    """

    @abstractmethod
    async def fetch(self, url: str, prompt: str = "") -> str:
        """获取 URL 内容。如果有 prompt，用 LLM 对内容做定向摘要。"""
        ...

    @abstractmethod
    async def search(self, query: str, num_results: int = 10) -> list[dict]:
        """Web 搜索。V1 默认用 DuckDuckGo（免费，无需 API key）。"""
        ...

    @abstractmethod
    async def browser_navigate(self, url: str, session_id: str) -> dict: ...
    @abstractmethod
    async def browser_snapshot(self, session_id: str) -> str: ...
    @abstractmethod
    async def browser_click(self, element_id: str, session_id: str) -> dict: ...
    @abstractmethod
    async def browser_type(self, element_id: str, text: str, session_id: str) -> dict: ...
    @abstractmethod
    async def browser_close(self, session_id: str) -> None: ...

    @abstractmethod
    def get_config(self) -> dict:
        """返回 Web 自动化配置。"""
        ...


# ── 实现 ──────────────────────────────────────────────

class WebAutomation(IWebAutomation):
    """IWebAutomation 实现。

    V1：httpx（fetch）+ DuckDuckGo（search）+ Playwright（browser）。
    """

    def __init__(
        self,
        search_backend: str = "duckduckgo",
        max_fetch_chars: int = 100_000,
        url_blacklist: list[str] | None = None,
    ):
        self._search_backend = search_backend
        self._max_fetch_chars = max_fetch_chars
        self._url_blacklist = url_blacklist or []
        self._http_client: httpx.AsyncClient | None = None
        self._browser_sessions: dict[str, object] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
                headers={"User-Agent": "my-agent/0.1"},
            )
        return self._http_client

    async def fetch(self, url: str, prompt: str = "") -> str:
        """获取 URL 内容。"""
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        if self._is_blacklisted(url):
            return f"URL 在黑名单中: {url}"

        client = await self._get_client()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type:
                # 简单提取文本（不做完整 HTML→Markdown 转换）
                text = resp.text
                # 去掉 HTML 标签（简单实现）
                import re
                text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:self._max_fetch_chars]
            return resp.text[:self._max_fetch_chars]
        except Exception as e:
            return f"获取失败: {e}"

    async def search(self, query: str, num_results: int = 10) -> list[dict]:
        """Web 搜索。V1 默认 DuckDuckGo。"""
        if self._search_backend == "duckduckgo":
            return await self._search_ddg(query, num_results)
        return [{"title": "search not configured", "url": "", "snippet": ""}]

    async def _search_ddg(self, query: str, num_results: int = 10) -> list[dict]:
        """DuckDuckGo 搜索（HTML 回退，无需 API key）。"""
        client = await self._get_client()
        try:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )
            if resp.status_code != 200:
                return []

            # 简单解析 HTML 搜索结果
            import re
            results = []
            # 提取链接和摘要
            links = re.findall(r'class="result__a"[^>]*>(.*?)</a>', resp.text)
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', resp.text)
            urls = re.findall(r'class="result__url"[^>]*>(.*?)</a>', resp.text)

            for i in range(min(len(links), num_results)):
                results.append({
                    "title": re.sub(r'<[^>]+>', '', links[i]).strip() if i < len(links) else "",
                    "url": urls[i].strip() if i < len(urls) else "",
                    "snippet": re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else "",
                })
            return results
        except Exception as e:
            logger.warning(f"DuckDuckGo search failed: {e}")
            return []

    # ── 浏览器控制 ──

    async def browser_navigate(self, url: str, session_id: str) -> dict:
        """导航到 URL。V1 使用 Playwright。"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {"error": "playwright not installed. Run: pip install playwright && playwright install"}

        if session_id not in self._browser_sessions:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch()
            page = await browser.new_page()
            self._browser_sessions[session_id] = {
                "playwright": pw,
                "browser": browser,
                "page": page,
            }

        session = self._browser_sessions[session_id]
        await session["page"].goto(url)
        return {"url": url, "status": "navigated"}

    async def browser_snapshot(self, session_id: str) -> str:
        """获取页面无障碍树快照。"""
        if session_id not in self._browser_sessions:
            return "未找到浏览器会话"
        page = self._browser_sessions[session_id]["page"]
        try:
            # 使用 accessibility snapshot（比截图省 token）
            snapshot = await page.accessibility.snapshot()
            return str(snapshot)[:50000] if snapshot else "无快照"
        except Exception:
            return await page.content()

    async def browser_click(self, element_id: str, session_id: str) -> dict:
        """点击元素。"""
        if session_id not in self._browser_sessions:
            return {"error": "未找到浏览器会话"}
        page = self._browser_sessions[session_id]["page"]
        try:
            await page.click(f"#{element_id}")
            return {"status": "clicked", "element": element_id}
        except Exception as e:
            return {"error": str(e)}

    async def browser_type(self, element_id: str, text: str, session_id: str) -> dict:
        """在元素中输入文本。"""
        if session_id not in self._browser_sessions:
            return {"error": "未找到浏览器会话"}
        page = self._browser_sessions[session_id]["page"]
        try:
            await page.fill(f"#{element_id}", text)
            return {"status": "typed", "element": element_id}
        except Exception as e:
            return {"error": str(e)}

    async def browser_close(self, session_id: str) -> None:
        """关闭浏览器会话。"""
        session = self._browser_sessions.pop(session_id, None)
        if session:
            try:
                await session["browser"].close()
                await session["playwright"].stop()
            except Exception:
                pass

    # ── 配置 ──

    def get_config(self) -> dict:
        return {
            "search_backend": self._search_backend,
            "browser_backend": "playwright",
            "max_fetch_chars": self._max_fetch_chars,
            "url_blacklist": self._url_blacklist,
        }

    def _is_blacklisted(self, url: str) -> bool:
        """检查 URL 是否在黑名单中。"""
        for blocked in self._url_blacklist:
            if blocked in url:
                return True
        return False
