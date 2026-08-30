<div align="center">

# 🌌 AstrBot Plugin: Halo Manager

<a href="https://github.com/Soulter/AstrBot">
  <img src="https://img.shields.io/badge/AstrBot-v3.x%2B-blue" alt="AstrBot Version">
</a>
<a href="https://halo.run">
  <img src="https://img.shields.io/badge/Halo-2.x-orange" alt="Halo Version">
</a>
<a href="https://www.python.org/">
  <img src="https://img.shields.io/badge/Python-3.8%2B-green" alt="Python">
</a>

**通过 AI 聊天机器人轻松管理你的 Halo 2.x 博客**

</div>

## 📖 简介

这是一个专为 [AstrBot](https://github.com/Soulter/AstrBot) 设计的插件，旨在让博主能够通过自然语言与 LLM 对话，完成博客的日常维护工作。

无论你是想快速发布一篇灵感笔记、回复读者的评论，还是将手机里的照片传到图床，只需要一句话，AI 就能帮你搞定。支持 **Halo 2.x** 及以上版本。

## ✨ 核心特性

- **📝 AI 写作与发布**：利用 LLM 的能力撰写文章，并直接推送到博客（支持自动生成 Slug）。
- **💬 评论管理**：随时查询最新评论，并让 AI 帮你拟定回复内容。
- **🖼️ 快捷图床**：发送图片给机器人，自动上传到 Halo 附件库并返回 Markdown 链接。
- **🔐 安全可靠**：基于 Halo Personal Access Token (PAT) 认证，配置简单且安全。

## ⚙️ 配置说明

在 AstrBot 管理面板的插件配置页中填写：

| 配置项 | 说明 |
|--------|------|
| 博客地址 (URL) | Halo 博客的完整访问地址，例如 `http://192.168.1.10:8090` |
| 个人访问令牌 (PAT) | 在 Halo 后台 -> 用户 -> 个人访问令牌 中生成的 Token |
| 文章发送账号 (Owner，可选) | 文章作者使用的 Halo **登录用户名**（不是昵称） |
| 注册瞬间发布与管理工具 | 开启后额外注册发布/列出/删除瞬间 3 个 AI 工具；需博客已安装并启用「瞬间」(plugin-moments) 插件，发布者为 Token 所属账号 |

关于「文章发送账号」：

- 留空时，文章作者默认为 PAT 所属账号。
- Halo 的 Console 发布接口会在服务端把作者强制设置为 PAT 所属账号，因此插件会在创建草稿后自动把作者改为该账号；请确保 PAT 拥有文章的读取与更新权限。
- 发布前会校验该账号在 Halo 中是否存在；若填写了不存在或已删除的用户名，发布会被拒绝（否则文章作者将显示为「已删除用户」）。

## 🚀 安装方法

### 方式一：通过 AstrBot 插件市场安装（推荐）

1. 打开 AstrBot 管理面板。
2. 进入 **插件管理 (Plugin Market)**。
3. 搜索 `halo_manager` 并点击安装。

### 方式二：手动安装

1. 进入 AstrBot 的插件目录：
   ```bash
   cd AstrBot/data/plugins/
   


### 第三部分：使用指南 (Usage Guide)

### 让 AI 能调用本插件（LLM 工具）

插件按 [AstrBot 文档 - 定义 Tool](https://docs.astrbot.app/dev/star/guides/ai.html#%E5%AE%9A%E4%B9%89-tool) 的推荐方式，使用 **FunctionTool 类 + `add_llm_tools`** 在 `__init__` 中注册了 4 个 **LLM 工具**（开启配置项「注册瞬间发布与管理工具」后，还会额外注册 3 个瞬间工具），AI 在对话时可自动选择调用：

| 工具名 | 说明 |
|--------|------|
| `publish_blog_post` | 发布一篇新博客文章 |
| `get_blog_comments` | 获取最新评论列表 |
| `reply_blog_comment` | 回复指定评论 |
| `upload_blog_image` | 将图片 URL 上传到博客 |
| `publish_blog_moment` | 发布一条瞬间（需开启「注册瞬间发布与管理工具」，且博客已安装瞬间插件） |
| `list_blog_moments` | 获取 Token 所属账号的瞬间列表（同上） |
| `delete_blog_moment` | 删除指定瞬间（同上） |

**启用方式**：在 AstrBot 管理面板中进入 **提供方 / 函数工具**（或 **LLM 工具**）设置，在可用工具列表中勾选上述 Halo Manager 提供的工具并保存。启用后，AI 在回答「帮我发一篇博客」「看看最近评论」「回复评论 xxx」等问题时会自动调用对应工具。

```markdown
## 💡 使用指南 (Prompt Examples)

配置并启用 LLM 工具后，你可以直接与机器人对话。以下是一些通过测试的指令示例：

### ✍️ 发布文章

> **用户**: 帮我写一篇关于“Python 异步编程”的技术博客，重点介绍 asyncio 库，写完直接发布到博客上。

**机器人**: *（思考并写作中...）* 好的，正在发布文章《Python 异步编程入门》...
**机器人**: ✅ 发布成功！链接: https://blog.example.com/archives/python-asyncio

### 📬 查看与回复评论

> **用户**: 最近博客有人评论吗？

**机器人**: 📝 最新评论：
--------------
👤 张三: 文章写得很棒！
🆔 ID: 12345
📄 文章ID: post-hello-world
--------------
💡 提示: 回复请调用 reply_blog_comment

> **用户**: 帮我回复张三：谢谢支持，我会继续努力的！

**机器人**: ✅ 回复成功！

### 📤 上传图片

1. **发送一张图片** 给机器人。
2. 紧接着（或同时引用图片）说：
> **用户**: 把这张图上传到博客。

**机器人**: ✅ 图片上传成功！
🔗 链接: https://blog.example.com/upload/2023/10/img.jpg
## ❓ 常见问题 (FAQ)

**Q: 插件支持 Halo 1.x 吗？**
A: **不支持**。Halo 1.x 与 2.x 的 API 架构完全不同，本插件专为 Kubernetes Native 风格的 Halo 2.x API 设计。

**Q: 发布文章失败，提示 401 Unauthorized？**
A: 请检查你的 Token 是否正确，或者 Token 是否过期。建议重新生成一个不过期的 PAT。

**Q: 图片上传失败？**
A: 请确保 Halo 后台的 **附件设置** 允许上传图片，且你配置的存储策略（默认为 `default`）是可用的。

**Q: 文章作者显示「已删除用户」？**
A: 说明文章的 owner 指向一个已不存在的账号（常见于 PAT 所属账号被删除，或配置里填了已删除的用户名/昵称）。可在插件配置中把「文章发送账号」设置为一个有效的 Halo 登录用户名，新发布的文章将以此账号作为作者。

**Q: 发布瞬间失败，提示未找到瞬间接口（404）？**
A: 说明博客尚未安装或未启用「瞬间」(plugin-moments) 插件，请先到 Halo 后台应用市场安装并启用；同时确认插件配置页已勾选「注册瞬间发布与管理工具」（修改后需重载插件）。若提示 401/403，则是 Token 所属账号缺少瞬间发布权限。

## 📜 许可证

本项目采用 [MIT License](LICENSE) 开源。




