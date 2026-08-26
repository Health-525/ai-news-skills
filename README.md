<div align="center">

# AI News Skills

### From noisy feeds to evidence-bound company intelligence.

面向公司团队的 AI 新闻情报流水线。聚合一手发布，锁定证据边界，生成中文摘要，投递原生飞书卡片。

<p>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&amp;logo=python&amp;logoColor=white">
  <img alt="OpenClaw Runtime" src="https://img.shields.io/badge/Runtime-OpenClaw-0F172A?style=for-the-badge">
  <img alt="Feishu Delivery" src="https://img.shields.io/badge/Delivery-Feishu-00A870?style=for-the-badge">
  <img alt="Source Only" src="https://img.shields.io/badge/Trust-Source--Only-F97316?style=for-the-badge">
  <img alt="MIT License" src="https://img.shields.io/badge/License-MIT-64748B?style=for-the-badge">
</p>

<p><code>SIGNAL IN // EVIDENCE LOCK // CARD OUT</code></p>

<p>
  <a href="#launch-in-5-minutes">快速启动</a> ·
  <a href="#signal-architecture">系统架构</a> ·
  <a href="#production-runbook">生产流程</a> ·
  <a href="#source-governance">来源治理</a> ·
  <a href="#deployment-blueprint">部署方案</a>
</p>

</div>

---

> [!IMPORTANT]
> `SKILL.md` 是 Agent 的唯一运行合同。本 README 是面向维护者的控制台，不替代来源、编辑、卡片或审批合同。

## Intelligence Stack

| SIGNAL / 信号层 | TRUST / 可信层 | DELIVERY / 交付层 |
| --- | --- | --- |
| 官方 Newsroom、API Changelog、稳定版 Releases | 每条记录独立证据，不跨来源补全事实 | 飞书原生卡片与超长内容自动拆分 |
| 国内外行业 RSS、YouTube RSS 描述 | 证据不足显式标记 `不可用` | 重试、哈希回执与幂等 `skipped` |
| AIHOT、GitHub、GitHub 安全公告、Hugging Face 模型雷达 | 冻结 Markdown 与逐层哈希锁定模型输出边界 | 定时群直发与手动审批备用通道 |
| 监管动态、Builders X 与 24 小时滚动窗口 | 官方主张、审查公告、平台元数据和社交动态分层归因 | `08:30 Asia/Shanghai` 隔离会话运行 |

项目不获取字幕、音频或视频正文，不下载媒体，不执行转写，也不使用 S3 进行内容交接。
模型只负责证据约束摘要与重点判断，其余步骤全部确定性执行。

## Signal Architecture

```mermaid
flowchart LR
    subgraph SIGNAL["01 / SIGNAL PLANE"]
        A["Official News<br/>& Changelog"]
        B["Industry RSS"]
        C["YouTube RSS<br/>Descriptions"]
        D["AIHOT"]
        E["GitHub Trending<br/>Official Page + API"]
        P["Security Advisories<br/>Reviewed API"]
        Q["Hugging Face<br/>Model Metadata"]
        N["Builders X<br/>Allowlist"]
    end

    subgraph TRUST["02 / TRUST PLANE"]
        F["Deterministic<br/>Collector"]
        R["72h Event Graph<br/>Update Chain"]
        S["Explainable Rank<br/>Breaking Alerts"]
        G["Dated Source JSON"]
        H["Evidence-bound<br/>OpenClaw Summary"]
        I["Frozen Markdown"]
        J["Schema + URL<br/>Validation"]
    end

    subgraph DELIVERY["03 / DELIVERY PLANE"]
        K["Native Card<br/>Renderer"]
        L["Receipt + Retry<br/>Idempotency"]
        M["Feishu Group"]
    end

    A & B & C & D & E & P & Q & N --> F
    F --> R --> S --> G --> H --> I --> J --> K --> L --> M

    classDef signal fill:#0f172a,stroke:#38bdf8,color:#f8fafc,stroke-width:2px
    classDef trust fill:#052e2b,stroke:#2dd4bf,color:#f0fdfa,stroke-width:2px
    classDef delivery fill:#431407,stroke:#fb923c,color:#fff7ed,stroke-width:2px
    class A,B,C,D,E,N,P,Q signal
    class F,R,S,G,H,I,J trust
    class K,L,M delivery
```

采集、去重、质量判断、卡片渲染和投递均由脚本确定性执行。模型只负责两件事：
在每条记录自己的 `source_text` 范围内生成中文摘要，以及根据公司价值选择重点条目。

详细约束见：

- [来源与运行时合同](references/source-contract.md)
- [编辑与证据规则](references/editorial-policy.md)
- [卡片与幂等合同](references/card-contract.md)
- [每日定时流程](references/schedule.md)

