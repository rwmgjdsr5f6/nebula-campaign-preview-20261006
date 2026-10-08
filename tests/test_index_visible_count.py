"""--index 索引页搜索框旁“当前显示 n 条，共 m 条”计数的黑盒回归测试。

覆盖用户验收约定：开启 --index 后，保留清单搜索框旁同句显示
“当前显示 n 条，共 m 条”——n 是当前可见的保留记录数，m 是本次
matched_count，均为实际数字；初始查询为空时两数相同，输入变化时
计数与可见行立即同步（共享邮箱逐条计数；表头行、搜索无结果提示行
及“已排除的联系人”区域均不计入 n），清空查询恢复全量；非空查询
零命中时 n 为 0 且表体显示“没有符合搜索条件的联系人”。零分组命中
或全部排除时页面始终静态为“当前显示 0 条，共 0 条”，输入任意查询
也保持 0/0，保留区域只剩“没有可预览的联系人”空状态。默认 text 与
--format html 计数一致，CSV 顺序与同目录相对预览链接不变。计数只
在本地浏览器更新：不写文件、不请求网络；逐人预览、report.json 与
省略 --index 时的产物由其余测试文件保证，本文件不再重复。

环境无浏览器可用，行为核对分两层，缺一不可：
1. 静态契约（HTMLParser 按浏览器规则解析真实落盘页面）：计数句是
   contact-search 容器内排在 label、input 之后的唯一一个 span，带
   固定 id，初始文字为“当前显示 M 条，共 M 条”（M 为实际数字，与
   报告 matched_count 一致）；页面恰有一个无 src 的内联 <script>，
   按固定 id 取该 span（容忍元素缺失），以 `var totalCount =
   fields.length;` 固定总数，过滤循环统计 visible 后用
   textContent 按“前缀 + visible + 中缀 + totalCount + 后缀”改写
   整句；脚本不含 trim、大小写归一、replace 等查询归一处理，也不
   含 fetch、XHR、http、src 等任何可发起请求的标记。
2. 数据驱动模拟：从真实页面脚本解析 var fields 数据岛（与保留行
   一一平行），并从脚本中正则提取计数句的三段固定文字（前缀、中缀、
   后缀），按与脚本逐行对应的规则（空查询全显；否则姓名或邮箱原文
   子串包含，区分大小写、不修剪、不折叠空格）逐查询计算 visible，
   拼出脚本在该查询下会写入的整句，对用户验收的每个查询逐一断言；
   模拟读取与拼装的都是生成物中的真实文本，而非测试另造的一份。

仅依赖 Python 3 标准库，完全离线；样例联系人为合成数据，邮箱使用
RFC 2606 保留的 example.invalid 虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 用户验收固定样例：甲、乙共用 shared@example.invalid，丙用
# b@example.invalid，丁用 cut@example.invalid，四人均属 newsletter。
CONTACTS = (
    "name,email,segment\n"
    "甲,shared@example.invalid,newsletter\n"
    "乙,shared@example.invalid,newsletter\n"
    "丙,b@example.invalid,newsletter\n"
    "丁,cut@example.invalid,newsletter\n"
)
# 模板内容逐字为“你好，{{name}}！”，无末尾换行。
TEMPLATE = "你好，{{name}}！"
SEGMENT = "newsletter"
EMAIL_SHARED = "shared@example.invalid"
EMAIL_B = "b@example.invalid"
EMAIL_CUT = "cut@example.invalid"

SEARCH_BOX_ID = "contact-search"
VISIBLE_COUNT_ID = "visible-count"
COUNT_TEXT_PREFIX = "当前显示 "
COUNT_TEXT_MIDDLE = " 条，共 "
COUNT_TEXT_SUFFIX = " 条"
SEARCH_EMPTY_MESSAGE = "没有符合搜索条件的联系人"
NO_PREVIEW_MESSAGE = "没有可预览的联系人"


def expected_count_text(visible, total):
    """按用户验收文案拼出计数句（两数均为实际数字的十进制文本）。"""
    return (
        f"{COUNT_TEXT_PREFIX}{visible}{COUNT_TEXT_MIDDLE}"
        f"{total}{COUNT_TEXT_SUFFIX}"
    )


class _CountParser(HTMLParser):
    """解析搜索框容器结构与计数 span：收集 contact-search 容器内
    按出现顺序排列的子元素标签、计数 span 的 id 与文本、全部文本。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.search_children = []
        self.count_spans = []
        self.texts = []
        self._depth = 0
        self._in_search = False
        self._span = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "div" and "contact-search" in attrs_dict.get(
            "class", ""
        ).split():
            self._in_search = True
            self._depth = 1
            return
        if self._in_search:
            self._depth += 1
            self.search_children.append(tag)
            if tag == "span":
                self._span = {
                    "id": attrs_dict.get("id"),
                    "parts": [],
                }

    def handle_startendtag(self, tag, attrs):
        # <input .../> 等自闭合写法也计入容器子元素顺序。
        if self._in_search:
            self.search_children.append(tag)

    def handle_data(self, data):
        self.texts.append(data)
        if self._span is not None:
            self._span["parts"].append(data)

    def handle_endtag(self, tag):
        if not self._in_search:
            return
        if tag == "span" and self._span is not None:
            self.count_spans.append(
                {"id": self._span["id"],
                 "text": "".join(self._span["parts"])}
            )
            self._span = None
        self._depth -= 1
        if tag == "div" or self._depth <= 0:
            self._in_search = False

    def text(self):
        return "".join(self.texts)


