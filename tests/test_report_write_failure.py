"""report.json 创建失败的回归测试（写盘阶段故障，不写索引页）。

流程次序：先写逐人预览，再写 report.json，--index 的索引页最后写入。
本文件核对 report.json 在文件创建前即失败（PermissionError，底层原因
固定为 report-write-denied）时的公开约定：退出 2，标准错误点名
report.json 的路径与底层原因、无 Traceback；此前写出的两份预览完整
保留且与同格式无故障对照逐字节一致；report.json 与 index.html 均不
存在，输出目录没有其他新增文件。每种格式（默认 text 与 --format
html）先做无故障对照：退出 0、标准错误为空，生成两份连续编号的
预览、report.json 与 index.html；报告的分组命中、排除、最终预览
计数依次为 2、0、2，排除明细为空，预览清单按甲、乙顺序对应邮箱
与实际扩展名。

故障注入不修改产品代码、不依赖管理员权限、不修改真实目录权限、
不耗尽磁盘空间，也不依赖任何平台专用接口：测试生成一个临时包装
脚本，在子进程内包装 builtins.open，仅对名为 report.json 的路径
在文件创建前抛出 PermissionError("report-write-denied")，其余打开
调用原样放行，然后以 runpy 按 `python -m newsletter_preview` 的
语义执行公开入口。仅使用 Python 3 标准库，Windows 与常见 Linux
环境均可执行，核心用例不因缺少平台接口而跳过。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

对照运行通过公开入口 `python -m newsletter_preview` 以子进程方式
运行，断言真实退出码、标准输出/错误、落盘文件内容与输出目录状态；
不直接调用内部函数。每个样例使用独立临时目录，结束后自动清理。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 验收固定样例：表头 name,email,segment，甲、乙两条记录同属
# newsletter；模板正文“你好，{{name}}！”末尾保留一个 LF。
CONTACTS = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,b@example.invalid,newsletter\n"
)
TEMPLATE = "你好，{{name}}！\n"
SEGMENT = "newsletter"
EMAIL_A = "a@example.invalid"
EMAIL_B = "b@example.invalid"

# 注入的底层原因原文，须出现在失败运行的标准错误中。
DENIED_REASON = "report-write-denied"

# 故障注入包装器源码：写入临时目录后由子进程执行。包装 builtins.open，
# 仅对名为 report.json 的路径在文件创建前抛出 PermissionError，其余
# 调用放行；随后以 runpy 按 python -m newsletter_preview 的语义执行
# 包内 __main__（其内部 sys.exit(main()) 的退出码原样成为进程退出码）。
WRAPPER_SOURCE = (
    "import builtins\n"
    "import os\n"
    "import runpy\n"
    "\n"
    "_real_open = builtins.open\n"
    "\n"
    "\n"
    "def _guarded_open(file, *args, **kwargs):\n"
    "    if not isinstance(file, int):\n"
    "        if os.path.basename(os.fspath(file)) == 'report.json':\n"
    f"            raise PermissionError({DENIED_REASON!r})\n"
    "    return _real_open(file, *args, **kwargs)\n"
    "\n"
    "\n"
    "builtins.open = _guarded_open\n"
    "runpy.run_module('newsletter_preview', run_name='__main__')\n"
)

# 每种格式：(命令行 --format 取值，None 表示省略即默认 text)，预览扩展名。
FORMATS = (
    ("默认 text（省略 --format）", None, "txt"),
    ("--format html", "html", "html"),
)


class ReportWriteFailureTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS)
        self.template_path = self._write("template.txt", TEMPLATE)
        self.wrapper_path = self._write("report_denied_wrapper.py",
                                        WRAPPER_SOURCE)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _argv(self, out_path, fmt):
        """公开入口参数：fmt 为 None 时省略 --format（即默认 text）。"""
        argv = [
            "--contacts",
            self.contacts_path,
            "--template",
            self.template_path,
            "--segment",
            SEGMENT,
            "--out",
            out_path,
        ]
        if fmt is not None:
            argv.extend(["--format", fmt])
        argv.append("--index")
        return argv

    def _env(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = (
            PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        )
        # 固定子进程标准流编码，保证中文错误信息在各平台可解码核对。
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _run_control(self, out_path, fmt):
        """无故障对照：直接经公开入口 python -m newsletter_preview 运行。"""
        return subprocess.run(
            [sys.executable, "-m", "newsletter_preview"]
            + self._argv(out_path, fmt),
            cwd=PROJECT_ROOT,
            env=self._env(),
            capture_output=True,
        )

    def _run_report_denied(self, out_path, fmt):
        """故障运行：同一公开入口，但 report.json 创建前即被拒绝。"""
        return subprocess.run(
            [sys.executable, self.wrapper_path] + self._argv(out_path, fmt),
            cwd=PROJECT_ROOT,
            env=self._env(),
            capture_output=True,
        )

    def _read_bytes(self, directory, name):
        with open(os.path.join(directory, name), "rb") as fh:
            return fh.read()

    def test_control_success_both_formats(self):
        # 无故障对照：退出 0、标准错误为空，两份连续编号预览、
        # report.json 与 index.html；计数 2、0、2，排除明细为空，
        # 预览清单按甲、乙顺序对应邮箱与实际扩展名。
        for label, fmt, extension in FORMATS:
            with self.subTest(label):
                out_path = os.path.join(self.tmp, f"control-{extension}")
                result = self._run_control(out_path, fmt)
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
                )
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")

                preview_1 = f"preview-0001.{extension}"
                preview_2 = f"preview-0002.{extension}"
                self.assertEqual(
                    sorted(os.listdir(out_path)),
                    ["index.html", preview_1, preview_2, "report.json"],
                )

                with open(os.path.join(out_path, "report.json"),
                          encoding="utf-8") as fh:
                    report = json.load(fh)
                self.assertEqual(report["segment_count"], 2)
                self.assertEqual(report["excluded_count"], 0)
                self.assertEqual(report["matched_count"], 2)
                self.assertEqual(report["excluded_contacts"], [])
                self.assertEqual(
                    report["previews"],
                    [
                        {"email": EMAIL_A, "file": preview_1},
                        {"email": EMAIL_B, "file": preview_2},
                    ],
                )

    def test_report_creation_denied_keeps_previews_both_formats(self):
        # report.json 创建前被拒绝：退出 2，标准错误点名 report.json
        # 路径与底层原因 report-write-denied、无 Traceback；两份预览
        # 完整保留且与同格式对照逐字节一致；report.json 与 index.html
        # 均不存在，目录中没有其他新增文件。
        for label, fmt, extension in FORMATS:
            with self.subTest(label):
                control_path = os.path.join(self.tmp,
                                            f"denied-control-{extension}")
                control = self._run_control(control_path, fmt)
                self.assertEqual(
                    control.returncode,
                    0,
                    msg=f"stderr: {control.stderr.decode('utf-8', 'replace')}",
                )

                out_path = os.path.join(self.tmp, f"denied-{extension}")
                result = self._run_report_denied(out_path, fmt)
                self.assertEqual(
                    result.returncode,
                    2,
                    msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
                )
                self.assertEqual(result.stdout, b"")
                stderr = result.stderr.decode("utf-8")
                # 点名 report.json 的完整路径与底层原因，无 Traceback。
                self.assertIn(os.path.join(out_path, "report.json"), stderr)
                self.assertIn(DENIED_REASON, stderr)
                self.assertNotIn("Traceback (most recent call last)", stderr)

                # 恰好两份预览：report.json 与 index.html 均不存在，
                # 也没有其他新增文件。
                preview_1 = f"preview-0001.{extension}"
                preview_2 = f"preview-0002.{extension}"
                self.assertEqual(
                    sorted(os.listdir(out_path)),
                    [preview_1, preview_2],
                )
                self.assertFalse(
                    os.path.exists(os.path.join(out_path, "report.json"))
                )
                self.assertFalse(
                    os.path.exists(os.path.join(out_path, "index.html"))
                )

                # 两份预览与同格式无故障对照逐字节一致。
                for name in (preview_1, preview_2):
                    self.assertEqual(
                        self._read_bytes(out_path, name),
                        self._read_bytes(control_path, name),
                        msg=f"{label}：{name} 与对照不一致",
                    )


if __name__ == "__main__":
    unittest.main()