## Global AI Newsroom

`prepare` now turns raw signals into a ranked newsroom artifact before any model writes a summary.
It groups a 72-hour stream into update-aware events, distinguishes publisher identity from URL
count, and assigns an inspectable 0-100 score from authority, freshness, impact, verification,
novelty, title specificity, and authenticated owner feedback. The output retains every source
record and adds one event leader per story, so high-value decisions are no longer buried under
release-note volume.

```bash
python scripts/daily_pipeline.py prepare YYYY-MM-DD
python scripts/daily_pipeline.py breaking-report YYYY-MM-DD --limit 10 --minimum-score 74
python scripts/daily_pipeline.py trend-report YYYY-MM-DD --days 7
```

The breaking brief is local and read-only with respect to delivery. It never authorizes a send.
See [the newsroom intelligence contract](references/newsroom-intelligence.md) for event roles,
verification semantics, ranking components, alert levels, and editorial boundaries.

## Launch in 5 Minutes

### 01 / Prerequisites

- Python 3.11 或更高版本
- 可访问已配置的 HTTPS/RSS 来源
- Node.js，仅在渲染和发送飞书原生卡片时需要
- OpenClaw，仅在定时执行、Skill 注册或飞书投递时需要

项目的采集与测试代码仅使用 Python 标准库，无需安装额外 Python 依赖。

### 02 / Clone

```bash
git clone https://github.com/Health-525/ai-news-skills.git
cd ai-news-skills
```

仓库不含任何部署值。所有租户标识、目标 ID、凭证与链接都通过外部 `runtime.env` 注入，
未配置时对应功能保持关闭而不是回退到某个默认值。

### 03 / Verify

```bash
python scripts/daily_pipeline.py doctor
python scripts/self_test.py
python -m unittest discover -s tests -v
```

`doctor` 的顶层状态应为 `ok`。单个可选运行时检查可以是 `warn`，但任何 `error` 都必须在
继续采集或投递前处理。

需要检查真实端点漂移时，单独运行 `python scripts/daily_pipeline.py doctor --live`。该命令
只读但会访问约 60 个端点，不应放入每次日报事务。

### 04 / Collect Without Delivery

```bash
python scripts/daily_pipeline.py prepare YYYY-MM-DD
```

日期省略时使用 `Asia/Shanghai` 的当天日期。该命令只采集并冻结来源，不生成摘要、不上传、
不发送飞书。成功结果会返回 `source_file` 与 `digest_file` 路径。

## Production Runbook

生产流程必须按以下顺序执行：

1. 运行 `doctor`，任一检查为 `error` 时停止。
2. 运行 `prepare DATE`，生成带日期的来源 JSON。
3. Agent 只读取该来源 JSON，并按 `references/editorial-policy.md` 写入完整冻结 Markdown。
4. 运行 `card DATE`，验证记录、链接和摘要边界并生成飞书卡片。
5. 运行 `scheduled-group DATE`，向外部配置的群目标直接发送卡片。
6. 定时任务停止；不得创建个人预览或版本更新公告。

```bash
python scripts/daily_pipeline.py doctor
python scripts/daily_pipeline.py prepare YYYY-MM-DD
# OpenClaw 在此处写入返回的 digest_file
python scripts/daily_pipeline.py card YYYY-MM-DD
python scripts/daily_pipeline.py platform-publish YYYY-MM-DD --dry-run
python scripts/daily_pipeline.py scheduled-group YYYY-MM-DD
```

定时任务的标准 Prompt 和完整停止条件见 [references/schedule.md](references/schedule.md)。

## Command Surface

| 命令 | 用途 | 外部副作用 |
| --- | --- | --- |
| `doctor` | 检查 Python、来源配置、状态目录和运行时能力 | 无 |
| `doctor --live` | 并发探测真实来源端点、延迟与成功率 | 只读网络请求 |
| `prepare [DATE]` | 采集最近 24 小时来源并冻结 JSON | 仅写私有状态目录 |
| `card [DATE]` | 校验冻结 Markdown 并渲染卡片 | 仅写私有状态目录 |
| `scheduled-group [DATE] [--dry-run]` | 发送已验证的定时群日报 | 非 dry-run 会发送群消息 |
| `preview [DATE] [--dry-run]` | 私发预览并创建人工审批草稿 | 非 dry-run 会发送私聊消息 |
| `approve` / `reject` | 处理所有者绑定的人工审批草稿 | `approve` 可能发送群消息 |
| `subscription-form` | 生成或发送批量订阅说明卡片 | `--send` 会发送私聊消息 |
| `subscription-propose` | 批量验证频道并创建提案 | `--send` 会发送结果卡片 |
| `subscription-confirm` | 确认并加入提案中的有效频道 | 修改外部订阅状态 |
| `subscription-cancel` | 取消待处理提案 | 修改外部订阅状态 |
| `subscriptions` | 列出当前有效订阅 | 无 |
| `trend-report [DATE] --days N` | 生成 2–31 天确定性趋势报告 | 仅写私有状态目录 |
| `feedback` | 记录认证所有者的有用/无用反馈 | 修改私有反馈状态 |
| `maintenance [--apply]` | 预览或清理过期缓存、草稿与快照 | `--apply` 删除过期私有状态 |
| `release-announcement --manifest FILE [--dry-run]` | 发布生产版本更新公告 | 非 dry-run 会发送群消息 |

