"""newsletter_preview 既有命令行流程的回归测试。

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

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 成功样例输入：表头 name,email,segment；前两人同属 newsletter 且共享
# 同一邮箱（共享邮箱不得导致记录合并），第三人为不匹配的 archive。
CONTACTS_VALID = (
    "name,email,segment\n"
    "张若岚,shared@example.invalid,newsletter\n"
    "李望舒,shared@example.invalid,newsletter\n"
    "沈知遥,shen@example.invalid,archive\n"
)
TEMPLATE_VALID = "你好，{{name}}！\n活动预览\n"
SEGMENT = "newsletter"

PREVIEW_1 = "你好，张若岚！\n活动预览\n"
PREVIEW_2 = "你好，李望舒！\n活动预览\n"


class PreviewFlowTestCase(unittest.TestCase):
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

    def test_success_generates_previews_and_report(self):
        contacts_path = self._write("contacts.csv", CONTACTS_VALID)
        template_path = self._write("template.txt", TEMPLATE_VALID)
        out_path = os.path.join(self.tmp, "out")

        result = self._run(contacts_path, template_path, out_path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")

        # 目录恰好包含两份连续编号预览与 report.json，无多余文件。
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
            report = json.load(fh)

        # 报告保留模板原文与筛选值。
        self.assertEqual(report["template"], TEMPLATE_VALID)
        self.assertEqual(report["segment"], SEGMENT)
        self.assertEqual(report["matched_count"], 2)
        # 按 CSV 顺序对应邮箱与文件名；共享邮箱保留为两条独立记录。
        self.assertEqual(
            report["previews"],
            [
                {
                    "email": "shared@example.invalid",
                    "file": "preview-0001.txt",
                },
                {
                    "email": "shared@example.invalid",
                    "file": "preview-0002.txt",
                },
            ],
        )

    def test_failure_unknown_template_variable(self):
        # 与成功输入相比仅一处变化：模板增加未知变量 {{age}}。
        template = "你好，{{name}}！\n{{age}}\n活动预览\n"
        self._run_failure(
            CONTACTS_VALID,
            template,
            ["未知变量", "age"],
        )

    def test_failure_missing_segment_column(self):
        # 与成功输入相比仅一处变化：CSV 删除 segment 整列。
        contacts = (
            "name,email\n"
            "张若岚,shared@example.invalid\n"
            "李望舒,shared@example.invalid\n"
            "沈知遥,shen@example.invalid\n"
        )
        self._run_failure(
            contacts,
            TEMPLATE_VALID,
            ["缺少", "列", "segment"],
        )

    def test_failure_blank_name_even_for_unmatched_contact(self):
        # 与成功输入相比仅一处变化：未匹配的 archive 联系人 name 留空
        # （表头第 1 行，该记录为文件第 4 行）。
        contacts = (
            "name,email,segment\n"
            "张若岚,shared@example.invalid,newsletter\n"
            "李望舒,shared@example.invalid,newsletter\n"
            ",shen@example.invalid,archive\n"
        )
        self._run_failure(
            contacts,
            TEMPLATE_VALID,
            ["第 4 行", "name", "空"],
        )


if __name__ == "__main__":
    unittest.main()
