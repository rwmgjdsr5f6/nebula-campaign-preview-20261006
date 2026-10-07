"""newsletter_preview 输出目录条目读取失败的回归测试。

固定合成样例：contacts.csv 表头为 name,email,segment，唯一数据行为
甲,a@example.invalid,newsletter，两行均以 LF 结束；template.txt 内容为
“你好，{{name}}！”并以一个 LF 结束。公开命令为 README 记载的
`python -m newsletter_preview --contacts … --template … --segment newsletter
--out previews`。

覆盖的失败路径：--out 已被识别为现有目录，但读取其中条目（os.listdir）
因权限不足等操作系统错误失败。此时命令须统一退出 2：标准输出为空，
标准错误包含“无法检查输出目录”、原样的 --out 路径与底层原因文字，
且不出现 Traceback；读取失败不得被当作空目录继续输出，也不得被误报成
目录非空；失败后不新增预览或 report.json，原空目录仍为空，目录权限
不变。恢复可读后可正常生成。即使筛选后无人保留，也采用同一拒绝结果；
联系人与模板的既有输入校验先于目录检查，输入错误的提示不被目录错误
取代。

仅依赖 Python 3 标准库，完全离线；邮箱使用 RFC 2606 保留的
example.invalid 虚构域名。目录不可读通过 chmod 0o000 模拟；若当前
平台或用户（如 root）下该方式不能令 os.listdir 失败，相关用例自动
跳过，不误判。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口以子进程方式运行，断言真实退出码、标准输出、标准错误
与落盘文件字节；不直接调用内部函数。每个用例使用独立临时目录，结束后
自动清理。
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

# 固定样例输入（逐字节固定）：表头加唯一数据行，均以 LF 结束。
CONTACTS_FIXTURE = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
)
TEMPLATE_FIXTURE = "你好，{{name}}！\n"
SEGMENT = "newsletter"

PREVIEW_JIA = "你好，甲！\n"
GENERATED_NAMES = ["preview-0001.txt", "report.json"]

EXPECTED_REPORT = {
    "template": TEMPLATE_FIXTURE,
    "segment": SEGMENT,
    "segment_count": 1,
    "excluded_count": 0,
    "excluded_contacts": [],
    "matched_count": 1,
    "previews": [
        {"email": "a@example.invalid", "file": "preview-0001.txt"},
    ],
}

# 目录条目读取失败时使用的权限：所有者也无任何权限。
UNREADABLE_MODE = 0o000
RESTORED_MODE = 0o700


class OutputDirUnreadableTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS_FIXTURE)
        self.template_path = self._write("template.txt", TEMPLATE_FIXTURE)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _run(self, out_path, segment=SEGMENT, contacts_path=None):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "newsletter_preview",
                "--contacts",
                contacts_path or self.contacts_path,
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

    def _make_unreadable_dir(self, name):
        """建一个读取条目会失败的现有空目录，返回 (路径, 底层原因文字)。

        若当前环境（如 root 或不支持权限位的平台）无法令 os.listdir
        失败，则恢复权限并跳过本用例。
        """
        out_path = os.path.join(self.tmp, name)
        os.mkdir(out_path)
        os.chmod(out_path, UNREADABLE_MODE)
        try:
            os.listdir(out_path)
        except OSError as exc:
            reason = str(exc)
        else:
            os.chmod(out_path, RESTORED_MODE)
            self.skipTest("当前环境无法通过权限位模拟目录条目读取失败")
        return out_path, reason

    def _assert_unreadable_rejected(self, result, out_path, reason):
        """读取失败场景的共同约定：退出 2、stdout 为空、stderr 含提示、
        原样路径与底层原因，不含 Traceback，且不误报为目录非空。"""
        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"", msg="拒绝时标准输出必须为空")
        stderr = result.stderr.decode("utf-8")
        self.assertIn("无法检查输出目录", stderr)
        self.assertIn(out_path, stderr)
        self.assertIn(reason, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertNotIn("输出目录非空", stderr)

    def _assert_generated(self, out_path):
        """正常生成的共同约定：恰好一份预览与报告，内容逐字节一致。"""
        self.assertEqual(sorted(os.listdir(out_path)), GENERATED_NAMES)
        with open(
            os.path.join(out_path, "preview-0001.txt"), "rb"
        ) as fh:
            self.assertEqual(fh.read(), PREVIEW_JIA.encode("utf-8"))
        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), EXPECTED_REPORT)

    def test_empty_readable_writable_dir_is_accepted(self):
        # 有效对照：现有空目录可正常读取且可写，退出 0，恰好生成
        # preview-0001.txt 与 report.json。
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
        self._assert_generated(out_path)

    def test_unreadable_dir_entries_rejected_then_recovers(self):
        # 目录存在但读取条目失败：按统一约定拒绝，目录保持为空、权限
        # 不变；恢复可读后可正常生成。
        out_path, reason = self._make_unreadable_dir("previews")

        result = self._run(out_path)

        self._assert_unreadable_rejected(result, out_path, reason)
        # 程序不得改变目录权限。
        self.assertEqual(
            stat.S_IMODE(os.stat(out_path).st_mode), UNREADABLE_MODE
        )
        # 恢复可读后核对：原空目录仍为空，未新增预览或报告。
        os.chmod(out_path, RESTORED_MODE)
        self.assertEqual(os.listdir(out_path), [])

        # 同一目录恢复可读可写后，同一输入可正常生成。
        recovered = self._run(out_path)
        self.assertEqual(
            recovered.returncode,
            0,
            msg=f"stderr: {recovered.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(recovered.stdout, b"")
        self.assertEqual(recovered.stderr, b"")
        self._assert_generated(out_path)

    def test_unreadable_dir_rejected_even_when_nobody_matches(self):
        # 筛选值不匹配任何记录：读取条目失败仍采用同一拒绝结果，
        # 不得当作空目录继续输出空报告。
        out_path, reason = self._make_unreadable_dir("previews")

        result = self._run(out_path, segment="archive")

        self._assert_unreadable_rejected(result, out_path, reason)
        os.chmod(out_path, RESTORED_MODE)
        self.assertEqual(os.listdir(out_path), [])

    def test_input_validation_error_takes_precedence_over_dir_error(self):
        # 联系人 CSV 缺少 segment 列，同时输出目录条目读取失败：
        # 既有输入校验先于目录检查，提示输入错误而非目录错误。
        contacts_path = self._write(
            "contacts-bad.csv", "name,email\n甲,a@example.invalid\n"
        )
        out_path, _ = self._make_unreadable_dir("previews")

        result = self._run(out_path, contacts_path=contacts_path)

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
        self.assertNotIn("Traceback (most recent call last)", stderr)
        os.chmod(out_path, RESTORED_MODE)
        self.assertEqual(os.listdir(out_path), [])


if __name__ == "__main__":
    unittest.main()
