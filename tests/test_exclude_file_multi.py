"""newsletter_preview 重复 --exclude-file 多名单合并的回归测试。

固定合成样例 contacts.csv 的表头为 name,email,segment：甲、乙共用
a@example.invalid，丙用 b@example.invalid，丁用 c@example.invalid，
四人均属 newsletter；另有 archive 分组的戊共用甲的邮箱。template.txt
为“你好，{{name}}！”并带末尾 LF。first.txt 写两行甲的邮箱，
second.txt 写甲和丁的邮箱，各邮箱独占一行。验收命令沿用 README 形式
并追加 --exclude-file first.txt --exclude-file second.txt：退出 0、
标准错误为空，仅生成丙的 preview-0001.txt 与 report.json，正文为
“你好，丙！”并保留末尾 LF；报告分组命中、排除、最终预览计数依次为
4、3、1，排除明细按 CSV 顺序列出甲、乙、丁，戊不进入清单。

另覆盖：交换名单顺序输出不变、与等价单文件/命令行名单逐字节一致、
文件内与文件间重复值不叠加、零命中或全部排除仍读取校验全部名单、
多个文件失败只报告按参数顺序的首个失败文件、缺值退出 2 并点名参数、
html 与 --index、--manifest 共用同一合并结果。

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


class ExcludeFileMultiTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS)
        self.template_path = self._write("template.txt", TEMPLATE)
        # first.txt：两行甲的邮箱（文件内重复）；second.txt：甲与丁的邮箱
        # （甲跨文件重复）。各邮箱独占一行。
        self.first_path = self._write(
            "first.txt", EMAIL_A + "\n" + EMAIL_A + "\n"
        )
        self.second_path = self._write(
            "second.txt", EMAIL_A + "\n" + EMAIL_C + "\n"
        )

    def tearDown(self):
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

    def _run(self, out_path, extra_args=(), segment=SEGMENT):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        argv = [
            sys.executable,
            "-m",
            "newsletter_preview",
            "--contacts",
            self.contacts_path,
            "--template",
            self.template_path,
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
        self.assertEqual(
            report["previews"],
            [{"email": EMAIL_B,
              "file": f"preview-0001.{extension}"}],
        )

    def _assert_dir_trees_equal(self, left, right):
        """两个输出目录的文件名集合与逐字节内容完全一致。"""
        self.assertEqual(sorted(os.listdir(left)), sorted(os.listdir(right)))
        for name in os.listdir(left):
            with open(os.path.join(left, name), "rb") as fh:
                left_bytes = fh.read()
            with open(os.path.join(right, name), "rb") as fh:
                right_bytes = fh.read()
            self.assertEqual(left_bytes, right_bytes, msg=name)

    def test_acceptance_two_files(self):
        # 验收命令：--exclude-file first.txt --exclude-file second.txt。
        out_path = os.path.join(self.tmp, "previews")
        result = self._run(
            out_path,
            ["--exclude-file", self.first_path,
             "--exclude-file", self.second_path],
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

    def test_swapping_file_order_keeps_output(self):
        # 交换两份名单的参数顺序，成功输出逐字节一致。
        out_normal = os.path.join(self.tmp, "out-normal")
        result_normal = self._run(
            out_normal,
            ["--exclude-file", self.first_path,
             "--exclude-file", self.second_path],
        )
        out_swapped = os.path.join(self.tmp, "out-swapped")
        result_swapped = self._run(
            out_swapped,
            ["--exclude-file", self.second_path,
             "--exclude-file", self.first_path],
        )

        self.assertEqual(result_normal.returncode, 0,
                         msg=result_normal.stderr.decode("utf-8", "replace"))
        self.assertEqual(result_swapped.returncode, 0,
                         msg=result_swapped.stderr.decode("utf-8", "replace"))
        self._assert_dir_trees_equal(out_normal, out_swapped)

    def test_two_files_equivalent_to_single_merged_file(self):
        # 两份名单与等价的单文件合并名单、等价命令行名单逐字节一致。
        merged_path = self._write(
            "merged.txt", EMAIL_A + "\n" + EMAIL_C + "\n"
        )
        out_multi = os.path.join(self.tmp, "out-multi")
        result_multi = self._run(
            out_multi,
            ["--exclude-file", self.first_path,
             "--exclude-file", self.second_path],
        )
        out_single = os.path.join(self.tmp, "out-single")
        result_single = self._run(
            out_single, ["--exclude-file", merged_path]
        )
        out_cli = os.path.join(self.tmp, "out-cli")
        result_cli = self._run(
            out_cli,
            ["--exclude-email", EMAIL_A, "--exclude-email", EMAIL_C],
        )

        for result in (result_multi, result_single, result_cli):
            self.assertEqual(result.returncode, 0,
                             msg=result.stderr.decode("utf-8", "replace"))
        self._assert_dir_trees_equal(out_multi, out_single)
        self._assert_dir_trees_equal(out_multi, out_cli)

    def test_duplicates_across_files_do_not_stack(self):
        # a@ 在 first.txt 出现两次、second.txt 一次、命令行再出现一次：
        # 重复值不叠加，排除数仍按记录为 3。
        out_path = os.path.join(self.tmp, "out-dup")
        result = self._run(
            out_path,
            ["--exclude-file", self.first_path,
             "--exclude-file", self.second_path,
             "--exclude-email", EMAIL_A],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self._assert_acceptance_report(self._read_report(out_path))

    def test_zero_hits_still_reads_all_files(self):
        # 零命中分组下，第二份名单不存在同样退出 2 并点名该文件。
        missing_path = os.path.join(self.tmp, "missing-excludes.txt")
        out_path = os.path.join(self.tmp, "out-zero")
        result = self._run(
            out_path,
            ["--exclude-file", self.first_path,
             "--exclude-file", missing_path],
            segment="no-such-segment",
        )

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("文件不存在", stderr)
        self.assertIn(missing_path, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_path))

    def test_all_excluded_still_reads_all_files(self):
        # 第一份名单已排除全部受众时，仍读取校验后续名单文件。
        all_path = self._write(
            "all.txt",
            EMAIL_A + "\n" + EMAIL_B + "\n" + EMAIL_C + "\n",
        )
        missing_path = os.path.join(self.tmp, "missing-excludes.txt")
        out_path = os.path.join(self.tmp, "out-all-excluded")
        result = self._run(
            out_path,
            ["--exclude-file", all_path,
             "--exclude-file", missing_path],
        )

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn(missing_path, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_path))

    def test_only_first_failing_file_is_reported(self):
        # 两份名单都失败时，只报告按参数出现顺序的首个失败文件。
        missing_first = os.path.join(self.tmp, "missing-first.txt")
        bad_second = self._write_bytes("bad-second.txt", b"\xff")
        out_path = os.path.join(self.tmp, "out-first-fail")
        result = self._run(
            out_path,
            ["--exclude-file", missing_first,
             "--exclude-file", bad_second],
        )

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn(missing_first, stderr)
        self.assertNotIn(bad_second, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_path))

        # 首个文件成功、第二个解码失败：报告第二个文件，输出目录不创建。
        out_path2 = os.path.join(self.tmp, "out-second-fail")
        result = self._run(
            out_path2,
            ["--exclude-file", self.first_path,
             "--exclude-file", bad_second],
        )
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("无法解码", stderr)
        self.assertIn(bad_second, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_path2))

        # 已存在的空输出目录在失败后仍为空。
        out_empty = os.path.join(self.tmp, "out-empty")
        os.mkdir(out_empty)
        result = self._run(
            out_empty,
            ["--exclude-file", self.first_path,
             "--exclude-file", bad_second],
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(os.listdir(out_empty), [])

    def test_missing_value_exits_2(self):
        # 任一次 --exclude-file 缺值：退出 2，stderr 点名该参数。
        out_path = os.path.join(self.tmp, "out-missing-value")
        result = self._run(
            out_path,
            ["--exclude-file", self.first_path, "--exclude-file"],
        )

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr = result.stderr.decode("utf-8")
        self.assertIn("--exclude-file", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_path))

    def test_html_format_uses_merged_list(self):
        # html 模式共用合并名单：只有丙的 preview-0001.html。
        out_path = os.path.join(self.tmp, "out-html")
        result = self._run(
            out_path,
            ["--exclude-file", self.first_path,
             "--exclude-file", self.second_path,
             "--format", "html"],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.html", "report.json"],
        )
        self._assert_acceptance_report(self._read_report(out_path),
                                       extension="html")

    def test_index_and_manifest_use_merged_list(self):
        # --index 与 --manifest 采用同一合并结果：与等价单文件排除时的
        # 全部产物逐字节一致。
        merged_path = self._write(
            "merged.txt", EMAIL_A + "\n" + EMAIL_C + "\n"
        )
        out_multi = os.path.join(self.tmp, "out-multi-full")
        result_multi = self._run(
            out_multi,
            ["--exclude-file", self.first_path,
             "--exclude-file", self.second_path,
             "--index", "--manifest"],
        )
        out_single = os.path.join(self.tmp, "out-single-full")
        result_single = self._run(
            out_single,
            ["--exclude-file", merged_path, "--index", "--manifest"],
        )

        self.assertEqual(result_multi.returncode, 0,
                         msg=result_multi.stderr.decode("utf-8", "replace"))
        self.assertEqual(result_single.returncode, 0,
                         msg=result_single.stderr.decode("utf-8", "replace"))
        self.assertEqual(
            sorted(os.listdir(out_multi)),
            ["index.html", "manifest.csv",
             "preview-0001.txt", "report.json"],
        )
        self._assert_dir_trees_equal(out_multi, out_single)

        with open(os.path.join(out_multi, "index.html"),
                  encoding="utf-8") as fh:
            index = fh.read()
        self.assertIn("分组命中：4；排除：3；最终预览：1", index)
        self.assertIn('href="preview-0001.txt"', index)
        self.assertNotIn("戊", index)

    def test_single_file_behavior_unchanged(self):
        # 仅提供一份名单时保持原有行为：与等价命令行名单逐字节一致。
        out_file = os.path.join(self.tmp, "out-one-file")
        result_file = self._run(
            out_file, ["--exclude-file", self.second_path]
        )
        out_cli = os.path.join(self.tmp, "out-one-cli")
        result_cli = self._run(
            out_cli,
            ["--exclude-email", EMAIL_A, "--exclude-email", EMAIL_C],
        )

        self.assertEqual(result_file.returncode, 0,
                         msg=result_file.stderr.decode("utf-8", "replace"))
        self.assertEqual(result_cli.returncode, 0,
                         msg=result_cli.stderr.decode("utf-8", "replace"))
        self._assert_dir_trees_equal(out_file, out_cli)


if __name__ == "__main__":
    unittest.main()
