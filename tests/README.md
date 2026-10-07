# 回归测试说明

对既有命令行流程（按 segment 筛选 → 姓名替换 → 文本预览与 JSON 报告）的
黑盒回归测试。仅使用 Python 3 标准库（`unittest` + `subprocess` +
`tempfile`），不访问公网或外部服务；样例联系人均为合成数据，邮箱使用
`example.invalid` 虚构域名。

## 运行

在项目根目录执行：

```sh
python -m unittest discover -s tests
```

加 `-v` 可查看逐用例名称。测试可重复执行，结论一致。

## 覆盖内容

- 成功样例（`test_success_generates_previews_and_report`）：
  经 `python -m newsletter_preview` 公开入口运行，核对退出码为 0、
  stderr 为空；输出目录恰好包含 `preview-0001.txt`、`preview-0002.txt`
  与 `report.json`；两份预览按 CSV 顺序分别替换为张若岚、李望舒，
  正文与换行逐字节一致；报告保留模板原文与筛选值，`matched_count` 为 2，
  `previews` 按相同顺序对应邮箱和文件名（共享邮箱保留为两条独立记录）。
- 失败样例（从成功输入各只改一处，均要求退出码 2、stderr 有对应提示、
  无 `Traceback` 未捕获异常）：
  - 模板增加 `{{age}}`：stderr 包含未知变量 `age`；
  - CSV 删除 `segment` 整列：stderr 包含缺失列 `segment`；
  - 未匹配的 archive 联系人 `name` 留空：stderr 包含第 4 行与空字段
    `name` 的信息。
  每个失败样例同时核对：尚不存在的输出目录运行后仍不存在；
  预先存在的空目录运行后仍为空，不留下任何预览或报告。

所有断言针对真实子进程的退出码、stderr 与落盘内容，不直接调用内部函数。
每个样例使用独立临时目录，结束后自动清理，不改动项目中的已有文件。

## `--format text/html` 输出格式（`test_format_html.py`）

- 省略 `--format` 与显式 `--format text` 的全部产物逐字节一致，均为
  `preview-0001.txt` 起连续编号的文本预览与 `report.json`。
- `--format html` 时逐人预览为 `preview-0001.html` 起连续编号，目录中
  只有 HTML 文件与 `report.json`（无文本副本）；HTML 声明 UTF-8、不引用
  网络资源，正文在唯一 `pre` 元素内按字面显示（以 `html.parser` 按浏览器
  实体规则还原后与替换正文逐字比较）：模板与字段值中的 `&`、`<`、`>`、
  标签及实体样式文字均不成其为页面元素，中文、空格、空行、正文开头与末尾
  换行保留；报告 `template` 保留原文、计数与顺序不变，`previews` 清单的
  `file` 改为 `.html` 文件名。零命中仍退出 0 且只生成报告。
- `--format` 取值非法或缺值退出 2，stderr 点名 `--format`、无 Traceback、
  不创建输出；html 模式下未知变量、缺列、输入不可读同样退出 2（新目录不
  创建、已有空目录保持为空）；非空输出目录被拒绝且原内容保留。

## 联系人 CSV 的起始 BOM 兼容（`test_contacts_bom.py`）- 同一份联系人数据，一份为普通 UTF-8、一份以 EF BB BF 三个字节开头，
  分别输出到独立空目录：两次均退出 0，只生成 `preview-0001.txt` 与
  `report.json`，且两份输出逐字节一致；报告中 `segment_count` 为 2、
  `excluded_count` 为 1、`matched_count` 为 1，排除明细只有乙，预览
  清单只对应甲的邮箱，预览正文为“你好，甲！”并保留末尾 LF。
- 带 BOM 的样例删除 `segment` 整列：退出 2，stderr 点名缺列 `segment`，
  无 Traceback；尚不存在的输出目录仍不存在，已有空目录仍为空。
