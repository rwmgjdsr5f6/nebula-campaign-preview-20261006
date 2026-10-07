"""邮件活动离线预览台：按 segment 单值筛选联系人，生成逐人文本预览与 JSON 报告。

仅使用 Python 3 标准库，完全离线，不发送任何邮件。
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import re
import sys

REQUIRED_COLUMNS = ("name", "email", "segment")
KNOWN_PLACEHOLDERS = ("name", "email", "segment")
OUTPUT_FORMATS = ("text", "html")
# 完整双花括号占位符，如 {{name}}；不完整的（如单个 { 或未闭合）按普通文字处理。
PLACEHOLDER_RE = re.compile(r"\{\{([^{}]*)\}\}")
# UTF-8 BOM（字节 EF BB BF）解码后的字符。仅联系人 CSV 文件开头的一个
# 视为编码标记；字段内部出现的同名字符属于正文，不得删除。
BOM = "\ufeff"


class InputError(Exception):
    """输入或运行环境校验失败，对应退出码 2。"""


def _read_text(path, description, strip_bom=False):
    """以 UTF-8 读取文本文件；无法读取或解码时抛出 InputError。

    strip_bom 为 True 时（仅联系人 CSV），把文件开头的一个 U+FEFF
    （字节 EF BB BF，UTF-8 BOM）视为编码标记并移除；字段内部的
    U+FEFF 原样保留，不修剪任何其他内容。模板文件不使用此选项。
    """
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
    except FileNotFoundError:
        raise InputError(f"无法读取{description}：文件不存在：{path}")
    except UnicodeDecodeError as exc:
        raise InputError(f"无法解码{description}（要求 UTF-8）：{path}：{exc}")
    except OSError as exc:
        raise InputError(f"无法读取{description}：{path}：{exc}")
    if strip_bom and text.startswith(BOM):
        text = text[len(BOM):]
    return text


def _parse_exclude_list(text):
    """解析 --exclude-file 名单文本，返回邮箱原文列表（保持文件顺序）。

    文件无表头、UTF-8：仅支持 LF 与 CRLF 两种行结束，末行允许没有换行。
    按 LF 切分后，每行至多移除一个行结束符（CRLF 的 CR、LF 本身作为
    切分符已消失）；单独出现的 CR 不是行结束，按正文保留。切分末尾由
    终止换行产生的空串只是行结束符的副产物，不当作一行；其余空行及仅
    含空白的行忽略。非空白行除行结束符外逐字保留：区分大小写、不修剪
    首尾空白。空文件视为空名单；名单文件不做 BOM 处理，开头的 U+FEFF
    属于正文。
    """
    lines = text.split("\n")
    # 末尾换行在 split 后产生一个空串，它只是行结束符的副产物而非一行；
    # 空文件同样切出单个空串，丢弃后即为空名单。该空串存在还意味着倒数
    # 第二段原本后面紧跟 LF——其末尾的 CR 属于 CRLF，需要一并移除。
    terminated_by_lf = bool(lines) and lines[-1] == ""
    if terminated_by_lf:
        lines.pop()
    emails = []
    last_index = len(lines) - 1
    for index, line in enumerate(lines):
        # 只有后面紧跟 LF 的 CR 才属于 CRLF；末行孤立的 CR 是正文，保留。
        if (index < last_index or terminated_by_lf) and line.endswith("\r"):
            line = line[:-1]
        if line.strip() == "":
            continue
        emails.append(line)
    return emails


def _read_exclude_list(path):
    """读取并解析 --exclude-file 名单；无法读取或解码时抛出 InputError。"""
    text = _read_text(path, "排除名单文件")
    return _parse_exclude_list(text)


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
    """校验模板中的完整双花括号占位符，仅允许 {{name}}、{{email}} 与 {{segment}}。"""
    for match in PLACEHOLDER_RE.finditer(template):
        variable = match.group(1)
        if variable not in KNOWN_PLACEHOLDERS:
            supported = "、".join(f"{{{{{name}}}}}" for name in KNOWN_PLACEHOLDERS)
            raise InputError(
                f"模板包含未知变量：{{{{{variable}}}}}（仅支持 {supported}）"
            )


def _render(template, record):
    """单次扫描替换全部已知占位符；替换值不再次解析，其余文字原样保留。"""
    return PLACEHOLDER_RE.sub(lambda match: record[match.group(1)], template)


def _wrap_html(body):
    """把替换后的正文包成声明 UTF-8 的完整 HTML 文档。

    正文放在唯一的 pre 元素中整体转义：模板与字段值中的 &、<、>、标签
    及实体样式文字均按字面显示，不会成为页面元素；正文在替换阶段已定型，
    这里不再次解析任何内容，不引用任何网络资源。中文、空格、空行与末尾
    换行随 UTF-8 字节与 pre 的预排版原样保留；仅正文开头的 LF 例外——
    HTML 解析会吞掉紧跟 pre 起始标签后的一个换行，故把该首字符写成数字
    字符引用 &#10;（浏览器显示与 HTML 实体解析结果仍是同一个 LF）。
    """
    escaped = html.escape(body, quote=False)
    # CRLF 同为一个紧跟标签的行终止符，一并保护其首字符。
    if escaped[:1] in ("\n", "\r"):
        first = "&#10;" if escaped[0] == "\n" else "&#13;"
        escaped = first + escaped[1:]
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>newsletter preview</title>\n"
        "</head>\n"
        "<body>\n"
        f"<pre>{escaped}</pre>\n"
        "</body>\n"
        "</html>\n"
    )


def _contact_text_cells(contact):
    """渲染一条联系人记录的姓名、邮箱两个纯文本单元格。

    保留清单与排除清单共用：两个字段都先经 HTML 转义再写入单元格，
    中文、&、尖括号、引号及 {{name}} 样式文字按字面显示，并保留
    大小写与首尾空白；单元格内不放任何链接。
    """
    return (
        f"<td>{html.escape(contact['name'], quote=True)}</td>"
        f"<td>{html.escape(contact['email'], quote=True)}</td>"
    )


def _contact_table(header_cells, rows, empty_message):
    """把已构造好的表格行渲染为带表头的完整表格；无行时渲染空状态。

    保留清单与排除清单共用同一套表格骨架与空状态规则：header_cells
    为表头单元格 HTML（模块内固定文案），rows 为完整 <tr> 片段；
    rows 为空时不输出表格，改为只输出固定提示段落。所有来自输入的
    转义由调用方在构造行时完成，此处只做拼装。
    """
    if not rows:
        return f"<p>{empty_message}</p>"
    return (
        "<table>\n"
        f"<thead><tr>{header_cells}</tr></thead>\n"
        "<tbody>\n"
        + "\n".join(rows)
        + "\n</tbody>\n"
        "</table>"
    )


# 两类清单的表头单元格与空状态均为模块内固定文案，不来自输入。
_RETAINED_HEADERS = "<th>姓名</th><th>邮箱</th><th>预览</th>"
_EXCLUDED_HEADERS = "<th>姓名</th><th>邮箱</th>"
_RETAINED_EMPTY = "没有可预览的联系人"
_EXCLUDED_EMPTY = "没有被排除的联系人"


def _build_index(segment, segment_count, excluded_count, matched, previews,
                 excluded):
    """构建 index.html：声明 UTF-8 的完整离线索引文档。

    页面展示筛选值与分组命中、排除、最终预览三个记录数（与报告同
    源）。保留联系人清单按 CSV 顺序列出每条保留记录的原始姓名、
    邮箱及预览链接；其后追加“已排除的联系人”区域，按 CSV 顺序逐条
    列出命中分组后被排除名单（--exclude-email / --exclude-file）移除
    的记录（与报告
    excluded_contacts 同内容、同顺序，条目数等于 excluded_count；
    未命中分组的记录不出现，共享邮箱的每条记录各列一项，重复排除
    值不重复增加条目），每条只显示原始姓名与邮箱文字，不提供预览
    或邮件链接；没有记录时两类清单各自显示固定空状态。两个区域的
    姓名/邮箱单元格与表格骨架由同一组辅助函数渲染
    （_contact_text_cells、_contact_table），区别仅在保留清单多出
    一列预览链接。所有来自输入的文字（筛选值、姓名、邮箱、预览
    文件名）与计数一样先经 HTML 转义再写入纯文本节点或属性：
    中文、&、尖括号、引号及 {{name}} 样式文字均按字面显示，不解析
    为标签、实体或变量，并保留大小写与首尾空白；保留清单中的邮箱
    仅作文字展示，不生成 mailto 等任何非文件链接。
    链接 href 只写同目录预览文件名这一相对地址，输出目录整体移动
    后仍可打开。matched 为空（零命中或全部排除）时保留清单区域
    显示固定提示且不含任何预览链接；零命中时两个空状态同时出现。
    文档不引用任何网络资源。
    """
    # 保留清单：共享的姓名、邮箱单元格之后追加一个同目录预览链接列。
    rows = []
    for contact, preview in zip(matched, previews):
        rows.append(
            "<tr>"
            + _contact_text_cells(contact)
            + f'<td><a href="{html.escape(preview["file"], quote=True)}">'
            "预览</a></td>"
            "</tr>"
        )
    listing = _contact_table(_RETAINED_HEADERS, rows, _RETAINED_EMPTY)

    # 排除区域只放文字：复用同一组单元格与表格骨架，无链接列、无
    # a 元素、无 mailto。
    excluded_rows = [
        "<tr>" + _contact_text_cells(contact) + "</tr>"
        for contact in excluded
    ]
    excluded_listing = _contact_table(
        _EXCLUDED_HEADERS, excluded_rows, _EXCLUDED_EMPTY
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>newsletter preview index</title>\n"
        "</head>\n"
        "<body>\n"
        "<h1>预览索引</h1>\n"
        f"<p>筛选值：{html.escape(segment, quote=True)}</p>\n"
        f"<p>分组命中：{segment_count}；排除：{excluded_count}；"
        f"最终预览：{len(matched)}</p>\n"
        f"{listing}\n"
        "<h2>已排除的联系人</h2>\n"
        f"{excluded_listing}\n"
        "</body>\n"
        "</html>\n"
    )


def _prepare_out_dir(path):
    """输出目录不存在时创建；存在时要求为空目录且可写。不覆盖已有文件。"""
    if os.path.exists(path):
        if not os.path.isdir(path):
            raise InputError(f"输出路径已存在且不是目录：{path}")
        try:
            entries = os.listdir(path)
        except OSError as exc:
            # 目录条目读取失败（权限不足等）既不能当作空目录继续，也不能
            # 误报为非空；连同底层原因按输入/环境校验失败处理（退出 2）。
            raise InputError(f"无法检查输出目录：{path}：{exc}")
        if entries:
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


def _normalize_excludes(cli_values, file_values):
    """校验并合并 --exclude-email 与 --exclude-file 的值，返回去重后的原值集合。

    保留原文：区分大小写、不去除两端空白；同一值无论来自命令行还是文件、
    出现几次，都只生效一次（重复值不叠加计数）。命令行的空字符串或仅含
    空白属于参数错误（InputError，退出 2）；名单文件中的空行或仅含空白
    行在解析阶段已忽略，不会进入这里。
    """
    values = list(cli_values or ())
    values.extend(file_values or ())
    if not values:
        return frozenset()
    for value in cli_values or ():
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
    parser.add_argument(
        "--exclude-file",
        metavar="FILE",
        default=None,
        help="从 UTF-8 本地名单文件追加排除邮箱（无表头，每行一个）："
        "支持 LF 与 CRLF 行结束及末行无换行；空行及仅含空白的行忽略，"
        "其余行只移除行结束符，保留大小写与首尾空白；空文件视为空名单。"
        "名单与 --exclude-email 合并后按邮箱原文精确匹配，重复值不叠加",
    )
    parser.add_argument(
        "--format",
        choices=OUTPUT_FORMATS,
        default="text",
        help="逐人预览格式：text（默认，.txt 文本）或 html"
        "（完整 UTF-8 HTML 文档，正文在 pre 中按字面显示，.html）",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="在输出目录额外生成 index.html：先按 CSV 顺序列出保留记录"
        "的原始姓名、邮箱与相对预览链接，再以内容、顺序与报告 "
        "excluded_contacts 一致的“已排除的联系人”区域逐条列出被排除"
        "记录（仅文字，无预览或邮件链接，无排除记录时显示空状态），"
        "并显示筛选值及分组命中、排除、最终预览三个计数；省略时不"
        "生成该文件，其余产物逐字节不变",
    )
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        # 先完整校验全部输入（含未匹配行、名单文件），失败时不创建任何输出。
        file_excludes = (
            _read_exclude_list(args.exclude_file)
            if args.exclude_file is not None
            else []
        )
        excluded_emails = _normalize_excludes(
            args.exclude_email, file_excludes
        )
        contacts_text = _read_text(args.contacts, "联系人 CSV", strip_bom=True)
        template_text = _read_text(args.template, "模板文件")
        contacts = _parse_contacts(contacts_text)
        _validate_template(template_text)
        _prepare_out_dir(args.out)

        # 按原文区分大小写精确比较，不修剪值；保持 CSV 顺序，重复邮箱不合并。
        # 计数按 CSV 数据记录统计，不按唯一邮箱：共享邮箱的每条匹配记录
        # 各计一次命中，被排除时排除数同样逐条累加。
        segment_matched = [c for c in contacts if c["segment"] == args.segment]
        matched = [c for c in segment_matched if c["email"] not in excluded_emails]
        # 排除明细：分组命中后被命令行/名单文件排除值移除的记录，按 CSV
        # 顺序逐条收录（共享邮箱的每条命中各列一项，重复项保留），仅存
        # 原始姓名与邮箱。
        excluded = [c for c in segment_matched if c["email"] in excluded_emails]
        segment_count = len(segment_matched)
        excluded_count = len(excluded)
        previews = []
        extension = "html" if args.format == "html" else "txt"
        for number, contact in enumerate(matched, start=1):
            filename = f"preview-{number:04d}.{extension}"
            # 单次扫描替换，替换值不再次解析；其余文字与换行原样保留。
            content = _render(template_text, contact)
            # html 模式仅在写出前包一层完整文档，正文内容不变；
            # text 模式逐字写出，与既有版本逐字节一致。
            if args.format == "html":
                content = _wrap_html(content)
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
        # 索引页最后写出：未开启 --index 时不走此步，已有预览与报告
        # 与未开启时逐字节一致；写入失败同样退出 2，已写出的文件保留。
        if args.index:
            _write_file(
                os.path.join(args.out, "index.html"),
                _build_index(
                    args.segment,
                    segment_count,
                    excluded_count,
                    matched,
                    previews,
                    excluded,
                ),
            )
    except InputError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0
