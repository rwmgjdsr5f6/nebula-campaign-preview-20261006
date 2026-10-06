"""newsletter_preview 输入读取失败（缺失文件、非法 UTF-8）的回归测试。

固定合成样例：contacts.csv 表头为 name,email,segment，唯一数据行为
甲,a@example.invalid,newsletter，两行均以 LF 结束；template.txt 内容为
“你好，{{name}}！”并以一个 LF 结束。先用这两个文件跑通 README 记载的
公开命令作为有效对照，再每次只改变一个输入：联系人路径不存在、模板
路径不存在、联系人或模板文件仅含一个非法 UTF-8 字节 0xFF（另一个文件
保持有效）。所有失败样例均须在产生任何预览之前被拒绝：退出码 2、
标准输出为空、标准错误给出可读提示且不含 Traceback；输出目录尚不存在
时仍不存在，已存在且为空时仍为空，两个输入文件的已有字节保持不变。

仅依赖 Python 3 标准库，完全离线；邮箱使用 RFC 2606 保留的
example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出、标准错误、落盘文件内容与目录状态；不直接
调用内部函数。每个样例使用独立临时目录，结束后自动清理。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 有效对照输入（逐字节固定）：两行 CSV 均以 LF 结束；模板以一个 LF 结束。
CONTACTS_BYTES = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
).encode("utf-8")
TEMPLATE_BYTES = "你好，{{name}}！\n".encode("utf-8")
PREVIEW_BYTES = "你好，甲！\n".encode("utf-8")
EMAIL = "a@example.invalid"
SEGMENT = "newsletter"

# 单独一个 0xFF 字节：在 UTF-8 下不可能出现在任何合法序列中。
INVALID_UTF8_BYTES = b"\xff"


class InputReadFailureTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write_input(self, name, data):
        """以二进制写入固定字节的输入文件，返回路径。"""
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
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

    def _assert_inputs_intact(self, existing_bytes, missing_paths):
        """已存在的输入文件字节不变；不存在的输入路径仍不被创建。"""
        for path, data in existing_bytes.items():
            self.assertTrue(os.path.isfile(path))
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), data)
        for path in missing_paths:
            self.assertFalse(os.path.exists(path))

    def _assert_rejected(
        self, contacts_path, template_path, fragments, existing_bytes, missing_paths
    ):
        """同一组失败输入，分别核对两种输出目录状态下的“读取先于输出”约定。"""

        def check(result):
            self.assertEqual(
                result.returncode,
                2,
                msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
            )
            # 失败不得向标准输出写入任何内容。
            self.assertEqual(result.stdout, b"")
            stderr = result.stderr.decode("utf-8")
            for fragment in fragments:
                self.assertIn(fragment, stderr)
            self.assertNotIn("Traceback (most recent call last)", stderr)
            self._assert_inputs_intact(existing_bytes, missing_paths)

        # 情形 A：输出目录尚不存在——运行后仍须不存在，且无预览或报告。
        out_absent = os.path.join(self.tmp, "previews-absent")
        check(self._run(contacts_path, template_path, out_absent))
        self.assertFalse(
            os.path.exists(out_absent),
            msg="输入读取失败后不得创建输出目录",
        )

        # 情形 B：输出目录已存在且为空——运行后仍须为空目录。
        out_empty = os.path.join(self.tmp, "previews-empty")
        os.mkdir(out_empty)
        check(self._run(contacts_path, template_path, out_empty))
        self.assertTrue(os.path.isdir(out_empty))
        self.assertEqual(
            os.listdir(out_empty),
            [],
            msg="输入读取失败后不得在空输出目录中留下任何文件",
        )

    def test_valid_control_generates_single_preview_and_report(self):
        # 有效对照：README 的公开命令读取两个固定文件，输出到 previews。
        contacts_path = self._write_input("contacts.csv", CONTACTS_BYTES)
        template_path = self._write_input("template.txt", TEMPLATE_BYTES)
        out_path = os.path.join(self.tmp, "previews")

        result = self._run(contacts_path, template_path, out_path)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stderr, b"")

        # 恰好生成一份预览与报告，无多余文件。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )

        # 正文为“你好，甲！”加一个 LF，逐字节一致。
        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), PREVIEW_BYTES)

        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        # 最终预览人数为 1，清单将该邮箱对应到这份文件。
        self.assertEqual(report["matched_count"], 1)
        self.assertEqual(
            report["previews"],
            [{"email": EMAIL, "file": "preview-0001.txt"}],
        )

    def test_failure_missing_contacts_file(self):
        # 与有效对照相比仅一处变化：联系人路径不存在（模板保持有效）。
        contacts_path = os.path.join(self.tmp, "missing-contacts.csv")
        template_path = self._write_input("template.txt", TEMPLATE_BYTES)
        self._assert_rejected(
            contacts_path,
            template_path,
            ["文件不存在", "联系人 CSV", contacts_path],
            existing_bytes={template_path: TEMPLATE_BYTES},
            missing_paths=(contacts_path,),
        )

    def test_failure_missing_template_file(self):
        # 与有效对照相比仅一处变化：模板路径不存在（联系人保持有效）。
        contacts_path = self._write_input("contacts.csv", CONTACTS_BYTES)
        template_path = os.path.join(self.tmp, "missing-template.txt")
        self._assert_rejected(
            contacts_path,
            template_path,
            ["文件不存在", "模板文件", template_path],
            existing_bytes={contacts_path: CONTACTS_BYTES},
            missing_paths=(template_path,),
        )

    def test_failure_contacts_not_valid_utf8(self):
        # 与有效对照相比仅一处变化：联系人文件仅含一个 0xFF 字节。
        contacts_path = self._write_input("contacts.csv", INVALID_UTF8_BYTES)
        template_path = self._write_input("template.txt", TEMPLATE_BYTES)
        self._assert_rejected(
            contacts_path,
            template_path,
            ["无法解码", "联系人 CSV", contacts_path, "UTF-8"],
            existing_bytes={
                contacts_path: INVALID_UTF8_BYTES,
                template_path: TEMPLATE_BYTES,
            },
            missing_paths=(),
        )

    def test_failure_template_not_valid_utf8(self):
        # 与有效对照相比仅一处变化：模板文件仅含一个 0xFF 字节。
        contacts_path = self._write_input("contacts.csv", CONTACTS_BYTES)
        template_path = self._write_input("template.txt", INVALID_UTF8_BYTES)
        self._assert_rejected(
            contacts_path,
            template_path,
            ["无法解码", "模板文件", template_path, "UTF-8"],
            existing_bytes={
                contacts_path: CONTACTS_BYTES,
                template_path: INVALID_UTF8_BYTES,
            },
            missing_paths=(),
        )


if __name__ == "__main__":
    unittest.main()