def extract_script(raw):
    """页面恰有一个无属性内联脚本，提取其完整文本。"""
    scripts = re.findall(r"<script>([\s\S]*?)</script>", raw)
    assert len(scripts) == 1, f"内联脚本数量应为 1，实际 {len(scripts)}"
    return scripts[0]


def extract_fields(script):
    """提取 var fields 数据岛并还原为 [[姓名, 邮箱], ...]。"""
    match = re.search(r"^var fields = (\[.*\]);$", script, re.MULTILINE)
    assert match is not None, "脚本中缺少 var fields 数据岛"
    return json.loads(match.group(1))


def extract_count_fragments(script):
    """从脚本提取计数句三段固定文字：

        countBox.textContent = "前缀" + visible + "中缀"
          + totalCount + "后缀";

    模拟时用生成物里的真实片段拼句，而不是测试自己另写一份模板。
    """
    match = re.search(
        r'countBox\.textContent = "([^"]*)" \+ visible \+ "([^"]*)"\s*'
        r'\+ totalCount \+ "([^"]*)";',
        script,
    )
    assert match is not None, "脚本中缺少 countBox.textContent 计数句"
    return match.group(1), match.group(2), match.group(3)


def simulate(fields, fragments, query):
    """按与内联脚本逐行对应的规则计算 (计数句, 提示行是否隐藏)。

    query 为空全部可见；否则 Python 子串运算 `query in 字段` 等价于
    脚本的 `字段.indexOf(query) !== -1`：区分大小写、不修剪、不折叠
    空格。n 逐条统计可见的保留数据行（数据岛即保留行，表头、提示行、
    排除行都不在其中），m 恒为数据岛长度。
    """
    prefix, middle, suffix = fragments
    visible = sum(
        1
        for name, email in fields
        if query == "" or query in name or query in email
    )
    total = len(fields)
    text = f"{prefix}{visible}{middle}{total}{suffix}"
    hint_hidden = query == "" or visible != 0
    return text, hint_hidden


