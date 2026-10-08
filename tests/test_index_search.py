"""--index 索引页保留记录搜索框的黑盒回归测试。

覆盖用户验收约定：开启 --index 后 index.html 在三个计数与保留清单
之上额外提供一个完全离线的搜索框——初始为空、显示全部保留记录；
输入变化时由页内一段内联脚本即时更新保留行可见性，姓名或邮箱任一
原始字段包含整个查询字符串即显示，匹配区分大小写、不修剪首尾空白、
不折叠连续空格，尖括号、引号、& 与 {{name}} 样式文字均按普通文字
参与匹配，任意查询合法；非空查询零命中时表体显示“没有符合搜索
条件的联系人”，清空查询恢复全部行；各次显示保持 CSV 顺序、原同目录
相对链接及共享邮箱的独立条目。搜索只改变保留行可见性：三个计数
（分组命中、排除、最终预览）始终表示完整生成结果，“已排除的联系
人”区域内容不变；零命中或全部排除时没有保留表格，保留区域固定
显示“没有可预览的联系人”，搜索框仍可输入。默认 text 格式与
--format html 的搜索规则一致；搜索完全离线：无表单提交、无网络
请求、不依赖任何外部资源。逐人预览、report.json 与省略 --index
时的产物由其余测试文件保证，本文件不再重复。

环境无浏览器可用，行为核对分两层，缺一不可：
1. 静态契约（HTMLParser 按浏览器规则解析真实落盘页面）：搜索框恰为
   一个无初值、无 name、未禁用的文本输入，带关联 label，不被 form
   包裹；页面恰有一个无 src 的内联 <script>，直接读取输入框 value、
   以 indexOf 对嵌入的原始姓名/邮箱数据做包含判断（脚本文本中不
   出现 trim、大小写归一、replace 等处理），监听 input 事件并在
   加载时先执行一次；表体每个保留行带 retained-row 类，表体末尾的
   “没有符合搜索条件的联系人”提示行带固定 id 且初始 hidden，排除
   区域的行一律不带 retained-row 类；脚本不含 fetch、XHR、http、
   src、import 等任何可发起请求的标记。
2. 数据驱动模拟：从真实页面脚本中解析出嵌入的 fields 数据岛
   （JSON，尖括号/& 等以 \\uXXXX 转义，json.loads 还原后须与 CSV
   原字段逐字相等），按与脚本逐行对应的规则（query 为空全部可见；
   否则 Python 子串运算 query in 字段 等价于 字段.indexOf(query)
   !== -1）计算每条保留行的可见性与提示行状态，对用户验收的每个
   查询逐一断言。模拟读取的是生成物中的真实数据，而非测试自己另
   造的一份。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 用户验收固定样例：甲、乙共用 a@example.invalid，丙用
# b@example.invalid，丁用 c@example.invalid，前四人按此顺序属于
# newsletter；戊用 d@example.invalid，属于 archive。
CONTACTS = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,a@example.invalid,newsletter\n"
    "丙,b@example.invalid,newsletter\n"
    "丁,c@example.invalid,newsletter\n"
    "戊,d@example.invalid,archive\n"
)
# 模板以恰好一个 LF 结束。
TEMPLATE = "你好，{{name}}！\n"
SEGMENT = "newsletter"
EMAIL_A = "a@example.invalid"
EMAIL_B = "b@example.invalid"
EMAIL_C = "c@example.invalid"

SEARCH_BOX_ID = "contact-search"
RETAINED_ROW_CLASS = "retained-row"
SEARCH_EMPTY_ID = "search-empty-row"
SEARCH_EMPTY_MESSAGE = "没有符合搜索条件的联系人"
NO_PREVIEW_MESSAGE = "没有可预览的联系人"
NO_EXCLUDED_MESSAGE = "没有被排除的联系人"
SEARCH_LABEL = "搜索姓名或邮箱："

# 空格/大小写验收：甲的姓名首尾各一空格、内部两空格，邮箱首格；
# 李四无空格。查询据此构造“若修剪或折叠就会误判”的正反用例。
SPACE_SEGMENT = "news  letter"
SPACE_CONTACTS = (
    "name,email,segment\n"
    '" 张  三 "," A@x.invalid",%s\n'
    "李四,b@x.invalid,%s\n" % (SPACE_SEGMENT, SPACE_SEGMENT)
)
SPACE_NAME = " 张  三 "
SPACE_EMAIL = " A@x.invalid"

# 特殊字符验收：姓名含尖括号、&、双引号及 {{name}} 样式文字。
SPECIAL_CONTACTS = (
    "name,email,segment\n"
    '"<x>&""{{name}}",q@example.invalid,newsletter\n'
)
SPECIAL_NAME = '<x>&"{{name}}'


class _IndexSearchParser(HTMLParser):
    """按浏览器规则解析索引页中与搜索相关的结构。

    区域划分与 test_index_page 的解析器一致：h2“已排除的联系人”
    之前的表格是保留表，之后是排除表。收集：全部输入框属性、label
    的 (for, 文本)、form 数量、保留记录行（姓名、邮箱、相对链接、
    初始是否 hidden、class 列表）、搜索提示行（是否存在、初始是否
    hidden、文本）、排除行（姓名、邮箱及其 class）、每段 <script>
    的完整文本与页面全部文本。
    """

    EXCLUDED_HEADING = "已排除的联系人"

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inputs = []
        self.labels = []
        self.forms = 0
        self.buttons = 0
        self.retained_rows = []
        self.excluded_rows = []
        # 搜索提示行：None 表示页面无该元素（零保留时只有固定段落）。
        self.search_empty = None
        self.texts = []
        self._in_excluded = False
        self._in_script = False
        self._region_is_retained = None
        # 当前 <tr> 的标记信息（class/id/hidden）；表头行无 td，
        # 不会据此创建记录行。
        self._tr = None
        self._row = None
        self._heading = None
        self._label_for = None
        self._label_parts = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "h2":
            self._heading = []
        elif tag == "form":
            self.forms += 1
        elif tag == "button":
            self.buttons += 1
        elif tag == "input":
            self.inputs.append(attrs_dict)
        elif tag == "label":
            self._label_for = attrs_dict.get("for")
            self._label_parts = []
        elif tag == "script":
            self._in_script = True
        elif tag == "table":
            self._region_is_retained = not self._in_excluded
        elif tag == "tr" and self._region_is_retained is not None:
            self._tr = {
                "classes": attrs_dict.get("class", "").split(),
                "id": attrs_dict.get("id"),
                "hidden": "hidden" in attrs_dict,
            }
        elif tag == "td" and self._tr is not None:
            # 表头行只有 th，不会走到这里；每行第一个 td 时按区域与
            # 行标记确定行类型一次，后续 td 只追加单元格。
            if self._row is None:
                if self._region_is_retained:
                    if RETAINED_ROW_CLASS in self._tr["classes"]:
                        self._row = {
                            "kind": "data",
                            "cells": [],
                            "href": None,
                            "hidden": self._tr["hidden"],
                            "classes": self._tr["classes"],
                        }
                    elif self._tr["id"] == SEARCH_EMPTY_ID:
                        self._row = {
                            "kind": "empty-hint",
                            "cells": [],
                            "hidden": self._tr["hidden"],
                        }
                else:
                    self._row = {
                        "kind": "excluded",
                        "cells": [],
                        "classes": self._tr["classes"],
                    }
        elif tag == "a" and self._row is not None:
            self._row["href"] = attrs_dict.get("href")

    def handle_data(self, data):
        if self._in_script:
            # 脚本是程序而非可见文本，脚本提取另由正则完成。
            return
        self.texts.append(data)
        if self._heading is not None:
            self._heading.append(data)
        if self._row is not None:
            self._row["cells"].append(data)
        if self._label_parts is not None:
            self._label_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "h2" and self._heading is not None:
            if "".join(self._heading) == self.EXCLUDED_HEADING:
                self._in_excluded = True
            self._heading = None
        elif tag == "script":
            self._in_script = False
        elif tag == "label" and self._label_parts is not None:
            self.labels.append(
                (self._label_for, "".join(self._label_parts))
            )
            self._label_for = None
            self._label_parts = None
        elif tag == "tr":
            if self._row is not None:
                row = self._row
                parts = row["cells"]
                if row["kind"] == "data":
                    self.retained_rows.append({
                        "name": parts[0],
                        "email": parts[1],
                        "href": row["href"],
                        "hidden": row["hidden"],
                        "classes": row["classes"],
                    })
                elif row["kind"] == "empty-hint":
                    self.search_empty = {
                        "hidden": row["hidden"],
                        "text": "".join(parts),
                    }
                else:
                    self.excluded_rows.append({
                        "name": parts[0],
                        "email": parts[1],
                        "classes": row["classes"],
                    })
            self._row = None
            self._tr = None
        elif tag == "table":
            self._region_is_retained = None

    def handle_startendtag(self, tag, attrs):
        # <input .../> 等自闭合写法也计入。
        self.handle_starttag(tag, attrs)

    def text(self):
        return "".join(self.texts)


# 脚本只有一段且数据岛中的尖括号已全部 U 转义，不含嵌套
# </script>，故可直接按标签正则提取其完整文本。
def extract_scripts(raw):
    return re.findall(r"<script>([\s\S]*?)</script>", raw)


def extract_fields(script):
    """从脚本文本提取嵌入的 [[姓名, 邮箱], ...] 数据岛并还原。

    数据岛是一行紧凑 JSON（ensure_ascii=False），其中 <、>、& 已被
    写成 \\u003c/\\u003e/\\u0026，引号仍是 JSON 的 \\\"，换行符等
    同理；json.loads 按 JS 字符串规则把这些转义逐字还原为原字段，
    因此返回值必须与 CSV 原始姓名、邮箱逐字相等。
    """
    match = re.search(r"^var fields = (\[.*\]);$", script, re.MULTILINE)
    assert match is not None, "脚本中缺少 var fields 数据岛"
    return json.loads(match.group(1))


def simulate(fields, query):
    """按与内联脚本逐行对应的规则计算可见性（等价转录，非另造规则）。

    query 为空：全部可见；否则 Python 子串运算 `query in 字段`
    等价于脚本的 `字段.indexOf(query) !== -1`：区分大小写、不修剪、
    不折叠空格。返回 (各行是否可见列表, 提示行是否应隐藏)。
    """
    visible = [
        query == "" or query in name or query in email
        for name, email in fields
    ]
    hint_hidden = query == "" or any(visible)
    return visible, hint_hidden


class IndexSearchTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS)
        self.template_path = self._write("template.txt", TEMPLATE)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _run(self, out_name, extra_args=(), fmt=None,
             contacts_path=None, template_path=None, segment=SEGMENT):
        out_path = os.path.join(self.tmp, out_name)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = (
            PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        )
        argv = [
            sys.executable, "-m", "newsletter_preview",
            "--contacts", contacts_path or self.contacts_path,
            "--template", template_path or self.template_path,
            "--segment", segment,
            "--out", out_path,
        ]
        if fmt is not None:
            argv.extend(["--format", fmt])
        argv.extend(extra_args)
        argv.append("--index")
        result = subprocess.run(
            argv, cwd=PROJECT_ROOT, env=env, capture_output=True
        )
        return result, out_path

    def _parse(self, out_path):
        with open(os.path.join(out_path, "index.html"), encoding="utf-8",
                  newline="") as fh:
            raw = fh.read()
        parser = _IndexSearchParser()
        parser.feed(raw)
        return raw, parser

    # -- 静态契约 -----------------------------------------------------

    def _assert_search_box_contract(self, raw, parser):
        """搜索框：恰一个无初值、无 name、未禁用的文本输入，label 关联。"""
        self.assertEqual(len(parser.inputs), 1)
        box = parser.inputs[0]
        self.assertEqual(box.get("type"), "text")
        self.assertEqual(box.get("id"), SEARCH_BOX_ID)
        # 初始为空：不带 value 属性（不能只等于空串，属性须缺席）。
        self.assertNotIn("value", box)
        # 无 name 且无 form：任何键入都不可能触发传统表单提交。
        self.assertNotIn("name", box)
        self.assertNotIn("disabled", box)
        self.assertNotIn("formaction", box)
        self.assertEqual(parser.forms, 0)
        self.assertEqual(parser.buttons, 0)
        self.assertNotIn("<form", raw)
        # label 通过 for 与输入框 id 关联，文案固定。
        self.assertIn((SEARCH_BOX_ID, SEARCH_LABEL), parser.labels)

    def _assert_script_contract(self, raw, scripts, expected_rows):
        """恰一个无属性内联脚本：直接按原文 indexOf，无归一、无网络。"""
        self.assertEqual(len(scripts), 1)
        self.assertEqual(raw.count("<script>"), 1)
        # 任何带属性的 script 标签（如 src、type）都不允许出现。
        self.assertNotIn("<script ", raw)
        script = scripts[0]

        # 直接读输入框当前值、监听 input 事件、加载时先执行一次。
        self.assertIn("box.value", script)
        self.assertIn(
            'box.addEventListener("input", applyFilter);', script
        )
        self.assertIn("applyFilter();", script)
        # 只按 retained-row 类收集行：排除区域永远不进 rows。
        self.assertIn(
            'getElementsByClassName("%s")' % RETAINED_ROW_CLASS, script
        )
        # 姓名、邮箱各做一次 indexOf 包含判断（共两次），直接比较。
        self.assertEqual(script.count(".indexOf(query)"), 2)
        self.assertIn("fields[index][0].indexOf(query)", script)
        self.assertIn("fields[index][1].indexOf(query)", script)
        # 行可见性即 hidden 布尔，不重建、不移除行；空查询全部可见。
        self.assertIn("rows[index].hidden = !show;", script)
        self.assertIn("query === ''", script)
        # 零保留时页面无提示行，脚本须容忍 getElementById 返回 null。
        self.assertIn("emptyRow !== null", script)

        # 查询原文不做任何归一：脚本文本中不得出现这些处理。
        for forbidden in (
            "trim", "toLowerCase", "toLocaleLowerCase", "normalize",
            ".replace", ".split", ".substr",
        ):
            self.assertNotIn(
                forbidden, script,
                msg=f"搜索脚本不得归一查询，但出现了 {forbidden!r}",
            )

        # 完全离线：脚本与页面都不得含可发起请求/导航的标记。
        for forbidden in (
            "src=", "http://", "https://", "fetch(", "XMLHttpRequest",
            "WebSocket", "EventSource", "navigator", "import(",
            "url(", "@import", "blob:", "data:", "mailto:",
            "window.open", "document.write", "localStorage",
        ):
            self.assertNotIn(
                forbidden, raw,
                msg=f"索引页须完全离线，但出现了 {forbidden!r}",
            )

        # 数据岛行数与表体保留行数一致。
        fields = extract_fields(script)
        self.assertEqual(len(fields), expected_rows)
        return script, fields

    def _assert_retained_rows_initial(self, parser, expected):
        """初始状态：全部保留行可见（无 hidden）、带标记类、顺序与链接。"""
        rows = parser.retained_rows
        self.assertEqual(len(rows), len(expected))
        for row, (name, email, href) in zip(rows, expected):
            self.assertEqual(row["name"], name)
            self.assertEqual(row["email"], email)
            self.assertEqual(row["href"], href)
            self.assertFalse(row["hidden"], msg="初始时保留行不得隐藏")
            self.assertIn(RETAINED_ROW_CLASS, row["classes"])
            # 同目录相对地址：不含路径分隔符。
            self.assertNotIn("/", href)

    # -- 用例 ---------------------------------------------------------

    def test_five_record_acceptance_default_text(self):
        result, out_path = self._run(
            "previews", ["--exclude-email", EMAIL_C], fmt=None
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        # 分组命中、排除、最终预览依次为 4、1、3。
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (4, 1, 3),
        )
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["index.html", "preview-0001.txt", "preview-0002.txt",
             "preview-0003.txt", "report.json"],
        )

        raw, parser = self._parse(out_path)
        self._assert_search_box_contract(raw, parser)
        scripts = extract_scripts(raw)
        script, fields = self._assert_script_contract(raw, scripts, 3)

        # 数据岛即 CSV 原字段（甲、乙共享邮箱各占一项，丙独立），
        # 顺序与行、与链接一一对应。
        self.assertEqual(
            fields,
            [["甲", EMAIL_A], ["乙", EMAIL_A], ["丙", EMAIL_B]],
        )
        self._assert_retained_rows_initial(
            parser,
            [
                ("甲", EMAIL_A, "preview-0001.txt"),
                ("乙", EMAIL_A, "preview-0002.txt"),
                ("丙", EMAIL_B, "preview-0003.txt"),
            ],
        )

        # 提示行存在且初始隐藏，文案固定。
        self.assertIsNotNone(parser.search_empty)
        self.assertTrue(parser.search_empty["hidden"])
        self.assertEqual(
            parser.search_empty["text"], SEARCH_EMPTY_MESSAGE
        )

        # 三个计数始终是完整生成结果，不随搜索变化。
        self.assertIn("分组命中：4；排除：1；最终预览：3", parser.text())

        # 排除区域只有丁，其行不带搜索标记类，永不参与过滤。
        self.assertEqual(
            [(r["name"], r["email"]) for r in parser.excluded_rows],
            [("丁", EMAIL_C)],
        )
        for row in parser.excluded_rows:
            self.assertNotIn(RETAINED_ROW_CLASS, row["classes"])
        self.assertNotIn("戊", parser.text())

        # 用户验收的完整查询序列。(查询, 期望各行可见性)。
        cases = [
            ("", [True, True, True]),       # 初始/清空：三行
            ("a@", [True, True, False]),    # 甲、乙（共享邮箱独立行）
            ("丙", [False, False, True]),   # 仅丙
            ("c@", [False, False, False]),  # 无匹配
            ("A@", [False, False, False]),  # 区分大小写：A@ 不命中
            ("b@example.invalid",
             [False, False, True]),         # 完整邮箱仍按子串命中
            ("", [True, True, True]),       # 清空恢复三行
        ]
        for query, expected_visible in cases:
            with self.subTest(query=query):
                visible, hint_hidden = simulate(fields, query)
                self.assertEqual(visible, expected_visible)
                # 非空零命中时提示行应显示；其余情况隐藏。
                if query and not any(expected_visible):
                    self.assertFalse(hint_hidden)
                    self.assertEqual(
                        SEARCH_EMPTY_MESSAGE,
                        parser.search_empty["text"]
                        if parser.search_empty else None,
                    )
                else:
                    self.assertTrue(hint_hidden)

    def test_format_html_same_search_rules(self):
        # --format html：链接改 .html，数据岛与过滤规则与 text 一致。
        result, out_path = self._run(
            "previews-html", ["--exclude-email", EMAIL_C], fmt="html"
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        raw, parser = self._parse(out_path)
        self._assert_search_box_contract(raw, parser)
        scripts = extract_scripts(raw)
        script, fields = self._assert_script_contract(raw, scripts, 3)
        self.assertEqual(
            fields,
            [["甲", EMAIL_A], ["乙", EMAIL_A], ["丙", EMAIL_B]],
        )
        self._assert_retained_rows_initial(
            parser,
            [
                ("甲", EMAIL_A, "preview-0001.html"),
                ("乙", EMAIL_A, "preview-0002.html"),
                ("丙", EMAIL_B, "preview-0003.html"),
            ],
        )
        for query, expected in (
            ("", [True, True, True]),
            ("a@", [True, True, False]),
            ("丙", [False, False, True]),
            ("c@", [False, False, False]),
        ):
            with self.subTest(query=query):
                visible, _ = simulate(fields, query)
                self.assertEqual(visible, expected)
        # 链接指向真实存在的同目录 .html 文件。
        for href in ("preview-0001.html", "preview-0002.html",
                     "preview-0003.html"):
            self.assertTrue(os.path.isfile(os.path.join(out_path, href)))

    def test_matching_is_case_sensitive_and_keeps_spaces_literal(self):
        contacts_path = self._write("contacts-spaces.csv", SPACE_CONTACTS)
        result, out_path = self._run(
            "previews-spaces", fmt=None, contacts_path=contacts_path,
            segment=SPACE_SEGMENT,
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        raw, parser = self._parse(out_path)
        scripts = extract_scripts(raw)
        _, fields = self._assert_script_contract(raw, scripts, 2)

        # 数据岛还原后与 CSV 原字段逐字相等（首尾、内部空格都在）。
        self.assertEqual(
            fields,
            [[SPACE_NAME, SPACE_EMAIL], ["李四", "b@x.invalid"]],
        )
        self._assert_retained_rows_initial(
            parser,
            [
                (SPACE_NAME, SPACE_EMAIL, "preview-0001.txt"),
                ("李四", "b@x.invalid", "preview-0002.txt"),
            ],
        )
        # 空格只按原 U+0020 保留，不替换为可见标记。
        for marker in ("&nbsp;", "&#160;", "&#xA0;", "&#32;", "&#x20;"):
            self.assertNotIn(marker, raw)

        # 正反用例：这些负例只有在“修剪/折叠/归一空格或大小写”时才
        # 会误判为命中。
        cases = [
            ("", [True, True]),
            ("张  三", [True, False]),    # 内部两空格：命中
            ("张 三", [False, False]),    # 一个空格：不折叠，零命中
            ("三 ", [True, False]),       # 查询尾空格须保留
            (" 李四 ", [False, False]),   # 修剪首尾才会命中李四
            (" A@x.invalid", [True, False]),  # 邮箱首空格须保留
            ("A@x.invalid ", [False, False]),  # 多出尾空格：不修剪
            ("a@X.INVALID", [False, False]),  # 大小写均不匹配
            ("李", [False, True]),
        ]
        for query, expected in cases:
            with self.subTest(query=query):
                visible, hint_hidden = simulate(fields, query)
                self.assertEqual(visible, expected)
                self.assertEqual(
                    hint_hidden, query == "" or any(expected)
                )

    def test_angle_brackets_quotes_ampersand_braces_are_literal(self):
        contacts_path = self._write("contacts-special.csv", SPECIAL_CONTACTS)
        result, out_path = self._run(
            "previews-special", fmt=None, contacts_path=contacts_path,
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        raw, parser = self._parse(out_path)
        scripts = extract_scripts(raw)
        script, fields = self._assert_script_contract(raw, scripts, 1)

        # 还原后姓名与 CSV 原文逐字相等：尖括号、&、引号、{{name}}
        # 都是普通文字。
        self.assertEqual(fields, [[SPECIAL_NAME, "q@example.invalid"]])

        # 脚本原始字节中数据岛部分不得出现裸尖括号或 &：<、>、& 全部
        # U 转义，引号是 JSON 的 \"；任何字段都不能终止脚本块。
        payload_line = next(
            line for line in script.splitlines()
            if line.startswith("var fields = ")
        )
        self.assertIn("\\u003cx\\u003e", payload_line)
        self.assertIn("\\u0026", payload_line)
        self.assertNotIn("<x>", script)
        self.assertNotIn("</script", script)
        # 可见单元格仍按 HTML 规则转义显示。
        self.assertIn("&lt;x&gt;&amp;&quot;{{name}}", raw)

        # 各类特殊查询都按普通子串匹配。
        for query, expected in (
            ("", [True]),
            ("<x>", [True]),
            ("&", [True]),
            ('"', [True]),
            ("{{name}}", [True]),
            ("</script>", [False]),
            ("<X>", [False]),          # 区分大小写
            ("q@example.invalid", [True]),
        ):
            with self.subTest(query=query):
                visible, _ = simulate(fields, query)
                self.assertEqual(visible, expected)

    def test_zero_segment_match_search_box_still_usable(self):
        # 零命中：没有保留表格，固定显示“没有可预览的联系人”，但
        # 搜索框仍在、可输入；脚本仍内联且数据岛为空、容忍空提示行。
        result, out_path = self._run(
            "previews-zero", segment="不存在的分组"
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        raw, parser = self._parse(out_path)
        self._assert_search_box_contract(raw, parser)
        self.assertIn(NO_PREVIEW_MESSAGE, parser.text())
        self.assertIn(NO_EXCLUDED_MESSAGE, parser.text())
        # 无保留行、无表内搜索提示行（没有表格）。
        self.assertEqual(parser.retained_rows, [])
        self.assertIsNone(parser.search_empty)
        scripts = extract_scripts(raw)
        script, fields = self._assert_script_contract(raw, scripts, 0)
        self.assertEqual(fields, [])
        self.assertIn("var fields = [];", script)
        # 空数据岛上模拟任意查询：不产生可见行，也不依赖提示行存在。
        for query in ("", "甲", "a@", "<", " "):
            visible, _ = simulate(fields, query)
            self.assertEqual(visible, [])

    def test_all_excluded_search_box_stays_usable_region_untouched(self):
        # 全部排除：保留区域固定空状态与可输入搜索框；排除区域四条
        # 全在且均不带搜索标记类；任何搜索都不改变排除条目。
        result, out_path = self._run(
            "previews-none",
            ["--exclude-email", EMAIL_A, "--exclude-email", EMAIL_B,
             "--exclude-email", EMAIL_C],
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        raw, parser = self._parse(out_path)
        self._assert_search_box_contract(raw, parser)
        scripts = extract_scripts(raw)
        script, fields = self._assert_script_contract(raw, scripts, 0)
        self.assertEqual(fields, [])
        self.assertIn(NO_PREVIEW_MESSAGE, parser.text())
        self.assertIsNone(parser.search_empty)
        self.assertEqual(parser.retained_rows, [])
        # 排除区域按 CSV 顺序列甲、乙、丙、丁（戊属 archive 不出现），
        # 行均无 retained-row 类：脚本永不收集它们。
        self.assertEqual(
            [(r["name"], r["email"]) for r in parser.excluded_rows],
            [
                ("甲", EMAIL_A), ("乙", EMAIL_A),
                ("丙", EMAIL_B), ("丁", EMAIL_C),
            ],
        )
        for row in parser.excluded_rows:
            self.assertEqual(row["classes"], [])
        self.assertIn("分组命中：4；排除：4；最终预览：0", parser.text())

    def test_search_does_not_affect_counts_or_excluded_region(self):
        # 同一页面对所有查询，计数文本与排除条目恒为完整结果；搜索
        # 只切 hidden，三个保留行始终都在 DOM（静态页面中全部存在）。
        result, out_path = self._run(
            "previews-const", ["--exclude-email", EMAIL_C]
        )
        self.assertEqual(result.returncode, 0)
        raw, parser = self._parse(out_path)
        scripts = extract_scripts(raw)
        _, fields = self._assert_script_contract(raw, scripts, 3)
        count_text = "分组命中：4；排除：1；最终预览：3"
        excluded = [("丁", EMAIL_C)]
        for query in ("", "a@", "丙", "c@", "甲", "不存在", "<", " "):
            with self.subTest(query=query):
                simulate(fields, query)  # 仅计算可见性
                # 静态页面是生成时定型的完整结果：无论查询是什么，
                # 计数、三行与排除区域都原样在页面中。
                self.assertIn(count_text, parser.text())
                self.assertEqual(
                    [(r["name"], r["email"])
                     for r in parser.excluded_rows],
                    excluded,
                )
                self.assertEqual(len(parser.retained_rows), 3)


if __name__ == "__main__":
    unittest.main()
