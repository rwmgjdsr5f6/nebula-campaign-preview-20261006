"""newsletter_preview --index 保留清单搜索框的回归测试。

在既有 --index 索引页约定之上新增的搜索功能（生成入口与参数完全
不变，打开 index.html 即可离线使用）：保留清单前固定有一个初始为
空的搜索框，初始显示全部保留记录；每次输入立即更新可见行，姓名或
邮箱任一原始字段“包含整个查询字符串”即显示；匹配区分大小写，不
修剪首尾空白、不折叠连续空格；尖括号、引号、& 与 {{name}} 样式
文字均按普通文字参与匹配，任意查询合法。搜索只改变保留行可见性：
CSV 顺序、同目录相对预览链接不变，三个计数始终表示完整生成结果，
“已排除的联系人”区域不变；有保留行但零匹配时显示“没有符合搜索
条件的联系人”，清空恢复全部行；零命中或全部排除时搜索框仍可输入，
保留区域始终只显示“没有可预览的联系人”，永不显示搜索零匹配提示。
默认 text 与 --format html 规则一致；搜索不使用 <script> 元素、
不写文件、不请求网络；逐人预览与 report.json 内容不变，省略
--index 时所有产物不变。

测试仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱
使用 RFC 2606 保留的 example.invalid 虚构域名。环境无浏览器可用，
故按两层静态核对搜索约定：(1) 用 html.parser 按浏览器实体规则解析
索引页，核对搜索框、隐藏的零匹配提示、带固定 id 的保留表格及各行
姓名/邮箱/链接（解析后文本即浏览器 textContent 与用户所见）；
(2) oninput 内联处理函数解码后的全文与固定常量逐字相等（任何改动
——包括括号不配对这类语法错误——都会被抓住），并以与该函数完全
相同的“整串包含、区分大小写、不修剪不折叠”规则在解析出的原始
单元格文本上模拟各验收查询的可见行集合。

从项目根目录执行：

    python -m unittest discover -s tests
"""

import html
import json
import os
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 用户验收固定样例：甲、乙共用 a@example.invalid，丙用 b@，丁用 c@，
# 前四人同属 newsletter；戊用 d@，属 archive。
CONTACTS = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,a@example.invalid,newsletter\n"
    "丙,b@example.invalid,newsletter\n"
    "丁,c@example.invalid,newsletter\n"
    "戊,d@example.invalid,archive\n"
)
TEMPLATE = "你好，{{name}}！\n"
SEGMENT = "newsletter"
EXCLUDE_EMAIL = "c@example.invalid"

SEARCH_INPUT_ID = "contact-search"
RETAINED_TABLE_ID = "retained-contacts"
NO_MATCH_ID = "contact-search-no-match"
NO_MATCH_MESSAGE = "没有符合搜索条件的联系人"
NO_RETAINED_MESSAGE = "没有可预览的联系人"

# oninput 解码后的固定处理函数全文：只读取保留表前两列（姓名、邮箱）
# 的 textContent，对未加工的查询原文做 indexOf 整串包含匹配（区分大小
# 写、不修剪、不折叠），逐行切换 display，并按是否有可见行切换零匹配
# 提示；t 为空（零保留，无表格）时提示恒为隐藏。无任何数据拼接、无
# 网络或文件操作。
EXPECTED_HANDLER = (
    "var q=this.value;"
    "var t=document.getElementById('retained-contacts');"
    "var n=document.getElementById('contact-search-no-match');"
    "var shown=0;"
    "if(t){var rs=t.tBodies[0].rows;"
    "for(var i=0;i<rs.length;i++){"
    "var cs=rs[i].cells;"
    "var ok=q.length===0||cs[0].textContent.indexOf(q)>=0"
    "||cs[1].textContent.indexOf(q)>=0;"
    "rs[i].style.display=ok?'':'none';"
    "if(ok)shown++;}}"
    "n.style.display=(!t||shown)?'none':'';"
)


