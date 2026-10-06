"""newsletter_preview 输出路径保护的回归测试。

验证输出目录“允许写入”的条件（不存在则创建；已存在则须为空目录且
可写），以及拒绝写入后原有目录条目与文件字节保持不变、不新增预览或
报告。仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱
使用 RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出、标准错误与落盘文件字节；不直接调用内部
函数，也不依赖权限修改或外部服务。每个场景使用独立临时目录，
结束后自动清理。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 固定样例输入：表头 name,email,segment；甲匹配 newsletter，乙为
# 不匹配的 archive。
CONTACTS_FIXTURE = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,b@example.invalid,archive\n"
)
TEMPLATE_FIXTURE = "你好，{{name}}！\n"
SEGMENT = "newsletter"

PREVIEW_JIA = "你好，甲！\n"
GENERATED_NAMES = ["preview-0001.txt", "report.json"]

EXPECTED_REPORT = {
    "template": TEMPLATE_FIXTURE,
    "segment": SEGMENT,
    "matched_count": 1,
    "previews": [
        {"email": "a@example.invalid", "file": "preview-0001.txt"},
    ],
}


class OutputPathGuardTestCase(unittest.TestCase):
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

    def _run(self, out_path):
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
                SEGMENT,
                "--out",
                out_path,
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _assert_rejected(self, result, fragments):
        """拒绝场景的共同约定：退出 2、stdout 为空、stderr 含提示且
        不含 Traceback。"""
        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"", msg="拒绝时标准输出必须为空")
        stderr = result.stderr.decode("utf-8")
        for fragment in fragments:
            self.assertIn(fragment, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)

    def _assert_dir_unchanged(self, out_path, expected_entries, snapshots):
        """目录条目集合不变，且各文件字节与运行前快照完全一致；
        不新增任何预览或报告。"""
        self.assertEqual(sorted(os.listdir(out_path)), expected_entries)
        for rel, before in snapshots.items():
            with open(os.path.join(out_path, rel), "rb") as fh:
                self.assertEqual(fh.read(), before, msg=f"文件字节被改动：{rel}")
        for name in GENERATED_NAMES:
            self.assertFalse(
                os.path.exists(os.path.join(out_path, name)),
                msg=f"拒绝后不得新增 {name}",
            )

    def test_existing_empty_writable_dir_is_accepted(self):
        # 输出目录已存在、为空且可写：正常生成，stdout/stderr 均为空，
        # 目录恰好包含 preview-0001.txt 与 report.json。
        out_path = os.path.join(self.tmp, "out-empty")
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

        # 预览正文完成姓名替换并保留末尾 LF。
        with open(
            os.path.join(out_path, "preview-0001.txt"), "rb"
        ) as fh:
            self.assertEqual(fh.read(), PREVIEW_JIA.encode("utf-8"))

        # 报告保留模板原文与筛选值，仅含甲对应的一条预览记录。
        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), EXPECTED_REPORT)

    def test_nonempty_dir_with_file_is_rejected_and_untouched(self):
        # 目录中已有 keep.txt：按非空目录拒绝，原条目与文件字节不变。
        out_path = os.path.join(self.tmp, "out-keep-file")
        os.mkdir(out_path)
        keep_path = os.path.join(out_path, "keep.txt")
        keep_bytes = "原有内容，请勿改动\n".encode("utf-8")
        with open(keep_path, "wb") as fh:
            fh.write(keep_bytes)

        result = self._run(out_path)

        self._assert_rejected(result, ["输出目录非空", "拒绝覆盖"])
        self.assertTrue(os.path.isdir(out_path))
        self._assert_dir_unchanged(
            out_path, ["keep.txt"], {"keep.txt": keep_bytes}
        )

    def test_nonempty_dir_with_empty_subdir_is_rejected_and_untouched(self):
        # 目录中仅有一个空子目录：同样按非空目录拒绝，子目录保留，
        # 不新增预览或报告。
        out_path = os.path.join(self.tmp, "out-subdir")
        os.mkdir(out_path)
        os.mkdir(os.path.join(out_path, "empty-sub"))

        result = self._run(out_path)

        self._assert_rejected(result, ["输出目录非空", "拒绝覆盖"])
        self.assertTrue(os.path.isdir(out_path))
        self.assertEqual(sorted(os.listdir(out_path)), ["empty-sub"])
        self.assertTrue(os.path.isdir(os.path.join(out_path, "empty-sub")))
        self.assertEqual(os.listdir(os.path.join(out_path, "empty-sub")), [])
        for name in GENERATED_NAMES:
            self.assertFalse(os.path.exists(os.path.join(out_path, name)))

    def test_output_path_that_is_a_file_is_rejected_and_untouched(self):
        # 输出路径本身是已存在的普通文件：提示不是目录，文件内容与
        # 类型保持不变。
        out_path = os.path.join(self.tmp, "not-a-dir.txt")
        original_bytes = "我是一个普通文件\n".encode("utf-8")
        with open(out_path, "wb") as fh:
            fh.write(original_bytes)

        result = self._run(out_path)

        self._assert_rejected(result, ["不是目录"])
        self.assertTrue(os.path.isfile(out_path))
        self.assertFalse(os.path.isdir(out_path))
        with open(out_path, "rb") as fh:
            self.assertEqual(fh.read(), original_bytes)

    def test_reusing_populated_dir_is_rejected_and_first_run_untouched(self):
        # 第一次成功生成；第二次以相同输入、相同目录调用，应按非空
        # 目录规则拒绝；第一次生成的文件名与字节保持一致，不追加文件。
        out_path = os.path.join(self.tmp, "out-reuse")

        first = self._run(out_path)
        self.assertEqual(
            first.returncode,
            0,
            msg=f"stderr: {first.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(first.stdout, b"")
        self.assertEqual(first.stderr, b"")

        # 留存第一次生成结果的文件名与字节快照。
        names_after_first = sorted(os.listdir(out_path))
        self.assertEqual(names_after_first, GENERATED_NAMES)
        snapshots = {}
        for name in names_after_first:
            with open(os.path.join(out_path, name), "rb") as fh:
                snapshots[name] = fh.read()

        second = self._run(out_path)
        self._assert_rejected(second, ["输出目录非空", "拒绝覆盖"])

        # 文件名集合与各文件字节均与第一次运行后一致，无追加文件。
        self.assertEqual(sorted(os.listdir(out_path)), names_after_first)
        for name, before in snapshots.items():
            with open(os.path.join(out_path, name), "rb") as fh:
                self.assertEqual(fh.read(), before, msg=f"首次产物被改动：{name}")


if __name__ == "__main__":
    unittest.main()
