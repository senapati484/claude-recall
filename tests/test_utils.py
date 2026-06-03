import sys
import unittest
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from utils import cwd_to_slug


class CwdToSlugTests(unittest.TestCase):
    def test_posix_project_root_slug(self):
        self.assertEqual(cwd_to_slug(Path("/home/user/projects/setu")), "setu")

    def test_nested_workspace_slug_uses_last_two_meaningful_segments(self):
        self.assertEqual(
            cwd_to_slug(Path("/home/user/work/client/acme/dashboard")),
            "acme-dashboard",
        )

    def test_wsl_path_strips_mount_and_user_prefix(self):
        self.assertEqual(
            cwd_to_slug(Path("/mnt/c/Users/Sayan/repos/my-app")),
            "my-app",
        )

    def test_windows_path_is_normalized_without_host_os_support(self):
        self.assertEqual(
            cwd_to_slug(PureWindowsPath(r"C:\Users\Sayan\repos\my-app")),
            "my-app",
        )

    def test_unicode_spaces_and_dots_are_slugified(self):
        self.assertEqual(
            cwd_to_slug(Path("/home/user/projects/Cafe/uber tool.v2")),
            "cafe-uber-tool-v2",
        )

    def test_noise_only_paths_fall_back_to_unknown_project(self):
        self.assertEqual(
            cwd_to_slug(Path("/home/user/projects/workspace/dev")),
            "unknown-project",
        )


if __name__ == "__main__":
    unittest.main()
