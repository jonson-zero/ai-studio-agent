#!/usr/bin/env python3

import http.server
import json
import os
import shutil
import time
import difflib
import urllib.parse
import subprocess
import sys
import textwrap
import webbrowser
import threading
import argparse
import pathlib
import fnmatch
import shlex
import re

DEFAULT_PORT = 9099
SERVICE_NAME = "ai-studio-agent"
APP_DISPLAY_NAME = "AI Studio Agent"
VERSION = "1.0"
MIN_USERSCRIPT_VERSION = "1.0"
OFFICIAL_REPO = "jonson-zero/ai-studio-agent"
last_known_script_ver = ""

update_cache = {
    "checked_at": 0,
    "has_update": False,
    "latest_version": VERSION,
    "tag_name": "",
    "release_url": "",
    "changelog": "",
    "error": None,
    "is_git": False
}

ALLOWED_ORIGINS = {
    "https://aistudio.google.com",
}

DEFAULT_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "message_to_user": {"type": "string"},
        "inspect_files": {
            "type": "array",
            "items": {"type": "string"}
        },
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["patch", "append", "overwrite", "delete", "mkdir"]},
                    "file_path": {"type": "string"},
                    "anchor_start": {"type": "string"},
                    "anchor_end": {"type": "string"},
                    "search": {"type": "string"},
                    "content": {"type": "string"},
                    "comment": {"type": "string", "maxLength": 120}
                },
                "required": ["action", "file_path"],
                "propertyOrdering": ["action", "file_path", "comment", "anchor_start", "anchor_end", "search", "content"]
            }
        }
    },
    "required": ["thought", "message_to_user", "changes"],
    "propertyOrdering": ["thought", "message_to_user", "inspect_files", "changes"]
}

BUILTIN_PRESETS = {
    "default": {
        "id": "default",
        "name": "Default",
        "description": "Universal coding agent for any language",
        "builtin_version": 1,
        "file_extensions": ["*"],
        "validator_cmd": "",
        "use_godot_skeleton": False,
        "system_instruction": (
            "=== CODING AGENT PROTOCOL ===\n"
            "You are an autonomous expert software engineering agent with direct read/write access to the local project workspace.\n"
            "Your communications and file operations are strictly mediated via the structured JSON output specified below.\n\n"
            "OPERATIONAL WORKFLOW (READ BEFORE WRITE):\n"
            "0. PLANNING (thought):\n"
            "   - Before generating any code or modifications, formulate your step-by-step plan in 'thought'.\n"
            "   - Explicitly identify: target file, function/scope, and the exact 2-6 verbatim anchor lines you will use for each patch.\n\n"
            "1. INSPECTION PHASE (inspect_files):\n"
            "   - If you need to read or verify any existing file before making changes, list its relative path in 'inspect_files' and set 'changes': [].\n"
            "   - The bridge server will instantly provide the full verbatim file content under [TOOL RESULT: Project File Contents].\n"
            "   - NEVER guess or extrapolate existing file contents. Always inspect first to verify exact anchor lines.\n\n"
            "2. MODIFICATION PHASE (changes):\n"
            "   - 'patch': Surgical compact modification (NEVER copy whole functions or surrounding unchanged code):\n"
            "     * STRICT MICRO-ANCHORS: Anchor blocks MUST be 2 to 6 lines maximum. NEVER exceed 8 lines.\n"
            "     * MULTI-EDITS: If modifying multiple disconnected parts of a file, split them into MULTIPLE small patch objects in 'changes'.\n"
            "     * SINGLE-PHASE RULE: NEVER output a 'patch' for a file created via 'overwrite' in the same response. Put the full implementation directly into 'overwrite'.\n"
            "     * TWO PATCH MODES:\n"
            "       a) Standard Micro-Patch (PRIMARY & SAFEST): Use 'search' (2-6 exact lines matching tabs/whitespace) and replacement 'content'.\n"
            "       b) Range Anchor Replacement: Provide 'anchor_start' (1-2 lines, e.g. func header) and 'anchor_end' (1-2 lines, e.g. end of block or next func). Use ONLY to replace an entire function body, never for partial insertions.\n"
            "   - 'append': Append new variables, exports, helper functions, or methods strictly to the end of the file.\n"
            "   - 'overwrite': For brand-new files or complete rewrites of short files (< 120 lines).\n"
            "   - 'mkdir': Creates a new directory ('file_path' is relative path, 'content': \"\").\n"
            "   - 'delete': Deletes an obsolete file (requires explicit confirmation).\n\n"
            "3. CONCEPTUAL DISCUSSIONS:\n"
            "   - If answering architectural questions without file edits, set 'changes': [] and explain in 'message_to_user'.\n\n"
            "4. USER SUMMARY (message_to_user):\n"
            "   - Explain what was implemented, detailing module changes, interfaces, and architecture choices.\n"
            "   - Do NOT dump full raw files here (all code modifications belong strictly inside 'changes').\n\n"
            "CRITICAL INTEGRITY RULES:\n"
            "- NO PHANTOM ACTIONS: NEVER state in 'message_to_user' that you fixed, created, or modified code unless the actual code is fully present in 'changes'. If code edits are requested, 'changes' MUST NOT be empty.\n"
            "- NO RUNAWAY REPETITION: If the user points out missing logic or errors, do NOT apologize or defend yourself. Immediately provide the fix inside 'changes' or request context via 'inspect_files'.\n"
            "- REAL CODE ONLY: Never use placeholder comments (e.g. '// ... existing code ...' or '# ... rest of function unchanged') inside 'content'.\n"
            "- VERBATIM INDENTATION: Preserve exact whitespace/tabs as present in the target file.\n"
            "- CLEAN UTF-8: Output clean text without \\uXXXX unicode escapes.\n"
            "- JSON ESCAPING: Strictly escape double quotes (\") inside 'search' and 'content' as \\\"."
        )
    },
    "godot4": {
        "id": "godot4",
        "name": "Godot 4",
        "description": "Godot 4 & GDScript specialized coding agent",
        "builtin_version": 1,
        "file_extensions": [".gd", ".tscn", ".tres", ".gdshader"],
        "validator_cmd": "{bin} --headless --path {project_path} -s res://{file_path} --check-only",
        "use_godot_skeleton": True,
        "system_instruction": (
            "=== GODOT 4 CODING AGENT PROTOCOL ===\n"
            "You are an autonomous expert Godot 4 software engineering agent with direct read/write access to the local project workspace.\n"
            "Your communications and file operations are strictly mediated via the structured JSON output specified below.\n\n"
            "OPERATIONAL WORKFLOW (READ BEFORE WRITE):\n"
            "0. PLANNING (thought):\n"
            "   - Before generating any code or modifications, formulate your step-by-step plan in 'thought'.\n"
            "   - Explicitly identify: target file, function/scope, and the exact 2-6 verbatim anchor lines you will use for each patch.\n\n"
            "1. INSPECTION PHASE (inspect_files):\n"
            "   - If you need to read or verify any existing file before making changes, list its relative path in 'inspect_files' and set 'changes': [].\n"
            "   - The bridge server will instantly provide the full verbatim file content under [TOOL RESULT: Project File Contents].\n"
            "   - NEVER guess or extrapolate existing file contents. Always inspect first to verify exact anchor lines.\n\n"
            "2. MODIFICATION PHASE (changes):\n"
            "   - 'patch': Surgical compact modification (NEVER copy whole functions or surrounding unchanged code):\n"
            "     * STRICT MICRO-ANCHORS: Anchor blocks MUST be 2 to 6 lines maximum. NEVER exceed 8 lines.\n"
            "     * MULTI-EDITS: If modifying multiple disconnected parts of a file, split them into MULTIPLE small patch objects in 'changes'.\n"
            "     * SINGLE-PHASE RULE: NEVER output a 'patch' for a file created via 'overwrite' in the same response. Put the full implementation directly into 'overwrite'.\n"
            "     * TWO PATCH MODES:\n"
            "       a) Standard Micro-Patch (PRIMARY & SAFEST): Use 'search' (2-6 exact lines matching tabs/whitespace) and replacement 'content'.\n"
            "       b) Range Anchor Replacement: Provide 'anchor_start' (1-2 lines, e.g. func header) and 'anchor_end' (1-2 lines, e.g. end of block or next func). Use ONLY to replace an entire function body, never for partial insertions.\n"
            "   - 'append': Append new variables, signals, @export properties, or helper methods strictly to the end of the script.\n"
            "   - 'overwrite': For brand-new GDScript files, scenes (.tscn), or complete rewrites of short scripts (< 120 lines).\n"
            "   - 'mkdir': Creates a new directory (e.g. 'res://scenes/enemies', 'file_path' is relative path, 'content': \"\").\n"
            "   - 'delete': Deletes an obsolete script/scene (requires explicit confirmation).\n\n"
            "3. CONCEPTUAL DISCUSSIONS:\n"
            "   - If answering architectural questions or Godot node hierarchy design without file edits, set 'changes': [] and explain in 'message_to_user'.\n\n"
            "4. USER SUMMARY (message_to_user):\n"
            "   - Explain what was implemented, detailing node structure, signal connections, and GDScript architecture choices.\n"
            "   - Do NOT dump full raw GDScript files here (all code modifications belong strictly inside 'changes').\n\n"
            "CRITICAL INTEGRITY & GODOT 4 ANTI-HALLUCINATION RULES:\n"
            "- NO PHANTOM ACTIONS: NEVER state in 'message_to_user' that you fixed, created, or connected scripts/nodes unless the actual code is fully present in 'changes'. If code edits are requested, 'changes' MUST NOT be empty.\n"
            "- NO RUNAWAY REPETITION: If the user points out missing logic or syntax errors, do NOT apologize or defend yourself. Immediately provide the fix inside 'changes' or request context via 'inspect_files'.\n"
            "- STRICT GDSCRIPT 2.0 STANDARDS: Adhere to Godot 4 idioms — static typing (e.g. `var speed: float = 100.0`), `@export`, `@onready`, `Callable`, lambda syntax, and exact TAB indentation.\n"
            "- REAL CODE ONLY: Never use placeholder comments (e.g. '# ... existing code ...' or '# ... rest of function unchanged') inside 'content'.\n"
            "- VERBATIM INDENTATION: GDScript is whitespace-sensitive. 'search', 'anchor_start', and 'anchor_end' must preserve exact tab characters.\n"
            "- CLEAN UTF-8: Output clean text without \\uXXXX unicode escapes.\n"
            "- JSON ESCAPING: Strictly escape double quotes (\") inside 'search' and 'content' as \\\"."
        )
    }
}

ALWAYS_IGNORE_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", ".pytest_cache",
    "node_modules", ".venv", "venv", "env", "dist", "build",
    ".godot", ".import", ".idea", ".vscode"
}

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def is_frozen_app():
    return getattr(sys, 'frozen', False)

def get_storage_dir():
    app_dir = get_app_dir()
    local_cfg = os.path.join(app_dir, "config.json")
    portable_flag = os.path.join(app_dir, "portable.txt")
    if os.path.exists(portable_flag) or os.path.exists(local_cfg):
        return app_dir
    try:
        target = os.path.join(str(pathlib.Path.home()), f".{SERVICE_NAME}")
        os.makedirs(target, exist_ok=True)
        test_f = os.path.join(target, ".write_test")
        with open(test_f, "w") as f:
            f.write("ok")
        os.remove(test_f)
        return target
    except Exception as e:
        print(f"[WARN] Home storage path unavailable ({e}). Using application folder.")
        return app_dir

STORAGE_DIR = get_storage_dir()
PRESETS_DIR = os.path.join(STORAGE_DIR, "presets")
HISTORY_DIR = os.path.join(STORAGE_DIR, "history")
CONFIG_FILE = os.path.join(STORAGE_DIR, "config.json")
HISTORY_FILE = os.path.join(STORAGE_DIR, "history.json")

os.makedirs(PRESETS_DIR, exist_ok=True)
os.makedirs(HISTORY_DIR, exist_ok=True)

browse_lock = threading.Lock()
state_lock = threading.Lock()

