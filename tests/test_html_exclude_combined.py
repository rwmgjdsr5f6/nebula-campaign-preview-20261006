"""newsletter_preview HTML 预览与 --exclude-email 组合使用的回归测试。

在同一份固定 UTF-8 CSV 样例上核对两者组合后的整体一致性：选择
newsletter 分组并以 HTML 格式输出，重复排除同一邮箱后，留下的联系人、
逐人 HTML 正文与 report.json 清单始终对应——被排除记录按 CSV 顺序进
入排除明细，保留记录按顺序从 preview-0001.html 连续编号，共享邮箱的
两条保留记录不合并、不留空号；姓名中的 & 与尖括号在 pre 中按字面显示，
不成标签或实体。另覆盖两个边界：全部命中记录被排除时仅生成计数为 0
的报告；被排除记录的必需字段为空时仍先做完整校验，退出 2 且不留下输出。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出/错误、落盘文件内容与输出目录状态；不直接调用
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

# 固定 UTF-8 CSV 样例：表头为现有必需列；数据依次为甲、乙&安、丙、
# 丁<宁>、戊。前四条 segment 为 newsletter，戊为 archive；甲、丙、戊
# 共用 cut@example.invalid，乙&安与丁<宁>共用 keep@example.invalid。
CONTACTS = (
    "name,email,segment\n"
    "甲,cut@example.invalid,newsletter\n"
    "乙&安,keep@example.invalid,newsletter\n"
    "丙,cut@example.invalid,newsletter\n"
    "丁<宁>,keep@example.invalid,newsletter\n"
    "戊,cut@example.invalid,archive\n"
)
# 模板正文末尾恰有一个 LF。
TEMPLATE = "你好，{{name}}！\n"
SEGMENT = "newsletter"
EMAIL_CUT = "cut@example.invalid"
EMAIL_KEEP = "keep@example.invalid"

BODY_YI = "你好，乙&安！\n"
BODY_DING = "你好，丁<宁>！\n"


class _PreTextExtractor(HTMLParser):
    """按浏览器规则解析文档，收集唯一 pre 元素内的全部文本。

    convert_charrefs=True 使数字/命名实体按解析结果还原，因此还原后的
    文本与浏览器显示一致：& 与尖括号样式文字应作为字面数据出现。
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

    def _run(self, out_path, extra_args=(), contacts_path=None,
             template_path=None):
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
            SEGMENT,
            "--out",
            out_path,
            "--format",
            "html",
        ]
        argv.extend(extra_args)
        return subprocess.run(
            argv,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )

    def _read_text(self, out_path, name):
        with open(os.path.join(out_path, name), encoding="utf-8",
                  newline="") as fh:
            return fh.read()

    def test_exclude_leaves_matching_contacts_bodies_and_report(self):
        # 选择 newsletter、HTML 格式，重复两次排除 cut@example.invalid：
        # 甲、丙被移除（archive 的戊不属于分组，不进排除明细），乙&安、
        # 丁<宁>保留。
        out_path = os.path.join(self.tmp, "out-html-exclude")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_CUT,
             "--exclude-email", EMAIL_CUT],
        )

        # 退出 0，标准输出与标准错误均为空。
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

        # 目录恰好包含两份连续编号的 HTML 与报告，无文本副本、无空号。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.html", "preview-0002.html", "report.json"],
        )

        # 两份正文按 CSV 顺序替换，末尾 LF 保留；姓名中的 & 与尖括号
        # 在 pre 中按字面显示，不成为标签或实体。
        for name, expected_body in (
            ("preview-0001.html", BODY_YI),
            ("preview-0002.html", BODY_DING),
        ):
            doc = self._read_text(out_path, name)
            parser = _PreTextExtractor()
            parser.feed(doc)
            self.assertEqual(parser.text(), expected_body)

        # 原始 HTML 字节中符号必须以转义形式出现，且不能形成真实元素：
        # 不存在 <宁> 标签，&安 不被当作实体。
        doc_first = self._read_text(out_path, "preview-0001.html")
        self.assertIn("乙&amp;安", doc_first)
        self.assertNotIn("乙&安", doc_first)
        doc_second = self._read_text(out_path, "preview-0002.html")
        self.assertIn("丁&lt;宁&gt;", doc_second)
        self.assertNotIn("<宁>", doc_second)

        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)

        # 报告保留模板原文与筛选值。
        self.assertEqual(report["template"], TEMPLATE)
        self.assertEqual(report["segment"], SEGMENT)
        # 计数按数据记录逐条统计：分组命中 4、排除 2（重复参数不叠加）、
        # 保留 2。
        self.assertEqual(report["segment_count"], 4)
        self.assertEqual(report["excluded_count"], 2)
        self.assertEqual(report["matched_count"], 2)

        # 排除明细仅按 CSV 顺序包含甲和丙（archive 的戊不在内）。
        self.assertEqual(
            report["excluded_contacts"],
            [
                {"name": "甲", "email": EMAIL_CUT},
                {"name": "丙", "email": EMAIL_CUT},
            ],
        )

        # 预览清单保留两条相同邮箱记录，分别指向实际存在的 HTML 文件；
        # 不合并、不留空号。
        self.assertEqual(
            report["previews"],
            [
                {"email": EMAIL_KEEP, "file": "preview-0001.html"},
                {"email": EMAIL_KEEP, "file": "preview-0002.html"},
            ],
        )
        for entry in report["previews"]:
            self.assertTrue(
                os.path.isfile(os.path.join(out_path, entry["file"]))
            )

    def test_excluding_both_emails_leaves_report_only(self):
        # 边界一：在 cut 之外再排除 keep@example.invalid，newsletter 的
        # 四条记录全部被移除；命令仍退出 0，目录仅有报告。
        out_path = os.path.join(self.tmp, "out-all-excluded")
        result = self._run(
            out_path,
            ["--exclude-email", EMAIL_CUT,
             "--exclude-email", EMAIL_KEEP],
        )

        self.assertEqual(result.returncode, 0,
                         msg=result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(os.listdir(out_path), ["report.json"])

        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        # 三个计数依次为 4、4、0。
        self.assertEqual(report["segment_count"], 4)
        self.assertEqual(report["excluded_count"], 4)
        self.assertEqual(report["matched_count"], 0)
        # 排除明细依原顺序包含前四条（archive 的戊不在内）。
        self.assertEqual(
            report["excluded_contacts"],
            [
                {"name": "甲", "email": EMAIL_CUT},
                {"name": "乙&安", "email": EMAIL_KEEP},
                {"name": "丙", "email": EMAIL_CUT},
                {"name": "丁<宁>", "email": EMAIL_KEEP},
            ],
        )
        self.assertEqual(len(report["excluded_contacts"]),
                         report["excluded_count"])
        # 预览清单为空。
        self.assertEqual(report["previews"], [])

    def test_excluded_row_with_empty_name_still_exits_2_without_output(self):
        # 边界二：甲的 name 留空，即使仍排除其邮箱，CSV 完整校验先于
        # 排除生效：退出 2，stderr 指出第 2 行及空字段 name，无 Traceback。
        contacts = (
            "name,email,segment\n"
            ",cut@example.invalid,newsletter\n"
            "乙&安,keep@example.invalid,newsletter\n"
            "丙,cut@example.invalid,newsletter\n"
            "丁<宁>,keep@example.invalid,newsletter\n"
            "戊,cut@example.invalid,archive\n"
        )
        contacts_path = self._write("contacts-empty-name.csv", contacts)
        extra = ["--exclude-email", EMAIL_CUT]

        # 尚不存在的输出目录：运行后保持不存在。
        out_absent = os.path.join(self.tmp, "out-absent")
        result = self._run(out_absent, extra, contacts_path=contacts_path)
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("第 2 行", stderr)
        self.assertIn("name", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))

        # 已存在的空目录：运行后保持为空，不留下预览或报告。
        out_empty = os.path.join(self.tmp, "out-empty")
        os.mkdir(out_empty)
        result = self._run(out_empty, extra, contacts_path=contacts_path)
        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("第 2 行", stderr)
        self.assertIn("name", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertEqual(os.listdir(out_empty), [])


if __name__ == "__main__":
    unittest.main()
