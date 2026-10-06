"""newsletter_preview --exclude-email 受众排除的回归测试。

验证可重复的 --exclude-email 按 email 原文精确排除（区分大小写、
不修剪空白）、共享邮箱全部排除而其他重复邮箱各自保留、保留记录按
CSV 顺序从 preview-0001.txt 连续编号、全部被排除时仅生成计数为 0
的报告，以及参数与输入校验失败时退出 2 且不留下输出。仅依赖 Python 3
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

# 验收样例：甲、乙、丙同属 newsletter；甲乙共用 a@example.invalid。
CONTACTS_SHARED = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,a@example.invalid,newsletter\n"
    "丙,b@example.invalid,newsletter\n"
)
# 中间保留样例：被排除记录夹在保留记录之间，用于核对编号连续无空号。
CONTACTS_INTERLEAVED = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,b@example.invalid,newsletter\n"
    "丙,c@example.invalid,newsletter\n"
    "丁,a@example.invalid,newsletter\n"
)
TEMPLATE = "你好，{{name}}！\n"
SEGMENT = "newsletter"
EMAIL_A = "a@example.invalid"
EMAIL_B = "b@example.invalid"

PREVIEW_BING = "你好，丙！\n"


class ExcludeEmailTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS_SHARED)
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
            argv,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def test_shared_email_excluded_other_preview_kept(self):
        # 验收样例：排除 a@example.invalid 后共享该邮箱的甲、乙两条记录
        # 全部移除；丙保留并编号 0001，报告只列 b@example.invalid。
        out_path = os.path.join(self.tmp, "previews")
        result = self._run(
            out_path, ["--exclude-email", EMAIL_A]
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )
        with open(os.path.join(out_path, "preview-0001.txt"),
                  encoding="utf-8", newline="") as fh:
            # 姓名替换正确，模板原文与末尾 LF 保留。
            self.assertEqual(fh.read(), PREVIEW_BING)
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["matched_count"], 1)
        self.assertEqual(
            report["previews"],
            [{"email": EMAIL_B, "file": "preview-0001.txt"}],
        )

    def test_numbering_stays_contiguous_with_interleaved_exclusions(self):
        # 被排除记录夹在保留记录之间：乙、丙保留，编号仍从 0001 连续，
        # 不因甲、丁被排除而留下空号。
        contacts_path = self._write("interleaved.csv", CONTACTS_INTERLEAVED)
        out_path = os.path.join(self.tmp, "out")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_A],
            contacts_path=contacts_path,
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "preview-0002.txt", "report.json"],
        )
        with open(os.path.join(out_path, "preview-0001.txt"),
                  encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "你好，乙！\n")
        with open(os.path.join(out_path, "preview-0002.txt"),
                  encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "你好，丙！\n")
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["matched_count"], 2)
        self.assertEqual(
            report["previews"],
            [
                {"email": EMAIL_B, "file": "preview-0001.txt"},
                {"email": "c@example.invalid", "file": "preview-0002.txt"},
            ],
        )

    def test_all_excluded_leaves_zero_report_only(self):
        # 全部匹配记录被排除：退出 0，目录仅 report.json，计数 0、清单空。
        out_path = os.path.join(self.tmp, "out-all")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_A, "--exclude-email", EMAIL_B],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(os.listdir(out_path), ["report.json"])
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["matched_count"], 0)
        self.assertEqual(report["previews"], [])

    def test_non_matching_exclude_value_is_not_an_error(self):
        # 排除值不属于匹配受众：不报错，行为与不提供排除参数一致。
        out_path = os.path.join(self.tmp, "out-nomatch")
        result = self._run(
            out_path, ["--exclude-email", "nobody@example.invalid"]
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["matched_count"], 3)

    def test_comparison_is_case_and_space_sensitive(self):
        # 大小写不同或带首尾空格的排除值不匹配任何记录；重复提供同一值
        # 不叠加效果（排除结果与提供一次相同）。
        out_path = os.path.join(self.tmp, "out-case")
        result = self._run(
            out_path,
            [
                "--exclude-email", "A@EXAMPLE.INVALID",
                "--exclude-email", " " + EMAIL_A,
                "--exclude-email", "A@EXAMPLE.INVALID",
            ],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["matched_count"], 3)

    def test_empty_or_blank_exclude_value_exits_2_without_output(self):
        # 空字符串与仅含空白均为参数错误：退出 2，stderr 含
        # --exclude-email、无 Traceback；不存在的目录不创建，
        # 已存在的空目录保持为空。
        for bad_value in ("", "   ", "\t"):
            out_absent = os.path.join(self.tmp, f"absent-{bad_value!r}")
            result = self._run(
                out_absent, ["--exclude-email", bad_value]
            )
            self.assertEqual(result.returncode, 2, msg=repr(bad_value))
            stderr = result.stderr.decode("utf-8")
            self.assertIn("--exclude-email", stderr, msg=repr(bad_value))
            self.assertNotIn("Traceback (most recent call last)", stderr)
            self.assertFalse(os.path.exists(out_absent),
                             msg=repr(bad_value))

            out_empty = os.path.join(self.tmp, f"empty-{bad_value!r}")
            os.mkdir(out_empty)
            result = self._run(
                out_empty, ["--exclude-email", bad_value]
            )
            self.assertEqual(result.returncode, 2, msg=repr(bad_value))
            stderr = result.stderr.decode("utf-8")
            self.assertIn("--exclude-email", stderr, msg=repr(bad_value))
            self.assertNotIn("Traceback (most recent call last)", stderr)
            self.assertEqual(os.listdir(out_empty), [], msg=repr(bad_value))

    def test_missing_exclude_value_exits_2(self):
        # --exclude-email 后缺少值：argparse 报错退出 2，stderr 含
        # --exclude-email 且无 Traceback，不创建输出目录。
        out_path = os.path.join(self.tmp, "out-missing")
        result = self._run(out_path, ["--exclude-email"])

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("--exclude-email", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_path))

    def test_excluded_rows_are_still_fully_validated(self):
        # 即使问题记录的邮箱恰好被排除，CSV 完整校验仍不得跳过：
        # 丙（b@example.invalid）name 为空，排除 b 仍须退出 2。
        contacts = (
            "name,email,segment\n"
            "甲,a@example.invalid,newsletter\n"
            ",b@example.invalid,newsletter\n"
        )
        contacts_path = self._write("bad-contacts.csv", contacts)
        out_absent = os.path.join(self.tmp, "out-bad")
        result = self._run(
            out_absent,
            ["--exclude-email", EMAIL_B],
            contacts_path=contacts_path,
        )

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("第 3 行", stderr)
        self.assertIn("name", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))


if __name__ == "__main__":
    unittest.main()