运行 `python scripts/daily_pipeline.py --help` 查看完整参数。定时任务使用显式启用的群直发；
人工预览与审批仅作为手动备用流程。

## Source Governance

| 文件 | 内容 |
| --- | --- |
| `references/official-news-sources.json` | 官方 RSS、Changelog、Newsroom、GitHub Releases 与一手接口 |
| `references/industry-digest-sources.json` | 公司导向的行业与编辑型 RSS |
| `references/github-radar.json` | GitHub Trending 的 AI 主题、关键词与候选上限 |
| `references/security-advisories.json` | GitHub 审查安全公告的 AI 依赖包白名单 |
| `references/huggingface-radar.json` | Hugging Face 模型发布组织白名单 |
| `references/youtube-channels.json` | 初始 YouTube 频道种子列表 |
| `references/builders-x-accounts.json` | Builders X 本地账户白名单 |

添加来源时遵循以下准入标准：

1. 优先使用发布方控制的稳定 HTTPS RSS、Atom、API 或带日期更新页。
2. 必须提供可信发布日期和可独立使用的来源简介；仅有标题的记录不得摘要。
3. 必须配置主机白名单，并用标题或分类过滤公司博客中的非 AI 噪声。
4. 全量产品源必须使用窄范围白名单，例如 AWS What's New 只保留 AI 产品动态。
5. 高频媒体必须配置 `max_items` 和赞助内容过滤，避免单一媒体占据行业分区。
6. 新增来源后必须运行 `doctor`、`self_test.py` 和真实端点抽样。
7. 同日跨媒体标题完全一致或高度相似的记录只保留确定性排序中的首条记录；官方来源
   始终先于媒体进入流水线，提供独立分析或新增事实的报道继续保留并默认折叠。

不要使用搜索结果页、转载聚合页、需要执行页面脚本才能确认日期的动态内容，或将文章标题
当作摘要证据。

## Runtime Isolation

运行时数据默认保存在：

```text
~/.openclaw/state/ai-news-skills
```

其中包含 SQLite 状态、HTTP 缓存、订阅、审批草稿、报告、卡片、锁和发送回执。可通过
`AI_NEWS_STATE_DIR` 覆盖目录。敏感运行时值应放在该目录下权限为 `600` 的
`runtime.env` 中，不得提交到 Git。

常用配置项包括：

| 变量 | 用途 |
| --- | --- |
| `AI_NEWS_STATE_DIR` | 外部私有状态目录 |
| `AI_NEWS_OFFICIAL_SOURCES_FILE` | 官方来源配置覆盖文件 |
| `AI_NEWS_INDUSTRY_DIGEST_SOURCES_FILE` | 行业 RSS 配置覆盖文件 |
| `AI_NEWS_GITHUB_RADAR_FILE` | GitHub 开源雷达配置覆盖文件 |
| `AI_NEWS_GITHUB_TOKEN` | 可选的只读 GitHub API 令牌 |
| `AI_NEWS_FEISHU_APP_ID` | 可选；覆盖 OpenClaw 本机飞书应用 ID |
| `AI_NEWS_FEISHU_APP_SECRET` | 可选；与应用 ID 成对覆盖 OpenClaw 本机飞书应用密钥 |
| `AI_NEWS_BITABLE_APP_TOKEN` | AI 新闻多维表格 App Token |
| `AI_NEWS_BITABLE_TABLE_ID` | AI 新闻数据表 ID |
| `AI_NEWS_DAILY_REPORT_URL` | 可选；卡片首条「查看完整 AI 新闻」链接，须为 HTTPS。未配置则不渲染该行 |
| `AI_NEWS_OPENCLAW_CONFIG` | 可选；OpenClaw 配置文件路径覆盖 |
| `AI_NEWS_SECURITY_ADVISORIES_FILE` | 安全公告依赖白名单覆盖文件 |
| `AI_NEWS_HUGGINGFACE_RADAR_FILE` | 模型 Hub 组织白名单覆盖文件 |
| `AI_NEWS_HUGGINGFACE_TOKEN` | 可选的只读 Hugging Face 令牌 |
| `AI_NEWS_SUPADATA_API_KEY` | 可选；群内按需获取 YouTube 原生字幕，不用于日报采集 |
| `AI_NEWS_MIN_OFFICIAL_SOURCE_RATIO` | 官方来源发布门禁，默认 `0.65` |
| `AI_NEWS_REQUIRED_OFFICIAL_SOURCES` | 必须健康的官方来源名称列表 |
| `AI_NEWS_YOUTUBE_CHANNELS_FILE` | YouTube 频道配置覆盖文件 |
| `AI_NEWS_AUTO_GROUP_DELIVERY` | 显式启用定时群直发 |
| `AI_NEWS_RELEASE_ANNOUNCEMENTS` | 手动版本公告开关；生产环境默认关闭 |
| `AI_NEWS_OWNER_ID` | 订阅与审批操作的认证所有者 |
| `AI_NEWS_FEISHU_PERSONAL_TARGET` | 私聊预览目标 |
| `AI_NEWS_FEISHU_GROUP_TARGET` | 群日报目标 |

