# Joplin-Obsidian Bridge

[English](README.md) | [简体中文](https://github.com/gorf/joplin-obsidian-bridge/blob/main/README_CN.md)

---

Just like the old saying "eating at one house, sleeping at another," I can't give up the features of either Joplin or Obsidian, so I created this CLI (**job**) to sync notes between the two. Install from PyPI as `joplin-obsidian-bridge`; run with the `job` command.

---

> **⚠️ Risks and backup**  
> Sync can delete notes (e.g. when treating a side as "source of truth") and can create duplicates if rules or timing are wrong. **Back up both your Joplin data and Obsidian vault before the first run and before major syncs.** Prefer `job sync-manual` and read the plan before using `--force`. Use `job check-duplicates` to spot duplicates.

---

## Tool Introduction

Joplin-Obsidian Bridge lets you sync note content between Joplin and Obsidian on Windows, including:
- Titles
- Body text
- Tags
- Attachments (images, PDFs, etc.)
- Folder structure
- Selective sync support (certain notebooks can be synced one-way or skipped)
- Sync direction control (bidirectional, unidirectional)
- **Reliable ID-based identification**: Uses `notebridge_id` to ensure note consistency, unaffected by filename changes

You just need to enter one command in the terminal, and the tool will automatically keep both sides in sync.

---

## Installation

### Option 1: From PyPI (recommended)

1. Install Python (3.8+ recommended).
2. Run: `pip install joplin-obsidian-bridge` ([PyPI](https://pypi.org/project/joplin-obsidian-bridge/))
3. Put `config.json` in the **current working directory**, or in:
   - Windows: `%APPDATA%\notebridge\config.json`
   - macOS/Linux: `~/.config/notebridge/config.json`
4. Run: `job sync` or `python -m notebridge sync`  
   **Back up Joplin and Obsidian first.** Prefer `job sync-manual` to review the plan before applying changes.

### Option 2: From source

1. Install Python (3.8+ recommended).
2. Clone the repo and run: `pip install -r requirements.txt`
3. Put `config.json` in the project folder and run: `python notebridge.py sync`

---

## Configuration

Before first use, simple configuration is required:

1. **Joplin Settings**
   - Open Joplin, go to Settings → Web Clipper → Enable Web Clipper service.
   - Note down the "Port" and "Token".
2. **Obsidian Settings**
   - Find your Obsidian vault folder path.
3. **Create Config File**
   - Create `config.json` in the **current working directory** or the path above, with example content:
     ```json
     {
       "joplin": {
         "api_base": "http://127.0.0.1:41184",
         "token": "your_joplin_token"
       },
       "obsidian": {
         "vault_path": "C:/Users/your_username/Obsidian_vault_path"
       },
       "sync_rules": {
         "joplin_to_obsidian_only": ["Work Notes", "Temp Notes"],
         "obsidian_to_joplin_only": ["Personal Diary"],
         "skip_sync": ["Drafts", "Test"],
         "bidirectional": ["Study Notes", "Project Docs"]
       }
     }
     ```

---

## Usage

Commands below use `job` (after pip install). From source, replace `job` with `python notebridge.py`.

**Before syncing:** Back up both Joplin and your Obsidian vault. Sync may delete or duplicate notes if used incorrectly.

### Basic Sync Commands

```bash
# Preview sync plan (no actual sync)
job sync

# Execute bidirectional sync
job sync --force

# Sync only from Joplin to Obsidian
job sync --force --joplin-to-obsidian

# Sync only from Obsidian to Joplin
job sync --force --obsidian-to-joplin

# Manual confirmation mode sync (recommended, prevents duplicate headers)
job sync-manual

# Manual confirmation one-way sync
job sync-manual --joplin-to-obsidian
job sync-manual --obsidian-to-joplin
```

### Additional Feature Commands

```bash
# Check for duplicate notes (ultra-fast version, greatly improved performance)
job check-duplicates

# Quick title similarity check (recommended, manual decision)
job quick-title-check

# Clean Joplin imports from Obsidian
job clean-joplin-imports

# Performance test comparison (new vs old algorithm)
job test-duplicates

# Run tests (tags & attachments sync logic, no real Joplin/Obsidian needed)
python -m unittest tests.test_tags_and_attachments -v

# Interactive duplicate cleaning (recommended)
job interactive-clean

# Auto-clean duplicate notes and sync IDs
job clean-duplicates

# Fix missing attachments in Obsidian
job fix-attachments
```

### Sync Mode Explanation

#### Automatic Sync Mode
- **Bidirectional sync** (default): Mutual sync between Joplin and Obsidian
- **Joplin → Obsidian**: Only sync from Joplin to Obsidian, suitable for initial import
- **Obsidian → Joplin**: Only sync from Obsidian to Joplin, suitable for backup

#### Manual Confirmation Mode (Recommended)
**Why is manual confirmation mode recommended?**
- ✅ **Prevents duplicate headers**: Automatically checks and fixes duplicate sync info headers before each sync
- ✅ **Prevents reverse sync**: Automatically detects note source, avoids syncing notes back to their origin (e.g., Obsidian → Joplin → Obsidian)
- ✅ **Intelligent deletion detection**: Uses sync time and cached state to automatically determine if a note was deleted or is new
- ✅ **Enhanced deletion handling**: Provides options to delete locally or copy to the other side
- ✅ **Complete control**: Shows details for each note before sync, you decide whether to sync
- ✅ **Safe and reliable**: Can view note content, sync status, duplicate headers, note source, etc. anytime
- ✅ **Flexible operations**: Supports skip single, skip all, exit, etc.

**Use Cases:**
- Recommended for first-time sync
- First sync after resolving duplicate header issues
- When uncertain which notes need syncing
- When careful inspection of each note is needed

### Selective Sync Configuration

In `config.json`, you can configure sync rules for different notebooks (supports wildcard pattern matching):

- `joplin_to_obsidian_only`: Only sync from Joplin to Obsidian
- `obsidian_to_joplin_only`: Only sync from Obsidian to Joplin  
- `skip_sync`: Skip sync
- `bidirectional`: Bidirectional sync (default)

#### Wildcard Support

All sync rules support wildcard pattern matching:

- `*` matches any number of characters, e.g., `"Conflict*"` matches all notebooks starting with Conflict
- `?` matches a single character, e.g., `"Test?"` matches "Test1", "Test2", etc.

Example configuration:
```json
{
  "sync_rules": {
    "joplin_to_obsidian_only": ["Work Notes", "Project*"],
    "obsidian_to_joplin_only": ["Personal Diary", "Backup*"],
    "skip_sync": ["Conflict*", "Temp*", "Draft*"],
    "bidirectional": ["Important*", "Study*"]
  }
}
```

### Smart Duplicate Detection and Cleaning

#### Quick Title Similarity Check (Recommended)
```bash
job quick-title-check
```
- ⚡ **Blazing fast**: Only checks title similarity, extremely fast
- 🎯 **Manual decision**: You have complete control over what's duplicate
- 📝 **Content preview**: Shows note content preview for easy judgment
- 🔧 **Adjustable threshold**: Can set similarity threshold (70%-90%)
- 📊 **Detailed comparison**: Can view full content comparison
- 🛡️ **Safe confirmation**: Requires confirmation before deletion, prevents accidental deletion

#### Clean Joplin Imports (Recommended)
```bash
job clean-joplin-imports
```
- 🔍 **Smart detection**: Automatically identifies notes from Joplin in Obsidian
- 📊 **Status analysis**: Distinguishes unmodified, modified, and orphaned notes
- 🎯 **Flexible selection**: Can choose to delete all, only unmodified, or only orphaned
- 🛡️ **Safe operation**: Requires confirmation before deletion, prevents accidental deletion
- 💡 **Re-sync**: Can re-sync after cleaning to avoid duplicates

#### Ultra-fast Duplicate Check (Fully Automatic)
```bash
job check-duplicates
```
- 🚀 **Layered detection algorithm**: Uses 5-layer detection strategy, 3-5x performance improvement
- 🔍 **Smart content preprocessing**: More thorough removal of headers, markdown syntax, HTML tags, etc.
- 💾 **Caching mechanism**: Avoids redundant calculations, greatly improves detection speed
- 🎯 **Advanced similarity calculation**: Specifically handles "same content after removing headers" cases
- 📊 **Detailed statistics report**: Provides performance stats, duplicate rate analysis, etc.
- 🔧 **Multiple duplicate type detection**: ID duplicates, content hash duplicates, title similarity, content similarity, post-header-removal duplicates

#### Performance Testing
```bash
job test-duplicates
```
- Compare new vs old algorithm performance
- Show detection result differences
- Provide performance improvement multiplier

#### Interactive Cleaning (Recommended)
```bash
job interactive-clean
```
- Smart duplicate note detection
- Multiple cleaning strategy options
- Content comparison preview support
- Confirm each deletion individually, safe and reliable

#### Auto Cleaning
```bash
job clean-duplicates
```
- Auto-clean duplicate sync IDs in all notes
- Auto-find and delete duplicate notes
- Ensure clean, conflict-free note library

---

## FAQ

- **Q: Will content be lost during sync?**
  A: The tool tries its best to avoid content loss. If both sides modify the same note simultaneously, the newest version will be kept.
- **Q: What content is supported for sync?**
  A: Supports titles, body text, tags, attachments, folder structure, etc.
- **Q: Do I need to keep Joplin running?**
  A: Yes, and the Web Clipper service must be enabled.
- **Q: How are sync conflicts handled?**
  A: The tool automatically selects the newest version based on timestamps, avoiding manual conflict resolution.
- **Q: Can I sync only some notes?**
  A: Yes, by configuring `sync_rules` you can precisely control which notebooks sync and how.
- **Q: Will manual file deletion during program execution cause errors?**
  A: No, the program has optimized error handling to automatically skip non-existent files and continue.
- **Q: What if I encounter permission issues?**
  A: The program automatically detects permission errors and skips problematic files without interrupting the entire sync process.
- **Q: What improvements does the new duplicate detection algorithm have?**
  A: The new algorithm uses a 5-layer detection strategy with 3-5x performance improvement, more accurately detecting "same content after removing headers" duplicates.
- **Q: What if duplicate checking is too slow?**
  A: The new version has greatly optimized performance using caching and layered detection with significant speed improvements. If still slow, run `job test-duplicates` to view performance comparison.
- **Q: How to detect "same content after removing headers" duplicates?**
  A: The new algorithm specifically adds a 5th layer detection using advanced similarity calculation to accurately identify such duplicates.
- **Q: What if one-way sync rules don't take effect?**
  A: The latest version has fixed one-way sync rule filtering issues. The program now correctly checks sync rules for each note, ensuring only allowed direction notes are synced. If issues persist, check sync rule settings in config file.

---

## Latest Updates

### v1.4.0 - Intelligent Deletion Detection and Sync Time Updates
- ✅ **Smart deletion vs new note detection**
  - Automatically determines via sync time records
  - Uses sync state cache (sync_state.json) to check if both sides existed in last sync
  - Shows detailed judgment basis (sync time, last state, source)
  - Dynamically identifies deletion candidates and adds to deletion list
- ✅ **Enhanced deletion handling options**
  - Delete locally: Keep in sync with other side (real deletion)
  - Copy to other side: Restore note to other side (accidental deletion or re-need)
  - Skip: Don't handle for now
  - Shows confidence and judgment basis
- ✅ **Fix sync time updates on re-sync**
  - sync_joplin_to_obsidian: Updates sync time on re-sync
  - sync_obsidian_to_joplin_with_notebook_id: Updates sync time on re-sync
  - Bidirectional sync updates: Updates sync time on both sides
  - Smart bidirectional sync updates: Updates sync time on both sides
  - Manual confirmation sync updates: Updates sync time on both sides
- ✅ **Improved duplicate header handling**
  - Prioritize headers with sync time
  - Preserve complete sync info (id, time, source, version)
  - Handle empty values to avoid losing valid info
  - Fix sync info being incorrectly cleaned

### v1.3.0 - Manual Confirmation Mode, Complete Solution for Duplicate Headers and Reverse Sync
- ✅ **New manual confirmation sync mode**: Human confirmation required before each note sync, complete control
- ✅ **Fixed sync rule checking in manual confirmation mode**: Manual mode now strictly follows configured sync rules
  - Matched note pairs: Check if specified direction sync is allowed
  - New notes: Check if notebook/folder sync is allowed
  - Auto-skip non-compliant notes with reason shown
- ✅ **Smart reverse sync prevention**: Automatically detects note source, avoids syncing unmodified notes back to origin
  - Note from Obsidian → Joplin, if unmodified in Joplin, won't sync back to Obsidian
  - Note from Joplin → Obsidian, if unmodified in Obsidian, won't sync back to Joplin
  - Only truly modified notes sync, intelligently determined by timestamps
- ✅ **Fixed sync info format issues**:
  - Joplin uses HTML comment format: `<!-- notebridge_id: xxx -->`
  - Obsidian uses YAML frontmatter format: In note properties
  - Auto-converts format during sync, no longer mixed use
- ✅ **Dual-end sync info writeback**: After successful sync, both sides have correctly formatted sync info
  - Joplin → Obsidian, Joplin side also adds sync info (HTML comments)
  - Obsidian → Joplin, Obsidian side also adds sync info (YAML format)
  - Forced writeback ensures no duplicate syncs
- ✅ **Enhanced image link handling**: Supports both HTML and Markdown format images
  - Supports `<img src=":/resource_id"/>` format (HTML)
  - Supports `![](:/resource_id)` format (Markdown)
  - Auto-downloads resources and converts to Obsidian local paths
  - Preserves original size info (as comments)
- ✅ **Fixed sync info field missing issues**:
  - Ensures extracted sync info contains all required fields
  - Missing fields use defaults (`notebridge_version` defaults to `'1'`)
  - Avoids `'notebridge_version'` and other field missing errors during sync
- ✅ **Auto-skip empty and invalid notes**:
  - Auto-skips notes with empty titles (possibly deleted)
  - Auto-skips notes with empty content
  - Avoids syncing invalid or deleted notes
- ✅ **Auto-detect and fix duplicate headers**: Automatically checks and fixes duplicate sync info headers during sync
- ✅ **Enhanced sync info cleaning logic**: Thoroughly cleans mixed HTML comment and YAML format duplicate info
- ✅ **Added preventive check command**: `prevent-duplicate-headers` for regular duplicate header checks
- ✅ **Fixed timestamp issues**: Avoids generating future timestamps

### Tags and Attachments Sync Fix ([Issue #1](https://github.com/gorf/notebridge/issues/1))
- ✅ **Tags sync**: Joplin → Obsidian writes note tags to YAML frontmatter `tags`; Obsidian → Joplin reads frontmatter and inline `#tags` and creates/assigns tags in Joplin
- ✅ **Attachments**: Clear error messages when resource download fails; case-insensitive resource ID; ensure `attachments` directory exists before writing

### v1.2.0 - Fix One-way Sync Rule Filtering Issues
- ✅ **Fixed one-way sync rules not taking effect**: Program now correctly checks each note's sync rules, ensuring only allowed direction notes sync
- ✅ **Enhanced sync rule checking**: Validates sync rules for each note during sync execution
- ✅ **Improved sync reporting**: Added statistics for skips due to one-way sync restrictions
- ✅ **Added test script**: `test_sync_rules.py` for validating sync rule logic

### Reverse Sync Problem Solution (Smart Judgment, No Manual Work)

**What is the reverse sync problem?**
- After syncing from Obsidian to Joplin, if unmodified in Joplin, shouldn't sync back to Obsidian
- After syncing from Joplin to Obsidian, if unmodified in Obsidian, shouldn't sync back to Joplin

**Smart judgment logic (automatic, no manual work):**
1. Detect note source (`notebridge_source` field)
2. Compare sync timestamps on both sides
3. **If timestamps match** → Not modified → Auto-skip
4. **If timestamps differ** → Modified → Allow sync

**Application scenarios:**
- ✅ Scenario 1: Note from Obsidian, unmodified in Joplin → **Auto-skip**
- ✅ Scenario 2: Note from Obsidian, modified in Joplin → **Allow sync**
- ✅ Scenario 3: Note from Joplin, unmodified in Obsidian → **Auto-skip**
- ✅ Scenario 4: Note from Joplin, modified in Obsidian → **Allow sync**

### Duplicate Header Problem Solution
1. **Immediate fix**: Run `job fix-duplicate-headers` to fix existing duplicate headers
2. **Preventive measures**:
   - Use manual confirmation mode sync: `job sync-manual`
   - Auto-check and fix duplicate headers before each sync
   - Regular preventive checks: `job prevent-duplicate-headers`
3. **Root solution**:
   - Improved sync info addition logic, thoroughly cleans old sync info
   - Added duplicate header check in `update_obsidian_note` function
   - Fixed timestamp generation logic
   - **New smart reverse sync detection**: Auto-skips unmodified reverse syncs

## Advanced Usage & Development Plans

- Support scheduled auto-sync
- Support sync history versions
- Support more customization options

Suggestions or issues are welcome!

