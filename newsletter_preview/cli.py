"""命令行入口与预览生成逻辑。

用法：
    python -m newsletter_preview \
        --contacts contacts.csv --template template.txt \
        --segment newsletter --out previews

约定见 README：输入均为 UTF-8；CSV 表头必需列 name、email、segment；
筛选按原文区分大小写精确比较；模板仅支持 {{name}}。
输入校验失败（退出码 2）时不创建任何输出目录或文件。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from typing import List, Sequence

REQUIRED_COLUMNS = ("name", "email", "segment")
# 完整双花括号占位符（花括号内不含花括号即可），如 {{name}}、{{city}}、
# {{ name }}、{{ }}；仅字面 {{name}} 受支持，其余一律按未知变量处理。
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
SUPPORTED_PLACEHOLDER = "{{name}}"
SUPPORTED_VARIABLE = "name"


class InputError(Exception):
    """输入无法读取、解码或参数/输出目录不合法。退出码为 2。"""


class ValidationError(Exception):
    """CSV 或模板内容校验失败。退出码为 2，不产生任何输出文件。"""


def _read_utf8(path: str, kind: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except OSError as exc:
        raise InputError(f"无法读取{kind} {path}: {exc.strerror or exc}") from None
    except UnicodeDecodeError as exc:
        raise InputError(
            f"{kind} {path} 不是有效的 UTF-8：第 {exc.start} 字节附近解码失败"
        ) from None


def _validate_template(template: str) -> str:
    """校验模板；当前仅支持字面 {{name}}，出现其他完整占位符即失败。"""
    unknown = [
        match.group(1)
        for match in _PLACEHOLDER_RE.finditer(template)
        if match.group(0) != SUPPORTED_PLACEHOLDER
    ]
    if unknown:
        names = "、".join(dict.fromkeys(unknown))
        raise ValidationError(f"模板包含未知变量：{names}（仅支持 {{{{{SUPPORTED_VARIABLE}}}}}）")
    return template


def _read_contacts(text: str, source: str) -> List[dict]:
    """完整解析并校验 CSV，包括最终不会匹配 segment 的行。"""
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValidationError(f"联系人 CSV {source} 为空：缺少表头行")

    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise ValidationError(
            f"联系人 CSV {source} 缺少必需列：{ '、'.join(missing) }"
        )

    indexes = {col: header.index(col) for col in REQUIRED_COLUMNS}
    width = len(header)
    contacts: List[dict] = []
    for line_no, row in enumerate(reader, start=2):
        if len(row) != width:
            raise ValidationError(
                f"联系人 CSV {source} 第 {line_no} 行字段数为 {len(row)}，"
                f"与表头列数 {width} 不一致"
            )
        values = {col: row[indexes[col]] for col in REQUIRED_COLUMNS}
        for col, value in values.items():
            if value.strip() == "":
                raise ValidationError(
                    f"联系人 CSV {source} 第 {line_no} 行必需列 {col} 的值为空或仅含空白"
                )
        contacts.append(values)
    return contacts


def _render(template: str, name: str) -> str:
    # 校验已保证 {{name}} 是唯一占位符；替换值原样写入、不再次解析。
    return template.replace(SUPPORTED_PLACEHOLDER, name)


def _check_out_dir(out_dir: str) -> None:
    if os.path.exists(out_dir):
        if not os.path.isdir(out_dir):
            raise InputError(f"输出路径 {out_dir} 已存在且不是目录")
        try:
            entries = os.listdir(out_dir)
        except OSError as exc:
            raise InputError(f"无法读取输出目录 {out_dir}: {exc.strerror or exc}") from None
        if entries:
            raise InputError(f"输出目录 {out_dir} 非空，拒绝覆盖已有文件")
        if not os.access(out_dir, os.W_OK):
            raise InputError(f"输出目录不可写：{out_dir}")
    else:
        parent = os.path.dirname(os.path.abspath(out_dir))
        if not os.path.isdir(parent):
            raise InputError(f"输出目录的父目录不存在：{parent}")
        if not os.access(parent, os.W_OK):
            raise InputError(f"输出目录父目录不可写：{parent}")


def _write_outputs(
    out_dir: str, template: str, segment: str, matched: Sequence[dict]
) -> None:
    previews = []
    try:
        os.makedirs(out_dir, exist_ok=True)
        for order, contact in enumerate(matched, start=1):
            file_name = f"preview-{order:04d}.txt"
            with open(os.path.join(out_dir, file_name), "w", encoding="utf-8", newline="") as fh:
                fh.write(_render(template, contact["name"]))
            previews.append({"email": contact["email"], "file": file_name})

        report = {
            "template": template,
            "segment": segment,
            "matched_count": len(matched),
            "previews": previews,
        }
        with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8", newline="") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError as exc:
        raise InputError(f"写入输出目录 {out_dir} 失败：{exc.strerror or exc}") from None


def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="newsletter_preview",
        description="按 segment 筛选合成联系人并生成逐人文本预览与 JSON 报告（不发送）。",
    )
    parser.add_argument("--contacts", required=True, help="联系人 CSV（UTF-8，表头含 name/email/segment）")
    parser.add_argument("--template", required=True, help="文字模板（UTF-8，仅支持 {{name}}）")
    parser.add_argument("--segment", required=True, help="筛选值，区分大小写精确比较")
    parser.add_argument("--out", required=True, help="输出目录（不存在则创建；存在时必须为空）")
    args = parser.parse_args(argv)

    try:
        # 先完整读取并校验全部输入（含未匹配行），通过后再检查、创建输出目录。
        contacts_text = _read_utf8(args.contacts, "联系人 CSV")
        template = _read_utf8(args.template, "模板文件")
        contacts = _read_contacts(contacts_text, args.contacts)
        _validate_template(template)
        _check_out_dir(args.out)

        matched = [c for c in contacts if c["segment"] == args.segment]
        _write_outputs(args.out, template, args.segment, matched)
    except (InputError, ValidationError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


def main() -> None:
    sys.exit(run())
