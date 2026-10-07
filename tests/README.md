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
