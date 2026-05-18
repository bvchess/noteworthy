# Obsidian Export — Requirements

## 1. Context

Noteworthy currently exports Apple Notes as a **faithful backup**: each note lives in its own directory next to a `.noteworthy.json` metadata file, attachments live in a per-note `Attachments/` subdir, smart folders are reproduced as symlink trees, and inter-note links are written as relative markdown paths.

This document specifies a **second export mode** whose goal is to produce a directory that can be opened directly as an [Obsidian](https://obsidian.md) vault with no further user setup. The backup mode is **not being removed or changed**; the Obsidian mode is additive.

The two modes have meaningfully different audiences and shapes, so we treat them as distinct outputs rather than trying to make one tree serve both. Detailed implementation strategy (one writer vs. two, where to branch, CLI surface) is deferred to a follow-up plan once these requirements are agreed.

## 2. Goals

- A user can run `noteworthy` in Obsidian mode against a target directory, open that directory in Obsidian, and immediately have a usable vault: folders match Apple Notes, internal links resolve, attachments display, tags appear, dates show up as Properties.
- The output follows Obsidian's native conventions (wikilinks, frontmatter Properties, single `assets/` folder, `.obsidian/` config) — it should not look like a generic markdown dump.
- The export is **idempotent / re-runnable**: running it again over an existing Obsidian vault target should update only what changed, the same way backup mode does today.
- Round-tripping is supported via `apple_notes_uuid` in frontmatter so re-export can identify existing notes.

## 3. Non-goals

- **Bi-directional sync.** We export Apple Notes → Obsidian, not the reverse. User edits in the vault are not preserved across re-runs (an early decision to revisit only if there's demand).
- **Preserving smart folders.** Apple's smart folders are virtual queries and have no Obsidian equivalent we want to maintain — they are skipped.
- **Preserving the distributed `.noteworthy.json` files.** Obsidian mode does not write them and does not require them on subsequent runs (frontmatter holds what's needed).
- **Reproducing the viewer subsystem.** The standalone Python viewer is a backup-mode feature; Obsidian *is* the viewer for this mode.
- **Bundling community plugins or themes.** We write only what Obsidian needs to recognize the vault, and if Obsidian makes updates (for example by adding plug-ins) we don't overwrite them.

## 4. Vault layout

Single-account case (most users):

```
<target>/
├── .obsidian/
│   └── app.json                   # vault settings (see §9)
├── assets/                        # ALL attachments, flat, globally-unique filenames
│   ├── photo.jpg
│   ├── receipt.pdf
│   └── recording.m4a
├── <Folder>/
│   ├── <Subfolder>/
│   │   └── Note Three.md
│   ├── Note One.md
│   └── Note Two.md
└── Note In Root.md
```

Multi-account case (if Apple Notes has more than one account):

```
<target>/
├── .obsidian/
├── assets/
├── iCloud/
│   └── <Folder>/...
└── On My Mac/
    └── <Folder>/...
```

Key points:

- **Notes are flat `.md` files**, not directories. The current "note-as-directory + Attachments/ subdir" layout is replaced.
- **Folder hierarchy from Apple Notes is preserved** (it's meaningful to users), but smart folders are dropped entirely.
- **One top-level `assets/` directory** holds every attachment from every note. No per-note attachment folders.
- **Account-level dirs only appear when there are multiple accounts.** With a single account (the common case), folders sit directly at the vault root. The account name moves into each note's `account` frontmatter property so it isn't lost.
- **No `.noteworthy.json` files anywhere.** Frontmatter replaces them. (See §7.)
- **No `Deleted/` folder.** Deleted notes are not written to the vault at all.

## 5. Filename uniqueness

Obsidian wikilinks of the form `[[Name]]` resolve unambiguously **only when "Name" is unique across the vault**. Because we want all links written as path-less wikilinks, we make both note filenames and attachment filenames globally unique:

### 5.1 Note filenames

- Start from `_sanitize_name(note.name)` (existing logic in `notes_datatypes.py:12`), extended with Obsidian's forbidden wikilink characters. The chosen replacements use **fullwidth Unicode look-alikes** wherever possible, so the visible filename in Obsidian's sidebar/tab is indistinguishable from the original:

    | Forbidden | Replacement | Codepoint | Rationale |
    | --- | --- | --- | --- |
    | `#` | `＃` | U+FF03 FULLWIDTH NUMBER SIGN | Looks identical, can't be confused with heading-link `#` |
    | `\|` | `｜` | U+FF5C FULLWIDTH VERTICAL LINE | Looks identical, can't be confused with alias-pipe `\|` |
    | `^` | `＾` | U+FF3E FULLWIDTH CIRCUMFLEX | Looks identical, can't be confused with block-ref `^` |
    | `[` | `［` | U+FF3B FULLWIDTH LEFT SQUARE BRACKET | Looks identical, can't terminate a wikilink |
    | `]` | `］` | U+FF3D FULLWIDTH RIGHT SQUARE BRACKET | same |

    The existing replacements (`/`→`_`, `:`→`-`, `"`→curly, tab→space, control-char percent-encode) carry over. The fullwidth approach is better than ad-hoc choices like `#`→`-` because it (a) preserves visual identity and (b) round-trips: if we ever need to recover the original name from the filename, the mapping is unambiguous.

- After sanitization, group all notes vault-wide by **case-insensitive** filename. For groups of size > 1, disambiguate deterministically (sort by `(creation_date, id)`, keep the first as-is, suffix the rest with ` (2)`, ` (3)`, …). Space + parens chosen because it reads naturally as an Obsidian display name and stays out of the way of wikilinks.
- **Empty after sanitization.** If a note's name consists entirely of characters that map to empty (or is empty to begin with), fall back to the literal name `Untitled`. The same global-uniqueness pass then disambiguates: `Untitled.md`, `Untitled (2).md`, etc.
- **Always** record the original (un-sanitized, un-disambiguated) display name as an entry in the `aliases` frontmatter list when it differs from the filename. This covers disambiguation suffixes, forbidden-character replacements, and the `Untitled` fallback, so users can still find the note by its real name in the quick switcher.

### 5.2 Attachment filenames

- Same logic, flat namespace inside `assets/`. Extension preserved; collision suffix goes before the extension: `photo.jpg`, `photo (2).jpg`.
- Reuse the existing per-note collision logic in `markdown_renderer.py:_make_unique_filename` (line 885), generalized to a vault-wide name set.

### 5.3 Why not "shortest path" resolution?

Obsidian *can* disambiguate `[[Folder/Name]]` when names collide, but that defeats the user's stated goal ("since names will be unique, paths are not required"). Global uniqueness is the simpler contract and produces cleaner-looking notes.

## 6. Markdown dialect

| Construct | Backup mode (today) | Obsidian mode |
| --- | --- | --- |
| Inter-note link | `[Name](../Folder/Note/Note.md)` | `[[Note]]` (or `[[Note\|Display]]` if display ≠ filename) |
| Image attachment | `![title](Attachments/photo.jpg)` | `![[photo.jpg]]` (embed) |
| Non-image attachment | `[title](Attachments/file.pdf)` | `[[file.pdf]]` (link, never embed) |
| External URL | `[text](https://…)` | unchanged |
| Hard line break | two trailing spaces + `\n` | unchanged (works in Obsidian regardless of Strict-line-breaks setting) |
| Tables / lists / formatting | unchanged | unchanged |
| Checklists | `- [ ]` / `- [x]` (GFM) | unchanged (Obsidian renders these natively) |

Notes on the dialect choice:

- We default to **wikilinks, not markdown links**, because Obsidian's native UI, link autocompletion, and Properties handling assume them. The `.obsidian/app.json` we write makes this explicit (see §9).
- **Embed vs. link is decided by extension, not by attachment role.** Image extensions get `![[…]]`; everything else (PDF, audio, video, generic files) gets `[[…]]`. The image set follows Obsidian's native image list from the [file-formats reference](https://obsidian.md/help/file-formats): `.avif .bmp .gif .jpeg .jpg .png .svg .webp`. Comparison is case-insensitive.
- **Inter-note links to targets not in the export** (deleted target, locked-and-skipped target, smart-folder-only target) are still emitted as `[[Target Name]]`. Obsidian renders unresolved wikilinks in a distinct color, surfacing the gap to the user instead of silently flattening it to plain text.
- The existing renderer's link emission at `markdown_renderer.py:990` and `:1059` is where the dialect branches.

### 6.1 Note title (drop the first line)

In Apple Notes the **first line of each note is its title** — the same string that Apple stores as the note's name and that Noteworthy uses to derive the filename. The backup-mode renderer emits this line into the markdown body as ordinary content, which is fine when the file is read on its own.

In Obsidian the title comes from the **filename**, not from the file's body, so emitting the title line again produces a visible duplicate (a big bold line at the top of the note that repeats the file name in the tab and sidebar). Obsidian mode must therefore **strip the title from the body** before writing the file.

Rules:

- Drop the first non-empty content block whose plain text matches the note's name. Comparison is case-insensitive and ignores **both inline and block formatting** — so a body that starts with `# My Plan`, `**My Plan**`, or just `My Plan` all match a note named "My Plan" and all get stripped.
- If the first content block is empty or whitespace, skip past it before testing.
- If the first content block does **not** match the note's name (rare, but possible if a user manually edited the body without renaming the note), leave the body alone — we don't silently delete unrelated content.
- The stripped line is replaced with nothing, not a blank line, so the body starts cleanly at the next real content block.

### 6.2 No raw HTML in the output

The current backup-mode renderer can fall back to inline HTML in some situations (e.g., `<span>` tags for inline styling that has no direct markdown equivalent). Obsidian *does* render inline HTML in Reading view, but:

- It looks out of place in Live Preview / Source mode (visible angle-bracket markup in the editor).
- It breaks Obsidian features that rely on parsing markdown structure (link backlinks, search highlighting, outline view).
- It conflicts with the "plain markdown vault" promise users expect of an Obsidian export.

Obsidian mode must **never emit raw HTML**. The policy for handling source formatting:

1. **If there's a clean markdown equivalent, translate.** Examples:
   - Underline → `==highlight==` (Obsidian's mark/highlight syntax — closest visual analog).
   - Strikethrough → `~~text~~` (GFM, native in Obsidian).
   - Inline monospace → `` `text` `` if not already.
   - Block-level pre/code → fenced code blocks.
2. **Otherwise, drop the formatting and keep the text.** Colored text, font-family changes, font-size changes, and similar styling for which Obsidian has no clean syntax all lose the styling; the underlying text is preserved as plain runs.
3. **Never `<span>`, `<u>`, `<font>`, `<br>`, etc.** Text is more important than visual fidelity.

This rule applies to the renderer's output, not to content the user themselves wrote into a note (Apple Notes' editor doesn't expose raw HTML, so this is rarely relevant in practice).

## 7. Frontmatter (Properties)

Every exported note begins with a YAML frontmatter block. Obsidian's [Properties](https://obsidian.md/help/properties) interprets these as typed fields visible in the right-hand UI panel.

```yaml
---
aliases:
  - Original Note Name
tags:
  - work
  - meetings/standup
created: 2024-08-21T10:30:00
modified: 2025-03-14T09:12:00
account: iCloud
folder: Work/Projects/Acme
apple_notes_uuid: 5C7F1A28-DEAD-BEEF-9999-1234567890AB
---
```

Field-by-field mapping from the current `Note.to_metadata_dict()` (`notes_datatypes.py:152`):

| Frontmatter property | Type | Source | Notes |
| --- | --- | --- | --- |
| `aliases` | List | Disambiguation (§5.1); also original `name` when sanitization mangled it | Omit if no aliases needed |
| `tags` | Tags (List) | `Note.tags` | Apple Notes already stores lowercase, no `#`. Sanitize for Obsidian: replace spaces with `-`, drop characters outside `[a-z0-9_/\-]`. Slashes already mean nested in both worlds. |
| `created` | Date & Time | `Note.creation_date` converted to **local time** and emitted as naive ISO 8601 (`YYYY-MM-DDTHH:MM:SS`, no offset, no `Z`) | Obsidian's Date & Time property displays in local time; emitting in the same form avoids visible drift |
| `modified` | Date & Time | `Note.modification_date`, same handling as `created` | |
| `account` | Text | Owning account's `name` | Useful for filtering by source |
| `folder` | Text | Slash-joined folder path within the account | Single string, not a list — Obsidian's text type renders cleanly; mirrors `Folder.full_name` |
| `apple_notes_uuid` | Text | `Note._uuid` (`ZIDENTIFIER`) | Round-trip key for re-export |

Not included from current metadata: `id` (Core Data URI; opaque, of no Obsidian value), per-folder `sort_order`/`display_order`/`is_expanded` (Apple-specific UI state), `folders` membership list (folder is implicit from path, smart folders are dropped).

Frontmatter is emitted with stable key ordering and the same `ensure_ascii=False`-equivalent UTF-8 handling as today, so re-runs produce minimal diffs.

## 8. Tag handling

- Apple Notes tags arrive lowercase, no `#`, as plain strings (`Note.tags`).
- Obsidian-compatible tags per [Tags docs](https://obsidian.md/help/tags) allow `[a-zA-Z0-9_\-/]` plus Unicode and emoji, must include at least one non-numeric character, and **cannot contain spaces**.
- Sanitization on the way out: replace runs of whitespace with `-`, drop any remaining illegal ASCII punctuation, and skip entirely if the result is empty or all-numeric.
- Slashes in source tags pass through unchanged and become Obsidian nested tags.
- Written as a YAML list under `tags:` in frontmatter (the only format Obsidian's Tags type accepts).

## 9. `.obsidian/` config

We write a minimal `.obsidian/app.json` so the vault behaves correctly on first open:

```json
{
  "attachmentFolderPath": "assets",
  "newLinkFormat": "shortest",
  "useMarkdownLinks": false,
  "alwaysUpdateLinks": true
}
```

- `attachmentFolderPath: "assets"` — new attachments the user adds in Obsidian go to the same place ours do.
- `useMarkdownLinks: false` — wikilinks are the default for new links.
- `newLinkFormat: "shortest"` — matches our path-less wikilinks.
- `alwaysUpdateLinks: true` — if the user later renames a note in Obsidian, links update.

On re-export we leave any existing `.obsidian/` contents alone (user may have installed plugins, themes, etc.); only `app.json` is created if missing. We do **not** rewrite it on subsequent runs.

## 10. Excluded content

Three categories of source content are deliberately omitted from the Obsidian vault:

- **Smart folders.** Skipped entirely. No symlinks, no saved-search files, no markers. The user explicitly stated virtual folders should be ignored, and Obsidian has no first-class equivalent. If we add support later, the natural replacement is a generated "saved search" note containing a `query` block — left as a future enhancement.
- **Locked / password-protected notes.** Skipped, but **with a warning emitted to stderr** per note (one line including the note's name and account so the user can find it in Apple Notes if they want to unlock and re-export). Behavior matches whatever backup mode does today for unreadable notes; the only Obsidian-specific change is making sure the warning is visible. Wikilinks from other notes pointing to a skipped target follow the unresolved-link rule in §6.
- **Deleted notes.** Skipped entirely. No `Deleted/` folder in the vault (unlike backup mode).

Apple-side metadata that has no Obsidian counterpart — pinned status, folder sort-order, folder display-order, sidebar expansion state — is dropped silently. Obsidian sorts files alphabetically; users who want a specific order can rename or use a community sorting plugin.

## 11. Re-export behavior and target-directory safety

### 11.1 Target-directory inspection

Before doing any work, the exporter inspects the target directory and classifies it:

| Target state | Detection signal | Behavior |
| --- | --- | --- |
| Empty / doesn't exist | no files | Create vault from scratch in the requested mode. |
| Obsidian vault | `.obsidian/` directory present | If `--obsidian` was passed → idempotent re-export. If not → **error and exit** with a message: "Target looks like an Obsidian vault but `--obsidian` was not specified. Re-run with `--obsidian`, or choose a different target." |
| Backup-mode export | `.noteworthy.json` files present at any depth and no `.obsidian/` | If `--obsidian` was passed → **error and exit** with a message: "Target contains a backup-mode export. `--obsidian` would corrupt it. Re-run without `--obsidian`, or choose a different target." If not → idempotent re-export (existing behavior). |
| Unrelated non-empty dir | neither signal, but files exist | Same conservative refusal in both modes — error and exit unless an explicit override flag (out of scope for v1). |

The single guiding principle: **a forgotten or wrong mode flag must never silently destroy or corrupt an existing export.** Detection happens before any write, and errors are clear about how to fix the invocation.

### 11.2 Re-export semantics (Obsidian mode)

- Per-note identity is established by `apple_notes_uuid` in frontmatter (scan all `.md` files, build a uuid → path map, mirroring the role `read_distributed_metadata` plays today).
- A note whose UUID is no longer in Apple Notes: leave in place by default (the user may have edited it); offer a `--prune` flag in a later iteration.
- A note that moved folders in Apple Notes: relocate the `.md` file in the vault. Wikilinks keep working because they're path-less. Attachments stay in `assets/`.
- A note whose Apple Notes name changed: rename the `.md` file; add the previous name to `aliases` so old wikilinks still resolve.
- An attachment no longer referenced by any exported note: leave in place (same rationale — user may have started referencing it manually). Future `--prune` may clean these up.
- **User-added frontmatter** on an existing note is preserved on re-export: we update only the keys we own (the schema in §7) and leave any extra keys the user added in place.

## 12. Decisions

Choices made during requirements review, recorded here for the implementation plan to reference:

1. **Deleted notes** — skipped entirely. No `Deleted/` folder in the vault.
2. **Single-account flattening** — when only one account exists, folders sit at the vault root (no `<Account>/` wrapper). The account name is preserved in each note's `account` frontmatter property. Assumes the user won't add accounts later.
3. **`folder` frontmatter property** — included on every note (slash-joined path within the account).
4. **`apple_notes_uuid`** — always written.
5. **CLI surface** — flag form: `noteworthy <target> --obsidian`. Target-directory detection (§11.1) protects against forgotten or wrong flags.
6. **Forbidden-character sanitization** — fullwidth Unicode look-alikes for `# | ^ [ ]` (see table in §5.1). Better than ad-hoc punctuation swaps because it preserves visual identity and is round-trippable.
7. **Attachment embedding** — decided by file extension, not by attachment role. Image extensions (`.avif .bmp .gif .jpeg .jpg .png .svg .webp`) get `![[…]]` embeds; everything else (PDF, audio, video, generic files) gets `[[…]]` links.
8. **Aliases** — original display name is always added to `aliases` when it differs from the on-disk filename (covers both sanitization changes and disambiguation suffixes).
9. **Formatting without a markdown equivalent** — translate when there's a clean match (underline → `==highlight==`, strikethrough → `~~text~~`, etc.); otherwise drop the styling and preserve the text in plain form. Never emit raw HTML.

## 13. Verification

Once implemented, the export should be verified end-to-end by:

1. **Functional smoke test.** Run the exporter against a small representative test backup. Open the target dir in Obsidian. Confirm: notes appear in correct folders; clicking a wikilink navigates; image embeds render; PDF link opens; Properties panel shows frontmatter values; tag pane lists tags; no "unresolved link" warnings.
2. **Re-export idempotence.** Run twice in a row; second run should produce no file modifications (verify with `git diff` if target is a git repo).
3. **Unit tests.**
   - New test module mirroring `tests/test_export.py` that asserts wikilink output, frontmatter content, attachment paths, and tag sanitization against the existing protobuf fixtures.
   - New sync-style tests mirroring `tests/test_sync.py` that verify directory layout, `.obsidian/app.json` contents, global filename uniqueness, smart-folder skipping, and rename/move behavior on re-export.
4. **Real-vault test.** Export the developer's own Apple Notes to a scratch vault, open in Obsidian, spot-check linked notes that previously used the markdown-link form.

---

*Once this document is reviewed, a follow-up implementation plan will cover code changes: dialect-selectable renderer, new layout writer, CLI wiring with target-directory detection, and test scaffolding.*
