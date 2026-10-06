"""离线预览流程的端到端回归测试。

覆盖 README 记载的公开入口 ``python -m newsletter_preview``：

- 成功样例：核对真实落盘产物——两份连续编号的逐人文本预览（正文与
  换行逐字节准确）与 report.json（模板原文、筛选值、匹配人数、按 CSV
  顺序对应的邮箱与文件名，共享邮箱不合并）。
- 失败样例：模板含未知变量、CSV 缺少 segment 列、未匹配联系人的 name
  为空，均须以退出码 2 结束、stderr 含对应中文信息且无未捕获异常堆栈，
  并且不留下任何输出（尚不存在的目录仍不存在；已存在的空目录仍为空）。

仅依赖 Python 3 标准库；所有联系人、邮箱均为合成数据
（example.invalid 为 RFC 2606 保留域名），完全离线。
从项目根目录执行::

    python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

MODULE = "newsletter_preview"

# 项目根目录（本文件位于 <root>/tests/ 下）；子进程以此为 cwd，
# 保证从任何目录启动测试时都能解析到 newsletter_preview 包。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 成功样例输入：表头 name,email,segment；前两人 segment 为 newsletter
# 且共享同一邮箱，第三人为 archive。
CONTACTS_CSV = (
    "name,email,segment\n"
    "张若岚,shared@example.invalid,newsletter\n"
    "李望舒,shared@example.invalid,newsletter\n"
    "沈知遥,shen@example.invalid,archive\n"
)

# 模板两行，均以换行结束。
TEMPLATE_TXT = "你好，{{name}}！\n活动预览\n"

SEGMENT = "newsletter"

EXPECTED_PREVIEWS = [
    ("preview-0001.txt", "张若岚", "shared@example.invalid"),
    ("preview-0002.txt", "李望舒", "shared@example.invalid"),
]


def _write(path, content):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)


class PreviewFlowTests(unittest.TestCase):
    """通过公开命令行入口驱动，断言实际退出结果、文件内容与目录状态。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.contacts_path = os.path.join(self.tmpdir, "contacts.csv")
        self.template_path = os.path.join(self.tmpdir, "template.txt")
        _write(self.contacts_path, CONTACTS_CSV)
        _write(self.template_path, TEMPLATE_TXT)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, out_dir):
        """以公开入口 python -m newsletter_preview 执行，返回完整结果。"""
        return subprocess.run(
            [
                sys.executable,
                "-m",
                MODULE,
                "--contacts",
                self.contacts_path,
                "--template",
                self.template_path,
                "--segment",
                SEGMENT,
                "--out",
                out_dir,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=PROJECT_ROOT,
        )

    def test_success_creates_two_previews_and_report(self):
        """有效输入：退出 0，目录恰好含两份连续编号预览与 report.json。"""
        out_dir = os.path.join(self.tmpdir, "out")
        self.assertFalse(os.path.exists(out_dir))

        result = self._run(out_dir)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        # 成功路径不向 stderr 输出错误内容。
        self.assertEqual(result.stderr, "")

        self.assertTrue(os.path.isdir(out_dir))
        entries = sorted(os.listdir(out_dir))
        self.assertEqual(
            entries,
            ["preview-0001.txt", "preview-0002.txt", "report.json"],
        )

        # 逐人预览按 CSV 顺序使用各自姓名，正文与换行逐字节准确。
        for filename, name, _email in EXPECTED_PREVIEWS:
            with open(os.path.join(out_dir, filename), "r", encoding="utf-8",
                      newline="") as fh:
                content = fh.read()
            self.assertEqual(content, f"你好，{name}！\n活动预览\n")

        # 报告保留模板原文与筛选值，计数为 2，预览清单顺序与邮箱对应；
        # 共享邮箱不得合并为一条记录。
        with open(os.path.join(out_dir, "report.json"), "r",
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["template"], TEMPLATE_TXT)
        self.assertEqual(report["segment"], SEGMENT)
        self.assertEqual(report["matched_count"], 2)
        self.assertEqual(
            report["previews"],
            [
                {"email": email, "file": filename}
                for filename, _name, email in EXPECTED_PREVIEWS
            ],
        )

    def _assert_validation_failure(self, out_dir, expected_fragments):
        """通用失败断言：退出 2、stderr 含指定信息、无堆栈、无任何产物。"""
        out_existed_before = os.path.exists(out_dir)
        if out_existed_before:
            self.assertTrue(os.path.isdir(out_dir))
            before = sorted(os.listdir(out_dir))
            self.assertEqual(before, [])

        result = self._run(out_dir)

        self.assertEqual(
            result.returncode,
            2,
            msg=f"stdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        stderr = result.stderr
        for fragment in expected_fragments:
            self.assertIn(fragment, stderr)
        # 输入校验失败须被捕获并给出中文错误，不得出现未捕获异常堆栈。
        self.assertNotIn("Traceback (most recent call last)", stderr)

        if out_existed_before:
            # 预先存在的空目录必须仍然为空。
            self.assertTrue(os.path.isdir(out_dir))
            self.assertEqual(sorted(os.listdir(out_dir)), [])
        else:
            # 尚不存在的输出目录必须保持不存在。
            self.assertFalse(os.path.exists(out_dir))

    def test_failure_unknown_template_variable_creates_nothing(self):
        """模板增加 {{age}}：退出 2，stderr 指出未知变量 age，无输出。"""
        _write(self.template_path, "你好，{{name}}（{{age}}）！\n活动预览\n")
        self._assert_validation_failure(
            os.path.join(self.tmpdir, "out-absent"),
            ["未知变量", "age"],
        )

    def test_failure_missing_segment_column_creates_nothing(self):
        """CSV 删除 segment 整列：退出 2，stderr 指出缺失列 segment。"""
        _write(
            self.contacts_path,
            "name,email\n"
            "张若岚,shared@example.invalid\n"
            "李望舒,shared@example.invalid\n"
            "沈知遥,shen@example.invalid\n",
        )
        self._assert_validation_failure(
            os.path.join(self.tmpdir, "out-absent"),
            ["缺少必需列", "segment"],
        )

    def test_failure_empty_name_on_archived_row_creates_nothing(self):
        """未匹配的 archive 联系人 name 留空：退出 2，指出第 4 行 name。"""
        _write(
            self.contacts_path,
            "name,email,segment\n"
            "张若岚,shared@example.invalid,newsletter\n"
            "李望舒,shared@example.invalid,newsletter\n"
            ",shen@example.invalid,archive\n",
        )
        # CSV 表头为物理第 1 行，末条记录为物理第 4 行。
        self._assert_validation_failure(
            os.path.join(self.tmpdir, "out-absent"),
            ["第 4 行", "name"],
        )

    def test_failure_leaves_preexisting_empty_dir_untouched(self):
        """输出目录预先存在且为空：校验失败后仍为空（以未知变量为例）。"""
        _write(self.template_path, "你好，{{name}}，{{age}}\n")
        out_dir = os.path.join(self.tmpdir, "out-existing")
        os.mkdir(out_dir)
        self._assert_validation_failure(out_dir, ["未知变量", "age"])


if __name__ == "__main__":
    unittest.main()
