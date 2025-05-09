# /Users/roman/work/itter/README.md

# itter.sh - The Sh*tter Twitter Clone for SSH Enthusiasts

itter.sh is a minimalist, "humorous", Twitter-like social media platform accessible exclusively via SSH.
It uses Python with `asyncssh` for the server, Supabase for the backend database and real-time updates, and `rich` for a slightly more stylish terminal experience.

## Features

*   **SSH-Only Access**: Login and interact entirely through your SSH client.
*   **Public Key Authentication**: Secure access using SSH keys.
*   **User Registration via SSH**: `ssh -p <PORT> register:yourdesiredusername@<HOST>`
*   **Eets**: Post short messages (up to 180 characters).
*   **Timelines**:
    *   View your personal timeline (`#mine`: your eets + eets from users you follow).
    *   View all public eets (`#all`).
    *   View eets by specific users (`@username`).
    *   View eets by channel/hashtag (`#channelname`).
    *   Paginated static timelines.
    *   Live, auto-updating timelines (`watch` command).
*   **Follow/Unfollow Users**.
*   **User Profiles**: View basic stats (eet count, followers, following).
*   **Profile Editing**: Update display name and email.
*   **Real-time Updates**: Live timelines get new eets pushed via Supabase Realtime.

## Setup

### 1. Prerequisites

*   Python 3.8+
*   A Supabase project.
*   `ssh-keygen` tool (usually available on Linux/macOS/WSL).

### 2. Clone & Configure

*(Assuming you have the project files)*

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Supabase Setup**:
    *   Go to your Supabase project.
    *   In the SQL Editor, run the contents of `schema.sql` to create the necessary tables, functions, and policies.
    *   Ensure Row Level Security (RLS) is enabled for `users`, `posts`, and `follows` tables (the script does this).
    *   Ensure the `posts` table is enabled for real-time broadcasting (Database > Replication in Supabase dashboard).

3.  **Environment Variables**:
    *   Copy `.env.example` to `.env`.
    *   Fill in your Supabase project details:
        *   `SUPABASE_URL`: Your project's API URL.
        *   `SUPABASE_KEY`: Your project's **`service_role` key** (found in Project Settings > API).
        *   `SUPABASE_WSURL`: Your project's Realtime WebSocket URL.
    *   Configure SSH server settings if needed (defaults are `SSH_HOST="0.0.0.0"` and `SSH_PORT="8022"`).
    *   Set `ITTER_DEBUG_MODE="True"` for verbose logging during development, `"False"` for production.

4.  **Generate SSH Host Key**:
    In the project directory, run:
    ```bash
    ssh-keygen -t ed25519 -f ./ssh_host_key -N ""
    ```
    This creates `ssh_host_key` and `ssh_host_key.pub`. The server will use `ssh_host_key`.

### 3. Run the Server

```bash
python main.py
```


```bash
ssh-keygen -t ed25519 -f ~/.ssh/itter_id_ed25519
ssh -p 8022 -i ~/.ssh/itter_id_ed25519 register:roman@localhost
ssh -p 8022 -i ~/.ssh/itter_id_ed25519 roman@localhost
```