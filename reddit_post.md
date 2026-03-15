# Reddit post — use in r/Joplin and/or r/Obsidian

---

## Version for r/Joplin

**Title:** CLI to sync Joplin ↔ Obsidian (bidirectional, tags, attachments)

**Body:**

I couldn’t choose between Joplin and Obsidian, so I built a small CLI that keeps both in sync.

**What it does:**
- Bidirectional sync (or one-way if you prefer)
- Syncs titles, body, tags, and attachments (images/PDFs)
- Keeps folder structure; you can pick which notebooks sync which way
- Uses a stable ID so notes stay matched even if you rename files

**Install:** `pip install joplin-obsidian-bridge`  
**Run:** `job sync` (or `job sync-manual` for step-by-step)

**⚠️ Risk warning:** Sync can delete notes or create lots of duplicates if used wrong. **Back up both Joplin and your Obsidian vault before the first run.** Use `job sync-manual` to review the plan first; `job check-duplicates` to find duplicates.

You need Joplin’s Web Clipper enabled (for the API) and a small `config.json` (Joplin token + Obsidian vault path). Details and config examples are on GitHub.

GitHub: https://github.com/gorf/joplin-obsidian-bridge  
PyPI: https://pypi.org/project/joplin-obsidian-bridge/

It’s still early (v0.2.x). If you try it and hit issues, I’m happy to fix and improve.

---

## Version for r/Obsidian

**Title:** CLI to sync Obsidian ↔ Joplin (bidirectional, tags, attachments)

**Body:**

I wanted to use both Obsidian and Joplin, so I made a CLI that syncs notes between them.

**What it does:**
- Bidirectional sync (or one-way)
- Syncs content, tags, and attachments (images/PDFs)
- Preserves folder structure; you can choose which folders sync which way
- Uses a stable ID so the same note stays linked even after renames

**Install:** `pip install joplin-obsidian-bridge`  
**Run:** `job sync` (or `job sync-manual` for more control)

**⚠️ Risk warning:** Sync can delete notes or create many duplicates if used incorrectly. **Back up your Obsidian vault and Joplin data before the first sync.** Use `job sync-manual` to review the plan first; `job check-duplicates` to find duplicates.

Setup is a small `config.json` (Obsidian vault path + Joplin API token from Web Clipper). Instructions and examples are on GitHub.

GitHub: https://github.com/gorf/joplin-obsidian-bridge  
PyPI: https://pypi.org/project/joplin-obsidian-bridge/

Still early (v0.2.x). Feedback and bug reports welcome.

---

## Short version (if you prefer one post for both)

**Title:** Joplin ↔ Obsidian sync CLI (bidirectional, tags, attachments)

**Body:**

CLI that keeps Joplin and Obsidian in sync: titles, body, tags, attachments, folder structure. You can do full bidirectional or one-way per notebook/folder.

**⚠️ Back up both Joplin and Obsidian before first run.** Sync can delete or duplicate notes if used wrong; use `job sync-manual` to review the plan first.

`pip install joplin-obsidian-bridge` then `job sync`. Config is a small JSON (Joplin token + Obsidian vault path).  
https://github.com/gorf/joplin-obsidian-bridge

Early stage (v0.2.x), happy to fix issues if you try it.