def init_presets():
    for pid, pdata in BUILTIN_PRESETS.items():
        pf = os.path.join(PRESETS_DIR, f"{pid}.json")
        should_write = False
        if not os.path.exists(pf):
            should_write = True
        else:
            try:
                with open(pf, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                existing_ver = existing.get("builtin_version", 1)
                target_ver = pdata.get("builtin_version", 1)
                is_user_modified = existing.get("user_modified", False)
                if existing_ver < target_ver and not is_user_modified:
                    should_write = True
                    print(f"[PRESET] Auto-upgraded built-in preset '{pid}' to v{target_ver}")
            except Exception:
                should_write = True

        if should_write:
            try:
                to_save = dict(pdata)
                to_save["user_modified"] = False
                with open(pf, "w", encoding="utf-8") as f:
                    json.dump(to_save, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[WARN] Failed to write preset {pid}: {e}")

init_presets()

def load_all_presets():
    presets = {}
    if os.path.exists(PRESETS_DIR):
        for fn in os.listdir(PRESETS_DIR):
            if fn.endswith(".json"):
                full = os.path.join(PRESETS_DIR, fn)
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        pid = data.get("id") or os.path.splitext(fn)[0]
                        data["id"] = pid
                        presets[pid] = data
                except Exception:
                    pass
    for pid, pdata in BUILTIN_PRESETS.items():
        if pid not in presets:
            presets[pid] = pdata
    return presets

def parse_semver(ver_str):
    import re
    cleaned = re.sub(r'^[vV]', '', (ver_str or '').strip())
    parts = []
    for x in cleaned.split('.'):
        m = re.match(r'^(\d+)', x.strip())
        if m:
            parts.append(int(m.group(1)))
        else:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return parts[:3]

def is_newer_version(remote_ver, local_ver):
    return parse_semver(remote_ver) > parse_semver(local_ver)

def check_github_update(repo=None, force=False):
    import urllib.request
    import urllib.error
    global update_cache
    repo = repo or OFFICIAL_REPO
    frozen = is_frozen_app()

    now = time.time()
    if not force and (now - update_cache.get("checked_at", 0)) < 3600:
        update_cache["is_frozen"] = frozen
        return update_cache

    update_cache["checked_at"] = now
    update_cache["is_frozen"] = frozen

    if not repo:
        update_cache["error"] = "No repository configured"
        return update_cache

    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={
            "User-Agent": f"{SERVICE_NAME}/{VERSION}",
            "Accept": "application/vnd.github.v3+json"
        })
        with urllib.request.urlopen(req, timeout=6) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                raw_tag = data.get("tag_name", "").strip()
                tag = raw_tag.lstrip("vV")
                update_cache["tag_name"] = raw_tag
                update_cache["latest_version"] = tag or VERSION
                update_cache["release_url"] = data.get("html_url", f"https://github.com/{repo}/releases/latest")
                update_cache["changelog"] = data.get("body", "")
                update_cache["has_update"] = bool(tag and is_newer_version(tag, VERSION))
                update_cache["error"] = None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            update_cache["error"] = "Repository or release not found on GitHub"
        else:
            update_cache["error"] = f"GitHub API returned error {e.code}"
        update_cache["has_update"] = False
    except urllib.error.URLError:
        update_cache["error"] = "Could not connect to GitHub (network offline)"
        update_cache["has_update"] = False
    except Exception as e:
        update_cache["error"] = str(e)
        update_cache["has_update"] = False
    return update_cache

def get_userscript_url():
    return f"https://raw.githubusercontent.com/{OFFICIAL_REPO}/main/agent.user.js"

def load_config():
    defaults = {
        "project_path": "",
        "recent_projects": [],
        "active_preset": "default",
        "validator_bin": "",
        "port": DEFAULT_PORT,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                defaults.update(json.load(f))
        except Exception:
            pass
    return defaults

def add_recent_project(proj_path):
    if not proj_path or not os.path.exists(proj_path):
        return
    norm = os.path.normpath(proj_path).replace("\\", "/")
    recents = config.get("recent_projects", [])
    if norm in recents:
        recents.remove(norm)
    recents.insert(0, norm)
    config["recent_projects"] = recents[:8]

def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        tmp_file = CONFIG_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, CONFIG_FILE)
    except Exception as e:
        print(f"[ERROR] Failed to save config: {e}")

def get_active_preset_data():
    presets = load_all_presets()
    cur_id = config.get("active_preset", "default")
    return presets.get(cur_id) or presets.get("default") or list(presets.values())[0]

def get_workspace_key(proj_path):
    if not proj_path:
        return "global"
    import hashlib
    norm = os.path.normpath(proj_path).replace("\\", "/").rstrip("/").lower()
    return hashlib.sha256(norm.encode('utf-8')).hexdigest()[:16]

def get_workspace_history_file(proj_path):
    key = get_workspace_key(proj_path)
    return os.path.join(HISTORY_DIR, f"{key}.json")

def load_workspace_history(proj_path):
    w_file = get_workspace_history_file(proj_path)
    if os.path.exists(w_file):
        try:
            with open(w_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                legacy = json.load(f)
                if isinstance(legacy, list):
                    norm_cur = os.path.normpath(proj_path or '').replace("\\", "/").rstrip("/").lower()
                    filtered = [
                        item for item in legacy
                        if not item.get("project_path") or os.path.normpath(item.get("project_path")).replace("\\", "/").rstrip("/").lower() == norm_cur
                    ]
                    if filtered:
                        return filtered
        except Exception:
            pass
    return []

def save_workspace_history(proj_path, hist):
    try:
        w_file = get_workspace_history_file(proj_path)
        tmp_file = w_file + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(hist[:80], f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, w_file)
    except Exception as e:
        print(f"[ERROR] Failed to save workspace history: {e}")

config = load_config()
latest_ai_message = ""
ACTIVE_PORT = DEFAULT_PORT

def load_gitignore_patterns(project_path):
    patterns = []
    gi_path = os.path.join(project_path, ".gitignore")
    if os.path.exists(gi_path):
        try:
            with open(gi_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
        except Exception:
            pass
    return patterns

def clean_rel_path(path_str):
    p = (path_str or "").strip()
    if p.lower().startswith("res://"):
        p = p[6:]
    return p.lstrip("/\\")

def is_ignored(rel_path, is_dir, patterns):
    norm_rel = rel_path.replace("\\", "/").strip("/")
    parts = norm_rel.split("/")
    for part in parts:
        if part in ALWAYS_IGNORE_DIRS:
            return True

    for pat in patterns:
        pat = pat.replace("\\", "/").strip()
        dir_only = pat.endswith("/")
        pat_clean = pat.rstrip("/")

        if dir_only and not is_dir:
            continue

        if "/" not in pat_clean:
            if any(fnmatch.fnmatch(p, pat_clean) for p in parts):
                return True
        else:
            pat_pattern = pat_clean.lstrip("/")
            if fnmatch.fnmatch(norm_rel, pat_pattern) or fnmatch.fnmatch(norm_rel, "*/" + pat_pattern):
                return True
    return False

def is_safe_path(base_dir, target_rel_path):
    if not base_dir or not target_rel_path:
        return False
    try:
        clean_target = clean_rel_path(target_rel_path)
        if os.path.isabs(clean_target) or os.path.splitdrive(clean_target)[0]:
            return False
        base = os.path.realpath(base_dir)
        target = os.path.realpath(os.path.join(base, clean_target))
        return os.path.commonpath([base]) == os.path.commonpath([base, target])
    except Exception:
        return False

MAX_FILE_READ_SIZE = 300 * 1024  # 300 KB limit for inspect and content

def read_text_file_safe(full_path, max_size=MAX_FILE_READ_SIZE):
    if not os.path.exists(full_path):
        return None, "File does not exist"
    if os.path.isdir(full_path):
        return None, "Path is a directory, not a file"
    try:
        size = os.path.getsize(full_path)
        if size > max_size:
            return None, f"File exceeds {max_size // 1024}KB limit ({size // 1024}KB)"
        with open(full_path, "rb") as f:
            raw = f.read(4096)
            if b"\x00" in raw:
                return None, "Binary file detected"
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            return normalize_newlines(f.read()), None
    except Exception as e:
        return None, f"Read error: {e}"

def normalize_newlines(text):
    return text.replace('\r\n', '\n') if text else ""

def pick_folder_subprocess():
    with browse_lock:
        if not is_frozen_app():
            tk_code = (
                "import tkinter as tk; from tkinter import filedialog; "
                "root = tk.Tk(); root.withdraw(); "
                "root.wm_attributes('-topmost', 1); "
                "p = filedialog.askdirectory(title='Select Project Workspace Directory', parent=root); "
                "print(p or '')"
            )
            try:
                flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                res = subprocess.run([sys.executable, "-c", tk_code], capture_output=True, text=True, timeout=45, creationflags=flags)
                out = res.stdout.strip().splitlines()
                if out and out[-1].strip():
                    return out[-1].replace("\\", "/")
            except Exception:
                pass

        if sys.platform != "win32":
            try:
                res = subprocess.run(["zenity", "--file-selection", "--directory", "--title=Select Project Workspace Directory"],
                                     capture_output=True, text=True, timeout=45)
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass

        if sys.platform == "win32":
            ps_code = (
                "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
                "$top = New-Object System.Windows.Forms.Form; "
                "$top.TopMost = $true; "
                "$top.Visible = $false; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = 'Select Project Workspace Directory'; "
                "$f.ShowNewFolderButton = $true; "
                "if ($f.ShowDialog($top) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }; "
                "$top.Dispose()"
            )
            try:
                res = subprocess.run(["powershell.exe", "-Sta", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_code],
                                     capture_output=True, text=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
                out = res.stdout.strip().splitlines()
                if out and out[-1].strip():
                    return out[-1].replace("\\", "/")
            except Exception:
                pass
        return ""

def pick_file_subprocess():
    with browse_lock:
        if not is_frozen_app():
            tk_code = (
                "import tkinter as tk; from tkinter import filedialog; "
                "root = tk.Tk(); root.withdraw(); "
                "root.wm_attributes('-topmost', 1); "
                "p = filedialog.askopenfilename(title='Select Validator Executable (e.g. godot.exe, python.exe)', "
                "filetypes=[('Executables', '*.exe;*'), ('All Files', '*.*')], parent=root); "
                "print(p or '')"
            )
            try:
                flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                res = subprocess.run([sys.executable, "-c", tk_code], capture_output=True, text=True, timeout=45, creationflags=flags)
                out = res.stdout.strip().splitlines()
                if out and out[-1].strip():
                    return out[-1].replace("\\", "/")
            except Exception:
                pass

        if sys.platform != "win32":
            try:
                res = subprocess.run(["zenity", "--file-selection", "--title=Select Validator Executable"],
                                     capture_output=True, text=True, timeout=45)
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass

        if sys.platform == "win32":
            ps_code = (
                "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
                "$top = New-Object System.Windows.Forms.Form; "
                "$top.TopMost = $true; "
                "$top.Visible = $false; "
                "$f = New-Object System.Windows.Forms.OpenFileDialog; "
                "$f.Title = 'Select Validator Executable'; "
                "$f.Filter = 'Executables (*.exe)|*.exe|All files (*.*)|*.*'; "
                "if ($f.ShowDialog($top) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.FileName }; "
                "$top.Dispose()"
            )
            try:
                res = subprocess.run(["powershell.exe", "-Sta", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_code],
                                     capture_output=True, text=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
                out = res.stdout.strip().splitlines()
                if out and out[-1].strip():
                    return out[-1].replace("\\", "/")
            except Exception:
                pass
        return ""

def get_project_files(proj_path, allowed_extensions=None):
    if not proj_path or not os.path.exists(proj_path):
        return []
    gi_patterns = load_gitignore_patterns(proj_path)
    matched_files = []
    allow_all = not allowed_extensions or "*" in allowed_extensions

    for root, dirs, filenames in os.walk(proj_path):
        rel_root = os.path.relpath(root, proj_path).replace("\\", "/")
        if rel_root == ".":
            rel_root = ""

        dirs[:] = [d for d in dirs if not is_ignored(f"{rel_root}/{d}".lstrip("/"), True, gi_patterns)]

        for fn in filenames:
            rel_file = f"{rel_root}/{fn}".lstrip("/")
            if is_ignored(rel_file, False, gi_patterns):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if allow_all or ext in allowed_extensions:
                matched_files.append(rel_file)

    matched_files.sort()
    return matched_files

def generate_project_skeleton(proj_path, preset):
    if not proj_path or not os.path.exists(proj_path):
        return "# Project path not configured."

    if preset.get("use_godot_skeleton"):
        skeleton = []
        files = get_project_files(proj_path, [".gd"])
        for rel in files:
            full = os.path.join(proj_path, rel)
            lines = []
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        s = line.strip()
                        if (s.startswith("class_name ") or s.startswith("extends ") or
                            s.startswith("@export") or s.startswith("@onready") or
                            s.startswith("signal ") or (s.startswith("func ") and s.endswith(":"))):
                            lines.append(line.rstrip())
            except Exception:
                pass
            if lines:
                skeleton.append(f"# === {rel} ===\n" + "\n".join(lines))
        return "\n\n".join(skeleton) if skeleton else "# No GDScript signatures found."

    files = get_project_files(proj_path, preset.get("file_extensions"))
    if not files:
        return "# Workspace is empty or no matching files found."

    tree_lines = ["# Project File Tree:"]
    for f in files[:200]:
        tree_lines.append(f"├── {f}")
    if len(files) > 200:
        tree_lines.append(f"# ... and {len(files) - 200} more files.")
    return "\n".join(tree_lines)

def run_syntax_validator(proj_path, rel_path, preset, bin_path):
    cmd_template = preset.get("validator_cmd", "").strip()
    if not cmd_template:
        return None
    try:
        norm_bin = os.path.normpath(bin_path).replace("\\", "/") if bin_path else "godot"
        norm_proj = os.path.normpath(proj_path).replace("\\", "/")
        full_file = os.path.normpath(os.path.join(proj_path, rel_path)).replace("\\", "/")

        safe_bin = f'"{norm_bin}"' if (" " in norm_bin and not norm_bin.startswith('"')) else norm_bin
        safe_proj = f'"{norm_proj}"' if (" " in norm_proj and not norm_proj.startswith('"')) else norm_proj
        safe_file = f'"{full_file}"' if (" " in full_file and not full_file.startswith('"')) else full_file

        clean_rel = clean_rel_path(rel_path).replace("\\", "/")
        cmd_str = cmd_template.format(
            bin=safe_bin,
            project_path=safe_proj,
            file_path=clean_rel,
            full_path=safe_file
        )
        cmd_args = shlex.split(cmd_str, posix=(sys.platform != "win32"))
        cmd_args = [arg.strip('"\'') for arg in cmd_args]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        res = subprocess.run(cmd_args, cwd=proj_path, shell=False, capture_output=True, text=True, timeout=8, creationflags=flags)
        out = (res.stderr + "\n" + res.stdout).strip()
        if res.returncode != 0 or "ERROR:" in out or "Error:" in out or "Compile Error:" in out or "SyntaxError:" in out:
            err_lines = [l for l in out.splitlines() if "error" in l.lower() or "warning" in l.lower() or "syntax" in l.lower()]
            return "\n".join(err_lines[:2]) if err_lines else (out.splitlines()[0] if out else "Syntax validation warning")
    except Exception as e:
        return f"Validator execution error: {e}"
    return None

ELLIPSIS_PATTERNS = re.compile(
    r'^[ \t]*(?://|#|/\*|--|<!--)\s*(?:\.{3}|…|existing code|rest of|same as|logic here).*?(?:\*/|-->)?$',
    re.IGNORECASE | re.MULTILINE
)

def apply_range_patch(old_content, anchor_start, anchor_end, new_content):
    if not anchor_start or not anchor_end:
        return old_content, False, "Both anchor_start and anchor_end are required for range patching."
    old_lines = old_content.splitlines()
    start_lines = [l.strip() for l in anchor_start.strip().splitlines() if l.strip()]
    end_lines = [l.strip() for l in anchor_end.strip().splitlines() if l.strip()]
    if not start_lines or not end_lines:
        return old_content, False, "Anchors must contain non-empty lines."

    start_idx = -1
    for i in range(len(old_lines)):
        if old_lines[i].strip() == start_lines[0]:
            if all(i + j < len(old_lines) and old_lines[i + j].strip() == start_lines[j] for j in range(len(start_lines))):
                start_idx = i
                break
    if start_idx == -1:
        return old_content, False, f"anchor_start '{start_lines[0][:50]}' not found in file."

    search_from = start_idx + len(start_lines)
    matches = []
    for i in range(search_from, len(old_lines)):
        if old_lines[i].strip() == end_lines[0]:
            if all(i + j < len(old_lines) and old_lines[i + j].strip() == end_lines[j] for j in range(len(end_lines))):
                matches.append(i)

    if not matches:
        if anchor_start.strip() == anchor_end.strip():
            end_idx = start_idx + len(start_lines)
        else:
            return old_content, False, f"anchor_end '{end_lines[0][:50]}' not found after line {start_idx + 1}."
    else:
        end_idx = matches[0] + len(end_lines)

    orig_first = old_lines[start_idx]
    orig_indent = orig_first[:len(orig_first) - len(orig_first.lstrip())]
    dedented = textwrap.dedent(new_content)
    aligned_content = textwrap.indent(dedented, orig_indent, predicate=lambda l: bool(l.strip())).splitlines()
    content_lines = aligned_content
    first_content = next((l.strip() for l in content_lines if l.strip()), "")
    last_content = next((l.strip() for l in reversed(content_lines) if l.strip()), "")

    replace_from = start_idx if first_content == start_lines[0] else (start_idx + len(start_lines))
    replace_to = end_idx if last_content == end_lines[-1] else (end_idx - len(end_lines))

    if replace_from > replace_to:
        replace_from = start_idx
        replace_to = end_idx

    if False:
        decl_re = re.compile(r'\b(?:const|let|var|def|function|class)\s+([a-zA-Z_][a-zA-Z0-9_]*)\b')
        deleted_decls = set()
        for dl in deleted_lines:
            for m in decl_re.finditer(dl):
                deleted_decls.add(m.group(1))
        if deleted_decls:
            lost = [v for v in deleted_decls if not re.search(r'\b' + re.escape(v) + r'\b', new_content)]
            if lost:
                return old_content, False, f"REJECTED: Range patch would delete declarations without re-defining them: {lost}. Use micro-patch 'search' instead."

    replaced = old_lines[:replace_from] + aligned_content + old_lines[replace_to:]
    res = "\n".join(replaced) + ("\n" if old_content.endswith("\n") else "")
    return res, True, f"Replaced range between line {start_idx + 1} and {end_idx}"

def apply_patch_smart(old_content, search_block, replace_block):
    if not search_block:
        return old_content, False, "Search block is empty."

    import re
    search_block = re.sub(r'^\s*//\s*(?:pseudo anchor|actual search|search block).*$\n?', '', search_block, flags=re.MULTILINE)
    search_block = re.sub(r'^\s*/\*.*?\*/\s*\n?', '', search_block, flags=re.DOTALL)
    if not search_block.strip():
        return old_content, False, "Search block is empty or contained only comment markers."

    if ELLIPSIS_PATTERNS.search(search_block):
        chunks = [c.strip() for c in ELLIPSIS_PATTERNS.split(search_block) if c.strip()]
        if len(chunks) >= 2:
            escaped = [re.escape(c) for c in chunks]
            pattern = r'[\s\S]*?'.join(escaped)
            matches = list(re.finditer(pattern, old_content))
            if len(matches) == 1:
                m = matches[0]
                return old_content[:m.start()] + replace_block + old_content[m.end():], True, "Ellipsis wildcard match"
            elif len(matches) > 1:
                return old_content, False, f"Ambiguous ellipsis match ({len(matches)} occurrences). Provide more anchor lines."

    search_lines_pre = search_block.splitlines()
    if len(search_lines_pre) > 20:
        return old_content, False, f"REJECTED: Search block is too large ({len(search_lines_pre)} lines). Maximum allowed search size is 20 lines. Pick a compact unique 2 to 6 line anchor."
    search_block = re.sub(r'^```[a-zA-Z0-9_-]*\n', '', search_block)
    search_block = re.sub(r'\n```$', '', search_block)

    exact_count = old_content.count(search_block)
    if exact_count == 1:
        return old_content.replace(search_block, replace_block, 1), True, "Exact match"
    elif exact_count > 1:
        line_nums = []
        pos = 0
        while True:
            p = old_content.find(search_block, pos)
            if p == -1:
                break
            line_nums.append(old_content[:p].count('\n') + 1)
            pos = p + max(1, len(search_block))
        return old_content, False, f"Ambiguous search block found in {exact_count} locations (lines: {line_nums}). Add unique surrounding context or function header as anchor."

    old_lines = old_content.splitlines()
    search_lines = search_block.splitlines()

    # 1. Trailing-whitespace tolerant match
    len_s = len(search_lines)
    ws_matches = []
    for i in range(len(old_lines) - len_s + 1):
        if all(old_lines[i + j].rstrip() == search_lines[j].rstrip() for j in range(len_s)):
            ws_matches.append(i)

    if len(ws_matches) == 1:
        i = ws_matches[0]
        end_idx = i + len_s
        new_lines = old_lines[:i] + replace_block.splitlines() + old_lines[end_idx:]
        return "\n".join(new_lines) + ("\n" if old_content.endswith("\n") else ""), True, "Whitespace-trimmed match"
    elif len(ws_matches) > 1:
        return old_content, False, f"Ambiguous match found in {len(ws_matches)} locations with trimmed whitespace. Provide more unique context."

    # 2. Non-empty line sequence matching with indentation alignment
    non_empty_search = [(idx, line.strip()) for idx, line in enumerate(search_lines) if line.strip()]
    if non_empty_search:
        non_empty_old = [(idx, line.strip()) for idx, line in enumerate(old_lines) if line.strip()]
        len_seq = len(non_empty_search)
        search_stripped_seq = [item[1] for item in non_empty_search]
        for i in range(len(non_empty_old) - len_seq + 1):
            if [non_empty_old[i + j][1] for j in range(len_seq)] == search_stripped_seq:
                start_line_idx = non_empty_old[i][0]
                end_line_idx = non_empty_old[i + len_seq - 1][0] + 1

                orig_first_line = old_lines[start_line_idx]
                search_first_line = search_lines[non_empty_search[0][0]]
                orig_indent = orig_first_line[:len(orig_first_line) - len(orig_first_line.lstrip())]
                search_indent = search_first_line[:len(search_first_line) - len(search_first_line.lstrip())]

                replace_lines = replace_block.splitlines()
                adjusted_replace = []
                for r_line in replace_lines:
                    if r_line.strip() and r_line.startswith(search_indent):
                        adjusted_replace.append(orig_indent + r_line[len(search_indent):])
                    else:
                        adjusted_replace.append(r_line)

                new_lines = old_lines[:start_line_idx] + adjusted_replace + old_lines[end_line_idx:]
                return "\n".join(new_lines) + ("\n" if old_content.endswith("\n") else ""), True, "Indentation-aligned match"

    # 3. Fuzzy sequence match (Levenshtein / Gestalt Pattern Matching for minor typos or drift)
    if len_s >= 2 and len_s <= len(old_lines) <= 400:
        best_ratio = 0.0
        second_best_ratio = 0.0
        best_span = None

        search_clean = "\n".join(l.strip() for l in search_lines if l.strip())
        for win_len in range(max(1, len_s - 2), min(len(old_lines) + 1, len_s + 3)):
            for i in range(len(old_lines) - win_len + 1):
                win_lines = old_lines[i : i + win_len]
                win_clean = "\n".join(l.strip() for l in win_lines if l.strip())
                ratio = difflib.SequenceMatcher(None, win_clean, search_clean).quick_ratio()
                if ratio > 0.70:
                    ratio = difflib.SequenceMatcher(None, win_clean, search_clean).ratio()

                if ratio > best_ratio:
                    second_best_ratio = best_ratio
                    best_ratio = ratio
                    best_span = (i, i + win_len)
                elif ratio > second_best_ratio:
                    second_best_ratio = ratio

        if best_ratio >= 0.82 and (best_ratio - second_best_ratio >= 0.08 or second_best_ratio < 0.65) and best_span:
            start_i, end_i = best_span
            new_lines = old_lines[:start_i] + replace_block.splitlines() + old_lines[end_i:]
            return "\n".join(new_lines) + ("\n" if old_content.endswith("\n") else ""), True, f"Fuzzy match ({int(best_ratio * 100)}% similarity)"

    first_clean = non_empty_search[0][1] if non_empty_search else ""
    candidate_indices = []
    if first_clean:
        for idx, l in enumerate(old_lines):
            if l.strip() == first_clean:
                candidate_indices.append(idx)

    diag_lines = [f"Search block ({len_s} lines) could not be matched."]
    if candidate_indices:
        best_cand = candidate_indices[0]
        diverge_offset = 0
        for k in range(len_s):
            if (best_cand + k < len(old_lines)) and (old_lines[best_cand + k].rstrip() != search_lines[k].rstrip()):
                diverge_offset = k
                break
        line_num = best_cand + 1
        div_line_num = best_cand + diverge_offset + 1
        diag_lines.append(f"Anchor line '{first_clean[:50]}' found at line {line_num}, but diverged at line {div_line_num}.")
        start_show = max(0, best_cand - 2)
        end_show = min(len(old_lines), best_cand + len_s + 4)
        snippet_lines = []
        for ln in range(start_show, end_show):
            prefix = ">>" if ln == (best_cand + diverge_offset) else "  "
            snippet_lines.append(f"{prefix} {ln + 1:4d} | {old_lines[ln]}")
        diag_lines.append("Actual file context around candidate:\n```\n" + "\n".join(snippet_lines) + "\n```")
    else:
        diag_lines.append(f"Initial line '{first_clean[:50]}' was not found anywhere in target file.")

    return old_content, False, "\n".join(diag_lines)

def simulate_changes_batch(proj, changes, confirm_delete):
    sim_files = {}
    seen_ranges = []
    for idx, item in enumerate(changes):
        if item.get("action", "").lower() == "patch" and item.get("anchor_start") and item.get("anchor_end"):
            astart = item.get("anchor_start").strip()
            aend = item.get("anchor_end").strip()
            fpath = item.get("file_path", "")
            for (prev_path, prev_start, prev_end) in seen_ranges:
                if prev_path == fpath and (astart == prev_end or aend == prev_start or astart == prev_start):
                    return False, [f"{fpath}: REJECTED: Overlapping or adjacent range anchors detected between patches."]
            seen_ranges.append((fpath, astart, aend))

    for idx, item in enumerate(changes):
        rel_p = clean_rel_path(item.get("file_path", ""))
        if not rel_p:
            continue
        if not is_safe_path(proj, rel_p):
            return False, [f"{rel_p}: Security violation (unsafe path)."]

        act = item.get("action", "overwrite").lower()
        cnt = normalize_newlines(item.get("content", ""))
        srch = normalize_newlines(item.get("search", ""))
        a_start = normalize_newlines(item.get("anchor_start", ""))
        a_end = normalize_newlines(item.get("anchor_end", ""))
        full_p = os.path.join(proj, rel_p)

        if rel_p not in sim_files:
            old_c = None
            if os.path.exists(full_p) and not os.path.isdir(full_p):
                with open(full_p, "r", encoding="utf-8", errors="replace") as f:
                    old_c = normalize_newlines(f.read())
            sim_files[rel_p] = old_c

        cur_c = sim_files[rel_p]

        if act == "mkdir":
            continue
        elif act == "delete":
            if not item.get("confirmed", False) and not confirm_delete:
                return False, [f"{rel_p}: Deletion blocked (explicit user confirmation required)."]
            sim_files[rel_p] = None
        elif act == "patch":
            if not cnt:
                return False, [f"{rel_p} (change #{idx+1}): Patch rejected because 'content' is missing or empty."]
            if cur_c is None:
                return False, [f"{rel_p} (change #{idx+1}): File does not exist to patch."]
            if a_start and a_end:
                new_c, ok, diag = apply_range_patch(cur_c, a_start, a_end, cnt)
            else:
                new_c, ok, diag = apply_patch_smart(cur_c, srch, cnt)
            if not ok:
                return False, [f"{rel_p} (change #{idx+1}): {diag}"]
            sim_files[rel_p] = new_c
        elif act == "append":
            sim_files[rel_p] = (cur_c + "\n" if cur_c else "") + cnt + "\n"
        else:
            sim_files[rel_p] = cnt

    return True, []

HTML_DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Studio Agent Dashboard</title>
<style>
  :root {
    --bg-base: #090d13;
    --bg-surface: #0e131b;
    --bg-card: #131923;
    --bg-card-hover: #171f2b;
    --border: #232b38;
    --border-light: #2e3848;
    --text-primary: #f0f6fc;
    --text-secondary: #8b949e;
    --text-muted: #586069;
    --accent: #238636;
    --accent-hover: #2ea043;
    --blue: #4493f8;
    --blue-bg: rgba(68, 147, 248, 0.12);
    --green: #3fb950;
    --green-bg: rgba(63, 185, 80, 0.12);
    --red: #f85149;
    --red-bg: rgba(248, 81, 73, 0.12);
    --yellow: #d29922;
    --yellow-bg: rgba(210, 153, 34, 0.12);
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg-base);
    color: var(--text-primary);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    font-size: 13px;
    -webkit-font-smoothing: antialiased;
  }

  /* --- HEADER --- */
  header {
    background: var(--bg-surface);
    border-bottom: 1px solid var(--border);
    padding: 10px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    flex-shrink: 0;
  }

  .header-left, .header-right {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .brand-title {
    font-size: 14px;
    font-weight: 700;
    color: #fff;
    letter-spacing: -0.2px;
  }
  .version-tag {
    font-size: 11px;
    background: rgba(255, 255, 255, 0.05);
    color: var(--text-secondary);
    border: 1px solid var(--border);
    padding: 2px 7px;
    border-radius: 12px;
    font-weight: 600;
  }

  .badge-update {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--green-bg);
    border: 1px solid rgba(63, 185, 80, 0.4);
    padding: 3px 9px;
    border-radius: 12px;
    font-size: 11px;
    color: var(--green);
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
  }
  .badge-update:hover {
    background: rgba(63, 185, 80, 0.2);
    border-color: var(--green);
  }

  .v-divider {
    width: 1px;
    height: 18px;
    background: var(--border);
    margin: 0 4px;
  }

  /* Interactive Workspace Pill */
  .workspace-pill {
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    padding: 5px 12px;
    border-radius: 6px;
    cursor: pointer;
    user-select: none;
    transition: all 0.15s ease;
  }
  .workspace-pill:hover {
    background: var(--bg-card-hover);
    border-color: var(--blue);
  }
  .workspace-name {
    font-weight: 600;
    font-size: 12px;
    color: var(--text-primary);
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .dot-green { background: var(--green); box-shadow: 0 0 6px rgba(63, 185, 80, 0.6); }
  .dot-red { background: var(--red); box-shadow: 0 0 6px rgba(248, 81, 73, 0.6); }

  /* Preset Selector */
  .preset-control {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    padding: 3px 8px;
    border-radius: 6px;
  }
  .preset-control label {
    font-size: 10px;
    font-weight: 700;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  select.preset-select {
    background: transparent;
    color: #fff;
    border: none;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    outline: none;
  }
  select.preset-select option {
    background: var(--bg-surface);
    color: #fff;
  }

  /* Button System */
  .btn {
    background: var(--bg-card);
    color: var(--text-primary);
    border: 1px solid var(--border);
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
    font-weight: 600;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: all 0.15s;
    user-select: none;
    line-height: 1;
  }
  .btn svg {
    flex-shrink: 0;
  }
  .btn:hover:not(:disabled) {
    background: var(--bg-card-hover);
    border-color: var(--border-light);
    color: #fff;
  }
  .btn.btn-primary {
    background: var(--accent);
    color: #fff;
    border-color: rgba(255,255,255,0.1);
  }
  .btn.btn-primary:hover {
    background: var(--accent-hover);
  }
  .btn.btn-studio {
    color: #79c0ff;
    border-color: rgba(68, 147, 248, 0.35);
    background: var(--blue-bg);
  }
  .btn.btn-studio:hover {
    background: rgba(68, 147, 248, 0.22);
    border-color: var(--blue);
    color: #fff;
  }
  .btn.btn-ghost {
    background: transparent;
    border-color: transparent;
    color: var(--text-secondary);
  }
  .btn.btn-ghost:hover {
    background: rgba(255, 255, 255, 0.05);
    color: var(--text-primary);
    border-color: transparent;
  }
  .btn.btn-danger-ghost {
    background: transparent;
    border-color: transparent;
    color: var(--text-secondary);
  }
  .btn.btn-danger-ghost:hover {
    background: var(--red-bg);
    color: var(--red);
    border-color: transparent;
  }

  /* --- MAIN CONTENT --- */
  main {
    flex: 1;
    overflow-y: auto;
    padding: 24px 32px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    max-width: 1240px;
    margin: 0 auto;
    width: 100%;
  }

  .setup-alert {
    background: #1c150c;
    border: 1px solid rgba(210, 153, 34, 0.4);
    border-radius: 8px;
    padding: 12px 18px;
    color: #e6edf3;
    font-size: 13px;
    display: none;
    align-items: center;
    justify-content: space-between;
  }

  /* Context History Toolbar */
  .history-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 4px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  }
  .history-heading {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }
  .history-title {
    font-size: 14px;
    font-weight: 700;
    color: #fff;
    letter-spacing: -0.1px;
  }
  .history-count {
    font-size: 12px;
    color: var(--text-secondary);
    font-weight: 500;
  }
  .history-actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  /* Transaction Cards */
  .trans-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
  }
  .trans-header {
    padding: 10px 16px;
    background: rgba(255, 255, 255, 0.02);
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
    user-select: none;
    transition: background 0.15s;
  }
  .trans-header:hover {
    background: rgba(255, 255, 255, 0.04);
  }
  .trans-meta {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .toggle-icon {
    font-size: 10px;
    color: var(--text-secondary);
    width: 14px;
    display: inline-flex;
    justify-content: center;
    user-select: none;
    transition: color 0.15s, transform 0.15s;
  }
  .trans-header:hover .toggle-icon, .diff-file-bar:hover .toggle-icon {
    color: var(--blue);
  }
  .trans-time {
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-size: 12px;
    color: var(--text-secondary);
  }
  .trans-stats {
    font-size: 12px;
    font-weight: 700;
    display: flex;
    gap: 8px;
    font-family: ui-monospace, SFMono-Regular, monospace;
  }
  .stat-plus { color: var(--green); }
  .stat-minus { color: var(--red); }
  .trans-badge {
    font-size: 10px;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .badge-applied {
    background: var(--green-bg);
    color: var(--green);
    border: 1px solid rgba(63, 185, 80, 0.3);
  }
  .badge-reverted {
    background: var(--red-bg);
    color: var(--red);
    border: 1px solid rgba(248, 81, 73, 0.3);
  }

  .diff-section {
    padding: 12px 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .diff-file-card {
    background: #080b0f;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .diff-file-bar {
    background: #111620;
    padding: 7px 14px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
    cursor: pointer;
    user-select: none;
    transition: background 0.15s;
  }
  .diff-file-bar:hover {
    background: #161c28;
  }
  .diff-badge {
    font-size: 10px;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 4px;
    text-transform: uppercase;
  }
  .diff-badge-a { background: rgba(63, 185, 80, 0.2); color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.3); }
  .diff-badge-m { background: rgba(88, 166, 255, 0.2); color: #58a6ff; border: 1px solid rgba(88, 166, 255, 0.3); }
  .diff-badge-d { background: rgba(248, 81, 73, 0.2); color: #f85149; border: 1px solid rgba(248, 81, 73, 0.3); }
  .diff-badge-dir { background: rgba(210, 153, 34, 0.2); color: #d29922; border: 1px solid rgba(210, 153, 34, 0.3); }

  .diff-code {
    padding: 6px 0;
    overflow-x: auto;
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    font-size: 11.5px;
    line-height: 1.45;
    background: #080b0f;
  }
  .diff-row {
    display: flex;
    align-items: baseline;
    padding: 1px 12px;
    white-space: pre;
    min-height: 18px;
  }
  .diff-row:hover { background: rgba(255, 255, 255, 0.03); }
  .diff-marker { width: 18px; flex-shrink: 0; user-select: none; font-weight: 700; text-align: left; }
  .diff-content { flex: 1; }
  .diff-line-add { background: rgba(46, 160, 67, 0.15); color: #7ee787; }
  .diff-line-add .diff-marker { color: #56d364; }
  .diff-line-del { background: rgba(248, 81, 73, 0.15); color: #ffa198; }
  .diff-line-del .diff-marker { color: #f85149; }
  .diff-line-hunk {
    background: rgba(56, 139, 253, 0.1);
    color: #79c0ff;
    font-weight: 600;
    padding-top: 4px;
    padding-bottom: 4px;
    margin: 4px 0;
    border-top: 1px solid rgba(56, 139, 253, 0.15);
    border-bottom: 1px solid rgba(56, 139, 253, 0.15);
  }
  .diff-line-header { color: var(--text-secondary); font-weight: 600; font-size: 11px; background: rgba(255, 255, 255, 0.02); }
  .diff-line-context { color: #c9d1d9; }

  /* Modals */
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.75);
    backdrop-filter: blur(2px);
    display: none;
    justify-content: center;
    align-items: center;
    z-index: 1000;
  }
  .modal {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    width: 680px;
    max-height: 88vh;
    display: flex;
    flex-direction: column;
    box-shadow: 0 20px 50px rgba(0,0,0,0.7);
  }
  .modal-tabs {
    display: flex;
    border-bottom: 1px solid var(--border);
    background: var(--bg-base);
    padding: 0 16px;
  }
  .tab-btn {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--text-secondary);
    padding: 12px 16px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .tab-btn.active {
    color: #fff;
    border-bottom-color: var(--blue);
    background: rgba(88,166,255,0.05);
  }
  .modal-body {
    overflow-y: auto;
    padding: 20px 24px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .modal-row {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .modal-row label {
    font-size: 12px;
    color: var(--text-secondary);
    font-weight: 600;
  }
  .input-with-btn {
    display: flex;
    gap: 8px;
  }
  .input-text {
    background: var(--bg-base);
    border: 1px solid var(--border);
    color: #fff;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 12px;
    width: 100%;
    outline: none;
  }
  .input-text:focus {
    border-color: var(--blue);
  }
  textarea.input-text {
    font-family: ui-monospace, monospace;
    font-size: 11px;
    line-height: 1.45;
    resize: vertical;
    min-height: 140px;
  }

  /* Toast Notification */
  .toast {
    position: fixed;
    bottom: 28px;
    left: 50%;
    transform: translateX(-50%) translateY(20px);
    background: #1f2733;
    color: #fff;
    padding: 8px 18px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
    box-shadow: 0 8px 24px rgba(0,0,0,0.6);
    border: 1px solid var(--border-light);
    display: none;
    z-index: 2000;
    transition: opacity 0.2s, transform 0.2s;
    opacity: 0;
    pointer-events: none;
  }
  .toast.show {
    display: block;
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
  .toast.error {
    background: #902420;
    border-color: #f85149;
  }
</style>
</head>
<body>

<header>
  <div class="header-left">
    <div class="brand">
      <span class="brand-title">AI Studio Agent</span>
      <span class="version-tag" id="lblVersionTag">v1.0</span>
      <div id="badgeUpdate" class="badge-update" style="display:none;" onclick="showUpdateModal()">
        <span>Update available: <strong id="lblLatestVer"></strong></span>
      </div>
    </div>

    <div class="v-divider"></div>

    <div class="workspace-pill" id="btnWorkspacePill" onclick="openSettings('workspace')" title="Click to configure workspace folder">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>
      <span class="workspace-name" id="lblProjectName">Checking workspace...</span>
      <span class="dot" id="dotProject"></span>
    </div>

    <div class="preset-control">
      <label>Preset:</label>
      <select id="selPresetHeader" class="preset-select" onchange="switchPreset(this.value)"></select>
    </div>
  </div>

  <div class="header-right">
    <button class="btn" onclick="checkUpdates(true)" title="Check for release updates on GitHub">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
      <span>Check Updates</span>
    </button>
    <button class="btn" onclick="installUserscript()" title="Install or update browser userscript">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>
      <span>Userscript</span>
    </button>
    <div class="v-divider"></div>
    <button class="btn btn-studio" onclick="openAIStudio()" title="Open Google AI Studio in new tab">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
      <span>AI Studio</span>
    </button>
    <button class="btn btn-primary" onclick="openSettings('workspace')" title="Configure workspace directory and presets">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
      <span>Settings</span>
    </button>
  </div>
</header>

<main>
  <div id="setupBanner" class="setup-alert">
    <div><strong>Workspace Required:</strong> Set your local project folder to enable the coding agent bridge.</div>
    <button class="btn btn-primary" onclick="openSettings('workspace')">Set Workspace</button>
  </div>

  <div id="scriptOutdatedBanner" class="setup-alert" style="background:#201809; border-color:#d29922; display:none;">
    <div><strong>Tampermonkey Script Outdated:</strong> Extension is <span id="lblScriptVer"></span>, but bridge requires <span id="lblReqScriptVer"></span>.</div>
    <button class="btn btn-primary" onclick="installUserscript()">Update Script in Browser</button>
  </div>

  <div class="history-toolbar">
    <div class="history-heading">
      <h2 class="history-title">Transactions</h2>
      <span class="history-count" id="lblHistoryCount">0 batches</span>
    </div>
    <div class="history-actions">
      <button class="btn btn-ghost" id="btnCollapseToggle" onclick="toggleCollapseAll()">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 14 12 6 20 14"></polyline><polyline points="4 20 12 12 20 20"></polyline></svg>
        <span id="lblCollapseAll">Collapse All</span>
      </button>
      <button class="btn btn-danger-ghost" onclick="clearHistory()" title="Clear transaction history list without deleting files">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
        <span>Clear History</span>
      </button>
    </div>
  </div>

  <div id="historyContainer" style="display:flex; flex-direction:column; gap:16px;"></div>
</main>

<!-- Update Release Modal -->
<div id="updateModal" class="modal-overlay" onclick="if(event.target===this) document.getElementById('updateModal').style.display='none';">
  <div class="modal" style="width:560px;">
    <div style="padding:16px 20px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center;">
      <h3 style="color:#fff; font-size:14px; margin:0;">New Release Available</h3>
      <button class="btn btn-ghost" style="padding:4px 8px;" onclick="document.getElementById('updateModal').style.display='none'">✕</button>
    </div>
    <div style="padding:20px; display:flex; flex-direction:column; gap:12px; font-size:12.5px;">
      <p>A newer version of <strong>AI Studio Agent</strong> is available: <span id="modalVerBadge" style="color:var(--green); font-weight:bold;"></span> (installed: <span id="modalCurrentVer"></span>)</p>
      <div style="font-weight:600; color:var(--text-secondary); font-size:11.5px;">RELEASE NOTES:</div>
      <div id="modalChangelogBox" style="background:var(--bg-base); border:1px solid var(--border); border-radius:6px; padding:10px 14px; max-height:180px; overflow-y:auto; color:var(--text-primary); font-size:12px; white-space:pre-wrap; line-height:1.4;"></div>
      <p style="font-size:11.5px; color:var(--text-secondary);">
        Download the latest binary or script from GitHub. Your projects, presets, and history remain untouched in storage.
      </p>
    </div>
    <div style="display:flex; justify-content:flex-end; gap:8px; padding:14px 20px; border-top:1px solid var(--border); background:var(--bg-base);">
      <button class="btn" onclick="document.getElementById('updateModal').style.display='none'">Later</button>
      <button class="btn btn-primary" id="btnViewRelease" onclick="openReleasePage()">Download on GitHub</button>
    </div>
  </div>
</div>

<!-- Settings Modal -->
<div id="settingsModal" class="modal-overlay" onclick="if(event.target===this) closeSettings();">
  <div class="modal">
    <div class="modal-tabs" style="justify-content:space-between; align-items:center;">
      <div style="display:flex;">
        <button class="tab-btn" id="tabWorkspaceBtn" onclick="switchTab('workspace')">Workspace Config</button>
        <button class="tab-btn" id="tabPresetBtn" onclick="switchTab('preset')">Preset Editor</button>
      </div>
      <button class="btn btn-ghost" style="padding:6px 10px;" onclick="closeSettings()" title="Close (Esc)">✕</button>
    </div>
    
    <div class="modal-body" id="tabWorkspace">
      <div class="modal-row" id="rowRecentWorkspaces" style="display:none;">
        <label>Recent Workspaces:</label>
        <select id="selRecentWorkspaces" class="input-text" onchange="selectRecentWorkspace(this.value)"></select>
      </div>
      <div class="modal-row">
        <label>Project Workspace Directory:</label>
        <div class="input-with-btn">
          <input type="text" id="modalProjectPath" class="input-text" placeholder="Select local project folder..." oninput="syncRecentWithInput(this.value)">
          <button class="btn" onclick="browseFolder()">Browse</button>
        </div>
      </div>
      <div class="modal-row">
        <label>Syntax Validator Executable (optional, e.g. godot.exe):</label>
        <div class="input-with-btn">
          <input type="text" id="modalValidatorBin" class="input-text" placeholder="Optional executable path...">
          <button class="btn" onclick="browseFile()">Browse</button>
        </div>
      </div>
      <p style="font-size: 11px; color: var(--text-secondary); margin-top: 6px;">Storage location: <span id="lblStoragePath" style="color:var(--blue);"></span></p>
    </div>

    <div class="modal-body" id="tabPreset" style="display:none;">
      <div class="modal-row">
        <label>Select Preset to Edit or Clone:</label>
        <div class="input-with-btn">
          <select id="selPresetEditor" class="input-text" onchange="loadPresetToEditor(this.value)"></select>
          <button class="btn" onclick="createNewPreset()">+ New</button>
          <button class="btn" id="btnResetPreset" style="display:none;" onclick="resetCurrentBuiltinPreset()" title="Reset to default prompt">Reset to default</button>
          <button class="btn btn-danger-ghost" id="btnDeletePreset" onclick="deleteCurrentPreset()">Delete</button>
        </div>
      </div>
      <div class="modal-row">
        <label>Preset Display Name:</label>
        <input type="text" id="modalPresetName" class="input-text">
      </div>
      <div class="modal-row">
        <label>File Extensions (comma-separated, or * for all):</label>
        <input type="text" id="modalPresetExts" class="input-text" placeholder="* or .py, .ts, .gd, .rs">
      </div>
      <div class="modal-row">
        <label>Syntax Check Command (optional):</label>
        <input type="text" id="modalPresetValidatorCmd" class="input-text" placeholder="{bin} --headless --path {project_path} -s res://{file_path} --check-only or python -m py_compile {file_path}">
      </div>
      <div class="modal-row" style="flex-direction:row; align-items:center; gap:8px;">
        <input type="checkbox" id="modalPresetGodotSkeleton" style="cursor:pointer; width:16px; height:16px;">
        <label for="modalPresetGodotSkeleton" style="margin:0; cursor:pointer;">Use Godot GDScript Scene/Skeleton Awareness</label>
      </div>
      <div class="modal-row">
        <label>System Instructions Protocol:</label>
        <textarea id="modalSystemPrompt" class="input-text"></textarea>
      </div>
    </div>
    
    <div style="display: flex; justify-content: flex-end; gap: 8px; padding: 14px 24px; border-top: 1px solid var(--border); background: var(--bg-base); border-radius: 0 0 10px 10px;">
      <button class="btn" onclick="closeSettings()">Cancel</button>
      <button class="btn btn-primary" id="btnSaveModal" onclick="saveActiveModal()">Save Changes</button>
    </div>
  </div>
</div>

<div id="toast" class="toast">Ready</div>

<script>
  let isSettingsOpen = false;
  let toastTimeout = null;
  let remoteUserscriptUrl = 'https://raw.githubusercontent.com/jonson-zero/ai-studio-agent/main/agent.user.js';

  function parseSemver(verStr) {
    const cleaned = (verStr || '').replace(/^[vV]/, '').trim();
    const parts = cleaned.split('.').map(x => parseInt(x, 10) || 0);
    while (parts.length < 3) parts.push(0);
    return parts.slice(0, 3);
  }

  function isNewerVersion(remote, local) {
    const r = parseSemver(remote);
    const l = parseSemver(local);
    for (let i = 0; i < Math.max(r.length, l.length); i++) {
      const rv = r[i] || 0;
      const lv = l[i] || 0;
      if (rv > lv) return true;
      if (rv < lv) return false;
    }
    return false;
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function installUserscript() {
    window.open(remoteUserscriptUrl, '_blank');
  }

  function openAIStudio() {
    window.open('https://aistudio.google.com/', '_blank', 'noopener,noreferrer');
  }

  function showToast(msg, isError = false) {
    const t = document.getElementById('toast');
    if (!t) return;
    if (toastTimeout) clearTimeout(toastTimeout);
    t.textContent = msg;
    t.className = 'toast show' + (isError ? ' error' : '');
    toastTimeout = setTimeout(() => {
      t.className = 'toast';
    }, 2800);
  }

  let activeTab = 'workspace';
  let cachedPresets = {};
  let currentActivePresetId = 'default';
  let currentEditingPresetId = 'default';
  const collapsedTransactions = new Set();
  const collapsedFiles = new Set();
  let currentTransactionsCache = [];

  function renderMarkdown(rawText) {
    if (!rawText) return '';
    let html = rawText.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const codeBlocks = [];
    html = html.replace(/```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g, (m, lang, code) => {
      const id = `__CB_${codeBlocks.length}__`;
      codeBlocks.push(`<pre style="background:#080b0f;border:1px solid var(--border);padding:8px 12px;border-radius:6px;overflow-x:auto;margin:6px 0;font-family:monospace;font-size:11.5px;color:#e6edf3;"><code>${code.trim()}</code></pre>`);
      return id;
    });
    html = html.replace(/`([^`]+)`/g, '<code style="background:rgba(110,118,129,0.3);padding:2px 5px;border-radius:4px;font-family:monospace;font-size:11px;color:#f0f6fc;">$1</code>');
    html = html.replace(/^### (.*$)/gim, '<h4 style="margin:6px 0 2px;font-size:12.5px;color:var(--blue);">$1</h4>');
    html = html.replace(/^## (.*$)/gim, '<h3 style="margin:8px 0 3px;font-size:13.5px;color:#fff;">$1</h3>');
    html = html.replace(/^# (.*$)/gim, '<h2 style="margin:10px 0 4px;font-size:14.5px;color:#fff;">$1</h2>');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong style="color:#fff;">$1</strong>');
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/^> (.*$)/gim, '<blockquote style="border-left:3px solid var(--blue);margin:4px 0;padding:2px 10px;color:var(--text-secondary);">$1</blockquote>');
    html = html.replace(/^\s*[-*]\s+(.*$)/gim, '<li style="margin-left:16px;margin-bottom:2px;">$1</li>');
    html = html.replace(/\n/g, '<br>');
    html = html.replace(/(<\/li>)<br>/g, '$1');
    codeBlocks.forEach((block, idx) => {
      html = html.replace(`__CB_${idx}__`, block);
    });
    return html;
  }

  function normalizePath(p) {
    return (p || '').trim().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
  }

  function syncRecentWithInput(val) {
    const recentSel = document.getElementById('selRecentWorkspaces');
    if (!recentSel) return;
    const norm = normalizePath(val);
    const match = Array.from(recentSel.options).find(o => o.value && normalizePath(o.value) === norm);
    recentSel.value = match ? match.value : '';
  }

  function selectRecentWorkspace(val) {
    if (!val) return;
    document.getElementById('modalProjectPath').value = val;
    syncRecentWithInput(val);
  }

  function updateRecentWorkspacesDropdown(recentProjects, currentPath) {
    const recentRow = document.getElementById('rowRecentWorkspaces');
    const recentSel = document.getElementById('selRecentWorkspaces');
    if (!recentProjects || recentProjects.length === 0) {
      recentRow.style.display = 'none';
      return;
    }
    recentRow.style.display = 'flex';
    
    const serialized = JSON.stringify(recentProjects);
    if (recentSel.dataset.cache !== serialized) {
      recentSel.dataset.cache = serialized;
      const curSelected = recentSel.value || currentPath || '';
      recentSel.innerHTML = '<option value="">-- Select from recent workspaces --</option>' +
        recentProjects.map(p => `<option value="${p}">${p}</option>`).join('');
      syncRecentWithInput(curSelected);
    }
  }

  function toggleTransaction(id) {
    if (collapsedTransactions.has(id)) {
      collapsedTransactions.delete(id);
    } else {
      collapsedTransactions.add(id);
    }
    const body = document.getElementById(`trans-body-${id}`);
    const card = body ? body.closest('.trans-card') : null;
    const icon = card ? card.querySelector('.trans-meta .toggle-icon') : null;
    if (body) body.style.display = collapsedTransactions.has(id) ? 'none' : 'block';
    if (icon) icon.innerText = collapsedTransactions.has(id) ? '▶' : '▼';
  }

  function toggleFile(fKey) {
    if (collapsedFiles.has(fKey)) {
      collapsedFiles.delete(fKey);
    } else {
      collapsedFiles.add(fKey);
    }
    const diff = document.getElementById(`file-diff-${fKey}`);
    const card = diff ? diff.closest('.diff-file-card') : null;
    const icon = card ? card.querySelector('.diff-file-bar .toggle-icon') : null;
    if (diff) diff.style.display = collapsedFiles.has(fKey) ? 'none' : 'block';
    if (icon) icon.innerText = collapsedFiles.has(fKey) ? '▶' : '▼';
  }

  function toggleCollapseAll() {
    const shouldCollapse = collapsedTransactions.size < currentTransactionsCache.length;
    collapsedTransactions.clear();
    collapsedFiles.clear();
    if (shouldCollapse) {
      currentTransactionsCache.forEach((t, tIdx) => {
        collapsedTransactions.add(t.id);
        (t.files || []).forEach((_, fIdx) => collapsedFiles.add(`${t.id}_${fIdx}`));
      });
    }
    const lbl = document.getElementById('lblCollapseAll');
    if (lbl) lbl.innerText = shouldCollapse ? 'Expand All' : 'Collapse All';
    loadHistory();
  }

  let currentUpdateInfo = null;

  async function checkUpdates(manual = false) {
    try {
      const res = await fetch('/api/check_update' + (manual ? '?force=1' : ''));
      const info = await res.json();
      currentUpdateInfo = info;
      const badge = document.getElementById('badgeUpdate');
      if (info.has_update) {
        badge.style.display = 'inline-flex';
        document.getElementById('lblLatestVer').innerText = 'v' + info.latest_version;
        if (manual) showUpdateModal();
      } else {
        badge.style.display = 'none';
        if (manual) {
          if (info.error) {
            showToast(info.error, true);
          } else {
            showToast('You are on the latest version (v' + info.current_version + ')');
          }
        }
      }
    } catch(e) {
      if (manual) showToast('Failed to reach server for updates', true);
    }
  }

  function showUpdateModal() {
    if (!currentUpdateInfo) return;
    document.getElementById('modalVerBadge').innerText = 'v' + currentUpdateInfo.latest_version;
    document.getElementById('modalCurrentVer').innerText = 'v' + currentUpdateInfo.current_version;
    document.getElementById('modalChangelogBox').innerText = currentUpdateInfo.changelog || 'No release notes provided.';
    document.getElementById('updateModal').style.display = 'flex';
  }

  function openReleasePage() {
    const url = (currentUpdateInfo && currentUpdateInfo.release_url) ? currentUpdateInfo.release_url : 'https://github.com/jonson-zero/ai-studio-agent/releases/latest';
    window.open(url, '_blank');
  }

  function openSettings(tab = 'workspace') {
    isSettingsOpen = true;
    document.getElementById('settingsModal').style.display = 'flex';
    switchTab(tab);
    if (tab === 'preset') {
      loadPresetToEditor(currentEditingPresetId || currentActivePresetId);
    } else {
      syncRecentWithInput(document.getElementById('modalProjectPath').value);
    }
  }
  function closeSettings() {
    isSettingsOpen = false;
    document.getElementById('settingsModal').style.display = 'none';
  }

  function switchTab(tab) {
    activeTab = tab;
    document.getElementById('tabWorkspace').style.display = tab === 'workspace' ? 'flex' : 'none';
    document.getElementById('tabPreset').style.display = tab === 'preset' ? 'flex' : 'none';
    document.getElementById('tabWorkspaceBtn').className = 'tab-btn' + (tab === 'workspace' ? ' active' : '');
    document.getElementById('tabPresetBtn').className = 'tab-btn' + (tab === 'preset' ? ' active' : '');
  }

  async function browseFolder() {
    const btn = document.querySelector('button[onclick="browseFolder()"]');
    const oldText = btn ? btn.textContent : 'Browse';
    if (btn) { btn.textContent = 'Opening...'; btn.disabled = true; }
    try {
      const res = await fetch('/api/browse_folder', {method: 'POST'});
      const data = await res.json();
      if (data.path) {
        document.getElementById('modalProjectPath').value = data.path;
        syncRecentWithInput(data.path);
        showToast('Selected: ' + data.path);
      }
    } catch (e) {
      showToast('Browse error: ' + e.message, true);
    } finally {
      if (btn) { btn.textContent = oldText; btn.disabled = false; }
    }
  }

  async function browseFile() {
    const btn = document.querySelector('button[onclick="browseFile()"]');
    const oldText = btn ? btn.textContent : 'Browse';
    if (btn) { btn.textContent = 'Opening...'; btn.disabled = true; }
    try {
      const res = await fetch('/api/browse_file', {method: 'POST'});
      const data = await res.json();
      if (data.path) {
        document.getElementById('modalValidatorBin').value = data.path;
        showToast('Selected: ' + data.path);
      }
    } catch (e) {
      showToast('Browse error: ' + e.message, true);
    } finally {
      if (btn) { btn.textContent = oldText; btn.disabled = false; }
    }
  }

  async function switchPreset(presetId) {
    const res = await fetch('/api/set_preset', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({preset: presetId})
    });
    const data = await res.json();
    if (data.status === 'ok') {
      currentActivePresetId = presetId;
      showToast(`Active preset: ${presetId}`);
      await loadStatus();
    } else {
      alert(data.error || 'Failed to switch preset');
    }
  }

  function loadPresetToEditor(pid) {
    currentEditingPresetId = pid;
    const p = cachedPresets[pid] || {};
    document.getElementById('modalPresetName').value = p.name || pid;
    document.getElementById('modalPresetExts').value = Array.isArray(p.file_extensions) ? p.file_extensions.join(', ') : (p.file_extensions || '*');
    document.getElementById('modalPresetValidatorCmd').value = p.validator_cmd || '';
    document.getElementById('modalSystemPrompt').value = p.system_instruction || '';
    const chk = document.getElementById('modalPresetGodotSkeleton');
    if (chk) chk.checked = !!p.use_godot_skeleton;
    
    const isBuiltin = ['default', 'godot4'].includes(pid);
    const delBtn = document.getElementById('btnDeletePreset');
    if (delBtn) delBtn.disabled = isBuiltin;
    const resetBtn = document.getElementById('btnResetPreset');
    if (resetBtn) resetBtn.style.display = isBuiltin ? 'inline-flex' : 'none';
    
    const eSel = document.getElementById('selPresetEditor');
    if (eSel && eSel.value !== pid) {
      eSel.value = pid;
    }
  }

  async function resetCurrentBuiltinPreset() {
    if (!confirm(`Reset preset '${currentEditingPresetId}' to official built-in instructions? Any personal edits to this preset will be replaced.`)) return;
    const res = await fetch('/api/reset_preset', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: currentEditingPresetId})
    });
    const d = await res.json();
    if (d.status === 'ok') {
      showToast('Reset to official instructions');
      await loadStatus();
      loadPresetToEditor(currentEditingPresetId);
    } else {
      showToast('Error: ' + (d.error || 'Failed to reset'), true);
    }
  }

  async function createNewPreset() {
    const name = prompt('Enter new preset name (e.g. Python Agent):');
    if (!name || !name.trim()) return;
    const cleanName = name.trim();
    let pid = cleanName.toLowerCase().replace(/[^a-z0-9]/g, '_');
    if (!pid) pid = 'custom_' + Date.now();
    if (cachedPresets[pid]) {
      pid = pid + '_' + Math.floor(Math.random() * 1000);
    }
    const base = cachedPresets[currentEditingPresetId] || cachedPresets['default'] || {};
    const newPreset = {
      id: pid,
      name: cleanName,
      description: 'Custom coding agent preset',
      file_extensions: ['*'],
      validator_cmd: '',
      use_godot_skeleton: false,
      system_instruction: base.system_instruction || ''
    };
    await fetch('/api/save_preset', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({preset: newPreset})
    });
    showToast(`Created preset: ${cleanName}`);
    currentEditingPresetId = pid;
    await loadStatus();
    openSettings('preset');
    loadPresetToEditor(pid);
  }

  async function deleteCurrentPreset() {
    if (['default', 'godot4'].includes(currentEditingPresetId)) {
      alert('Cannot delete built-in presets.');
      return;
    }
    if (!confirm(`Delete preset '${currentEditingPresetId}'?`)) return;
    const res = await fetch('/api/delete_preset', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: currentEditingPresetId})
    });
    const data = await res.json();
    if (data.status === 'ok') {
      showToast('Preset deleted');
      currentEditingPresetId = 'default';
      await loadStatus();
      loadPresetToEditor('default');
    } else {
      alert(data.error || 'Failed to delete preset');
    }
  }

  async function saveActiveModal() {
    const btn = document.getElementById('btnSaveModal');
    const origText = btn ? btn.textContent : 'Save Changes';
    if (btn) { btn.textContent = 'Saving...'; btn.disabled = true; }
    try {
      if (activeTab === 'workspace') {
        const path = document.getElementById('modalProjectPath').value.trim();
        const bin = document.getElementById('modalValidatorBin').value.trim();
        const res = await fetch('/api/set_config', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({path, validator_bin: bin})
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'Failed to save config');
        showToast('Workspace settings saved');
      } else {
        const extsRaw = document.getElementById('modalPresetExts').value;
        const exts = extsRaw.split(',').map(s => s.trim()).filter(Boolean);
        const chk = document.getElementById('modalPresetGodotSkeleton');
        const p = {
          id: currentEditingPresetId,
          name: document.getElementById('modalPresetName').value.trim() || currentEditingPresetId,
          file_extensions: exts.length ? exts : ['*'],
          validator_cmd: document.getElementById('modalPresetValidatorCmd').value.trim(),
          use_godot_skeleton: chk ? chk.checked : false,
          system_instruction: document.getElementById('modalSystemPrompt').value.trim()
        };
        const res = await fetch('/api/save_preset', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({preset: p})
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'Failed to save preset');
        showToast('Preset saved');
      }
      closeSettings();
      await loadStatus();
    } catch (e) {
      showToast('Error: ' + e.message, true);
    } finally {
      if (btn) { btn.textContent = origText; btn.disabled = false; }
    }
  }

  async function clearHistory() {
    if (!confirm('Clear transaction history? Project files will not be affected.')) return;
    await fetch('/api/clear_history', {method: 'POST'});
    showToast('History cleared');
    loadHistory();
  }

  async function revertFile(transId, filePath) {
    if (!confirm(`Revert changes only for "${filePath}"?`)) return;
    const res = await fetch('/api/revert_file', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: transId, file_path: filePath})
    });
    const d = await res.json();
    if (d.status === 'ok') {
      showToast(`Reverted "${filePath}"`);
    } else {
      showToast('Error: ' + (d.error || 'Failed to revert file'), true);
    }
    loadHistory();
  }

  async function revertTransaction(id, isolated = true) {
    const idx = currentTransactionsCache.findIndex(t => t.id === id);
    const newerUnreverted = currentTransactionsCache.slice(0, idx).filter(t => !t.reverted).length;
    let confirmMsg = 'Revert changes in this transaction?';
    if (!isolated && newerUnreverted > 0) {
      confirmMsg = `CASCADE REVERT\n\nThere are ${newerUnreverted} newer transaction(s) applied after this one.\n\nProceed with cascading revert?`;
    } else if (isolated) {
      confirmMsg = `ISOLATED REVERT\n\nRevert only files changed in this specific request?`;
    }
    if (!confirm(confirmMsg)) return;
    const res = await fetch('/api/undo', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, isolated})
    });
    const d = await res.json();
    if (d.status === 'ok') {
      showToast(`Reverted ${d.reverted_count || 1} batch(es) cleanly`);
    } else {
      showToast('Error: ' + (d.error || 'Failed to revert'), true);
    }
    loadHistory();
  }

  function renderDiff(diffText) {
    if (!diffText || !diffText.trim()) return '<div style="padding:10px 14px; color:var(--text-secondary); font-style:italic;">New empty file or identical content</div>';
    const lines = diffText.split('\n');
    if (lines.length && lines[lines.length - 1] === '') lines.pop();

    return lines.map(l => {
      const esc = l.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      if (l.startsWith('@@') && l.includes('@@', 2)) {
        return `<div class="diff-row diff-line-hunk"><span class="diff-marker">@@</span><span class="diff-content">${esc}</span></div>`;
      }
      if (l.startsWith('--- ') || l.startsWith('+++ ')) {
        return `<div class="diff-row diff-line-header"><span class="diff-marker">#</span><span class="diff-content">${esc}</span></div>`;
      }
      if (l.startsWith('+')) {
        return `<div class="diff-row diff-line-add"><span class="diff-marker">+</span><span class="diff-content">${esc.substring(1)}</span></div>`;
      }
      if (l.startsWith('-')) {
        return `<div class="diff-row diff-line-del"><span class="diff-marker">-</span><span class="diff-content">${esc.substring(1)}</span></div>`;
      }
      const val = l.startsWith(' ') ? esc.substring(1) : esc;
      return `<div class="diff-row diff-line-context"><span class="diff-marker">&nbsp;</span><span class="diff-content">${val}</span></div>`;
    }).join('');
  }

  function populatePresetDropdowns(activeId) {
    const hSel = document.getElementById('selPresetHeader');
    const eSel = document.getElementById('selPresetEditor');
    const opts = Object.values(cachedPresets).map(p => 
      `<option value="${p.id}">${p.name || p.id}</option>`
    ).join('');
    
    hSel.innerHTML = opts;
    hSel.value = activeId;
    
    eSel.innerHTML = opts;
    if (cachedPresets[currentEditingPresetId]) {
      eSel.value = currentEditingPresetId;
    } else {
      eSel.value = activeId;
    }
  }

  async function loadStatus() {
    try {
      const res = await fetch('/api/status');
      const data = await res.json();
      cachedPresets = data.presets || {};
      currentActivePresetId = data.active_preset.id;
      if (data.userscript_url) remoteUserscriptUrl = data.userscript_url;
      document.getElementById('lblStoragePath').innerText = data.storage_dir;
      
      populatePresetDropdowns(data.active_preset.id);
      updateRecentWorkspacesDropdown(data.recent_projects, data.project_path);

      if (!isSettingsOpen) {
        document.getElementById('modalProjectPath').value = data.project_path || '';
        document.getElementById('modalValidatorBin').value = data.validator_bin || '';
        currentEditingPresetId = data.active_preset.id;
        loadPresetToEditor(data.active_preset.id);
        syncRecentWithInput(data.project_path);
      }

      const dot = document.getElementById('dotProject');
      const lbl = document.getElementById('lblProjectName');
      if (data.project_valid) {
        dot.className = 'dot dot-green';
        lbl.innerText = `${data.project_name}`;
        lbl.title = data.project_path;
        document.getElementById('setupBanner').style.display = 'none';
      } else {
        dot.className = 'dot dot-red';
        lbl.innerText = 'No workspace selected';
        lbl.title = 'Click to configure workspace folder';
        document.getElementById('setupBanner').style.display = 'flex';
      }

      const scriptBanner = document.getElementById('scriptOutdatedBanner');
      if (data.connected_script_version && data.min_userscript_version) {
        const isOutdated = isNewerVersion(data.min_userscript_version, data.connected_script_version);
        if (isOutdated) {
          scriptBanner.style.display = 'flex';
          document.getElementById('lblScriptVer').innerText = 'v' + data.connected_script_version;
          document.getElementById('lblReqScriptVer').innerText = 'v' + data.min_userscript_version;
        } else {
          scriptBanner.style.display = 'none';
        }
      } else {
        scriptBanner.style.display = 'none';
      }
    } catch (e) {
      console.error("Status load error:", e);
    }
  }

  async function loadHistory() {
    try {
      const res = await fetch('/api/history');
      if (!res.ok) return;
      const transactions = await res.json();
      if (!Array.isArray(transactions)) return;
      currentTransactionsCache = transactions;
      
      const countEl = document.getElementById('lblHistoryCount');
      if (countEl) countEl.innerText = `${transactions.length} ${transactions.length === 1 ? 'batch' : 'batches'}`;

      const container = document.getElementById('historyContainer');
      if (!transactions.length) {
        container.innerHTML = '<div style="text-align:center; padding:48px; color:var(--text-secondary); background:var(--bg-card); border:1px dashed var(--border); border-radius:8px;">No transactions recorded yet. Submit changes via Google AI Studio to inspect diffs here.</div>';
        return;
      }

      container.innerHTML = transactions.map(t => {
        const isTransCollapsed = collapsedTransactions.has(t.id);
        const idx = currentTransactionsCache.findIndex(x => x.id === t.id);
        const newerUnreverted = currentTransactionsCache.slice(0, idx).filter(x => !x.reverted).length;
        return `
        <div class="trans-card">
          <div class="trans-header" onclick="toggleTransaction('${t.id}')">
            <div class="trans-meta">
              <span class="toggle-icon">${isTransCollapsed ? '▶' : '▼'}</span>
              <span class="trans-badge ${t.reverted ? 'badge-reverted' : 'badge-applied'}">${t.reverted ? 'REVERTED' : 'APPLIED'}</span>
              <span class="trans-time">${t.timestamp}</span>
              <span class="stat-plus">+${t.total_additions || 0}</span>
              <span class="stat-minus">-${t.total_deletions || 0}</span>
              <span style="color:var(--text-secondary);">(${t.files.length} file${t.files.length === 1 ? '' : 's'})</span>
            </div>
            <div style="display:flex; align-items:center; gap:6px;" onclick="event.stopPropagation();">
              ${t.reverted ? '' : `
                <button class="btn btn-danger-ghost" style="padding:3px 8px; font-size:11.5px;" title="Revert only this single request without cascading" onclick="revertTransaction('${t.id}', true)">Revert Batch</button>
                ${newerUnreverted > 0 ? `<button class="btn btn-danger-ghost" style="padding:3px 8px; font-size:11.5px; opacity:0.85;" title="Cascade revert including newer requests" onclick="revertTransaction('${t.id}', false)">Cascade (${newerUnreverted + 1})</button>` : ''}
              `}
            </div>
          </div>
          <div id="trans-body-${t.id}" style="display: ${isTransCollapsed ? 'none' : 'block'};">
            ${t.ai_message ? `<div style="padding:10px 16px; background:var(--blue-bg); color:#79c0ff; border-bottom:1px solid var(--border); font-size:12.5px; line-height:1.45;"><strong>Model:</strong> ${renderMarkdown(t.ai_message)}</div>` : ''}
            <div class="diff-section">
              ${t.files.map((f, idx) => {
                const fKey = `${t.id}_${idx}`;
                const isFileCollapsed = collapsedFiles.has(fKey);
                return `
                <div class="diff-file-card">
                  <div class="diff-file-bar" onclick="toggleFile('${fKey}')">
                    <div style="display:flex; align-items:center; gap:8px;">
                      <span class="toggle-icon">${isFileCollapsed ? '▶' : '▼'}</span>
                      <span class="diff-badge ${f.status === 'DIR' || f.action === 'mkdir' ? 'diff-badge-dir' : f.status === 'A' ? 'diff-badge-a' : f.status === 'D' ? 'diff-badge-d' : 'diff-badge-m'}">${f.status === 'DIR' || f.action === 'mkdir' ? 'DIR' : f.status === 'A' ? 'NEW' : f.status === 'D' ? 'DEL' : 'MOD'}</span>
                      <span style="font-family:ui-monospace, monospace; font-weight:600; color:#e6edf3;">${escapeHtml(f.file_path)}</span>
                      ${f.comment ? `<span style="color:var(--text-secondary); font-size:11px;">// ${escapeHtml(f.comment)}</span>` : ''}
                    </div>
                    <div style="display:flex; align-items:center; gap:8px;">
                      <div class="trans-stats">
                        <span class="stat-plus">+${f.additions || 0}</span>
                        <span class="stat-minus">-${f.deletions || 0}</span>
                      </div>
                      ${f.reverted ? '<span style="font-size:10px; color:var(--red); font-weight:700;">REVERTED</span>' : `<button class="btn btn-danger-ghost" style="padding:2px 6px; font-size:11px;" onclick="event.stopPropagation(); revertFile('${t.id}', '${f.file_path}')">Revert File</button>`}
                    </div>
                  </div>
                  <div id="file-diff-${fKey}" class="diff-code" style="display: ${isFileCollapsed ? 'none' : 'block'};">
                    ${renderDiff(f.diff)}
                  </div>
                </div>
              `;
              }).join('')}
            </div>
          </div>
        </div>
      `;
      }).join('');
    } catch (e) {
      console.error("History load error:", e);
    }
  }

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (document.getElementById('settingsModal').style.display === 'flex') closeSettings();
      if (document.getElementById('updateModal').style.display === 'flex') document.getElementById('updateModal').style.display = 'none';
    }
    if (e.key === 'Enter' && isSettingsOpen && e.target && e.target.tagName === 'INPUT' && e.target.type === 'text') {
      saveActiveModal();
    }
  });

  loadStatus();
  loadHistory();
  checkUpdates(false);
  setInterval(() => { loadStatus(); loadHistory(); }, 3000);
</script>
</body>
</html>
"""

class AgentBridgeHandler(http.server.BaseHTTPRequestHandler):
    def _is_origin_allowed(self):
        origin = self.headers.get('Origin')
        if not origin:
            return True
        if origin in ALLOWED_ORIGINS:
            return True
        parsed = urllib.parse.urlparse(origin)
        return parsed.hostname in ('127.0.0.1', 'localhost')

    def _set_headers(self, status=200, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        origin = self.headers.get('Origin', '*')
        if self._is_origin_allowed():
            self.send_header('Access-Control-Allow-Origin', origin)
        else:
            self.send_header('Access-Control-Allow-Origin', 'null')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        if any(api in (args[0] if args else "") for api in ["/api/status", "/api/history", "/api/files"]):
            return
        super().log_message(format, *args)

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ["/", "/dashboard"]:
            self._set_headers(200, 'text/html; charset=utf-8')
            self.wfile.write(HTML_DASHBOARD.encode('utf-8'))
            return

        if path in ["/agent.user.js", "/AI-Studio-Agent.user.js"]:
            local_script = os.path.join(get_app_dir(), "agent.user.js")
            if os.path.exists(local_script):
                try:
                    with open(local_script, "rb") as f:
                        content = f.read()
                    self._set_headers(200, 'text/javascript; charset=utf-8')
                    self.wfile.write(content)
                    return
                except Exception:
                    pass
            self.send_response(302)
            self.send_header('Location', get_userscript_url())
            self.end_headers()
            return

        if not self._is_origin_allowed():
            self._set_headers(403)
            self.wfile.write(b'{"error":"Cross-Origin Forbidden"}')
            return

        if path == "/api/status":
            global last_known_script_ver
            params = urllib.parse.parse_qs(parsed.query)
            if "client_ver" in params and params["client_ver"]:
                last_known_script_ver = params["client_ver"][0]

            with state_lock:
                proj = config.get("project_path", "")
                preset = get_active_preset_data()
                all_presets = load_all_presets()
                data = {
                    "service": SERVICE_NAME,
                    "version": VERSION,
                    "min_userscript_version": MIN_USERSCRIPT_VERSION,
                    "connected_script_version": last_known_script_ver,
                    "port": ACTIVE_PORT,
                    "project_path": proj,
                    "project_name": os.path.basename(os.path.normpath(proj)) if proj else "",
                    "project_valid": bool(proj and os.path.exists(proj)),
                    "recent_projects": config.get("recent_projects", []),
                    "validator_bin": config.get("validator_bin", ""),
                    "official_repo": OFFICIAL_REPO,
                    "userscript_url": get_userscript_url(),
                    "storage_dir": STORAGE_DIR,
                    "active_preset": preset,
                    "presets": all_presets,
                    "has_update": update_cache.get("has_update", False),
                    "latest_version": update_cache.get("latest_version", VERSION),
                    "latest_ai_message": latest_ai_message
                }
            self._set_headers(200)
            self.wfile.write(json.dumps(data).encode('utf-8'))

        elif path == "/api/history":
            with state_lock:
                proj = config.get("project_path", "")
                hist_copy = load_workspace_history(proj)
            self._set_headers(200)
            self.wfile.write(json.dumps(hist_copy).encode('utf-8'))

        elif path == "/api/system_instruction":
            with state_lock:
                preset = get_active_preset_data()
                data = {
                    "preset_id": preset["id"],
                    "system_instruction": preset.get("system_instruction", ""),
                    "schema": DEFAULT_SCHEMA
                }
            self._set_headers(200)
            self.wfile.write(json.dumps(data).encode('utf-8'))

        elif path == "/api/check_update":
            params = urllib.parse.parse_qs(parsed.query)
            force = "force" in params
            info = check_github_update(force=force)
            resp = dict(info)
            resp["current_version"] = VERSION
            self._set_headers(200)
            self.wfile.write(json.dumps(resp).encode('utf-8'))

        elif path == "/api/project_skeleton":
            with state_lock:
                proj = config.get("project_path", "")
                preset = get_active_preset_data()
            skeleton = generate_project_skeleton(proj, preset)
            self._set_headers(200)
            self.wfile.write(json.dumps({"skeleton": skeleton}).encode('utf-8'))

        elif path == "/api/files":
            with state_lock:
                proj = config.get("project_path", "")
                preset = get_active_preset_data()
            files = get_project_files(proj, preset.get("file_extensions"))
            self._set_headers(200)
            self.wfile.write(json.dumps(files).encode('utf-8'))

        elif path == "/api/file_content":
            params = urllib.parse.parse_qs(parsed.query)
            rel_path = params.get("path", [""])[0]
            with state_lock:
                proj = config.get("project_path", "")

            if not is_safe_path(proj, rel_path):
                self._set_headers(403)
                self.wfile.write(b'{"error":"Forbidden path"}')
                return

            full_path = os.path.join(proj, rel_path)
            content, err = read_text_file_safe(full_path)
            if err:
                self._set_headers(200)
                self.wfile.write(json.dumps({"path": rel_path, "content": f"[{err}]"}).encode('utf-8'))
                return
            self._set_headers(200)
            self.wfile.write(json.dumps({"path": rel_path, "content": content or ""}).encode('utf-8'))
        else:
            self._set_headers(404)
            self.wfile.write(b'{"error":"Not Found"}')

    def do_POST(self):
        global latest_ai_message
        if not self._is_origin_allowed():
            self._set_headers(403)
            self.wfile.write(b'{"error":"Cross-Origin Forbidden"}')
            return

        parsed = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8')

        if parsed.path == "/api/browse_folder":
            folder = pick_folder_subprocess()
            self._set_headers(200)
            self.wfile.write(json.dumps({"path": folder}).encode('utf-8'))
            return

        if parsed.path == "/api/browse_file":
            fpath = pick_file_subprocess()
            self._set_headers(200)
            self.wfile.write(json.dumps({"path": fpath}).encode('utf-8'))
            return

        if parsed.path == "/api/set_preset":
            try:
                req = json.loads(post_data)
                target_pid = req.get("preset", "default")
                all_presets = load_all_presets()
                if target_pid not in all_presets:
                    self._set_headers(404)
                    self.wfile.write(b'{"error":"Preset not found"}')
                    return
                with state_lock:
                    config["active_preset"] = target_pid
                    save_config(config)
                self._set_headers(200)
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if parsed.path == "/api/reset_preset":
            try:
                req = json.loads(post_data)
                pid = req.get("id", "").strip().lower()
                if pid not in BUILTIN_PRESETS:
                    self._set_headers(400)
                    self.wfile.write(b'{"error":"Only built-in presets can be reset"}')
                    return
                pdata = dict(BUILTIN_PRESETS[pid])
                pdata["user_modified"] = False
                pf = os.path.join(PRESETS_DIR, f"{pid}.json")
                with open(pf, "w", encoding="utf-8") as f:
                    json.dump(pdata, f, ensure_ascii=False, indent=2)
                self._set_headers(200)
                self.wfile.write(json.dumps({"status":"ok", "preset": pdata}).encode('utf-8'))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if parsed.path == "/api/save_preset":
            try:
                req = json.loads(post_data)
                preset_data = req.get("preset") 
                if not preset_data or not preset_data.get("id"):
                    self._set_headers(400)
                    self.wfile.write(b'{"error":"Missing preset id"}')
                    return
                pid = os.path.basename(preset_data["id"].strip().lower())
                if not pid:
                    self._set_headers(400)
                    self.wfile.write(b'{"error":"Invalid preset id"}')
                    return
                existing = load_all_presets().get(pid, {})
                merged = {**existing, **preset_data, "id": pid}
                if pid in BUILTIN_PRESETS:
                    merged["user_modified"] = True
                
                raw_exts = merged.get("file_extensions", ["*"])
                if isinstance(raw_exts, list):
                    cleaned_exts = []
                    for e in raw_exts:
                        e_str = str(e).strip().lower()
                        if not e_str:
                            continue
                        if e_str != "*" and not e_str.startswith("."):
                            e_str = f".{e_str}"
                        cleaned_exts.append(e_str)
                    merged["file_extensions"] = cleaned_exts or ["*"]
                
                pf = os.path.join(PRESETS_DIR, f"{pid}.json")
                with open(pf, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)
                self._set_headers(200)
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if parsed.path == "/api/delete_preset":
            try:
                req = json.loads(post_data)
                pid = os.path.basename(req.get("id", "").strip().lower())
                if pid in BUILTIN_PRESETS:
                    self._set_headers(400)
                    self.wfile.write(b'{"error":"Cannot delete builtin preset"}')
                    return
                pf = os.path.join(PRESETS_DIR, f"{pid}.json")
                if os.path.exists(pf):
                    os.remove(pf)
                with state_lock:
                    if config.get("active_preset") == pid:
                        config["active_preset"] = "default"
                        save_config(config)
                self._set_headers(200)
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if parsed.path == "/api/set_config":
            try:
                req = json.loads(post_data)
                with state_lock:
                    if "path" in req:
                        new_p = req["path"].strip()
                        if new_p:
                            norm_p = os.path.normpath(new_p).replace("\\", "/")
                            try:
                                os.makedirs(norm_p, exist_ok=True)
                            except Exception as e:
                                print(f"[WARN] Could not create project folder '{norm_p}': {e}")
                            config["project_path"] = norm_p
                            add_recent_project(norm_p)
                        else:
                            config["project_path"] = ""
                    if "validator_bin" in req:
                        config["validator_bin"] = req["validator_bin"].strip()
                    save_config(config)

                    if "system_instruction" in req:
                        preset = get_active_preset_data()
                        preset["system_instruction"] = req["system_instruction"]
                        pf = os.path.join(PRESETS_DIR, f"{preset['id']}.json")
                        with open(pf, "w", encoding="utf-8") as f:
                            json.dump(preset, f, ensure_ascii=False, indent=2)

                self._set_headers(200)
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if parsed.path == "/api/open_release":
            release_url = update_cache.get("release_url") or f"https://github.com/{OFFICIAL_REPO}/releases/latest"
            webbrowser.open(release_url)
            self._set_headers(200)
            self.wfile.write(b'{"status":"ok"}')
            return

        if parsed.path == "/api/clear_history":
            with state_lock:
                proj = config.get("project_path", "")
                save_workspace_history(proj, [])
            self._set_headers(200)
            self.wfile.write(b'{"status":"ok"}')
            return

        if parsed.path in ["/api/undo", "/api/revert_transaction", "/api/revert_file"]:
            try:
                req = json.loads(post_data)
                trans_id = req.get("id")
                target_file_path = req.get("file_path")
                with state_lock:
                    proj = config.get("project_path", "")
                    if not proj or not os.path.exists(proj):
                        self._set_headers(400)
                        self.wfile.write(b'{"error":"Project path not valid"}')
                        return

                    w_hist = load_workspace_history(proj)
                    target_idx = -1
                    if trans_id:
                        for idx, t in enumerate(w_hist):
                            if t.get("id") == trans_id:
                                target_idx = idx
                                break

                    if target_file_path:
                        if target_idx == -1:
                            for idx, t in enumerate(w_hist):
                                if not t.get("reverted"):
                                    for f in t.get("files", []):
                                        if f.get("file_path") == target_file_path and not f.get("reverted"):
                                            target_idx = idx
                                            trans_id = t.get("id")
                                            break
                                if target_idx != -1:
                                    break

                        if target_idx == -1:
                            self._set_headers(404)
                            self.wfile.write(b'{"error":"No active transaction found for target file"}')
                            return

                        item = w_hist[target_idx]
                        matching_files = [f for f in item.get("files", []) if f.get("file_path") == target_file_path and not f.get("reverted")]
                        matched_file = matching_files[0] if matching_files else None

                        if not matched_file:
                            self._set_headers(400)
                            self.wfile.write(b'{"error":"File not found or already reverted"}')
                            return

                        if not is_safe_path(proj, matched_file["file_path"]):
                            self._set_headers(403)
                            self.wfile.write(b'{"error":"Security violation"}')
                            return

                        full_path = os.path.join(proj, matched_file["file_path"])
                        if matched_file.get("action") == "mkdir" or matched_file.get("status") == "DIR":
                            if os.path.isdir(full_path):
                                try:
                                    if not os.listdir(full_path):
                                        os.rmdir(full_path)
                                except Exception:
                                    pass
                        elif matched_file.get("action") == "delete" or matched_file.get("status") == "D":
                            if matched_file.get("old_content") is not None:
                                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                                with open(full_path, "w", encoding="utf-8") as file_out:
                                    file_out.write(matched_file["old_content"])
                        elif matched_file.get("old_content") is None:
                            if os.path.exists(full_path):
                                os.remove(full_path)
                                parent = os.path.dirname(full_path)
                                base_real = os.path.realpath(proj)
                                while parent and os.path.realpath(parent) != base_real:
                                    try:
                                        if not os.listdir(parent):
                                            os.rmdir(parent)
                                            parent = os.path.dirname(parent)
                                        else:
                                            break
                                    except Exception:
                                        break
                        else:
                            os.makedirs(os.path.dirname(full_path), exist_ok=True)
                            with open(full_path, "w", encoding="utf-8") as file_out:
                                file_out.write(matched_file["old_content"])

                        for f in matching_files:
                            f["reverted"] = True
                        if all(f.get("reverted") for f in item.get("files", [])):
                            item["reverted"] = True

                        save_workspace_history(proj, w_hist)
                        print(f"[POINT REVERT] File {target_file_path} in batch {trans_id}")
                        self._set_headers(200)
                        self.wfile.write(json.dumps({"status": "ok", "reverted_file": target_file_path, "target_id": trans_id}).encode('utf-8'))
                        return

                    isolated = req.get("isolated", False)
                    if isolated:
                        item = w_hist[target_idx]
                        if item.get("reverted"):
                            self._set_headers(400)
                            self.wfile.write(b'{"error":"Transaction already reverted"}')
                            return

                        for f in reversed(item.get("files", [])):
                            if f.get("reverted"):
                                continue
                            if not is_safe_path(proj, f["file_path"]):
                                continue
                            full_path = os.path.join(proj, f["file_path"])
                            if f.get("action") == "mkdir" or f.get("status") == "DIR":
                                if os.path.isdir(full_path):
                                    try:
                                        if not os.listdir(full_path):
                                            os.rmdir(full_path)
                                    except Exception:
                                        pass
                            elif f.get("action") == "delete" or f.get("status") == "D":
                                if f.get("old_content") is not None:
                                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                                    with open(full_path, "w", encoding="utf-8") as file_out:
                                        file_out.write(f["old_content"])
                            elif f.get("old_content") is None:
                                if os.path.exists(full_path):
                                    os.remove(full_path)
                                    parent = os.path.dirname(full_path)
                                    base_real = os.path.realpath(proj)
                                    while parent and os.path.realpath(parent) != base_real:
                                        try:
                                            if not os.listdir(parent):
                                                os.rmdir(parent)
                                                parent = os.path.dirname(parent)
                                            else:
                                                break
                                        except Exception:
                                            break
                            else:
                                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                                with open(full_path, "w", encoding="utf-8") as file_out:
                                    file_out.write(f["old_content"])
                            f["reverted"] = True

                        item["reverted"] = True
                        save_workspace_history(proj, w_hist)
                        print(f"[ISOLATED REVERT] Batch {item.get('id')} ({item.get('timestamp')})")
                        self._set_headers(200)
                        self.wfile.write(json.dumps({"status": "ok", "reverted_count": 1, "target_id": trans_id, "mode": "isolated"}).encode('utf-8'))
                        return

                    reverted_batches = 0
                    for i in range(target_idx + 1):
                        item = w_hist[i]
                        if item.get("reverted"):
                            continue

                        for f in reversed(item.get("files", [])):
                            if f.get("reverted"):
                                continue
                            if not is_safe_path(proj, f["file_path"]):
                                continue

                            full_path = os.path.join(proj, f["file_path"])
                            if f.get("action") == "mkdir" or f.get("status") == "DIR":
                                if os.path.isdir(full_path):
                                    try:
                                        if not os.listdir(full_path):
                                            os.rmdir(full_path)
                                    except Exception:
                                        pass
                            elif f.get("action") == "delete" or f.get("status") == "D":
                                if f.get("old_content") is not None:
                                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                                    with open(full_path, "w", encoding="utf-8") as file_out:
                                        file_out.write(f["old_content"])
                            elif f.get("old_content") is None:
                                if os.path.exists(full_path):
                                    os.remove(full_path)
                                    parent = os.path.dirname(full_path)
                                    base_real = os.path.realpath(proj)
                                    while parent and os.path.realpath(parent) != base_real:
                                        try:
                                            if not os.listdir(parent):
                                                os.rmdir(parent)
                                                parent = os.path.dirname(parent)
                                            else:
                                                break
                                        except Exception:
                                            break
                            else:
                                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                                with open(full_path, "w", encoding="utf-8") as file_out:
                                    file_out.write(f["old_content"])
                            f["reverted"] = True

                        item["reverted"] = True
                        reverted_batches += 1
                        print(f"[CASCADE REVERT] Batch {item.get('id')} ({item.get('timestamp')})")

                    save_workspace_history(proj, w_hist)
                    self._set_headers(200)
                    self.wfile.write(json.dumps({"status": "ok", "reverted_count": reverted_batches, "target_id": trans_id}).encode('utf-8'))
                    return
            except Exception as e:
                self._set_headers(500)
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if parsed.path == "/api/preview_changes":
            try:
                data = json.loads(post_data)
                changes = data.get("changes", [])
                with state_lock:
                    proj = config.get("project_path", "")
                if not proj or not os.path.exists(proj) or not changes:
                    self._set_headers(200)
                    self.wfile.write(b'{"files":[]}')
                    return

                preview_files = []
                simulated_files = {}
                for item in changes:
                    rel_p = clean_rel_path(item.get("file_path", ""))
                    if not rel_p or not is_safe_path(proj, rel_p):
                        continue
                    full_p = os.path.join(proj, rel_p)
                    if rel_p not in simulated_files:
                        old_c = None
                        if os.path.exists(full_p):
                            with open(full_p, "r", encoding="utf-8", errors="replace") as f:
                                old_c = normalize_newlines(f.read())
                        simulated_files[rel_p] = old_c

                    cur_c = simulated_files[rel_p]
                    act = item.get("action", "overwrite").lower()
                    cnt = normalize_newlines(item.get("content", ""))
                    srch = normalize_newlines(item.get("search", ""))
                    a_start = normalize_newlines(item.get("anchor_start", ""))
                    a_end = normalize_newlines(item.get("anchor_end", ""))
                    new_c = cur_c

                    if act == "mkdir":
                        preview_files.append({"file_path": rel_p, "additions": 0, "deletions": 0, "diff": f"# Directory: {rel_p}\n"})
                        continue
                    elif act == "delete":
                        old_lines = cur_c.splitlines(keepends=True) if cur_c else []
                        d_str = "".join(difflib.unified_diff(old_lines, [], fromfile=f"a/{rel_p}", tofile="/dev/null"))
                        preview_files.append({"file_path": rel_p, "additions": 0, "deletions": len(old_lines), "diff": d_str})
                        simulated_files[rel_p] = None
                        continue
                    elif act == "patch":
                        if cur_c is None:
                            continue
                        if a_start and a_end:
                            new_c, ok, _ = apply_range_patch(cur_c, a_start, a_end, cnt)
                        else:
                            new_c, ok, _ = apply_patch_smart(cur_c, srch, cnt)
                        if not ok:
                            new_c = cur_c
                    elif act == "append":
                        new_c = (cur_c + "\n" if cur_c else "") + cnt + "\n"
                    else:
                        new_c = cnt

                    old_l = cur_c.splitlines(keepends=True) if cur_c else []
                    new_l = new_c.splitlines(keepends=True) if new_c else []
                    d_str = "".join(difflib.unified_diff(old_l, new_l, fromfile=f"a/{rel_p}", tofile=f"b/{rel_p}"))
                    adds = sum(1 for l in d_str.splitlines() if l.startswith('+') and not l.startswith('+++'))
                    dels = sum(1 for l in d_str.splitlines() if l.startswith('-') and not l.startswith('---'))
                    preview_files.append({"file_path": rel_p, "action": act, "comment": item.get("comment", ""), "additions": adds, "deletions": dels, "diff": d_str})
                    simulated_files[rel_p] = new_c

                self._set_headers(200)
                self.wfile.write(json.dumps({"files": preview_files}).encode('utf-8'))
            except Exception as e:
                self._set_headers(500)
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if parsed.path not in ["/", "/api/apply_changes"]:
            self._set_headers(404)
            self.wfile.write(b'{"error":"Endpoint not found"}')
            return

        try:
            data = json.loads(post_data)
            thought_text = data.get("thought", "")
            new_msg = data.get("message_to_user", "")
            changes = data.get("changes", [])
            inspect_req = data.get("inspect_files", [])

            if thought_text:
                print(f"[THOUGHT] {thought_text}")

            with state_lock:
                latest_ai_message = new_msg
                proj = config.get("project_path", "")
                bin_path = config.get("validator_bin", "")
                preset = get_active_preset_data()

            if new_msg:
                print(f"[NOTE] {new_msg}")

            inspected_data = {}
            if inspect_req and proj and os.path.exists(proj):
                for rf in inspect_req:
                    rf_clean = clean_rel_path(rf)
                    if not is_safe_path(proj, rf_clean):
                        inspected_data[rf_clean] = "[Error: Unsafe file path]"
                        continue
                    full_p = os.path.join(proj, rf_clean)
                    c_text, c_err = read_text_file_safe(full_p)
                    if c_err:
                        inspected_data[rf_clean] = f"[{c_err}]"
                    else:
                        inspected_data[rf_clean] = c_text

            if not changes:
                self._set_headers(200)
                resp_payload = {"status": "ok", "updated": []}
                if inspected_data:
                    resp_payload["inspected_files"] = inspected_data
                self.wfile.write(json.dumps(resp_payload).encode('utf-8'))
                return

            updated = []
            errors = []
            syntax_warnings = []
            transaction_files = []
            total_additions = 0
            total_deletions = 0

            seen_ranges = []
            for item in changes:
                if item.get("action", "").lower() == "patch" and item.get("anchor_start") and item.get("anchor_end"):
                    astart = item.get("anchor_start").strip()
                    aend = item.get("anchor_end").strip()
                    fpath = item.get("file_path", "")
                    for (prev_path, prev_start, prev_end) in seen_ranges:
                        if prev_path == fpath and (astart == prev_end or aend == prev_start or astart == prev_start):
                            errors.append(f"{fpath}: REJECTED: Overlapping or adjacent range anchors detected between patches.")
                            self._set_headers(200)
                            self.wfile.write(json.dumps({"status": "ok", "updated": [], "errors": errors}).encode('utf-8'))
                            return
                    seen_ranges.append((fpath, astart, aend))

            if not proj or not os.path.exists(proj):
                print("[ERROR] Project path not configured")
                self._set_headers(400)
                self.wfile.write(b'{"error":"Project path is not configured"}')
                return

            confirm_del = data.get("confirm_delete", False)
            sim_ok, sim_errs = simulate_changes_batch(proj, changes, confirm_del)
            if not sim_ok:
                resp_payload = {
                    "status": "error",
                    "updated": [],
                    "errors": [
                        "ATOMIC TRANSACTION FAILED: No files were modified on disk. All files remain in their original state."
                    ] + sim_errs,
                    "warnings": []
                }
                if inspected_data:
                    resp_payload["inspected_files"] = inspected_data
                self._set_headers(200)
                self.wfile.write(json.dumps(resp_payload).encode('utf-8'))
                return

            for item in changes:
                action = item.get("action", "overwrite").lower()
                rel_path = clean_rel_path(item.get("file_path", ""))
                content = normalize_newlines(item.get("content", ""))
                search = normalize_newlines(item.get("search", ""))
                anchor_start = normalize_newlines(item.get("anchor_start", ""))
                anchor_end = normalize_newlines(item.get("anchor_end", ""))
                comment = item.get("comment", "")

                if not rel_path:
                    continue

                if not is_safe_path(proj, rel_path):
                    errors.append(f"{rel_path}: Security violation")
                    continue

                full_path = os.path.join(proj, rel_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)

                old_content = None
                is_new_file = not os.path.exists(full_path)
                if not is_new_file:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        old_content = normalize_newlines(f.read())

                dir_path = os.path.dirname(rel_path).replace("\\", "/")

                if action == "mkdir":
                    dir_created = not os.path.exists(full_path)
                    os.makedirs(full_path, exist_ok=True)
                    transaction_files.append({
                        "file_path": rel_path,
                        "dir_path": rel_path,
                        "file_name": os.path.basename(rel_path),
                        "ext": "",
                        "status": "DIR",
                        "action": "mkdir",
                        "comment": comment or "Created directory",
                        "old_content": None,
                        "diff": f"# Directory created: {rel_path}\n" if dir_created else f"# Directory already exists: {rel_path}\n",
                        "additions": 0,
                        "deletions": 0,
                        "syntax_error": None,
                        "reverted": False
                    })
                    updated.append(rel_path)
                    print(f"[APPLY] [MKDIR] {rel_path} ({comment})")
                    continue

                if action == "delete":
                    if not item.get("confirmed", False) and not data.get("confirm_delete", False):
                        errors.append(f"{rel_path}: Deletion blocked (user confirmation required)")
                        print(f"[BLOCKED] Deletion of {rel_path} without explicit user confirmation")
                        continue
                    if old_content is None:
                        continue
                    old_lines = old_content.splitlines(keepends=True)
                    diff = "".join(difflib.unified_diff(old_lines, [], fromfile=f"a/{rel_path}", tofile="/dev/null"))
                    deletions = len(old_lines)
                    total_deletions += deletions

                    os.remove(full_path)
                    parent = os.path.dirname(full_path)
                    base_real = os.path.realpath(proj)
                    while parent and os.path.realpath(parent) != base_real:
                        try:
                            if not os.listdir(parent):
                                os.rmdir(parent)
                                parent = os.path.dirname(parent)
                            else:
                                break
                        except Exception:
                            break

                    transaction_files.append({
                        "file_path": rel_path,
                        "dir_path": dir_path,
                        "file_name": os.path.basename(rel_path),
                        "ext": os.path.splitext(rel_path)[1].lower(),
                        "status": "D",
                        "action": "delete",
                        "comment": comment or "Deleted file",
                        "old_content": old_content,
                        "diff": diff,
                        "additions": 0,
                        "deletions": deletions,
                        "syntax_error": None,
                        "reverted": False
                    })
                    updated.append(rel_path)
                    print(f"[APPLY] [DELETE] {rel_path} ({comment})")
                    continue

                new_content = ""
                if action == "patch":
                    if not content:
                        errors.append(f"{rel_path}: Patch rejected because 'content' is missing or empty.")
                        continue
                    if old_content is None:
                        errors.append(f"{rel_path}: File does not exist to patch")
                        continue
                    if anchor_start and anchor_end:
                        new_content, success, diag = apply_range_patch(old_content, anchor_start, anchor_end, content)
                    else:
                        new_content, success, diag = apply_patch_smart(old_content, search, content)
                    if not success:
                        print(f"[FAIL] Target patch failed in {rel_path}: {diag}")
                        errors.append(f"{rel_path}: {diag}")
                        continue
                elif action == "append":
                    new_content = (old_content + "\n" if old_content else "") + content + "\n"
                else:
                    new_content = content

                old_lines = old_content.splitlines(keepends=True) if old_content else []
                new_lines = new_content.splitlines(keepends=True)
                diff = "".join(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}"))

                additions = sum(1 for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++'))
                deletions = sum(1 for l in diff.splitlines() if l.startswith('-') and not l.startswith('---'))

                total_additions += additions
                total_deletions += deletions

                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(new_content)

                err = run_syntax_validator(proj, rel_path, preset, bin_path)
                if err:
                    syntax_warnings.append(f"{rel_path}: {err}")
                    print(f"[WARN] Validation: {rel_path} - {err}")

                status_code = "A" if is_new_file else "M"
                dir_path = os.path.dirname(rel_path).replace("\\", "/")

                transaction_files.append({
                    "change_index": len(transaction_files),
                    "file_path": rel_path,
                    "dir_path": dir_path,
                    "file_name": os.path.basename(rel_path),
                    "ext": os.path.splitext(rel_path)[1].lower(),
                    "status": status_code,
                    "action": action,
                    "comment": comment,
                    "old_content": old_content,
                    "diff": diff,
                    "additions": additions,
                    "deletions": deletions,
                    "syntax_error": err,
                    "reverted": False
                })

                updated.append(rel_path)
                print(f"[APPLY] [{action.upper()}] {rel_path} ({comment})")

            trans_entry = None
            if transaction_files:
                entry_id = str(int(time.time() * 1000))
                with state_lock:
                    w_hist = load_workspace_history(proj)
                    trans_entry = {
                        "id": entry_id,
                        "project_path": proj,
                        "timestamp": time.strftime("%H:%M:%S"),
                        "date": time.strftime("%Y-%m-%d"),
                        "ai_message": new_msg,
                        "total_additions": total_additions,
                        "total_deletions": total_deletions,
                        "reverted": False,
                        "files": transaction_files
                    }
                    w_hist.insert(0, trans_entry)
                    save_workspace_history(proj, w_hist)

            resp_payload = {
                "status": "ok",
                "updated": updated,
                "errors": errors,
                "warnings": syntax_warnings
            }
            if trans_entry:
                resp_payload["transaction"] = trans_entry
            if inspected_data:
                resp_payload["inspected_files"] = inspected_data
            self._set_headers(200)
            self.wfile.write(json.dumps(resp_payload).encode('utf-8'))

        except Exception as e:
            print(f"[ERROR] Server Exception: {e}")
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

def create_server(target_port):
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    while True:
        try:
            srv = http.server.ThreadingHTTPServer(('127.0.0.1', target_port), AgentBridgeHandler)
            return srv, target_port
        except OSError as e:
            if e.errno in (98, 10048) or "already in use" in str(e).lower():
                print(f"\n[WARNING] Port {target_port} is already in use by another process.")
                is_interactive = hasattr(sys.stdin, 'isatty') and sys.stdin.isatty()
                if not is_interactive:
                    target_port += 1
                    print(f"[INFO] Non-interactive mode: auto-probing port {target_port}...")
                    continue
                try:
                    ans = input(f"Enter alternative port, or Enter to try {target_port + 1} (Ctrl+C to exit): ").strip()
                    target_port = int(ans) if ans else (target_port + 1)
                except (KeyboardInterrupt, EOFError):
                    print("\nServer startup cancelled.")
                    sys.exit(0)
                except ValueError:
                    print("[ERROR] Invalid port number. Please enter a valid integer.")
            else:
                raise e

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=f"{APP_DISPLAY_NAME} Server")
    parser.add_argument("--port", type=int, help="Override server port")
    parser.add_argument("--project", type=str, help="Override workspace project path")
    parser.add_argument("--preset", type=str, help="Override active preset (e.g. godot4, default)")
    args = parser.parse_args()

    preferred_port = args.port or config.get("port", DEFAULT_PORT)
    if args.project:
        config["project_path"] = args.project
    if config.get("project_path"):
        add_recent_project(config["project_path"])
        save_config(config)
    if args.preset:
        config["active_preset"] = args.preset

    server, ACTIVE_PORT = create_server(preferred_port)

    preset_name = get_active_preset_data().get("name", "Default")
    print("-" * 60)
    print(f"{APP_DISPLAY_NAME} v{VERSION} [ONLINE]")
    print(f"Address:  http://127.0.0.1:{ACTIVE_PORT}")
    print(f"Preset:   {preset_name}")
    print(f"Storage:  {STORAGE_DIR}")
    print("-" * 60)

    threading.Thread(target=check_github_update, daemon=True).start()

    if not config.get("project_path"):
        print("[SETUP] Project path is not set. Opening dashboard...")
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{ACTIVE_PORT}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        sys.exit(0)