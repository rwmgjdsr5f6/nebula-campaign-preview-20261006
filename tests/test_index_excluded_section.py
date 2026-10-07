"""newsletter_preview --index 索引页“已排除的联系人”区域的回归测试。

覆盖排除区域的公开约定：开启 --index 时，index.html 在保留联系人
清单之后新增“已排除的联系人”区域，内容与顺序和 report.json 的
excluded_contacts 一致（条目数等于 excluded_count），逐条以纯文字
显示被 --exclude-email 移除记录的原始姓名与邮箱，不含预览或邮件
链接；仅收录命中分组后被移除的记录，未命中分组的记录不出现；共享
邮箱的记录各列一项，重复排除值不重复增加条目。默认 text 格式与
--format html 的索引页都有该区域。无排除记录时显示“没有被排除的
联系人”；全部排除时仍显示全部排除条目，并保留“没有可预览的
联系人”提示与三个计数，页面无预览链接；零命中时两个空状态同时
出现。姓名与邮箱保留大小写、首尾空白，中文、&、尖括号、引号及
{{name}} 样式文字经 HTML 转义后按字面显示。逐人预览与 report.json
在同输入、同格式下保持原有字节内容，省略 --index 的输出不变。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出/错误与落盘文件内容；不直接调用内部函数。
每个样例使用独立临时目录，结束后自动清理。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 验收固定样例：newsletter 分组的甲&乙、丙<丁>、戊与 archive 分组的
# 己；甲&乙、丙<丁>、己共用 cut@example.invalid，戊用
# keep@example.invalid。模板以单个 LF 结尾。
CONTACTS = (
    "name,email,segment\n"
    "甲&乙,cut@example.invalid,newsletter\n"
    "丙<丁>,cut@example.invalid,newsletter\n"
    "戊,keep@example.invalid,newsletter\n"
    "己,cut@example.invalid,archive\n"
)
TEMPLATE = "你好，{{name}}！\n"
SEGMENT = "newsletter"
EMAIL_CUT = "cut@example.invalid"
EMAIL_KEEP = "keep@example.invalid"


class _IndexParser(HTMLParser):
    """按浏览器规则解析索引页，收集单元格文本、列表项、链接与全文。

    convert_charrefs=True 使数字/命名实体按解析结果还原，因此还原后
    的文本与浏览器显示一致：&、尖括号、引号样式文字应作为字面数据
    出现，不成标签或实体。items 按文档顺序收集 li 文本，即“已排除
    的联系人”区域的逐条内容。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells = []
        self.items = []
        self.links = []
        self.texts = []
        self._cell = None
        self._item = None

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            self._cell = []
        elif tag == "li":
            self._item = []
        elif tag == "a":
            attrs_dict = dict(attrs)
            self.links.append(attrs_dict.get("href"))

    def handle_data(self, data):
        self.texts.append(data)
        if self._cell is not None:
            self._cell.append(data)
        if self._item is not None:
            self._item.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self.cells.append("".join(self._cell))
            self._cell = None
        elif tag == "li" and self._item is not None:
            self.items.append("".join(self._item))
            self._item = None

    def text(self):
        return "".join(self.texts)


