"""newsletter_preview --exclude-file 名单文件排除的回归测试。

固定合成样例 contacts.csv 的表头为 name,email,segment：甲、乙共用
a@example.invalid，丙用 b@example.invalid，丁用 c@example.invalid，
四人均属 newsletter；另有 archive 分组的戊共用甲的邮箱。template.txt
为“你好，{{name}}！”并带末尾 LF。excludes.txt 含两行甲的邮箱及一个
空行。验收命令沿用 README 形式并增加
--exclude-file excludes.txt --exclude-email c@example.invalid：
退出 0、标准错误为空，仅生成丙的 preview-0001.txt 与 report.json，
正文为“你好，丙！”并保留末尾 LF；报告分组命中、排除、最终预览计数
依次为 4、3、1，排除明细按 CSV 顺序列出甲、乙、丁，预览清单仅列丙的
邮箱与文件名，戊不进入清单。

另覆盖：LF/CRLF/末行无换行与空行规则、保留大小写与首尾空白、空文件
视为空名单、文件名单与等价命令行名单逐字节一致、两处来源合并去重、
零命中仍读取校验名单、html 与 --index 共用同一名单、全部排除时生成
零预览报告；以及文件不存在、不可读、UTF-8 解码失败与参数缺值时退出 2、
标准错误包含路径/参数名与原因、无 Traceback、不创建输出。

仅依赖 Python 3 标准库，完全离线；邮箱使用 RFC 2606 保留的
example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准错误、落盘文件内容与输出目录状态；不直接调用
内部函数。每个样例使用独立临时目录，结束后自动清理。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 验收样例：甲、乙、丙、丁属 newsletter，戊属 archive；甲、乙、戊共用
# a@example.invalid，丙用 b@，丁用 c@。
CONTACTS = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,a@example.invalid,newsletter\n"
    "丙,b@example.invalid,newsletter\n"
    "丁,c@example.invalid,newsletter\n"
    "戊,a@example.invalid,archive\n"
)
TEMPLATE = "你好，{{name}}！\n"
SEGMENT = "newsletter"
EMAIL_A = "a@example.invalid"
EMAIL_B = "b@example.invalid"
EMAIL_C = "c@example.invalid"

PREVIEW_BING = "你好，丙！\n"


class _PreTextExtractor(HTMLParser):
    """收集文档中唯一 pre 元素内的全部文本（实体按浏览器规则还原）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._pre_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self._pre_depth += 1

    def handle_endtag(self, tag):
        if tag == "pre":
            self._pre_depth -= 1

    def handle_data(self, data):
        if self._pre_depth:
            self.parts.append(data)

    def text(self):
        return "".join(self.parts)


class ExcludeFileTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS)
        self.template_path = self._write("template.txt", TEMPLATE)
        # root 绕过文件权限，chmod 000 无法合成不可读，相关用例跳过。
        self._is_root = hasattr(os, "geteuid") and os.geteuid() == 0

    def tearDown(self):
        # 恢复可能被改掉的权限，保证临时目录可被清理。
        for dirpath, _dirnames, filenames in os.walk(self.tmp):
            for name in filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o644)
                except OSError:
                    pass
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _write_bytes(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _run(self, out_path, extra_args=(), contacts_path=None,
             template_path=None, segment=SEGMENT):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
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
        argv.extend(extra_args)
        return subprocess.run(
            argv,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _read_report(self, out_path):
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            return json.load(fh)

    def _assert_acceptance_report(self, report, extension="txt"):
        self.assertEqual(report["segment_count"], 4)
        self.assertEqual(report["excluded_count"], 3)
        self.assertEqual(report["matched_count"], 1)
        # 排除明细按 CSV 顺序：甲、乙（共享 a@）、丁（c@）；
        # 戊属于 archive，不进入清单。
        self.assertEqual(
            report["excluded_contacts"],
            [
                {"name": "甲", "email": EMAIL_A},
                {"name": "乙", "email": EMAIL_A},
                {"name": "丁", "email": EMAIL_C},
            ],
        )
        self.assertEqual(len(report["excluded_contacts"]),
                         report["excluded_count"])
        self.assertEqual(
            report["previews"],
            [{"email": EMAIL_B,
              "file": f"preview-0001.{extension}"}],
        )

    def test_acceptance_fixed_sample(self):
        # excludes.txt：两行甲的邮箱与一个空行（空行在中间）。
        excludes_path = self._write(
            "excludes.txt", EMAIL_A + "\n\n" + EMAIL_A + "\n"
        )
        out_path = os.path.join(self.tmp, "previews")
        result = self._run(
            out_path,
            ["--exclude-file", excludes_path,
             "--exclude-email", EMAIL_C],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )
        with open(os.path.join(out_path, "preview-0001.txt"),
                  "rb") as fh:
            self.assertEqual(fh.read(), PREVIEW_BING.encode("utf-8"))
        self._assert_acceptance_report(self._read_report(out_path))

    def test_line_endings_and_trailing_newline_variants_agree(self):
        # 同样的两行名单（a@、c@）在不同行结束写法下结果一致：
        # LF、CRLF、末行无换行（LF 与 CRLF 各一种），并穿插空行与
        # 仅含空白的行。
        variants = {
            "lf": EMAIL_A + "\n" + EMAIL_C + "\n",
            "crlf": EMAIL_A + "\r\n" + EMAIL_C + "\r\n",
            "lf-no-final": EMAIL_A + "\n" + EMAIL_C,
            "crlf-no-final": EMAIL_A + "\r\n" + EMAIL_C,
            "blanks": (
                "\n"
                "   \n"
                + EMAIL_A + "\r\n"
                + "\t \r\n"
                + EMAIL_C + "\n"
                + "\n"
            ),
        }
        reference = None
        for label, content in variants.items():
            excludes_path = self._write(f"excludes-{label}.txt", content)
            out_path = os.path.join(self.tmp, f"out-{label}")
            result = self._run(out_path, ["--exclude-file", excludes_path])

            self.assertEqual(result.returncode, 0,
                             msg=f"{label}: "
                                 f"{result.stderr.decode('utf-8', 'replace')}")
            self.assertEqual(result.stderr, b"", msg=label)
            with open(os.path.join(out_path, "preview-0001.txt"),
                      "rb") as fh:
                preview_bytes = fh.read()
            with open(os.path.join(out_path, "report.json"), "rb") as fh:
                report_bytes = fh.read()
            self.assertEqual(preview_bytes, PREVIEW_BING.encode("utf-8"),
                             msg=label)
            self._assert_acceptance_report(json.loads(report_bytes))
            if reference is None:
                reference = (preview_bytes, report_bytes)
            else:
                # 五种行结束写法的产物逐字节一致。
                self.assertEqual((preview_bytes, report_bytes), reference,
                                 msg=label)

    def test_empty_file_is_empty_list(self):
        # 空文件与仅含空行/空白行的文件都视为空名单：行为与不给参数一致，
        # 四人全部保留。
        for label, content in (
            ("empty", ""),
            ("blanks", "\n  \n\t\n\r\n"),
        ):
            excludes_path = self._write(f"excludes-{label}.txt", content)
            out_path = os.path.join(self.tmp, f"out-{label}")
            result = self._run(out_path, ["--exclude-file", excludes_path])

            self.assertEqual(result.returncode, 0,
                             msg=result.stderr.decode("utf-8", "replace"))
            report = self._read_report(out_path)
            self.assertEqual(report["segment_count"], 4)
            self.assertEqual(report["excluded_count"], 0)
            self.assertEqual(report["matched_count"], 4)
            self.assertEqual(report["excluded_contacts"], [])
            self.assertEqual(
                sorted(os.listdir(out_path)),
                [
                    "preview-0001.txt",
                    "preview-0002.txt",
                    "preview-0003.txt",
                    "preview-0004.txt",
                    "report.json",
                ],
            )

    def test_non_blank_lines_keep_case_and_surrounding_space(self):
        # 名单行只移除行结束符：带首尾空白或大小写不同的值不修剪，
        # 因而不匹配记录中的邮箱；丙（b@）与丁（c@）均保留。
        excludes_path = self._write(
            "excludes-spaces.txt",
            " " + EMAIL_A + " \n"
            + EMAIL_A.upper() + "\n"
            + "\t" + EMAIL_C + "\n",
        )
        out_path = os.path.join(self.tmp, "out-spaces")
        result = self._run(out_path, ["--exclude-file", excludes_path])

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        report = self._read_report(out_path)
        self.assertEqual(report["excluded_count"], 0)
        self.assertEqual(report["matched_count"], 4)

    def test_lone_cr_without_lf_is_kept_as_content(self):
        # 仅支持 LF 与 CRLF：末行“a@…\r”后无 LF 时，CR 不属于行结束符，
        # 按正文保留，该值不等于 a@，无人被排除。
        excludes_path = self._write_bytes(
            "excludes-lone-cr.txt", (EMAIL_A + "\r").encode("utf-8")
        )
        out_path = os.path.join(self.tmp, "out-lone-cr")
        result = self._run(out_path, ["--exclude-file", excludes_path])

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        report = self._read_report(out_path)
        self.assertEqual(report["matched_count"], 4)
        self.assertEqual(report["excluded_count"], 0)

    def test_file_list_equivalent_to_command_line_list(self):
        # 文件名单与等价的命令行名单产生逐字节一致的输出。
        excludes_path = self._write(
            "excludes.txt", EMAIL_A + "\n" + EMAIL_C + "\n"
        )
        out_file = os.path.join(self.tmp, "out-file")
        result_file = self._run(
            out_file, ["--exclude-file", excludes_path]
        )
        out_cli = os.path.join(self.tmp, "out-cli")
        result_cli = self._run(
            out_cli,
            ["--exclude-email", EMAIL_A, "--exclude-email", EMAIL_C],
        )

        self.assertEqual(result_file.returncode, 0,
                         msg=result_file.stderr.decode("utf-8", "replace"))
        self.assertEqual(result_cli.returncode, 0,
                         msg=result_cli.stderr.decode("utf-8", "replace"))
        self.assertEqual(os.listdir(out_file), os.listdir(out_cli))
        for name in os.listdir(out_file):
            with open(os.path.join(out_file, name), "rb") as fh:
                file_bytes = fh.read()
            with open(os.path.join(out_cli, name), "rb") as fh:
                cli_bytes = fh.read()
            self.assertEqual(file_bytes, cli_bytes, msg=name)

    def test_merged_values_dedupe_across_sources(self):
        # a@ 同时在文件（出现两次）与命令行（再出现一次）中，c@ 在文件中：
        # 重复值不叠加，排除数仍按记录为 3。
        excludes_path = self._write(
            "excludes.txt", EMAIL_A + "\n" + EMAIL_A + "\n" + EMAIL_C
        )
        out_path = os.path.join(self.tmp, "out-merge")
        result = self._run(
            out_path,
            ["--exclude-file", excludes_path,
             "--exclude-email", EMAIL_A],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self._assert_acceptance_report(self._read_report(out_path))

    def test_non_matching_file_value_does_not_affect_result(self):
        # 文件中的未命中值不报错，也不影响结果。
        excludes_path = self._write(
            "excludes.txt", "nobody@example.invalid\n"
        )
        out_path = os.path.join(self.tmp, "out-nomatch")
        result = self._run(out_path, ["--exclude-file", excludes_path])

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        report = self._read_report(out_path)
        self.assertEqual(report["segment_count"], 4)
        self.assertEqual(report["excluded_count"], 0)
        self.assertEqual(report["matched_count"], 4)

    def test_zero_segment_hits_still_reads_list_file(self):
        # 零命中也要检查名单：不存在的名单文件在零命中分组下同样退出 2。
        missing_path = os.path.join(self.tmp, "missing-excludes.txt")
        out_absent = os.path.join(self.tmp, "out-zero-absent")
        result = self._run(
            out_absent,
            ["--exclude-file", missing_path],
            segment="no-such-segment",
        )

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("文件不存在", stderr)
        self.assertIn("排除名单文件", stderr)
        self.assertIn(missing_path, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))

        # 可读的空名单配合零命中分组：退出 0，三个计数均为 0。
        empty_path = self._write("empty-excludes.txt", "")
        out_zero = os.path.join(self.tmp, "out-zero")
        result = self._run(
            out_zero,
            ["--exclude-file", empty_path],
            segment="no-such-segment",
        )
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        report = self._read_report(out_zero)
        self.assertEqual(report["segment_count"], 0)
        self.assertEqual(report["excluded_count"], 0)
        self.assertEqual(report["matched_count"], 0)
        self.assertEqual(report["excluded_contacts"], [])
        self.assertEqual(report["previews"], [])

    def test_missing_file_exits_2_without_creating_output(self):
        missing_path = os.path.join(self.tmp, "missing-excludes.txt")

        # 情形 A：输出目录尚不存在——运行后仍不存在。
        out_absent = os.path.join(self.tmp, "out-absent")
        result = self._run(
            out_absent, ["--exclude-file", missing_path]
        )
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("文件不存在", stderr)
        self.assertIn("排除名单文件", stderr)
        self.assertIn(missing_path, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))

        # 情形 B：输出目录已存在且为空——运行后仍为空目录。
        out_empty = os.path.join(self.tmp, "out-empty")
        os.mkdir(out_empty)
        result = self._run(
            out_empty, ["--exclude-file", missing_path]
        )
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn(missing_path, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertEqual(os.listdir(out_empty), [])

    def test_directory_path_exits_2_with_path_and_reason(self):
        # 传入目录路径属于“不可读/无法打开”：退出 2，stderr 含路径与原因。
        directory = os.path.join(self.tmp, "a-directory")
        os.mkdir(directory)
        out_path = os.path.join(self.tmp, "out-dir")
        result = self._run(out_path, ["--exclude-file", directory])

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("排除名单文件", stderr)
        self.assertIn(directory, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_path))

    def test_unreadable_file_exits_2(self):
        if self._is_root:
            self.skipTest("root 用户绕过文件权限，无法合成读取失败")
        excludes_path = self._write(
            "excludes-secret.txt", EMAIL_A + "\n"
        )
        os.chmod(excludes_path, 0)
        out_absent = os.path.join(self.tmp, "out-secret")
        result = self._run(
            out_absent, ["--exclude-file", excludes_path]
        )

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("排除名单文件", stderr)
        self.assertIn(excludes_path, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))

        # 已存在的空目录保持为空。
        out_empty = os.path.join(self.tmp, "out-secret-empty")
        os.mkdir(out_empty)
        result = self._run(
            out_empty, ["--exclude-file", excludes_path]
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(os.listdir(out_empty), [])

    def test_invalid_utf8_exits_2(self):
        # 名单文件仅含一个非法 UTF-8 字节 0xFF：退出 2，stderr 含路径、
        # 解码原因与 UTF-8 提示，无 Traceback，不创建输出。
        excludes_path = self._write_bytes("excludes-bad.txt", b"\xff")
        out_absent = os.path.join(self.tmp, "out-bad")
        result = self._run(
            out_absent, ["--exclude-file", excludes_path]
        )

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("无法解码", stderr)
        self.assertIn("排除名单文件", stderr)
        self.assertIn(excludes_path, stderr)
        self.assertIn("UTF-8", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))

        # 零命中分组下同样要先解码名单，非法 UTF-8 仍退出 2。
        out_zero = os.path.join(self.tmp, "out-bad-zero")
        result = self._run(
            out_zero,
            ["--exclude-file", excludes_path],
            segment="no-such-segment",
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(out_zero))

    def test_missing_exclude_file_value_exits_2(self):
        # --exclude-file 后缺值：argparse 报错退出 2，stderr 点名
        # --exclude-file，无 Traceback，不创建输出目录。
        out_path = os.path.join(self.tmp, "out-missing-value")
        result = self._run(out_path, ["--exclude-file"])

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("--exclude-file", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_path))

    def test_bom_in_exclude_file_is_body_not_stripped(self):
        # 名单文件不做 BOM 处理：开头 U+FEFF 属于该行正文，值不再等于
        # 甲的邮箱，因此无人因该值被排除（c@ 仍由命令行排除）。
        excludes_path = self._write_bytes(
            "excludes-bom.txt",
            ("\ufeff" + EMAIL_A + "\n").encode("utf-8"),
        )
        out_path = os.path.join(self.tmp, "out-bom")
        result = self._run(
            out_path,
            ["--exclude-file", excludes_path,
             "--exclude-email", EMAIL_C],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        report = self._read_report(out_path)
        self.assertEqual(report["segment_count"], 4)
        # 仅丁（c@）被命令行排除；带 BOM 的行不匹配甲、乙。
        self.assertEqual(report["excluded_count"], 1)
        self.assertEqual(
            report["excluded_contacts"],
            [{"name": "丁", "email": EMAIL_C}],
        )
        self.assertEqual(report["matched_count"], 3)

    def test_html_format_uses_same_exclude_list(self):
        # html 模式共用同一名单：只有丙的 preview-0001.html，无 .txt 副本。
        excludes_path = self._write(
            "excludes.txt", EMAIL_A + "\n" + EMAIL_C + "\n"
        )
        out_path = os.path.join(self.tmp, "out-html")
        result = self._run(
            out_path,
            ["--exclude-file", excludes_path, "--format", "html"],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.html", "report.json"],
        )
        with open(os.path.join(out_path, "preview-0001.html"),
                  encoding="utf-8") as fh:
            document = fh.read()
        extractor = _PreTextExtractor()
        extractor.feed(document)
        self.assertEqual(extractor.text(), PREVIEW_BING)
        self._assert_acceptance_report(self._read_report(out_path),
                                       extension="html")

    def test_index_page_uses_same_exclude_list(self):
        # --index 共用名单：索引只含丙的相对 .txt 链接，排除区域与报告
        # excluded_contacts 同内容同顺序（甲、乙、丁，无戊）。
        excludes_path = self._write(
            "excludes.txt", EMAIL_A + "\n" + EMAIL_C + "\n"
        )
        out_path = os.path.join(self.tmp, "out-index")
        result = self._run(
            out_path,
            ["--exclude-file", excludes_path, "--index"],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["index.html", "preview-0001.txt", "report.json"],
        )
        report = self._read_report(out_path)
        with open(os.path.join(out_path, "index.html"),
                  encoding="utf-8") as fh:
            index = fh.read()
        # 唯一预览链接是同目录相对地址。
        self.assertIn('href="preview-0001.txt"', index)
        self.assertNotIn('href="preview-0002.txt"', index)
        self.assertNotIn("mailto:", index)
        # 三个计数与报告一致。
        self.assertIn("分组命中：4；排除：3；最终预览：1", index)
        # 排除区域按 CSV 顺序列甲、乙、丁，且不含 archive 的戊。
        excluded_positions = [index.find(name) for name in ("甲", "乙", "丁")]
        self.assertTrue(all(position >= 0 for position
                            in excluded_positions))
        self.assertEqual(excluded_positions, sorted(excluded_positions))
        self.assertNotIn("戊", index)
        self.assertNotIn("没有被排除的联系人", index)
        self.assertEqual(
            report["excluded_contacts"],
            [
                {"name": "甲", "email": EMAIL_A},
                {"name": "乙", "email": EMAIL_A},
                {"name": "丁", "email": EMAIL_C},
            ],
        )

    def test_all_excluded_generates_zero_preview_report(self):
        # 文件与命令行合起来排除全部四人：退出 0，仅 report.json，
        # 最终预览为 0、清单空，排除数为 4。
        excludes_path = self._write(
            "excludes.txt", EMAIL_A + "\n" + EMAIL_B + "\n"
        )
        out_path = os.path.join(self.tmp, "out-all")
        result = self._run(
            out_path,
            ["--exclude-file", excludes_path,
             "--exclude-email", EMAIL_C],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(os.listdir(out_path), ["report.json"])
        report = self._read_report(out_path)
        self.assertEqual(report["segment_count"], 4)
        self.assertEqual(report["excluded_count"], 4)
        self.assertEqual(report["matched_count"], 0)
        self.assertEqual(report["previews"], [])
        self.assertEqual(
            [item["name"] for item in report["excluded_contacts"]],
            ["甲", "乙", "丙", "丁"],
        )

    def test_omitting_flag_is_byte_identical_to_baseline(self):
        # 省略 --exclude-file 与不传任何排除参数的两次运行逐字节一致。
        out_plain = os.path.join(self.tmp, "out-plain")
        result_plain = self._run(out_plain, [])
        out_again = os.path.join(self.tmp, "out-again")
        result_again = self._run(out_again, [])

        self.assertEqual(result_plain.returncode, 0)
        self.assertEqual(result_again.returncode, 0)
        self.assertEqual(os.listdir(out_plain), os.listdir(out_again))
        for name in os.listdir(out_plain):
            with open(os.path.join(out_plain, name), "rb") as fh:
                plain = fh.read()
            with open(os.path.join(out_again, name), "rb") as fh:
                again = fh.read()
            self.assertEqual(plain, again, msg=name)


if __name__ == "__main__":
    unittest.main()
