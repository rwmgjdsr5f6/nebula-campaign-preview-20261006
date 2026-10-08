"""--index 索引页搜索框旁“当前显示 n 条，共 m 条”计数的黑盒回归测试。

覆盖用户验收约定：开启 --index 后，index.html 在搜索框旁额外显示
“当前显示 n 条，共 m 条”——n 为当前可见保留记录数（逐条统计，
共享邮箱各算一条；表头、“搜索无结果”提示行与排除区域不计入），
m 为本次 matched_count（保留记录总数），均为实际数字。初始查询
为空，两数相同；输入变化时计数与可见行由页内内联脚本即时同步，
清空查询恢复全量；非空查询零命中时显示“当前显示 0 条，共 m 条”
并出现“没有符合搜索条件的联系人”提示行。计数只描述保留清单：
分组命中、排除、最终预览三个总数与“已排除的联系人”区域不随
查询变化。零分组命中或全部排除时计数始终为“当前显示 0 条，
共 0 条”，输入任意查询仍保留“没有可预览的联系人”空状态。
默认 text 与 --format html 计数一致，保留顺序及相对预览链接不变。
计数仅在本地浏览器更新：不写文件、不请求网络。逐人预览、
report.json 与省略 --index 时的产物逐字节一致性、索引写入失败
语义由其余测试文件保证，本文件不再重复。

环境无浏览器可用，行为核对分两层，缺一不可：
1. 静态契约（HTMLParser 按浏览器规则解析真实落盘页面）：页面恰有
   一个带固定 id 的计数元素，位于搜索框 div 内、输入框之后，初始
   文案为“当前显示 m 条，共 m 条”（m 为保留行数）；内联脚本按
   id 取得该元素，在 input 事件处理（即加载时先执行一次的
   applyFilter）内用保留行遍历累计的可见数与保留行总数改写其
   textContent——计数只覆盖带 retained-row 类的数据行，表头、
   提示行与排除区域的行不在统计集合中；脚本不含任何归一查询或
   可发起请求的标记。
2. 数据驱动模拟：从真实页面脚本中解析出嵌入的 fields 数据岛
   （JSON，json.loads 还原后须与 CSV 原字段逐字相等），按与脚本
   逐行对应的规则（query 为空全部可见；否则 Python 子串运算
   query in 字段 等价于 字段.indexOf(query) !== -1；计数文案由
   可见数与 fields 长度拼成）计算每个查询的可见性、提示行状态与
   计数文案，对用户验收的每个查询逐一断言。模拟读取的是生成物中
   的真实数据，而非测试自己另造的一份。

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

# 用户验收固定样例：甲、乙、丙、丁按此顺序同属 newsletter；甲乙共用
# shared@example.invalid，丙用 b@example.invalid，丁用
# cut@example.invalid（被 --exclude-email 排除）。
CONTACTS = (
    "name,email,segment\n"
    "甲,shared@example.invalid,newsletter\n"
    "乙,shared@example.invalid,newsletter\n"
    "丙,b@example.invalid,newsletter\n"
    "丁,cut@example.invalid,newsletter\n"
)
# 模板无末尾换行。
TEMPLATE = "你好，{{name}}！"
SEGMENT = "newsletter"
EMAIL_SHARED = "shared@example.invalid"
EMAIL_B = "b@example.invalid"
EMAIL_CUT = "cut@example.invalid"

SEARCH_BOX_ID = "contact-search"
SEARCH_COUNT_ID = "search-count"
RETAINED_ROW_CLASS = "retained-row"
SEARCH_EMPTY_ID = "search-empty-row"
SEARCH_EMPTY_MESSAGE = "没有符合搜索条件的联系人"
NO_PREVIEW_MESSAGE = "没有可预览的联系人"


def count_text(visible, total):
    """计数文案的唯一拼法：当前显示 n 条，共 m 条。"""
    return f"当前显示 {visible} 条，共 {total} 条"


class _IndexCountParser(HTMLParser):
    """按浏览器规则解析索引页中与“当前显示”计数相关的结构。

    区域划分与 test_index_search 的解析器一致：h2“已排除的联系人”
    之前的表格是保留表，之后是排除表。收集：全部带 SEARCH_COUNT_ID
    的元素（标签名、文本，按出现顺序）、搜索框 div 内各子标签顺序、
    保留数据行（姓名、邮箱、相对链接、class 列表）、搜索提示行
    （是否存在、初始是否 hidden、文本）、排除行（姓名、邮箱及
    class）、每段 <script> 的完整文本与页面全部文本。
    """

    EXCLUDED_HEADING = "已排除的联系人"

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.count_elements = []
        self.search_div_tags = []
        self.retained_rows = []
        self.excluded_rows = []
        # 搜索提示行：None 表示页面无该元素（零保留时只有固定段落）。
        self.search_empty = None
        self.texts = []
        self._in_excluded = False
        self._in_script = False
        self._in_search_div = False
        self._region_is_retained = None
        self._tr = None
        self._row = None
        self._heading = None
        self._count = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "h2":
            self._heading = []
        elif tag == "script":
            self._in_script = True
        elif tag == "div" and attrs_dict.get("class") == "contact-search":
            self._in_search_div = True
        elif tag == "table":
            self._region_is_retained = not self._in_excluded
        elif tag == "tr" and self._region_is_retained is not None:
            self._tr = {
                "classes": attrs_dict.get("class", "").split(),
                "id": attrs_dict.get("id"),
                "hidden": "hidden" in attrs_dict,
            }
        elif tag == "td" and self._tr is not None:
            if self._row is None:
                if self._region_is_retained:
                    if RETAINED_ROW_CLASS in self._tr["classes"]:
                        self._row = {"kind": "data", "cells": [],
                                     "href": None,
                                     "classes": self._tr["classes"]}
                    elif self._tr["id"] == SEARCH_EMPTY_ID:
                        self._row = {"kind": "empty-hint", "cells": [],
                                     "hidden": self._tr["hidden"]}
                else:
                    self._row = {"kind": "excluded", "cells": [],
                                 "classes": self._tr["classes"]}
        elif tag == "a" and self._row is not None:
            self._row["href"] = attrs_dict.get("href")
        if self._in_search_div and tag != "div":
            self.search_div_tags.append(tag)
        if attrs_dict.get("id") == SEARCH_COUNT_ID:
            self._count = {"tag": tag, "parts": []}

    def handle_data(self, data):
        if self._in_script:
            # 脚本是程序而非可见文本，脚本提取另由正则完成。
            return
        self.texts.append(data)
        if self._count is not None:
            self._count["parts"].append(data)
        if self._heading is not None:
            self._heading.append(data)
        if self._row is not None:
            self._row["cells"].append(data)

    def handle_endtag(self, tag):
        if tag == "h2" and self._heading is not None:
            if "".join(self._heading) == self.EXCLUDED_HEADING:
                self._in_excluded = True
            self._heading = None
        elif tag == "script":
            self._in_script = False
        elif tag == "div":
            self._in_search_div = False
        elif tag == "tr":
            if self._row is not None:
                row = self._row
                parts = row["cells"]
                if row["kind"] == "data":
                    self.retained_rows.append({
                        "name": parts[0],
                        "email": parts[1],
                        "href": row["href"],
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
        if self._count is not None and tag == self._count["tag"]:
            self.count_elements.append("".join(self._count["parts"]))
            self._count = None

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
    写成 \\u003c/\\u003e/\\u0026；json.loads 按 JS 字符串规则把
    这些转义逐字还原为原字段，因此返回值必须与 CSV 原始姓名、邮箱
    逐字相等。
    """
    match = re.search(r"^var fields = (\[.*\]);$", script, re.MULTILINE)
    assert match is not None, "脚本中缺少 var fields 数据岛"
    return json.loads(match.group(1))