class _IndexSearchParser(HTMLParser):
    """收集搜索框属性、各段落 id/样式、两个表格的 id 与单元格文本。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.search_input_attrs = None
        self.handler = None
        self.paragraphs = []  # (id, style, text)
        self.table_ids = []
        # 按区域收集扁平单元格文本：带保留表 id 的进 retained，其余
        # <table>（“已排除的联系人”）进 excluded；空状态是段落而非表格。
        self.retained_cells = []
        self.excluded_cells = []
        self._current_cells = None
        self._row_cells = None
        self._cell = None
        self._p = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "input" and attrs_dict.get("id") == SEARCH_INPUT_ID:
            self.search_input_attrs = attrs_dict
            # 属性值已由解析器按实体规则解码，即浏览器中的处理函数原文。
            self.handler = attrs_dict.get("oninput")
        elif tag == "p":
            self._p = [attrs_dict.get("id"),
                       attrs_dict.get("style"), []]
        elif tag == "table":
            table_id = attrs_dict.get("id")
            self.table_ids.append(table_id)
            self._current_cells = (
                self.retained_cells
                if table_id == RETAINED_TABLE_ID
                else self.excluded_cells
            )
        elif tag == "tr" and self._current_cells is not None:
            self._row_cells = []
        elif tag == "td" and self._row_cells is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._p is not None:
            self._p[2].append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self._row_cells.append("".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row_cells is not None:
            self._current_cells.extend(self._row_cells)
            self._row_cells = None
        elif tag == "table":
            self._current_cells = None
        elif tag == "p" and self._p is not None:
            self.paragraphs.append(
                (self._p[0], self._p[1], "".join(self._p[2]))
            )
            self._p = None

    def _rows_of(self, cells, width):
        return [cells[i:i + width] for i in range(0, len(cells), width)]

    def retained_rows(self):
        """保留表记录行：每行 [姓名, 邮箱, “预览”]。"""
        return self._rows_of(self.retained_cells, 3)

    def excluded_rows(self):
        """排除表记录行：每行 [姓名, 邮箱]。"""
        return self._rows_of(self.excluded_cells, 2)


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
        result = subprocess.run(argv, cwd=PROJECT_ROOT, env=env,
                                capture_output=True)
        return out_path, result

    def _index(self, out_path):
        with open(os.path.join(out_path, "index.html"), encoding="utf-8",
                  newline="") as fh:
            raw = fh.read()
        parser = _IndexSearchParser()
        parser.feed(raw)
        return raw, parser

    def _assert_search_box(self, parser, retained_present):
        # 搜索框始终存在、初始为空（无 value）、type=search、带 label
        # 关联的固定 id，并内联 oninput；零保留时仍可输入（无 disabled）。
        attrs = parser.search_input_attrs
        self.assertIsNotNone(attrs)
        self.assertEqual(attrs.get("type"), "search")
        self.assertEqual(attrs.get("id"), SEARCH_INPUT_ID)
        self.assertNotIn("value", attrs)
        self.assertNotIn("disabled", attrs)
        self.assertIn("oninput", attrs)
        # 解码后的处理函数与固定实现逐字相等（括号配对、分支与空表守卫
        # 都钉死在常量里）。
        self.assertEqual(parser.handler, EXPECTED_HANDLER)
        # 零匹配提示是独立段落，带固定 id 且初始隐藏；其文案固定。
        no_match = [p for p in parser.paragraphs if p[0] == NO_MATCH_ID]
        self.assertEqual(len(no_match), 1)
        _, style, text = no_match[0]
        self.assertEqual(text, NO_MATCH_MESSAGE)
        self.assertEqual(style, "display:none;")
        # 保留表格 id 只在确有保留记录时出现。
        self.assertEqual(
            RETAINED_TABLE_ID in parser.table_ids, retained_present
        )

    def _simulate(self, parser, query):
        """按 EXPECTED_HANDLER 的同一规则在解析出的姓名/邮箱上过滤。

        空查询显示全部；否则姓名或邮箱任一字段以未加工原文 indexOf
        整个查询串（Python 的 in 与 JS String.prototype.indexOf 同为
        区分大小写、不修剪、不折叠的逐码元子串匹配）即可见。
        """
        rows = parser.retained_rows()
        visible = []
        for row in rows:
            name, email = row[0], row[1]
            ok = query == "" or query in name or query in email
            if ok:
                visible.append((name, email))
        return visible

    def test_acceptance_five_records_counts_and_initial_view(self):
        # 用户验收命令：默认 text 格式，排除 c@example.invalid，--index。
        out_path, result = self._run(
            "previews", ["--exclude-email", EXCLUDE_EMAIL]
        )
        self.assertEqual(
            result.returncode, 0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["index.html", "preview-0001.txt", "preview-0002.txt",
             "preview-0003.txt", "report.json"],
        )
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        # 分组命中、排除、最终预览依次为 4、1、3。
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (4, 1, 3),
        )

        raw, parser = self._index(out_path)
        self.assertTrue(raw.startswith("<!DOCTYPE html>"))
        self.assertIn('<meta charset="utf-8">', raw)
        self._assert_search_box(parser, retained_present=True)

        # 三个计数始终表示完整生成结果（搜索只改可见性，计数为静态文本）。
        self.assertIn("分组命中：4；排除：1；最终预览：3", raw)

        # 初始显示甲、乙、丙（CSV 顺序）及各自同目录相对预览链接；
        # 共享邮箱 a@ 是两条独立条目。
        self.assertEqual(
            parser.retained_rows(),
            [
                ["甲", "a@example.invalid", "预览"],
                ["乙", "a@example.invalid", "预览"],
                ["丙", "b@example.invalid", "预览"],
            ],
        )

        # 初始空查询等价：模拟过滤返回全部三行。
        self.assertEqual(
            self._simulate(parser, ""),
            [("甲", "a@example.invalid"),
             ("乙", "a@example.invalid"),
             ("丙", "b@example.invalid")],
        )

    def test_query_visibility_acceptance_sequence(self):
        # 同一页面上依次施加验收查询：a@ → 甲、乙；丙 → 仅丙；
        # c@ → 零匹配；清空 → 恢复三行。
        out_path, result = self._run(
            "previews-q", ["--exclude-email", EXCLUDE_EMAIL]
        )
        self.assertEqual(result.returncode, 0)
        _, parser = self._index(out_path)

        self.assertEqual(
            self._simulate(parser, "a@"),
            [("甲", "a@example.invalid"),
             ("乙", "a@example.invalid")],
        )
        self.assertEqual(
            self._simulate(parser, "丙"),
            [("丙", "b@example.invalid")],
        )
        # 丁（c@）已被排除，不在保留表中，故 c@ 无任何匹配。
        self.assertEqual(self._simulate(parser, "c@"), [])
        # 清空查询恢复三行，顺序不变。
        self.assertEqual(
            self._simulate(parser, ""),
            [("甲", "a@example.invalid"),
             ("乙", "a@example.invalid"),
             ("丙", "b@example.invalid")],
        )

        # 零匹配时处理函数会显示提示：有保留行而 shown 为 0；至少一行
        # 可见（含空查询）时提示保持隐藏——该分支钉在 EXPECTED_HANDLER
        # 中（(!t||shown)?'none':''），这里用模拟可见数复核。
        self.assertEqual(len(self._simulate(parser, "c@")), 0)
        self.assertEqual(len(self._simulate(parser, "a@")), 2)

    def test_matching_is_case_sensitive_and_keeps_spaces(self):
        out_path, result = self._run(
            "previews-case", ["--exclude-email", EXCLUDE_EMAIL]
        )
        self.assertEqual(result.returncode, 0)
        _, parser = self._index(out_path)

        # 大写不命中（邮箱原文为小写）。
        self.assertEqual(self._simulate(parser, "A@"), [])
        self.assertEqual(self._simulate(parser, "A@EXAMPLE.INVALID"), [])
        # 首尾空格不修剪：不存在以空格开头的字段。
        self.assertEqual(self._simulate(parser, " a@"), [])
        self.assertEqual(self._simulate(parser, "a@ "), [])
        # 整串必须连续包含：折叠/拆开的形态不命中。
        self.assertEqual(self._simulate(parser, "a @"), [])
        # 中文姓名按字面、整串包含。
        self.assertEqual(len(self._simulate(parser, "example.invalid")), 3)

    def test_special_characters_in_query_are_literal(self):
        # 姓名含双引号、尖括号、&；另一姓名就是 {{name}} 样式文字。
        # 页面转义保证它们不成标签/属性；处理函数只读 textContent，
        # 故这些字符作为普通文字参与整串包含匹配。
        contacts = (
            'name,email,segment\n'
            '"陈""明<x>&\x27",q@example.invalid,newsletter\n'
            '{{name}},r@example.invalid,newsletter\n'
        )
        contacts_path = self._write("contacts-special.csv", contacts)
        out_path, result = self._run(
            "previews-special", contacts_path=contacts_path
        )
        self.assertEqual(result.returncode, 0)
        raw, parser = self._index(out_path)

        # 原始字节中危险字符全部转义、不成真实标签或属性；无脚本元素、
        # 无网络引用。
        for forbidden in ("<x>", "<script", "src=", "http://", "https://",
                          "<img", "<link", "mailto:"):
            self.assertNotIn(forbidden, raw)
        self.assertIn("&lt;x&gt;", raw)
        self.assertIn("&amp;", raw)
        self.assertIn("&quot;", raw)

        # 处理函数本身不含任何联系人数据（只含固定 id 与 DOM 操作）。
        self.assertNotIn("陈", EXPECTED_HANDLER)
        self.assertNotIn("q@example.invalid", EXPECTED_HANDLER)

        # 按解析后的原始字段文本匹配：尖括号、花括号、引号、& 均字面。
        self.assertEqual(
            self._simulate(parser, "<x>"),
            [("陈\"明<x>&'", "q@example.invalid")],
        )
        self.assertEqual(
            self._simulate(parser, "{{name}}"),
            [("{{name}}", "r@example.invalid")],
        )
        self.assertEqual(
            self._simulate(parser, "&'"),
            [("陈\"明<x>&'", "q@example.invalid")],
        )

    def test_excluded_region_and_counts_unaffected_by_search(self):
        out_path, result = self._run(
            "previews-ex", ["--exclude-email", EXCLUDE_EMAIL]
        )
        self.assertEqual(result.returncode, 0)
        raw, parser = self._index(out_path)

        # 排除区域（第二个表格）不带保留表 id、无搜索控件，仍只列丁。
        self.assertEqual(parser.table_ids, [RETAINED_TABLE_ID, None])
        self.assertEqual(parser.excluded_rows(), [["丁", EXCLUDE_EMAIL]])
        # 排除区域没有搜索框/零匹配提示之外的 input。
        self.assertEqual(raw.count("<input "), 1)
        # 保留链接仍是同目录相对地址且文件真实存在。
        for name in ("preview-0001.txt", "preview-0002.txt",
                     "preview-0003.txt"):
            self.assertIn(f'href="{name}"', raw)
            self.assertTrue(os.path.isfile(os.path.join(out_path, name)))

    def test_zero_segment_match_search_box_remains_usable(self):
        # 零命中：搜索框仍在、可输入；无保留表格，保留区域始终是固定
        # 空状态；零匹配提示带隐藏样式，处理函数中的 !t 守卫保证输入
        # 任何查询都不会让它出现。
        out_path, result = self._run("previews-zero", segment="no-such-group")
        self.assertEqual(result.returncode, 0)
        raw, parser = self._index(out_path)
        self._assert_search_box(parser, retained_present=False)
        self.assertIn(NO_RETAINED_MESSAGE, raw)
        self.assertIn("没有被排除的联系人", raw)
        self.assertIn("分组命中：0；排除：0；最终预览：0", raw)
        # 钉死空表守卫：无表格时提示恒隐藏。
        self.assertIn("(!t||shown)?'none':''", EXPECTED_HANDLER)
        # 两个区域都为空：均无 <table>（空状态是段落，不是表格）。
        self.assertEqual(parser.table_ids, [])

    def test_all_excluded_search_box_remains_usable(self):
        # 全部排除（含丁的三个邮箱）：计数 4、4、0；搜索框仍可输入，
        # 保留区域始终为固定空状态，无任何预览链接；排除区域仍完整。
        out_path, result = self._run(
            "previews-all-excluded",
            ["--exclude-email", "a@example.invalid",
             "--exclude-email", "b@example.invalid",
             "--exclude-email", EXCLUDE_EMAIL],
        )
        self.assertEqual(result.returncode, 0)
        raw, parser = self._index(out_path)
        self._assert_search_box(parser, retained_present=False)
        self.assertIn(NO_RETAINED_MESSAGE, raw)
        self.assertIn("分组命中：4；排除：4；最终预览：0", raw)
        self.assertNotIn("<a ", raw)
        # 排除区域仍逐条列出甲、乙、丙、丁（CSV 顺序，共享邮箱独立）。
        self.assertEqual(
            parser.excluded_rows(),
            [
                ["甲", "a@example.invalid"],
                ["乙", "a@example.invalid"],
                ["丙", "b@example.invalid"],
                ["丁", EXCLUDE_EMAIL],
            ],
        )

    def test_html_format_same_search_rules_and_txt_unchanged(self):
        # --format html：搜索 UI 与规则一致，链接扩展名为 .html；
        # report.json 与逐人预览内容不因搜索框改变（与不带 --index 的
        # 同输入运行逐字节比较）。
        out_on, result_on = self._run(
            "previews-html-on", ["--exclude-email", EXCLUDE_EMAIL],
            fmt="html",
        )
        self.assertEqual(result_on.returncode, 0)
        raw, parser = self._index(out_on)
        self._assert_search_box(parser, retained_present=True)
        self.assertEqual(
            parser.retained_rows(),
            [
                ["甲", "a@example.invalid", "预览"],
                ["乙", "a@example.invalid", "预览"],
                ["丙", "b@example.invalid", "预览"],
            ],
        )
        for name in ("preview-0001.html", "preview-0002.html",
                     "preview-0003.html"):
            self.assertIn(f'href="{name}"', raw)
        self.assertEqual(
            self._simulate(parser, "a@"),
            [("甲", "a@example.invalid"),
             ("乙", "a@example.invalid")],
        )
        self.assertEqual(self._simulate(parser, "c@"), [])

        # 与省略 --index 的同输入运行对照：除 index.html 外逐字节一致。
        out_off_real = os.path.join(self.tmp, "previews-html-off-real")
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT
        argv = [
            sys.executable, "-m", "newsletter_preview",
            "--contacts", self.contacts_path,
            "--template", self.template_path,
            "--segment", SEGMENT,
            "--out", out_off_real,
            "--format", "html",
            "--exclude-email", EXCLUDE_EMAIL,
        ]
        result_off = subprocess.run(argv, cwd=PROJECT_ROOT, env=env,
                                    capture_output=True)
        self.assertEqual(result_off.returncode, 0)
        self.assertEqual(
            set(os.listdir(out_on)) - set(os.listdir(out_off_real)),
            {"index.html"},
        )
        for name in os.listdir(out_off_real):
            with open(os.path.join(out_on, name), "rb") as fh:
                on_bytes = fh.read()
            with open(os.path.join(out_off_real, name), "rb") as fh:
                off_bytes = fh.read()
            self.assertEqual(on_bytes, off_bytes, msg=name)

    def test_search_is_fully_offline_inline_only(self):
        # 搜索不使用脚本元素、外部 URI、事件属性以外的任何资源引用。
        out_path, result = self._run(
            "previews-offline", ["--exclude-email", EXCLUDE_EMAIL]
        )
        self.assertEqual(result.returncode, 0)
        raw, _ = self._index(out_path)
        for forbidden in ("<script", "</script", "src=", "href=\"http",
                          "http://", "https://", "url(", "@import",
                          "fetch(", "XMLHttpRequest", "mailto:"):
            self.assertNotIn(forbidden, raw)
        # 过滤逻辑只内联在搜索框的 oninput 属性中（页面仅一个 input）。
        self.assertEqual(raw.count("oninput="), 1)
        self.assertEqual(raw.count("<input "), 1)


if __name__ == "__main__":
    unittest.main()
