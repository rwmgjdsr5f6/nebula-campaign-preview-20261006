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
# --manifest 导出的核对清单文件名与固定表头。清单为无 BOM 的 UTF-8 CSV，
# 首行逐字为该表头，之后每行对应一条已生成预览的保留联系人记录。
MANIFEST_FILENAME = "manifest.csv"
MANIFEST_HEADER = ("name", "email", "segment", "preview_file")
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


# 索引页中需要按原文保留空格的元素共用的类名：筛选值段落与两类
# 联系人清单的姓名、邮箱单元格带此类（表头与“预览”链接单元格不带）。
INDEX_FIELD_CLASS = "field-value"
# 保留清单搜索框（输入元素）的固定 id，也是页内 <label for> 与脚本
# 查找的同一目标；初始不带 value 属性，故第一次打开时查询为空。
INDEX_SEARCH_BOX_ID = "contact-search"
# 保留清单每一数据行 <tr> 的固定类名：脚本只凭该类收集参与搜索的
# 行，已排除区域的行不带此类，永不参与过滤。
INDEX_RETAINED_ROW_CLASS = "retained-row"
# 保留清单表体末尾“搜索无结果”提示行的固定 id 与文案。该行初始带
# hidden（布尔属性），仅当查询非空且没有任何保留行命中时由脚本展示；
# 它不是数据行：无 data-* 字段、不带 retained-row 类、不含预览链接。
INDEX_SEARCH_EMPTY_ID = "search-empty-row"
INDEX_SEARCH_EMPTY_MESSAGE = "没有符合搜索条件的联系人"
# 保留区域没有任何记录（零命中或全部排除）时的固定空状态文案。
INDEX_NO_PREVIEW_MESSAGE = "没有可预览的联系人"
# 搜索框旁“当前显示 n 条，共 m 条”计数元素的固定 id。n 为当前可见
# 保留记录数（逐条统计，共享邮箱各算一条；表头、“搜索无结果”提示行
# 与排除区域不计入），m 为本次 matched_count。元素与搜索框一样始终
# 渲染：零命中或全部排除时固定显示“当前显示 0 条，共 0 条”。
INDEX_SEARCH_COUNT_ID = "search-count"


def _esc(value):
    """转义进入索引页的单条输入文字（姓名、邮箱、预览文件名等）。

    quote=True 同时转义引号：无论落在纯文本节点还是 href 属性中，
    中文、&、尖括号、引号及 {{name}} 样式文字均按字面显示，不解析
    为标签、实体或变量，并保留大小写与首尾空白。空格不做任何替换
    （不修剪、不写 &nbsp; 等标记），仍是原 U+0020 字符；其在浏览器
    排版中的保留由 _index_style_block 的预排版样式保证。
    """
    return html.escape(value, quote=True)


def _index_style_block():
    """索引页 <head> 内的本地样式块：只声明字段文字的预排版规则。

    white-space: pre-wrap 让普通空格 U+0020 与 pre 元素一样不参与
    排版折叠：元素首尾以及内部的连续空格都按原数量显示，长字段仍可
    自动换行。规则仅作用于带 INDEX_FIELD_CLASS 类的元素（筛选值段落、
    两类清单的姓名与邮箱单元格），表头与“预览”链接单元格维持普通
    排版。样式随页面内联、不引用任何网络资源；空格保留只靠该 CSS
    规则，输入不修剪，也不替换为 &nbsp; 等可见标记。
    """
    return (
        "<style>\n"
        f".{INDEX_FIELD_CLASS} {{ white-space: pre-wrap; }}\n"
        "</style>\n"
    )


