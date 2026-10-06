# 邮件活动离线预览台

计划管理邮件活动内容、收件人分组和离线预览。面向本地单机使用，采用 Python 3 标准库与 SQLite。

## 离线预览

读取联系人 CSV 与文字模板，按 segment 单值筛选，生成逐人文本预览与 JSON 报告（不发送邮件）：

```sh
python -m newsletter_preview --contacts contacts.csv --template template.txt --segment newsletter --out previews
```

- CSV 为 UTF-8，首行表头须含 `name`、`email`、`segment`（列顺序不限，额外列忽略）。
- 模板仅支持 `{{name}}` 变量；其他 `{{...}}` 占位符视为未知变量。
- 输出目录不存在时创建，存在时须为空；逐人预览命名为 `preview-0001.txt` 起连续编号，另生成 `report.json`（含模板原文、筛选值、匹配人数与预览清单）。
- 可选 `--exclude-email EMAIL`（可重复）：从匹配结果中排除与该值完全相同的邮箱（区分大小写，不修剪空白）；保留记录仍从 `preview-0001.txt` 连续编号，`report.json` 只统计保留记录。值为空或仅含空白时退出 2。
- 成功退出 0；输入校验失败、输入不可读、参数不合法或输出目录非空/不可写时退出 2，且不创建任何输出。