- 边界：字段内部的 U+FEFF 原样保留；模板开头或正文内部的 U+FEFF 仍
  作为正文保留在预览与报告原文中；BOM 之后的非法 UTF-8 字节仍导致
  退出 2；仅含 BOM 的文件与空文件同样报缺少表头行；文件开头有两个
  BOM 时只忽略第一个。

## `--exclude-file` 名单文件排除（`test_exclude_file.py`）

- 固定五人验收样例（甲、乙共用 a@example.invalid，丙用 b@，丁用 c@，
  四人属 newsletter；戊共用甲的邮箱但属 archive）：excludes.txt 含
  两行甲的邮箱与一个空行，命令增加 `--exclude-file excludes.txt
  --exclude-email c@example.invalid`，退出 0、stderr 为空，仅生成丙的
  `preview-0001.txt`（正文“你好，丙！”并保留末尾 LF）与 `report.json`；
  分组命中、排除、最终预览依次为 4、3、1，排除明细按 CSV 顺序列甲、乙、
  丁，预览清单仅列丙的邮箱与文件名，戊不进入清单。
- 行结束与空行规则：LF、CRLF、末行无换行（两种）及穿插空行/空白行的
  等价名单产物逐字节一致；空文件与仅含空白行的文件视为空名单；非空白行
  只移除行结束符，保留大小写与首尾空白；后无 LF 的孤立 CR 按正文保留；
  开头 U+FEFF 不剥离（名单文件无 BOM 处理）。
- 合并与一致性：文件名单与等价的 `--exclude-email` 命令行逐字节一致；
  两处来源及文件内重复值合并去重，不叠加计数；未命中值不影响结果；
  零命中也要成功读取名单。省略新参数的输出与基线逐字节一致。
- `--format html` 与 `--index` 采用同一名单：HTML 预览编号、正文转义与
  报告清单同步为 `.html`；索引页只含保留记录的相对链接，排除区域与报告
  同内容同顺序，全部排除时显示“没有可预览的联系人”并列出全部排除条目，
  只生成 `report.json` 与 `index.html`。
- 失败样例：名单文件不存在、是目录、不可读（chmod 000，root 下跳过）、
  含非法 UTF-8 字节（含零命中分组）以及 `--exclude-file` 缺值，均退出 2、
  stderr 点名路径或 `--exclude-file` 与原因、无 Traceback；尚不存在的
  输出目录不创建，已存在的空目录保持为空。

## `report.json` 创建失败（`test_report_write_failure.py`）

- 固定样例：contacts.csv 表头 `name,email,segment`，甲
  （a@example.invalid）、乙（b@example.invalid）两条 newsletter
  记录；template.txt 正文“你好，{{name}}！”末尾一个 LF。经
  `python -m newsletter_preview` 以筛选值 newsletter、独立临时输出
  目录并开启 `--index` 运行，覆盖默认 text（省略 `--format`）与
  `--format html` 两种格式。
- 无故障对照（每种格式）：退出 0、stdout/stderr 为空；输出目录恰好
  两份连续编号预览、`report.json` 与 `index.html`；报告分组命中、
  排除、最终预览计数依次为 2、0、2，排除明细为空，预览清单按甲、乙
  顺序对应邮箱与实际扩展名。
- 故障运行（相同输入、新输出目录）：临时包装脚本在子进程内包装
  `builtins.open`，仅对名为 `report.json` 的路径在文件创建前抛出
  `PermissionError("report-write-denied")`，其余文件操作正常，再以
  runpy 按公开入口语义执行。预期退出 2，stderr 点名 report.json 的
  路径与底层原因 `report-write-denied`、无 Traceback；两份预览完整
  保留且与同格式对照逐字节一致，`report.json` 与 `index.html` 均不
  存在，目录无其他新增文件。
- 故障注入仅用标准库：不需要管理员权限、不修改真实目录权限、不耗尽
  磁盘空间、不依赖平台专用接口（无 resource/信号/chmod），Windows
  与常见 Linux 环境均可执行，核心用例不跳过。

