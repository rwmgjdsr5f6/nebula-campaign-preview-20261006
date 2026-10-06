"""newsletter_preview 输出路径保护的回归测试。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出、标准错误、落盘文件内容与输出目录状态；
不直接调用内部函数。每个场景使用独立临时目录，结束后自动清理，
不依赖权限修改与外部服务。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 固定样例输入：表头 name,email,segment；甲匹配 newsletter，乙为 archive。
CONTACTS = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,b@example.invalid,archive\n"
)
# 模板以单个 LF 结尾。
TEMPLATE = "你好，{{name}}！\n"
SEGMENT = "newsletter"

# 唯一匹配联系人的预览正文，保留模板末尾 LF。
PREVIEW_1 = "你好，甲！\n"

KEEP_CONTENT = "既有内容，不得改动。\n"


class OutputPathProtectionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS)
        self.template_path = self._write("template.txt", TEMPLATE)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _run(self, out_path):
        """以固定样例输入通过公开入口运行，返回 CompletedProcess。"""
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
                SEGMENT,
                "--out",
                out_path,
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _snapshot(self, root):
        """记录目录树中所有条目的相对路径、类型与文件字节内容。"""
        snapshot = {}
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames:
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                snapshot[rel] = ("dir", None)
            for name in filenames:
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, root)
                with open(path, "rb") as fh:
                    snapshot[rel] = ("file", fh.read())
        return snapshot

    def _assert_rejected_nonempty_dir(self, out_path, before):
        """非空目录拒绝后的公共断言：退出码、输出流与目录内容不变。"""
        result = self._run(out_path)
        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        stderr = result.stderr.decode("utf-8")
        self.assertIn("输出目录非空", stderr)
        self.assertIn("拒绝覆盖", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        # 原目录条目与文件字节保持不变，不新增预览或报告。
        self.assertEqual(self._snapshot(out_path), before)

    def test_empty_writable_dir_accepts_output(self):
        out_path = os.path.join(self.tmp, "out")
        os.mkdir(out_path)

        result = self._run(out_path)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

        # 目录恰好包含一份预览与 report.json，无多余文件。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )

        with open(
            os.path.join(out_path, "preview-0001.txt"), encoding="utf-8"
        ) as fh:
            self.assertEqual(fh.read(), PREVIEW_1)

        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)

        # 报告保留模板原文与筛选值。
        self.assertEqual(report["template"], TEMPLATE)
        self.assertEqual(report["segment"], SEGMENT)
        self.assertEqual(report["matched_count"], 1)
        self.assertEqual(
            report["previews"],
            [{"email": "a@example.invalid", "file": "preview-0001.txt"}],
        )

    def test_dir_with_existing_file_is_rejected(self):
        out_path = os.path.join(self.tmp, "out")
        os.mkdir(out_path)
        keep_path = os.path.join(out_path, "keep.txt")
        with open(keep_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(KEEP_CONTENT)

        before = self._snapshot(out_path)
        self._assert_rejected_nonempty_dir(out_path, before)

        # keep.txt 字节内容逐位不变。
        with open(keep_path, "rb") as fh:
            self.assertEqual(fh.read(), KEEP_CONTENT.encode("utf-8"))

    def test_dir_with_only_empty_subdir_is_rejected(self):
        out_path = os.path.join(self.tmp, "out")
        os.mkdir(out_path)
        os.mkdir(os.path.join(out_path, "subdir"))

        before = self._snapshot(out_path)
        self._assert_rejected_nonempty_dir(out_path, before)

        # 空子目录仍然存在且仍为空。
        subdir = os.path.join(out_path, "subdir")
        self.assertTrue(os.path.isdir(subdir))
        self.assertEqual(os.listdir(subdir), [])

    def test_existing_regular_file_as_out_path_is_rejected(self):
        out_path = self._write("out", KEEP_CONTENT)
        before = self._snapshot(self.tmp)

        result = self._run(out_path)

        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        stderr = result.stderr.decode("utf-8")
        self.assertIn("不是目录", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)

        # 该路径仍是普通文件，内容保持不变；临时目录内无其他变化。
        self.assertTrue(os.path.isfile(out_path))
        self.assertEqual(self._snapshot(self.tmp), before)

    def test_reusing_same_out_dir_is_rejected_without_changes(self):
        out_path = os.path.join(self.tmp, "out")

        first = self._run(out_path)
        self.assertEqual(
            first.returncode,
            0,
            msg=f"stderr: {first.stderr.decode('utf-8', 'replace')}",
        )
        before = self._snapshot(out_path)

        # 相同输入、相同输出目录再次调用：按非空目录规则拒绝。
        second = self._run(out_path)
        self.assertEqual(
            second.returncode,
            2,
            msg=f"stderr: {second.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(second.stdout, b"")
        stderr = second.stderr.decode("utf-8")
        self.assertIn("输出目录非空", stderr)
        self.assertIn("拒绝覆盖", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)

        # 第一次生成的全部文件名称与字节内容保持一致，不追加文件。
        self.assertEqual(self._snapshot(out_path), before)


if __name__ == "__main__":
    unittest.main()
