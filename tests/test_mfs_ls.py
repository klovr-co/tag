from __future__ import annotations

import unittest

from scripts import mfs_ls


class MfsListScopeTests(unittest.TestCase):
    def test_allows_paths_below_configured_scope(self) -> None:
        scopes = ["slack://team-memory"]
        self.assertTrue(mfs_ls.is_path_allowed("slack://team-memory/channels", scopes))

    def test_rejects_similar_but_different_scope(self) -> None:
        scopes = ["slack://team-memory"]
        self.assertFalse(mfs_ls.is_path_allowed("slack://team-memory-copy/channels", scopes))

    def test_rejects_parent_traversal_outside_scope(self) -> None:
        scopes = ["file://local/repo/allowed"]
        self.assertFalse(
            mfs_ls.is_path_allowed("file://local/repo/allowed/../secret", scopes)
        )

    def test_rejects_percent_encoded_parent_traversal(self) -> None:
        scopes = ["file://local/repo/allowed"]
        self.assertFalse(
            mfs_ls.is_path_allowed("file://local/repo/allowed/%2e%2e/secret", scopes)
        )


if __name__ == "__main__":
    unittest.main()
