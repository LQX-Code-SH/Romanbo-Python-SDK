"""文档里写的「当前状态」数字必须与代码一致。

2026-09-27 一天之内，「测试项数」在 README 与 `docs/TESTING.md` 里过期了三回
（188 → 201 → 236 → 237）：每加一批用例，文档里的数字就悄悄变成假的，而没人会
在改代码时想起去改文档。与其靠记性，不如直接数出来对比——本文件就是干这个的。

只检查**描述当前状态**的文档；历史记录（`docs/TEST_REPORTS.md`、
`docs/SERVO_TEST_PLAN.md` 里的执行结果）写的是当时的数字，**不该**跟着变。
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 需要与代码同步的文档（相对仓库根）
CURRENT_STATE_DOCS = ("README.md", "docs/TESTING.md")
#: 形如「237 项单元测试」
COUNT_PATTERN = re.compile(r"(\d+)\s*项单元测试")


class TestDocumentedCounts(unittest.TestCase):
    """文档里的用例总数要等于实际数量。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = pathlib.Path(__file__).resolve().parent.parent
        loader = unittest.TestLoader()
        cls.actual = loader.discover(str(cls.root / "tests"),
                                     top_level_dir=str(cls.root)).countTestCases()

    def test_documented_count_matches_the_suite(self) -> None:
        checked = 0
        for rel in CURRENT_STATE_DOCS:
            text = (self.root / rel).read_text(encoding="utf-8")
            found = COUNT_PATTERN.findall(text)
            for number in found:
                with self.subTest(doc=rel, documented=number):
                    self.assertEqual(
                        int(number), self.actual,
                        f"{rel} 写的是 {number} 项，实际有 {self.actual} 项——"
                        f"改了用例记得同步文档（本测试就是为了拦住这件事）")
                checked += 1
        self.assertGreater(checked, 0, "一个计数都没找到？检查 COUNT_PATTERN 与文档")

    def test_history_docs_are_not_counted(self) -> None:
        """历史记录里的数字**不该**被同步——那是当时的执行结果。"""
        for rel in ("docs/TEST_REPORTS.md", "docs/SERVO_TEST_PLAN.md"):
            text = (self.root / rel).read_text(encoding="utf-8")
            for number in COUNT_PATTERN.findall(text):
                self.assertNotEqual(
                    int(number), self.actual,
                    f"{rel} 是历史记录，不该出现当前数量 {self.actual}——"
                    f"要么把它挪进当前状态文档，要么保留当时的数字")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
