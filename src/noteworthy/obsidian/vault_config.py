"""Write the `.obsidian/app.json` config that makes a freshly-exported vault open correctly.

Obsidian stores per-vault settings in `.obsidian/`. We write only `app.json`, only
when missing, leaving any user-installed plugins, themes, or workspace state alone.
The four keys we set tell Obsidian to: store new attachments in `assets/` (matching
our export), use wikilinks for new links (matching the markdown we emit), prefer
shortest-form wikilinks (matching our globally-unique filenames), and auto-update
links when files are renamed. See obsidian_requirements.md §9.
"""

from __future__ import annotations

import json
import pathlib


__all__ = ["ensure_app_json"]


# The settings written on first run. Kept as a module constant so tests can
# import it if they want and to make the schema visible at a glance.
_APP_JSON_DEFAULTS = {
    "attachmentFolderPath": "assets",
    "newLinkFormat": "shortest",
    "useMarkdownLinks": False,
    "alwaysUpdateLinks": True,
}


def ensure_app_json(target_path: pathlib.Path) -> None:
    """Create `<target>/.obsidian/app.json` if it doesn't already exist.

    Existing app.json files are left untouched (the user may have customized
    them), and other files in `.obsidian/` are never read or modified. When
    the file is already present this function does nothing — including not
    re-creating `.obsidian/` — so the second run leaves zero mtime churn.
    """
    target_path = pathlib.Path(target_path)
    app_json = target_path / ".obsidian" / "app.json"
    if app_json.exists():
        return  # respect whatever the user (or a prior run) put there

    app_json.parent.mkdir(parents=True, exist_ok=True)
    app_json.write_text(json.dumps(_APP_JSON_DEFAULTS, indent=2) + "\n", encoding="utf-8")
