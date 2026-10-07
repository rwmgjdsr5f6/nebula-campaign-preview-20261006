"""newsletter_preview --index 索引页的回归测试。

验证：
- --index 为无值参数：开启时在输出目录额外生成 index.html，省略时产物
  与既有版本完全一致；开启时逐人预览与 report.json 与同输入、同格式
  但未开启时逐字节一致，唯一新增文件是索引页（text 与 html 均适用）；
- 索引页是声明 UTF-8 的完整 HTML 文档，不引用任何网络资源：显示筛选值
  及分组命中、排除、最终预览三个记录数（与 report.json 一致），按 CSV
  顺序列出保留记录的原始姓名、邮箱与预览链接（相对地址为对应文件名，
  重复邮箱分别列项，被排除或未命中的记录不在清单中，邮箱仅显示为文字）；
- 姓名、邮箱与筛选值中的中文、&、尖括号、引号及 {{name}} 样式文字按
  字面显示（以 html.parser 按浏览器实体规则还原后逐字比较），不解析为
  标签、实体或变量，大小写与首尾空白保留；
- 零命中或全部排除时退出 0，只生成 report.json 与 index.html，索引保留
  三个计数并显示“没有可预览的联系人”，无预览链接；
- 输入校验失败仍退出 2，stderr 点名原因且无 Traceback，新目录不创建、
  已有空目录保持为空；非空输出目录被拒绝且原内容保留。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests
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

# 验收样例：前两条记录共享邮箱且同属 newsletter，姓名分别含 & 与尖括号；
# 第三条同组但邮箱被排除。模板无末尾换行。
CONTACTS = (
    "name,email,segment\n"
    "甲&乙,shared@example.invalid,newsletter\n"
    "丙<丁>,shared@example.invalid,newsletter\n"
    "戊,cut@example.invalid,newsletter\n"
)
TEMPLATE = "你好，{{name}}！"
SEGMENT = "newsletter"
EXCLUDED_EMAIL = "cut@example.invalid"


class _TextExtractor(HTMLParser):
    """按浏览器规则解析文档，收集全部文本与链接。

    convert_charrefs=True 使实体按解析结果还原，因此还原后的文本与浏览器
    显示一致；links 记录每个 a 元素的 href 与链接文字。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self._href = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag == "a":
            self._href = None

    def handle_data(self, data):
        self.parts.append(data)
        if self._href is not None:
            self.links.append((self._href, data))

    def text(self):
        return "".join(self.parts)


