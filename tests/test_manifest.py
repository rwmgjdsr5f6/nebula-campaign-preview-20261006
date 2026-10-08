"""newsletter_preview --manifest 离线核对清单的黑盒回归测试。

固定合成输入（逐字节固定）：contacts.csv 为 UTF-8，表头
name,email,segment，四条数据行依次为
甲,a@example.invalid,newsletter；乙,a@example.invalid,newsletter；
丙,b@example.invalid,newsletter；丁,d@example.invalid,archive
（甲、乙共享邮箱且都属 newsletter，丙属 newsletter，丁属 archive）；
template.txt 内容为“你好，{{name}}！”并以一个 LF 结束。命令沿用
README 记载的公开入口：

    python -m newsletter_preview \\
        --contacts contacts.csv --template template.txt \\
        --segment newsletter --out previews \\
        --exclude-email b@example.invalid --manifest --index

验收预期：退出 0、标准输出与标准错误均为空；输出目录恰好包含两份
连续编号预览、report.json、index.html 与新增的 manifest.csv。清单为
无 BOM 的 UTF-8 CSV，首行固定 name,email,segment,preview_file，随后
按 CSV 顺序只列甲、乙两条保留记录（共享邮箱各占一行），第四列分别
为 preview-0001.txt、preview-0002.txt，与 report.json 的 previews
逐条对应；被 --exclude-email 移除的丙与未命中分组的丁不进入清单；
报告分组命中、排除、最终预览三个计数依次为 3、1、2。--format html
时仅第四列与预览扩展名改为 .html，其余映射关系不变。零命中或全部
排除仍退出 0，清单只含首行表头加一个 LF。省略 --manifest 时不生成
清单，且预览、报告与索引页与开启时逐字节一致。

前三列必须保存输入 CSV 的 name、email、segment 原文：保留中文、
大小写、首尾空白与连续空格；逗号、双引号与字段内换行（含 LF、字段
内 CRLF 及孤立 CR）经 CSV 转义后重新解析须逐字恢复原值，{{name}}
样式文字也原样保留。本文件以一组带上述全部特殊字符的合成记录核对
往返一致，并断言清单无 BOM、记录顺序与第四列同报告 previews 一致。

写入失败覆盖两个阶段（均只针对 manifest.csv，其余文件操作正常）：
其一为清单 open 在文件创建前抛出 PermissionError（清单不存在），
其二为清单已成功创建（open 成功返回）后的第一次正文 write 在写入
任何字节前抛出 OSError（清单存在但为零字节）；两种故障分别覆盖默认
文本格式与 --format html。索引页在清单之后写出，故清单失败时
index.html 不应产生。预期真实进程退出 2、标准输出为空，标准错误
包含“无法写入输出文件”、manifest.csv 的完整路径与固定底层原因、
无 Traceback；此前已写出的预览与报告完整保留且与无故障对照逐字节
一致，不回滚。输入校验失败（缺必需列）时退出 2、不创建输出目录；
输出目录非空时退出 2、原有文件保留且不生成清单。

失败注入不依赖管理员权限、不修改真实目录权限、不耗尽磁盘空间，也不
使用任何平台专用接口（无 RLIMIT、无 chmod、无信号量）：测试把一个
标准库 sitecustomize 模块放进独立临时目录，仅在故障运行时将该目录
prepend 到子进程 PYTHONPATH，并以两个环境变量传入目标 manifest.csv
路径与故障阶段；该模块包装 builtins.open，对目标路径按阶段在创建前
抛 PermissionError 或在 open 成功后把文件对象包一层代理（代理的第
一次 write 在转发给真实对象前抛出 OSError，上下文管理与其余属性原
样转发，with 语句正常关闭该零字节文件）；对非目标路径的 open 完全
原样转发。sitecustomize 由 Python 启动时的 site 模块自动导入，
Windows 与常见 Linux 环境行为一致，核心用例在任何平台都不跳过；未
设置环境变量时注入完全不生效。

仅依赖 Python 3 标准库，完全离线；邮箱使用 RFC 2606 保留的
example.invalid 虚构域名。从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出、标准错误与落盘文件字节；不直接调用内部
函数。每个场景使用独立临时目录，结束后自动清理，不触碰已有文件。
"""

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 固定合成输入（逐字节固定）：四行 CSV 均以 LF 结束；模板以一个 LF 结束。
CONTACTS_BYTES = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,a@example.invalid,newsletter\n"
    "丙,b@example.invalid,newsletter\n"
    "丁,d@example.invalid,archive\n"
).encode("utf-8")
TEMPLATE_BYTES = "你好，{{name}}！\n".encode("utf-8")
SEGMENT = "newsletter"
EMAIL_SHARED = "a@example.invalid"
EMAIL_BING = "b@example.invalid"
EMAIL_DING = "d@example.invalid"
EXCLUDED_EMAIL = EMAIL_BING

