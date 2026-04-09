#!/usr/bin/env python3
"""
Tailscale Device Manager
------------------------
Detect Tailscale peers, then SSH / scan / copy / replace / run .py files.
"""

import json
import os
import subprocess
import sys
import shutil
from pathlib import Path
from typing import Optional

# ── Local folder where remote copies are stored ──────────────────────────────
LOCAL_COPIES_ROOT = Path.home() / "tailscale_copies"

# ── Colours ──────────────────────────────────────────────────────────────────
C_HEADER  = "\033[96m"
C_GOOD    = "\033[92m"
C_WARN    = "\033[93m"
C_ERR     = "\033[91m"
C_DIM     = "\033[2m"
C_BOLD    = "\033[1m"
C_RESET   = "\033[0m"

def hdr(text: str) -> str:
    return f"{C_HEADER}{C_BOLD}{text}{C_RESET}"

def good(text: str) -> str:
    return f"{C_GOOD}{text}{C_RESET}"

def warn(text: str) -> str:
    return f"{C_WARN}{text}{C_RESET}"

def err(text: str) -> str:
    return f"{C_ERR}{text}{C_RESET}"

def dim(text: str) -> str:
    return f"{C_DIM}{text}{C_RESET}"

def banner(title: str) -> None:
    width = 60
    print(f"\n{C_HEADER}{'─' * width}")
    print(f"  {C_BOLD}{title}{C_RESET}{C_HEADER}")
    print(f"{'─' * width}{C_RESET}\n")

def prompt(text: str) -> str:
    return input(f"{C_BOLD}{text}{C_RESET} ").strip()

# ── Tailscale ─────────────────────────────────────────────────────────────────
def check_tailscale() -> bool:
    return shutil.which("tailscale") is not None