class IndexExcludedSectionTestCase(unittest.TestCase):
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

    def _run(self, out_path, extra_args=(), fmt="text", index=True,
             contacts_path=None, template_path=None, segment=SEGMENT):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        argv = [
            sys.executable,
            "-m",
            "newsletter_preview",
            "--contacts",
            contacts_path or self.contacts_path,
            "--template",
            template_path or self.template_path,
            "--segment",
            segment,
            "--out",
            out_path,
            "--format",
            fmt,
        ]
        argv.extend(extra_args)
        if index:
            argv.append("--index")
        return subprocess.run(
            argv,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _read(self, out_path, name):
        with open(os.path.join(out_path, name), encoding="utf-8",
                  newline="") as fh:
            return fh.read()

    def _parse_index(self, out_path):
        raw = self._read(out_path, "index.html")
        parser = _IndexParser()
        parser.feed(raw)
        return raw, parser

    def test_acceptance_excluded_section_default_text_format(self):
        # 用户验收命令：默认 text 格式、排除 cut@example.invalid、
        # --index。三个人数依次为 3、2、1；排除区域按顺序只有甲&乙
        # 和丙<丁>（未命中分组的己不出现）；保留清单只有戊及
        # preview-0001.txt 链接，正文为“你好，戊！”并保留末尾 LF。
        out_path = os.path.join(self.tmp, "previews")
        result = self._run(out_path, ["--exclude-email", EMAIL_CUT])

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["index.html", "preview-0001.txt", "report.json"],
        )

        raw, parser = self._parse_index(out_path)
        self.assertIn("分组命中：3；排除：2；最终预览：1", parser.text())

        # 保留清单只有戊，链接指向 preview-0001.txt。
        self.assertEqual(parser.cells, ["戊", EMAIL_KEEP, "预览"])
        self.assertEqual(parser.links, ["preview-0001.txt"])
        self.assertEqual(
            self._read(out_path, "preview-0001.txt"), "你好，戊！\n"
        )

        # 排除区域标题在保留清单之后，逐条为甲&乙、丙<丁>，与报告
        # excluded_contacts 同序同数；未命中分组的己不出现。
        text = parser.text()
        self.assertLess(text.index("预览索引"), text.index("已排除的联系人"))
        self.assertEqual(
            parser.items,
            [f"甲&乙（{EMAIL_CUT}）", f"丙<丁>（{EMAIL_CUT}）"],
        )
        self.assertNotIn("己", text)
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["segment_count"], 3)
        self.assertEqual(report["excluded_count"], 2)
        self.assertEqual(report["matched_count"], 1)
        self.assertEqual(
            report["excluded_contacts"],
            [
                {"name": "甲&乙", "email": EMAIL_CUT},
                {"name": "丙<丁>", "email": EMAIL_CUT},
            ],
        )

        # 排除条目中的 & 与尖括号在原始 HTML 中必须转义，不成标签。
        self.assertIn("<li>甲&amp;乙（cut@example.invalid）</li>", raw)
        self.assertIn("<li>丙&lt;丁&gt;（cut@example.invalid）</li>", raw)
        self.assertNotIn("甲&乙", raw)
        self.assertNotIn("<丁>", raw)

        # 排除条目只显示文字：整个页面只有保留清单的一个预览链接，
        # 无 mailto，无网络资源。
        self.assertEqual(raw.count("<a "), 1)
        self.assertNotIn("mailto:", raw)
        for forbidden in ("http://", "https://", "src=", "<script",
                          "<img", "<link"):
            self.assertNotIn(forbidden, raw)

    def test_excluded_section_identical_in_html_format(self):
        # --format html 的索引页有相同的排除区域；预览改 .html，
        # 其余结构与计数不变。
        out_text = os.path.join(self.tmp, "previews-text")
        out_html = os.path.join(self.tmp, "previews-html")
        result_text = self._run(out_text, ["--exclude-email", EMAIL_CUT])
        result_html = self._run(
            out_html, ["--exclude-email", EMAIL_CUT], fmt="html"
        )
        self.assertEqual(result_text.returncode, 0)
        self.assertEqual(result_html.returncode, 0)

        raw_text, parser_text = self._parse_index(out_text)
        raw_html, parser_html = self._parse_index(out_html)
        self.assertEqual(parser_text.items, parser_html.items)
        self.assertEqual(
            parser_html.items,
            [f"甲&乙（{EMAIL_CUT}）", f"丙<丁>（{EMAIL_CUT}）"],
        )
        self.assertEqual(parser_html.cells, ["戊", EMAIL_KEEP, "预览"])
        self.assertEqual(parser_html.links, ["preview-0001.html"])
        self.assertEqual(raw_html.count("<a "), 1)
        # 两处排除区域逐字节相同（链接扩展名差异只在保留清单中）。
        section_text = raw_text.split('<h2>已排除的联系人</h2>\n')[1]
        section_html = raw_html.split('<h2>已排除的联系人</h2>\n')[1]
        self.assertEqual(section_text, section_html)

    def test_no_exclusions_shows_empty_message(self):
        # 不提供 --exclude-email：排除区域显示“没有被排除的联系人”，
        # 无列表项；计数为 3、0、3。
        out_path = os.path.join(self.tmp, "previews-all")
        result = self._run(out_path)
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )

        raw, parser = self._parse_index(out_path)
        self.assertIn("分组命中：3；排除：0；最终预览：3", parser.text())
        self.assertIn("已排除的联系人", parser.text())
        self.assertIn("没有被排除的联系人", parser.text())
        self.assertEqual(parser.items, [])
        self.assertNotIn("<li>", raw)
        self.assertEqual(
            parser.links,
            ["preview-0001.txt", "preview-0002.txt", "preview-0003.txt"],
        )

    def test_all_excluded_shows_entries_and_empty_listing(self):
        # 全部排除：仍显示全部排除条目（共享邮箱各列一项），保留
        # “没有可预览的联系人”提示与三个计数，页面没有预览链接。
        out_path = os.path.join(self.tmp, "previews-none")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_CUT,
             "--exclude-email", EMAIL_KEEP],
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(sorted(os.listdir(out_path)),
                         ["index.html", "report.json"])

        raw, parser = self._parse_index(out_path)
        self.assertIn("分组命中：3；排除：3；最终预览：0", parser.text())
        self.assertIn("没有可预览的联系人", parser.text())
        self.assertEqual(
            parser.items,
            [
                f"甲&乙（{EMAIL_CUT}）",
                f"丙<丁>（{EMAIL_CUT}）",
                f"戊（{EMAIL_KEEP}）",
            ],
        )
        self.assertEqual(parser.cells, [])
        self.assertEqual(parser.links, [])
        self.assertNotIn("<a ", raw)

    def test_zero_match_shows_both_empty_states(self):
        # 零命中：保留清单与排除区域同时显示空状态提示，计数 0、0、0。
        out_path = os.path.join(self.tmp, "previews-zero")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_CUT],
            segment="no-such-segment",
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )

        _, parser = self._parse_index(out_path)
        self.assertIn("分组命中：0；排除：0；最终预览：0", parser.text())
        self.assertIn("没有可预览的联系人", parser.text())
        self.assertIn("没有被排除的联系人", parser.text())
        self.assertEqual(parser.items, [])
        self.assertEqual(parser.links, [])

    def test_duplicate_exclude_values_do_not_duplicate_entries(self):
        # 重复提供同一排除值不重复增加条目：排除区域仍各列一项。
        out_path = os.path.join(self.tmp, "previews-dup")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_CUT,
             "--exclude-email", EMAIL_CUT],
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )

        _, parser = self._parse_index(out_path)
        self.assertIn("分组命中：3；排除：2；最终预览：1", parser.text())
        self.assertEqual(
            parser.items,
            [f"甲&乙（{EMAIL_CUT}）", f"丙<丁>（{EMAIL_CUT}）"],
        )

    def test_excluded_section_preserves_case_whitespace_and_escapes(self):
        # 排除区域中的姓名与邮箱保留大小写、首尾空白；引号、&、尖括号
        # 及 {{name}} 样式文字经转义后按字面显示，不成为元素。
        contacts = (
            'name,email,segment\n'
            '"  留白""<x>&  ",Cut@Example.INVALID,newsletter\n'
            '"{{name}}",keep@example.invalid,newsletter\n'
        )
        contacts_path = self._write("contacts-special.csv", contacts)
        out_path = os.path.join(self.tmp, "previews-special")
        result = self._run(
            out_path,
            ["--exclude-email", "Cut@Example.INVALID"],
            contacts_path=contacts_path,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )

        raw, parser = self._parse_index(out_path)
        # 排除值区分大小写：仅首条记录被排除，排除区域逐字保留
        # 大小写与首尾空白。
        self.assertEqual(
            parser.items, ['  留白"<x>&  （Cut@Example.INVALID）']
        )
        self.assertIn("分组命中：2；排除：1；最终预览：1", parser.text())
        # 保留清单中的 {{name}} 姓名按字面显示，不再替换。
        self.assertEqual(parser.cells, ["{{name}}", "keep@example.invalid",
                                        "预览"])
        # 原始字节中危险字符全部转义：不成标签、不破坏属性。
        self.assertIn("&quot;", raw)
        self.assertIn("&lt;x&gt;", raw)
        self.assertIn("&amp;", raw)
        self.assertNotIn("<x>", raw)
        self.assertNotIn("mailto:", raw)

    def test_indexed_and_plain_runs_byte_identical_with_exclusions(self):
        # 有排除记录时，开启 --index 的逐人预览与 report.json 仍与
        # 未开启时逐字节一致；index.html 是唯一新增文件。
        off = os.path.join(self.tmp, "out-off")
        on = os.path.join(self.tmp, "out-on")
        result_off = self._run(
            off, ["--exclude-email", EMAIL_CUT], index=False
        )
        result_on = self._run(on, ["--exclude-email", EMAIL_CUT])
        self.assertEqual(result_off.returncode, 0)
        self.assertEqual(result_on.returncode, 0)

        off_names = set(os.listdir(off))
        on_names = set(os.listdir(on))
        self.assertNotIn("index.html", off_names)
        self.assertEqual(on_names - off_names, {"index.html"})
        for name in off_names:
            with open(os.path.join(off, name), "rb") as fh:
                off_bytes = fh.read()
            with open(os.path.join(on, name), "rb") as fh:
                on_bytes = fh.read()
            self.assertEqual(off_bytes, on_bytes,
                             msg=f"{name} 因 --index 发生字节变化")


if __name__ == "__main__":
    unittest.main()