MANIFEST_NAME = "manifest.csv"
MANIFEST_HEADER = ("name", "email", "segment", "preview_file")

# 注入的两个固定底层原因（按故障阶段），须原样出现在标准错误中。
DENY_OPEN_REASON = "manifest-write-denied"
DENY_BODY_REASON = "manifest-body-write-denied"
# 向子进程传递目标 manifest.csv 路径与故障阶段（open/body）的环境变量。
DENY_PATH_ENV = "NEWSLETTER_PREVIEW_TEST_DENY_MANIFEST_PATH"
DENY_MODE_ENV = "NEWSLETTER_PREVIEW_TEST_DENY_MANIFEST_MODE"

# 故障注入模块：仅在故障运行时通过 PYTHONPATH 进入子进程。site 模块在
# 解释器启动时自动导入它；它包装 builtins.open——对环境变量指定的目标
# 路径，mode=open 时在创建前抛 PermissionError（文件不会产生），
# mode=body 时仍调用真实 open（文件以 "x" 成功新建），但把返回对象包
# 成代理：代理第一次 write 在转发前抛出 OSError，故文件创建成功而正文
# 零字节；__enter__/__exit__ 与其余属性全部原样转发，with 块得以正常
# 关闭文件。非目标路径的 open 完全不受影响，输入读取、预览、报告与
# 索引写入逐字节不变。
SITECUSTOMIZE = '''\
"""测试注入：对目标 manifest.csv 按阶段在创建前或首次正文 write 前抛错。"""
import builtins
import os

_TARGET = os.environ.get(%r)
_MODE = os.environ.get(%r, "open")

if _TARGET:
    _target = os.path.normcase(os.path.abspath(_TARGET))
    _real_open = builtins.open

    class _DenyFirstWrite(object):
        """包装文件对象：仅第一次 write 在转发前抛出 OSError。

        其余属性与方法（含 writable、flush、close 及上下文管理）全部
        原样转发给被包装的真实文件对象；第一次 write 不转发任何字节，
        因此真实缓冲与磁盘文件都保持为空。
        """

        def __init__(self, raw):
            self.__dict__["_raw"] = raw
            self.__dict__["_used"] = False

        def write(self, data):
            if not self.__dict__["_used"]:
                self.__dict__["_used"] = True
                raise OSError(%r)
            return self._raw.write(data)

        def __enter__(self):
            self._raw.__enter__()
            return self

        def __exit__(self, *exc):
            return self._raw.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self.__dict__["_raw"], name)

    def _guarded_open(file, *args, **kwargs):
        try:
            path = os.fspath(file)
        except TypeError:
            path = None
        if (
            isinstance(path, str)
            and os.path.normcase(os.path.abspath(path)) == _target
        ):
            if _MODE == "body":
                # 真实 open 照常成功：文件被创建，随后第一次正文 write 失败。
                return _DenyFirstWrite(_real_open(file, *args, **kwargs))
            raise PermissionError(%r)
        return _real_open(file, *args, **kwargs)

    builtins.open = _guarded_open
''' % (DENY_PATH_ENV, DENY_MODE_ENV, DENY_BODY_REASON, DENY_OPEN_REASON)


def _expected_manifest_bytes(extension):
    """四条验收记录在排除 b@example.invalid 后清单的逐字节预期内容。

    甲、乙共享 a@example.invalid 且都属 newsletter，按 CSV 顺序各占一行；
    丙被排除、丁未命中分组，均不出现。
    """
    return (
        "name,email,segment,preview_file\n"
        f"甲,{EMAIL_SHARED},newsletter,preview-0001.{extension}\n"
        f"乙,{EMAIL_SHARED},newsletter,preview-0002.{extension}\n"
    ).encode("utf-8")