class IndexTestCase(unittest.TestCase):
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

    def _run(self, out_path, extra_args=(), contacts_path=None,
             template_path=None, segment=SEGMENT):
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
        ]
        argv.extend(extra_args)
        return subprocess.run(
            argv, cwd=PROJECT_ROOT, env=env, capture_output=True
        )

    def _read(self, out_path, name):
        with open(os.path.join(out_path, name), "rb") as fh:
            return fh.read()

    def test_index_page_counts_links_and_literal_text(self):
        out = os.path.join(self.tmp, "out-index")
        result = self._run(
            out,
            ("--format", "html", "--exclude-email", EXCLUDED_EMAIL, "--index"),
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            sorted(os.listdir(out)),
            ["index.html", "preview-0001.html", "preview-0002.html",
             "report.json"],
        )

        doc = self._read(out, "index.html").decode("utf-8")
        # 声明 UTF-8 的完整文档，无任何网络资源；邮箱不成为链接。
        self.assertIn("<meta charset=\"utf-8\">", doc)
        self.assertNotIn("http://", doc)
        self.assertNotIn("https://", doc)
        self.assertNotIn("src=", doc)
        self.assertNotIn("mailto:", doc)

        parser = _TextExtractor()
        parser.feed(doc)
        text = parser.text()
        # 三个记录数与 report.json 一致：分组命中 3、排除 1、最终预览 2。
        with open(os.path.join(out, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (3, 1, 2),
        )
        self.assertIn("3", text)
        self.assertIn("1", text)
        self.assertIn("2", text)
        self.assertIn(SEGMENT, text)
        # 姓名中的 & 与尖括号按字面显示（还原后与原文逐字一致），
        # 原始标记中必须以转义形式出现。
        self.assertIn("甲&乙", text)
        self.assertIn("丙<丁>", text)
        self.assertIn("甲&amp;乙", doc)
        self.assertIn("丙&lt;丁&gt;", doc)
        self.assertNotIn("<丁>", doc)
        # 共享邮箱的两条保留记录分别列项，邮箱仅作为文字出现。
        self.assertEqual(text.count("shared@example.invalid"), 2)
        self.assertNotIn("戊", text)
        self.assertNotIn(EXCLUDED_EMAIL, text)
        # 预览链接为相对地址（对应文件名），按 CSV 顺序各一条。
        self.assertEqual(
            parser.links,
            [("preview-0001.html", "preview-0001.html"),
             ("preview-0002.html", "preview-0002.html")],
        )

    def test_index_omitted_and_enabled_outputs_byte_identical(self):
        # text 与 html 两种格式下：开启 --index 时既有产物与未开启时
        # 逐字节一致，唯一新增文件是 index.html。
        for fmt in ("text", "html"):
            extension = "txt" if fmt == "text" else "html"
            names = [f"preview-0001.{extension}",
                     f"preview-0002.{extension}", "report.json"]
            extra = ("--format", fmt, "--exclude-email", EXCLUDED_EMAIL)
            out_plain = os.path.join(self.tmp, f"plain-{fmt}")
            out_index = os.path.join(self.tmp, f"index-{fmt}")
            for out, tail in ((out_plain, extra), (out_index, extra + ("--index",))):
                result = self._run(out, tail)
                self.assertEqual(
                    result.returncode, 0,
                    msg=result.stderr.decode("utf-8", "replace"),
                )
            self.assertEqual(sorted(os.listdir(out_plain)), sorted(names))
            self.assertEqual(
                sorted(os.listdir(out_index)), sorted(names + ["index.html"])
            )
            for name in names:
                self.assertEqual(
                    self._read(out_plain, name), self._read(out_index, name),
                    msg=f"{fmt} 模式下 {name} 须逐字节一致",
                )

    def test_index_zero_hits_and_all_excluded(self):
        # 零命中：只生成报告与索引，索引保留三个计数并显示提示，无链接。
        out_zero = os.path.join(self.tmp, "out-zero")
        result = self._run(out_zero, ("--index",), segment="none")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(sorted(os.listdir(out_zero)), ["index.html", "report.json"])
        doc = self._read(out_zero, "index.html").decode("utf-8")
        parser = _TextExtractor()
        parser.feed(doc)
        self.assertIn("没有可预览的联系人", parser.text())
        self.assertEqual(parser.links, [])
        with open(os.path.join(out_zero, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (0, 0, 0),
        )

        # 全部排除：同样只生成报告与索引，无预览链接。
        out_all = os.path.join(self.tmp, "out-all-excluded")
        result = self._run(
            out_all,
            ("--exclude-email", "shared@example.invalid",
             "--exclude-email", EXCLUDED_EMAIL, "--index"),
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(sorted(os.listdir(out_all)), ["index.html", "report.json"])
        doc = self._read(out_all, "index.html").decode("utf-8")
        parser = _TextExtractor()
        parser.feed(doc)
        text = parser.text()
        self.assertIn("没有可预览的联系人", text)
        self.assertEqual(parser.links, [])
        with open(os.path.join(out_all, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (3, 3, 0),
        )

    def test_index_validation_failures_create_nothing(self):
        # 未知变量：新目录不创建。
        bad_template = self._write("bad-template.txt", "{{name}}{{age}}")
        out_absent = os.path.join(self.tmp, "out-uv")
        result = self._run(out_absent, ("--index",), template_path=bad_template)
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("未知变量", stderr)
        self.assertIn("age", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))

        # 缺列：已有空目录保持为空。
        bad_contacts = self._write(
            "bad-contacts.csv", "name,email\n甲,a@example.invalid\n"
        )
        out_empty = os.path.join(self.tmp, "out-empty")
        os.mkdir(out_empty)
        result = self._run(out_empty, ("--index",), contacts_path=bad_contacts)
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("segment", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertEqual(os.listdir(out_empty), [])

        # 必需字段仅含空白：新目录不创建。
        blank_contacts = self._write(
            "blank-contacts.csv",
            "name,email,segment\n ,a@example.invalid,newsletter\n",
        )
        out_blank = os.path.join(self.tmp, "out-blank")
        result = self._run(out_blank, ("--index",), contacts_path=blank_contacts)
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("name", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_blank))

        # 输入不可读：新目录不创建。
        out_unread = os.path.join(self.tmp, "out-unread")
        result = self._run(
            out_unread, ("--index",),
            contacts_path=os.path.join(self.tmp, "missing.csv"),
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(
            "Traceback (most recent call last)",
            result.stderr.decode("utf-8"),
        )
        self.assertFalse(os.path.exists(out_unread))

    def test_index_nonempty_output_dir_rejected_and_preserved(self):
        out = os.path.join(self.tmp, "out-nonempty")
        os.mkdir(out)
        with open(os.path.join(out, "existing.txt"), "w", encoding="utf-8") as fh:
            fh.write("keep\n")
        result = self._run(out, ("--index",))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(
            "Traceback (most recent call last)",
            result.stderr.decode("utf-8"),
        )
        self.assertEqual(sorted(os.listdir(out)), ["existing.txt"])
        with open(os.path.join(out, "existing.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "keep\n")


if __name__ == "__main__":
    unittest.main()
