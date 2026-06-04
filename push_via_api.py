#!/usr/bin/env python3
"""Push git commit via GitHub API through Maton proxy."""

import base64
import json
import os
import subprocess
import sys
import urllib.request

API_KEY = os.environ.get("MATON_API_KEY", "v2.Gs5DQebwmjbpDqdzFtH55KGw7XL_qtep9p9ZQ-3kBQB76L5MdvD0uECj-HdhyePG3PRLJa_XNcFewluT3JHptE86LlvwJwTxfOs0JX-jY2T4HJMYuOo6Tsad")
REPO = "Sawera557/Flashclaw-Dashboard"
BASE_URL = f"https://api.maton.ai/github/repos/{REPO}"

def gh_api(method, path, data=None):
    """Call GitHub API through Maton proxy."""
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/vnd.github.v3+json")
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        print(f"HTTP {e.code} on {method} {path}: {err_body}", file=sys.stderr)
        sys.exit(1)

def file_content(filepath):
    """Read file content from working tree."""
    with open(filepath, "rb") as f:
        return f.read()

def git_show(sha):
    """Get the git object content for a blob."""
    result = subprocess.run(
        ["git", "show", sha],
        capture_output=True, text=True,
        cwd="/root/.openclaw/workspace/flashclaw-dashboard"
    )
    return result.stdout.encode() if result.returncode == 0 else result.stderr.encode()

def create_blob(content):
    """Create a blob via GitHub API."""
    encoded = base64.b64encode(content).decode()
    return gh_api("POST", "/git/blobs", {
        "content": encoded,
        "encoding": "base64"
    })

def create_tree(entries):
    """Create a tree via GitHub API."""
    return gh_api("POST", "/git/trees", {"tree": entries})

def create_commit(message, tree_sha, parent_sha):
    """Create a commit via GitHub API."""
    return gh_api("POST", "/git/commits", {
        "message": message,
        "tree": tree_sha,
        "parents": [parent_sha]
    })

def create_ref(ref, sha):
    """Create a reference (branch) via GitHub API."""
    return gh_api("POST", "/git/refs", {
        "ref": f"refs/heads/{ref}",
        "sha": sha
    })

# Step 1: Get all files from git workspace
print("Getting file tree from local commit...")
result = subprocess.run(
    ["git", "ls-tree", "-r", "HEAD"],
    capture_output=True, text=True,
    cwd="/root/.openclaw/workspace/flashclaw-dashboard"
)
entries_raw = result.stdout.strip().split("\n")

# Step 2: Create blobs for all files
print("Creating blobs...")
tree_entries = []
for line in entries_raw:
    if not line:
        continue
    parts = line.split()
    mode = parts[0]
    obj_type = parts[1]
    obj_sha = parts[2]
    path = parts[3]
    
    if obj_type == "commit":  # submodule
        continue
    
    content = git_show(obj_sha)
    blob = create_blob(content)
    blob_sha = blob["sha"]
    print(f"  {path} -> {blob_sha[:8]}...")
    tree_entries.append({
        "path": path,
        "mode": mode,
        "type": "blob",
        "sha": blob_sha
    })

# Step 3: Create tree
print("Creating tree...")
tree = create_tree(tree_entries)
tree_sha = tree["sha"]
print(f"  Tree SHA: {tree_sha}")

# Step 4: Get the commit message
commit_msg = subprocess.run(
    ["git", "log", "-1", "--format=%B", "HEAD"],
    capture_output=True, text=True,
    cwd="/root/.openclaw/workspace/flashclaw-dashboard"
).stdout.strip()

# Step 5: Parent SHA - use the remote LinkedIn-tracker
PARENT_SHA = "8af41b4b5b36b29fca7b74dbebd3b1367d62b243"

# Step 6: Create commit
print(f"Creating commit (parent: {PARENT_SHA[:8]}...)")
commit = create_commit(commit_msg, tree_sha, PARENT_SHA)
commit_sha = commit["sha"]
print(f"  Commit SHA: {commit_sha}")

# Step 7: Create branch reference
print("Creating branch 'major-issues-resolved'...")
try:
    ref = create_ref("major-issues-resolved", commit_sha)
    print(f"  Branch created at ref: {ref['ref']}")
    print(f"\n✅ Success! Pushed to https://github.com/{REPO}/tree/major-issues-resolved")
except SystemExit as e:
    # Maybe branch already exists? Try updating it
    print("  Branch may already exist, trying to update it...")
    try:
        # Delete old ref first
        req = urllib.request.Request(
            f"{BASE_URL}/git/refs/heads/major-issues-resolved",
            method="DELETE"
        )
        req.add_header("Authorization", f"Bearer {API_KEY}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        urllib.request.urlopen(req)
        # Create new ref
        ref = create_ref("major-issues-resolved", commit_sha)
        print(f"  Branch updated: {ref['ref']}")
        print(f"\n✅ Success! Pushed to https://github.com/{REPO}/tree/major-issues-resolved")
    except Exception as e2:
        print(f"  Error: {e2}", file=sys.stderr)
        sys.exit(1)
