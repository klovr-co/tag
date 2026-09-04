from __future__ import annotations

import unittest

from scripts.slack_mrkdwn import to_mrkdwn


class SlackMrkdwnTests(unittest.TestCase):
    def test_converts_common_markdown(self) -> None:
        markdown = "## Update\n**Ready** — [OpenTag](https://example.test)\n~~old~~\n"

        self.assertEqual(
            "*Update*\n*Ready* — <https://example.test|OpenTag>\n~old~\n",
            to_mrkdwn(markdown),
        )

    def test_keeps_fenced_code_literal(self) -> None:
        markdown = "```python\nprint('**literal**')\n```\n"

        self.assertEqual(markdown, to_mrkdwn(markdown))

    def test_keeps_inline_code_literal(self) -> None:
        markdown = "Use `**literal**`, then **bold**."

        self.assertEqual("Use `**literal**`, then *bold*.", to_mrkdwn(markdown))
