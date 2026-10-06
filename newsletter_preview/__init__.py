"""邮件活动离线预览台：读取合成联系人 CSV 与文字模板，按 segment 筛选并生成逐人离线预览。

仅使用 Python 3 标准库；本包不执行任何发送动作。
"""

__all__ = ["main"]

from .cli import main
