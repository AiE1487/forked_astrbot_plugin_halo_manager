import html
import json
import re
import time
import uuid
from urllib.parse import quote
import aiohttp
from typing import Any, Dict, Optional

from pydantic import Field
from pydantic.dataclasses import dataclass

# 导入所有标准 API
from astrbot.api.all import *
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.message.components import Image

# Halo API 常量
API_CONTENT = "content.halo.run/v1alpha1"
API_CONSOLE = "api.console.halo.run/v1alpha1"
# Console API：草稿接口 https://api.halo.run/#/PostV1alpha1Console/DraftPost
CONSOLE_POSTS = "/apis/api.console.halo.run/v1alpha1/posts"
# Console API：当前用户详情，"-" 代表自己 https://api.halo.run/#/UserV1alpha1Console/GetCurrentUserDetail
CONSOLE_USER_SELF = "/apis/api.console.halo.run/v1alpha1/users/-"
# 旧版本 Halo 的当前用户端点，仅作兼容回退（users/me 在现行版本并不存在）
CONSOLE_USER_SELF_FALLBACKS = (
    "/apis/api.console.halo.run/v1alpha1/users/me",
    "/apis/api.uc.halo.run/v1alpha1/users/me",
)
# Console API：用户资源根路径，GET {CONSOLE_USERS}/{用户名} 可校验账号是否存在（不存在返回 404）
CONSOLE_USERS = "/apis/api.console.halo.run/v1alpha1/users"
# Content API：单资源创建（备用）
CONTENT_POSTS = f"/apis/{API_CONTENT}/posts"
# 瞬间 API 常量
API_MOMENT_UC = "uc.api.moment.halo.run/v1alpha1"
# UC API：瞬间接口（需博客安装并启用「瞬间」插件 plugin-moments）
# 见 https://github.com/halo-sigs/plugin-moments/blob/main/src/main/java/run/halo/moments/uc/UcMomentEndpoint.java
# 创建/列表/删除均针对 PAT 认证用户：服务端自动把 spec.owner 设为 Token 所属账号；
# 该账号有瞬间审核权限或为超管时自动 approved=true，否则进入待审核。
UC_MOMENTS = f"/apis/{API_MOMENT_UC}/moments"


CONFIG_HALO_URL = "halo_url"
CONFIG_HALO_TOKEN = "halo_token"
CONFIG_HALO_OWNER = "halo_owner"
CONFIG_ENABLE_MOMENTS = "enable_moments_tools"


def _build_console_draft_payload(
    title: str, content: str, slug: str, owner: str = ""
) -> Dict[str, Any]:
    """按官方 DraftPost 文档构建 content+post 包装体，用于 POST /apis/api.console.halo.run/v1alpha1/posts。"""
    raw = content or ""
    excerpt_raw = (raw[:500] + "...") if len(raw) > 500 else raw
    spec: Dict[str, Any] = {
        "title": title or "无标题",
        "slug": slug,
        "visible": "PUBLIC",
        "allowComment": True,
        "excerpt": {"autoGenerate": True, "raw": excerpt_raw},
        "publish": False,
        "deleted": False,
        "pinned": False,
        "priority": 0,
        "template": "",
    }
    if owner and owner.strip():
        spec["owner"] = owner.strip()
    return {
        "content": {
            "content": raw,
            "raw": raw,
            "rawType": "MARKDOWN",
            "version": 0,
        },
        "post": {
            "apiVersion": API_CONTENT,
            "kind": "Post",
            "metadata": {"name": slug, "labels": {}},
            "spec": spec,
        },
    }


def _build_create_post_payload(
    title: str, content: str, slug: str, owner: str = ""
) -> Dict[str, Any]:
    """Content API 单 Post 资源体（Console 草稿 404 时备用）。"""
    raw = content or ""
    excerpt_raw = (raw[:500] + "...") if len(raw) > 500 else raw
    spec: Dict[str, Any] = {
        "title": title or "无标题",
        "slug": slug,
        "visible": "PUBLIC",
        "allowComment": True,
        "excerpt": {"autoGenerate": True, "raw": excerpt_raw},
        "publish": True,
        "deleted": False,
        "pinned": False,
        "priority": 0,
        "template": "",
        "raw": raw,
        "originalContent": raw,
    }
    if owner and owner.strip():
        spec["owner"] = owner.strip()
    return {
        "apiVersion": API_CONTENT,
        "kind": "Post",
        "metadata": {"name": slug, "labels": {}},
        "spec": spec,
    }


