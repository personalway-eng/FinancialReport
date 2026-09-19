# X（Twitter）关注时间线接入

项目已接入 X 官方 API v2，用于采集当前账户关注对象发布的最新帖子。数据会写入现有的 `news_articles` 表，随后自动参与 DeepSeek 财经分析。

## 授权要求

请在 X Developer Portal 创建应用并启用 OAuth 2.0。授权作用域至少包括：

- `tweet.read`
- `users.read`
- `offline.access`（建议启用，用于刷新令牌）

使用该账户的 OAuth 用户访问令牌配置本机环境。不要将任何令牌提交到仓库。

推荐把令牌仅保存在本机文件中：将 `config/x_credentials.env.example` 复制为
`config/x_credentials.env`，然后只填写 `X_USER_ACCESS_TOKEN`。采集脚本会自动
读取它；该文件已被 `.gitignore` 忽略。

PowerShell 当前会话示例：

```powershell
$env:X_USER_ACCESS_TOKEN = "你的 OAuth 用户访问令牌"
# 可选；未设置时采集器会调用 /2/users/me 自动识别当前账户
$env:X_USER_ID = "你的 X 用户 ID"
```

## 运行采集

```powershell
.\.venv\Scripts\python.exe scripts\x_timeline_collector.py
```

默认获取一页 50 条、排除转帖。常用选项：

```powershell
# 获取最多两页，每页 100 条
.\.venv\Scripts\python.exe scripts\x_timeline_collector.py --max-results 100 --pages 2

# 保留转帖并排除回复
.\.venv\Scripts\python.exe scripts\x_timeline_collector.py --include-retweets --exclude-replies
```

帖子链接在数据库中唯一，因此重复执行只会写入新内容。未设置 `X_USER_ACCESS_TOKEN` 时脚本会安全跳过，便于先接入代码、后完成授权。

## 官方 MCP

官方 X MCP 服务地址为 `https://api.x.com/mcp`，适合在 MCP 客户端中交互查询 X 数据；定时任务采用同一官方 API 的 REST 时间线端点，避免在无人值守任务中运行 MCP 的 stdio 桥接进程。

官方 MCP 的 OAuth 桥接器配置说明：<https://github.com/xdevplatform/docs/blob/main/tools/mcp.mdx>
