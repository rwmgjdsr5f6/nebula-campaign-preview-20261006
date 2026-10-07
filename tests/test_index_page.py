"""newsletter_preview --index 索引页的回归测试。

覆盖无值参数 --index 的公开约定：开启后在输出目录额外生成
index.html，同输入、同格式下与未开启的运行逐字节一致，索引页是
唯一新增文件；页面为声明 UTF-8 的完整 HTML 文档，显示筛选值与
分组命中、排除、最终预览三个记录数（与 report.json 一致），保留
联系人清单按 CSV 顺序列出原始姓名、邮箱与相对预览链接，重复邮箱
分别列项，链接地址仅为同目录文件名，目录移动后仍可打开；其后的
“已排除的联系人”区域逐条列出原始姓名与邮箱（仅文字，无预览或
邮件链接），内容与顺序同 report.json 的 excluded_contacts，条目
数等于 excluded_count，未命中分组的记录不出现，共享邮箱各列一项，
重复排除值不重复增加条目，没有排除记录时显示“没有被排除的联系
人”；中文、&、尖括号、引号及 {{name}} 样式文字按字面显示，保留
大小写与首尾空白；页面无网络资源，邮箱仅为文字；零命中时两个空
状态同时出现，全部排除时仍列出全部排除条目、显示“没有可预览的
联系人”，无预览链接。失败语义沿用既有约定：缺列、必需字段为空、
未知变量、输入不可读退出 2 且不创建输出；输出目录非空退出 2 且
原文件保留；索引写入失败退出 2，标准错误点名文件与原因，已写
文件允许保留。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。索引写入失败用例不使用
RLIMIT、信号或权限变更：把一个标准库 sitecustomize 模块放进独立临时
目录，仅在故障运行时 prepend 到子进程 PYTHONPATH，由解释器启动时的
site 模块自动导入；该模块包装 builtins.open，仅对环境变量指定的
index.html 目标路径在创建前抛出 PermissionError，其余调用原样转发，
因此 Windows 与常见 Linux 环境行为一致，任何平台都不跳过。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出/错误、落盘文件内容与输出目录状态；不直接
调用内部函数。每个样例使用独立临时目录，结束后自动清理。
"""

import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 验收固定样例：前两人共享 shared@example.invalid，戊为
# cut@example.invalid；姓名内含 & 与尖括号。
CONTACTS = (
    "name,email,segment\n"
    "甲&乙,shared@example.invalid,newsletter\n"
    "丙<丁>,shared@example.invalid,newsletter\n"
    "戊,cut@example.invalid,newsletter\n"
)
# 模板无末尾换行。
TEMPLATE = "你好，{{name}}！"
SEGMENT = "newsletter"
EMAIL_SHARED = "shared@example.invalid"
EMAIL_CUT = "cut@example.invalid"

# 索引写入失败用例的固定合成输入：唯一数据行 甲，模板以一个 LF 结束。
SIMPLE_CONTACTS = (
    "name,email,segment\n甲,a@example.invalid,newsletter\n"
)
SIMPLE_TEMPLATE = "你好，{{name}}！\n"

# 空格保留验收的固定合成输入：CSV 引号仅标示字段边界，不属于值；
# 字段首尾与内部的普通空格 U+0020 必须按原数量在浏览器实际排版中
# 保留（不折叠、不修剪、不替换为可见标记）。两条记录同属分组
# “ news  letter ”；第二条的邮箱带首尾空格，将以同样带空格的原文
# 作为 --exclude-email 精确排除。
SPACE_CONTACTS = (
    "name,email,segment\n"
    '" 甲  &乙 "," a@example.invalid "," news  letter "\n'
    '" 丙  丁 "," b@example.invalid "," news  letter "\n'
)
SPACE_TEMPLATE = "你好，{{name}}！\n"
SPACE_SEGMENT = " news  letter "
SPACE_EXCLUDE_EMAIL = " b@example.invalid "
SPACE_NAME_KEPT = " 甲  &乙 "
SPACE_EMAIL_KEPT = " a@example.invalid "
SPACE_NAME_EXCLUDED = " 丙  丁 "
SPACE_EMAIL_EXCLUDED = " b@example.invalid "

# 注入的底层失败原因（固定），须原样出现在标准错误中。
DENY_REASON = "index-write-denied"
# 向子进程传递目标 index.html 路径的环境变量名。
DENY_PATH_ENV = "NEWSLETTER_PREVIEW_TEST_DENY_INDEX_PATH"