def _head_snapshot_from_post_response(res: dict) -> str:
    """从创建文章接口的响应中解析 headSnapshot（内容快照名），用于后续调用发布接口。"""
    if not res or "error" in res:
        return ""
    status = res.get("status") or {}
    spec = res.get("spec") or {}
    for key in ("headSnapshot", "releaseSnapshot"):
        val = status.get(key) or spec.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return ""


def _moment_html_from_text(text: str) -> str:
    """把瞬间文本转为简单 HTML（转义 + 段落/换行/加粗/斜体/行内代码/链接）。

    瞬间内容较短，官方 UC 前端发送前也会在前端做 Markdown 转换，这里用
    极简实现替代，避免引入额外依赖。
    """
    escaped = html.escape((text or "").strip())
    if not escaped:
        return ""
    # 行内元素（顺序重要：先 code，避免其它替换破坏代码内容；加粗先于斜体）
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*\s][^*]*)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" target="_blank">\1</a>',
        escaped,
    )
    # 空行分段，段内换行转 <br>
    parts = []
    for para in re.split(r"\n\s*\n", escaped):
        para = para.strip()
        if para:
            parts.append("<p>%s</p>" % "<br>".join(para.split("\n")))
    return "".join(parts)


def _build_moment_payload(content: str, visible: str = "PUBLIC") -> Dict[str, Any]:
    """构建瞬间 (Moment) 资源体，用于 POST UC 瞬间接口。

    spec.owner / spec.approved 由服务端按 PAT 认证用户与权限填充，无需传参；
    metadata 用 generateName 由服务端生成实际资源名。
    """
    raw = (content or "").strip()
    return {
        "apiVersion": API_MOMENT_UC,
        "kind": "Moment",
        "metadata": {"generateName": "moment-"},
        "spec": {
            "content": {"raw": raw, "html": _moment_html_from_text(raw), "medium": []},
            "releaseTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "visible": visible if visible in ("PUBLIC", "PRIVATE") else "PUBLIC",
        },
    }


# ---------- LLM Tools（按文档 https://docs.astrbot.app/dev/star/guides/ai.html#定义-tool 使用 FunctionTool + add_llm_tools 注册） ----------


