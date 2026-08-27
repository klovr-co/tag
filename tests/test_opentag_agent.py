from __future__ import annotations

import os
import unittest
from pathlib import Path

from scripts import opentag_agent


class OpenTagAgentPromptTests(unittest.TestCase):
    def test_prompt_treats_gws_as_a_normal_local_tool(self) -> None:
        previous_transport = os.environ.get("OPENTAG_TRANSPORT")
        os.environ["OPENTAG_TRANSPORT"] = "slack"
        try:
            prompt = opentag_agent.build_prompt(
                skill_dir=Path("/tmp/open-tag"),
                workdir=Path("/tmp/workspace"),
                memory_root=Path("/tmp/memory"),
                channel_id="C123",
                question="Check my email",
                thread_text="",
                attachments_dir=None,
                allowed_scopes="file://local/tmp/workspace",
            )
        finally:
            if previous_transport is None:
                del os.environ["OPENTAG_TRANSPORT"]
            else:
                os.environ["OPENTAG_TRANSPORT"] = previous_transport

        self.assertIn("This includes `gws` when it is installed and authenticated.", prompt)
        self.assertIn("not add per-tool feature flags or caller allowlists.", prompt)
        self.assertNotIn("OPENTAG_GWS_ENABLED", prompt)
        self.assertNotIn("OPENTAG_GWS_ALLOWED_CALLERS", prompt)
        self.assertNotIn("read-only Gmail operations", prompt)