def _index_contact_row(cells, link=None, retained=False):
    """构建清单中的一条 <tr>：姓名、邮箱单元格共用，可选第三列链接。

    cells 为已按 CSV 顺序取好的单元格原文序列（保留清单为姓名、
    邮箱，排除清单同为姓名、邮箱）；逐格转义后包成纯文本单元格，
    并带上预排版类，使姓名、邮箱的首尾及连续空格按原数量显示。
    link 给定时（仅保留清单）追加同目录预览文件名的相对链接单元
    格，文件名同样转义；该单元格维持普通排版、不带预排版类。排除
    清单不传，故无 a 元素、无 mailto。

    retained 为 True 时（仅保留清单的数据行）在 <tr> 上加
    INDEX_RETAINED_ROW_CLASS 类：页内离线脚本只收集带该类的行并按
    嵌入的原始姓名、邮箱控制其 hidden，故排除清单的行永不带此类、
    不参与搜索；“搜索无结果”提示行同样不带类。
    """
    tag = (
        f'<tr class="{INDEX_RETAINED_ROW_CLASS}">'
        if retained
        else "<tr>"
    )
    row = tag + "".join(
        f'<td class="{INDEX_FIELD_CLASS}">{_esc(cell)}</td>'
        for cell in cells
    )
    if link is not None:
        row += f'<td><a href="{_esc(link)}">预览</a></td>'
    row += "</tr>"
    return row


def _index_contact_table(header, rows, empty_message,
                         search_empty_message=None):
    """构建两类联系人清单共享的表格或固定空状态。

    header 为表头单元格文字（两类清单的“姓名”“邮箱”列一致，保留
    清单额外有“预览”列）；rows 为 _index_contact_row 已生成的行
    HTML。无行时不生成表格，返回固定空状态段落；有行时输出与原先
    逐清单手写形式完全一致的 table/thead/tbody 结构与换行。

    search_empty_message 给定时（仅保留清单）在表体末尾追加一个
    初始带 hidden 的提示行，跨全部列显示该文案：它不是数据行
    （不带 INDEX_RETAINED_ROW_CLASS、无字段与链接），仅由页内搜索
    脚本在查询非空且零命中时取消隐藏；查询为空或存在可见行时始终
    隐藏。
    """
    if not rows:
        return f"<p>{empty_message}</p>"
    head = "".join(f"<th>{column}</th>" for column in header)
    body = "\n".join(rows)
    if search_empty_message is not None:
        body += (
            "\n"
            f'<tr id="{INDEX_SEARCH_EMPTY_ID}" hidden>'
            f'<td colspan="{len(header)}">{search_empty_message}</td>'
            "</tr>"
        )
    return (
        "<table>\n"
        f"<thead><tr>{head}</tr></thead>\n"
        "<tbody>\n"
        + body
        + "\n</tbody>\n"
        "</table>"
    )


def _index_search_box(matched_count):
    """构建保留清单上方的离线搜索框及其旁的“当前显示”计数。

    搜索框为一个 label 与无初值文本输入框。输入框不带 value 属性：
    页面第一次打开时查询为空，保留清单显示全部记录。输入框不放在
    form 内，任何键入都不会触发提交或导航。搜索框是否渲染、能否
    输入与保留记录数无关——零命中或全部排除时保留区域只有固定空
    状态段落，搜索框仍照常出现且可输入。

    输入框旁是一个带 INDEX_SEARCH_COUNT_ID 的计数元素，文案为
    “当前显示 n 条，共 m 条”：m 为本次 matched_count（保留记录
    总数），n 为当前可见保留记录数。初始查询为空，n 与 m 相同，
    故此处直接按 matched_count 渲染两个数字；之后的同步更新由页尾
    脚本（_index_search_script）在浏览器本地完成。matched_count
    为零时初始文案即“当前显示 0 条，共 0 条”，且任何查询下都不变。
    """
    return (
        '<div class="contact-search">\n'
        f'<label for="{INDEX_SEARCH_BOX_ID}">搜索姓名或邮箱：</label>\n'
        f'<input type="text" id="{INDEX_SEARCH_BOX_ID}">\n'
        f'<span id="{INDEX_SEARCH_COUNT_ID}">'
        f"当前显示 {matched_count} 条，共 {matched_count} 条"
        "</span>\n"
        "</div>\n"
    )