@dataclass
class PublishBlogPostTool(FunctionTool[AstrAgentContext]):
    """在 Halo 博客上发布一篇新文章。当用户要求发博客、写文章、发布到博客时调用。"""

    plugin: Any = Field(default=None, exclude=True)
    name: str = "publish_blog_post"
    description: str = "在 Halo 博客上发布一篇新文章。当用户要求发博客、写文章、发布到博客时调用。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "文章标题。"},
                "content": {"type": "string", "description": "文章正文，支持 Markdown 格式。"},
                "slug": {"type": "string", "description": "可选，URL 路径别名。不填则自动生成。"},
            },
            "required": ["title", "content"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if self.plugin is None:
            return "error: plugin not initialized"
        event = context.context.event
        return await self.plugin._llm_publish_post(
            event,
            title=kwargs.get("title", ""),
            content=kwargs.get("content", ""),
            slug=kwargs.get("slug", ""),
        )


@dataclass
class GetBlogCommentsTool(FunctionTool[AstrAgentContext]):
    """获取 Halo 博客最新的评论列表。当用户问「有什么新评论」「看看评论」时调用。"""

    plugin: Any = Field(default=None, exclude=True)
    name: str = "get_blog_comments"
    description: str = "获取 Halo 博客最新的评论列表。当用户问「有什么新评论」「看看评论」时调用。"
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if self.plugin is None:
            return "error: plugin not initialized"
        event = context.context.event
        return await self.plugin._llm_get_comments(event)


@dataclass
class ReplyBlogCommentTool(FunctionTool[AstrAgentContext]):
    """回复 Halo 博客上的一条评论。当用户要求「回复评论」「回复某条评论」时调用。"""

    plugin: Any = Field(default=None, exclude=True)
    name: str = "reply_blog_comment"
    description: str = "回复 Halo 博客上的一条评论。当用户要求「回复评论」「回复某条评论」时调用。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "comment_id": {
                    "type": "string",
                    "description": "要回复的评论的唯一 ID（从 get_blog_comments 可获取）。",
                },
                "content": {"type": "string", "description": "回复内容。"},
            },
            "required": ["comment_id", "content"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if self.plugin is None:
            return "error: plugin not initialized"
        event = context.context.event
        return await self.plugin._llm_reply_comment(
            event,
            comment_id=kwargs.get("comment_id", ""),
            content=kwargs.get("content", ""),
        )


@dataclass
class UploadBlogImageTool(FunctionTool[AstrAgentContext]):
    """将指定图片 URL 的图片上传到 Halo 博客。当用户要求「把这张图发到博客」「上传图片到博客」且提供了图片链接时调用。"""

    plugin: Any = Field(default=None, exclude=True)
    name: str = "upload_blog_image"
    description: str = "将指定图片 URL 的图片上传到 Halo 博客。当用户要求「把这张图发到博客」「上传图片到博客」且提供了图片链接时调用。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "image_url": {
                    "type": "string",
                    "description": "图片的完整 URL，需可公网访问。",
                },
            },
            "required": ["image_url"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if self.plugin is None:
            return "error: plugin not initialized"
        event = context.context.event
        return await self.plugin._llm_upload_image(
            event, image_url=kwargs.get("image_url", "")
        )


@dataclass
class PublishBlogMomentTool(FunctionTool[AstrAgentContext]):
    """在 Halo 博客发布一条瞬间（类似微博的短内容）。当用户要求「发瞬间」「发条动态」时调用。"""

    plugin: Any = Field(default=None, exclude=True)
    name: str = "publish_blog_moment"
    description: str = "在 Halo 博客发布一条瞬间（类似微博的短内容）。当用户要求「发瞬间」「发条动态」「发说说」时调用。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "瞬间内容，支持简单 Markdown（加粗、斜体、行内代码、链接）。不许为空。",
                },
                "visible": {
                    "type": "string",
                    "enum": ["PUBLIC", "PRIVATE"],
                    "description": "可选，可见性：PUBLIC 公开（默认）、PRIVATE 私密。",
                },
            },
            "required": ["content"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if self.plugin is None:
            return "error: plugin not initialized"
        event = context.context.event
        return await self.plugin._llm_publish_moment(
            event,
            content=kwargs.get("content", ""),
            visible=kwargs.get("visible") or "",
        )


@dataclass
class ListBlogMomentsTool(FunctionTool[AstrAgentContext]):
    """获取 Halo 博客上 Token 所属账号发布的瞬间列表。当用户问「看看瞬间」「最近发了什么动态」时调用。"""

    plugin: Any = Field(default=None, exclude=True)
    name: str = "list_blog_moments"
    description: str = "获取 Halo 博客上 Token 所属账号发布的瞬间列表。当用户问「看看瞬间」「最近发了什么动态」时调用。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "可选，返回条数，1-20，默认 5。",
                },
            },
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if self.plugin is None:
            return "error: plugin not initialized"
        event = context.context.event
        return await self.plugin._llm_list_moments(event, limit=kwargs.get("limit", 5))


