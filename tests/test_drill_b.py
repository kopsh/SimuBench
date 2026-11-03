"""#498 演练（一次性，不合并）：断言演练锚文件不存在——与 drill-anchor 分支互斥，
构造「各自绿、合并后红」的自愈重测红灯。"""
from pathlib import Path


def test_drill_anchor_file_is_absent():
    assert not (Path(__file__).parents[1] / "docs" / "drill-anchor.md").exists()
