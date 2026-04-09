# tailscale_manager

================================================================================
  Tailscale Device Manager
  A terminal-based multi-device management and chat tool over Tailscale VPN
================================================================================

Overview
--------
Tailscale Device Manager is a command-line utility that discovers all online
devices on your Tailscale network and lets you manage them from one place.
SSH into remote machines, sync files, run Python scripts remotely, and join
peer-to-peer chatrooms — all without port forwarding or public IPs.

Built for managing fleets of Pi-based WhisPlay devices, but works with any
Tailscale-connected machine (Linux, Windows, macOS, Android).


Requirements
------------
  - Python 3.10+
  - Tailscale installed and logged in (`tailscale up`)
  - SSH access to remote devices (key-based auth recommended)
  - rsync (optional, falls back to scp)


Quick Start
-----------
  1. Make sure Tailscale is running:
       tailscale up

  2. Run the manager:
       python tailscale_manager.py

  3. The tool scans your Tailscale network and shows all online peers.
     Pick a device by number, or jump straight into an active chatroom.


Features
--------

  [1] SSH — Interactive Shell
      Opens a live SSH session to the selected device. When you exit the
      remote shell, you return to the device menu automatically.

  [2] Scan — Remote Directory Tree
      Prints the file tree of any path on the remote device. Uses `tree`
      if available, falls back to `find`. Configurable depth, capped at
      500 lines to keep the terminal responsive.

  [3] Copy — Sync Device to Local
      Downloads the remote device's files to a local folder using rsync
      (or scp). Files land in:
        ~/tailscale_copies/<device_name>/
      Excludes .git, __pycache__, and .pyc files automatically.

  [4] Replace — Push File Back to Device
      Browse your local copy of a device's files, pick one, and push it
      back to the same path on the remote machine. Useful for editing
      code locally then deploying it.

  [5] List .py — Find Python Files
      Searches the remote device for all .py files (skips hidden dirs).
      Supports name filtering. Results are numbered for use with Run.

  [6] Run .py — Execute Remotely
      Pick a Python file from the list and run it on the device. Output
      streams live to your terminal. Supports passing arguments.

  [7] Scan Local — Browse Local Files
      Walks your local filesystem with depth and name filters. Used as
      the first step of "Send File".

  [8] Send File — Push to Whisplay/apps
      Pick any local file and send it to ~/Whisplay/apps/ (or any custom
      path) on the remote device. Creates the remote directory if needed.

  [9] Chatroom — Peer-to-Peer Chat
      Join or host a real-time text chatroom over your Tailscale network.
      See details below.


Chatroom System
---------------
The chatroom is a TCP-based group chat (port 8989) with automatic host
failover. No central server required — any device can host.

  Hosting:
    - Select option 9 from the device menu, pick your device type
      (Windows / Android / Linux / Whisplay), and choose a username.
    - If no chatrooms are found on the network, you become the host.
    - The host accepts connections from all peers and relays messages.

  Joining:
    - The manager auto-scans all Tailscale peers on port 8989.
    - Active chatrooms appear in the main device list as [C1], [C2], etc.
    - Select a chatroom code to join instantly.

  In-Chat Commands:
    /history    — Replay the full chat log
    exit        — Leave the chatroom

  Automatic Host Failover:
    When the host disconnects (exits, crashes, or loses network):

    1. The host picks the first connected client as the successor and
       sends a __MIGRATE__ message to all clients.
    2. Clients receive the new host's IP and wait for it to start.
    3. If the designated successor doesn't come up, clients cascade
       through the peer list in connection order.
    4. If no peers respond, the client becomes the host itself.
    5. Chat history is preserved across migrations.

    This means the chatroom survives any single device dropping out —
    participants reconnect to the new host automatically.


Network Architecture
--------------------
  - All traffic flows over Tailscale's WireGuard mesh (encrypted, NAT-
    traversing). No ports need to be opened on your router.
  - SSH connections use Tailscale IPs (100.x.x.x) with host key checking
    disabled for convenience (devices are already authenticated by
    Tailscale).
  - The chatroom server binds to 0.0.0.0:8989 but is only reachable
    over the Tailscale network.
  - Device discovery uses `tailscale status --json` to enumerate peers.


File Structure
--------------
  tailscale_manager.py        — This tool (standalone, no dependencies
                                 beyond Python stdlib + Tailscale CLI)
  ~/tailscale_copies/         — Default local folder for device file
     <device_name>/              copies (created automatically)


Configuration
-------------
  LOCAL_COPIES_ROOT    Path where device copies are stored.
                       Default: ~/tailscale_copies

  SSH username         Prompted on first device selection. Defaults to
                       your local $USER / %USERNAME%.

  Chat port            8989 (hardcoded). Change the `chat_port` variable
                       in chatroom_menu() and chatroom_loop() if needed.


Tips
----
  - Set up SSH key auth (`ssh-copy-id user@100.x.x.x`) to avoid typing
    passwords repeatedly.
  - Use Copy (3) → edit locally → Replace (4) as a lightweight dev
    workflow for headless Pi devices.
  - The chatroom failover works best with 3+ devices — the more peers,
    the more resilient the room.
  - On Windows, install Tailscale from https://tailscale.com/download
    and make sure `tailscale.exe` is on your PATH.
  - The tool auto-detects your own Tailscale IP for failover ordering.
    If detection fails, it falls back gracefully.


Troubleshooting
---------------
  "tailscale CLI not found"
    → Install Tailscale and ensure it's on your PATH.
    → Linux: sudo apt install tailscale
    → Windows: https://tailscale.com/download

  "No online Tailscale peers found"
    → Run `tailscale status` to verify you're connected.
    → Make sure other devices are logged into the same Tailnet.

  SSH connection refused
    → Ensure sshd is running on the remote device.
    → Check that the SSH user exists and has permissions.

  Chatroom not found
    → Someone needs to host first (option 9 → Host).
    → The scan only checks port 8989 with a 0.7s timeout.
      Slow networks may need a second attempt.

  rsync not found
    → The tool falls back to scp automatically.
    → Install rsync for faster incremental syncs:
      sudo apt install rsync


License
-------
  See the LICENSE file in the Whisplay repository root.
