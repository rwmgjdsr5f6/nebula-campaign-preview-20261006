"""newsletter_preview 联系人 CSV 结构边界的回归测试。

验证公开允许的表头布局（列顺序不限、允许额外列）与按 CSV 规则带引号
的字段（字段内逗号、字段内 LF 换行）被正确解析；额外列中的占位符样式
文本不进入模板校验。结构错误（缺少必需列、记录字段数与表头不一致）时
退出码为 2，标准错误给出可读诊断，且不产生任何输出。仅依赖 Python 3
标准库，完全离线；样例联系人为合成数据，邮箱使用 RFC 2606 保留的
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

# 成功样例输入：表头为 segment,note,email,name——列顺序与既有样例不同
# 且含额外列 note，均为公开允许的表头布局。前两条记录同属 newsletter：
# 甲的姓名含逗号，乙的姓名含一个 LF 换行，两个姓名字段均按 CSV 规则用
# 双引号包围。第三条记录属于 archive，不参与匹配。note 列各行填写
# {{age}}，用于确认额外列不会进入模板校验。
CONTACTS_VALID = (
    "segment,note,email,name\n"
    "newsletter,{{age}},a@example.invalid,\"甲,乙\"\n"
    "newsletter,{{age}},b@example.invalid,\"丙\n丁\"\n"
    "archive,{{age}},c@example.invalid,戊\n"
)
# 模板以单个 LF 结束。
TEMPLATE_VALID = "你好，{{name}}！\n"
SEGMENT = "newsletter"

# 预览按 CSV 原顺序替换姓名：逗号、姓名内部换行与模板末尾换行逐字节保留。
PREVIEW_1 = "你好，甲,乙！\n"
PREVIEW_2 = "你好，丙\n丁！\n"

# 报告内容（与 json.load 结果逐项相等比较）：保留模板原文与筛选值，
# 清单按顺序关联邮箱与文件。
REPORT_VALID = {
    "template": TEMPLATE_VALID,
    "segment": SEGMENT,
    "matched_count": 2,
    "previews": [
        {"email": "a@example.invalid", "file": "preview-0001.txt"},
        {"email": "b@example.invalid", "file": "preview-0002.txt"},
    ],
}

# 失败样例一：与成功输入相比仅删除 email 整列，其余行保持合法。
CONTACTS_MISSING_EMAIL = (
    "segment,note,name\n"
    "newsletter,{{age}},\"甲,乙\"\n"
    "newsletter,{{age}},\"丙\n丁\"\n"
    "archive,{{age}},戊\n"
)

# 失败样例二：与成功输入相比仅最后一条 archive 记录少一个字段
# （表头 4 列，该记录 3 列，位于文件第 5 行）。
CONTACTS_ROW_TOO_NARROW = (
    "segment,note,email,name\n"
    "newsletter,{{age}},a@example.invalid,\"甲,乙\"\n"
    "newsletter,{{age}},b@example.invalid,\"丙\n丁\"\n"
    "archive,{{age}},c@example.invalid\n"
)

# 失败样例三：与成功输入相比仅最后一条 archive 记录多一个字段
# （表头 4 列，该记录 5 列，位于文件第 5 行）。
CONTACTS_ROW_TOO_WIDE = (
    "segment,note,email,name\n"
    "newsletter,{{age}},a@example.invalid,\"甲,乙\"\n"
    "newsletter,{{age}},b@example.invalid,\"丙\n丁\"\n"
    "archive,{{age}},c@example.invalid,戊,额外\n"
)


class CsvStructureTestCase(unittest.TestCase):
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

    def _assert_success_outputs(self, out_path):
        """核对成功运行的输出目录：恰好两份预览与报告，内容逐项一致。"""
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "preview-0002.txt", "report.json"],
        )
        with open(
            os.path.join(out_path, "preview-0001.txt"), encoding="utf-8"
        ) as fh:
            self.assertEqual(fh.read(), PREVIEW_1)
        with open(
            os.path.join(out_path, "preview-0002.txt"), encoding="utf-8"
        ) as fh:
            self.assertEqual(fh.read(), PREVIEW_2)
        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), REPORT_VALID)

    def _run_failure(self, contacts, fragments):
        """同一组失败输入，分别核对两种输出目录状态下的“无输出”约定。"""
        contacts_path = self._write("contacts.csv", contacts)
        template_path = self._write("template.txt", TEMPLATE_VALID)

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

    def test_success_quoted_fields_and_extra_column(self):
        contacts_path = self._write("contacts.csv", CONTACTS_VALID)
        template_path = self._write("template.txt", TEMPLATE_VALID)
        out_path = os.path.join(self.tmp, "out")

        result = self._run(contacts_path, template_path, out_path)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stderr, b"")
        self._assert_success_outputs(out_path)

    def test_success_is_repeatable(self):
        # 同样输入在独立输出目录重复执行，退出码与落盘结果完全一致。
        contacts_path = self._write("contacts.csv", CONTACTS_VALID)
        template_path = self._write("template.txt", TEMPLATE_VALID)

        for name in ("out-first", "out-second"):
            out_path = os.path.join(self.tmp, name)
            result = self._run(contacts_path, template_path, out_path)
            self.assertEqual(
                result.returncode,
                0,
                msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
            )
            self.assertEqual(result.stderr, b"")
            self._assert_success_outputs(out_path)

    def test_failure_missing_email_column(self):
        self._run_failure(
            CONTACTS_MISSING_EMAIL,
            ["缺少必需列", "email"],
        )

    def test_failure_row_field_count_too_few(self):
        self._run_failure(
            CONTACTS_ROW_TOO_NARROW,
            ["第 5 行", "字段数（3）", "表头字段数（4）", "不一致"],
        )

    def test_failure_row_field_count_too_many(self):
        self._run_failure(
            CONTACTS_ROW_TOO_WIDE,
            ["第 5 行", "字段数（5）", "表头字段数（4）", "不一致"],
        )


if __name__ == "__main__":
    unittest.main()