def _index_search_script(matched):
    """构建页尾内联搜索脚本（无 src、无网络请求），数据岛内嵌原字段。

    参与匹配的原始姓名、邮箱按 CSV 顺序以 JSON 数组嵌入脚本，与表体
    中带 INDEX_RETAINED_ROW_CLASS 的数据行一一平行；搜索只在浏览器
    本地进行，不写任何文件、不发起任何请求。原始字段中的 < 与 > 在
    脚本数据岛内一律写成 U 转义（& 以及 U+2028/U+2029 同样转义，引号
    为 JSON 自带的 \"）：字段里的尖括号、引号、</script> 样式文字与
    {{name}} 都不可能终止脚本块或成为页面元素，浏览器按 JS 字符串
    规则把转义逐字还原为原字段，参与比较的仍是未修剪、未折叠、区分
    大小写的原文。

    过滤规则完全在客户端即时完成：监听输入框的 input 事件，每次输入
    变化直接取 box.value 作为查询（不修剪首尾空白、不折叠连续空格、
    不做大小写归一或任何转义，故任意查询都合法），查询为空时全部保留
    行可见；非空时仅当本行姓名或邮箱原文以 indexOf 包含整个查询字符串
    才可见，其余行加 hidden，查询非空且零命中时显示表体末尾的固定提示
    行。同一趟遍历还累计可见保留行数，并据此改写搜索框旁的计数元素
    （INDEX_SEARCH_COUNT_ID）为“当前显示 n 条，共 m 条”：n 即可见
    保留行数（表头、提示行与排除区域本就不在 rows 中，不计入），m 为
    保留行总数即 matched_count；计数元素始终存在，无需判空。三个计数
    与“已排除的联系人”区域不属于脚本操作对象：排除清单的行不带
    retained-row 类，永不被收集或改写。
    """
    fields = [[contact["name"], contact["email"]] for contact in matched]
    payload = json.dumps(fields, ensure_ascii=False)
    # 此时 payload 是完整合法 JSON：尖括号等字符只会出现在字符串值
    # 内部，把这些原字符替换成等价的 JS/JSON 字符串转义后仍合法；
    # 还原结果与原字段逐字相等。
    payload = (
        payload
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return (
        "<script>\n"
        "(function () {\n"
        '"use strict";\n'
        f"var box = document.getElementById("
        f'"{INDEX_SEARCH_BOX_ID}");\n'
        f"var emptyRow = document.getElementById("
        f'"{INDEX_SEARCH_EMPTY_ID}");\n'
        f"var count = document.getElementById("
        f'"{INDEX_SEARCH_COUNT_ID}");\n'
        "var rows = Array.prototype.slice.call(\n"
        f'document.getElementsByClassName("{INDEX_RETAINED_ROW_CLASS}"));\n'
        f"var fields = {payload};\n"
        "function applyFilter() {\n"
        "  var query = box.value;\n"
        "  var index;\n"
        "  var show;\n"
        "  var visible = 0;\n"
        "  for (index = 0; index < rows.length; index += 1) {\n"
        "    show = query === '' ||\n"
        "      fields[index][0].indexOf(query) !== -1 ||\n"
        "      fields[index][1].indexOf(query) !== -1;\n"
        "    rows[index].hidden = !show;\n"
        "    if (show) {\n"
        "      visible += 1;\n"
        "    }\n"
        "  }\n"
        "  if (emptyRow !== null) {\n"
        "    emptyRow.hidden = query === '' || visible !== 0;\n"
        "  }\n"
        '  count.textContent = "当前显示 " + visible +'
        ' " 条，共 " + rows.length + " 条";\n'
        "}\n"
        'box.addEventListener("input", applyFilter);\n'
        "applyFilter();\n"
        "}());\n"
        "</script>\n"
    )


def _build_index(segment, segment_count, excluded_count, matched, previews,
                 excluded):
    """构建 index.html：声明 UTF-8 的完整离线索引文档。

    页面展示筛选值与分组命中、排除、最终预览三个记录数（与报告同
    源），计数始终描述完整生成结果，不随搜索变化。计数段落之后是
    保留清单的离线搜索框（见 _index_search_box）：初始为空、显示
    全部保留行；用户输入变化时由页尾内联脚本（_index_search_script）
    即时改变保留行的可见性——姓名或邮箱任一原始字段包含整个查询字
    串（区分大小写、不修剪首尾空白、不折叠连续空格）即显示，不匹配
    的行仅加 hidden，非空查询零命中时显示表体末尾的固定提示“没有
    符合搜索条件的联系人”，清空查询恢复全部行。搜索框旁的计数元素
    （_index_search_box）同步显示“当前显示 n 条，共 m 条”：n 为当前
    可见保留记录数（逐条统计，表头、提示行与排除区域不计入），m 为
    本次 matched_count；初始查询为空时两数相同，输入变化时与可见行
    一并即时更新，清空查询恢复全量计数。搜索只操作保留清单：
    三个计数与“已排除的联系人”区域（含全部排除条目）在任何查询下
    都不变；共享邮箱的每条记录在数据岛中各占一项，过滤后仍是独立
    条目，CSV 顺序与同目录相对预览链接不变。

    保留联系人清单按 CSV 顺序列出每条保留记录的原始姓名、邮箱及预览
    链接；其后追加“已排除的联系人”区域，按 CSV 顺序逐条列出命中分组
    后被排除名单（--exclude-email / --exclude-file）移除的记录（与
    报告 excluded_contacts 同内容、同顺序，条目数等于 excluded_count；
    未命中分组的记录不出现，共享邮箱的每条记录各列一项，重复排除值
    不重复增加条目），每条只显示原始姓名与邮箱文字，不提供预览或
    邮件链接；没有排除记录时显示固定空状态。姓名、邮箱的转义与表格/
    空状态拼装由 _index_contact_row 与 _index_contact_table 统一承担，
    两类清单不再各自维护同一套呈现逻辑；它们的差异只在调用处声明：
    保留清单多一个“预览”表头、行上带搜索标记类，并传入同目录预览
    文件名作为第三列相对链接与零命中搜索提示行，排除清单不传链接、
    不带标记类。筛选值段落与两类清单的姓名、邮箱单元格带预排版类，
    配合 head 中的本地样式块（white-space: pre-wrap，见
    _index_style_block）在浏览器排版中按原数量保留首尾与连续的普通
    空格 U+0020；空格不修剪、不替换为 &nbsp; 等可见标记，表头与
    “预览”链接单元格维持普通排版。保留清单中的邮箱仅作文字展示，
    不生成 mailto 等任何非文件链接；链接 href 只写同目录预览文件名，
    输出目录整体移动后仍可打开。matched 为空（零命中或全部排除）时
    保留清单区域不生成表格，固定显示“没有可预览的联系人”且不含任何
    预览链接，搜索框仍可输入，其旁计数始终为“当前显示 0 条，共 0 条”；
    零命中时两个空状态同时出现。文档不
    引用任何网络资源，搜索脚本同样完全内联、无网络请求。
    """
    retained_rows = [
        _index_contact_row(
            (contact["name"], contact["email"]),
            link=preview["file"],
            retained=True,
        )
        for contact, preview in zip(matched, previews)
    ]
    listing = _index_contact_table(
        ("姓名", "邮箱", "预览"),
        retained_rows,
        INDEX_NO_PREVIEW_MESSAGE,
        search_empty_message=INDEX_SEARCH_EMPTY_MESSAGE,
    )

    # 排除区域只放文字：不传链接、不带搜索标记类，故两列均为纯文本
    # 单元格，无 a 元素、无 mailto，搜索脚本也永不触及这些行。
    excluded_rows = [
        _index_contact_row((contact["name"], contact["email"]))
        for contact in excluded
    ]
    excluded_listing = _index_contact_table(
        ("姓名", "邮箱"),
        excluded_rows,
        "没有被排除的联系人",
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>newsletter preview index</title>\n"
        f"{_index_style_block()}"
        "</head>\n"
        "<body>\n"
        "<h1>预览索引</h1>\n"
        f'<p class="{INDEX_FIELD_CLASS}">筛选值：{_esc(segment)}</p>\n'
        f"<p>分组命中：{segment_count}；排除：{excluded_count}；"
        f"最终预览：{len(matched)}</p>\n"
        f"{_index_search_box(len(matched))}"
        f"{listing}\n"
        "<h2>已排除的联系人</h2>\n"
        f"{excluded_listing}\n"
        f"{_index_search_script(matched)}"
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


def _build_manifest(matched, previews):
    """构建离线核对清单 CSV 文本（无 BOM 的 UTF-8，首行固定表头）。

    matched 与 previews 按 CSV 顺序一一平行：后者即 report.json 的
    previews 清单，故本清单每一行对应一条已生成预览的保留联系人记录，
    第四列与报告 previews[i]["file"] 逐条相同（text 格式为 .txt、html
    格式为 .html）。前三列写输入 CSV 的 name、email、segment 原文：
    保留中文、大小写、首尾空白与连续空格；字段中的逗号、双引号与字段
    内换行由 csv 模块按 RFC 4180 加引号/转义，重新解析后逐字恢复原值，
    {{name}} 样式文字同样原样保留。写入经 newline="" 且行结束符固定为
    LF，不产生 BOM，也不做任何平台换行转换；零命中或全部排除时
    matched 为空，文本只含首行表头加一个 LF。
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(MANIFEST_HEADER)
    for contact, preview in zip(matched, previews):
        writer.writerow(
            (
                contact["name"],
                contact["email"],
                contact["segment"],
                preview["file"],
            )
        )
    return buffer.getvalue()


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
        "的原始姓名、邮箱与相对预览链接，并提供一个完全离线的搜索框"
        "（初始为空显示全部；输入即时过滤保留行，姓名或邮箱原文包含"
        "整个查询即显示，区分大小写、不修剪空白；非空查询零命中时"
        "显示固定提示，清空恢复全部；搜索不改变计数与排除区域），"
        "搜索框旁同步显示“当前显示 n 条，共 m 条”（n 为当前可见"
        "保留记录数，m 为最终预览记录数，随查询即时更新，零保留时"
        "恒为 0/0），再"
        "以内容、顺序与报告 excluded_contacts 一致的“已排除的联系人”"
        "区域逐条列出被排除记录（仅文字，无预览或邮件链接，无排除"
        "记录时显示空状态），并显示筛选值及分组命中、排除、最终预览"
        "三个计数；省略时不生成该文件，其余产物逐字节不变",
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="在输出目录额外生成 manifest.csv：无 BOM 的 UTF-8 离线核对"
        "清单，首行固定为 name,email,segment,preview_file，其后每行对应"
        "一条已生成预览的保留联系人（按 CSV 顺序，共享邮箱各占一行）；"
        "前三列保存输入 CSV 对应字段原文（保留中文、大小写、首尾空白与"
        "连续空格，逗号、双引号与字段内换行经 CSV 转义后可恢复原值），"
        "第四列只写同目录预览文件名，与 report.json 的 previews 逐条对应"
        "（text 为 .txt，html 为 .html）；未命中分组或被任一排除来源移除"
        "的记录不进入清单，零命中或全部排除时仅含表头。省略时不生成该"
        "文件，其余产物逐字节不变",
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
        # 核对清单在报告之后、索引页之前写出：未开启 --manifest 时不走
        # 此步，已有预览、报告与索引页与未开启时逐字节一致；写入失败同样
        # 退出 2，已写出的文件保留。清单与报告 previews 同源同顺序。
        if args.manifest:
            _write_file(
                os.path.join(args.out, MANIFEST_FILENAME),
                _build_manifest(matched, previews),
            )
        # 索引页最后写出：未开启 --index 时不走此步，已有预览、报告与
        # 清单与未开启时逐字节一致；写入失败同样退出 2，已写出的文件保留。
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