def _expected_report(extension):
    """固定输入下两种格式共用的报告内容（仅预览扩展名不同）。"""
    return {
        "template": TEMPLATE_BYTES.decode("utf-8"),
        "segment": SEGMENT,
        "segment_count": 3,
        "excluded_count": 1,
        "excluded_contacts": [
            {"name": "丙", "email": EMAIL_BING},
        ],
        "matched_count": 2,
        "previews": [
            {"email": EMAIL_SHARED, "file": f"preview-0001.{extension}"},
            {"email": EMAIL_SHARED, "file": f"preview-0002.{extension}"},
        ],
    }


def _parse_manifest(raw_bytes):
    """以 UTF-8 解析清单字节，返回 (表头, 数据行列表)。"""
    reader = csv.reader(io.StringIO(raw_bytes.decode("utf-8")))
    rows = list(reader)
    return rows[0], rows[1:]


class ManifestTestCase(unittest.TestCase):
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
        if isinstance(data, bytes):
            with open(path, "wb") as fh:
                fh.write(data)
        else:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(data)
        return path

    def _run(self, out_path, fmt=None, manifest=True, index=True,
             contacts_path=None, segment=None, extra_argv=None,
             deny_mode=None):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。

        fmt 为 None 时省略 --format（即默认文本格式）；manifest/index
        控制是否带上对应无值参数；deny_mode 为 "open"/"body" 时启用
        sitecustomize 注入，令该输出目录中 manifest.csv 的对应阶段失败。
        """
        env = dict(os.environ)
        # 固定子进程 stdio 编码，保证各平台下 stderr 可按 UTF-8 解码。
        env["PYTHONIOENCODING"] = "utf-8"
        pythonpath = [PROJECT_ROOT]
        if deny_mode is not None:
            pythonpath.insert(0, self.inject_dir)
            env[DENY_PATH_ENV] = os.path.join(out_path, MANIFEST_NAME)
            env[DENY_MODE_ENV] = deny_mode
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
            contacts_path or self.contacts_path,
            "--template",
            self.template_path,
            "--segment",
            segment if segment is not None else SEGMENT,
            "--out",
            out_path,
            "--exclude-email",
            EXCLUDED_EMAIL,
        ]
        if fmt is not None:
            argv.extend(["--format", fmt])
        if manifest:
            argv.append("--manifest")
        if index:
            argv.append("--index")
        if extra_argv:
            argv.extend(extra_argv)
        return subprocess.run(
            argv,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _read_bytes(self, out_path, name):
        with open(os.path.join(out_path, name), "rb") as fh:
            return fh.read()

    def _assert_acceptance_outputs(self, out_path, extension):
        """核对验收命令的全部产物：计数、文件清单、预览与清单内容。"""
        preview_names = [f"preview-0001.{extension}",
                         f"preview-0002.{extension}"]
        self.assertEqual(
            sorted(os.listdir(out_path)),
            sorted(preview_names + ["report.json", "index.html",
                                    MANIFEST_NAME]),
        )

        # 报告：分组命中、排除、最终预览计数为 3、1、2，排除明细只有丙，
        # 预览清单按甲、乙顺序对应共享邮箱与实际扩展名。
        with open(
            os.path.join(out_path, "report.json"), encoding="utf-8"
        ) as fh:
            report = json.load(fh)
        self.assertEqual(report, _expected_report(extension))

        # 清单逐字节等于固定预期：无 BOM、LF 行结束、只含甲、乙两行。
        raw = self._read_bytes(out_path, MANIFEST_NAME)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "清单不得带 BOM")
        self.assertEqual(raw, _expected_manifest_bytes(extension))

        # 重新解析：表头固定，数据行与报告 previews 逐条对应（第四列与
        # 邮箱），行数等于 matched_count；共享邮箱的甲、乙各占一行且保持
        # CSV 顺序；丙（被排除）与丁（未命中）不在清单中。
        header, rows = _parse_manifest(raw)
        self.assertEqual(list(header), list(MANIFEST_HEADER))
        self.assertEqual(len(rows), report["matched_count"])
        self.assertEqual(len(rows), len(report["previews"]))
        for index, (row, preview) in enumerate(
            zip(rows, report["previews"])
        ):
            self.assertEqual(row[0], ("甲", "乙")[index])
            self.assertEqual(row[1], EMAIL_SHARED)
            self.assertEqual(row[2], SEGMENT)
            self.assertEqual(row[3], preview["file"])
            self.assertEqual(row[1], preview["email"])
        self.assertEqual([r[3] for r in rows], preview_names)

        # 预览正文：模板替换结果与末尾 LF 保留（html 为完整 UTF-8 文档）。
        if extension == "txt":
            for name, who in ((preview_names[0], "甲"),
                              (preview_names[1], "乙")):
                self.assertEqual(
                    self._read_bytes(out_path, name),
                    f"你好，{who}！\n".encode("utf-8"),
                )
        else:
            for name, who in ((preview_names[0], "甲"),
                              (preview_names[1], "乙")):
                doc = self._read_bytes(out_path, name).decode("utf-8")
                self.assertTrue(doc.startswith("<!DOCTYPE html>"))
                self.assertIn('<meta charset="utf-8">', doc)
                self.assertIn(f"<pre>你好，{who}！\n</pre>", doc)

    def test_manifest_default_text_format(self):
        # 主验收：默认文本格式，--exclude-email b@… --manifest --index。
        out_path = os.path.join(self.tmp, "out-text")
        result = self._run(out_path)
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self._assert_acceptance_outputs(out_path, "txt")

    def test_manifest_html_format_uses_html_extension(self):
        # --format html：仅第四列与预览扩展名映射为 .html，其余不变。
        out_path = os.path.join(self.tmp, "out-html")
        result = self._run(out_path, fmt="html")
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self._assert_acceptance_outputs(out_path, "html")

    def test_manifest_without_index(self):
        # --manifest 不依赖 --index：只多 manifest.csv，不产生 index.html。
        out_path = os.path.join(self.tmp, "out-no-index")
        result = self._run(out_path, index=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        names = os.listdir(out_path)
        self.assertEqual(
            sorted(names),
            ["manifest.csv", "preview-0001.txt", "preview-0002.txt",
             "report.json"],
        )
        self.assertEqual(
            self._read_bytes(out_path, MANIFEST_NAME),
            _expected_manifest_bytes("txt"),
        )

    def test_zero_segment_hit_manifest_header_only(self):
        # 零命中：退出 0，清单仅含表头加一个 LF，报告三计数均为 0。
        out_path = os.path.join(self.tmp, "out-zero")
        result = self._run(out_path, segment="no-such-segment", index=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        raw = self._read_bytes(out_path, MANIFEST_NAME)
        self.assertEqual(raw, b"name,email,segment,preview_file\n")
        header, rows = _parse_manifest(raw)
        self.assertEqual(list(header), list(MANIFEST_HEADER))
        self.assertEqual(rows, [])
        with open(
            os.path.join(out_path, "report.json"), encoding="utf-8"
        ) as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (0, 0, 0),
        )
        self.assertEqual(report["previews"], [])
        self.assertEqual(
            sorted(os.listdir(out_path)), ["manifest.csv", "report.json"]
        )

    def test_all_excluded_manifest_header_only(self):
        # 全部排除（再排除共享邮箱 a@…）：退出 0，清单仅含表头，报告
        # 三计数为 3、3、0，无预览文件；排除明细按 CSV 顺序列甲、乙、丙。
        out_path = os.path.join(self.tmp, "out-all-excluded")
        result = self._run(
            out_path,
            index=False,
            extra_argv=["--exclude-email", EMAIL_SHARED],
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        raw = self._read_bytes(out_path, MANIFEST_NAME)
        self.assertEqual(raw, b"name,email,segment,preview_file\n")
        _, rows = _parse_manifest(raw)
        self.assertEqual(rows, [])
        with open(
            os.path.join(out_path, "report.json"), encoding="utf-8"
        ) as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (3, 3, 0),
        )
        self.assertEqual(
            report["excluded_contacts"],
            [
                {"name": "甲", "email": EMAIL_SHARED},
                {"name": "乙", "email": EMAIL_SHARED},
                {"name": "丙", "email": EMAIL_BING},
            ],
        )
        self.assertEqual(
            sorted(os.listdir(out_path)), ["manifest.csv", "report.json"]
        )

    def test_omitting_manifest_changes_nothing_else(self):
        # 省略 --manifest：不生成清单，且预览、报告、索引与开启时逐字节
        # 一致（同输入、同格式、同排除）。
        base = os.path.join(self.tmp, "out-omit")
        enabled = os.path.join(self.tmp, "out-enable")
        result_base = self._run(base, manifest=True)
        result_enabled = self._run(enabled, manifest=False)
        self.assertEqual(result_base.returncode, 0)
        self.assertEqual(result_enabled.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(enabled, MANIFEST_NAME)))
        for name in ("preview-0001.txt", "preview-0002.txt", "report.json",
                     "index.html"):
            self.assertEqual(
                self._read_bytes(base, name),
                self._read_bytes(enabled, name),
                msg=f"{name} 在开启/省略 --manifest 时必须逐字节一致",
            )

    def test_manifest_preserves_special_characters_roundtrip(self):
        # 前三列保存输入 CSV 原文：首尾及连续空格、逗号、双引号、字段内
        # LF/孤立 CR/CRLF、中文、大小写与 {{name}} 样式文字，重新解析后
        # 逐字恢复；该记录是唯一保留记录（segment 本身也含逗号），另有
        # 一条 archive 记录未命中分组、不得进入清单。
        tricky_name = ' 张  三 ",x" {{name}}\n二行\rcrlf\r\n尾 '
        tricky_email = " A@Example.INVALID "
        tricky_segment = "news, letter"
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(MANIFEST_HEADER[:3])
        writer.writerow((tricky_name, tricky_email, tricky_segment))
        writer.writerow(("丁", EMAIL_DING, "archive"))
        contacts = self._write_input("tricky.csv", buf.getvalue())

        out_path = os.path.join(self.tmp, "out-tricky")
        result = self._run(
            out_path,
            contacts_path=contacts,
            segment=tricky_segment,
            index=False,
        )
        # 固定的排除值 b@example.invalid 不在该输入中，不影响结果；确认
        # 退出成功且只有一条保留记录。
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stderr, b"")

        raw = self._read_bytes(out_path, MANIFEST_NAME)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "清单不得带 BOM")
        # 含特殊字符的字段在落盘字节中必然经过引号/转义。
        self.assertIn(b'"', raw)
        header, rows = _parse_manifest(raw)
        self.assertEqual(list(header), list(MANIFEST_HEADER))
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0],
            [tricky_name, tricky_email, tricky_segment,
             "preview-0001.txt"],
        )

        # 与报告 previews 逐条对应：第四列文件名、邮箱原文一致。
        with open(
            os.path.join(out_path, "report.json"), encoding="utf-8"
        ) as fh:
            report = json.load(fh)
        self.assertEqual(report["matched_count"], 1)
        self.assertEqual(
            report["previews"],
            [{"email": tricky_email, "file": "preview-0001.txt"}],
        )

        # {{name}} 在姓名原文中原样保留；预览替换只发生一次，字段值里的
        # {{name}} 不被二次替换（预览正文中应能找到该字面量）。
        preview = self._read_bytes(out_path, "preview-0001.txt").decode(
            "utf-8"
        )
        self.assertIn("{{name}}", preview)
        self.assertTrue(preview.startswith("你好，"))

    def _run_control_and_denied(self, fmt, extension, deny_mode, reason):
        """先跑无故障对照，再在新目录注入指定阶段的清单写入故障。"""
        control = os.path.join(self.tmp, f"out-{fmt}-{deny_mode}-control")
        denied = os.path.join(self.tmp, f"out-{fmt}-{deny_mode}-denied")
        control_result = self._run(control, fmt=fmt)
        self.assertEqual(
            control_result.returncode,
            0,
            msg=f"stderr: {control_result.stderr.decode('utf-8', 'replace')}",
        )
        denied_result = self._run(denied, fmt=fmt, deny_mode=deny_mode)

        self.assertEqual(
            denied_result.returncode,
            2,
            msg=f"stderr: {denied_result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(
            denied_result.stdout, b"", msg="拒绝时标准输出必须为空"
        )
        stderr = denied_result.stderr.decode("utf-8")
        manifest_path = os.path.join(denied, MANIFEST_NAME)
        # 标准错误点名无法写入输出文件、manifest.csv 完整路径与固定底层
        # 原因，且无 Traceback。
        self.assertIn("无法写入输出文件", stderr)
        self.assertIn(manifest_path, stderr)
        self.assertIn(reason, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)

        preview_names = [f"preview-0001.{extension}",
                         f"preview-0002.{extension}"]
        if deny_mode == "open":
            # 创建前失败：manifest.csv 与 index.html 均不存在；目录中只有
            # 此前写出的两份预览与完整报告。
            self.assertEqual(sorted(os.listdir(denied)),
                             sorted(preview_names + ["report.json"]))
            self.assertFalse(os.path.exists(manifest_path))
            self.assertFalse(os.path.exists(os.path.join(denied, "index.html")))
        else:
            # 首次正文 write 失败：manifest.csv 存在但零字节；index.html
            # 在清单之后写出，故不存在；其余为两份预览与完整报告。
            self.assertEqual(
                sorted(os.listdir(denied)),
                sorted(preview_names + ["report.json", MANIFEST_NAME]),
            )
            self.assertTrue(os.path.isfile(manifest_path))
            self.assertEqual(os.path.getsize(manifest_path), 0)
            self.assertEqual(self._read_bytes(denied, MANIFEST_NAME), b"")
            self.assertFalse(
                os.path.exists(os.path.join(denied, "index.html"))
            )

        # 此前写出的预览与报告均保留，与同格式对照逐字节一致（不回滚）。
        for name in preview_names + ["report.json"]:
            self.assertEqual(
                self._read_bytes(denied, name),
                self._read_bytes(control, name),
                msg=f"{name} 与同格式对照不一致",
            )

    def test_manifest_open_failure_text(self):
        # 默认文本格式：manifest.csv 创建前以 PermissionError 失败。
        self._run_control_and_denied(
            "text", "txt", "open", DENY_OPEN_REASON
        )

    def test_manifest_open_failure_html(self):
        # --format html：manifest.csv 创建前以 PermissionError 失败。
        self._run_control_and_denied(
            "html", "html", "open", DENY_OPEN_REASON
        )

    def test_manifest_body_write_failure_text(self):
        # 默认文本格式：manifest.csv 创建成功后首次正文 write 前失败。
        self._run_control_and_denied(
            "text", "txt", "body", DENY_BODY_REASON
        )

    def test_manifest_body_write_failure_html(self):
        # --format html：manifest.csv 创建成功后首次正文 write 前失败。
        self._run_control_and_denied(
            "html", "html", "body", DENY_BODY_REASON
        )

    def test_nonempty_output_dir_rejected_with_manifest(self):
        # 输出目录非空：退出 2，原有文件原样保留，不生成任何新产物。
        out_path = os.path.join(self.tmp, "out-nonempty")
        os.mkdir(out_path)
        sentinel = os.path.join(out_path, "existing.txt")
        with open(sentinel, "wb") as fh:
            fh.write(b"keep\n")
        result = self._run(out_path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("输出目录非空", result.stderr.decode("utf-8"))
        self.assertEqual(os.listdir(out_path), ["existing.txt"])
        self.assertEqual(self._read_bytes(out_path, "existing.txt"), b"keep\n")
        self.assertFalse(os.path.exists(os.path.join(out_path, MANIFEST_NAME)))

    def test_validation_failure_creates_nothing_with_manifest(self):
        # 输入校验失败（缺 segment 列）：退出 2、无 Traceback，输出目录
        # 不被创建，清单自然也不存在。
        bad_path = self._write_input(
            "bad.csv", b"name,email\nx,y@example.invalid\n"
        )
        out_path = os.path.join(self.tmp, "out-invalid")
        result = self._run(out_path, contacts_path=bad_path)
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("segment", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_path))
        self.assertFalse(os.path.exists(os.path.join(out_path, MANIFEST_NAME)))


if __name__ == "__main__":
    unittest.main()
