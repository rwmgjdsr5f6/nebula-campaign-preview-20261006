"""newsletter_preview --format html 与 --exclude-email 组合使用的回归测试。

在固定 UTF-8 CSV 样例上核对 HTML 预览与邮箱排除组合使用时，排除后
留下的联系人、逐人正文与 report.json 清单始终互相对应：

- 固定样例五条记录依次为 甲、乙&安、丙、丁<宁>、戊；前四条 segment
  为 newsletter，戊为 archive；甲、丙、戊共用 cut@example.invalid，
  乙&安与丁<宁>共用 keep@example.invalid。模板正文为
  “你好，{{name}}！”且末尾恰有一个 LF。
- 选择 newsletter、--format html 并重复两次排除 cut@example.invalid：
  退出 0、标准输出与标准错误均为空；目录恰好为 preview-0001.html、
  preview-0002.html 与 report.json；两份正文依次为“你好，乙&安！”与
  “你好，丁<宁>！”，末尾 LF 保留，姓名中的 &、<、> 按字面显示，
  不成为标签或实体；报告保留模板原文与筛选值，segment_count 为 4、
  excluded_count 为 2、matched_count 为 2；排除明细按 CSV 顺序仅含
  甲、丙（archive 的戊不在内），预览清单保留两条相同邮箱记录并分别
  指向实际 HTML 文件，不合并、不留空号。
- 边界一：再排除 keep@example.invalid 后四条命中全部移除：退出 0，
  目录仅有 report.json，三个计数依次为 4、4、0，排除明细按原顺序
  包含前四条，预览清单为空。
- 边界二：将甲的 name 留空且仍排除其邮箱：退出 2，标准错误点名
  第 2 行与空字段 name，无 Traceback；尚不存在的输出目录保持不
  存在，已有空目录保持为空。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出、标准错误、落盘文件内容与输出目录状态；
不直接调用内部函数。每个样例使用独立临时目录，结束后自动清理。
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

# 固定验收样例（各行以 LF 结束）：前四条属 newsletter，戊属 archive；
# 甲、丙、戊共用 cut 邮箱，乙&安与丁<宁>共用 keep 邮箱。姓名中的
# & 与尖括号用于核对 HTML 转义后按字面显示。
CONTACTS = (
    "name,email,segment\n"
    "甲,cut@example.invalid,newsletter\n"
    "乙&安,keep@example.invalid,newsletter\n"
    "丙,cut@example.invalid,newsletter\n"
    "丁<宁>,keep@example.invalid,newsletter\n"
    "戊,cut@example.invalid,archive\n"
)
# 边界样例：甲的 name 留空（表头第 1 行，该记录为文件第 2 行）。
CONTACTS_BLANK_NAME = (
    "name,email,segment\n"
    ",cut@example.invalid,newsletter\n"
    "乙&安,keep@example.invalid,newsletter\n"
    "丙,cut@example.invalid,newsletter\n"
    "丁<宁>,keep@example.invalid,newsletter\n"
    "戊,cut@example.invalid,archive\n"
)
TEMPLATE = "你好，{{name}}！\n"
SEGMENT = "newsletter"
EMAIL_CUT = "cut@example.invalid"
EMAIL_KEEP = "keep@example.invalid"

BODY_YI = "你好，乙&安！\n"
BODY_DING = "你好，丁<宁>！\n"


class _PreTextExtractor(HTMLParser):
    """按浏览器规则解析文档，收集唯一 pre 元素内的全部文本。

    convert_charrefs=True 使数字/命名实体按解析结果还原，因此还原后的
    文本与浏览器显示一致：&、<、> 转义后应按字面出现，尖括号文字不成为
    页面元素，实体样式文字不被当作实体解析。
    """

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


class HtmlExcludeCombinedTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.contacts_path = self._write("contacts.csv", CONTACTS)
        self.template_path = self._write("template.txt", TEMPLATE)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _run(self, out_path, extra_args=(), contacts_path=None):
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
            self.template_path,
            "--segment",
            SEGMENT,
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

    def _pre_text(self, out_path, filename):
        """读取 HTML 文件，返回（原始文档, pre 内按浏览器规则还原的文本）。"""
        with open(os.path.join(out_path, filename), encoding="utf-8") as fh:
            doc = fh.read()
        parser = _PreTextExtractor()
        parser.feed(doc)
        return doc, parser.text()

    def test_html_previews_and_report_stay_aligned_after_exclusions(self):
        # 选择 newsletter、HTML 格式，重复两次排除 cut@example.invalid：
        # 甲、丙被移除（archive 的戊不参与），乙&安、丁<宁>保留。
        out_path = os.path.join(self.tmp, "out")
        result = self._run(
            out_path,
            [
                "--format", "html",
                "--exclude-email", EMAIL_CUT,
                "--exclude-email", EMAIL_CUT,
            ],
        )

        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

        # 目录恰好包含连续编号的两份 HTML 与报告，无文本副本、无空号。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.html", "preview-0002.html", "report.json"],
        )

        # 逐人正文按 CSV 顺序对应保留记录：符号字面显示，末尾 LF 保留。
        doc_yi, text_yi = self._pre_text(out_path, "preview-0001.html")
        self.assertEqual(text_yi, BODY_YI)
        self.assertIn("乙&amp;安", doc_yi)
        self.assertNotIn("乙&安", doc_yi)
        # 正文末尾的 LF 紧贴 </pre>，未被 HTML 包装吞掉。
        self.assertIn("！\n</pre>", doc_yi)

        doc_ding, text_ding = self._pre_text(out_path, "preview-0002.html")
        self.assertEqual(text_ding, BODY_DING)
        self.assertIn("丁&lt;宁&gt;", doc_ding)
        self.assertNotIn("<宁>", doc_ding)
        self.assertIn("！\n</pre>", doc_ding)

        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)

        # 报告保留模板原文与筛选值；计数依次为分组命中 4、排除 2、保留 2。
        self.assertEqual(report["template"], TEMPLATE)
        self.assertEqual(report["segment"], SEGMENT)
        self.assertEqual(report["segment_count"], 4)
        self.assertEqual(report["excluded_count"], 2)
        self.assertEqual(report["matched_count"], 2)

        # 排除明细仅按 CSV 顺序包含甲、丙；不同分组的戊不在其中，
        # 重复传入同一排除值不叠加。
        self.assertEqual(
            report["excluded_contacts"],
            [
                {"name": "甲", "email": EMAIL_CUT},
                {"name": "丙", "email": EMAIL_CUT},
            ],
        )

        # 预览清单保留两条相同邮箱记录，分别指向目录中的实际 HTML 文件，
        # 不合并、不留空号；清单顺序与逐人正文一一对应。
        self.assertEqual(
            report["previews"],
            [
                {"email": EMAIL_KEEP, "file": "preview-0001.html"},
                {"email": EMAIL_KEEP, "file": "preview-0002.html"},
            ],
        )
        expected_bodies = (BODY_YI, BODY_DING)
        for entry, expected_body in zip(report["previews"], expected_bodies):
            preview_file = os.path.join(out_path, entry["file"])
            self.assertTrue(os.path.isfile(preview_file))
            parser = _PreTextExtractor()
            with open(preview_file, encoding="utf-8") as fh:
                parser.feed(fh.read())
            self.assertEqual(parser.text(), expected_body)
        self.assertEqual(len(report["previews"]), report["matched_count"])
        self.assertEqual(
            len(report["excluded_contacts"]), report["excluded_count"]
        )

    def test_excluding_remaining_shared_email_leaves_report_only(self):
        # 边界：在排除 cut 的基础上再排除 keep，四条 newsletter 命中
        # 全部移除；退出 0，目录仅有报告，预览清单为空。
        out_path = os.path.join(self.tmp, "out-all")
        result = self._run(
            out_path,
            [
                "--format", "html",
                "--exclude-email", EMAIL_CUT,
                "--exclude-email", EMAIL_CUT,
                "--exclude-email", EMAIL_KEEP,
            ],
        )

        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(os.listdir(out_path), ["report.json"])

        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["template"], TEMPLATE)
        self.assertEqual(report["segment"], SEGMENT)
        self.assertEqual(report["segment_count"], 4)
        self.assertEqual(report["excluded_count"], 4)
        self.assertEqual(report["matched_count"], 0)
        # 排除明细依原 CSV 顺序包含前四条；共享邮箱逐条列出、不合并。
        self.assertEqual(
            report["excluded_contacts"],
            [
                {"name": "甲", "email": EMAIL_CUT},
                {"name": "乙&安", "email": EMAIL_KEEP},
                {"name": "丙", "email": EMAIL_CUT},
                {"name": "丁<宁>", "email": EMAIL_KEEP},
            ],
        )
        self.assertEqual(report["previews"], [])

    def test_blank_name_exits_2_and_leaves_no_output_when_email_excluded(self):
        # 边界：甲的 name 留空且其邮箱仍被排除——即使该记录会因排除
        # 移除，CSV 完整校验仍须先失败：退出 2，stderr 点名第 2 行与
        # 空字段 name，无 Traceback；不创建任何输出。
        contacts_path = self._write("blank-name.csv", CONTACTS_BLANK_NAME)
        extra = [
            "--format", "html",
            "--exclude-email", EMAIL_CUT,
            "--exclude-email", EMAIL_CUT,
        ]

        # 情形 A：输出目录尚不存在——运行后仍须不存在。
        out_absent = os.path.join(self.tmp, "out-absent")
        result = self._run(out_absent, extra, contacts_path=contacts_path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        stderr = result.stderr.decode("utf-8")
        self.assertIn("第 2 行", stderr)
        self.assertIn("name", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))

        # 情形 B：输出目录已存在且为空——运行后仍须为空目录。
        out_empty = os.path.join(self.tmp, "out-empty")
        os.mkdir(out_empty)
        result = self._run(out_empty, extra, contacts_path=contacts_path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        stderr = result.stderr.decode("utf-8")
        self.assertIn("第 2 行", stderr)
        self.assertIn("name", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertTrue(os.path.isdir(out_empty))
        self.assertEqual(os.listdir(out_empty), [])


if __name__ == "__main__":
    unittest.main()
