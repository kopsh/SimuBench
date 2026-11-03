"""#498 演练（一次性，不合并）：断言锚文件内容仍是 break——Y1（改为 break2）合入后，
本测试在自愈重测中变红。"""

from pathlib import Path


def test_drill_anchor_content_unchanged():
    assert (Path(__file__).parents[1] / "docs" / "drill-anchor.md").read_text(encoding="utf-8") == "break\n"
