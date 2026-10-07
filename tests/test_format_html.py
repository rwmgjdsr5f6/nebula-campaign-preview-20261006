"""newsletter_preview --format text/html 输出格式的回归测试。

验证：
- 省略 --format 或给 text 时输出与既有文本模式逐字节一致（.txt）；
- --format html 时逐人预览为 preview-0001.html 起连续编号，目录中只有
  HTML 文件与 report.json，报告 previews 清单改为 .html 文件名；
- HTML 为声明 UTF-8 的完整文档，正文在唯一 pre 元素内按字面显示：
  &、<、>、标签与实体样式文字经转义后不成其为页面元素，中文、空格、
  空行、正文开头与末尾换行全部保留（以 html.parser 按浏览器实体规则
  还原 pre 文本，与替换后正文逐字比较）；
- --format 取值非法或缺值退出 2，stderr 点名 --format 且无 Traceback，
  不创建输出；html 模式下未知变量、缺列、输入不可读同样退出 2，
  新目录不创建、已有空目录保持为空；非空输出目录被拒绝且原内容保留。

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

# 验收样例：记录“甲&乙”与“丙”共享邮箱且同属 newsletter，丁属 archive；
# 姓名中含 &，用于核对 HTML 转义与字段值不再次解析。
CONTACTS = (
    "name,email,segment\n"
    "甲&乙,shared@example.invalid,newsletter\n"
    "丙,shared@example.invalid,newsletter\n"
    "丁,ding@example.invalid,archive\n"
)
# 模板正文第一行为标签样式文字，含末尾换行；第二行变量后接末尾换行。
TEMPLATE = "<提醒>\n你好，{{name}}！\n"
SEGMENT = "newsletter"

BODY_JIA = "<提醒>\n你好，甲&乙！\n"
BODY_BING = "<提醒>\n你好，丙！\n"


class _PreTextExtractor(HTMLParser):
    """按浏览器规则解析文档，收集唯一 pre 元素内的全部文本。

    convert_charrefs=True 使数字/命名实体按解析结果还原，因此还原后的
    文本与浏览器显示一致：标签样式文字应作为数据出现，实体样式文字
    （如 &amp;nbsp;）应保持字面。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._pre_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self._pre_depth += 1

    def handle_endtag(self, tag):
        if tag == "pre":
            self._pre_depth -= 1

    def handle_data(self, data):
        if self._pre_depth:
            self.parts.append(data)

    def text(self):
        return "".join(self.parts)