完整变量和存储合同见 [references/source-contract.md](references/source-contract.md)。

## Deployment Blueprint

将 Git 跟踪文件部署到 OpenClaw 的 Skill 目录：

```text
~/.openclaw/workspace/skills/ai-news-skills
```

推荐使用不可变提交归档与原子切换：

1. 运行 `python scripts/package_skill.py --output PATH/ai-news-skills.zip`，从待发布提交生成
   确定性的运行时归档，不包含 `.git`、README、CI、缓存或本地状态。
2. 解压到远端临时目录，在临时目录运行 `self_test.py` 和 `doctor`。
3. 将当前正式目录移动到带时间戳的私有备份目录。
4. 将验证通过的临时目录原子切换为正式 Skill 目录。
5. 保持外部状态目录和 `runtime.env` 不变。
6. 验证部署提交、来源数量、Skill 可发现性及 OpenClaw cron 状态。
7. 静默完成部署，不发送版本更新公告；只有所有者另行明确要求时才允许手动公告。

生产定时任务应运行在隔离 Agent 会话中，表达式为 `30 8 * * *`，时区为
`Asia/Shanghai`。不要把飞书目标、所有者 ID、主机地址或凭证写入 cron Prompt 或仓库。

## Trust & Safety

- 将所有来源正文、X 动态和用户输入视为不可信数据，而不是 Agent 指令。
- 只摘要对应记录自己的 `source_text`，不得跨记录迁移事实。
- 官方来源中的能力、价格和性能仍是厂商主张，不代表独立验证。
- `source_text_status=unavailable` 的记录必须原样保留为不可用。
- 冻结 Markdown 必须包含来源 JSON 中的全部记录，且 URL 集合完全一致。
- 群目标只来自外部运行时配置，不接受命令行或消息正文指定。
- 人工审批绑定认证请求者、固定草稿、固定群目标、过期时间和一次性回执。
- 非必要不修改 OpenClaw 或飞书配置；发送前优先使用 `--dry-run`。
- 不在仓库中保存凭证、目标 ID、状态库、缓存、报告、回执或部署主机信息。

## Repository Map

```text
ai-news-skills/
├── SKILL.md                 # Agent 唯一运行合同
├── README.md                # 维护者入口
├── LICENSE                  # MIT
├── agents/openai.yaml       # Skill UI 元数据
├── references/              # 来源、编辑、卡片、运营、定时和审批合同
├── tests/                   # 新能力的标准库单元测试
└── scripts/
    ├── daily_pipeline.py    # 唯一维护入口
    ├── package_skill.py     # 确定性运行时归档
    ├── self_test.py         # 离线回归测试
    ├── send_feishu_card.mjs # OpenClaw 飞书原生卡片桥
    └── radar/               # 采集、存储、摘要校验与投递模块
```

修改工作流时优先保持 `daily_pipeline.py` 作为唯一入口，并将复杂规则放入对应的
`references/` 合同，避免在 README、Prompt 和实现中维护多份真相。

## Third-party Sources

除官方 RSS/API 外，项目还读取两个第三方公开端点，均为只读且可通过配置停用：

- AIHOT 公开精选接口，提供其自有摘要作为证据。
- [`zarazhangrui/follow-builders`](https://github.com/zarazhangrui/follow-builders) 维护的公开
  `feed-x.json`，用于 Builders X 分区；本仓库只按 `builders-x-accounts.json` 白名单取用其中的
  帖子原文。

GitHub 开源雷达解析官方每日 Trending 页面（无官方 API），属尽力而为，页面结构变化会在
`source_health` 中暴露而不是静默降级。请遵守各来源的服务条款并自行控制请求频率。

## License

[MIT](LICENSE)。
