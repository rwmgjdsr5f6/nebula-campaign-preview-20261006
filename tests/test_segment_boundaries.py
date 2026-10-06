"""segment 受众筛选边界的回归测试。

仅依赖 Python 3 标准库，完全离线；联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准错误、落盘文件内容与报告；不直接调用内部函数。
每个样例使用独立临时目录，结束后自动清理。

固定样例在 segment 列上刻意制造边界差异，验证程序按原文精确匹配：
甲、戊为 newsletter；乙为首字母大写 Newsletter；丙为前导空格
" newsletter"；丁为尾随空格 "newsletter "。任何大小写归一化或
输入空格修剪都会改变下列用例声明的结果。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 五名联系人的 segment 值仅在大小写或首尾空格上有区别；邮箱全部相同，
# 用以同时验证共享邮箱不会导致记录合并。
CONTACTS_BOUNDARY = (
    "name,email,segment\n"
    "甲,shared@example.invalid,newsletter\n"
    "乙,shared@example.invalid,Newsletter\n"
    "丙,shared@example.invalid, newsletter\n"
    "丁,shared@example.invalid,newsletter \n"
    "戊,shared@example.invalid,newsletter\n"
)
TEMPLATE_BOUNDARY = "你好，{{name}}！\n"

SEGMENT_EXACT = "newsletter"
SEGMENT_TRAILING_SPACE = "newsletter "
SEGMENT_UNMATCHED = "archive"

PREVIEW_JIA = "你好，甲！\n"
PREVIEW_DING = "你好，丁！\n"
PREVIEW_WU = "你好，戊！\n"

SHARED_EMAIL = "shared@example.invalid"


class SegmentBoundaryTestCase(unittest.TestCase):
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

    def _run(self, contacts_path, template_path, segment, out_path):
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

    def _read_report(self, out_path):
        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_exact_value_matches_only_jia_and_wu(self):
        # newsletter 原文精确匹配：仅甲、戊入选；大小写不同的乙、带首尾
        # 空格的丙、丁均不得入选。
        contacts_path = self._write("contacts.csv", CONTACTS_BOUNDARY)
        template_path = self._write("template.txt", TEMPLATE_BOUNDARY)
        out_path = os.path.join(self.tmp, "out")

        result = self._run(
            contacts_path, template_path, SEGMENT_EXACT, out_path
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")

        # 目录恰好包含两份从 0001 起连续编号的预览与 report.json；
        # 未匹配记录不得造成编号留空（如 preview-0005.txt）或多余文件。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "preview-0002.txt", "report.json"],
        )

        with open(
            os.path.join(out_path, "preview-0001.txt"), encoding="utf-8"
        ) as fh:
            self.assertEqual(fh.read(), PREVIEW_JIA)
        with open(
            os.path.join(out_path, "preview-0002.txt"), encoding="utf-8"
        ) as fh:
            self.assertEqual(fh.read(), PREVIEW_WU)

        # 报告完整固定：模板原文、筛选值、人数与按 CSV 顺序的预览清单。
        self.assertEqual(
            self._read_report(out_path),
            {
                "template": TEMPLATE_BOUNDARY,
                "segment": SEGMENT_EXACT,
                "matched_count": 2,
                "previews": [
                    {"email": SHARED_EMAIL, "file": "preview-0001.txt"},
                    {"email": SHARED_EMAIL, "file": "preview-0002.txt"},
                ],
            },
        )

    def test_trailing_space_value_matches_only_ding(self):
        # 筛选值 "newsletter "（尾随一个空格）必须原样参与比较：
        # 仅丁的分组值带尾随空格，甲、戊的无空格值不得匹配；筛选值在
        # 报告中也必须保留空格，不得被修剪。
        contacts_path = self._write("contacts.csv", CONTACTS_BOUNDARY)
        template_path = self._write("template.txt", TEMPLATE_BOUNDARY)
        out_path = os.path.join(self.tmp, "out")

        result = self._run(
            contacts_path, template_path, SEGMENT_TRAILING_SPACE, out_path
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")

        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )

        with open(
            os.path.join(out_path, "preview-0001.txt"), encoding="utf-8"
        ) as fh:
            self.assertEqual(fh.read(), PREVIEW_DING)

        self.assertEqual(
            self._read_report(out_path),
            {
                "template": TEMPLATE_BOUNDARY,
                "segment": SEGMENT_TRAILING_SPACE,
                "matched_count": 1,
                "previews": [
                    {"email": SHARED_EMAIL, "file": "preview-0001.txt"},
                ],
            },
        )

    def _assert_zero_match_report(self, out_path):
        # 零匹配：目录中只有 report.json，不得有任何 preview-*.txt；
        # 报告仍须完整保留模板原文与筛选值。
        self.assertEqual(os.listdir(out_path), ["report.json"])
        self.assertEqual(
            self._read_report(out_path),
            {
                "template": TEMPLATE_BOUNDARY,
                "segment": SEGMENT_UNMATCHED,
                "matched_count": 0,
                "previews": [],
            },
        )

    def test_zero_match_creates_missing_output_dir_and_writes_report(self):
        # 情形 A：输出目录尚不存在——零匹配也应创建目录并写入报告。
        contacts_path = self._write("contacts.csv", CONTACTS_BOUNDARY)
        template_path = self._write("template.txt", TEMPLATE_BOUNDARY)
        out_path = os.path.join(self.tmp, "out-absent")
        self.assertFalse(os.path.exists(out_path))

        result = self._run(
            contacts_path, template_path, SEGMENT_UNMATCHED, out_path
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertTrue(os.path.isdir(out_path))
        self._assert_zero_match_report(out_path)

    def test_zero_match_keeps_existing_empty_output_dir(self):
        # 情形 B：输出目录已存在且为空——目录保留，报告照常写入，
        # 不产生任何预览文件。
        contacts_path = self._write("contacts.csv", CONTACTS_BOUNDARY)
        template_path = self._write("template.txt", TEMPLATE_BOUNDARY)
        out_path = os.path.join(self.tmp, "out-empty")
        os.mkdir(out_path)

        result = self._run(
            contacts_path, template_path, SEGMENT_UNMATCHED, out_path
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertTrue(os.path.isdir(out_path))
        self._assert_zero_match_report(out_path)


if __name__ == "__main__":
    unittest.main()
