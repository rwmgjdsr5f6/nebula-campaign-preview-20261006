"""newsletter_preview 模板处理边界的回归测试。

固定内容作者能够观察到的替换与校验结果：替换值中的占位符样式文本
保持字面、不再次解释；单花括号与未闭合文本按普通文字处理；仅精确
的 {{name}} 被接受，{{Name}}、{{ name }}、{{}} 均报“未知变量”；
模板校验先于受众匹配，零匹配联系人时也不跳过。仅依赖 Python 3
标准库，完全离线；样例联系人数据为合成，邮箱使用 RFC 2606 保留的
example.invalid 虚构域名。

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

# 成功样例输入：唯一联系人姓名本身含有占位符样式文本 {{age}} 与
# {{name}}，用于固定“替换值不再次解释”的行为。
CONTACTS_PLACEHOLDER_NAME = (
    "name,email,segment\n"
    "甲{{age}}{{name}},a@example.invalid,newsletter\n"
)
# 与成功样例同一人，但分组为 archive：筛选 newsletter 时零匹配，
# 用于核对模板校验不依赖是否存在匹配联系人。
CONTACTS_ARCHIVE_ONLY = (
    "name,email,segment\n"
    "甲{{age}}{{name}},a@example.invalid,archive\n"
)
EMAIL = "a@example.invalid"
SEGMENT = "newsletter"

# 两行模板，每行末尾均为 CRLF；两处 {{name}} 都应替换为完整原始姓名。
TEMPLATE_CRLF = "你好，{{name}}！\r\n确认{{name}}。\r\n"
# 姓名中的 {{age}} 与 {{name}} 在替换后保持字面内容，不再次解释；
# 其余文字与 CRLF 换行逐字节一致。
PREVIEW_CRLF = "你好，甲{{age}}{{name}}！\r\n确认甲{{age}}{{name}}。\r\n"

# 单花括号 {name} 与未闭合的 {{name 均不构成完整占位符：不触发变量
# 错误，也不发生任何替换，预览与模板完全相同。
TEMPLATE_LITERAL = "原样 {name} {{name"


class TemplateBoundaryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _run(self, contacts_path, template_path, out_path):
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
                SEGMENT,
                "--out",
                out_path,
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _assert_success(self, contacts, template, expected_preview_bytes):
        """成功路径：退出码 0、标准错误为空，输出目录恰好一份预览加报告。"""
        contacts_path = self._write("contacts.csv", contacts)
        template_path = self._write("template.txt", template)
        out_path = os.path.join(self.tmp, "out")

        result = self._run(contacts_path, template_path, out_path)

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

        # 以二进制读取，逐字节核对正文（含 CRLF 换行）。
        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), expected_preview_bytes)

        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        # 报告保留模板原文与筛选值。
        self.assertEqual(report["template"], template)
        self.assertEqual(report["segment"], SEGMENT)
        self.assertEqual(report["matched_count"], 1)
        self.assertEqual(
            report["previews"],
            [{"email": EMAIL, "file": "preview-0001.txt"}],
        )

    def _run_failure(self, contacts, template, fragments):
        """同一组失败输入，分别核对两种输出目录状态下的“无输出”约定。"""
        contacts_path = self._write("contacts.csv", contacts)
        template_path = self._write("template.txt", template)

        # 情形 A：输出目录尚不存在——运行后仍须不存在。
        out_absent = os.path.join(self.tmp, "out-absent")
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
        out_empty = os.path.join(self.tmp, "out-empty")
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

    def test_success_replacement_value_is_not_reparsed(self):
        # 姓名含 {{age}}{{name}}：两处 {{name}} 都替换为完整原始姓名，
        # 替换值中的占位符样式文本保持字面，CRLF 与其余文字逐字节一致。
        self._assert_success(
            CONTACTS_PLACEHOLDER_NAME,
            TEMPLATE_CRLF,
            PREVIEW_CRLF.encode("utf-8"),
        )

    def test_success_incomplete_placeholders_stay_literal(self):
        # 单花括号与未闭合文本不触发变量错误，预览与模板完全相同。
        self._assert_success(
            CONTACTS_PLACEHOLDER_NAME,
            TEMPLATE_LITERAL,
            TEMPLATE_LITERAL.encode("utf-8"),
        )

    def test_failure_unknown_variable_case_mismatch(self):
        # 占位符区分大小写：{{Name}} 不是 {{name}}。
        self._run_failure(
            CONTACTS_PLACEHOLDER_NAME,
            "你好，{{Name}}！\n",
            ["未知变量", "{{Name}}"],
        )

    def test_failure_unknown_variable_inner_spaces(self):
        # 花括号内侧的空格属于变量名的一部分：{{ name }} 不是 {{name}}。
        self._run_failure(
            CONTACTS_PLACEHOLDER_NAME,
            "你好，{{ name }}！\n",
            ["未知变量", "{{ name }}"],
        )

    def test_failure_empty_placeholder(self):
        # 空占位符 {{}} 同样是未知变量。
        self._run_failure(
            CONTACTS_PLACEHOLDER_NAME,
            "你好，{{}}！\n",
            ["未知变量", "{{}}"],
        )

    def test_failure_template_checked_even_without_match(self):
        # 联系人分组为 archive、筛选 newsletter 时零匹配，但模板校验
        # 不得因此被跳过：{{Name}} 仍报相同的校验失败。
        self._run_failure(
            CONTACTS_ARCHIVE_ONLY,
            "你好，{{Name}}！\n",
            ["未知变量", "{{Name}}"],
        )


if __name__ == "__main__":
    unittest.main()