def simulate(fields, query):
    """按与内联脚本逐行对应的规则计算可见性与计数（等价转录）。

    query 为空：全部可见；否则 Python 子串运算 `query in 字段`
    等价于脚本的 `字段.indexOf(query) !== -1`：区分大小写、不修剪、
    不折叠空格。计数文案由可见保留行数（n）与保留行总数（m，即
    matched_count）拼成——与脚本中 count.textContent 的拼法一致。
    返回 (各行是否可见列表, 提示行是否应隐藏, 计数文案)。
    """
    visible = [
        query == "" or query in name or query in email
        for name, email in fields
    ]
    hint_hidden = query == "" or any(visible)
    return visible, hint_hidden, count_text(sum(visible), len(fields))


class IndexSearchCountTestCase(unittest.TestCase):
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

    def _run(self, out_name, extra_args=(), fmt=None, segment=SEGMENT):
        out_path = os.path.join(self.tmp, out_name)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = (
            PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        )
        argv = [
            sys.executable, "-m", "newsletter_preview",
            "--contacts", self.contacts_path,
            "--template", self.template_path,
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
        parser = _IndexCountParser()
        parser.feed(raw)
        return raw, parser

    # -- 静态契约 -----------------------------------------------------

    def _assert_count_contract(self, raw, parser, script, expected_total):
        """计数元素：恰一个、固定 id、在搜索框 div 内输入框之后。"""
        # 恰一个计数元素，初始文案为“当前显示 m 条，共 m 条”。
        self.assertEqual(len(parser.count_elements), 1)
        self.assertEqual(
            parser.count_elements[0],
            count_text(expected_total, expected_total),
        )
        # 计数元素在搜索框 div 内、文本输入框之后（“搜索框旁”）。
        self.assertEqual(parser.search_div_tags, ["label", "input", "span"])
        # 原始字节中计数元素位于输入框之后、保留区域（表格或空状态
        # 段落）与“已排除的联系人”标题之前。
        self.assertLess(raw.index(f'id="{SEARCH_BOX_ID}"'),
                        raw.index(f'id="{SEARCH_COUNT_ID}"'))
        self.assertLess(raw.index(f'id="{SEARCH_COUNT_ID}"'),
                        raw.index("<h2>已排除的联系人</h2>"))

        # 脚本按 id 取得计数元素，并在 applyFilter 内用保留行遍历
        # 累计的可见数与保留行总数改写其 textContent。
        self.assertIn(
            f'document.getElementById("{SEARCH_COUNT_ID}")', script
        )
        self.assertIn("count.textContent =", script)
        self.assertIn('"当前显示 " + visible + " 条，共 " + rows.length',
                      script)
        # 统计集合只含带 retained-row 类的数据行：表头、提示行与
        # 排除区域的行不进 rows，不计入 n 或 m。
        self.assertIn(
            f'getElementsByClassName("{RETAINED_ROW_CLASS}")', script
        )
        # 计数随输入即时更新：applyFilter 即 input 事件处理，且加载
        # 时先执行一次（初始文案与静态渲染一致）。
        self.assertIn('box.addEventListener("input", applyFilter);',
                      script)
        self.assertIn("applyFilter();", script)

        # 查询原文不做任何归一；完全离线、不发起任何请求。
        for forbidden in (
            "trim", "toLowerCase", "toLocaleLowerCase", "normalize",
            ".replace", ".split", ".substr",
        ):
            self.assertNotIn(
                forbidden, script,
                msg=f"搜索脚本不得归一查询，但出现了 {forbidden!r}",
            )
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

    # -- 用例 ---------------------------------------------------------

    def test_acceptance_shared_email_count_default_text(self):
        # 用户验收命令：默认 text、排除 cut@example.invalid、--index。
        result, out_path = self._run(
            "previews", ["--exclude-email", EMAIL_CUT]
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
        scripts = extract_scripts(raw)
        self.assertEqual(len(scripts), 1)
        script = scripts[0]
        fields = extract_fields(script)
        # 数据岛即 CSV 原字段（甲、乙共享邮箱各占一项，丙独立）。
        self.assertEqual(
            fields,
            [["甲", EMAIL_SHARED], ["乙", EMAIL_SHARED], ["丙", EMAIL_B]],
        )
        self._assert_count_contract(raw, parser, script, 3)

        # 保留清单为甲、乙、丙（共享邮箱逐条各列一项），顺序与相对
        # 链接不变；排除区域只有丁，其行不带搜索标记类。
        self.assertEqual(
            [(r["name"], r["email"], r["href"])
             for r in parser.retained_rows],
            [
                ("甲", EMAIL_SHARED, "preview-0001.txt"),
                ("乙", EMAIL_SHARED, "preview-0002.txt"),
                ("丙", EMAIL_B, "preview-0003.txt"),
            ],
        )
        for row in parser.retained_rows:
            self.assertIn(RETAINED_ROW_CLASS, row["classes"])
            self.assertNotIn("/", row["href"])
        self.assertEqual(
            [(r["name"], r["email"]) for r in parser.excluded_rows],
            [("丁", EMAIL_CUT)],
        )
        for row in parser.excluded_rows:
            self.assertNotIn(RETAINED_ROW_CLASS, row["classes"])

        # 提示行存在且初始隐藏；三个总数恒为完整生成结果。
        self.assertIsNotNone(parser.search_empty)
        self.assertTrue(parser.search_empty["hidden"])
        self.assertEqual(
            parser.search_empty["text"], SEARCH_EMPTY_MESSAGE
        )
        self.assertIn("分组命中：4；排除：1；最终预览：3", parser.text())

        # 用户验收的完整查询序列。(查询, 各行可见性, 计数文案)。
        cases = [
            ("", [True, True, True],
             count_text(3, 3)),                 # 初始/清空：3/3
            ("shared", [True, True, False],
             count_text(2, 3)),                 # 甲乙共享邮箱：2/3
            ("zzz", [False, False, False],
             count_text(0, 3)),                 # 零命中：0/3 + 提示行
            ("", [True, True, True],
             count_text(3, 3)),                 # 清空恢复全量
            ("Shared", [False, False, False],
             count_text(0, 3)),                 # 区分大小写
            ("b@example.invalid", [False, False, True],
             count_text(1, 3)),                 # 完整邮箱按子串命中
        ]
        for query, expected_visible, expected_count in cases:
            with self.subTest(query=query):
                visible, hint_hidden, text = simulate(fields, query)
                self.assertEqual(visible, expected_visible)
                self.assertEqual(text, expected_count)
                # 非空零命中时提示行应显示；其余情况隐藏。
                if query and not any(expected_visible):
                    self.assertFalse(hint_hidden)
                else:
                    self.assertTrue(hint_hidden)
                # 三个总数与排除区域不随查询变化（静态页面即完整
                # 结果：任何查询下它们都原样在页面中）。
                self.assertIn("分组命中：4；排除：1；最终预览：3",
                              parser.text())
                self.assertEqual(len(parser.excluded_rows), 1)

    def test_format_html_same_counting(self):
        # --format html：链接改 .html，计数规则与文案和 text 一致。
        result, out_path = self._run(
            "previews-html", ["--exclude-email", EMAIL_CUT], fmt="html"
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        raw, parser = self._parse(out_path)
        scripts = extract_scripts(raw)
        self.assertEqual(len(scripts), 1)
        fields = extract_fields(scripts[0])
        self.assertEqual(
            fields,
            [["甲", EMAIL_SHARED], ["乙", EMAIL_SHARED], ["丙", EMAIL_B]],
        )
        self._assert_count_contract(raw, parser, scripts[0], 3)
        self.assertEqual(
            [r["href"] for r in parser.retained_rows],
            ["preview-0001.html", "preview-0002.html",
             "preview-0003.html"],
        )
        for query, expected_visible, expected_count in (
            ("", [True, True, True], count_text(3, 3)),
            ("shared", [True, True, False], count_text(2, 3)),
            ("zzz", [False, False, False], count_text(0, 3)),
        ):
            with self.subTest(query=query):
                visible, _, text = simulate(fields, query)
                self.assertEqual(visible, expected_visible)
                self.assertEqual(text, expected_count)
        # 链接指向真实存在的同目录 .html 文件。
        for href in ("preview-0001.html", "preview-0002.html",
                     "preview-0003.html"):
            self.assertTrue(os.path.isfile(os.path.join(out_path, href)))

    def test_zero_segment_match_count_stays_zero(self):
        # 零分组命中：计数始终“当前显示 0 条，共 0 条”，任何查询下
        # 保留区域仍只有“没有可预览的联系人”空状态。
        result, out_path = self._run(
            "previews-zero", segment="不存在的分组"
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        raw, parser = self._parse(out_path)
        scripts = extract_scripts(raw)
        self.assertEqual(len(scripts), 1)
        fields = extract_fields(scripts[0])
        self.assertEqual(fields, [])
        self._assert_count_contract(raw, parser, scripts[0], 0)
        self.assertEqual(parser.count_elements, [count_text(0, 0)])
        self.assertIn(NO_PREVIEW_MESSAGE, parser.text())
        self.assertEqual(parser.retained_rows, [])
        self.assertIsNone(parser.search_empty)
        # 空数据岛上模拟任意查询：计数恒为 0/0，空状态文案是静态
        # 页面内容，不随查询消失。
        for query in ("", "甲", "shared", "zzz", "<", " "):
            with self.subTest(query=query):
                visible, _, text = simulate(fields, query)
                self.assertEqual(visible, [])
                self.assertEqual(text, count_text(0, 0))
                self.assertIn(NO_PREVIEW_MESSAGE, parser.text())

    def test_all_excluded_count_stays_zero(self):
        # 全部排除：计数始终“当前显示 0 条，共 0 条”，保留区域固定
        # 空状态；排除区域四条全在且均不参与计数与过滤。
        result, out_path = self._run(
            "previews-none",
            ["--exclude-email", EMAIL_SHARED,
             "--exclude-email", EMAIL_B,
             "--exclude-email", EMAIL_CUT],
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        raw, parser = self._parse(out_path)
        scripts = extract_scripts(raw)
        self.assertEqual(len(scripts), 1)
        fields = extract_fields(scripts[0])
        self.assertEqual(fields, [])
        self._assert_count_contract(raw, parser, scripts[0], 0)
        self.assertEqual(parser.count_elements, [count_text(0, 0)])
        self.assertIn(NO_PREVIEW_MESSAGE, parser.text())
        self.assertIn("分组命中：4；排除：4；最终预览：0", parser.text())
        self.assertEqual(parser.retained_rows, [])
        self.assertEqual(
            [(r["name"], r["email"]) for r in parser.excluded_rows],
            [
                ("甲", EMAIL_SHARED), ("乙", EMAIL_SHARED),
                ("丙", EMAIL_B), ("丁", EMAIL_CUT),
            ],
        )
        for row in parser.excluded_rows:
            self.assertNotIn(RETAINED_ROW_CLASS, row["classes"])
        for query in ("", "甲", "shared", "zzz"):
            with self.subTest(query=query):
                visible, _, text = simulate(fields, query)
                self.assertEqual(visible, [])
                self.assertEqual(text, count_text(0, 0))
                self.assertIn(NO_PREVIEW_MESSAGE, parser.text())

    def test_count_excludes_header_hint_and_excluded_rows(self):
        # n 只逐条统计可见保留记录：表头行、“搜索无结果”提示行与
        # 排除区域的行都不带 retained-row 类，不在统计集合中。
        result, out_path = self._run(
            "previews-scope", ["--exclude-email", EMAIL_CUT]
        )
        self.assertEqual(result.returncode, 0)
        raw, parser = self._parse(out_path)
        script = extract_scripts(raw)[0]
        fields = extract_fields(script)

        # 统计集合与数据岛一一平行：长度即保留记录数 3，不多不少
        # （表头 1 行、提示 1 行、排除 1 行均不计入）。
        self.assertEqual(len(parser.retained_rows), 3)
        self.assertEqual(len(fields), 3)
        # 提示行不是数据行：无 retained-row 类、不参与计数。
        self.assertIsNotNone(parser.search_empty)
        self.assertEqual(len(parser.excluded_rows), 1)

        # 逐查询核对：n 恰为可见保留行数，m 恒为 3。
        for query, expected_visible in (
            ("", [True, True, True]),
            ("shared", [True, True, False]),
            ("丙", [False, False, True]),
            ("zzz", [False, False, False]),
        ):
            with self.subTest(query=query):
                visible, _, text = simulate(fields, query)
                self.assertEqual(visible, expected_visible)
                self.assertEqual(
                    text,
                    count_text(sum(expected_visible), 3),
                )


if __name__ == "__main__":
    unittest.main()
