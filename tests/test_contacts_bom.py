"""newsletter_preview 联系人 CSV 起始 UTF-8 BOM 兼容的回归测试。

联系人文件开头、首行列名之前的一个 BOM（U+FEFF，字节 EF BB BF）
视为编码标记：带与不带起始标记的同一份数据，筛选、排除、预览与
报告结果必须逐字节一致。兼容仅限联系人文件开头：字段内部的
U+FEFF 属于正文，不得删除或修剪；模板文件的读取语义不变，模板
中的 U+FEFF（无论开头还是字段内部）仍作为正文保留在预览与报告
原文中。带 BOM 的样例删除 segment 整列后仍须退出 2，标准错误
点名缺列且不含 Traceback，并保持“失败时无输出”的约定；BOM 不
得掩盖其后的非法 UTF-8 字节。仅依赖 Python 3 标准库，完全离线；
样例联系人为合成数据，邮箱使用 RFC 2606 保留的 example.invalid
虚构域名。

从项目根目录执行：

    python -m unittest discover -s tests

测试通过公开入口 `python -m newsletter_preview` 以子进程方式运行，
断言真实退出码、标准输出、标准错误、落盘文件内容与目录状态；不
直接调用内部函数。每个样例使用独立临时目录，结束后自动清理。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

# 项目根目录（本文件位于 <root>/tests/ 下），同时作为子进程工作目录。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 固定验收样例（各行以 LF 结束）：甲、乙同属 newsletter，丙属 archive。
CONTACTS = (
    "name,email,segment\n"
    "甲,a@example.invalid,newsletter\n"
    "乙,b@example.invalid,newsletter\n"
    "丙,c@example.invalid,archive\n"
)
CONTACTS_BYTES = CONTACTS.encode("utf-8")
TEMPLATE = "你好，{{name}}！\n"
SEGMENT = "newsletter"
EXCLUDED_EMAIL = "b@example.invalid"
PREVIEW_BYTES = "你好，甲！\n".encode("utf-8")

# UTF-8 BOM：字符 U+FEFF 编码为 EF BB BF 三个字节。
BOM = "\ufeff"
BOM_BYTES = b"\xef\xbb\xbf"

EXPECTED_REPORT = {
    "template": TEMPLATE,
    "segment": SEGMENT,
    "segment_count": 2,
    "excluded_count": 1,
    "excluded_contacts": [{"name": "乙", "email": EXCLUDED_EMAIL}],
    "matched_count": 1,
    "previews": [
        {"email": "a@example.invalid", "file": "preview-0001.txt"},
    ],
}


class ContactsBomTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.template_path = self._write_text("template.txt", TEMPLATE)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_text(self, name, content):
        """以 UTF-8 文本写入（newline="" 不转换换行），返回路径。"""
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def _write_bytes(self, name, data):
        """以固定字节写入输入文件（用于放置 BOM 或非法字节），返回路径。"""
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _run(self, contacts_path, out_path, extra_args=(), template_path=None):
        """通过 README 记载的公开入口运行，返回 CompletedProcess。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        argv = [
            sys.executable,
            "-m",
            "newsletter_preview",
            "--contacts",
            contacts_path,
            "--template",
            template_path or self.template_path,
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

    def _assert_success_outputs(self, result, out_path):
        """核对固定样例的成功产物：退出码、目录清单、预览字节与报告内容。"""
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        # 各自独立的空目录中恰好一份预览与报告，无多余文件。
        self.assertEqual(
            sorted(os.listdir(out_path)),
            ["preview-0001.txt", "report.json"],
        )
        with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
            self.assertEqual(fh.read(), PREVIEW_BYTES)
        with open(os.path.join(out_path, "report.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), EXPECTED_REPORT)

    def test_bom_and_plain_contacts_produce_byte_identical_outputs(self):
        # 两份 CSV 除其一以 EF BB BF 开头外完全相同；分别输出到两个
        # 独立的空目录，结果（预览与报告）必须逐字节一致。
        plain_path = self._write_bytes("contacts.csv", CONTACTS_BYTES)
        bom_path = self._write_bytes("contacts-bom.csv", BOM_BYTES + CONTACTS_BYTES)
        out_plain = os.path.join(self.tmp, "previews-plain")
        out_bom = os.path.join(self.tmp, "previews-bom")
        os.mkdir(out_plain)
        os.mkdir(out_bom)
        args = ["--exclude-email", EXCLUDED_EMAIL]

        result_plain = self._run(plain_path, out_plain, args)
        result_bom = self._run(bom_path, out_bom, args)

        self._assert_success_outputs(result_plain, out_plain)
        self._assert_success_outputs(result_bom, out_bom)
        for filename in ("preview-0001.txt", "report.json"):
            with open(os.path.join(out_plain, filename), "rb") as fh:
                plain_bytes = fh.read()
            with open(os.path.join(out_bom, filename), "rb") as fh:
                bom_bytes = fh.read()
            self.assertEqual(plain_bytes, bom_bytes, msg=filename)

    def test_bom_file_missing_segment_column_exits_2_without_output(self):
        # 带 BOM 的样例删除 segment 整列：BOM 被识别为编码标记后，
        # 缺列检查仍须失败；stderr 点名 segment 与缺列说明。
        contacts = (
            "name,email\n"
            "甲,a@example.invalid\n"
            "乙,b@example.invalid\n"
            "丙,c@example.invalid\n"
        ).encode("utf-8")
        contacts_path = self._write_bytes(
            "contacts-bom-no-segment.csv", BOM_BYTES + contacts
        )

        # 情形 A：输出目录尚不存在——运行后仍须不存在。
        out_absent = os.path.join(self.tmp, "out-absent")
        result = self._run(contacts_path, out_absent)
        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        self.assertEqual(result.stdout, b"")
        stderr = result.stderr.decode("utf-8")
        for fragment in ("缺少必需列", "segment", "name, email"):
            self.assertIn(fragment, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(
            os.path.exists(out_absent),
            msg="校验失败后不得创建输出目录",
        )

        # 情形 B：输出目录已存在且为空——运行后仍须为空目录。
        out_empty = os.path.join(self.tmp, "out-empty")
        os.mkdir(out_empty)
        result = self._run(contacts_path, out_empty)
        self.assertEqual(
            result.returncode,
            2,
            msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
        )
        stderr = result.stderr.decode("utf-8")
        for fragment in ("缺少必需列", "segment"):
            self.assertIn(fragment, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertTrue(os.path.isdir(out_empty))
        self.assertEqual(
            os.listdir(out_empty),
            [],
            msg="校验失败后不得在空输出目录中留下任何文件",
        )

    def test_field_internal_feff_is_kept_verbatim(self):
        # 兼容处理只针对文件开头：姓名“甲”后的字段内部 U+FEFF 必须
        # 原样进入替换结果；带与不带文件开头 BOM 的两份输出仍逐字节一致。
        body = (
            "name,email,segment\n"
            f"甲{BOM},a@example.invalid,newsletter\n"
            "丙,c@example.invalid,archive\n"
        ).encode("utf-8")
        plain_path = self._write_bytes("inner-feff.csv", body)
        bom_path = self._write_bytes("inner-feff-bom.csv", BOM_BYTES + body)
        expected = f"你好，甲{BOM}！\n".encode("utf-8")

        for label, contacts_path in (("plain", plain_path), ("bom", bom_path)):
            out_path = os.path.join(self.tmp, f"out-inner-{label}")
            result = self._run(contacts_path, out_path)
            self.assertEqual(
                result.returncode,
                0,
                msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
            )
            with open(os.path.join(out_path, "preview-0001.txt"), "rb") as fh:
                self.assertEqual(fh.read(), expected, msg=label)

    def test_template_feff_is_kept_as_body(self):
        # 模板读取语义不变：模板开头的 BOM 与正文内部的 U+FEFF 都作为
        # 正文保留在预览与报告原文中，不随联系人 BOM 兼容而被剥离。
        contacts = self._write_bytes("contacts.csv", CONTACTS_BYTES)
        contacts_bom = self._write_bytes(
            "contacts-bom.csv", BOM_BYTES + CONTACTS_BYTES
        )
        cases = {
            "leading": BOM + "你好，{{name}}！\n",
            "inner": f"你{BOM}好，{{{{name}}}}！\n",
        }
        for label, template_text in cases.items():
            template_path = self._write_text(f"template-{label}.txt", template_text)
            for c_label, contacts_path in (
                ("plain", contacts),
                ("bom", contacts_bom),
            ):
                out_path = os.path.join(self.tmp, f"out-tpl-{label}-{c_label}")
                result = self._run(
                    contacts_path, out_path, template_path=template_path
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"stderr: {result.stderr.decode('utf-8', 'replace')}",
                )
                with open(
                    os.path.join(out_path, "preview-0001.txt"), encoding="utf-8"
                ) as fh:
                    # 模板中的 U+FEFF 保留；替换值“甲”本身不含 BOM。
                    self.assertEqual(
                        fh.read(),
                        template_text.replace("{{name}}", "甲"),
                        msg=f"{label}/{c_label}",
                    )
                with open(
                    os.path.join(out_path, "report.json"), encoding="utf-8"
                ) as fh:
                    report = json.load(fh)
                self.assertEqual(report["template"], template_text)

    def test_bom_does_not_mask_invalid_utf8(self):
        # BOM 之后出现非法 UTF-8 字节 0xFF：仍按非法 UTF-8 拒绝（退出 2、
        # stderr 含解码与 UTF-8 提示、无 Traceback），且不创建输出目录。
        contacts_path = self._write_bytes("bad-after-bom.csv", BOM_BYTES + b"\xff")
        out_absent = os.path.join(self.tmp, "out-bad")
        result = self._run(contacts_path, out_absent)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        stderr = result.stderr.decode("utf-8")
        for fragment in ("无法解码", "联系人 CSV", "UTF-8"):
            self.assertIn(fragment, stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_absent))

    def test_bom_only_file_is_treated_like_empty_file(self):
        # 仅含一个 BOM 的联系人文件剥离标记后为空文本，与完全空文件同样
        # 报“缺少表头行”：退出 2，无 Traceback，输出目录不被创建。
        bom_only = self._write_bytes("only-bom.csv", BOM_BYTES)
        empty = self._write_bytes("empty.csv", b"")
        for label, contacts_path in (("bom-only", bom_only), ("empty", empty)):
            out_path = os.path.join(self.tmp, f"out-{label}")
            result = self._run(contacts_path, out_path)
            self.assertEqual(result.returncode, 2, msg=label)
            stderr = result.stderr.decode("utf-8")
            self.assertIn("缺少表头行", stderr, msg=label)
            self.assertNotIn("Traceback (most recent call last)", stderr)
            self.assertFalse(os.path.exists(out_path), msg=label)

    def test_double_bom_strips_exactly_one(self):
        # 文件开头有两个 BOM 时只移除第一个（与 utf-8-sig 语义一致）：
        # 第二个 U+FEFF 留在首列名上，必需列 name 无法匹配，退出 2。
        contacts_path = self._write_bytes(
            "two-boms.csv", BOM_BYTES + BOM_BYTES + CONTACTS_BYTES
        )
        out_path = os.path.join(self.tmp, "out-two")
        result = self._run(contacts_path, out_path)

        self.assertEqual(result.returncode, 2)
        stderr = result.stderr.decode("utf-8")
        self.assertIn("缺少必需列", stderr)
        self.assertIn("name", stderr)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertFalse(os.path.exists(out_path))


if __name__ == "__main__":
    unittest.main()
