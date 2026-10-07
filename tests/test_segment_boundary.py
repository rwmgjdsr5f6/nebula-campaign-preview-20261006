"""newsletter_preview 受众筛选边界的回归测试。

验证 segment 筛选按原文精确匹配（区分大小写、不去除空格）、预览编号
不因未匹配记录留空号，以及零匹配时仍生成报告。仅依赖 Python 3 标准库，
完全离线；样例联系人为合成数据，邮箱使用 RFC 2606 保留的
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

# 边界样例输入：五人共享同一邮箱（共享邮箱不得导致记录合并），
# segment 值仅在大小写或首尾空格上与 newsletter 有所差异：
# 甲、戊为精确值 newsletter；乙首字母大写；丙前导空格；丁尾随空格。
CONTACTS_BOUNDARY = (
    "name,email,segment\n"
    "甲,shared@example.invalid,newsletter\n"
    "乙,shared@example.invalid,Newsletter\n"
    "丙,shared@example.invalid, newsletter\n"
    "丁,shared@example.invalid,newsletter \n"
    "戊,shared@example.invalid,newsletter\n"
)
TEMPLATE = "你好，{{name}}！\n"
EMAIL = "shared@example.invalid"

SEGMENT_EXACT = "newsletter"
SEGMENT_TRAILING_SPACE = "newsletter "
SEGMENT_ABSENT = "archive"

PREVIEW_JIA = "你好，甲！\n"
PREVIEW_DING = "你好，丁！\n"
PREVIEW_WU = "你好，戊！\n"

# 各场景的报告内容（与 json.load 结果逐项相等比较）。
REPORT_EXACT = {
    "template": TEMPLATE,
    "segment": SEGMENT_EXACT,
    "segment_count": 2,
    "excluded_count": 0,
    "excluded_contacts": [],
    "matched_count": 2,
    "previews": [
        {"email": EMAIL, "file": "preview-0001.txt"},
        {"email": EMAIL, "file": "preview-0002.txt"},
    ],
}
REPORT_TRAILING_SPACE = {
    "template": TEMPLATE,
    "segment": SEGMENT_TRAILING_SPACE,
    "segment_count": 1,
    "excluded_count": 0,
    "excluded_contacts": [],
    "matched_count": 1,
    "previews": [
        {"email": EMAIL, "file": "preview-0001.txt"},
    ],
}
REPORT_ZERO_MATCH = {
    "template": TEMPLATE,
    "segment": SEGMENT_ABSENT,
    "segment_count": 0,
    "excluded_count": 0,
    "excluded_contacts": [],
    "matched_count": 0,
    "previews": [],
}


class SegmentBoundaryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS_BOUNDARY)
        self.template_path = self._write("template.txt", TEMPLATE)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _run(self, segment, out_path):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "newsletter_preview",
                "--contacts",
                self.contacts_path,
                "--template",
                self.template_path,
                "--segment",
                segment,
                "--out",
                out_path,
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _assert_run_ok(self, result):
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stderr, b"")

    def _assert_out_dir(self, out_path, expected_names, expected_previews,
                        expected_report):
        """核对输出目录恰好包含预期文件，且预览正文与报告逐项一致。"""
        self.assertEqual(sorted(os.listdir(out_path)), expected_names)
        for filename, body in expected_previews:
            with open(
                os.path.join(out_path, filename), encoding="utf-8"
            ) as fh:
                self.assertEqual(fh.read(), body, msg=filename)
        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), expected_report)

    def test_exact_match_is_case_and_space_sensitive(self):
        # 筛选 newsletter：仅甲、戊入选；大小写不同（乙）或含空格
        # （丙、丁）的记录不得匹配。编号从 0001 连续，不因中间未匹配
        # 记录留空号。
        out_path = os.path.join(self.tmp, "out")
        result = self._run(SEGMENT_EXACT, out_path)

        self._assert_run_ok(result)
        self._assert_out_dir(
            out_path,
            ["preview-0001.txt", "preview-0002.txt", "report.json"],
            [
                ("preview-0001.txt", PREVIEW_JIA),
                ("preview-0002.txt", PREVIEW_WU),
            ],
            REPORT_EXACT,
        )

    def test_trailing_space_segment_matches_only_exact_record(self):
        # 筛选值带尾随空格时按原文精确匹配：仅丁入选，只生成
        # 编号 0001 的一份预览；报告筛选值保留空格。
        out_path = os.path.join(self.tmp, "out")
        result = self._run(SEGMENT_TRAILING_SPACE, out_path)

        self._assert_run_ok(result)
        self._assert_out_dir(
            out_path,
            ["preview-0001.txt", "report.json"],
            [("preview-0001.txt", PREVIEW_DING)],
            REPORT_TRAILING_SPACE,
        )

    def test_zero_match_creates_missing_out_dir(self):
        # 零匹配且输出目录尚不存在：目录应被创建，只写入 report.json，
        # 没有任何预览文件。
        out_path = os.path.join(self.tmp, "out-absent")
        result = self._run(SEGMENT_ABSENT, out_path)

        self._assert_run_ok(result)
        self.assertTrue(os.path.isdir(out_path))
        self._assert_out_dir(
            out_path,
            ["report.json"],
            [],
            REPORT_ZERO_MATCH,
        )

    def test_zero_match_uses_existing_empty_out_dir(self):
        # 零匹配且输出目录已存在但为空：目录保留，只写入 report.json，
        # 没有任何预览文件。
        out_path = os.path.join(self.tmp, "out-empty")
        os.mkdir(out_path)
        result = self._run(SEGMENT_ABSENT, out_path)

        self._assert_run_ok(result)
        self._assert_out_dir(
            out_path,
            ["report.json"],
            [],
            REPORT_ZERO_MATCH,
        )


if __name__ == "__main__":
    unittest.main()