class IndexVisibleCountTestCase(unittest.TestCase):
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

    def _run(self, out_name, extra_args=(), fmt=None,
             contacts_path=None, template_path=None, segment=SEGMENT):
        out_path = os.path.join(self.tmp, out_name)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = (
            PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        )
        argv = [
            sys.executable, "-m", "newsletter_preview",
            "--contacts", contacts_path or self.contacts_path,
            "--template", template_path or self.template_path,
            "--segment", segment,
            "--out", out_path,
        ]
        if fmt is not None:
            argv.extend(["--format", fmt])
        argv.extend(extra_args)
        argv.append("--index")
        result = subprocess.run(
            argv, cwd=PROJECT_ROOT, env=env, capture_output=True
        )
        return result, out_path

    def _load(self, out_path):
        with open(os.path.join(out_path, "index.html"), encoding="utf-8",
                  newline="") as fh:
            raw = fh.read()
        parser = _CountParser()
        parser.feed(raw)
        script = extract_script(raw)
        fields = extract_fields(script)
        fragments = extract_count_fragments(script)
        with open(os.path.join(out_path, "report.json"),
                  encoding="utf-8") as fh:
            report = json.load(fh)
        return raw, parser, script, fields, fragments, report

    def _assert_static_contract(self, raw, parser, script, fields,
                                report, expected_total):
        # 计数 span 唯一，带固定 id，位于 label、input 之后。
        self.assertEqual(
            parser.search_children[:3], ["label", "input", "span"]
        )
        self.assertEqual(len(parser.count_spans), 1)
        span = parser.count_spans[0]
        self.assertEqual(span["id"], VISIBLE_COUNT_ID)
        # 初始查询为空：n 与 m 相同，且 m 等于报告 matched_count。
        self.assertEqual(report["matched_count"], expected_total)
        initial = expected_count_text(expected_total, expected_total)
        self.assertEqual(span["text"], initial)
        # 落盘页面中的计数句只有这一处（脚本以拼接生成，不烘焙结果）。
        self.assertEqual(raw.count(initial), 1)
        self.assertIn(f'id="{VISIBLE_COUNT_ID}"', raw)

        # 脚本按固定 id 取节点、容忍缺失；总数取数据岛长度；加载即执行。
        self.assertIn(
            f'getElementById("{VISIBLE_COUNT_ID}")', script
        )
        self.assertIn("countBox !== null", script)
        self.assertIn("var totalCount = fields.length;", script)
        self.assertIn("countBox.textContent = ", script)
        self.assertIn('"当前显示 " + visible + " 条，共 "', script)
        self.assertIn('+ totalCount + " 条";', script)
        self.assertIn("applyFilter();", script)
        self.assertEqual(len(fields), expected_total)

        # 查询不得归一；页面与脚本不得含可发起请求/导航的标记。
        for forbidden in (
            "trim", "toLowerCase", "toLocaleLowerCase", "normalize",
            ".replace", ".split",
        ):
            self.assertNotIn(forbidden, script)
        for forbidden in (
            "src=", "http://", "https://", "fetch(", "XMLHttpRequest",
            "WebSocket", "import(", "mailto:",
        ):
            self.assertNotIn(forbidden, raw)

    def test_acceptance_shared_zzz_sequence_default_text(self):
        # 用户验收命令（默认 text）：排除 cut@example.invalid。
        result, out_path = self._run(
            "previews", ["--exclude-email", EMAIL_CUT]
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

        raw, parser, script, fields, fragments, report = self._load(
            out_path
        )
        # 三个分组计数依次为 4、1、3，不随查询变化。
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (4, 1, 3),
        )
        self.assertIn("分组命中：4；排除：1；最终预览：3", parser.text())
        self._assert_static_contract(
            raw, parser, script, fields, report, 3
        )
        # 数据岛即保留三人（甲、乙共享邮箱各占一项），丙独立。
        self.assertEqual(
            fields,
            [["甲", EMAIL_SHARED], ["乙", EMAIL_SHARED],
             ["丙", EMAIL_B]],
        )
        # 相对预览链接保持 CSV 顺序、指向同目录 .txt。
        for href in ("preview-0001.txt", "preview-0002.txt",
                     "preview-0003.txt"):
            self.assertIn(f'href="{href}"', raw)
            self.assertTrue(os.path.isfile(os.path.join(out_path, href)))

        # 用户验收查询序列：(查询, 期望 n, 提示行是否隐藏)。
        cases = [
            ("", 3, True),       # 初始/清空：3/3
            ("shared", 2, True),  # 仅甲、乙：2/3
            ("zzz", 0, False),   # 零命中：0/3，提示行显示
            ("", 3, True),       # 清空恢复 3/3
            # 追加边界：区分大小写；排除区域的丁不计入 n。
            ("Shared", 0, False),
            ("cut", 0, False),
            ("b@", 1, True),
        ]
        for query, expect_visible, expect_hint_hidden in cases:
            with self.subTest(query=query):
                text, hint_hidden = simulate(fields, fragments, query)
                self.assertEqual(text, expected_count_text(
                    expect_visible, 3
                ))
                self.assertEqual(hint_hidden, expect_hint_hidden)
                if expect_visible == 0 and query != "":
                    self.assertIn(SEARCH_EMPTY_MESSAGE, parser.text())

    def test_format_html_same_count(self):
        # --format html：计数与 text 一致，仅预览链接扩展名不同。
        result, out_path = self._run(
            "previews-html", ["--exclude-email", EMAIL_CUT], fmt="html"
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        raw, parser, script, fields, fragments, report = self._load(
            out_path
        )
        self._assert_static_contract(
            raw, parser, script, fields, report, 3
        )
        for href in ("preview-0001.html", "preview-0002.html",
                     "preview-0003.html"):
            self.assertIn(f'href="{href}"', raw)
            self.assertTrue(os.path.isfile(os.path.join(out_path, href)))
        self.assertNotIn('href="preview-0001.txt"', raw)
        for query, expect_visible in (
            ("", 3), ("shared", 2), ("zzz", 0), ("丙", 1)
        ):
            with self.subTest(query=query):
                text, _ = simulate(fields, fragments, query)
                self.assertEqual(
                    text, expected_count_text(expect_visible, 3)
                )

    def test_zero_segment_match_always_zero_zero(self):
        # 零分组命中：静态 0/0；任意查询模拟下始终 0/0，保留区域
        # 只有固定空状态，页面无搜索无结果提示行（没有表格）。
        result, out_path = self._run(
            "previews-zero", segment="不存在的分组"
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        raw, parser, script, fields, fragments, report = self._load(
            out_path
        )
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (0, 0, 0),
        )
        self._assert_static_contract(
            raw, parser, script, fields, report, 0
        )
        self.assertEqual(fields, [])
        self.assertIn("var fields = [];", script)
        self.assertEqual(parser.count_spans[0]["text"],
                         expected_count_text(0, 0))
        self.assertIn(NO_PREVIEW_MESSAGE, parser.text())
        self.assertNotIn(SEARCH_EMPTY_MESSAGE, parser.text())
        for query in ("", "shared", "zzz", "甲", "<", " "):
            with self.subTest(query=query):
                text, _ = simulate(fields, fragments, query)
                self.assertEqual(text, expected_count_text(0, 0))
        # 零保留时没有表格，故页面不存在搜索无结果提示行元素，
        # 脚本对 emptyRow 为 null 的容错已在静态契约中核对。

    def test_all_excluded_always_zero_zero_region_untouched(self):
        # 全部排除：静态 0/0；排除区域四人齐全；任意查询下计数恒为
        # 0/0，排除行不进入数据岛、永不计入 n。
        result, out_path = self._run(
            "previews-none",
            ["--exclude-email", EMAIL_SHARED,
             "--exclude-email", EMAIL_B,
             "--exclude-email", EMAIL_CUT],
        )
        self.assertEqual(
            result.returncode, 0,
            msg=result.stderr.decode("utf-8", "replace"),
        )
        raw, parser, script, fields, fragments, report = self._load(
            out_path
        )
        self.assertEqual(
            (report["segment_count"], report["excluded_count"],
             report["matched_count"]),
            (4, 4, 0),
        )
        self._assert_static_contract(
            raw, parser, script, fields, report, 0
        )
        self.assertIn(NO_PREVIEW_MESSAGE, parser.text())
        self.assertIn("分组命中：4；排除：4；最终预览：0", parser.text())
        # 排除区域仍有四条（甲、乙、丙、丁），但数据岛为空。
        self.assertEqual(fields, [])
        self.assertIn(EMAIL_CUT, parser.text())
        for query in ("", "shared", EMAIL_SHARED, EMAIL_CUT,
                      "丁", "zzz"):
            with self.subTest(query=query):
                text, _ = simulate(fields, fragments, query)
                self.assertEqual(text, expected_count_text(0, 0))
        # 计数句静态只有初始 0/0 一处；搜索不向页面烘焙其他值。
        self.assertEqual(raw.count(expected_count_text(0, 0)), 1)


if __name__ == "__main__":
    unittest.main()
