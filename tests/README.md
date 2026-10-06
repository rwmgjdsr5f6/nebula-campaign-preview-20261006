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
