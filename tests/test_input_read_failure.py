"""newsletter_preview 输入读取失败的回归测试。

固定两类读取失败在产生任何预览之前被拒绝：

- 输入路径不存在（联系人 CSV 或文字模板）；
- 输入文件不是合法 UTF-8（联系人 CSV 或文字模板仅含一个 0xFF 字节）。

另含一个使用最小合成样例的成功对照，确认有效输入经公开命令正常生成
一份预览与报告。仅依赖 Python 3 标准库，完全离线；样例联系人为合成
数据，邮箱使用 RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出/标准错误、落盘文件内容与输出目录状态；不直接
调用内部函数。每个样例使用独立临时目录，结束后自动清理，可重复执行。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 固定合成样例：表头 name,email,segment 与唯一数据行，两行均以 LF 结束。
CONTACTS_VALID = "name,email,segment\n甲,a@example.invalid,newsletter\n"
# 模板“你好，{{name}}！”加一个 LF。
TEMPLATE_VALID = "你好，{{name}}！\n"
EMAIL = "a@example.invalid"
SEGMENT = "newsletter"

# 成功对照的预览正文：“你好，甲！”加一个 LF（按 UTF-8 逐字节核对）。
PREVIEW_BYTES = "你好，甲！\n".encode("utf-8")

# 非法编码样例：单独一个 0xFF 字节，不是合法 UTF-8 的起始字节。
INVALID_UTF8_BYTES = b"\xff"


class InputReadFailureTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write_bytes(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(content)
        return path

    def _write_text(self, name, content):
        return self._write_bytes(name, content.encode("utf-8"))

    def _read_bytes(self, path):
        with open(path, "rb") as fh:
            return fh.read()

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

    def test_success_control_generates_one_preview_and_report(self):
        """有效对照：README 公开命令读取两个有效文件，正常生成预览与报告。"""
        contacts_path = self._write_text("contacts.csv", CONTACTS_VALID)
        template_path = self._write_text("template.txt", TEMPLATE_VALID)
        out_path = os.path.join(self.tmp, "previews")

        result = self._run(contacts_path, template_path, out_path)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stderr, b"")

        # 目录恰好包含一份预览与 report.json，无多余文件。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )

        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), PREVIEW_BYTES)

        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
        # 报告保留模板原文与筛选值，最终预览人数为 1，清单把该邮箱
        # 对应到 preview-0001.txt。
        self.assertEqual(report["template"], TEMPLATE_VALID)
        self.assertEqual(report["segment"], SEGMENT)
        self.assertEqual(report["matched_count"], 1)
        self.assertEqual(
            report["previews"],
            [{"email": EMAIL, "file": "preview-0001.txt"}],
        )

    def _assert_rejected(
        self,
        contacts_path,
        template_path,
        fragments,
        existing_inputs,
    ):
        """同一组失败输入在两种输出目录状态下均须在产生预览前被拒绝。

        existing_inputs 为运行前实际存在的输入文件 {path: bytes}，
        运行后逐字节核对保持不变（缺失路径不在其中，另行断言仍不存在）。
        """
        missing_paths = [
            path
            for path in (contacts_path, template_path)
            if path not in existing_inputs
        ]

        # 情形 A：输出目录尚不存在——运行后仍须不存在。
        out_absent = os.path.join(self.tmp, "previews-absent")
        result = self._run(contacts_path, template_path, out_absent)
        self._assert_failure_result(result, fragments, out_absent, absent=True)

        # 情形 B：输出目录已存在且为空——运行后仍须为空目录。
        out_empty = os.path.join(self.tmp, "previews-empty")
        os.mkdir(out_empty)
        result = self._run(contacts_path, template_path, out_empty)
        self._assert_failure_result(result, fragments, out_empty, absent=False)

        # 两次失败后，已存在的输入文件字节保持不变；缺失路径仍不存在。
        for path, content in existing_inputs.items():
            self.assertEqual(self._read_bytes(path), content)
        for path in missing_paths:
            self.assertFalse(
                os.path.exists(path),
                msg="失败路径不得被创建为文件或目录",
            )

    def _assert_failure_result(self, result, fragments, out_path, absent):
        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        # 失败时标准输出必须为空：不得产生任何预览内容。
        self.assertEqual(result.stdout, b"")
        stderr = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback (most recent call last)", stderr)
        for fragment in fragments:
            self.assertIn(fragment, stderr)
        if absent:
            self.assertFalse(
                os.path.exists(out_path),
                msg="校验失败后不得创建输出目录",
            )
        else:
            self.assertTrue(os.path.isdir(out_path))
            self.assertEqual(
                os.listdir(out_path),
                [],
                msg="校验失败后不得在空输出目录中留下任何文件",
            )

    def test_failure_missing_contacts_file(self):
        # 仅改变一处：联系人路径指向不存在的文件，模板保持有效。
        contacts_path = os.path.join(self.tmp, "missing-contacts.csv")
        template_path = self._write_text("template.txt", TEMPLATE_VALID)
        self._assert_rejected(
            contacts_path,
            template_path,
            ["文件不存在", "联系人 CSV", contacts_path],
            {template_path: TEMPLATE_VALID.encode("utf-8")},
        )

    def test_failure_missing_template_file(self):
        # 仅改变一处：模板路径指向不存在的文件，联系人保持有效。
        contacts_path = self._write_text("contacts.csv", CONTACTS_VALID)
        template_path = os.path.join(self.tmp, "missing-template.txt")
        self._assert_rejected(
            contacts_path,
            template_path,
            ["文件不存在", "模板文件", template_path],
            {contacts_path: CONTACTS_VALID.encode("utf-8")},
        )

    def test_failure_contacts_not_valid_utf8(self):
        # 仅改变一处：联系人文件只含一个 0xFF 字节，模板保持有效。
        contacts_path = self._write_bytes("contacts.csv", INVALID_UTF8_BYTES)
        template_path = self._write_text("template.txt", TEMPLATE_VALID)
        self._assert_rejected(
            contacts_path,
            template_path,
            ["无法解码", "联系人 CSV", contacts_path, "UTF-8"],
            {
                contacts_path: INVALID_UTF8_BYTES,
                template_path: TEMPLATE_VALID.encode("utf-8"),
            },
        )

    def test_failure_template_not_valid_utf8(self):
        # 仅改变一处：模板文件只含一个 0xFF 字节，联系人保持有效。
        contacts_path = self._write_text("contacts.csv", CONTACTS_VALID)
        template_path = self._write_bytes("template.txt", INVALID_UTF8_BYTES)
        self._assert_rejected(
            contacts_path,
            template_path,
            ["无法解码", "模板文件", template_path, "UTF-8"],
            {
                contacts_path: CONTACTS_VALID.encode("utf-8"),
                template_path: INVALID_UTF8_BYTES,
            },
        )


if __name__ == "__main__":
    unittest.main()
