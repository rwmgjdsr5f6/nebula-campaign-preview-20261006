# 邮件活动离线预览台

计划管理邮件活动内容、收件人分组和离线预览。面向本地单机使用，采用 Python 3 标准库与 SQLite。

## 离线预览（已实现）

读取合成联系人 CSV 与文字模板，按 `segment` 单值筛选，生成逐人文本预览与 JSON 报告。
纯本地、离线执行，不包含任何发送动作。

### 用法

```bash
python -m newsletter_preview \
  --contacts contacts.csv \
  --template template.txt \
  --segment newsletter \
  --out previews
```

### 输入

- 所有输入均为 UTF-8。
- 联系人 CSV 首行为表头，必需列为 `name`、`email`、`segment`，列顺序不限，额外列忽略。
- 筛选按原文区分大小写精确比较，不修剪值。
- 模板仅支持 `{{name}}`：全部替换为该行姓名；替换值原样写入、不再次解析；其余文字与换行原样保留。

### 输出

- 匹配行按 CSV 顺序写入 `preview-0001.txt`、`preview-0002.txt`……连续编号，UTF-8；重复邮箱不合并。
- `report.json` 包含 `template`（模板原文）、`segment`（筛选值原文）、`matched_count` 与 `previews`；
  `previews` 与预览文件同序，逐项记录 `email` 与 `file`。
- 输出目录不存在时创建；已存在时必须为空。零匹配时仅生成报告：`matched_count` 为 0，`previews` 为空列表。

### 退出码

- `0`：成功（含零匹配）。
- `2`：输入或参数不合法，原因写入标准错误，且不创建输出目录或文件、不覆盖已有文件。包括：
  - CSV 缺少必需列；
  - 任一行（含未匹配行）必需值为空或仅含空白，错误信息标注列名与行号；
  - 行字段数与表头不一致，错误信息标注行号；
  - 模板出现 `{{name}}` 以外的完整双花括号占位符（如 `{{city}}`），错误信息指出未知变量；
  - 输入文件无法读取或非有效 UTF-8、命令参数不合法、输出路径非目录、输出目录非空或不可写。

### 三行样例

```csv
name,email,segment
林溪,lin@example.invalid,newsletter
周宁,zhou@example.invalid,newsletter
何山,he@example.invalid,archive
```

模板 `你好，{{name}}！`、`--segment newsletter` 生成 `preview-0001.txt`（你好，林溪！）与
`preview-0002.txt`（你好，周宁！），报告 `matched_count` 为 2。
联系人与邮箱均为合成数据（使用虚构的 `example.invalid` 域名）。
