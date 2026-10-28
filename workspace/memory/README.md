# Homunculus Memory Vault

This folder is Homunculus's long-term memory. It's a plain folder of
markdown files — open it directly in any editor, or open it as a vault
in [Obsidian](https://obsidian.md) to get graph view, backlinks, and
mobile access via the Obsidian mobile app.

## Layout

```
memory/
  MEMORY.md              # index — every memory in one line, with [[wikilinks]]
  README.md              # this file
  user_<slug>.md         # facts about the user (role, preferences)
  feedback_<slug>.md     # collaboration rules the user has set
  project_<slug>.md      # ongoing work context
  reference_<slug>.md    # pointers to external resources
  logs/YYYY/MM/<date>.md # daily conversation logs (append-only)
```

## Memory types

| Type        | What it captures                                              |
|-------------|---------------------------------------------------------------|
| `user`      | Who the user is, role, preferences, expertise                 |
| `feedback`  | Rules the user has set ("don't do X", "always do Y")          |
| `project`   | Current work, deadlines, in-progress decisions (decays fast)  |
| `reference` | Pointers to external resources (Linear projects, dashboards)  |

Each file has YAML frontmatter (`name`, `description`, `type`, optional
`related`) followed by the memory body. The `related:` field lists
other memory slugs this entry connects to, surfaced as `[[wikilinks]]`
at the bottom of the body so Obsidian's graph picks them up.

## Using as an Obsidian vault

1. Install Obsidian (free, local-first).
2. "Open folder as vault" → point at this `memory/` directory.
3. The graph view will show how memories link via `[[wikilinks]]`.
4. For mobile: enable iCloud / Syncthing / Dropbox sync on this folder,
   then open the same folder in Obsidian mobile.

Obsidian's per-user config (`.obsidian/`) is gitignored — it's safe to
configure locally without polluting the repo.
