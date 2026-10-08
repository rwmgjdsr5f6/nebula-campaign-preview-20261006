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

## `--index` 保留记录搜索框（`test_index_search.py`）

- 五条合成记录验收：甲、乙共用 `a@example.invalid`，丙用 `b@`，丁用
  `c@`，前四人属 newsletter，戊用 `d@` 属 archive；模板“你好，
  {{name}}！”以 LF 结束。`--exclude-email c@example.invalid --index`
  后退出 0，分组命中、排除、最终预览依次为 4、1、3，目录含三份连续
  编号预览、`report.json` 与 `index.html`；保留行初始全部可见且带
  `retained-row` 标记，甲、乙为共享邮箱的两个独立条目、各自相对链接。
- 搜索框静态契约：恰一个无初值（无 `value` 属性）、无 `name`、未禁用、
  不被 `form` 包裹、无按钮的文本输入，带 `for` 关联的 label；页面恰有
  一个无属性（无 `src`）的内联 `<script>`，直接读输入框 `value`、监听
  `input` 事件、加载时先执行一次，按 `retained-row` 类只收集保留行，
  对嵌入的原始姓名/邮箱数据岛各做一次 `indexOf` 包含判断；脚本不含
  `trim`、大小写归一、`replace` 等任何查询归一，也不含 `fetch`、
  XHR、`http`、`src=`、`import` 等可发起请求的标记。表体末尾的
  “没有符合搜索条件的联系人”提示行带固定 id 且初始 `hidden`，排除
  区域的行一律不带 `retained-row` 类。
- 过滤行为：从真实落盘页面脚本中解析 `var fields` 数据岛（`<`、`>`、
  `&` 以 `\uXXXX` 转义，`json.loads` 还原后须与 CSV 原字段逐字相等），
  按与脚本逐行对应的规则（空查询全显；否则子串包含）模拟全部验收查询：
  初始/清空为甲、乙、丙三行；`a@` 显甲、乙；`丙` 只显丙；`c@` 零命中、
  提示行应显示；`A@` 因区分大小写零命中；再清空恢复三行。
- 空格与特殊字符：首尾及内部连续空格逐字参与匹配（“张  三”命中而
  “张 三”零命中、查询尾空格保留、邮箱首格保留、不修剪不误中）；姓名含
  尖括号、引号、`&`、`{{name}}` 时数据岛无裸尖括号、不成 `</script>`，
  还原后与原文相等，`</script>`、`<X>` 等查询按普通字面/大小写规则处理。
- `--format html` 与默认 text 的搜索规则一致，仅链接扩展名不同；零命中
  与全部排除时没有保留表格、固定显示“没有可预览的联系人”，但搜索框仍
  渲染可输入、脚本数据岛为空且容忍提示行元素不存在（节点下真实执行内联
  脚本无异常由实现时以 node 抽查，测试内以空数据岛模拟覆盖）；任何查询
  下三个计数与排除区域恒为完整结果。

## report.json 创建失败（`test_report_write_failure.py`）

- 固定合成输入：contacts.csv 表头 `name,email,segment`，数据行
  甲/a@example.invalid/newsletter 与 乙/b@example.invalid/newsletter；
  template.txt 为“你好，{{name}}！”并以一个 LF 结束。命令开启
  `--index`，分别覆盖默认文本格式与 `--format html`。
- 每种格式先跑无故障对照：退出 0、stdout/stderr 为空，目录恰好含两份
  连续编号预览、`report.json` 与 `index.html`；报告计数 2、0、2，
  排除明细为空，预览清单按甲、乙顺序对应邮箱与实际扩展名。
- 随后在相同输入的新输出目录中注入故障：仅对目标 `report.json` 的
  `open` 在文件创建前抛出 `PermissionError("report-write-denied")`
  （其余文件操作正常）。预期退出 2、stdout 为空，stderr 包含
  report.json 路径与该原因、无 Traceback；两份预览完整保留且与同格式
  对照逐字节一致，`report.json` 与 `index.html` 均不存在，无其他
  新增文件。
- 注入机制为纯标准库、跨平台：测试生成一个 `sitecustomize.py` 放入
  独立临时目录，仅故障运行时 prepend 到子进程 `PYTHONPATH` 并以环境
  变量传入目标路径；该模块包装 `builtins.open`，仅对目标路径抛错。
  不需要管理员权限、不修改真实目录权限、不耗尽磁盘空间，也不依赖
  RLIMIT 等平台专用接口，核心用例在 Windows 与 Linux 上都不跳过。

## report.json 创建成功后正文写入失败（`test_report_body_write_failure.py`）

- 与上一节覆盖同一写入点的不同阶段：上一节在文件创建前（`open` 时）
  失败，`report.json` 不存在；本节令 `report.json` 已成功创建后的
  **第一次正文 `write`** 在写入任何字节前抛出
  `OSError("report-body-write-denied")`，`open` 本身成功，其余文件
  操作正常。
- 固定合成输入、命令（开启 `--index`）与两种格式（默认文本、
  `--format html`）均与上一节相同；每种格式先跑无故障对照（退出 0、
  stdout/stderr 为空，目录恰含两份连续编号预览、`report.json` 与
  `index.html`；报告计数 2、0、2，排除明细为空，预览清单按甲、乙
  顺序对应邮箱与实际扩展名）。
- 故障运行预期退出 2、stdout 为空，stderr 包含 `report.json` 完整
  路径与该原因、无 Traceback；两份预览完整保留且与同格式对照逐字节
  一致，`report.json` 存在但为零字节（创建成功、正文一字未写），
  `index.html` 不存在（索引页在报告之后写出，报告失败即不执行），
  目录没有其他文件，两份输入文件内容保持不变。
- 注入机制同样为纯标准库、跨平台：`sitecustomize.py` 包装
  `builtins.open`，对目标路径仍调用真实 `open`（文件以 `"x"` 成功
  新建），再把返回对象包一层代理——代理的第一次 `write` 在转发前抛
  `OSError`（任何字节都不落盘），上下文管理与其余属性原样转发，
  `with` 块正常关闭该零字节文件；非目标路径完全不受影响。不依赖
  管理员权限、真实权限变更、磁盘耗尽或平台专用接口，Windows 与
  Linux 上核心用例均不跳过。

