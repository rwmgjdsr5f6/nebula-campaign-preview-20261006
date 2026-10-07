"""newsletter_preview 三种变量混合替换的回归测试。

固定 {{name}}、{{email}}、{{segment}} 在同一份模板中混合出现时的
替换结果：替换值中的占位符样式文本（姓名里的 {{email}}、分组里的
{{name}}）保持字面、不再次解释；邮箱与分组值的首尾空格及模板末尾
LF 原样保留；同一变量多处出现取同一原始值。默认 text 格式与
--format html 各自核对退出码、标准错误、预览产物与 report.json；
HTML 的唯一 pre 元素经实体还原后与文本正文逐字一致，姓名中的
<b> 与 & 按文字显示，不产生 b 元素。错误样例把模板中一处
{{email}} 改为 {{Email}}、把 {{segment}} 改为 {{ segment }}，均
退出 2，stderr 点名未知变量与出错占位符且无 Traceback，输出目录
不创建、已有空目录保持为空。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。每个样例使用独立临时
目录，结束后自动清理，项目目录不留数据。

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

# 成功样例输入：第一条记录的姓名含占位符样式文本 {{email}} 与 HTML
# 特殊字符 <b>、&，邮箱与分组均带首尾空格，分组含 {{name}}；第二条
# 分组为 archive，筛选时不命中。
CONTACTS = (
    "name,email,segment\n"
    "甲{{email}}<b>&, A@example.invalid , News{{name}} \n"
    "乙,b@example.invalid,archive\n"
)
# 筛选值与第一条记录 segment 列的完整原文相同（含首尾空格）。
SEGMENT = " News{{name}} "
NAME = "甲{{email}}<b>&"
EMAIL = " A@example.invalid "

# 三种变量混合出现，{{email}} 出现两处，模板末尾含一个 LF。
TEMPLATE = "姓名={{name}}；邮箱={{email}}；分组={{segment}}；再次={{email}}。\n"
# 期望正文：替换值中的 {{email}} 与 {{name}} 保持字面，邮箱与分组的
# 首尾空格及末尾 LF 原样保留，两处邮箱取同一原始值。
EXPECTED_BODY = (
    "姓名=甲{{email}}<b>&；邮箱= A@example.invalid ；"
    "分组= News{{name}} ；再次= A@example.invalid 。\n"
)

# 错误样例：其余输入不变，仅把模板中一处占位符改为未知变量。
TEMPLATE_CASE_MISMATCH = TEMPLATE.replace("{{email}}", "{{Email}}", 1)
TEMPLATE_INNER_SPACES = TEMPLATE.replace("{{segment}}", "{{ segment }}")


class _PreTextExtractor(HTMLParser):
    """按浏览器规则解析文档，收集唯一 pre 元素内的全部文本及全部标签名。

    convert_charrefs=True 使数字/命名实体按解析结果还原，因此还原后的
    pre 文本与浏览器显示一致；tags 记录出现过的元素名，用于核对 <b>
    等文字未成为真实页面元素。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._pre_depth = 0
        self.parts = []
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
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


class MixedVariablesTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _run(self, template_path, out_path, extra_args=()):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        argv = [
            sys.executable,
            "-m",
            "newsletter_preview",
            "--contacts",
            self.contacts_path,
            "--template",
            template_path,
            "--segment",
            SEGMENT,
            "--out",
            out_path,
        ]
        argv.extend(extra_args)
        return subprocess.run(
            argv, cwd=PROJECT_ROOT, env=env, capture_output=True
        )

    def _assert_report(self, out_path, preview_file):
        """报告保留模板原文与完整筛选值，计数 1/0/1，排除明细为空。"""
        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["template"], TEMPLATE)
        self.assertEqual(report["segment"], SEGMENT)
        self.assertEqual(report["segment_count"], 1)
        self.assertEqual(report["excluded_count"], 0)
        self.assertEqual(report["matched_count"], 1)
        self.assertEqual(report["excluded_contacts"], [])
        # 预览清单保留原始邮箱（含首尾空格）并指向实际文件。
        self.assertEqual(
            report["previews"],
            [{"email": EMAIL, "file": preview_file}],
        )
        self.assertIn(preview_file, os.listdir(out_path))

    def test_text_format_mixed_replacement(self):
        template_path = self._write("template.txt", TEMPLATE)
        out_path = os.path.join(self.tmp, "out-text")

        result = self._run(template_path, out_path)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )
        # 以二进制读取，逐字节核对正文（含首尾空格与末尾 LF）。
        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), EXPECTED_BODY.encode("utf-8"))
        self._assert_report(out_path, "preview-0001.txt")

    def test_html_format_mixed_replacement(self):
        template_path = self._write("template.txt", TEMPLATE)
        out_path = os.path.join(self.tmp, "out-html")

        result = self._run(template_path, out_path, ("--format", "html"))

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.html", "report.json"],
        )

        with open(os.path.join(out_path, "preview-0001.html"), "rb") as fh:
            doc = fh.read().decode("utf-8")
        parser = _PreTextExtractor()
        parser.feed(doc)
        # 唯一 pre 元素经实体还原后与文本正文逐字一致。
        self.assertEqual(parser.tags.count("pre"), 1)
        self.assertEqual(parser.text(), EXPECTED_BODY)
        # 姓名中的 <b> 与 & 作为文字显示，不产生 b 元素。
        self.assertNotIn("b", parser.tags)
        self.assertIn("&lt;b&gt;", doc)
        self.assertIn("&amp;", doc)
        self._assert_report(out_path, "preview-0001.html")

    def test_failure_unknown_variable_variants(self):
        """两种未知变量变体：退出 2，stderr 点名变量，两种目录状态均无输出。"""
        variants = (
            ("case-mismatch", TEMPLATE_CASE_MISMATCH, "{{Email}}"),
            ("inner-spaces", TEMPLATE_INNER_SPACES, "{{ segment }}"),
        )
        for label, template, placeholder in variants:
            with self.subTest(variant=label, artifact="stderr"):
                template_path = self._write(f"template-{label}.txt", template)

                # 情形 A：输出目录尚不存在——运行后仍须不存在。
                out_absent = os.path.join(self.tmp, f"out-absent-{label}")
                result = self._run(template_path, out_absent)
                self.assertEqual(
                    result.returncode,
                    2,
                    msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
                )
                stderr = result.stderr.decode("utf-8")
                self.assertIn("未知变量", stderr)
                self.assertIn(placeholder, stderr)
                self.assertNotIn("Traceback (most recent call last)", stderr)
                self.assertFalse(
                    os.path.exists(out_absent),
                    msg="校验失败后不得创建输出目录",
                )

            with self.subTest(variant=label, artifact="out-empty"):
                # 情形 B：输出目录已存在且为空——运行后仍须为空目录。
                out_empty = os.path.join(self.tmp, f"out-empty-{label}")
                os.mkdir(out_empty)
                result = self._run(template_path, out_empty)
                self.assertEqual(
                    result.returncode,
                    2,
                    msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
                )
                stderr = result.stderr.decode("utf-8")
                self.assertIn("未知变量", stderr)
                self.assertIn(placeholder, stderr)
                self.assertNotIn("Traceback (most recent call last)", stderr)
                self.assertTrue(os.path.isdir(out_empty))
                self.assertEqual(
                    os.listdir(out_empty),
                    [],
                    msg="校验失败后不得在空输出目录中留下任何文件",
                )


if __name__ == "__main__":
    unittest.main()