def get_tailscale_peers() -> list[dict]:
    """Return list of online Tailscale peers with name, ip, os."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    peers = []
    for node_id, info in data.get("Peer", {}).items():
        if not info.get("Online", False):
            continue
        name = info.get("HostName") or info.get("DNSName", "unknown").split(".")[0]
        ips  = info.get("TailscaleIPs", [])
        ip   = ips[0] if ips else "n/a"
        os_  = info.get("OS", "?")
        peers.append({"name": name, "ip": ip, "os": os_, "id": node_id})

    # Also include self for completeness
    self_info = data.get("Self", {})
    if self_info:
        self_ips = self_info.get("TailscaleIPs", [])
        peers.insert(0, {
            "name": self_info.get("HostName", "self") + dim(" (this device)"),
            "ip"  : self_ips[0] if self_ips else "127.0.0.1",
            "os"  : self_info.get("OS", "?"),
            "id"  : "self",
        })

    return peers

def list_devices(peers: list[dict]) -> None:
    banner("Tailscale Devices")
    fmt = f"  {{idx:>2}}. {{name:<28}} {{ip:<18}} {{os}}"
    print(dim(fmt.format(idx="#", name="Hostname", ip="Tailscale IP", os="OS")))
    print(dim("  " + "─" * 56))
    for i, p in enumerate(peers, 1):
        print(fmt.format(idx=i, name=p["name"], ip=p["ip"], os=p["os"]))
    print()

def pick_device(peers: list[dict]) -> Optional[dict]:
    list_devices(peers)
    choice = prompt("Select device number (or 'q' to quit):")
    if choice.lower() == "q":
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(peers):
            return peers[idx]
        print(err("  Invalid selection."))
    except ValueError:
        print(err("  Please enter a number."))
    return None

# ── SSH helpers ───────────────────────────────────────────────────────────────
def build_ssh_base(device: dict, user: str = "") -> list[str]:
    host = device["ip"]
    target = f"{user}@{host}" if user else host
    return [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=8",
        target,
    ]

def ssh_interactive(device: dict, user: str) -> None:
    """Drop into a live SSH shell; returns to menu when session ends."""
    cmd = build_ssh_base(device, user)
    print(good(f"\n  Connecting to {device['name']} ({device['ip']}) …\n"))
    subprocess.call(cmd)
    print(dim(f"\n  SSH session ended — back in device menu.\n"))

def ssh_run(device: dict, user: str, remote_cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    """Run a single command over SSH, return (rc, stdout, stderr)."""
    cmd = build_ssh_base(device, user) + [remote_cmd]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()

def ssh_stream(device: dict, user: str, remote_cmd: str) -> int:
    """Run a remote command and stream its output live; return exit code."""
    cmd = build_ssh_base(device, user) + [remote_cmd]
    proc = subprocess.Popen(cmd)
    proc.wait()
    return proc.returncode

# ── Feature 2: Scan ───────────────────────────────────────────────────────────
def scan_device(device: dict, user: str) -> None:
    """Print the remote directory structure (non-hidden, depth ≤ 5)."""
    root_input = prompt("Remote root path to scan [~]:")
    root = root_input if root_input else "~"
    depth_input = prompt("Max depth [4]:")
    try:
        depth = int(depth_input)
    except ValueError:
        depth = 4

    print(warn(f"\n  Scanning {root} on {device['name']} (depth {depth}) …\n"))

    # Use 'tree' if available, else fall back to 'find' formatted as a tree
    rc, out, _ = ssh_run(
        device, user,
        f"command -v tree >/dev/null 2>&1 && "
        f"tree -L {depth} --noreport -a --ignore-case {root} 2>/dev/null || "
        f"find {root} -maxdepth {depth} -not -path '*/\\.*' 2>/dev/null | sort",
        timeout=30,
    )

    if not out:
        print(warn("  Nothing returned — check the path and SSH access."))
        return

    lines = out.splitlines()
    print(dim(f"  {'─' * 56}"))
    for line in lines[:500]:               # cap at 500 lines in terminal
        print(f"  {line}")
    if len(lines) > 500:
        print(warn(f"\n  … {len(lines) - 500} more lines truncated. Use Copy to get full tree."))
    print(dim(f"  {'─' * 56}"))
    print(good(f"\n  {len(lines)} entries listed.\n"))

# ── Feature 3: Copy ───────────────────────────────────────────────────────────
def copy_device(device: dict, user: str) -> None:
    """Rsync entire remote home (or chosen path) to LOCAL_COPIES_ROOT/<device>."""
    root_input = prompt("Remote path to copy [~]:")
    remote_root = root_input if root_input else "~"

    safe_name = device["name"].replace(" ", "_").replace(dim(""), "")
    # Strip ANSI codes from name for folder use
    import re as _re
    safe_name = _re.sub(r'\x1b\[[0-9;]*m', '', safe_name)
    local_dest = LOCAL_COPIES_ROOT / safe_name
    local_dest.mkdir(parents=True, exist_ok=True)

    host_target = f"{user}@{device['ip']}"
    print(warn(f"\n  Copying {remote_root} → {local_dest} …\n"))

    if shutil.which("rsync"):
        cmd = [
            "rsync", "-avz", "--progress",
            "--exclude='.git'", "--exclude='__pycache__'", "--exclude='*.pyc'",
            "-e", "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8",
            f"{host_target}:{remote_root}/",
            str(local_dest) + "/",
        ]
    else:
        # Fallback: scp recursive
        cmd = [
            "scp", "-r",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=8",
            f"{host_target}:{remote_root}/.",
            str(local_dest) + "/",
        ]

    rc = subprocess.call(cmd)
    if rc == 0:
        print(good(f"\n  Copy complete → {local_dest}\n"))
    else:
        print(err(f"\n  Copy failed (rc={rc})\n"))

# ── Feature 4: Replace ────────────────────────────────────────────────────────
def replace_file(device: dict, user: str) -> None:
    """Push a file from the local copy folder back to the device."""
    import re as _re
    safe_name = _re.sub(r'\x1b\[[0-9;]*m', '', device["name"]).replace(" ", "_")
    local_root = LOCAL_COPIES_ROOT / safe_name

    if not local_root.exists():
        print(warn(f"\n  No local copy found at {local_root}"))
        print(dim("  Run option 3 (Copy) first.\n"))
        return

    # Walk local copy and collect all files
    all_files = sorted(
        p for p in local_root.rglob("*") if p.is_file()
    )

    if not all_files:
        print(warn("  Local copy folder is empty.\n"))
        return

    # Optional filter
    filt = prompt("Filter by name (leave blank for all):").lower()
    matches = [f for f in all_files if filt in f.name.lower()] if filt else all_files

    if not matches:
        print(warn("  No matching files.\n"))
        return

    print(f"\n  {C_BOLD}Local files in {local_root.name}/{C_RESET}")
    print(dim(f"  {'─' * 56}"))
    for i, f in enumerate(matches, 1):
        rel = f.relative_to(local_root)
        print(f"  {i:>3}. {rel}")
    print()

    sel = prompt("Select file number to push to device:")
    try:
        chosen = matches[int(sel) - 1]
    except (ValueError, IndexError):
        print(err("  Invalid selection.\n"))
        return

    rel_path = chosen.relative_to(local_root)
    # The remote path mirrors the local structure
    # local_root corresponds to whatever remote_root was — assume ~
    remote_path = f"~/{rel_path}"

    print(warn(f"\n  Will replace {remote_path} on {device['name']}"))
    confirm = prompt("Proceed? (y/n):")
    if confirm.lower() != "y":
        print(dim("  Cancelled.\n"))
        return

    host_target = f"{user}@{device['ip']}"
    if shutil.which("rsync"):
        cmd = [
            "rsync", "-avz",
            "-e", "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8",
            str(chosen),
            f"{host_target}:{remote_path}",
        ]
    else:
        cmd = [
            "scp",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=8",
            str(chosen),
            f"{host_target}:{remote_path}",
        ]

    rc = subprocess.call(cmd)
    if rc == 0:
        print(good(f"\n  Replaced {remote_path} on {device['name']}\n"))
    else:
        print(err(f"\n  Transfer failed (rc={rc})\n"))

# ── Feature 5: List .py files ─────────────────────────────────────────────────
def list_py_files(device: dict, user: str) -> list[str]:
    """Find all .py files on the remote device and return as a list."""
    root_input = prompt("Search from path [~]:")
    root = root_input if root_input else "~"
    filt = prompt("Name filter (leave blank for all):").lower()

    print(warn(f"\n  Searching for .py files on {device['name']} …\n"))
    rc, out, _ = ssh_run(
        device, user,
        f"find {root} -name '*.py' -not -path '*/\\.*' 2>/dev/null | sort",
        timeout=60,
    )

    if not out:
        print(warn("  No .py files found.\n"))
        return []

    files = [l for l in out.splitlines() if l]
    if filt:
        files = [f for f in files if filt in f.lower()]

    print(f"  {C_BOLD}Python files on {device['name']}{C_RESET}")
    print(dim(f"  {'─' * 56}"))
    for i, f in enumerate(files, 1):
        print(f"  {i:>3}. {f}")
    print(good(f"\n  {len(files)} file(s) found.\n"))
    return files

# ── Feature 6: Run .py ────────────────────────────────────────────────────────
def run_py_file(device: dict, user: str) -> None:
    """Select a remote .py file and run it, streaming output to terminal."""
    files = list_py_files(device, user)
    if not files:
        return

    sel = prompt("Select file number to run (or 'b' to cancel):")
    if sel.lower() == "b":
        return
    try:
        chosen = files[int(sel) - 1]
    except (ValueError, IndexError):
        print(err("  Invalid selection.\n"))
        return

    args = prompt("Arguments (leave blank for none):")
    cmd_str = f"python3 {chosen} {args}".strip()

    print(warn(f"\n  Running: {cmd_str}\n"))
    print(dim(f"  {'─' * 56}"))
    rc = ssh_stream(device, user, cmd_str)
    print(dim(f"  {'─' * 56}"))
    if rc == 0:
        print(good(f"\n  Process exited cleanly (rc=0)\n"))
    else:
        print(err(f"\n  Process exited with rc={rc}\n"))

# ── Feature 7: Scan local device ─────────────────────────────────────────────
def scan_local_files() -> list[Path]:
    """Browse the local machine for files and return a filtered list."""
    import re as _re
    root_input = prompt("Local root path to scan [~]:")
    root = Path(root_input).expanduser() if root_input else Path.home()
    depth_input = prompt("Max depth [4]:")
    try:
        depth = int(depth_input)
    except ValueError:
        depth = 4
    filt = prompt("Filename filter (leave blank for all):").lower()

    print(warn(f"\n  Scanning {root} (depth {depth}) …\n"))

    matches: list[Path] = []
    try:
        for p in sorted(root.rglob("*")):
            # Calculate depth relative to root
            rel = p.relative_to(root)
            if len(rel.parts) > depth:
                continue
            # Skip hidden files/dirs
            if any(part.startswith(".") for part in rel.parts):
                continue
            if p.is_file():
                if not filt or filt in p.name.lower():
                    matches.append(p)
    except PermissionError:
        pass

    if not matches:
        print(warn("  No files found.\n"))
        return []

    print(f"  {C_BOLD}Local files under {root}{C_RESET}")
    print(dim(f"  {'─' * 56}"))
    for i, f in enumerate(matches, 1):
        rel = f.relative_to(root)
        size = f.stat().st_size
        size_str = f"{size:,} B" if size < 1024 else f"{size // 1024} KB"
        print(f"  {i:>4}. {rel}  {dim(size_str)}")
    print(good(f"\n  {len(matches)} file(s) found.\n"))
    return matches


# ── Feature 8: Send file to Whisplay/apps ────────────────────────────────────
def send_to_whisplay_apps(device: dict, user: str) -> None:
    """Scan local files, pick one, and push it to ~/Whisplay/apps/ on the device."""
    print(warn("\n  Step 1 — Select a local file to send\n"))
    files = scan_local_files()
    if not files:
        return

    sel = prompt("Select file number to send (or 'b' to cancel):")
    if sel.lower() == "b":
        return
    try:
        chosen = files[int(sel) - 1]
    except (ValueError, IndexError):
        print(err("  Invalid selection.\n"))
        return

    remote_dir_input = prompt("Remote destination dir [~/Whisplay/apps]:")
    remote_dir = remote_dir_input if remote_dir_input else "~/Whisplay/apps"

    remote_path = f"{remote_dir}/{chosen.name}"
    print(warn(f"\n  Will send {chosen.name} → {device['name']}:{remote_path}"))
    confirm = prompt("Proceed? (y/n):")
    if confirm.lower() != "y":
        print(dim("  Cancelled.\n"))
        return

    # Ensure remote directory exists
    ssh_run(device, user, f"mkdir -p {remote_dir}", timeout=15)

    host_target = f"{user}@{device['ip']}"
    if shutil.which("rsync"):
        cmd = [
            "rsync", "-avz", "--progress",
            "-e", "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8",
            str(chosen),
            f"{host_target}:{remote_dir}/",
        ]
    else:
        cmd = [
            "scp",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=8",
            str(chosen),
            f"{host_target}:{remote_path}",
        ]

    rc = subprocess.call(cmd)
    if rc == 0:
        print(good(f"\n  Sent {chosen.name} → {device['name']}:{remote_path}\n"))
    else:
        print(err(f"\n  Transfer failed (rc={rc})\n"))


# ── Device action menu ────────────────────────────────────────────────────────
def device_menu(device: dict) -> None:
    default_user = os.environ.get("USER", "ubuntu")
    user_input = prompt(f"SSH username [{default_user}]:")
    user = user_input if user_input else default_user

    while True:
        banner(f"Device: {device['name']}  ({device['ip']})")
        options = [
            ("1", "SSH          — open interactive shell"),
            ("2", "Scan         — print remote directory structure"),
            ("3", "Copy         — rsync device files to local copy folder"),
            ("4", "Replace      — push a local copy file back to device"),
            ("5", "List .py     — find all Python files on device"),
            ("6", "Run .py      — select and run a Python file (live output)"),
            ("7", "Scan Local   — browse files on this machine"),
            ("8", "Send File    — pick local file → send to device Whisplay/apps"),
            ("9", "Chatroom     — join or host a chatroom"),
            ("b", "Back         — return to device list"),
        ]
        for key, label in options:
            print(f"  {C_BOLD}{key}{C_RESET}. {label}")
        print()

        choice = prompt("Choice:")

        if choice == "1":
            ssh_interactive(device, user)

        elif choice == "2":
            scan_device(device, user)

        elif choice == "3":
            copy_device(device, user)

        elif choice == "4":
            replace_file(device, user)

        elif choice == "5":
            list_py_files(device, user)

        elif choice == "6":
            run_py_file(device, user)

        elif choice == "7":
            scan_local_files()

        elif choice == "8":
            send_to_whisplay_apps(device, user)

        elif choice == "9":
            chatroom_menu(device)

        elif choice.lower() == "b":
            break

        else:
            print(err("  Unknown option."))


# ── Feature 10: Chatroom ─────────────────────────────────────────────────────
def chatroom_menu(device: dict) -> None:
    banner("Chatroom — Device Type Selection")
    device_types = ["Windows", "Android", "Linux", "Whisplay"]
    for i, dtype in enumerate(device_types, 1):
        print(f"  {C_BOLD}{i}{C_RESET}. {dtype}")
    print(f"  {C_BOLD}b{C_RESET}. Back")
    choice = prompt("Select device type:")
    if choice.lower() == "b":
        return
    try:
        dtype = device_types[int(choice)-1]
    except (ValueError, IndexError):
        print(err("  Invalid selection."))
        return
    print(good(f"\n  Entering chatroom as {dtype} device...\n"))
    # Use SSH username for chat identity
    default_user = os.environ.get("USER", "ubuntu")
    user_input = prompt(f"Chat username [{default_user}]:")
    chat_user = user_input if user_input else default_user
    # Discover available chatrooms among online peers
    peers = get_tailscale_peers()
    chatroom_peers = []
    chat_port = 8989
    import socket
    for peer in peers:
        if peer["ip"] == device["ip"]:
            continue
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.7)
            s.connect((peer["ip"], chat_port))
            s.close()
            chatroom_peers.append(peer)
        except Exception:
            continue
    if chatroom_peers:
        print(good("\n  Found active chatrooms on these devices:"))
        for i, p in enumerate(chatroom_peers, 1):
            print(f"  {i}. {p['name']}  {p['ip']}  {p['os']}")
        print(f"  0. Host a new chatroom on this device")
        sel = prompt("Join which chatroom? (number, or 0 to host):")
        if sel == "0":
            chatroom_loop(device, dtype, chat_user, host=True)
        else:
            try:
                idx = int(sel) - 1
                if 0 <= idx < len(chatroom_peers):
                    chatroom_loop(chatroom_peers[idx], dtype, chat_user, host=False)
                else:
                    print(err("  Invalid selection."))
            except Exception:
                print(err("  Invalid input."))
    else:
        print(dim("  No active chatrooms found. Hosting a new chatroom on this device.\n"))
        chatroom_loop(device, dtype, chat_user, host=True)


import threading
import socket

def chatroom_loop(device: dict, dtype: str, chat_user: str, host: bool = True) -> None:
    chat_port = 8989
    stop_flag = threading.Event()
    messages = []            # Shared chat history for this session
    peer_ips = []            # Ordered list of client IPs (for failover)
    my_ip = _get_my_tailscale_ip()
    migrate_target = [None]  # Mutable container for migration target IP

    def print_msg(msg):
        print(f"  {msg}")
        sys.stdout.flush()
        messages.append(msg)

    clients = []  # List of (conn, addr, thread)
    clients_lock = threading.Lock()

    def broadcast(msg):
        with clients_lock:
            dead_clients = []
            for c, addr, t in clients:
                try:
                    c.sendall((msg + "\n").encode())
                except Exception as e:
                    print(f"  [ERROR] Failed to send to {addr}: {e}")
                    sys.stdout.flush()
                    dead_clients.append(c)
            for dc in dead_clients:
                clients[:] = [(c, a, t) for (c, a, t) in clients if c != dc]

    def broadcast_peer_list():
        """Send the current ordered peer list to all clients."""
        with clients_lock:
            ips = [addr[0] for _, addr, _ in clients]
        peer_msg = f"__PEERS__:{','.join(ips)}"
        broadcast(peer_msg)

    # ── HOST SERVER ───────────────────────────────────────────────────────
    def host_server():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", chat_port))
        srv.listen(8)
        print(dim(f"  [Chatroom] Hosting on {device['ip']}:{chat_port} as {chat_user} ({dtype}) (Ctrl+C or 'exit' to leave)"))
        sys.stdout.flush()
        srv.settimeout(1.0)

        def handle_client(conn, addr):
            with conn:
                name = f"{addr[0]}"
                # Send chat history to new client
                try:
                    for hist_msg in messages:
                        conn.sendall((hist_msg + "\n").encode())
                except Exception as e:
                    print(f"  [ERROR] Failed to send history to {addr}: {e}")
                try:
                    while not stop_flag.is_set():
                        data = conn.recv(4096)
                        if not data:
                            break
                        msg = data.decode(errors="ignore").strip()
                        if msg:
                            if msg == "/history":
                                try:
                                    for hist_msg in messages:
                                        conn.sendall((hist_msg + "\n").encode())
                                except Exception as e:
                                    print(f"  [ERROR] Failed to send history to {addr}: {e}")
                            else:
                                print_msg(f"[{name}] {msg}")
                                broadcast(f"[{name}] {msg}")
                except Exception as e:
                    print(f"  [ERROR] Exception in handle_client for {addr}: {e}")
            # Remove client on disconnect
            with clients_lock:
                for i, (c, a, t) in enumerate(clients):
                    if c == conn:
                        clients.pop(i)
                        break
            print_msg(f"[System] {name} left the chatroom.")
            broadcast(f"[System] {name} left the chatroom.")
            broadcast_peer_list()

        # Accept loop
        try:
            while not stop_flag.is_set():
                try:
                    conn, addr = srv.accept()
                    print_msg(f"[System] {addr[0]} joined the chatroom.")
                    broadcast(f"[System] {addr[0]} joined the chatroom.")
                    t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
                    with clients_lock:
                        clients.append((conn, addr, t))
                    t.start()
                    broadcast_peer_list()
                except socket.timeout:
                    continue
        except KeyboardInterrupt:
            pass
        finally:
            # On host exit: pick successor and tell everyone to migrate
            with clients_lock:
                if clients:
                    successor_ip = clients[0][1][0]  # First client's IP
                    migrate_msg = f"__MIGRATE__:{successor_ip}"
                    for c, _, _ in clients:
                        try:
                            c.sendall((migrate_msg + "\n").encode())
                        except Exception:
                            pass
                    import time; time.sleep(0.3)
                for c, _, _ in clients:
                    try:
                        c.close()
                    except Exception as e:
                        print(f"  [ERROR] Failed to close client: {e}")
            srv.close()

    # ── CLIENT CONNECT ────────────────────────────────────────────────────
    def client_connect():
        nonlocal peer_ips
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        host_disconnected = threading.Event()
        try:
            s.connect((device["ip"], chat_port))
            s.settimeout(None)
            print(dim(f"  [Chatroom] Connected to {device['ip']}:{chat_port} as {chat_user} ({dtype})"))
            sys.stdout.flush()

            def recv_loop():
                nonlocal peer_ips
                while not stop_flag.is_set():
                    try:
                        data = s.recv(4096)
                        if not data:
                            print("  [System] Host disconnected.")
                            sys.stdout.flush()
                            host_disconnected.set()
                            break
                        for line in data.decode(errors="ignore").splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            # Handle control messages
                            if line.startswith("__PEERS__:"):
                                peer_ips = [ip for ip in line[10:].split(",") if ip]
                                continue
                            if line.startswith("__MIGRATE__:"):
                                migrate_target[0] = line[12:].strip()
                                print(f"  [System] Host is leaving. New host: {migrate_target[0]}")
                                sys.stdout.flush()
                                host_disconnected.set()
                                break
                            print(f"  {line}")
                            sys.stdout.flush()
                            messages.append(line)
                    except socket.timeout:
                        continue
                    except OSError as e:
                        print(f"  [System] Connection error: {e}")
                        sys.stdout.flush()
                        host_disconnected.set()
                        break

            t = threading.Thread(target=recv_loop, daemon=True)
            t.start()

            while not stop_flag.is_set() and not host_disconnected.is_set():
                try:
                    msg = prompt(f"[{chat_user}@{dtype}] >")
                except EOFError:
                    stop_flag.set()
                    break
                if msg.lower() == "exit":
                    stop_flag.set()
                    break
                if msg == "/history":
                    print(dim("\n--- Chat History ---"))
                    for m in messages:
                        print(f"  {m}")
                    print(dim("--- End of History ---\n"))
                    sys.stdout.flush()
                    continue
                try:
                    s.sendall(f"{chat_user}: {msg}".encode() + b"\n")
                except Exception:
                    print_msg("[System] Connection lost.")
                    host_disconnected.set()
                    break
            s.close()
        except Exception as e:
            print_msg(f"[System] Could not connect: {e}")
            return

        # ── FAILOVER LOGIC ────────────────────────────────────────────────
        if host_disconnected.is_set() and not stop_flag.is_set():
            _handle_failover()

    def _handle_failover():
        """Cascade through known peers to find or become the new host."""
        import time

        # Build ordered candidate list: __MIGRATE__ target first, then peer list
        candidates = []
        if migrate_target[0]:
            candidates.append(migrate_target[0])
        for ip in peer_ips:
            if ip not in candidates:
                candidates.append(ip)

        if not candidates:
            # No known peers at all — become host as last resort
            print(good("\n  [System] No peers known. Becoming host..."))
            sys.stdout.flush()
            _run_as_host()
            return

        # If I'm the first candidate, become host immediately
        if candidates[0] == my_ip:
            print(good("\n  [System] You are the new host! Starting server..."))
            sys.stdout.flush()
            _run_as_host()
            return

        # Try each candidate in order
        for i, candidate_ip in enumerate(candidates):
            if stop_flag.is_set():
                return
            # If it's my turn in the list, I become host
            if candidate_ip == my_ip:
                print(good("\n  [System] Earlier candidates unreachable. Becoming host..."))
                sys.stdout.flush()
                _run_as_host()
                return

            wait = 2 + i          # give earlier candidates time to spin up
            print(dim(f"\n  [System] Waiting {wait}s then trying {candidate_ip} ({i+1}/{len(candidates)})..."))
            sys.stdout.flush()
            time.sleep(wait)

            # Probe whether the candidate is listening
            reachable = False
            for attempt in range(4):
                try:
                    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    probe.settimeout(2)
                    probe.connect((candidate_ip, chat_port))
                    probe.close()
                    reachable = True
                    break
                except Exception:
                    time.sleep(1)

            if reachable:
                new_device = dict(device)
                new_device["ip"] = candidate_ip
                client_connect_to(new_device)
                return

            print(dim(f"  [System] {candidate_ip} not responding, trying next..."))

        # Exhausted all candidates — become host as last resort
        print(good("\n  [System] No candidates responded. Becoming host..."))
        sys.stdout.flush()
        _run_as_host()

    def client_connect_to(target_device):
        """Connect to a new host after failover."""
        nonlocal peer_ips
        import time
        for attempt in range(5):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            try:
                s.connect((target_device["ip"], chat_port))
                s.settimeout(None)
                print(good(f"  [System] Connected to new host {target_device['ip']}"))
                sys.stdout.flush()
                break
            except Exception:
                print(dim(f"  [System] Retry {attempt+1}/5 connecting to {target_device['ip']}..."))
                sys.stdout.flush()
                s.close()
                time.sleep(2)
        else:
            # Could not reach this host — re-enter failover to try next peer
            print(warn(f"  [System] Could not reach {target_device['ip']}. Trying next candidate..."))
            sys.stdout.flush()
            # Remove the failed IP so _handle_failover skips it
            if target_device["ip"] in peer_ips:
                peer_ips.remove(target_device["ip"])
            if migrate_target[0] == target_device["ip"]:
                migrate_target[0] = None
            _handle_failover()
            return

        host_disconnected = threading.Event()

        def recv_loop():
            nonlocal peer_ips
            while not stop_flag.is_set():
                try:
                    data = s.recv(4096)
                    if not data:
                        print("  [System] Host disconnected.")
                        sys.stdout.flush()
                        host_disconnected.set()
                        break
                    for line in data.decode(errors="ignore").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("__PEERS__:"):
                            peer_ips = [ip for ip in line[10:].split(",") if ip]
                            continue
                        if line.startswith("__MIGRATE__:"):
                            migrate_target[0] = line[12:].strip()
                            print(f"  [System] Host is leaving. New host: {migrate_target[0]}")
                            sys.stdout.flush()
                            host_disconnected.set()
                            break
                        print(f"  {line}")
                        sys.stdout.flush()
                        messages.append(line)
                except socket.timeout:
                    continue
                except OSError as e:
                    print(f"  [System] Connection error: {e}")
                    sys.stdout.flush()
                    host_disconnected.set()
                    break

        t = threading.Thread(target=recv_loop, daemon=True)
        t.start()

        while not stop_flag.is_set() and not host_disconnected.is_set():
            try:
                msg = prompt(f"[{chat_user}@{dtype}] >")
            except EOFError:
                stop_flag.set()
                break
            if msg.lower() == "exit":
                stop_flag.set()
                break
            if msg == "/history":
                print(dim("\n--- Chat History ---"))
                for m in messages:
                    print(f"  {m}")
                print(dim("--- End of History ---\n"))
                sys.stdout.flush()
                continue
            try:
                s.sendall(f"{chat_user}: {msg}".encode() + b"\n")
            except Exception:
                print_msg("[System] Connection lost.")
                host_disconnected.set()
                break
        s.close()

        # Recurse failover if needed
        if host_disconnected.is_set() and not stop_flag.is_set():
            _handle_failover()

    # ── RUN AS HOST (used for both initial host and failover) ─────────────
    def _run_as_host():
        stop_flag.clear()
        server_thread = threading.Thread(target=host_server, daemon=True)
        server_thread.start()
        try:
            while not stop_flag.is_set():
                try:
                    msg = prompt(f"[{chat_user}@{dtype} HOST] >")
                except EOFError:
                    stop_flag.set()
                    break
                if msg.lower() == "exit":
                    stop_flag.set()
                    break
                print_msg(f"[You] {msg}")
                broadcast(f"[Host] {msg}")
                sys.stdout.flush()
        except KeyboardInterrupt:
            stop_flag.set()
        print(dim("  Leaving chatroom...\n"))
        sys.stdout.flush()

    # ── ENTRY ─────────────────────────────────────────────────────────────
    if host:
        _run_as_host()
    else:
        client_connect()


def _get_my_tailscale_ip() -> str:
    """Get this machine's Tailscale IP."""
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()[0]
    except Exception:
        pass
    # Fallback: parse from tailscale status --json (works on Windows)
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            self_ips = data.get("Self", {}).get("TailscaleIPs", [])
            for ip in self_ips:
                if "." in ip:  # prefer IPv4
                    return ip
    except Exception:
        pass
    return ""

# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    banner("Tailscale Device Manager")

    if not check_tailscale():
        print(err("  tailscale CLI not found. Install it first:"))
        print(dim("  https://tailscale.com/download"))
        sys.exit(1)

    import socket
    chat_port = 8989
    while True:
        print("  Fetching Tailscale peers …")
        peers = get_tailscale_peers()

        if not peers:
            print(err("  No online Tailscale peers found."))
            print(dim("  Make sure tailscaled is running: sudo tailscale up"))
            retry = prompt("Retry? (y/n):")
            if retry.lower() != "y":
                break
            continue

        # Scan for chatrooms
        chatroom_peers = []
        for peer in peers:
            try:
                if peer["ip"] == "127.0.0.1":
                    continue
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.7)
                s.connect((peer["ip"], chat_port))
                s.close()
                chatroom_peers.append(peer)
            except Exception:
                continue

        # Show devices and chatrooms
        banner("Tailscale Devices & Chatrooms")
        fmt = f"  {{idx:>2}}. {{name:<28}} {{ip:<18}} {{os}}"
        print(dim(fmt.format(idx="#", name="Hostname", ip="Tailscale IP", os="OS")))
        print(dim("  " + "─" * 56))
        for i, p in enumerate(peers, 1):
            print(fmt.format(idx=i, name=p["name"], ip=p["ip"], os=p["os"]))
        if chatroom_peers:
            print(dim("\n  Chatrooms available on these devices:"))
            for j, p in enumerate(chatroom_peers, 1):
                print(f"    [C{j}] {p['name']}  {p['ip']}  {p['os']}")
        print()

        choice = prompt("Select device number, chatroom (e.g. C1), or 'q' to quit:")
        if choice.lower() == "q":
            print(dim("\n  Bye!\n"))
            break
        if choice.upper().startswith("C") and chatroom_peers:
            try:
                idx = int(choice[1:]) - 1
                if 0 <= idx < len(chatroom_peers):
                    # Enter chatroom directly
                    dtype = "Auto"
                    default_user = os.environ.get("USER", "ubuntu")
                    user_input = prompt(f"Chat username [{default_user}]:")
                    chat_user = user_input if user_input else default_user
                    chatroom_loop(chatroom_peers[idx], dtype, chat_user, host=False)
                    continue
                else:
                    print(err("  Invalid chatroom selection."))
                    continue
            except Exception:
                print(err("  Invalid chatroom input."))
                continue
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(peers):
                device = peers[idx]
            else:
                print(err("  Invalid selection."))
                continue
        except ValueError:
            print(err("  Please enter a number or chatroom code."))
            continue

        if device.get("id") == "self":
            print(warn("  That' this device — skipping SSH, opening local app menu."))
            device["ip"] = "127.0.0.1"

        device_menu(device)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(dim("\n\n  Interrupted. Bye!\n"))
        sys.exit(0)