# 故障注入模块：仅在故障运行时通过 PYTHONPATH 进入子进程。site 模块在
# 解释器启动时自动导入它；它包装 builtins.open，仅对环境变量指定的
# 目标路径在创建前抛出 PermissionError，其余 open 调用原样转发，因此
# 输入读取、逐人预览与报告写入完全不受影响，目标文件根本不会被创建。
SITECUSTOMIZE = '''\
"""测试注入：仅对指定路径的 open 在创建前抛出 PermissionError。"""
import builtins
import os

_TARGET = os.environ.get(%r)

if _TARGET:
    _target = os.path.normcase(os.path.abspath(_TARGET))
    _real_open = builtins.open

    def _guarded_open(file, *args, **kwargs):
        try:
            path = os.fspath(file)
        except TypeError:
            path = None
        if (
            isinstance(path, str)
            and os.path.normcase(os.path.abspath(path)) == _target
        ):
            raise PermissionError(%r)
        return _real_open(file, *args, **kwargs)

    builtins.open = _guarded_open
''' % (DENY_PATH_ENV, DENY_REASON)


class _IndexParser(HTMLParser):
    """按浏览器规则解析索引页，分区域收集单元格文本、全部文本与链接。

    保留联系人清单位于“已排除的联系人”h2 之前；h2 之后的表格属于
    排除区域。convert_charrefs=True 使数字/命名实体按解析结果还原，
    因此还原后的文本与浏览器显示一致：&、尖括号、引号样式文字应作为
    字面数据出现，不成标签或实体；普通空格在解析阶段本就逐字保留
    （折叠只发生在排版阶段），其在浏览器中的保留由 style_text 内的
    white-space: pre-wrap 规则与各元素的 class 共同保证，测试两者
    一起核对。除单元格文本外，另按区域收集每个 td 是否带预排版类
    （retained/excluded_cell_classes，与对应单元格文本一一平行），
    并收集全部段落的 (class, 文本) 与 <style> 内的样式文本。
    """

    EXCLUDED_HEADING = "已排除的联系人"
    FIELD_CLASS = "field-value"

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.retained_cells = []
        self.excluded_cells = []
        self.retained_cell_classes = []
        self.excluded_cell_classes = []
        self.links = []
        self.texts = []
        self.paragraphs = []
        self.style_text = ""
        self._cell = None
        self._cell_class = None
        self._heading = None
        self._in_excluded = False
        self._current_cells = None
        self._current_classes = None
        self._paragraph = None
        self._style_parts = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "h2":
            self._heading = []
        elif tag == "table":
            if self._in_excluded:
                self._current_cells = self.excluded_cells
                self._current_classes = self.excluded_cell_classes
            else:
                self._current_cells = self.retained_cells
                self._current_classes = self.retained_cell_classes
        elif tag == "td":
            self._cell = []
            self._cell_class = attrs_dict.get("class")
        elif tag == "p":
            self._paragraph = [attrs_dict.get("class"), []]
        elif tag == "style":
            self._style_parts = []
        elif tag == "a":
            self.links.append(attrs_dict.get("href"))

    def handle_data(self, data):
        self.texts.append(data)
        if self._cell is not None:
            self._cell.append(data)
        if self._heading is not None:
            self._heading.append(data)
        if self._paragraph is not None:
            self._paragraph[1].append(data)
        if self._style_parts is not None:
            self._style_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "h2" and self._heading is not None:
            if "".join(self._heading) == self.EXCLUDED_HEADING:
                self._in_excluded = True
            self._heading = None
        elif tag == "td" and self._cell is not None:
            if self._current_cells is not None:
                self._current_cells.append("".join(self._cell))
                self._current_classes.append(
                    self._cell_class == self.FIELD_CLASS
                )
            self._cell = None
            self._cell_class = None
        elif tag == "p" and self._paragraph is not None:
            self.paragraphs.append(
                (self._paragraph[0], "".join(self._paragraph[1]))
            )
            self._paragraph = None
        elif tag == "style" and self._style_parts is not None:
            self.style_text += "".join(self._style_parts)
            self._style_parts = None
        elif tag == "table":
            self._current_cells = None
            self._current_classes = None

    def text(self):
        return "".join(self.texts)


class IndexPageTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS)
        self.template_path = self._write("template.txt", TEMPLATE)
        # 注入目录：仅故障运行把它 prepend 到子进程 PYTHONPATH。
        self.inject_dir = os.path.join(self.tmp, "inject")
        os.mkdir(self.inject_dir)
        with open(
            os.path.join(self.inject_dir, "sitecustomize.py"),
            "w",
            encoding="utf-8",
            newline="",
        ) as fh:
            fh.write(SITECUSTOMIZE)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _run(self, out_path, extra_args=(), fmt="html", index=True,
             contacts_path=None, template_path=None, segment=SEGMENT,
             deny_index=False):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。

        fmt 为 None 时省略 --format（即默认 text 格式）。deny_index 为
        True 时启用 sitecustomize 注入，令该输出目录中 index.html 的
        创建在文件创建前以 PermissionError(DENY_REASON) 失败。
        """
        env = dict(os.environ)
        # 固定子进程 stdio 编码，保证各平台下 stderr 可按 UTF-8 解码。
        env["PYTHONIOENCODING"] = "utf-8"
        pythonpath = [PROJECT_ROOT]
        if deny_index:
            pythonpath.insert(0, self.inject_dir)
            env[DENY_PATH_ENV] = os.path.join(out_path, "index.html")
        env["PYTHONPATH"] = (
            os.pathsep.join(pythonpath)
            + os.pathsep
            + env.get("PYTHONPATH", "")
        )
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
        ]
        if fmt is not None:
            argv.extend(["--format", fmt])
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

    def test_acceptance_counts_links_and_literal_text(self):
        # 用户验收命令：html 格式、排除 cut@example.invalid、--index。
        out_path = os.path.join(self.tmp, "previews")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_CUT],
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

        # 目录恰好包含两份预览、报告与索引，无其他文件。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["index.html", "preview-0001.html", "preview-0002.html",
             "report.json"],
        )

        raw, parser = self._parse_index(out_path)

        # 完整文档声明 UTF-8。
        self.assertTrue(raw.startswith("<!DOCTYPE html>"))
        self.assertIn('<meta charset="utf-8">', raw)

        # 计数依次为分组命中 3、排除 1、最终预览 2，与报告一致。
        self.assertIn("筛选值：newsletter", parser.text())
        self.assertIn("分组命中：3；排除：1；最终预览：2", parser.text())
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["segment_count"], 3)
        self.assertEqual(report["excluded_count"], 1)
        self.assertEqual(report["matched_count"], 2)

        # 保留清单按 CSV 顺序列出保留者的原始姓名与邮箱；重复邮箱分别
        # 列项，被排除的戊不在保留清单内。
        self.assertEqual(
            parser.retained_cells,
            [
                "甲&乙", EMAIL_SHARED, "预览",
                "丙<丁>", EMAIL_SHARED, "预览",
            ],
        )
        # 链接为对应文件名的相对地址。
        self.assertEqual(
            parser.links,
            ["preview-0001.html", "preview-0002.html"],
        )
        for filename in parser.links:
            self.assertTrue(os.path.isfile(os.path.join(out_path, filename)))

        # 排除区域在保留清单之后，只含被排除的戊（逐条、仅文字、
        # 无链接），条目数与报告 excluded_count 一致；空状态不出现。
        self.assertIn("已排除的联系人", parser.text())
        self.assertNotIn("没有被排除的联系人", parser.text())
        self.assertEqual(parser.excluded_cells, ["戊", EMAIL_CUT])
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            excluded_contacts = json.load(fh)["excluded_contacts"]
        self.assertEqual(
            parser.excluded_cells,
            [c for item in excluded_contacts
             for c in (item["name"], item["email"])],
        )

        # & 与尖括号在原始 HTML 中必须转义，不成标签或实体；解析后
        # 按字面还原。
        self.assertIn("甲&amp;乙", raw)
        self.assertNotIn("甲&乙", raw)
        self.assertIn("丙&lt;丁&gt;", raw)
        self.assertNotIn("<丁>", raw)

        # 邮箱仅作文字：无 mailto；页面无网络资源。
        self.assertNotIn("mailto:", raw)
        for forbidden in ("http://", "https://", "src=", "<script",
                          "<img", "<link"):
            self.assertNotIn(forbidden, raw)

    def test_default_text_acceptance_fixed_csv_excluded_region(self):
        # 用户验收固定样例（省略 --format，即默认 text）：newsletter
        # 分组的甲&乙、丙<丁>、戊与 archive 分组的己；甲&乙、丙<丁>、
        # 己共用 cut@example.invalid，戊用 keep@example.invalid；模板
        # “你好，{{name}}！”末尾一个 LF。选择 newsletter、排除
        # cut@example.invalid 并开启 --index 后：退出 0，三人数
        # 3、2、1；排除区域按顺序只有甲&乙和丙<丁>（archive 的己不
        # 出现）；保留清单只有戊及 preview-0001.txt；正文为
        # “你好，戊！”并保留末尾 LF。
        contacts = (
            "name,email,segment\n"
            "甲&乙,cut@example.invalid,newsletter\n"
            "丙<丁>,cut@example.invalid,newsletter\n"
            "戊,keep@example.invalid,newsletter\n"
            "己,cut@example.invalid,archive\n"
        )
        template = "你好，{{name}}！\n"
        contacts_path = self._write("contacts-fixed.csv", contacts)
        template_path = self._write("template-fixed.txt", template)

        # 同一排除值重复提供两遍：去重后条目不翻倍。
        out_path = os.path.join(self.tmp, "previews-fixed")
        result = self._run(
            out_path,
            ["--exclude-email", "cut@example.invalid",
             "--exclude-email", "cut@example.invalid"],
            fmt=None,
            contacts_path=contacts_path,
            template_path=template_path,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

        # 默认 text 格式：仅一份 .txt 预览、报告与索引。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["index.html", "preview-0001.txt", "report.json"],
        )

        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (3, 2, 1),
        )
        self.assertEqual(
            report["excluded_contacts"],
            [
                {"name": "甲&乙", "email": "cut@example.invalid"},
                {"name": "丙<丁>", "email": "cut@example.invalid"},
            ],
        )
        self.assertEqual(
            report["previews"],
            [{"email": "keep@example.invalid", "file": "preview-0001.txt"}],
        )

        raw, parser = self._parse_index(out_path)
        self.assertIn("分组命中：3；排除：2；最终预览：1", parser.text())
        # 保留清单只有戊及其 .txt 相对链接。
        self.assertEqual(
            parser.retained_cells,
            ["戊", "keep@example.invalid", "预览"],
        )
        self.assertEqual(parser.links, ["preview-0001.txt"])
        # 排除区域按顺序只有甲&乙和丙<丁>（己属于 archive，不出现），
        # 条数等于 excluded_count，重复排除值不翻倍。
        self.assertEqual(
            parser.excluded_cells,
            [
                "甲&乙", "cut@example.invalid",
                "丙<丁>", "cut@example.invalid",
            ],
        )
        self.assertEqual(len(parser.excluded_cells) // 2,
                         report["excluded_count"])
        self.assertNotIn("己", parser.text())
        self.assertNotIn("没有被排除的联系人", parser.text())

        # 排除条目只显示文字：排除表格内无链接，整个页面只有保留者
        # 的一个文件链接，无 mailto。
        self.assertNotIn("mailto:", raw)
        self.assertEqual(raw.count("<a "), 1)

        # 特殊字符经转义后按字面显示。
        self.assertIn("甲&amp;乙", raw)
        self.assertIn("丙&lt;丁&gt;", raw)

        # 正文替换正确且末尾 LF 逐字节保留。
        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), "你好，戊！\n".encode("utf-8"))

    def test_relative_links_survive_directory_move(self):
        # 整个输出目录移动后，索引中的相对链接仍能打开对应预览。
        out_path = os.path.join(self.tmp, "previews")
        result = self._run(out_path, ["--exclude-email", EMAIL_CUT])
        self.assertEqual(result.returncode, 0)

        moved = os.path.join(self.tmp, "relocated", "previews-copy")
        shutil.copytree(out_path, moved)

        _, parser = self._parse_index(moved)
        for href in parser.links:
            self.assertNotIn("/", href)
            self.assertTrue(
                os.path.isfile(os.path.join(moved, href)),
                msg=f"移动目录后链接失效：{href}",
            )

    def test_text_format_index_links_txt_previews(self):
        # text 格式同样可用：链接指向 .txt 文件；模板无末尾换行，
        # 预览正文逐字为“你好，甲&乙！”；排除区域两种格式一致。
        out_path = os.path.join(self.tmp, "previews-text")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_CUT],
            fmt="text",
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["index.html", "preview-0001.txt", "preview-0002.txt",
             "report.json"],
        )
        _, parser = self._parse_index(out_path)
        self.assertEqual(
            parser.links,
            ["preview-0001.txt", "preview-0002.txt"],
        )
        self.assertEqual(self._read(out_path, "preview-0001.txt"),
                         "你好，甲&乙！")
        # 默认 text 格式的索引页同样带有排除区域，内容同 html 格式。
        self.assertEqual(parser.excluded_cells, ["戊", EMAIL_CUT])

    def test_indexed_and_plain_runs_are_byte_identical_both_formats(self):
        # 开启 --index 时原有预览与 report.json 与未开启时逐字节
        # 一致；index.html 是唯一新增文件（text、html 两种格式各验）。
        for fmt in ("text", "html"):
            off = os.path.join(self.tmp, f"out-{fmt}-off")
            on = os.path.join(self.tmp, f"out-{fmt}-on")
            result_off = self._run(off, fmt=fmt, index=False)
            result_on = self._run(on, fmt=fmt, index=True)
            self.assertEqual(result_off.returncode, 0)
            self.assertEqual(result_on.returncode, 0)

            off_names = set(os.listdir(off))
            on_names = set(os.listdir(on))
            self.assertNotIn("index.html", off_names)
            self.assertEqual(on_names - off_names, {"index.html"})
            self.assertEqual(off_names, on_names - {"index.html"})
            for name in off_names:
                with open(os.path.join(off, name), "rb") as fh:
                    off_bytes = fh.read()
                with open(os.path.join(on, name), "rb") as fh:
                    on_bytes = fh.read()
                self.assertEqual(
                    off_bytes,
                    on_bytes,
                    msg=f"{fmt} 格式下 {name} 因 --index 发生字节变化",
                )

    def test_all_excluded_shows_message_and_counts_without_links(self):
        # 全部排除时退出 0，只有报告与索引；三计数为 3、3、0，
        # 保留区域显示固定提示且不含任何预览链接；排除区域仍列出
        # 全部三条排除记录（顺序同报告），不显示排除空状态。
        out_path = os.path.join(self.tmp, "previews-none")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_CUT,
             "--exclude-email", EMAIL_SHARED],
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
        self.assertIn("已排除的联系人", parser.text())
        self.assertNotIn("没有被排除的联系人", parser.text())
        self.assertEqual(parser.links, [])
        self.assertNotIn("<a ", raw)
        self.assertNotIn("preview-0001", raw)
        # 三条排除记录按 CSV 顺序各列一项（共享邮箱不去重）。
        self.assertEqual(
            parser.excluded_cells,
            [
                "甲&乙", EMAIL_SHARED,
                "丙<丁>", EMAIL_SHARED,
                "戊", EMAIL_CUT,
            ],
        )
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(len(parser.excluded_cells) // 2,
                         report["excluded_count"])

    def test_zero_segment_match_shows_message_and_counts(self):
        # 零命中同样退出 0：计数 0、0、0，保留与排除两个空状态同时
        # 出现、均无链接与条目。
        out_path = os.path.join(self.tmp, "previews-zero")
        result = self._run(out_path, segment="archive")
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(sorted(os.listdir(out_path)),
                         ["index.html", "report.json"])

        raw, parser = self._parse_index(out_path)
        self.assertIn("筛选值：archive", parser.text())
        self.assertIn("分组命中：0；排除：0；最终预览：0", parser.text())
        self.assertIn("没有可预览的联系人", parser.text())
        self.assertIn("没有被排除的联系人", parser.text())
        self.assertEqual(parser.links, [])
        self.assertEqual(parser.retained_cells, [])
        self.assertEqual(parser.excluded_cells, [])
        self.assertNotIn("<a ", raw)

    def test_special_characters_quotes_braces_and_spaces_are_literal(self):
        # 姓名含双引号、单引号、尖括号、&；分组值含同样符号、
        # {{name}} 样式文字及首尾空白；另一姓名含首尾空白。页面与
        # 报告均按字面显示，不解析为标签、实体或变量。
        special_segment = ' A&B<">{{name}} '
        contacts = (
            'name,email,segment\n'
            '"陈""明<x>&\'",q@example.invalid,'
            '" A&B<"">{{name}} "\n'
            '  留白  ,r@example.invalid,'
            '" A&B<"">{{name}} "\n'
        )
        contacts_path = self._write("contacts-special.csv", contacts)
        out_path = os.path.join(self.tmp, "previews-special")
        result = self._run(
            out_path,
            contacts_path=contacts_path,
            segment=special_segment,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )

        raw, parser = self._parse_index(out_path)
        text = parser.text()
        self.assertIn(f"筛选值：{special_segment}", text)
        self.assertIn('陈"明<x>&\'', text)
        self.assertIn("  留白  ", text)
        self.assertIn("{{name}}", text)
        # 邮箱与首尾空白逐字保留。
        self.assertIn("q@example.invalid", text)
        self.assertIn("r@example.invalid", text)

        # 原始字节中危险字符全部转义：不成标签、不破坏属性。
        self.assertIn("&amp;", raw)
        self.assertIn("&lt;", raw)
        self.assertIn("&gt;", raw)
        self.assertIn("&quot;", raw)
        # 注入的 <x> 不得成为真实元素：原文中不应出现裸的 "<x>"。
        self.assertNotIn("<x>", raw)

    def test_field_spaces_preserved_in_browser_layout_both_formats(self):
        # 空格保留验收：姓名、邮箱与筛选值的首尾及内部连续普通空格
        # U+0020 必须在浏览器实际排版中按原数量保留。默认 text 与
        # --format html 各用一个独立空输出目录，字段显示结果一致、
        # 链接扩展名随格式。环境无浏览器可用，故按浏览器渲染链静态
        # 核对两层且缺一不可：(1) HTMLParser(convert_charrefs=True)
        # 还原后的元素文本与 CSV 原文逐字相等（含首尾与连续空格、
        # 未修剪、无可见标记）；(2) 这些元素都带预排版类，且页面
        # 本地 <style> 中确有选择该类的 white-space: pre-wrap 规则
        # ——浏览器对普通空格的折叠只发生在排版阶段，正是该规则令
        # 其与 pre 一样保留空格。
        per_format = {}
        for fmt, extension in ((None, "txt"), ("html", "html")):
            with self.subTest(format=fmt or "text"):
                contacts_path = self._write(
                    f"contacts-spaces-{fmt or 'text'}.csv",
                    SPACE_CONTACTS,
                )
                template_path = self._write(
                    f"template-spaces-{fmt or 'text'}.txt",
                    SPACE_TEMPLATE,
                )
                out_path = os.path.join(
                    self.tmp, f"previews-spaces-{fmt or 'text'}"
                )
                result = self._run(
                    out_path,
                    ["--exclude-email", SPACE_EXCLUDE_EMAIL],
                    fmt=fmt,
                    contacts_path=contacts_path,
                    template_path=template_path,
                    segment=SPACE_SEGMENT,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"stderr: "
                    f"{result.stderr.decode('utf-8', 'replace')}",
                )

                # 三计数为分组命中 2、排除 1、最终预览 1；甲保留、
                # 丙排除。
                with open(os.path.join(out_path, "report.json"),
                          encoding="utf-8") as fh:
                    report = json.load(fh)
                self.assertEqual(
                    (report["segment_count"], report["excluded_count"],
                     report["matched_count"]),
                    (2, 1, 1),
                )
                self.assertEqual(
                    report["previews"],
                    [{"email": SPACE_EMAIL_KEPT,
                      "file": f"preview-0001.{extension}"}],
                )
                self.assertEqual(
                    report["excluded_contacts"],
                    [{"name": SPACE_NAME_EXCLUDED,
                      "email": SPACE_EMAIL_EXCLUDED}],
                )

                raw, parser = self._parse_index(out_path)
                per_format[fmt or "text"] = (raw, parser)

                # 样式块：选择预排版类并声明 white-space: pre-wrap，
                # 本地内联、无网络引用；这是浏览器不折叠空格的直接
                # 依据。
                style = parser.style_text
                self.assertIn(".field-value", style)
                self.assertIn("white-space", style)
                self.assertIn("pre-wrap", style)
                for forbidden in ("http://", "https://", "@import",
                                  "url("):
                    self.assertNotIn(forbidden, style)

                # 筛选值段落：带预排版类，解析文本与命令行筛选值原文
                # 完全相等（首尾各一空格、中间两空格）；计数段落维持
                # 普通排版（不带类）。
                segment_paragraphs = [
                    text for cls, text in parser.paragraphs
                    if text.startswith("筛选值：")
                ]
                self.assertEqual(
                    segment_paragraphs,
                    [f"筛选值：{SPACE_SEGMENT}"],
                )
                segment_classes = [
                    cls == _IndexParser.FIELD_CLASS
                    for cls, text in parser.paragraphs
                    if text.startswith("筛选值：")
                ]
                self.assertEqual(segment_classes, [True])
                count_paragraphs = [
                    (cls, text) for cls, text in parser.paragraphs
                    if text.startswith("分组命中")
                ]
                self.assertEqual(len(count_paragraphs), 1)
                self.assertIsNone(count_paragraphs[0][0])
                self.assertEqual(
                    count_paragraphs[0][1],
                    "分组命中：2；排除：1；最终预览：1",
                )

                # 保留清单：甲的姓名、邮箱两格文本逐字等于 CSV 原文，
                # 两格都带预排版类；第三格是不带类的“预览”链接格，
                # 链接扩展名为该格式应有的扩展名。
                self.assertEqual(
                    parser.retained_cells,
                    [SPACE_NAME_KEPT, SPACE_EMAIL_KEPT, "预览"],
                )
                self.assertEqual(
                    parser.retained_cell_classes,
                    [True, True, False],
                )
                self.assertEqual(
                    parser.links, [f"preview-0001.{extension}"]
                )

                # 排除区域只列丙（姓名、邮箱），两格文本逐字等于原文
                # 且都带预排版类，无任何链接。
                self.assertEqual(
                    parser.excluded_cells,
                    [SPACE_NAME_EXCLUDED, SPACE_EMAIL_EXCLUDED],
                )
                self.assertEqual(
                    parser.excluded_cell_classes, [True, True]
                )

                # 空格只按原 U+0020 保留：不修剪、不替换为不换行空格
                # 或任何数字字符引用；原始字节中字段边缘空格仍在
                # （& 等字符已按既有规则 HTML 转义，故按转义后形态
                # 核对原文，空格本身不参与转义）。
                for field in (SPACE_NAME_KEPT, SPACE_EMAIL_KEPT,
                              SPACE_NAME_EXCLUDED, SPACE_EMAIL_EXCLUDED):
                    self.assertIn(html.escape(field, quote=True), raw)
                self.assertIn(">筛选值： news  letter <", raw)
                for marker in ("&nbsp;", "&#160;", "&#xA0;", "&#32;",
                               "&#x20;"):
                    self.assertNotIn(marker, raw)
                # 中文字面显示、& 仍按 HTML 转义为 &amp;。
                self.assertIn(" 甲  &amp;乙 ", raw)

                # 页面仍仅用本地相对预览链接、无网络资源。
                self.assertNotIn("mailto:", raw)
                for forbidden in ("http://", "https://", "src=",
                                  "<script", "<img", "<link"):
                    self.assertNotIn(forbidden, raw)

                # 相对链接指向真实存在的同目录预览文件。
                for href in parser.links:
                    self.assertNotIn("/", href)
                    self.assertTrue(
                        os.path.isfile(os.path.join(out_path, href))
                    )

        # 两种格式的索引页字段显示（解析后文本、类标记与样式）一致。
        raw_text, parser_text = per_format["text"]
        raw_html, parser_html = per_format["html"]
        self.assertEqual(
            parser_text.retained_cells, parser_html.retained_cells
        )
        self.assertEqual(
            parser_text.excluded_cells, parser_html.excluded_cells
        )
        self.assertEqual(
            parser_text.style_text, parser_html.style_text
        )
        self.assertIn('href="preview-0001.txt"', raw_text)
        self.assertIn('href="preview-0001.html"', raw_html)

    def _assert_failure_leaves_no_output(self, result, out_absent,
                                         out_empty, fragments):
        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        stderr = result.stderr.decode("utf-8")
        for fragment in fragments:
            self.assertIn(fragment, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)

    def test_input_failures_with_index_create_nothing(self):
        # 缺列、必需字段为空、未知变量、输入不可读：在 --index 下
        # 仍退出 2，不创建新目录，已有空目录仍为空。
        cases = [
            (
                "missing-column",
                "name,email\n甲,a@example.invalid\n",
                TEMPLATE,
                ["缺少", "列", "segment"],
                SEGMENT,
            ),
            (
                "blank-field",
                CONTACTS.replace("戊,", "  ,", 1),
                TEMPLATE,
                ["为空或仅含空白", "name"],
                SEGMENT,
            ),
            (
                "unknown-variable",
                CONTACTS,
                "你好，{{name}}！{{age}}",
                ["未知变量", "age"],
                SEGMENT,
            ),
        ]
        for label, contacts, template, fragments, segment in cases:
            with self.subTest(label):
                contacts_path = self._write(f"{label}.csv", contacts)
                template_path = self._write(f"{label}.txt", template)
                out_absent = os.path.join(self.tmp, f"out-{label}-absent")
                result = self._run(
                    out_absent,
                    ["--exclude-email", EMAIL_CUT],
                    contacts_path=contacts_path,
                    template_path=template_path,
                    segment=segment,
                )
                self._assert_failure_leaves_no_output(
                    result, out_absent, None, fragments
                )
                self.assertFalse(os.path.exists(out_absent))

                out_empty = os.path.join(self.tmp, f"out-{label}-empty")
                os.mkdir(out_empty)
                result = self._run(
                    out_empty,
                    ["--exclude-email", EMAIL_CUT],
                    contacts_path=contacts_path,
                    template_path=template_path,
                    segment=segment,
                )
                self._assert_failure_leaves_no_output(
                    result, None, out_empty, fragments
                )
                self.assertEqual(os.listdir(out_empty), [])

        # 联系人文件不存在：同样退出 2，无输出。
        out_absent = os.path.join(self.tmp, "out-missing-input")
        result = self._run(
            out_absent,
            contacts_path=os.path.join(self.tmp, "no-such.csv"),
        )
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("无法读取", stderr)
        self.assertIn("no-such.csv", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))

    def test_nonempty_output_dir_rejected_and_untouched_with_index(self):
        # 输出目录非空：退出 2，原文件原样保留，不新增任何产物。
        out_path = os.path.join(self.tmp, "previews-keep")
        os.mkdir(out_path)
        keep_path = os.path.join(out_path, "keep.txt")
        keep_bytes = "原有内容，请勿改动\n".encode("utf-8")
        with open(keep_path, "wb") as fh:
            fh.write(keep_bytes)

        result = self._run(out_path, ["--exclude-email", EMAIL_CUT])

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("输出目录非空", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertEqual(os.listdir(out_path), ["keep.txt"])
        with open(keep_path, "rb") as fh:
            self.assertEqual(fh.read(), keep_bytes)

    def _run_index_control(self, label, fmt, extension):
        """单联系人固定输入的无故障对照：退出 0、stdio 全空，目录恰好
        一份连续编号预览、report.json 与 index.html；报告计数
        1、0、1，预览正文替换姓名并保留末尾 LF。返回输出目录路径。"""
        contacts_path = self._write(f"c-simple-{label}.csv", SIMPLE_CONTACTS)
        template_path = self._write(f"t-simple-{label}.txt", SIMPLE_TEMPLATE)
        out_path = os.path.join(self.tmp, f"out-index-{label}-control")
        result = self._run(
            out_path,
            fmt=fmt,
            contacts_path=contacts_path,
            template_path=template_path,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

        preview_name = f"preview-0001.{extension}"
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["index.html", preview_name, "report.json"],
        )

        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (1, 0, 1),
        )
        self.assertEqual(
            report["previews"],
            [{"email": "a@example.invalid", "file": preview_name}],
        )

        # 预览正文准确替换姓名并保留模板末尾的一个 LF。
        if extension == "txt":
            self.assertEqual(
                self._read_bytes(out_path, preview_name),
                "你好，甲！\n".encode("utf-8"),
            )
        else:
            doc = self._read_bytes(out_path, preview_name).decode("utf-8")
            self.assertTrue(doc.startswith("<!DOCTYPE html>"))
            self.assertIn('<meta charset="utf-8">', doc)
            self.assertIn("<pre>你好，甲！\n</pre>", doc)
        return out_path

    def _run_index_denied(self, label, fmt, extension, control_out):
        """index.html 创建前以 PermissionError 失败：核对退出结果与
        落盘状态，并与同格式对照逐字节比对保留的预览与报告。"""
        contacts_path = self._write(f"c-simple-{label}-deny.csv",
                                    SIMPLE_CONTACTS)
        template_path = self._write(f"t-simple-{label}-deny.txt",
                                    SIMPLE_TEMPLATE)
        out_path = os.path.join(self.tmp, f"out-index-{label}-denied")
        result = self._run(
            out_path,
            fmt=fmt,
            contacts_path=contacts_path,
            template_path=template_path,
            deny_index=True,
        )

        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"", msg="故障时标准输出必须为空")
        stderr = result.stderr.decode("utf-8")
        index_path = os.path.join(out_path, "index.html")
        # 标准错误点名 index.html 的完整目标路径与固定底层原因，无
        # Traceback。
        self.assertIn("无法写入输出文件", stderr)
        self.assertIn(index_path, stderr)
        self.assertIn(DENY_REASON, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)

        # 输入读取、逐人预览与报告写入仍正常进行：目录恰好保留一份
        # 预览与 report.json，与同格式无故障结果逐字节一致；index.html
        # 不存在，也没有其他新增文件。
        preview_name = f"preview-0001.{extension}"
        self.assertEqual(sorted(os.listdir(out_path)),
                         [preview_name, "report.json"])
        self.assertFalse(os.path.exists(index_path))
        for name in (preview_name, "report.json"):
            self.assertEqual(
                self._read_bytes(out_path, name),
                self._read_bytes(control_out, name),
                msg=f"{name} 与同格式无故障结果不一致",
            )

    def _read_bytes(self, out_path, name):
        with open(os.path.join(out_path, name), "rb") as fh:
            return fh.read()

    def test_index_write_failure_default_text_format(self):
        # 默认文本格式（省略 --format）：无故障对照后，在另一空目录仅
        # 令 index.html 创建前以 PermissionError 失败。
        control_out = self._run_index_control("text", None, "txt")
        self._run_index_denied("text", None, "txt", control_out)

    def test_index_write_failure_html_format(self):
        # --format html：同上，保留的 .html 预览与报告与对照逐字节一致。
        control_out = self._run_index_control("html", "html", "html")
        self._run_index_denied("html", "html", "html", control_out)


if __name__ == "__main__":
    unittest.main()
