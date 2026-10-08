"""newsletter_preview --manifest 离线核对清单的回归测试。

覆盖无值参数 --manifest 的公开约定：开启后在输出目录额外生成
manifest.csv，同输入、同格式（无论是否同时开启 --index）下与未开启
的运行逐字节一致，清单是唯一新增文件。清单为无 BOM 的 UTF-8 CSV：
首行固定 name,email,segment,preview_file（CRLF 行结束），其后每行
对应一条已生成预览的联系人记录，记录沿用 CSV 顺序，共享邮箱的联系人
各占一行；前三列保存输入 CSV 对应字段原文——保留中文、大小写、首尾
空白与连续空格，逗号、双引号与字段内换行经标准 CSV 转义后可用任意
CSV 解析器恢复原值，{{name}} 样式文字原样保留；末列只写同目录预览
文件名（text 为 .txt、html 为 .html），与 report.json 的 previews
逐条对应。未命中分组或已被任一排除来源（--exclude-email 与
--exclude-file）移除的记录不进入清单；零命中及全部排除都退出 0，
清单仅含表头。编号、报告计数、变量替换、索引内容及其他文件字节均不
变。失败语义沿用既有约定：输入校验失败或输出目录非空退出 2，不创建
新产物且保留已有文件；清单写入失败退出 2，标准错误包含 manifest.csv
路径与底层原因且无 Traceback，此前写出的文件允许保留；清单在
index.html 之前写出，故清单失败时索引页不生成。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。清单写入失败用例不使用
RLIMIT、信号或权限变更：把一个标准库 sitecustomize 模块放进独立临时
目录，仅在故障运行时 prepend 到子进程 PYTHONPATH，由解释器启动时的
site 模块自动导入；该模块包装 builtins.open，仅对环境变量指定的
manifest.csv 目标路径在创建前抛出 PermissionError（文件根本不会被
创建），其余调用原样转发，因此 Windows 与常见 Linux 环境行为一致，
任何平台都不跳过。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出/错误与落盘文件字节；不直接调用内部函数。
每个样例使用独立临时目录，结束后自动清理。
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

# 验收固定样例：甲、乙同属 newsletter 且共享 a@example.invalid；
# 丙属 newsletter、邮箱 b@example.invalid；丁属 archive、邮箱
# d@example.invalid。
CONTACTS = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,a@example.invalid,newsletter\n"
    "丙,b@example.invalid,newsletter\n"
    "丁,d@example.invalid,archive\n"
)
TEMPLATE = "你好，{{name}}！\n"
SEGMENT = "newsletter"
EMAIL_SHARED = "a@example.invalid"
EMAIL_BING = "b@example.invalid"

MANIFEST_HEADER = ["name", "email", "segment", "preview_file"]

# 字段保真验收的固定合成输入（无表头外的引号不属于值）：
# 姓名含逗号与中文；邮箱带首尾空白；分组含连续空格；下一条姓名含双
# 引号与字段内 LF；再一条姓名就是 {{name}} 样式文字。
SPECIAL_CONTACTS = (
    "name,email,segment\n"
    '"张,三"," z@x.invalid ","news  letter"\n'
    '"李""四\n换行",q@example.invalid,"news  letter"\n'
    "{{name}},r@example.invalid,\"news  letter\"\n"
)
SPECIAL_SEGMENT = "news  letter"
SPECIAL_EXPECTED = [
    ["张,三", " z@x.invalid ", "news  letter", None],
    ['李"四\n换行', "q@example.invalid", "news  letter", None],
    ["{{name}}", "r@example.invalid", "news  letter", None],
]

# 注入的底层失败原因（固定），须原样出现在标准错误中。
DENY_REASON = "manifest-write-denied"
# 向子进程传递目标 manifest.csv 路径的环境变量名。
DENY_PATH_ENV = "NEWSLETTER_PREVIEW_TEST_DENY_MANIFEST_PATH"

# 故障注入模块：仅在故障运行时通过 PYTHONPATH 进入子进程。site 模块在
# 解释器启动时自动导入它；它包装 builtins.open，仅对环境变量指定的
# 目标路径在创建前抛出 PermissionError，其余 open 调用原样转发，因此
# 输入读取、逐人预览与报告写入完全不受影响，目标文件根本不会被创建。
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


class ManifestTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS)
        self.template_path = self._write("template.txt", TEMPLATE)
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

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _run(self, out_path, extra_args=(), fmt=None, manifest=True,
             index=False, contacts_path=None, template_path=None,
             segment=SEGMENT, deny_manifest=False):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。

        fmt 为 None 时省略 --format（即默认 text 格式）；manifest 为
        True 时追加 --manifest；index 为 True 时追加 --index。
        deny_manifest 为 True 时启用 sitecustomize 注入，令该输出目录
        中 manifest.csv 的创建在文件创建前以 PermissionError 失败。
        """
        env = dict(os.environ)
        # 固定子进程 stdio 编码，保证各平台下 stderr 可按 UTF-8 解码。
        env["PYTHONIOENCODING"] = "utf-8"
        pythonpath = [PROJECT_ROOT]
        if deny_manifest:
            pythonpath.insert(0, self.inject_dir)
            env[DENY_PATH_ENV] = os.path.join(out_path, "manifest.csv")
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
            template_path or self.template_path,
            "--segment",
            segment,
            "--out",
            out_path,
        ]
        if fmt is not None:
            argv.extend(["--format", fmt])
        argv.extend(extra_args)
        if manifest:
            argv.append("--manifest")
        if index:
            argv.append("--index")
        return subprocess.run(
            argv,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _read_bytes(self, out_path, name):
        with open(os.path.join(out_path, name), "rb") as fh:
            return fh.read()

    def _parse_manifest(self, out_path):
        """以 UTF-8、newline='' 按标准 CSV 解析 manifest.csv，返回行表。"""
        raw = self._read_bytes(out_path, "manifest.csv")
        # 无 BOM：首字节不是 UTF-8 BOM（EF BB BF）。
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8")
        return list(csv.reader(io.StringIO(text)))

    def _assert_ok_silent(self, result):
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def _assert_manifest_matches(self, out_path, extension, contacts):
        """清单逐行等于 contacts（四列，末列填实际预览文件名），且与
        report.json 的 previews 逐条对应；原始字节为固定表头 + CRLF。"""
        rows = self._parse_manifest(out_path)
        expected = [
            list(contact[:3]) + [f"preview-{idx:04d}.{extension}"]
            for idx, contact in enumerate(contacts, start=1)
        ]
        self.assertEqual(rows, [MANIFEST_HEADER] + expected)

        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            [row[3] for row in rows[1:]],
            [preview["file"] for preview in report["previews"]],
        )
        self.assertEqual(
            [row[1] for row in rows[1:]],
            [preview["email"] for preview in report["previews"]],
        )
        self.assertEqual(len(rows) - 1, report["matched_count"])

        # 原始字节：固定表头开头、整文件以 CRLF 结束、无裸 LF（字段内
        # 换行在加引号字段中仍为单个 LF，故这里只校表头行与文件尾）。
        raw = self._read_bytes(out_path, "manifest.csv")
        self.assertTrue(
            raw.startswith(b"name,email,segment,preview_file\r\n")
        )
        self.assertTrue(raw.endswith(b"\r\n"))

    def test_acceptance_text_manifest_lists_jia_yi(self):
        # 用户验收命令：默认 text 格式、排除 b@example.invalid、
        # --manifest --index。清单按顺序只列甲、乙，分别对应
        # preview-0001.txt 与 preview-0002.txt；报告三计数 3、1、2。
        out_path = os.path.join(self.tmp, "previews")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_BING],
            index=True,
        )
        self._assert_ok_silent(result)

        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["index.html", "manifest.csv", "preview-0001.txt",
             "preview-0002.txt", "report.json"],
        )
        kept = [
            ["甲", EMAIL_SHARED, "newsletter"],
            ["乙", EMAIL_SHARED, "newsletter"],
        ]
        self._assert_manifest_matches(out_path, "txt", kept)

        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (3, 1, 2),
        )
        # 被排除的丙与未命中的丁均不进入清单。
        rows = self._parse_manifest(out_path)
        flat = [value for row in rows for value in row]
        self.assertNotIn("丙", flat)
        self.assertNotIn("丁", flat)
        self.assertNotIn(EMAIL_BING, flat)

    def test_html_format_manifest_uses_html_extension(self):
        # --format html：清单内容不变，仅预览文件名列随扩展名改为
        # .html；磁盘上也只有 .html 预览。
        out_path = os.path.join(self.tmp, "previews-html")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_BING],
            fmt="html",
        )
        self._assert_ok_silent(result)
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["manifest.csv", "preview-0001.html", "preview-0002.html",
             "report.json"],
        )
        kept = [
            ["甲", EMAIL_SHARED, "newsletter"],
            ["乙", EMAIL_SHARED, "newsletter"],
        ]
        self._assert_manifest_matches(out_path, "html", kept)

    def test_zero_segment_match_manifest_header_only(self):
        # 零命中退出 0：清单仅含固定表头一行，报告三计数 0、0、0。
        # （丁属于 archive，故须选一个不存在的分组才是真零命中。）
        out_path = os.path.join(self.tmp, "previews-zero")
        result = self._run(out_path, segment="no-such-segment")
        self._assert_ok_silent(result)
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["manifest.csv", "report.json"],
        )
        rows = self._parse_manifest(out_path)
        self.assertEqual(rows, [MANIFEST_HEADER])
        self.assertEqual(
            self._read_bytes(out_path, "manifest.csv"),
            b"name,email,segment,preview_file\r\n",
        )
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (0, 0, 0),
        )

    def test_all_excluded_manifest_header_only(self):
        # 全部排除退出 0：清单仅含表头；两个命中邮箱各自排除，三计数
        # 3、3、0（丁属 archive，不计入）。
        out_path = os.path.join(self.tmp, "previews-none")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_SHARED,
             "--exclude-email", EMAIL_BING],
        )
        self._assert_ok_silent(result)
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["manifest.csv", "report.json"],
        )
        self.assertEqual(
            self._parse_manifest(out_path), [MANIFEST_HEADER]
        )
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (3, 3, 0),
        )

    def test_exclude_file_removed_records_absent_from_manifest(self):
        # --exclude-file 与 --exclude-email 同为排除来源：名单中的
        # a@example.invalid（CRLF 行结束）移除甲、乙两条共享邮箱记录，
        # 空行忽略；清单只剩丙。
        list_path = self._write("exclude.txt", f"{EMAIL_SHARED}\r\n\n")
        out_path = os.path.join(self.tmp, "previews-file")
        result = self._run(
            out_path,
            ["--exclude-file", list_path],
        )
        self._assert_ok_silent(result)
        rows = self._parse_manifest(out_path)
        self.assertEqual(
            rows,
            [MANIFEST_HEADER,
             ["丙", EMAIL_BING, "newsletter", "preview-0001.txt"]],
        )

    def test_fields_round_trip_with_commas_quotes_newlines_spaces(self):
        # 逗号、双引号、字段内换行、首尾与连续空格、{{name}} 样式文字：
        # 经清单 CSV 解析后与输入 CSV 原值逐字相等。
        contacts_path = self._write("special.csv", SPECIAL_CONTACTS)
        out_path = os.path.join(self.tmp, "previews-special")
        result = self._run(
            out_path,
            contacts_path=contacts_path,
            segment=SPECIAL_SEGMENT,
        )
        self._assert_ok_silent(result)
        rows = self._parse_manifest(out_path)
        expected = [
            MANIFEST_HEADER,
            [SPECIAL_EXPECTED[0][0], SPECIAL_EXPECTED[0][1],
             SPECIAL_EXPECTED[0][2], "preview-0001.txt"],
            [SPECIAL_EXPECTED[1][0], SPECIAL_EXPECTED[1][1],
             SPECIAL_EXPECTED[1][2], "preview-0002.txt"],
            [SPECIAL_EXPECTED[2][0], SPECIAL_EXPECTED[2][1],
             SPECIAL_EXPECTED[2][2], "preview-0003.txt"],
        ]
        self.assertEqual(rows, expected)

        # 再与输入 CSV 直接解析出的记录逐字段比对：前三列即输入原文。
        with open(contacts_path, encoding="utf-8", newline="") as fh:
            source = list(csv.DictReader(fh))
        for source_record, manifest_row in zip(source, rows[1:]):
            self.assertEqual(source_record["name"], manifest_row[0])
            self.assertEqual(source_record["email"], manifest_row[1])
            self.assertEqual(source_record["segment"], manifest_row[2])

        # 原始字节中特殊值按 RFC 4180 加引号、双引号翻倍。
        raw = self._read_bytes(out_path, "manifest.csv").decode("utf-8")
        self.assertIn('"张,三"', raw)
        self.assertIn('"李""四\n换行"', raw)
        self.assertIn("{{name}},r@example.invalid", raw)

    def test_shared_email_each_gets_own_row_in_order(self):
        # 不排除时共享邮箱的三条 newsletter 记录各占一行，顺序同 CSV；
        # 丁（archive）不进入清单。
        out_path = os.path.join(self.tmp, "previews-shared")
        result = self._run(out_path)
        self._assert_ok_silent(result)
        rows = self._parse_manifest(out_path)
        self.assertEqual(
            [row[:3] for row in rows[1:]],
            [
                ["甲", EMAIL_SHARED, "newsletter"],
                ["乙", EMAIL_SHARED, "newsletter"],
                ["丙", EMAIL_BING, "newsletter"],
            ],
        )

    def test_manifest_is_only_new_file_byte_identical_otherwise(self):
        # 开启 --manifest 时原有产物与未开启时逐字节一致；manifest.csv
        # 是唯一新增文件。text/html 两种格式、以及与 --index 组合各验。
        for fmt in (None, "html"):
            for index in (False, True):
                with self.subTest(format=fmt or "text", index=index):
                    tag = f"{fmt or 'text'}-{index}"
                    off = os.path.join(self.tmp, f"off-{tag}")
                    on = os.path.join(self.tmp, f"on-{tag}")
                    result_off = self._run(
                        off, ["--exclude-email", EMAIL_BING],
                        fmt=fmt, manifest=False, index=index,
                    )
                    result_on = self._run(
                        on, ["--exclude-email", EMAIL_BING],
                        fmt=fmt, manifest=True, index=index,
                    )
                    self.assertEqual(result_off.returncode, 0)
                    self.assertEqual(result_on.returncode, 0)
                    off_names = set(os.listdir(off))
                    on_names = set(os.listdir(on))
                    self.assertNotIn("manifest.csv", off_names)
                    self.assertEqual(on_names - off_names,
                                     {"manifest.csv"})
                    for name in off_names:
                        self.assertEqual(
                            self._read_bytes(off, name),
                            self._read_bytes(on, name),
                            msg=f"{tag} 下 {name} 因 --manifest 字节变化",
                        )

    def test_input_failure_and_nonempty_dir_create_nothing(self):
        # 输入校验失败（缺列）：退出 2，不创建输出目录，无 Traceback。
        bad_contacts = "name,email\n甲,a@example.invalid\n"
        bad_path = self._write("bad.csv", bad_contacts)
        out_absent = os.path.join(self.tmp, "out-bad-absent")
        result = self._run(out_absent, contacts_path=bad_path)
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("缺少必需列", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))

        # 输出目录非空：退出 2，原有文件保留，不生成 manifest.csv。
        out_keep = os.path.join(self.tmp, "out-keep")
        os.mkdir(out_keep)
        keep_path = os.path.join(out_keep, "keep.txt")
        keep_bytes = "原有内容\n".encode("utf-8")
        with open(keep_path, "wb") as fh:
            fh.write(keep_bytes)
        result = self._run(
            out_keep, ["--exclude-email", EMAIL_BING], index=True
        )
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("输出目录非空", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertEqual(os.listdir(out_keep), ["keep.txt"])
        self.assertEqual(self._read_bytes(out_keep, "keep.txt"),
                         keep_bytes)

    def test_manifest_write_failure_exit_2_keeps_prior_files(self):
        # manifest.csv 创建前以 PermissionError 失败：退出 2，标准错误
        # 点名“无法写入输出文件”、完整 manifest.csv 路径与固定原因，
        # 无 Traceback；此前写出的两份预览与 report.json 保留，
        # manifest.csv 与排在其后的 index.html 均不存在。
        out_path = os.path.join(self.tmp, "previews-denied")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_BING],
            index=True,
            deny_manifest=True,
        )
        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        stderr = result.stderr.decode("utf-8")
        manifest_path = os.path.join(out_path, "manifest.csv")
        self.assertIn("无法写入输出文件", stderr)
        self.assertIn(manifest_path, stderr)
        self.assertIn(DENY_REASON, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)

        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "preview-0002.txt", "report.json"],
        )
        self.assertFalse(os.path.exists(manifest_path))
        self.assertFalse(os.path.exists(os.path.join(out_path, "index.html")))

        # 保留的报告仍如实计数，且不含任何清单相关字段。
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (3, 1, 2),
        )
        self.assertNotIn("manifest", report)

        # 输入文件保持不变。
        self.assertEqual(self._read_bytes_abs(self.contacts_path),
                         CONTACTS.encode("utf-8"))

    def _read_bytes_abs(self, path):
        with open(path, "rb") as fh:
            return fh.read()


if __name__ == "__main__":
    unittest.main()
