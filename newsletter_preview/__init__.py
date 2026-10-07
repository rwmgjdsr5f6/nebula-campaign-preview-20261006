"""邮件活动离线预览台：按 segment 单值筛选联系人，生成逐人文本预览与 JSON 报告。

仅使用 Python 3 标准库，完全离线，不发送任何邮件。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys

REQUIRED_COLUMNS = ("name", "email", "segment")
KNOWN_PLACEHOLDERS = ("name", "email")
# 完整双花括号占位符，如 {{name}}；不完整的（如单个 { 或未闭合）按普通文字处理。
PLACEHOLDER_RE = re.compile(r"\{\{([^{}]*)\}\}")


class InputError(Exception):
    """输入或运行环境校验失败，对应退出码 2。"""


def _read_text(path, description):
    """以 UTF-8 读取文本文件；无法读取或解码时抛出 InputError。"""
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        raise InputError(f"无法读取{description}：文件不存在：{path}")
    except UnicodeDecodeError as exc:
        raise InputError(f"无法解码{description}（要求 UTF-8）：{path}：{exc}")
    except OSError as exc:
        raise InputError(f"无法读取{description}：{path}：{exc}")


def _parse_contacts(text):
    """解析并完整校验联系人 CSV（含未匹配行），返回记录列表。"""
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader, None)
        if header is None:
            raise InputError(
                "联系人 CSV 为空：缺少表头行（必需列：name、email、segment）"
            )

        indices = {}
        for column in REQUIRED_COLUMNS:
            if column not in header:
                raise InputError(
                    f"联系人 CSV 缺少必需列：{column}"
                    f"（实际表头：{', '.join(header) or '（空）'}）"
                )
            indices[column] = header.index(column)

        contacts = []
        for row in reader:
            line_no = reader.line_num
            if len(row) != len(header):
                raise InputError(
                    f"联系人 CSV 第 {line_no} 行字段数（{len(row)}）"
                    f"与表头字段数（{len(header)}）不一致"
                )
            record = {column: row[idx] for column, idx in indices.items()}
            for column in REQUIRED_COLUMNS:
                if record[column].strip() == "":
                    raise InputError(
                        f"联系人 CSV 第 {line_no} 行必需字段 {column} "
                        f"为空或仅含空白"
                    )
            contacts.append(record)
    except csv.Error as exc:
        raise InputError(f"联系人 CSV 解析失败：{exc}")
    return contacts


def _validate_template(template):
    """校验模板中的完整双花括号占位符，仅允许 {{name}} 与 {{email}}。"""
    for match in PLACEHOLDER_RE.finditer(template):
        variable = match.group(1)
        if variable not in KNOWN_PLACEHOLDERS:
            supported = "、".join(f"{{{{{name}}}}}" for name in KNOWN_PLACEHOLDERS)
            raise InputError(
                f"模板包含未知变量：{{{{{variable}}}}}（仅支持 {supported}）"
            )


def _render_template(template, contact):
    """单遍替换模板中的已知占位符，取当前记录的原始字段值。

    替换值本身不再解析：姓名或邮箱中看似占位符的文字原样保留；
    其余正文与换行（LF/CRLF）保持原样。
    """
    def substitute(match):
        return contact[match.group(1)]

    return PLACEHOLDER_RE.sub(substitute, template)


def _prepare_out_dir(path):
    """输出目录不存在时创建；存在时要求为空目录且可写。不覆盖已有文件。"""
    if os.path.exists(path):
        if not os.path.isdir(path):
            raise InputError(f"输出路径已存在且不是目录：{path}")
        if os.listdir(path):
            raise InputError(f"输出目录非空，拒绝覆盖已有文件：{path}")
        if not os.access(path, os.W_OK):
            raise InputError(f"输出目录不可写：{path}")
    else:
        try:
            os.makedirs(path)
        except OSError as exc:
            raise InputError(f"无法创建输出目录：{path}：{exc}")


def _write_file(path, content):
    """以 UTF-8 写入新文件；已存在或不可写时抛出 InputError。"""
    try:
        with open(path, "x", encoding="utf-8", newline="") as fh:
            fh.write(content)
    except FileExistsError:
        raise InputError(f"输出文件已存在，拒绝覆盖：{path}")
    except OSError as exc:
        raise InputError(f"无法写入输出文件：{path}：{exc}")


def _normalize_excludes(values):
    """校验并收集 --exclude-email 的值，返回去重后的原值集合。

    保留原文：区分大小写、不去除两端空白；重复值不叠加效果。空字符串
    或仅含空白属于参数错误（InputError，退出 2）。
    """
    if not values:
        return frozenset()
    for value in values:
        if value.strip() == "":
            raise InputError("--exclude-email 的值不能为空字符串或仅含空白")
    return frozenset(values)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="newsletter_preview",
        description="离线预览：按 segment 单值筛选联系人，"
        "生成逐人文本预览与 JSON 报告（不发送邮件）。",
    )
    parser.add_argument("--contacts", required=True, help="联系人 CSV 文件路径（UTF-8）")
    parser.add_argument("--template", required=True, help="文字模板文件路径（UTF-8）")
    parser.add_argument("--segment", required=True, help="筛选值：segment 列的精确匹配值")
    parser.add_argument("--out", required=True, help="输出目录（不存在则创建，存在则须为空）")
    parser.add_argument(
        "--exclude-email",
        action="append",
        default=None,
        metavar="EMAIL",
        help="排除邮箱：从匹配记录中移除 email 与之完全相同的记录"
        "（区分大小写、不修剪空白）；可重复提供以排除多个邮箱",
    )
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        # 先完整校验全部输入（含未匹配行），失败时不创建任何输出。
        excluded_emails = _normalize_excludes(args.exclude_email)
        contacts_text = _read_text(args.contacts, "联系人 CSV")
        template_text = _read_text(args.template, "模板文件")
        contacts = _parse_contacts(contacts_text)
        _validate_template(template_text)
        _prepare_out_dir(args.out)

        # 按原文区分大小写精确比较，不修剪值；保持 CSV 顺序，重复邮箱不合并。
        # 计数按 CSV 数据记录统计，不按唯一邮箱：共享邮箱的每条匹配记录
        # 各计一次命中，被排除时排除数同样逐条累加。
        segment_matched = [c for c in contacts if c["segment"] == args.segment]
        matched = [c for c in segment_matched if c["email"] not in excluded_emails]
        # 排除明细：分组命中后被 --exclude-email 移除的记录，按 CSV 顺序逐条
        # 收录（共享邮箱的每条命中各列一项，重复项保留），仅存原始姓名与邮箱。
        excluded = [c for c in segment_matched if c["email"] in excluded_emails]
        segment_count = len(segment_matched)
        excluded_count = len(excluded)
        previews = []
        for number, contact in enumerate(matched, start=1):
            filename = f"preview-{number:04d}.txt"
            # 单遍替换，替换值不再次解析；其余文字与换行原样保留。
            content = _render_template(template_text, contact)
            _write_file(os.path.join(args.out, filename), content)
            previews.append({"email": contact["email"], "file": filename})

        report = {
            "template": template_text,
            "segment": args.segment,
            "segment_count": segment_count,
            "excluded_count": excluded_count,
            "excluded_contacts": [
                {"name": c["name"], "email": c["email"]} for c in excluded
            ],
            "matched_count": len(matched),
            "previews": previews,
        }
        _write_file(
            os.path.join(args.out, "report.json"),
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
    except InputError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0
