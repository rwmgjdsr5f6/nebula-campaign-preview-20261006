"""newsletter_preview report.json 创建成功后正文写入失败的回归测试。

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

随后在相同输入的另一个新输出目录中，只让 report.json 成功创建后的
第一次正文写入失败：目标文件的 open 照常返回（文件已被创建），但
返回文件对象的第一次 write 在写入任何字节前抛出
OSError("report-body-write-denied")，其余文件操作（输入读取、两份
预览写出、目录准备等）完全正常。预期真实进程退出 2，标准输出为空，
标准错误包含无法写入输出文件、report.json 的完整路径与上述原因、
无 Traceback；两份预览完整保留且与同格式对照逐字节一致，report.json
存在但为零字节（创建成功、正文一个字节都未写入），index.html 不
存在（它在报告之后才写出，根本未被执行），目录中没有其他新增文件。

失败注入不依赖管理员权限、不修改真实目录权限、不耗尽磁盘空间，也不
使用任何平台专用接口（无 RLIMIT、无 chmod、无信号量）：测试把一个
标准库 sitecustomize 模块放进独立临时目录，仅在故障运行时将该目录
prepend 到子进程 PYTHONPATH，并以环境变量传入目标 report.json 路径；
该模块包装 builtins.open，仅对目标路径把 open 返回的真实文件对象再
包一层代理——open 本身原样转发（文件照常创建），代理只拦截第一次
正文 write 并在转发前抛出 OSError，其余属性与方法（含 with 所需的
上下文管理）原样委托给真实文件；with 退出时真实文件正常关闭，磁盘
上留下零字节文件。对其他路径的 open 完全原样转发。sitecustomize 由
Python 启动时的 site 模块自动导入，Windows 与常见 Linux 环境行为
一致，核心用例在任何平台都不跳过；未设置环境变量时注入完全不生效。

仅依赖 Python 3 标准库，完全离线；邮箱使用 RFC 2606 保留的
example.invalid 虚构域名。从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出、标准错误与落盘文件字节；不直接调用内部
函数。每个场景使用独立临时目录，结束后自动清理，不触碰已有文件，
固定输入文件运行后逐字节不变。
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
DENY_REASON = "report-body-write-denied"
# 向子进程传递目标 report.json 路径的环境变量名。
DENY_PATH_ENV = "NEWSLETTER_PREVIEW_TEST_DENY_REPORT_BODY_PATH"

# 故障注入模块：仅在故障运行时通过 PYTHONPATH 进入子进程。site 模块在
# 解释器启动时自动导入它；它包装 builtins.open：对环境变量指定的目标
# 路径，open 仍原样调用（文件照常创建），但把返回的真实文件对象包成
# _DenyBodyWrite 代理；代理只拦截正文写入——第一次 write/writelines
# 在转发给真实文件前抛出 OSError，因此正文一个字节都不会落盘；其余
# 属性与方法（含 with 的 __enter__/__exit__、close）原样委托，with
# 退出时真实文件正常关闭，留下零字节文件。对其他路径的 open 完全原样
# 转发，输入读取与预览写出不受任何影响。未设置环境变量时本模块不改变
# 任何行为。
SITECUSTOMIZE = '''\
"""测试注入：目标文件创建成功后，第一次正文 write 在写字节前抛 OSError。"""
import builtins
import os

_TARGET = os.environ.get(%r)

if _TARGET:
    _target = os.path.normcase(os.path.abspath(_TARGET))
    _real_open = builtins.open

    class _DenyBodyWrite(object):
        """open 成功后的文件对象代理：仅拦截第一次正文写入。

        构造时真实文件已由 open 创建；write/writelines 在转发给真实
        文件之前直接抛出 OSError，故不会写入任何字节；with 进入时返回
        代理自身（保证 as 目标上的 write 被拦截），退出时委托真实文件
        正常关闭；其余属性全部原样委托。
        """

        def __init__(self, real_file):
            self._real = real_file

        def write(self, data):
            raise OSError(%r)

        def writelines(self, lines):
            raise OSError(%r)

        def __enter__(self):
            self._real.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._real.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _guarded_open(file, *args, **kwargs):
        try:
            path = os.fspath(file)
        except TypeError:
            path = None
        # open 始终原样转发：目标文件也必须先成功创建，随后才包装其
        # 文件对象，使故障精确发生在“创建成功后的第一次正文写入”。
        real_file = _real_open(file, *args, **kwargs)
        if (
            isinstance(path, str)
            and os.path.normcase(os.path.abspath(path)) == _target
        ):
            return _DenyBodyWrite(real_file)
        return real_file

    builtins.open = _guarded_open
''' % (DENY_PATH_ENV, DENY_REASON, DENY_REASON)


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


class ReportBodyWriteFailureTestCase(unittest.TestCase):
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

    def _run(self, out_path, fmt=None, deny_body=False):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。

        fmt 为 None 时省略 --format（即默认文本格式）；deny_body 为
        True 时启用 sitecustomize 注入：该输出目录中 report.json 的
        open 照常成功（文件被创建），但第一次正文 write 在写入任何
        字节前以 OSError(DENY_REASON) 失败，其余文件操作原样转发。
        """
        env = dict(os.environ)
        # 固定子进程 stdio 编码，保证各平台下 stderr 可按 UTF-8 解码。
        env["PYTHONIOENCODING"] = "utf-8"
        pythonpath = [PROJECT_ROOT]
        if deny_body:
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

    def _read_bytes(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    def _assert_inputs_unchanged(self):
        """故障运行不得改动固定输入文件：逐字节仍为初始内容。"""
        self.assertEqual(self._read_bytes(self.contacts_path), CONTACTS_BYTES)
        self.assertEqual(self._read_bytes(self.template_path), TEMPLATE_BYTES)

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
                self._read_bytes(os.path.join(out_path, preview_names[0])),
                "你好，甲！\n".encode("utf-8"),
            )
            self.assertEqual(
                self._read_bytes(os.path.join(out_path, preview_names[1])),
                "你好，乙！\n".encode("utf-8"),
            )
        else:
            for name, body in ((preview_names[0], "你好，甲！"),
                               (preview_names[1], "你好，乙！")):
                doc = self._read_bytes(os.path.join(out_path, name)).decode(
                    "utf-8"
                )
                self.assertTrue(doc.startswith("<!DOCTYPE html>"))
                self.assertIn('<meta charset="utf-8">', doc)
                self.assertIn(f"<pre>{body}\n</pre>", doc)
        return out_path

    def _run_body_denied(self, label, fmt, extension, control_out):
        """report.json 创建成功后正文写入失败：核对退出结果与落盘
        状态（零字节报告、无索引页、预览完整保留），并与同格式对照
        逐字节比对两份预览。"""
        out_path = os.path.join(self.tmp, f"out-{label}-body-denied")
        result = self._run(out_path, fmt=fmt, deny_body=True)

        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"", msg="拒绝时标准输出必须为空")
        stderr = result.stderr.decode("utf-8")
        report_path = os.path.join(out_path, "report.json")
        index_path = os.path.join(out_path, "index.html")
        # 标准错误点名无法写入输出文件、report.json 的完整路径与固定
        # 底层原因，且无 Traceback。
        self.assertIn("无法写入输出文件", stderr)
        self.assertIn(report_path, stderr)
        self.assertIn(DENY_REASON, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)

        # 目录中恰好只有两份预览与零字节 report.json：index.html 不
        # 存在（报告之后才写出，未执行到），也没有其他新增文件。
        preview_names = [f"preview-0001.{extension}",
                         f"preview-0002.{extension}"]
        self.assertEqual(
            sorted(os.listdir(out_path)),
            sorted(preview_names + ["report.json"]),
        )
        # report.json 创建成功但正文一个字节都未写入：是常规文件且
        # 实际读出零字节（不是不存在，也不是残留部分内容）。
        self.assertTrue(os.path.isfile(report_path))
        self.assertEqual(os.path.getsize(report_path), 0)
        self.assertEqual(self._read_bytes(report_path), b"")
        self.assertFalse(os.path.exists(index_path))

        # 两份预览完整保留，与同格式对照逐字节一致。
        for name in preview_names:
            self.assertEqual(
                self._read_bytes(os.path.join(out_path, name)),
                self._read_bytes(os.path.join(control_out, name)),
                msg=f"{name} 与同格式对照不一致",
            )

        # 固定输入文件保持不变。
        self._assert_inputs_unchanged()

    def test_control_default_text_format(self):
        # 默认文本格式（省略 --format）的无故障对照。
        self._run_control("text", None, "txt")

    def test_control_html_format(self):
        # --format html 的无故障对照。
        self._run_control("html", "html", "html")

    def test_report_body_write_failure_default_text_format(self):
        # 默认文本格式：report.json 创建后第一次正文写入前以 OSError 失败。
        control_out = self._run_control("text", None, "txt")
        self._run_body_denied("text", None, "txt", control_out)

    def test_report_body_write_failure_html_format(self):
        # --format html：report.json 创建后第一次正文写入前以 OSError 失败。
        control_out = self._run_control("html", "html", "html")
        self._run_body_denied("html", "html", "html", control_out)


if __name__ == "__main__":
    unittest.main()