class FormatTestCase(unittest.TestCase):
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
             template_path=None):
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
            SEGMENT,
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

    def test_default_and_text_format_are_byte_identical_txt(self):
        out_default = os.path.join(self.tmp, "out-default")
        out_text = os.path.join(self.tmp, "out-text")
        for out, extra in ((out_default, ()), (out_text, ("--format", "text"))):
            result = self._run(out, extra)
            self.assertEqual(
                result.returncode, 0,
                msg=result.stderr.decode("utf-8", "replace"),
            )
            self.assertEqual(result.stderr, b"")
            self.assertEqual(
                sorted(os.listdir(out)),
                ["preview-0001.txt", "preview-0002.txt", "report.json"],
            )
        # 省略参数与显式 text 的全部产物逐字节一致。
        for name in ("preview-0001.txt", "preview-0002.txt", "report.json"):
            self.assertEqual(
                self._read(out_default, name), self._read(out_text, name)
            )
        self.assertEqual(
            self._read(out_default, "preview-0001.txt").decode("utf-8"),
            BODY_JIA,
        )
        self.assertEqual(
            self._read(out_default, "preview-0002.txt").decode("utf-8"),
            BODY_BING,
        )

    def test_html_format_files_report_and_literal_pre_text(self):
        out = os.path.join(self.tmp, "out-html")
        result = self._run(out, ("--format", "html"))
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        self.assertEqual(result.stderr, b"")

        # 只有连续编号的 HTML 文件与 report.json，无文本副本。
        self.assertEqual(
            sorted(os.listdir(out)),
            ["preview-0001.html", "preview-0002.html", "report.json"],
        )

        for name, expected_body in (
            ("preview-0001.html", BODY_JIA),
            ("preview-0002.html", BODY_BING),
        ):
            raw = self._read(out, name)
            # 声明 UTF-8 且无任何网络资源引用。
            doc = raw.decode("utf-8")
            self.assertIn("<meta charset=\"utf-8\">", doc)
            self.assertNotIn("http://", doc)
            self.assertNotIn("https://", doc)
            self.assertNotIn("src=", doc)

            parser = _PreTextExtractor()
            parser.feed(doc)
            # pre 中按浏览器规则还原的文本与替换后正文逐字一致：
            # 标签样式文字字面显示，& 不成实体，开头与末尾 LF 保留。
            self.assertEqual(parser.text(), expected_body)
            # 模板中的 <提醒> 与尖括号必须以转义形式出现，
            # 不能成为真实页面元素。
            self.assertIn("&lt;提醒&gt;", doc)
            self.assertNotIn("<提醒>", doc)

        # 字段值中的 & 同样按字面转义（只出现在甲&乙的预览里），
        # 不与相邻文字组成实体。
        self.assertIn("甲&amp;乙", self._read(out, "preview-0001.html").decode("utf-8"))

        with open(os.path.join(out, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["template"], TEMPLATE)
        self.assertEqual(report["segment"], SEGMENT)
        self.assertEqual(report["segment_count"], 2)
        self.assertEqual(report["excluded_count"], 0)
        self.assertEqual(report["matched_count"], 2)
        self.assertEqual(
            report["previews"],
            [
                {"email": "shared@example.invalid", "file": "preview-0001.html"},
                {"email": "shared@example.invalid", "file": "preview-0002.html"},
            ],
        )

    def test_html_zero_hits_only_report(self):
        out = os.path.join(self.tmp, "out-zero")
        result = self._run_zero(out)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(os.listdir(out), ["report.json"])
        with open(os.path.join(out, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (0, 0, 0),
        )
        self.assertEqual(report["previews"], [])

    def _run_zero(self, out):
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [
                sys.executable, "-m", "newsletter_preview",
                "--contacts", self.contacts_path,
                "--template", self.template_path,
                "--segment", "none",
                "--out", out,
                "--format", "html",
            ],
            cwd=PROJECT_ROOT, env=env, capture_output=True,
        )

    def test_invalid_format_value_or_missing_value_exits_2(self):
        cases = (
            ("out-bad", ["--format", "bogus"]),
            ("out-missing", ["--format"]),
        )
        for dirname, tail in cases:
            out = os.path.join(self.tmp, dirname)
            result = self._run_raw(out, tail)
            self.assertEqual(result.returncode, 2)
            stderr = result.stderr.decode("utf-8")
            self.assertIn("--format", stderr)
            self.assertNotIn("Traceback (most recent call last)", stderr)
            self.assertFalse(
                os.path.exists(out), msg="参数非法时不得创建输出目录"
            )

    def _run_raw(self, out, tail):
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        argv = [
            sys.executable, "-m", "newsletter_preview",
            "--contacts", self.contacts_path,
            "--template", self.template_path,
            "--segment", SEGMENT,
            "--out", out,
        ]
        argv.extend(tail)
        return subprocess.run(
            argv, cwd=PROJECT_ROOT, env=env, capture_output=True
        )

    def test_html_input_validation_failures_create_nothing(self):
        # 未知变量：新目录不创建。
        bad_template = self._write("bad-template.txt", "{{name}}\n{{age}}\n")
        out_absent = os.path.join(self.tmp, "out-uv")
        result = self._run(
            out_absent, ("--format", "html"), template_path=bad_template
        )
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
        result = self._run(
            out_empty, ("--format", "html"), contacts_path=bad_contacts
        )
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("segment", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertEqual(os.listdir(out_empty), [])

        # 输入不可读：新目录不创建。
        out_unread = os.path.join(self.tmp, "out-unread")
        result = self._run_raw_input(
            out_unread, os.path.join(self.tmp, "missing.csv")
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(
            "Traceback (most recent call last)",
            result.stderr.decode("utf-8"),
        )
        self.assertFalse(os.path.exists(out_unread))

    def _run_raw_input(self, out, contacts):
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [
                sys.executable, "-m", "newsletter_preview",
                "--contacts", contacts,
                "--template", self.template_path,
                "--segment", SEGMENT,
                "--out", out,
                "--format", "html",
            ],
            cwd=PROJECT_ROOT, env=env, capture_output=True,
        )

    def test_html_nonempty_output_dir_rejected_and_preserved(self):
        out = os.path.join(self.tmp, "out-nonempty")
        os.mkdir(out)
        with open(os.path.join(out, "existing.txt"), "w", encoding="utf-8") as fh:
            fh.write("keep\n")
        result = self._run(out, ("--format", "html"))
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
