# AI News Skills platform contract

The optional publisher writes a validated frozen digest to a Feishu Bitable. Miaoda reads or syncs
that Bitable and never receives credentials or direct requests from OpenClaw.

## Bitable fields

Create one table with these exact field names and types:

| Field | Type |
| --- | --- |
| `记录键` | Text, unique business key |
| `新闻ID` | Text |
| `日报日期` | Date |
| `板块` | Single select |
| `标题` | Text |
| `中文摘要` | Multiline text |
| `来源名称` | Text |
| `原文链接` | URL |
| `发布时间` | Date and time |
| `是否重点` | Checkbox |
| `是否补录` | Checkbox |
| `GitHub总Star` | Number |
| `排序` | Number |
| `上传时间` | Date and time |

`记录键` is `<report_date>:<news_id>`. The publisher lists records for the report date, then sends
new rows through the official batch-create API and existing rows through batch-update. A failed
request never deletes historical rows.

Keep the Bitable app token and table ID only in the EC2 private runtime environment. By default the
publisher reuses the single Feishu application already configured on the same OpenClaw host; explicit
app credentials are an optional paired override and must never be copied into Miaoda. Grant the app the
minimum read/create/update Bitable record permissions and add the app as a collaborator on the target
Bitable. Never publish raw source text, ranking diagnostics,
confidence, event graphs, credentials, or Feishu target identifiers.
