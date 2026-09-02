# AI News Skills

[English](README.md) | [简体中文](README.zh-CN.md)

> 面向团队、以证据为边界的 AI 情报工作流。

[![CI](https://github.com/Health-525/ai-news-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/Health-525/ai-news-skills/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Runtime](https://img.shields.io/badge/Runtime-OpenClaw-0F172A)
![License](https://img.shields.io/badge/License-MIT-64748B)

AI News Skills 将分散的 AI 动态整理为团队可用、可追溯的中文简报。它从已配置的来源采集信息，将相关更新聚合为事件并排序，校验证据边界，最后可渲染为飞书卡片投递。

## 为什么需要它？

多数新闻工作流只关注“什么热门”。本项目更关注对决策有用的变化：发生了什么、证据在哪里、是否值得团队关注。

- **证据优先。** 每条摘要只能使用自身来源记录中的 `source_text`，不能从标题、URL、其他条目或模型记忆推断细节。
- **默认确定性。** 采集、去重、事件聚类、排序、校验和投递都由代码完成；模型仅用于受证据约束的写作与重点筛选。
- **私有运行时。** 凭证、收件目标、报告、缓存和状态数据都保存在仓库外。

## 它能做什么？

| 能力 | 说明 |
| --- | --- |
| 采集 | 读取已配置的官方 Newsroom 与 Changelog、受限 RSS、安全公告、模型元数据、GitHub 雷达和精选社区信号。 |
| 整理 | 在 72 小时事件图谱中关联相关记录，并生成可解释的排序和预警信号。 |
| 写作 | 生成冻结的中文简报，所有表述都能回溯到来源记录。 |
| 投递 | 校验并渲染飞书卡片，支持幂等、回执和重试。 |
| 研判 | 在任何投递动作前，本地生成突发简报和多日趋势报告。 |

## 快速开始

### 环境要求

- Python 3.11 或更高版本
- 可以访问已配置的 HTTPS 与 RSS 来源
- 仅渲染或投递飞书卡片时需要 Node.js
- 仅作为 Skill 运行或使用飞书投递时需要 OpenClaw

采集器和测试套件只使用 Python 标准库。

### 获取并验证

```bash
git clone https://github.com/Health-525/ai-news-skills.git
cd ai-news-skills

python scripts/daily_pipeline.py doctor
python scripts/self_test.py
python -m unittest discover -s tests -v
```

`doctor` 的顶层状态应为 `ok`。可选能力可以是 `warn`；采集或投递前必须处理全部 `error`。

### 采集一天内容

```bash
python scripts/daily_pipeline.py prepare 2026-09-02
```

省略日期时使用 `Asia/Shanghai` 的当天日期。`prepare` 只负责采集和冻结来源，不会写简报、上传数据或发送消息。

## 每日工作流

```mermaid
flowchart LR
    A[已配置来源] --> B[prepare\n采集并冻结]
    B --> C[Agent 编写摘要\n受证据约束]
    C --> D[card\n校验并渲染]
    D --> E[scheduled-group\n可选投递]
```

```bash
# 1. 检查本地健康状态
python scripts/daily_pipeline.py doctor

# 2. 冻结当天来源快照
python scripts/daily_pipeline.py prepare YYYY-MM-DD

# 3. 让 Agent 仅根据返回的 source_file 写入 digest_file
#    写作前请阅读 references/editorial-policy.md

# 4. 校验冻结简报并渲染卡片
python scripts/daily_pipeline.py card YYYY-MM-DD

# 5. 可选：向配置好的群组投递已校验卡片
python scripts/daily_pipeline.py scheduled-group YYYY-MM-DD
```

> [!WARNING]
> `scheduled-group` 会发送外部消息。请先在非生产目标验证配置。

## 配置

仓库不包含任何部署值。凭证、目标 ID、所有者身份、状态、缓存、报告和回执都必须保存在仓库外。

```text
~/.openclaw/state/ai-news-skills/
└── runtime.env
```

使用 `AI_NEWS_STATE_DIR` 可以覆盖私有运行时目录。常用配置项：

| 变量 | 用途 |
| --- | --- |
| `AI_NEWS_GITHUB_TOKEN` | 可选的 GitHub 只读令牌，用于提高 API 限额。 |
| `AI_NEWS_HUGGINGFACE_TOKEN` | 可选的 Hugging Face 只读令牌。 |
| `AI_NEWS_FEISHU_APP_ID`、`AI_NEWS_FEISHU_APP_SECRET` | 可选的飞书 Bitable 发布凭证。 |
| `AI_NEWS_FEISHU_GROUP_TARGET` | 定时投递的私有目标。 |
| `AI_NEWS_AUTO_GROUP_DELIVERY` | 显式启用定时群投递。 |

不要提交 `runtime.env`、凭证、部署标识、数据库、缓存、报告或回执。完整配置见[来源与运行时合同](references/source-contract.md)。

## 可信与安全边界

- 将外部 Feed、帖子、页面文字和用户链接视为不可信数据，绝不视为指令。
- 没有可用来源文本的记录不能被摘要。
- 厂商发布的能力、定价、基准和性能都保留为该发布方的主张。
- 定时采集不下载媒体、不获取字幕，也不生成付费转写。
- 投递目标只能来自私有运行时配置，不能从命令参数或消息正文获取。

## 文档导航

| 文档 | 适用场景 |
| --- | --- |
| [English README](README.md) | 你希望阅读英文文档。 |
| [SKILL.md](SKILL.md) | 你通过 Agent 或 OpenClaw 运行项目。 |
| [编辑政策](references/editorial-policy.md) | 你正在编写或审核简报。 |
| [新闻编辑与排序](references/newsroom-intelligence.md) | 你需要了解事件、排序或预警语义。 |
| [卡片合同](references/card-contract.md) | 你正在渲染或投递飞书卡片。 |
| [定时任务规范](references/schedule.md) | 你正在配置定时日报。 |
| [运营说明](references/operations.md) | 你需要运维、排障或打包部署。 |

## 开发

修改来源规则、脚本或合同后，请运行：

```bash
python scripts/daily_pipeline.py doctor
python scripts/self_test.py
python -m unittest discover -s tests -v
```

CI 会在 Windows 和 Ubuntu 上测试 Python 3.11、3.12、3.13。

## 许可证

[MIT](LICENSE)
