"""newsletter_preview 模板处理边界的回归测试。

固定内容作者能够观察到的替换与校验结果：替换值中插入的占位符文本按
字面保留、不再次解释；单花括号与未闭合的双花括号按普通文字处理、不
触发变量错误；完整双花括号占位符区分大小写与内部空白，空占位符同样
视为未知变量；模板校验先于 segment 匹配执行，没有匹配联系人时也不得
跳过。仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱
使用 RFC 2606 保留的 example.invalid 虚构域名。

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

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 唯一联系人：姓名本身包含占位符文本，用于验证替换值不被再次解释。
NAME = "甲{{age}}{{name}}"
EMAIL = "a@example.invalid"
CONTACTS = (
    "name,email,segment\n"
    f"{NAME},{EMAIL},newsletter\n"
)
# 同一联系人改归 archive：筛选 newsletter 时零匹配。
CONTACTS_ARCHIVE = (
    "name,email,segment\n"
    f"{NAME},{EMAIL},archive\n"
)

SEGMENT = "newsletter"

# 两行模板，每行末尾均为 CRLF；两处 {{name}} 都应替换为完整原始姓名。
TEMPLATE_CRLF = "你好，{{name}}！\r\n确认{{name}}。\r\n"
# 预期预览（逐字节一致，含姓名中插入的 {{age}}、{{name}} 字面文本与 CRLF）。
PREVIEW_CRLF = "你好，甲{{age}}{{name}}！\r\n确认甲{{age}}{{name}}。\r\n"

# 单花括号与未闭合双花括号均不是完整占位符，预览必须与模板完全相同。
TEMPLATE_LITERAL = "原样 {name} {{name"

# 各只含一个非法完整占位符的失败模板：大小写不同、内部空白、空变量名。
UNKNOWN_PLACEHOLDER_TEMPLATES = ["{{Name}}", "{{ name }}", "{{}}"]


class TemplateBoundaryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 同一用例可能多次复用失败断言（subTest），输出目录名需互不相同。
        self._failure_case_no = 0

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _run(self, contacts_path, template_path, out_path, segment=SEGMENT):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "newsletter_preview",
                "--contacts",
                contacts_path,
                "--template",
                template_path,
                "--segment",
                segment,
                "--out",
                out_path,
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _assert_validation_failure(self, contacts, template, fragments):
        """同一组失败输入，分别核对两种输出目录状态下的“无输出”约定。"""
        self._failure_case_no += 1
        case_no = self._failure_case_no
        contacts_path = self._write(f"contacts-{case_no}.csv", contacts)
        template_path = self._write(f"template-{case_no}.txt", template)

        # 情形 A：输出目录尚不存在——运行后仍须不存在。
        out_absent = os.path.join(self.tmp, f"out-absent-{case_no}")
        result = self._run(contacts_path, template_path, out_absent)
        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        stderr = result.stderr.decode("utf-8")
        for fragment in fragments:
            self.assertIn(fragment, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(
            os.path.exists(out_absent),
            msg="校验失败后不得创建输出目录",
        )

        # 情形 B：输出目录已存在且为空——运行后仍须为空目录。
        out_empty = os.path.join(self.tmp, f"out-empty-{case_no}")
        os.mkdir(out_empty)
        result = self._run(contacts_path, template_path, out_empty)
        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        stderr = result.stderr.decode("utf-8")
        for fragment in fragments:
            self.assertIn(fragment, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertTrue(os.path.isdir(out_empty))
        self.assertEqual(
            os.listdir(out_empty),
            [],
            msg="校验失败后不得在空输出目录中留下任何文件",
        )

    def test_success_replaces_name_without_reinterpolation(self):
        contacts_path = self._write("contacts.csv", CONTACTS)
        template_path = self._write("template.txt", TEMPLATE_CRLF)
        out_path = os.path.join(self.tmp, "out")

        result = self._run(contacts_path, template_path, out_path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")

        # 目录恰好包含一份预览与 report.json，无多余文件。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )

        # 以字节读取，逐字节核对：两处姓名均为完整原始姓名，姓名中插入的
        # {{age}} 与 {{name}} 保持字面内容，其余文字与 CRLF 原样保留。
        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), PREVIEW_CRLF.encode("utf-8"))

        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)

        # 报告保留模板原文（含 CRLF），匹配人数与预览清单对应唯一联系人。
        self.assertEqual(report["template"], TEMPLATE_CRLF)
        self.assertEqual(report["segment"], SEGMENT)
        self.assertEqual(report["matched_count"], 1)
        self.assertEqual(
            report["previews"],
            [{"email": EMAIL, "file": "preview-0001.txt"}],
        )

    def test_success_single_brace_and_unclosed_are_literal(self):
        contacts_path = self._write("contacts.csv", CONTACTS)
        template_path = self._write("template.txt", TEMPLATE_LITERAL)
        out_path = os.path.join(self.tmp, "out")

        result = self._run(contacts_path, template_path, out_path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )

        # 预览与模板完全相同：单花括号 {name} 与未闭合的 {{name 均为
        # 普通文字，不替换、不报错。
        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), TEMPLATE_LITERAL.encode("utf-8"))

        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["template"], TEMPLATE_LITERAL)
        self.assertEqual(report["matched_count"], 1)

    def test_failure_unknown_placeholders(self):
        # 每个模板只出现一个非法完整占位符；stderr 必须包含“未知变量”
        # 与对应的完整占位符原文（区分大小写、保留内部空白、含空占位符）。
        for template in UNKNOWN_PLACEHOLDER_TEMPLATES:
            with self.subTest(template=template):
                self._assert_validation_failure(
                    CONTACTS,
                    template,
                    ["未知变量", template],
                )

    def test_failure_template_validated_even_without_matching_contacts(self):
        # 联系人全部属于 archive、仍筛选 newsletter（零匹配）时，模板
        # 校验仍须先执行：{{Name}} 同样导致退出 2，不得因无匹配而跳过。
        self._assert_validation_failure(
            CONTACTS_ARCHIVE,
            "{{Name}}",
            ["未知变量", "{{Name}}"],
        )


if __name__ == "__main__":
    unittest.main()
