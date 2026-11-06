---
name: halo-manager
description: Halo 博客管理工具使用指南。当用户要发布/浏览文章、读取文章内容、查看或回复/发表/删除评论、发布/浏览/删除瞬间、上传图片到博客时，先阅读本技能，了解应调用哪个工具、参数怎么填、ID 从哪来、哪些操作需要先确认。
---

# Halo 博客管理工具指南

本插件（halo_manager）通过一组 LLM 工具管理一个 Halo 2.x 博客。使用工具前先记住三条核心约定：

1. **所有读取/回复/删除都靠列表工具返回的 ID**。文章 ID、评论 ID、瞬间 ID 都来自对应列表工具的输出，格式为 `(ID: xxx)` 或 `评论 ID: xxx`。不要凭空编造 ID；用户只给了标题/内容摘要时，先调用列表工具查找匹配项。
2. **列表工具每页固定 4 条**（list_blog_posts / list_blog_moments / list_blog_post_comments），参数 `page` 从 1 开始。输出会注明「第X/共Y页」；还有下一页时，引导用户说「查看第 N 页」。
3. **作者过滤 owner 参数**（list_blog_posts / list_blog_moments 共用同一套语义）：
   - 留空或 `all`：全部公开内容（默认）；
   - `me`：自己发布的（瞬间含私密与待审核，文章优先含草稿）；
   - 填 Halo **登录用户名**（不是昵称）：该用户发布的公开内容。

## 工具速查

| 工具 | 用途 | 关键参数 |
|------|------|----------|
| `publish_blog_post` | 发布新文章（Markdown 正文） | `title`, `content`, `slug?` |
| `list_blog_posts` | 分页列出已发布文章（带文章 ID） | `owner?`, `page?` |
| `get_blog_post_content` | 按文章 ID 读取全文（Markdown 源文） | `post_id` |
| `get_blog_comments` | 全站最新 5 条评论（带评论 ID） | 无 |
| `list_blog_post_comments` | 分页列出某篇文章下的公开评论（带评论 ID） | `post_id`, `page?` |
| `reply_blog_comment` | 回复一条已有评论 | `comment_id`, `content` |
| `create_blog_comment` | 在某篇文章下发表一条新评论 | `post_id`, `content` |
| `delete_blog_comment` | 删除一条评论（其下回复一并删除） | `comment_id` |
| `upload_blog_image` | 上传图片 URL 到博客图床并返回链接 | `image_url` |
| `publish_blog_moment` | 发布一条瞬间（短内容，支持简单 Markdown） | `content`, `visible?` |
| `list_blog_moments` | 分页浏览瞬间列表（带瞬间 ID） | `owner?`, `page?` |
| `delete_blog_moment` | 删除一条瞬间 | `moment_name` |

注意：`list_blog_post_comments` 只显示**已通过审核的公开评论**；待审核评论需到博客后台查看。瞬间相关工具是否注册取决于插件配置开关「注册瞬间发布与管理工具」，`delete_blog_comment` 取决于「注册评论删除工具」开关——工具列表里没有就不要尝试调用。

## 典型工作流

### 浏览文章 → 读全文

1. `list_blog_posts`（可带 `owner` 过滤）拿到文章列表与文章 ID；
2. `get_blog_post_content` 用文章 ID 读取正文；超过 4000 字会被截断并注明总长，可提示用户到网页版查看全文。

### 文章评论管理

1. 用 `list_blog_posts` 找到目标文章 ID（用户已提供 ID 则跳过）；
2. `list_blog_post_comments` 用 `post_id` 列出该文章的公开评论，取得评论 ID；
3. 按用户意图选择：
   - 回复某条评论 → `reply_blog_comment`（`comment_id` + `content`）；
   - 发表新评论 → `create_blog_comment`（`post_id` + `content`）；
   - 删除某条评论 → `delete_blog_comment`（`comment_id`，需删除开关已开启）。
4. 用户只说「看看最近评论」时直接用 `get_blog_comments`（全站最新 5 条，不分页）。

### 瞬间

- 发布 → `publish_blog_moment`；删除 → `delete_blog_moment`（`moment_name` 来自 `list_blog_moments`）；
- 浏览 → `list_blog_moments`，`owner` 可选 all / me / 用户名。

## 错误提示含义

- 瞬间接口 404：博客未安装/未启用「瞬间」(plugin-moments) 插件；
- 401/403：Token 所属账号权限不足（删除他人的瞬间/评论需要相应管理角色或超管）；
- 「待审核」：内容已提交成功，审核通过后才会公开展示，不是失败；
- 读文章 404：文章 ID 有误，或该文章未发布/未公开。

## 安全规则

- **删除类操作（delete_blog_moment / delete_blog_comment）必须先向用户复述目标内容的摘要并得到确认后再调用**；用户只说「删掉它」时，先列出候选条目让用户确认是哪一条。
- 发表评论/回复前，如用户未给出明确文案，先拟稿征求确认再调用。
- 评论与瞬间以博客 Token 所属账号身份发表，不要在文案中冒充其他身份。
