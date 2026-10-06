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
- 成功退出 0；输入校验失败、输入不可读、参数不合法或输出目录非空/不可写时退出 2，且不创建任何输出。

## 回归测试

测试位于 `tests/`，仅依赖 Python 3 标准库，全部样例使用合成联系人与虚构邮箱（`example.invalid`），无需公网或外部服务。从项目根目录执行：

```sh
python -m unittest discover -s tests
```

测试通过 `python -m newsletter_preview` 公开入口以子进程驱动，核对：

- 有效输入退出 0，输出目录恰好包含 `preview-0001.txt`、`preview-0002.txt` 与 `report.json`；预览正文/换行逐字节准确，报告含模板原文、筛选值、`matched_count` 与按 CSV 顺序对应的预览清单（共享邮箱不合并）。
- 模板含未知变量、CSV 缺少 `segment` 列、必需字段为空时退出 2，stderr 含对应错误信息且无异常堆栈；尚不存在的输出目录保持不存在，预先存在的空目录仍为空。

测试在临时目录中自建样例输入与输出，结束后自动清理，不改动项目中已有文件；可重复执行。