@dataclass
class DeleteBlogMomentTool(FunctionTool[AstrAgentContext]):
    """删除 Halo 博客上的一条瞬间。当用户要求「删除某条瞬间」时调用，需先通过 list_blog_moments 拿到瞬间 ID。"""

    plugin: Any = Field(default=None, exclude=True)
    name: str = "delete_blog_moment"
    description: str = "删除 Halo 博客上的一条瞬间。当用户要求「删除某条瞬间」时调用；moment_name 需先通过 list_blog_moments 获取。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "moment_name": {
                    "type": "string",
                    "description": "要删除的瞬间的唯一 ID（从 list_blog_moments 可获取）。",
                },
            },
            "required": ["moment_name"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if self.plugin is None:
            return "error: plugin not initialized"
        event = context.context.event
        return await self.plugin._llm_delete_moment(
            event, moment_name=kwargs.get("moment_name", "")
        )


@register(
    "astrbot_plugin_halo_manager",
    "CAN",
    "Halo 2.x 博客管理插件",
    "1.3.0",
    "https://github.com/danielsunrise/astrbot_plugin_halo_manager"
)
class HaloManager(Star):
    def __init__(self, context: Context, config: Dict[str, Any]):
        super().__init__(context)
        
        self.config = config or {}
        raw_url = self.config.get(CONFIG_HALO_URL, "")
        self.base_url = raw_url.rstrip("/") if raw_url else ""
        self.token = self.config.get(CONFIG_HALO_TOKEN, "")
        # 瞬间工具开关（修改后需重载插件才会重新注册/取消注册）
        self.enable_moments = bool(self.config.get(CONFIG_ENABLE_MOMENTS, False))
        self._cached_owner: Optional[str] = None  # 通过 token 拉取到的当前用户名，避免重复请求
        self._validated_owner: Optional[str] = None  # 已通过存在性校验的文章发送账号，避免重复请求
        if not self.base_url or not self.token:
            logger.warning("配置缺失！请在 Web 面板或 _conf_schema.json 中填写 URL 和 Token。")
        # 按文档在 __init__ 中注册 LLM 工具，供 AI 对话时自动调用
        tools = [
            PublishBlogPostTool(plugin=self),
            GetBlogCommentsTool(plugin=self),
            ReplyBlogCommentTool(plugin=self),
            UploadBlogImageTool(plugin=self),
        ]
        if self.enable_moments:
            # 瞬间工具依赖博客安装「瞬间」(plugin-moments) 插件，默认不注册
            tools.extend(
                [
                    PublishBlogMomentTool(plugin=self),
                    ListBlogMomentsTool(plugin=self),
                    DeleteBlogMomentTool(plugin=self),
                ]
            )
            logger.info(
                "已开启瞬间功能，额外注册 LLM 工具: publish_blog_moment / list_blog_moments / delete_blog_moment"
            )
        self.context.add_llm_tools(*tools)

    # ================= 辅助函数 =================

    async def _request(self, method: str, endpoint: str, json_data: Optional[dict] = None, form_data: Optional[aiohttp.FormData] = None) -> dict:
        """异步请求 Halo API"""
        if not self.base_url or not self.token:
            return {"error": "配置未填写", "details": "请在 AstrBot 设置中配置 Halo URL 和 Token"}

        url = f"{self.base_url}{endpoint}"
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json"
        }

        try:
            async with aiohttp.ClientSession() as session:
                req_headers = dict(headers)
                if not form_data:
                    req_headers["Content-Type"] = "application/json"
                req_kw: Dict[str, Any] = {"method": method, "url": url, "headers": req_headers}
                if form_data:
                    req_kw["data"] = form_data
                elif json_data is not None:
                    req_kw["json"] = json_data
                async with session.request(**req_kw) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        logger.warning("API Error %s: %s", resp.status, text[:100])
                        return {"error": f"API Error {resp.status}", "details": text[:200]}
                    try:
                        return json.loads(text) if text.strip() else {}
                    except ValueError:
                        logger.warning("Invalid JSON response: %s", text[:100])
                        return {"error": "响应非 JSON", "details": text[:200]}
        except Exception as e:
            logger.exception("网络请求异常: %s", e)
            return {"error": "网络请求异常", "details": str(e)}

    async def _publish_post(self, name: str, head_snapshot: str = "") -> dict:
        """PUT 控制台发布接口，使草稿正式发布。见 https://api.halo.run/#/PostV1alpha1Console/PublishPost"""
        path = f"{CONSOLE_POSTS}/{name}/publish"
        if head_snapshot:
            path = f"{path}?headSnapshot={quote(head_snapshot, safe='')}&async=false"
        else:
            path = f"{path}?async=false"
        return await self._request("PUT", path)

    async def _get_effective_owner(self) -> str:
        """文章作者：优先用配置的 halo_owner（实时读取，WebUI 改配置无需重载插件）；未配置时通过 token 请求当前用户，并缓存。"""
        owner = str(self.config.get(CONFIG_HALO_OWNER) or "").strip()
        if owner:
            return owner
        if self._cached_owner is not None:
            return self._cached_owner
        username = await self._fetch_current_username_from_token()
        self._cached_owner = username or ""
        if not self._cached_owner:
            logger.warning(
                "未配置 halo_owner 且无法通过 token 获取当前用户，文章作者将由 Halo 默认为 PAT 所属账号。"
            )
        return self._cached_owner

    def _parse_username_from_user_response(self, res: dict) -> str:
        """从用户接口响应中解析登录名（Owner 用）。

        Console GetCurrentUserDetail/GetUserDetail 返回 DetailedUser 包裹结构
        （{"user": {...}, "roles": [...]}），需先解包内层 user 对象；登录名取
        metadata.name（displayName 是昵称，用作 owner 会显示"已删除用户"）。
        """
        if not res or "error" in res:
            return ""
        user = res.get("user")
        if isinstance(user, dict):
            name = self._parse_username_from_user_response(user)
            if name:
                return name
        name = (res.get("metadata") or {}).get("name")
        if name and str(name).strip():
            return str(name).strip()
        username = (res.get("spec") or {}).get("username")
        if username and str(username).strip():
            return str(username).strip()
        return ""

    async def _fetch_current_username_from_token(self) -> str:
        """通过 PAT 请求 Halo 当前用户信息，返回登录名。优先使用 Console GetCurrentUserDetail。"""
        for endpoint in (CONSOLE_USER_SELF, *CONSOLE_USER_SELF_FALLBACKS):
            res = await self._request("GET", endpoint)
            name = self._parse_username_from_user_response(res)
            if name:
                return name
        return ""

    async def _validate_owner_exists(self, owner: str) -> Optional[bool]:
        """校验文章发送账号在 Halo 中是否存在。

        返回 True（存在）/ False（确认不存在，404）/ None（无法校验，如 PAT 缺少用户查看权限）。
        确认不存在时必须中止发布，否则文章作者会显示"已删除用户"。
        """
        res = await self._request("GET", f"{CONSOLE_USERS}/{quote(owner, safe='')}")
        if "error" not in res:
            return True
        if "404" in str(res.get("error", "")):
            return False
        logger.warning(
            "无法校验文章发送账号 %s 是否存在（%s），跳过校验继续发布。", owner, res.get("error")
        )
        return None

    async def _ensure_post_owner(self, post_name: str, owner: str) -> bool:
        """把已创建文章的作者改为配置的账号，返回是否设置成功。

        Console DraftPost 会在服务端用 PAT 所属账号覆盖 spec.owner，因此创建草稿后需
        通过 Content API 资源更新接口把 owner 改回（该接口按原样持久化 spec.owner）。
        失败只意味着作者将显示为 PAT 所属账号，不应阻断发布。
        """
        path = f"{CONTENT_POSTS}/{post_name}"
        res = await self._request("GET", path)
        if "error" in res:
            return False
        spec = res.get("spec") or {}
        if str(spec.get("owner") or "").strip() == owner:
            return True
        spec["owner"] = owner
        res["spec"] = spec
        put_res = await self._request("PUT", path, json_data=res)
        if "error" in put_res:
            logger.warning("设置文章作者为 %s 失败: %s", owner, put_res.get("details", put_res.get("error")))
            return False
        return True

    async def _publish_article(self, title: str, content: str, slug: str) -> str:
        """发布文章完整流程：校验作者 → 创建草稿 → 修正作者 → 发布，返回结果文本。

        @command 与 LLM 工具共用本方法，避免两份逻辑漂移。
        """
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).strip("-") or f"post-{int(time.time())}"
        owner_cfg = str(self.config.get(CONFIG_HALO_OWNER) or "").strip()
        if owner_cfg and self._validated_owner != owner_cfg:
            valid = await self._validate_owner_exists(owner_cfg)
            if valid is False:
                return (
                    f"❌ 发布失败：配置的「文章发送账号」{owner_cfg} 在 Halo 中不存在。"
                    "请填写登录用户名（不是昵称），或到 Halo 后台确认该账号未被删除。"
                )
            self._validated_owner = owner_cfg
        owner = await self._get_effective_owner()
        # 优先走官方 Console 草稿接口 https://api.halo.run/#/PostV1alpha1Console/DraftPost
        draft_payload = _build_console_draft_payload(title=title, content=content, slug=slug, owner=owner)
        res = await self._request("POST", CONSOLE_POSTS, json_data=draft_payload)
        via_console = "error" not in res
        if not via_console:
            # 部分环境 Console 未挂载，回退到 Content API 单资源创建（owner 会被原样存储）
            payload = _build_create_post_payload(title=title, content=content, slug=slug, owner=owner)
            res = await self._request("POST", CONTENT_POSTS, json_data=payload)
            if "error" in res:
                return f"❌ 发布失败: {res.get('details', '未知错误')}"
        post_name = (
            (res.get("metadata") or {}).get("name")
            or ((res.get("post") or {}).get("metadata") or {}).get("name")
            or slug
        )
        head_snapshot = _head_snapshot_from_post_response(res.get("post") or res)
        owner_note = ""
        if via_console and owner_cfg and not await self._ensure_post_owner(post_name, owner_cfg):
            owner_note = f"\n⚠️ 未能将作者设置为 {owner_cfg}（可能 PAT 权限不足），当前作者为 PAT 所属账号。"
        pub_res = await self._publish_post(post_name, head_snapshot)
        if "error" in pub_res:
            stage = "草稿" if via_console else "文章"
            return f"❌ {stage}已创建但发布失败: {pub_res.get('details', '未知错误')}"
        post_url = f"{self.base_url}/archives/{slug}"
        result = f"✅ 发布成功！\n文章标题: {title}"
        if owner:
            result += f"\n✍️ 作者: {owner}"
        result += f"\n🔗 链接: {post_url}{owner_note}"
        return result

    # ================= Command / Tools =================
    
    @command("publish_blog_post")
    async def publish_post(self, event: AstrMessageEvent, title: str, content: str, slug: Optional[str] = None):
        """
        发布一篇新的博客文章。
        Args:
            title (str): 文章标题
            content (str): 文章正文（Markdown 格式）
            slug (str): (可选) URL路径别名
        """
        yield event.plain_result(await self._publish_article(title=title, content=content, slug=slug or ""))

    @command("get_blog_comments")
    async def get_comments(self, event: AstrMessageEvent):
        """获取博客最新的评论列表"""
        
        # size 必须 > 0，否则 Halo 会 WARN: Page size must be greater than 0
        endpoint = f"/apis/{API_CONTENT}/comments?sort=metadata.creationTimestamp,desc&page=0&size=5"
        res = await self._request("GET", endpoint)

        if "error" in res:
            yield event.plain_result(f"❌ 获取失败: {res['error']}")
            return

        items = res.get("items", [])
        if not items:
            yield event.plain_result("📭 暂无新评论。")
            return

        msg_list = ["📝 最新 5 条评论："]
        for item in items:
            spec = item.get("spec", {})
            metadata = item.get("metadata", {})
            
            c_name_id = metadata.get("name")
            c_user = spec.get("owner", {}).get("displayName", "匿名用户")
            c_content = spec.get("content", "无内容")
            
            if len(c_content) > 50:
                c_content = c_content[:50] + "..."
            
            msg_list.append(f"--------------\n👤 {c_user}: {c_content}\n🆔 ID: {c_name_id}")

        msg_list.append("\n💡 让 AI 回复请说: '帮我回复评论 [ID] 内容...'")
        yield event.plain_result("\n".join(msg_list))

    @command("reply_blog_comment")
    async def reply_comment(self, event: AstrMessageEvent, comment_id: str, content: str):
        """
        回复博客评论 (自动查找关联文章)
        Args:
            comment_id (str): 评论的唯一 ID (name)
            content (str): 回复内容
        """
        info_res = await self._request("GET", f"/apis/{API_CONTENT}/comments/{comment_id}")
        
        if "error" in info_res:
            yield event.plain_result(f"❌ 找不到原评论 (ID: {comment_id})")
            return
            
        post_id = info_res.get("spec", {}).get("subjectRef", {}).get("name")
        if not post_id:
            yield event.plain_result("❌ 无法解析原评论所属文章，回复失败。")
            return

        reply_uuid = str(uuid.uuid4())
        payload = {
            "apiVersion": API_CONTENT,
            "kind": "Comment",
            "metadata": {"name": reply_uuid},
            "spec": {
                "content": content,
                "subjectRef": {
                    "group": "content.halo.run",
                    "kind": "Post",
                    "name": post_id,
                    "version": "v1alpha1"
                },
                "parentId": comment_id
            }
        }

        res = await self._request("POST", f"/apis/{API_CONTENT}/comments", json_data=payload)
        
        if "error" in res:
            yield event.plain_result(f"❌ 回复失败: {res.get('details', '未知错误')}")
        else:
            yield event.plain_result(f"✅ 回复成功！")

    # ================= LLM Tool 实现（由 FunctionTool.call 调用） =================

    async def _llm_publish_post(
        self,
        event: AstrMessageEvent,
        title: str,
        content: str,
        slug: str = "",
    ) -> str:
        return await self._publish_article(title=title, content=content, slug=slug)

    async def _llm_get_comments(self, event: AstrMessageEvent) -> str:
        # size 必须 > 0，否则 Halo 会 WARN: Page size must be greater than 0
        endpoint = f"/apis/{API_CONTENT}/comments?sort=metadata.creationTimestamp,desc&page=0&size=5"
        res = await self._request("GET", endpoint)
        if "error" in res:
            return f"获取失败: {res['error']}"
        items = res.get("items", [])
        if not items:
            return "暂无新评论。"
        lines = ["最新 5 条评论："]
        for item in items:
            spec = item.get("spec", {})
            metadata = item.get("metadata", {})
            c_name_id = metadata.get("name")
            c_user = spec.get("owner", {}).get("displayName", "匿名用户")
            c_content = spec.get("content", "无内容")
            if len(c_content) > 50:
                c_content = c_content[:50] + "..."
            lines.append(f"用户 {c_user}: {c_content}，评论 ID: {c_name_id}")
        return "\n".join(lines)

    async def _llm_reply_comment(
        self,
        event: AstrMessageEvent,
        comment_id: str,
        content: str,
    ) -> str:
        info_res = await self._request("GET", f"/apis/{API_CONTENT}/comments/{comment_id}")
        if "error" in info_res:
            return f"找不到原评论 (ID: {comment_id})"
        post_id = info_res.get("spec", {}).get("subjectRef", {}).get("name")
        if not post_id:
            return "无法解析原评论所属文章，回复失败。"
        payload = {
            "apiVersion": API_CONTENT,
            "kind": "Comment",
            "metadata": {"name": str(uuid.uuid4())},
            "spec": {
                "content": content,
                "subjectRef": {
                    "group": "content.halo.run",
                    "kind": "Post",
                    "name": post_id,
                    "version": "v1alpha1",
                },
                "parentId": comment_id,
            },
        }
        res = await self._request("POST", f"/apis/{API_CONTENT}/comments", json_data=payload)
        if "error" in res:
            return f"回复失败: {res.get('details', '未知错误')}"
        return "回复成功。"

    # ================= 瞬间（plugin-moments UC API，仅 LLM 工具） =================

    @staticmethod
    def _moment_error_hint(res: dict) -> str:
        """把瞬间接口的常见错误翻译成可操作的提示。"""
        err = str(res.get("error", ""))
        details = str(res.get("details", ""))
        if "404" in err:
            return "未找到瞬间接口（404）。请先在博客后台安装并启用「瞬间」(plugin-moments) 插件。"
        if "401" in err or "403" in err:
            return "没有权限操作瞬间（401/403）。请确认 Token 所属账号拥有瞬间发布权限（超级管理员或被授予瞬间相关角色）。"
        return f"{err}: {details}" if details else err

    async def _llm_publish_moment(
        self,
        event: AstrMessageEvent,
        content: str,
        visible: str = "",
    ) -> str:
        raw = (content or "").strip()
        if not raw:
            return "瞬间内容不能为空。"
        payload = _build_moment_payload(raw, visible=(visible or "PUBLIC").upper())
        # 服务端会把 spec.owner 设为 Token 所属账号（即发布者），无需传参
        res = await self._request("POST", UC_MOMENTS, json_data=payload)
        if "error" in res:
            return f"瞬间发布失败: {self._moment_error_hint(res)}"
        spec = res.get("spec") or {}
        name = str((res.get("metadata") or {}).get("name") or "")
        result = "✅ 瞬间发布成功。"
        if spec.get("approved") is False:
            result += "\n⏳ 当前账号没有瞬间审核权限，内容已提交待审核，审核通过后才会公开展示。"
        if name:
            result += f"\n🆔 瞬间 ID: {name}"
        if name and (spec.get("visible") or "PUBLIC") == "PUBLIC" and self.base_url:
            result += f"\n🔗 链接: {self.base_url}/moments/{name}"
        return result

    async def _llm_list_moments(self, event: AstrMessageEvent, limit: int = 5) -> str:
        try:
            size = max(1, min(20, int(limit)))
        except (TypeError, ValueError):
            size = 5
        endpoint = f"{UC_MOMENTS}?sort=spec.releaseTime,desc&page=0&size={size}"
        res = await self._request("GET", endpoint)
        if "error" in res:
            return f"获取瞬间失败: {self._moment_error_hint(res)}"
        items = res.get("items") or []
        if not items:
            return "暂无瞬间。"
        lines = [f"共 {len(items)} 条瞬间："]
        for item in items:
            spec = item.get("spec") or {}
            metadata = item.get("metadata") or {}
            raw = str((spec.get("content") or {}).get("raw") or "").strip()
            if len(raw) > 50:
                raw = raw[:50] + "..."
            release = str(
                spec.get("releaseTime") or metadata.get("creationTimestamp") or "未知时间"
            )
            line = f"· [{release}] {raw or '(无文字内容)'} (ID: {metadata.get('name', '')})"
            tags = ",".join(spec.get("tags") or [])
            if tags:
                line += f" 标签: {tags}"
            if spec.get("visible") == "PRIVATE":
                line += " [私密]"
            if spec.get("approved") is False:
                line += " [待审核]"
            lines.append(line)
        lines.append("如需删除某条瞬间，请提供对应的瞬间 ID。")
        return "\n".join(lines)

    async def _llm_delete_moment(self, event: AstrMessageEvent, moment_name: str) -> str:
        name = (moment_name or "").strip()
        if not name:
            return "缺少瞬间 ID。请先通过 list_blog_moments 获取要删除的瞬间 ID。"
        res = await self._request("DELETE", f"{UC_MOMENTS}/{quote(name, safe='')}")
        if "error" in res:
            return f"删除瞬间失败: {self._moment_error_hint(res)}"
        return f"✅ 已删除瞬间 {name}。"

    async def _llm_upload_image(
        self,
        event: AstrMessageEvent,
        image_url: str,
    ) -> str:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status != 200:
                        return "无法下载图片源文件。"
                    img_bytes = await resp.read()
        except Exception as e:
            return f"下载异常: {e}"
        file_name = f"upload_{int(time.time())}.jpg"
        form_data = aiohttp.FormData()
        form_data.add_field("file", img_bytes, filename=file_name, content_type="image/jpeg")
        form_data.add_field("policy", "default")
        form_data.add_field("group", "default")
        res = await self._request(
            "POST", f"/apis/{API_CONSOLE}/attachments/upload", form_data=form_data
        )
        if "error" in res:
            return f"上传 Halo 失败: {res.get('details', '未知错误')}"
        permalink = res.get("spec", {}).get("permalink", "")
        return f"上传成功，链接: {permalink}"

    @command("upload_blog_image")
    async def upload_image(self, event: AstrMessageEvent):
        """
        上传图片到博客。
        """
        target_img_url = None
        
        for component in event.message_obj.message:
            if isinstance(component, Image):
                target_img_url = component.url
                break
        
        if not target_img_url:
            yield event.plain_result("⚠️ 请发送包含图片的指令。")
            return

        yield event.plain_result("⏳ 正在下载并上传...")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(target_img_url) as resp:
                    if resp.status != 200:
                        yield event.plain_result("❌ 无法下载图片源文件。")
                        return
                    img_bytes = await resp.read()
        except Exception as e:
            yield event.plain_result(f"❌ 下载异常: {e}")
            return

        file_name = f"upload_{int(time.time())}.jpg"
        form_data = aiohttp.FormData()
        form_data.add_field('file', img_bytes, filename=file_name, content_type='image/jpeg')
        form_data.add_field('policy', 'default')
        form_data.add_field('group', 'default')

        res = await self._request("POST", f"/apis/{API_CONSOLE}/attachments/upload", form_data=form_data)

        if "error" in res:
            yield event.plain_result(f"❌ 上传 Halo 失败: {res.get('details', '未知错误')}")
        else:
            permalink = res.get("spec", {}).get("permalink", "")
            yield event.plain_result(f"✅ 上传成功！\n🔗 Link: {permalink}")
