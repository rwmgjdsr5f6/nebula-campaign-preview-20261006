"""newsletter_preview 三种模板变量混合替换的回归测试。

固定 {{name}}、{{email}}、{{segment}} 在同一模板中混合、重复出现时
作者能够观察到的结果：各占位符取当前记录的原始字段值（保留大小写与
首尾空白、保留末尾 LF），替换值不再次解析——因此姓名中的
{{email}}、分组中的 {{name}} 均保持字面；同一变量多处出现取同一原始
值。text 模式正文与预期逐字节一致；html 模式唯一 pre 元素经实体还原
后与同一正文一致，字段值中的 <b> 与 & 按字面显示、不产生 b 元素。
错误样例（{{Email}} 大小写不符、{{ segment }} 内侧空格）退出 2，
stderr 点名未知变量与出错占位符且无 Traceback，不创建任何输出。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准错误、落盘文件内容与输出目录状态；不直接调用
内部函数。每个样例使用独立临时目录，结束后自动清理。
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

# 合成联系人：表头固定 name,email,segment（UTF-8 CSV）。
# 第一条的姓名本身含占位符样式文本 {{email}} 与标签/实体样式文字
# <b>&；邮箱首尾各带一个空格；分组首尾各带一个空格且内含 {{name}}。
# 第二条分属 archive，筛选第一条的分组原文时不命中，故仅生成一份预览。
CONTACTS = (
    "name,email,segment\n"
    "甲{{email}}<b>&, A@example.invalid , News{{name}} \n"
    "乙,b@example.invalid,archive\n"
)
NAME = "甲{{email}}<b>&"
EMAIL = " A@example.invalid "
# 筛选值与第一条 segment 列的完整原文相同（含首尾空格与字面 {{name}}）。
SEGMENT = " News{{name}} "

# 三种变量混合、{{email}} 重复出现的模板，末尾恰有一个 LF。
TEMPLATE = "姓名={{name}}；邮箱={{email}}；分组={{segment}}；再次={{email}}。\n"

# 替换后的唯一正文：姓名中的 {{email}}、分组中的 {{name}} 保持字面；
# 邮箱与分组首尾空格、末尾 LF 原样保留；两处邮箱取同一原始值。
EXPECTED_BODY = (
    "姓名=甲{{email}}<b>&；邮箱= A@example.invalid ；"
    "分组= News{{name}} ；再次= A@example.invalid 。\n"
)

# 错误变体：其余输入与成功样例完全相同，仅在模板上各改一处。
# 变体 A：一处 {{email}} 改为大小写不符的 {{Email}}。
BAD_TEMPLATE_EMAIL = (
    "姓名={{name}}；邮箱={{Email}}；分组={{segment}}；再次={{email}}。\n"
)
# 变体 B：{{segment}} 改为花括号内侧带空格的 {{ segment }}。
BAD_TEMPLATE_SEGMENT = (
    "姓名={{name}}；邮箱={{email}}；分组={{ segment }}；再次={{email}}。\n"
)
BAD_VARIANTS = (
    ("email 大小写不符：{{Email}}", BAD_TEMPLATE_EMAIL, "{{Email}}"),
    ("segment 内侧空格：{{ segment }}", BAD_TEMPLATE_SEGMENT, "{{ segment }}"),
)


class _PreInspector(HTMLParser):
    """收集 pre 元素开闭次数、pre 内还原文本与文档中出现的全部标签名。

    convert_charrefs=True 使数字/命名实体按解析结果还原；starttag 与
    startendtag 分别记录，便于核对 pre 唯一且字段值中的 <b> 未成为元素。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._pre_depth = 0
        self.parts = []
        self.pre_start_count = 0
        self.pre_end_count = 0
        self.tags = set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        if tag == "pre":
            self.pre_start_count += 1
            self._pre_depth += 1

    def handle_startendtag(self, tag, attrs):
        self.tags.add(tag)

    def handle_endtag(self, tag):
        if tag == "pre":
            self.pre_end_count += 1
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

    def test_success_default_text_mixed_variables(self):
        # 省略 --format（默认 text）：退出 0、stderr 为空，目录中恰好
        # 一份 preview-0001.txt 与 report.json。
        template_path = self._write("template.txt", TEMPLATE)
        out_path = os.path.join(self.tmp, "out-text")
        result = self._run(template_path, out_path)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"默认 text 运行失败，stderr："
            f"{result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )

        # 正文与预期替换结果逐字节一致（含首尾空格与末尾 LF）。
        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(
                fh.read(),
                EXPECTED_BODY.encode("utf-8"),
                msg="preview-0001.txt 正文与预期替换结果不一致",
            )

        expected_report = {
            "template": TEMPLATE,
            "segment": SEGMENT,
            "segment_count": 1,
            "excluded_count": 0,
            "excluded_contacts": [],
            "matched_count": 1,
            "previews": [
                {"email": EMAIL, "file": "preview-0001.txt"},
            ],
        }
        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            report,
            expected_report,
            msg="text 模式 report.json 内容与预期不符",
        )

    def test_success_html_mixed_variables(self):
        # --format html：退出 0、stderr 为空，目录中恰好一份
        # preview-0001.html 与 report.json（无文本副本）。
        template_path = self._write("template.txt", TEMPLATE)
        out_path = os.path.join(self.tmp, "out-html")
        result = self._run(template_path, out_path, ("--format", "html"))

        self.assertEqual(
            result.returncode,
            0,
            msg=f"html 运行失败，stderr："
            f"{result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.html", "report.json"],
        )

        with open(
            os.path.join(out_path, "preview-0001.html"), encoding="utf-8"
        ) as fh:
            doc = fh.read()

        # 唯一 pre 元素经实体还原后的文本与 text 预期正文逐字一致：
        # <b>、& 作为文字显示，不产生 b 元素；空格与末尾 LF 保留。
        inspector = _PreInspector()
        inspector.feed(doc)
        self.assertEqual(
            inspector.pre_start_count,
            1,
            msg="HTML 文档应恰有一个 pre 元素（开始标签）",
        )
        self.assertEqual(
            inspector.pre_end_count,
            1,
            msg="HTML 文档应恰有一个 pre 元素（结束标签）",
        )
        self.assertNotIn("b", inspector.tags, msg="字段值中的 <b> 不得成为 b 元素")
        self.assertEqual(
            inspector.text(),
            EXPECTED_BODY,
            msg="HTML pre 经实体还原后的文本与预期替换结果不一致",
        )
        # 原始文档中尖括号与 & 必须是转义形态，且声明 UTF-8。
        self.assertIn("<meta charset=\"utf-8\">", doc)
        self.assertIn("甲{{email}}&lt;b&gt;&amp;", doc)
        self.assertNotIn("<b>", doc)

        expected_report = {
            "template": TEMPLATE,
            "segment": SEGMENT,
            "segment_count": 1,
            "excluded_count": 0,
            "excluded_contacts": [],
            "matched_count": 1,
            "previews": [
                {"email": EMAIL, "file": "preview-0001.html"},
            ],
        }
        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            report,
            expected_report,
            msg="html 模式 report.json 内容与预期不符",
        )

    def test_failure_unknown_variable_variants_create_nothing(self):
        # 两个错误变体各自在两种输出目录状态下核对：尚不存在的目录运行
        # 后仍不存在；已存在的空目录运行后仍为空。
        for label, bad_template, placeholder in BAD_VARIANTS:
            template_path = self._write("template.txt", bad_template)

            with self.subTest(variant=label, out_dir="尚不存在"):
                out_absent = os.path.join(self.tmp, "out-absent")
                result = self._run(template_path, out_absent)
                self.assertEqual(
                    result.returncode,
                    2,
                    msg=f"[{label}] 应退出 2，实际 {result.returncode}，"
                    f"stderr：{result.stderr.decode('utf-8', 'replace')}",
                )
                stderr = result.stderr.decode("utf-8")
                self.assertIn("未知变量", stderr, msg=f"[{label}] stderr 未点名未知变量")
                self.assertIn(placeholder, stderr, msg=f"[{label}] stderr 未给出错占位符")
                self.assertNotIn("Traceback (most recent call last)", stderr)
                self.assertFalse(
                    os.path.exists(out_absent),
                    msg=f"[{label}] 校验失败后不得创建输出目录 {out_absent}",
                )

            with self.subTest(variant=label, out_dir="已有空目录"):
                out_empty = os.path.join(self.tmp, "out-empty")
                os.mkdir(out_empty)
                try:
                    result = self._run(template_path, out_empty)
                    self.assertEqual(
                        result.returncode,
                        2,
                        msg=f"[{label}] 应退出 2，实际 {result.returncode}，"
                        f"stderr：{result.stderr.decode('utf-8', 'replace')}",
                    )
                    stderr = result.stderr.decode("utf-8")
                    self.assertIn("未知变量", stderr, msg=f"[{label}] stderr 未点名未知变量")
                    self.assertIn(placeholder, stderr, msg=f"[{label}] stderr 未给出错占位符")
                    self.assertNotIn("Traceback (most recent call last)", stderr)
                    self.assertTrue(os.path.isdir(out_empty))
                    self.assertEqual(
                        os.listdir(out_empty),
                        [],
                        msg=f"[{label}] 校验失败后不得在空输出目录留下任何产物",
                    )
                finally:
                    os.rmdir(out_empty)


if __name__ == "__main__":
    unittest.main()
