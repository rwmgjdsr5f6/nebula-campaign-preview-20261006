# 邮件活动离线预览台

计划管理邮件活动内容、收件人分组和离线预览。面向本地单机使用，采用 Python 3 标准库与 SQLite。

## 离线预览

读取联系人 CSV 与文字模板，按 segment 单值筛选，生成逐人文本预览与 JSON 报告（不发送邮件）：

```sh
python -m newsletter_preview --contacts contacts.csv --template template.txt --segment newsletter --out previews
```

- CSV 为 UTF-8，首行表头须含 `name`、`email`、`segment`（列顺序不限，额外列忽略）。
- 模板仅支持 `{{name}}` 与 `{{email}}` 变量（可混合或重复出现，取当前记录原始值，替换值不再解析）；其他 `{{...}}` 占位符视为未知变量。
- 可选的 `--exclude-email` 可重复提供，每次一个邮箱：匹配记录中 `email` 与任一排除值完全相同（区分大小写、不修剪空白）的记录将被移除；共享邮箱的匹配记录全部排除，其他重复邮箱各自保留。排除值为空或仅含空白时退出 2。
- 输出目录不存在时创建，存在时须为空；逐人预览按保留记录的 CSV 顺序命名为 `preview-0001.txt` 起连续编号（不因排除留空号），另生成 `report.json`（含模板原文、筛选值、排除前的分组命中记录数 `segment_count`、因 `--exclude-email` 被移除的记录数 `excluded_count`、被移除记录的明细 `excluded_contacts`（按 CSV 顺序逐项列出原始 `name` 与 `email`，共享邮箱的每条命中各列一项）、最终预览人数 `matched_count` 与预览清单；计数均按 CSV 数据记录统计，不按唯一邮箱）。
- 成功退出 0；输入校验失败、输入不可读、参数不合法或输出目录非空/不可写时退出 2，且不创建任何输出。
