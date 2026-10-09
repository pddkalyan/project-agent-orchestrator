"""
Control Center Collectors Package.
Provides adapters for normalizing GitHub public activity and local state into structured events.
"""

from control_center.collectors.github_collector import GitHubPublicActivityCollector

__all__ = ["GitHubPublicActivityCollector"]
