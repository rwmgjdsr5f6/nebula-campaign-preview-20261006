"""newsletter_preview report.json 创建失败的回归测试。

固定合成输入（逐字节固定）：contacts.csv 为 UTF-8，表头
name,email,segment，两条数据行依次为 甲,a@example.invalid,newsletter
与 乙,b@example.invalid,newsletter；template.txt 内容为“你好，{{name}}！”
并以一个 LF 结束。命令沿用 README 记载的公开入口并开启 --index，
分别覆盖默认文本格式（省略 --format）与 --format html：

    python -m newsletter_preview \\
        --contacts contacts.csv --template template.txt \\
        --segment newsletter --out previews --index

每种格式先跑无故障对照：退出 0、标准输出与标准错误均为空，输出目录
恰好包含两份连续编号预览、report.json 与 index.html；报告的分组命中、
排除、最终预览计数依次为 2、0、2，排除明细为空，预览清单按甲、乙
顺序对应邮箱与实际扩展名。

随后在相同输入的新输出目录中，令创建 report.json 的 open 调用在文件
创建前抛出 PermissionError，底层原因固定为 report-write-denied，其余
文件操作不受影响。预期真实进程退出 2，标准输出为空，标准错误包含
report.json 的路径与上述原因、无 Traceback；两份预览完整保留且与同
格式对照逐字节一致，report.json 与 index.html 均不存在，目录中没有
其他新增文件。

失败注入不依赖管理员权限、不修改真实目录权限、不耗尽磁盘空间，也不
使用任何平台专用接口（无 RLIMIT、无 chmod、无信号量）：测试把一个
标准库 sitecustomize 模块放进独立临时目录，仅在故障运行时将该目录
prepend 到子进程 PYTHONPATH，并以环境变量传入目标 report.json 路径；
该模块包装 builtins.open，仅对目标路径在创建前抛出
PermissionError("report-write-denied")，其余调用原样转发。sitecustomize
由 Python 启动时的 site 模块自动导入，Windows 与常见 Linux 环境行为
一致，核心用例在任何平台都不跳过。

仅依赖 Python 3 标准库，完全离线；邮箱使用 RFC 2606 保留的
example.invalid 虚构域名。从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出、标准错误与落盘文件字节；不直接调用内部
函数。每个场景使用独立临时目录，结束后自动清理，不触碰已有文件。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 固定合成输入（逐字节固定）：两行 CSV 均以 LF 结束；模板以一个 LF 结束。
CONTACTS_BYTES = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,b@example.invalid,newsletter\n"
).encode("utf-8")
TEMPLATE_BYTES = "你好，{{name}}！\n".encode("utf-8")
SEGMENT = "newsletter"
EMAIL_JIA = "a@example.invalid"
EMAIL_YI = "b@example.invalid"

# 注入的底层失败原因（固定），须原样出现在标准错误中。
DENY_REASON = "report-write-denied"
# 向子进程传递目标 report.json 路径的环境变量名。
DENY_PATH_ENV = "NEWSLETTER_PREVIEW_TEST_DENY_OPEN_PATH"

# 故障注入模块：仅在故障运行时通过 PYTHONPATH 进入子进程。site 模块在
# 解释器启动时自动导入它；它包装 builtins.open，仅对环境变量指定的
# 目标路径在创建前抛出 PermissionError，其余 open 调用原样转发，因此
# 输入读取与预览写入完全不受影响，目标文件根本不会被创建。
SITECUSTOMIZE = '''\
"""测试注入：仅对指定路径的 open 在创建前抛出 PermissionError。"""
import builtins
import os

_TARGET = os.environ.get(%r)

if _TARGET:
    _target = os.path.normcase(os.path.abspath(_TARGET))
    _real_open = builtins.open

    def _guarded_open(file, *args, **kwargs):
        try:
            path = os.fspath(file)
        except TypeError:
            path = None
        if (
            isinstance(path, str)
            and os.path.normcase(os.path.abspath(path)) == _target
        ):
            raise PermissionError(%r)
        return _real_open(file, *args, **kwargs)

    builtins.open = _guarded_open
''' % (DENY_PATH_ENV, DENY_REASON)


def _expected_report(extension):
    """固定输入下两种格式共用的报告内容（仅预览扩展名不同）。"""
    return {
        "template": TEMPLATE_BYTES.decode("utf-8"),
        "segment": SEGMENT,
        "segment_count": 2,
        "excluded_count": 0,
        "excluded_contacts": [],
        "matched_count": 2,
        "previews": [
            {"email": EMAIL_JIA, "file": f"preview-0001.{extension}"},
            {"email": EMAIL_YI, "file": f"preview-0002.{extension}"},
        ],
    }


class ReportWriteFailureTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write_input("contacts.csv", CONTACTS_BYTES)
        self.template_path = self._write_input("template.txt", TEMPLATE_BYTES)
        # 注入目录：仅故障运行把它 prepend 到子进程 PYTHONPATH。
        self.inject_dir = os.path.join(self.tmp, "inject")
        os.mkdir(self.inject_dir)
        with open(
            os.path.join(self.inject_dir, "sitecustomize.py"),
            "w",
            encoding="utf-8",
            newline="",
        ) as fh:
            fh.write(SITECUSTOMIZE)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_input(self, name, data):
        """以二进制写入固定字节的输入文件，返回路径。"""
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _run(self, out_path, fmt=None, deny_report=False):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。

        fmt 为 None 时省略 --format（即默认文本格式）；deny_report 为
        True 时启用 sitecustomize 注入，令该输出目录中 report.json 的
        创建在文件创建前以 PermissionError(DENY_REASON) 失败。
        """
        env = dict(os.environ)
        # 固定子进程 stdio 编码，保证各平台下 stderr 可按 UTF-8 解码。
        env["PYTHONIOENCODING"] = "utf-8"
        pythonpath = [PROJECT_ROOT]
        if deny_report:
            pythonpath.insert(0, self.inject_dir)
            env[DENY_PATH_ENV] = os.path.join(out_path, "report.json")
        env["PYTHONPATH"] = (
            os.pathsep.join(pythonpath)
            + os.pathsep
            + env.get("PYTHONPATH", "")
        )
        argv = [
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
            "--index",
        ]
        if fmt is not None:
            argv.extend(["--format", fmt])
        return subprocess.run(
            argv,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _read_bytes(self, out_path, name):
        with open(os.path.join(out_path, name), "rb") as fh:
            return fh.read()

    def _run_control(self, label, fmt, extension):
        """无故障对照：核对退出结果、文件清单、报告与预览内容，
        返回输出目录路径（供故障用例逐字节比对预览）。"""
        out_path = os.path.join(self.tmp, f"out-{label}-control")
        result = self._run(out_path, fmt=fmt)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

        # 目录恰好包含两份连续编号预览、report.json 与 index.html。
        preview_names = [f"preview-0001.{extension}",
                         f"preview-0002.{extension}"]
        self.assertEqual(
            sorted(os.listdir(out_path)),
            sorted(preview_names + ["report.json", "index.html"]),
        )

        # 报告：计数 2、0、2，排除明细为空，预览清单按甲、乙顺序
        # 对应邮箱与实际扩展名。
        with open(
            os.path.join(out_path, "report.json"), encoding="utf-8"
        ) as fh:
            self.assertEqual(json.load(fh), _expected_report(extension))

        # 预览正文：替换结果与末尾 LF 保留；html 为声明 UTF-8 的完整
        # 文档，正文在 pre 中按字面显示（本样例无需转义的字符）。
        if extension == "txt":
            self.assertEqual(
                self._read_bytes(out_path, preview_names[0]),
                "你好，甲！\n".encode("utf-8"),
            )
            self.assertEqual(
                self._read_bytes(out_path, preview_names[1]),
                "你好，乙！\n".encode("utf-8"),
            )
        else:
            for name, body in ((preview_names[0], "你好，甲！"),
                               (preview_names[1], "你好，乙！")):
                doc = self._read_bytes(out_path, name).decode("utf-8")
                self.assertTrue(doc.startswith("<!DOCTYPE html>"))
                self.assertIn('<meta charset="utf-8">', doc)
                self.assertIn(f"<pre>{body}\n</pre>", doc)
        return out_path

    def _run_denied(self, label, fmt, extension, control_out):
        """report.json 创建失败：核对退出结果与落盘状态，并与对照
        逐字节比对保留的预览。"""
        out_path = os.path.join(self.tmp, f"out-{label}-denied")
        result = self._run(out_path, fmt=fmt, deny_report=True)

        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"", msg="拒绝时标准输出必须为空")
        stderr = result.stderr.decode("utf-8")
        report_path = os.path.join(out_path, "report.json")
        # 标准错误点名 report.json 的路径与固定底层原因，且无 Traceback。
        self.assertIn("无法写入输出文件", stderr)
        self.assertIn(report_path, stderr)
        self.assertIn(DENY_REASON, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)

        # 目录中恰好只有两份预览：report.json 与 index.html 均不存在，
        # 也没有其他新增文件。
        preview_names = [f"preview-0001.{extension}",
                         f"preview-0002.{extension}"]
        self.assertEqual(sorted(os.listdir(out_path)), preview_names)
        self.assertFalse(os.path.exists(report_path))
        self.assertFalse(
            os.path.exists(os.path.join(out_path, "index.html"))
        )

        # 两份预览完整保留，与同格式对照逐字节一致。
        for name in preview_names:
            self.assertEqual(
                self._read_bytes(out_path, name),
                self._read_bytes(control_out, name),
                msg=f"{name} 与同格式对照不一致",
            )

    def test_control_default_text_format(self):
        # 默认文本格式（省略 --format）的无故障对照。
        self._run_control("text", None, "txt")

    def test_control_html_format(self):
        # --format html 的无故障对照。
        self._run_control("html", "html", "html")

    def test_report_write_failure_default_text_format(self):
        # 默认文本格式：report.json 创建前以 PermissionError 失败。
        control_out = self._run_control("text", None, "txt")
        self._run_denied("text", None, "txt", control_out)

    def test_report_write_failure_html_format(self):
        # --format html：report.json 创建前以 PermissionError 失败。
        control_out = self._run_control("html", "html", "html")
        self._run_denied("html", "html", "html", control_out)


if __name__ == "__main__":
    unittest.main()
