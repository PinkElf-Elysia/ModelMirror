"""Windows host bridge for explicitly selected local Coding projects."""

from .windows_helper import HELPER_VERSION, ProjectHostRegistry, inspect_git_project

__all__ = ["HELPER_VERSION", "ProjectHostRegistry", "inspect_git_project"]
