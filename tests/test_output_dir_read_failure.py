"""newsletter_preview 输出目录条目读取失败的回归测试。

固定合成样例：contacts.csv 为 UTF-8，表头 name,email,segment，唯一
数据行为 甲,a@example.invalid,newsletter；template.txt 内容为
“你好，{{name}}！”并以一个 LF 结束。命令沿用 README 记载的公开入口：

    python -m newsletter_preview \\
        --contacts contacts.csv --template template.txt \\
        --segment newsletter --out previews

当 --out 已被识别为现有目录、但读取其条目（os.listdir）因权限不足等
操作系统错误失败时，必须按公开约定拒绝：退出码 2，标准输出为空，
标准错误包含“无法检查输出目录”、原样的 --out 路径与底层原因文字，
且不出现 Traceback。读取失败既不能被当作空目录继续生成，也不能被
误报成目录非空；拒绝后不新增预览或 report.json，原目录条目、文件
字节与目录权限均保持不变。恢复可读后同一输入应能正常生成。

仅依赖 Python 3 标准库，完全离线；邮箱使用 RFC 2606 保留的
example.invalid 虚构域名。以 chmod 000 剥夺目录读/执行权限来稳定
触发 os.listdir 的 PermissionError（root 豁免权限检查，此类用例在
root 下跳过）。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出、标准错误与落盘文件字节；不直接调用内部
函数，也不访问外部服务。每个场景使用独立临时目录，结束后自动清理。
"""

import json
import os
import stat
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

GENERATED_NAMES = ["preview-0001.txt", "report.json"]

EXPECTED_REPORT = {
    "template": TEMPLATE_BYTES.decode("utf-8"),
    "segment": SEGMENT,
    "segment_count": 1,
    "excluded_count": 0,
    "excluded_contacts": [],
    "matched_count": 1,
    "previews": [
        {"email": EMAIL, "file": "preview-0001.txt"},
    ],
}


class OutputDirReadFailureTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write_input("contacts.csv", CONTACTS_BYTES)
        self.template_path = self._write_input("template.txt", TEMPLATE_BYTES)
        # root 绕过目录权限检查，chmod 000 无法触发读取失败；此类用例跳过。
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root 用户绕过目录权限，无法合成 os.listdir 权限失败")

    def tearDown(self):
        # 恢复可能被测试改掉的权限，保证临时目录可被清理。
        for dirpath, dirnames, _filenames in os.walk(self.tmp):
            for name in dirnames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o700)
                except OSError:
                    pass
        try:
            os.chmod(self.tmp, 0o700)
        except OSError:
            pass
        self._tmp.cleanup()

    def _write_input(self, name, data):
        """以二进制写入固定字节的输入文件，返回路径。"""
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _run(self, out_path, segment=SEGMENT, contacts_path=None,
             template_path=None, exclude_email=None):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。"""
        cmd = [
            sys.executable,
            "-m",
            "newsletter_preview",
            "--contacts",
            contacts_path or self.contacts_path,
            "--template",
            template_path or self.template_path,
            "--segment",
            segment,
            "--out",
            out_path,
        ]
        if exclude_email is not None:
            cmd.extend(["--exclude-email", exclude_email])
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _deny_dir_access(self, path):
        """剥夺目录读/执行权限，使 os.listdir 触发 PermissionError。"""
        mode = stat.S_IMODE(os.stat(path).st_mode)
        os.chmod(path, 0)
        return mode

    def _allow_dir_access(self, path, mode=0o700):
        os.chmod(path, mode)

    def _assert_read_failure_rejected(self, result, out_path):
        """目录条目读取失败的共同约定：退出 2、stdout 空、stderr 含
        “无法检查输出目录”、原样 --out 路径与底层原因，且无 Traceback。"""
        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"", msg="拒绝时标准输出必须为空")
        stderr = result.stderr.decode("utf-8")
        self.assertIn("无法检查输出目录", stderr)
        # 原样的 --out 路径必须出现在提示中。
        self.assertIn(out_path, stderr)
        # 底层原因文字（权限不足）必须透传，不得被替换成“非空”等其他判定。
        self.assertIn("Permission denied", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertNotIn("输出目录非空", stderr)

    def test_valid_control_empty_dir_generates_single_preview_and_report(self):
        # 有效对照：现有空目录可正常读取且可写时退出 0，只生成
        # preview-0001.txt 与 report.json；预览为“你好，甲！”加原有
        # 换行，报告 matched_count=1、excluded_count=0。
        out_path = os.path.join(self.tmp, "previews")
        os.mkdir(out_path)

        result = self._run(out_path)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(sorted(os.listdir(out_path)), GENERATED_NAMES)

        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), PREVIEW_BYTES)

        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), EXPECTED_REPORT)

    def test_unreadable_empty_dir_is_rejected_and_stays_empty(self):
        # 现有空目录的条目读取因权限失败：退出 2，不得当作空目录继续
        # 生成；目录仍为空、无预览或报告，目录权限保持被拒绝时的状态。
        out_path = os.path.join(self.tmp, "previews")
        os.mkdir(out_path)
        mode_before = self._deny_dir_access(out_path)

        try:
            result = self._run(out_path)

            self._assert_read_failure_rejected(result, out_path)
            self.assertTrue(os.path.isdir(out_path))
            # 失败后权限不得被命令改动（仍为读取失败时的 0 权限）。
            self.assertEqual(stat.S_IMODE(os.stat(out_path).st_mode), 0)
        finally:
            self._allow_dir_access(out_path, mode_before)

        # 恢复可读后目录仍为空，确认失败路径未留下任何条目。
        self.assertEqual(os.listdir(out_path), [])

    def test_unreadable_dir_then_restored_generates_normally(self):
        # 同一输入：目录条目读取失败时被拒绝；恢复可读后正常生成。
        out_path = os.path.join(self.tmp, "previews")
        os.mkdir(out_path)

        self._deny_dir_access(out_path)
        failed = self._run(out_path)
        self._assert_read_failure_rejected(failed, out_path)
        self._allow_dir_access(out_path)

        result = self._run(out_path)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(sorted(os.listdir(out_path)), GENERATED_NAMES)
        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), PREVIEW_BYTES)
        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), EXPECTED_REPORT)

    def test_unreadable_nonempty_dir_is_rejected_with_entries_intact(self):
        # 目录中已有文件与空子目录时条目读取失败：同样按“无法检查”拒绝，
        # 既不误报非空也不继续生成；原条目集合、文件字节与子目录不变。
        out_path = os.path.join(self.tmp, "previews")
        os.mkdir(out_path)
        keep_path = os.path.join(out_path, "keep.txt")
        keep_bytes = "原有内容，请勿改动\n".encode("utf-8")
        with open(keep_path, "wb") as fh:
            fh.write(keep_bytes)
        os.mkdir(os.path.join(out_path, "keep-sub"))

        mode_before = self._deny_dir_access(out_path)
        try:
            result = self._run(out_path)
            self._assert_read_failure_rejected(result, out_path)
            self.assertEqual(stat.S_IMODE(os.stat(out_path).st_mode), 0)
        finally:
            self._allow_dir_access(out_path, mode_before)

        self.assertEqual(sorted(os.listdir(out_path)), ["keep-sub", "keep.txt"])
        with open(keep_path, "rb") as fh:
            self.assertEqual(fh.read(), keep_bytes)
        self.assertTrue(os.path.isdir(os.path.join(out_path, "keep-sub")))
        self.assertEqual(os.listdir(os.path.join(out_path, "keep-sub")), [])
        for name in GENERATED_NAMES:
            self.assertFalse(
                os.path.exists(os.path.join(out_path, name)),
                msg=f"拒绝后不得新增 {name}",
            )

    def test_unreadable_dir_rejected_even_when_nobody_retained(self):
        # 即使筛选后无人保留（唯一联系人属 newsletter，却筛选 archive），
        # 目录检查先于筛选且失败，仍采用同一拒绝结果而非写空报告。
        out_path = os.path.join(self.tmp, "previews")
        os.mkdir(out_path)

        mode_before = self._deny_dir_access(out_path)
        try:
            result = self._run(out_path, segment="archive")
            self._assert_read_failure_rejected(result, out_path)
        finally:
            self._allow_dir_access(out_path, mode_before)

        self.assertEqual(os.listdir(out_path), [])

    def test_unreadable_dir_with_excluded_only_match_is_rejected(self):
        # 唯一命中被 --exclude-email 排除、最终保留为 0 时，目录条目
        # 读取失败同样先行拒绝，不生成 report.json。
        out_path = os.path.join(self.tmp, "previews")
        os.mkdir(out_path)

        mode_before = self._deny_dir_access(out_path)
        try:
            result = self._run(out_path, exclude_email=EMAIL)
            self._assert_read_failure_rejected(result, out_path)
        finally:
            self._allow_dir_access(out_path, mode_before)

        self.assertEqual(os.listdir(out_path), [])

    def test_input_validation_error_takes_priority_over_unreadable_dir(self):
        # 输入校验先于输出目录检查：模板含未知变量 {{age}} 时，即使
        # --out 目录条目无法读取，也必须报模板错误，而非目录错误。
        bad_template = self._write_input(
            "bad-template.txt", "你好，{{name}}！\n{{age}}\n".encode("utf-8")
        )
        out_path = os.path.join(self.tmp, "previews")
        os.mkdir(out_path)

        mode_before = self._deny_dir_access(out_path)
        try:
            result = self._run(out_path, template_path=bad_template)
        finally:
            self._allow_dir_access(out_path, mode_before)

        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        stderr = result.stderr.decode("utf-8")
        self.assertIn("未知变量", stderr)
        self.assertIn("age", stderr)
        self.assertNotIn("无法检查输出目录", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(os.listdir(out_path), [])

    def test_contacts_validation_error_takes_priority_over_unreadable_dir(self):
        # 联系人 CSV 缺少必需列 segment 时同样先报输入错误，目录读取
        # 失败不得取代输入校验提示，且目录保持为空。
        bad_contacts = self._write_input(
            "bad-contacts.csv", "name,email\n甲,a@example.invalid\n".encode("utf-8")
        )
        out_path = os.path.join(self.tmp, "previews")
        os.mkdir(out_path)

        mode_before = self._deny_dir_access(out_path)
        try:
            result = self._run(out_path, contacts_path=bad_contacts)
        finally:
            self._allow_dir_access(out_path, mode_before)

        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        stderr = result.stderr.decode("utf-8")
        self.assertIn("缺少必需列", stderr)
        self.assertIn("segment", stderr)
        self.assertNotIn("无法检查输出目录", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(os.listdir(out_path), [])


if __name__ == "__main__":
    unittest.main()
