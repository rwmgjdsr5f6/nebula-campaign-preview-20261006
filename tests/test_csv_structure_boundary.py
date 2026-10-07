"""newsletter_preview 联系人 CSV 结构边界的回归测试。

验证公开允许的表头布局与带引号字段：必需列可以按任意顺序出现、允许
额外列（额外列中的占位符样式文本不进入模板校验）；双引号字段内的
逗号与内嵌 LF 换行按 CSV 规则作为姓名内容，替换后逐字节保留。
同时验证结构错误时的对外状态：缺少必需列、数据行字段数与表头不一致
均退出 2，标准错误给出列名或行号与字段数定位信息且不含未捕获异常的
Traceback，输出目录保持原状。仅依赖 Python 3 标准库，完全离线；
样例联系人为合成数据，邮箱使用 RFC 2606 保留的 example.invalid
虚构域名。

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

# 成功样例输入：表头为 segment,note,email,name——必需列以非规范顺序
# 出现，且带有额外列 note；note 各行均填写 {{age}}，用于确认额外列
# 内容不会进入模板校验。前两人同属 newsletter：姓名“甲,乙”含逗号、
# 姓名“丙\n丁”含内嵌 LF 换行，两个字段均按 CSV 规则用双引号包围；
# 第三人属 archive，不参与筛选。
CONTACTS_VALID = (
    "segment,note,email,name\n"
    "newsletter,{{age}},a@example.invalid,\"甲,乙\"\n"
    "newsletter,{{age}},b@example.invalid,\"丙\n丁\"\n"
    "archive,{{age}},c@example.invalid,戊\n"
)
TEMPLATE_VALID = "你好，{{name}}！\n"
SEGMENT = "newsletter"

# 预览按 CSV 原顺序替换姓名：字段内半角逗号、内嵌 LF 与模板末尾 LF
# 均逐字节保留。
PREVIEW_1 = "你好，甲,乙！\n"
PREVIEW_2 = "你好，丙\n丁！\n"

EXPECTED_REPORT = {
    "template": TEMPLATE_VALID,
    "segment": SEGMENT,
    "segment_count": 2,
    "excluded_count": 0,
    "excluded_contacts": [],
    "matched_count": 2,
    "previews": [
        {"email": "a@example.invalid", "file": "preview-0001.txt"},
        {"email": "b@example.invalid", "file": "preview-0002.txt"},
    ],
}

# 失败样例 1：从有效 CSV 删除 email 整列（表头与每行同步去掉该列），
# 其余内容保持合法。
CONTACTS_MISSING_EMAIL = (
    "segment,note,name\n"
    "newsletter,{{age}},\"甲,乙\"\n"
    "newsletter,{{age}},\"丙\n丁\"\n"
    "archive,{{age}},戊\n"
)

# 失败样例 2：最后一条 archive 记录少一个字段（3 个，表头为 4 个）。
# 因第二条记录的姓名含内嵌 LF，该记录位于物理第 5 行。
CONTACTS_LAST_ROW_TOO_FEW = (
    "segment,note,email,name\n"
    "newsletter,{{age}},a@example.invalid,\"甲,乙\"\n"
    "newsletter,{{age}},b@example.invalid,\"丙\n丁\"\n"
    "archive,{{age}},戊\n"
)

# 失败样例 3：最后一条 archive 记录多一个字段（5 个，表头为 4 个）。
CONTACTS_LAST_ROW_TOO_MANY = (
    "segment,note,email,name\n"
    "newsletter,{{age}},a@example.invalid,\"甲,乙\"\n"
    "newsletter,{{age}},b@example.invalid,\"丙\n丁\"\n"
    "archive,{{age}},c@example.invalid,戊,extra\n"
)


class CsvStructureBoundaryTestCase(unittest.TestCase):
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

    def test_success_reordered_header_extra_column_and_quoted_fields(self):
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

        # 目录恰好包含两份连续编号预览与 report.json，无多余文件；
        # archive 记录不生成预览，额外列 note 不影响模板校验。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "preview-0002.txt", "report.json"],
        )

        # 以二进制读取，逐字节核对引号内逗号、姓名内部 LF 与模板
        # 末尾 LF。
        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), PREVIEW_1.encode("utf-8"))
        with open(os.path.join(out_path, "preview-0002.txt"), "rb") as fh:
            self.assertEqual(fh.read(), PREVIEW_2.encode("utf-8"))

        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)

        # 报告保留模板原文与筛选值；清单按 CSV 顺序关联邮箱与文件。
        self.assertEqual(report, EXPECTED_REPORT)

    def test_failure_missing_email_column(self):
        # 删除 email 整列后：退出 2，标准错误点名缺失的必需列 email，
        # 无 Traceback，两种输出目录状态下均无任何输出。
        self._run_failure(
            CONTACTS_MISSING_EMAIL,
            TEMPLATE_VALID,
            ["缺少必需列", "email"],
        )

    def test_failure_last_row_too_few_fields(self):
        # 最后一条 archive 记录只有 3 个字段（表头 4 个），位于物理
        # 第 5 行（第二条记录的内嵌 LF 占一行）：标准错误须包含行号、
        # 实际字段数 3、表头字段数 4 与不一致说明。
        self._run_failure(
            CONTACTS_LAST_ROW_TOO_FEW,
            TEMPLATE_VALID,
            ["第 5 行", "字段数（3）", "表头字段数（4）", "不一致"],
        )

    def test_failure_last_row_too_many_fields(self):
        # 最后一条 archive 记录有 5 个字段（表头 4 个）：标准错误须
        # 包含行号、实际字段数 5、表头字段数 4 与不一致说明。
        self._run_failure(
            CONTACTS_LAST_ROW_TOO_MANY,
            TEMPLATE_VALID,
            ["第 5 行", "字段数（5）", "表头字段数（4）", "不一致"],
        )


if __name__ == "__main__":
    unittest.main()
