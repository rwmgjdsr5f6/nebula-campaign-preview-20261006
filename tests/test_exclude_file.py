"""newsletter_preview --exclude-file 文件名单排除的回归测试。

覆盖可选参数 --exclude-file 的公开约定：从 UTF-8 本地名单文件（无
表头）读取排除值，每行一个邮箱，支持 LF、CRLF 行结束符并兼容末行
无换行；空行及仅含空白的行忽略，其他行只移除行结束符（区分大小
写、保留首尾空白），空文件视为空名单。文件名单与可重复的
--exclude-email 合并后按邮箱原文精确匹配：共享邮箱的命中记录全部
排除、重复值不叠加计数、未命中值不影响结果、非命中分组不入明细。
文件名单与等价的命令行名单、省略该参数的运行产生逐字节一致的输出。

失败语义：文件不存在、不可读或 UTF-8 解码失败时退出 2，标准错误
点名 --exclude-file 且包含路径与原因、无 Traceback；--exclude-file
缺值同样退出 2 并点名参数。失败先于输出目录准备：尚不存在的目录
不创建，已有空目录保持为空；分组零命中时名单文件仍被读取与校验。

text、html 及 --index 采用同一名单，各自的转义、相对链接与空状态
保持不变；全部排除仍退出 0 并生成零预览报告。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。以 chmod 000 触发的不可
读用例在 root 下跳过（root 豁免权限检查）。

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

# 验收固定样例：甲、乙共用 a，丙用 b，丁用 c，四人均属 newsletter；
# archive 分组的戊共用甲的邮箱（不命中分组，永不进入任何清单）。
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

# excludes.txt 固定字节：两行甲的邮箱（重复）与一个空行，整体以 LF 结束。
EXCLUDES_BYTES = (
    "a@example.invalid\n"
    "a@example.invalid\n"
    "\n"
).encode("utf-8")

PREVIEW_BING = "你好，丙！\n".encode("utf-8")
INVALID_UTF8_BYTES = b"\xff"


class _IndexPartsParser(HTMLParser):
    """收集索引页全部文本、相对链接，并以“已排除的联系人”标题分区。"""

    EXCLUDED_HEADING = "已排除的联系人"

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_parts = []
        self.links = []
        self.before_excluded = []
        self.after_excluded = []
        self._in_heading = False
        self._in_excluded = False

    def handle_starttag(self, tag, attrs):
        if tag == "h2":
            self._in_heading = True
        if tag == "a":
            for name, value in attrs:
                if name == "href":
                    self.links.append(value)

    def handle_endtag(self, tag):
        if tag == "h2":
            self._in_heading = False

    def handle_data(self, data):
        self.text_parts.append(data)
        if self._in_excluded or self.EXCLUDED_HEADING in data:
            self._in_excluded = True
            self.after_excluded.append(data)
        else:
            (self.before_excluded if not self._in_excluded
             else self.after_excluded).append(data)

    def text(self):
        return "".join(self.text_parts)


class ExcludeFileTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS.encode("utf-8"))
        self.template_path = self._write("template.txt", TEMPLATE.encode("utf-8"))
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self._is_root = True
        else:
            self._is_root = False

    def tearDown(self):
        # 恢复可能被 chmod 000 改掉的权限，保证临时目录可清理。
        for dirpath, dirnames, filenames in os.walk(self.tmp):
            for name in dirnames + filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o700)
                except OSError:
                    pass
        self._tmp.cleanup()

    def _write(self, name, data):
        """以二进制写入固定字节的文件，返回路径。"""
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

    def _assert_failure_leaves_no_output(self, result, out_absent, out_empty,
                                         fragments):
        """两种输出目录状态下共同的失败约定：退出 2、点名参数与路径、
        无 Traceback；尚不存在的目录不创建，已有空目录保持为空。"""
        self.assertEqual(
            result.returncode, 2,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        stderr = result.stderr.decode("utf-8")
        for fragment in fragments:
            self.assertIn(fragment, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))
        self.assertEqual(os.listdir(out_empty), [])

    # ------------------------------------------------------------
    # 验收样例：固定 contacts/template/excludes 与 README 命令
    # ------------------------------------------------------------
    def test_acceptance_fixed_sample(self):
        excludes_path = self._write("excludes.txt", EXCLUDES_BYTES)
        out_path = os.path.join(self.tmp, "out-accept")
        result = self._run(
            out_path,
            ["--exclude-file", excludes_path,
             "--exclude-email", EMAIL_C],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stderr, b"")
        # 仅生成丙的预览与报告；戊属 archive，不进入任何清单。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )
        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), PREVIEW_BING)
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        # 分组命中 4、排除 3（文件中重复的 a 不叠加，c 来自命令行）、
        # 最终预览 1。
        self.assertEqual(report["segment_count"], 4)
        self.assertEqual(report["excluded_count"], 3)
        self.assertEqual(report["matched_count"], 1)
        # 排除明细按 CSV 顺序列甲、乙、丁；戊不出现。
        self.assertEqual(
            report["excluded_contacts"],
            [
                {"name": "甲", "email": EMAIL_A},
                {"name": "乙", "email": EMAIL_A},
                {"name": "丁", "email": EMAIL_C},
            ],
        )
        # 预览清单仅列丙的邮箱与文件名。
        self.assertEqual(
            report["previews"],
            [{"email": EMAIL_B, "file": "preview-0001.txt"}],
        )
        # 全部清单（排除明细 + 预览）共四条记录，甲的邮箱恰出现两次
        # （甲、乙），重复的名单值不翻倍；戊的记录不出现。
        listed_emails = (
            [c["email"] for c in report["excluded_contacts"]]
            + [p["email"] for p in report["previews"]]
        )
        self.assertEqual(
            listed_emails, [EMAIL_A, EMAIL_A, EMAIL_C, EMAIL_B]
        )

    # ------------------------------------------------------------
    # 行结束符、空行、末行无换行
    # ------------------------------------------------------------
    def test_line_endings_blank_lines_and_no_final_newline(self):
        # 同一组排除值 {a, c} 的三种字节排列：LF 且末行无换行、
        # 全程 CRLF（含空白空行）、LF 混合空行/仅空白行与末尾空行。
        variants = {
            "lf-no-final": (
                f"{EMAIL_A}\n{EMAIL_C}".encode("utf-8")
            ),
            "crlf": (
                f"{EMAIL_A}\r\n{EMAIL_C}\r\n"
                f"  \r\n\t\r\n"
            ).encode("utf-8"),
            "lf-blanks": (
                f"\n  \n\t\n{EMAIL_A}\n{EMAIL_C}\n\n"
            ).encode("utf-8"),
        }
        reports = []
        for label, data in variants.items():
            excludes_path = self._write(f"excludes-{label}.txt", data)
            out_path = os.path.join(self.tmp, f"out-{label}")
            result = self._run(out_path, ["--exclude-file", excludes_path])
            self.assertEqual(result.returncode, 0,
                             msg=result.stderr.decode("utf-8", "replace"))
            self.assertEqual(result.stderr, b"", msg=label)
            self.assertEqual(
                sorted(os.listdir(out_path)),
                ["preview-0001.txt", "report.json"],
                msg=label,
            )
            with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
                self.assertEqual(fh.read(), PREVIEW_BING, msg=label)
            with open(os.path.join(out_path, "report.json"),
                      encoding="utf-8") as fh:
                reports.append((label, json.load(fh)))

        for label, report in reports:
            self.assertEqual((report["segment_count"],
                              report["excluded_count"],
                              report["matched_count"]), (4, 3, 1), msg=label)
            self.assertEqual(
                [c["name"] for c in report["excluded_contacts"]],
                ["甲", "乙", "丁"], msg=label,
            )
            self.assertEqual(
                report["previews"],
                [{"email": EMAIL_B, "file": "preview-0001.txt"}],
                msg=label,
            )

    def test_case_and_surrounding_whitespace_are_verbatim(self):
        # 只移除行结束符：大小写不同或带首尾空白的行不匹配任何记录。
        data = (
            "A@EXAMPLE.INVALID\n"
            f"  {EMAIL_A}  \n"
            f"\t{EMAIL_C}\n"
        ).encode("utf-8")
        excludes_path = self._write("excludes-case.txt", data)
        out_path = os.path.join(self.tmp, "out-case")
        result = self._run(out_path, ["--exclude-file", excludes_path])

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["segment_count"], 4)
        self.assertEqual(report["excluded_count"], 0)
        self.assertEqual(report["matched_count"], 4)

    def test_empty_file_is_empty_exclusion_list(self):
        # 空文件与只含空白行的文件都视为空名单：输出与不带任何排除
        # 参数的运行逐字节一致。
        control_out = os.path.join(self.tmp, "out-control")
        control = self._run(control_out, [])
        self.assertEqual(control.returncode, 0,
                         msg=control.stderr.decode("utf-8", "replace"))
        for label, data in (
            ("empty", b""),
            ("blanks", b"\n\r\n  \n\t\n"),
        ):
            excludes_path = self._write(f"excludes-{label}.txt", data)
            out_path = os.path.join(self.tmp, f"out-{label}")
            result = self._run(out_path, ["--exclude-file", excludes_path])
            self.assertEqual(result.returncode, 0,
                             msg=result.stderr.decode("utf-8", "replace"))
            self.assertEqual(sorted(os.listdir(out_path)),
                             sorted(os.listdir(control_out)))
            for name in os.listdir(control_out):
                with open(os.path.join(control_out, name), "rb") as a, \
                        open(os.path.join(out_path, name), "rb") as b:
                    self.assertEqual(a.read(), b.read(), msg=f"{label}:{name}")

    # ------------------------------------------------------------
    # 与命令行名单合并、去重、等价
    # ------------------------------------------------------------
    def test_file_equivalent_to_command_line_list_byte_for_byte(self):
        # 文件名单 {a, c} 与两条等价 --exclude-email 产生逐字节一致输出。
        excludes_path = self._write(
            "excludes-eq.txt", f"{EMAIL_A}\n{EMAIL_C}\n".encode("utf-8")
        )
        out_file = os.path.join(self.tmp, "out-file")
        out_cli = os.path.join(self.tmp, "out-cli")
        r_file = self._run(out_file, ["--exclude-file", excludes_path])
        r_cli = self._run(
            out_cli,
            ["--exclude-email", EMAIL_A, "--exclude-email", EMAIL_C],
        )
        self.assertEqual(r_file.returncode, 0,
                         msg=r_file.stderr.decode("utf-8", "replace"))
        self.assertEqual(r_cli.returncode, 0,
                         msg=r_cli.stderr.decode("utf-8", "replace"))
        self.assertEqual(sorted(os.listdir(out_file)),
                         sorted(os.listdir(out_cli)))
        for name in os.listdir(out_cli):
            with open(os.path.join(out_file, name), "rb") as a, \
                    open(os.path.join(out_cli, name), "rb") as b:
                self.assertEqual(a.read(), b.read(), msg=name)

    def test_merged_duplicates_across_sources_count_once(self):
        # 文件含 a 两次与 c，命令行再给 a：甲、乙仍各排除一次，
        # excluded_count 为 3 而非更多。
        excludes_path = self._write(
            "excludes-dupe.txt",
            f"{EMAIL_A}\n{EMAIL_A}\n{EMAIL_C}\n".encode("utf-8"),
        )
        out_path = os.path.join(self.tmp, "out-dupe")
        result = self._run(
            out_path,
            ["--exclude-file", excludes_path,
             "--exclude-email", EMAIL_A],
        )
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual((report["segment_count"],
                          report["excluded_count"],
                          report["matched_count"]), (4, 3, 1))
        self.assertEqual(
            [c["name"] for c in report["excluded_contacts"]],
            ["甲", "乙", "丁"],
        )

    def test_non_matching_values_have_no_effect(self):
        excludes_path = self._write(
            "excludes-nomatch.txt",
            "nobody@example.invalid\nother@example.invalid\n".encode("utf-8"),
        )
        out_path = os.path.join(self.tmp, "out-nomatch")
        result = self._run(out_path, ["--exclude-file", excludes_path])
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual((report["segment_count"],
                          report["excluded_count"],
                          report["matched_count"]), (4, 0, 4))
        self.assertEqual(report["excluded_contacts"], [])

    # ------------------------------------------------------------
    # 失败：缺文件、不可读、非法 UTF-8、参数缺值
    # ------------------------------------------------------------
    def _run_failure_in_both_dir_states(self, fragments):
        """在两种输出目录状态下核对失败约定：尚不存在的目录不创建，
        已有空目录保持为空；退出 2、点名参数与路径、无 Traceback。
        排除参数由 self._current_extra 提供。"""
        def invoke(out_path):
            return self._run(out_path, self._current_extra)

        out_absent = os.path.join(self.tmp, "absent")
        out_empty = os.path.join(self.tmp, "empty")
        os.mkdir(out_empty)
        result = invoke(out_absent)
        self._assert_failure_leaves_no_output(
            result, out_absent, out_empty, fragments
        )
        # 已有的空目录再跑一次：同样失败，目录仍为空。
        result_empty = invoke(out_empty)
        self.assertEqual(result_empty.returncode, 2)
        stderr = result_empty.stderr.decode("utf-8")
        for fragment in fragments:
            self.assertIn(fragment, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertEqual(os.listdir(out_empty), [])

    def test_missing_file_exits_2_without_output(self):
        missing = os.path.join(self.tmp, "no-such-excludes.txt")
        self._current_extra = ["--exclude-file", missing]
        self._run_failure_in_both_dir_states(
            ["--exclude-file", "文件不存在", missing],
        )

    def test_invalid_utf8_file_exits_2_without_output(self):
        bad_path = self._write("excludes-bad.txt", INVALID_UTF8_BYTES)
        self._current_extra = ["--exclude-file", bad_path]
        self._run_failure_in_both_dir_states(
            ["--exclude-file", "无法解码", "UTF-8", bad_path],
        )

    def test_unreadable_file_exits_2_without_output(self):
        if self._is_root:
            self.skipTest("root 用户绕过文件权限，chmod 000 无法触发读取失败")
        unreadable = self._write(
            "excludes-denied.txt", f"{EMAIL_A}\n".encode("utf-8")
        )
        os.chmod(unreadable, 0)
        self._current_extra = ["--exclude-file", unreadable]
        self._run_failure_in_both_dir_states(
            ["--exclude-file", "无法读取", "Permission denied", unreadable],
        )

    def test_missing_argument_value_exits_2(self):
        out_absent = os.path.join(self.tmp, "absent-missing-value")
        result = self._run(out_absent, ["--exclude-file"])
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("--exclude-file", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))

    def test_list_file_is_checked_even_with_zero_segment_hits(self):
        # 零命中也要检查名单：非法 UTF-8 名单在分组无人命中时仍致退出 2。
        bad_path = self._write("excludes-zero.txt", INVALID_UTF8_BYTES)
        out_absent = os.path.join(self.tmp, "absent-zero")
        result = self._run(
            out_absent,
            ["--exclude-file", bad_path],
            segment="no-such-segment",
        )
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("--exclude-file", stderr)
        self.assertIn(bad_path, stderr)
        self.assertFalse(os.path.exists(out_absent))

        # 名单有效但零命中：退出 0，报告三个计数为 0，名单仍被读取。
        good_path = self._write(
            "excludes-zero-ok.txt", f"{EMAIL_A}\n".encode("utf-8")
        )
        out_zero = os.path.join(self.tmp, "out-zero")
        ok = self._run(
            out_zero, ["--exclude-file", good_path],
            segment="no-such-segment",
        )
        self.assertEqual(ok.returncode, 0,
                         msg=ok.stderr.decode("utf-8", "replace"))
        self.assertEqual(os.listdir(out_zero), ["report.json"])
        with open(os.path.join(out_zero, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual((report["segment_count"],
                          report["excluded_count"],
                          report["matched_count"]), (0, 0, 0))

    # ------------------------------------------------------------
    # html 与 --index 共用同一名单
    # ------------------------------------------------------------
    def test_html_and_index_share_same_list(self):
        excludes_path = self._write("excludes-html.txt", EXCLUDES_BYTES)
        out_path = os.path.join(self.tmp, "out-html-index")
        result = self._run(
            out_path,
            ["--format", "html", "--index",
             "--exclude-file", excludes_path,
             "--exclude-email", EMAIL_C],
        )
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stderr, b"")
        # 只有丙的 HTML 预览、报告与索引；无 .txt 副本。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["index.html", "preview-0001.html", "report.json"],
        )
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual((report["segment_count"],
                          report["excluded_count"],
                          report["matched_count"]), (4, 3, 1))
        self.assertEqual(
            report["previews"],
            [{"email": EMAIL_B, "file": "preview-0001.html"}],
        )

        with open(os.path.join(out_path, "index.html"),
                  encoding="utf-8") as fh:
            index_doc = fh.read()
        parser = _IndexPartsParser()
        parser.feed(index_doc)
        # 相对链接只指向同目录的 .html 预览。
        self.assertEqual(parser.links, ["preview-0001.html"])
        before = "".join(parser.before_excluded)
        after = "".join(parser.after_excluded)
        # 保留清单只含丙；排除区域按 CSV 顺序列甲、乙、丁，不含戊。
        self.assertIn("丙", before)
        self.assertIn(EMAIL_B, before)
        self.assertNotIn("甲", before)
        self.assertNotIn("丁", before)
        self.assertNotIn("没有可预览的联系人", before)
        for text in ("甲", "乙", "丁", EMAIL_A, EMAIL_C):
            self.assertIn(text, after)
        self.assertNotIn("戊", parser.text())
        self.assertNotIn("没有被排除的联系人", after)

    def test_all_excluded_with_index_generates_zero_preview_report(self):
        excludes_path = self._write(
            "excludes-all.txt",
            f"{EMAIL_A}\n{EMAIL_B}\n{EMAIL_C}\n".encode("utf-8"),
        )
        out_path = os.path.join(self.tmp, "out-all-index")
        result = self._run(
            out_path, ["--index", "--exclude-file", excludes_path]
        )
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        # 零预览：只有报告与索引，无任何预览文件。
        self.assertEqual(sorted(os.listdir(out_path)),
                         ["index.html", "report.json"])
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual((report["segment_count"],
                          report["excluded_count"],
                          report["matched_count"]), (4, 4, 0))
        self.assertEqual(report["previews"], [])
        with open(os.path.join(out_path, "index.html"),
                  encoding="utf-8") as fh:
            index_doc = fh.read()
        parser = _IndexPartsParser()
        parser.feed(index_doc)
        self.assertEqual(parser.links, [])
        self.assertIn("没有可预览的联系人", parser.text())
        # 排除区域仍列出甲、乙、丙、丁四人（戊属 archive，不出现）。
        self.assertEqual(
            [c["name"] for c in report["excluded_contacts"]],
            ["甲", "乙", "丙", "丁"],
        )
        for name in ("甲", "乙", "丙", "丁"):
            self.assertIn(name, "".join(parser.after_excluded))
        self.assertNotIn("戊", parser.text())


if __name__ == "__main__":
    unittest.main()
