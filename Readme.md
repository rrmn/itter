> PRs very welcome!

# itter.sh - Social Media for Purists

itter.sh is a minimalist Social Media platform accessible exclusively via terminal.

It uses Python with `asyncssh` for the server and Supabase for the backend database and real-time updates.

## Features

*   **SSH-Only Access**: Login and interact entirely through your SSH client.
*   **Public Key Authentication**: Secure access using SSH keys.
*   **User Registration via SSH**: `ssh register:yourdesiredusername@app.itter.sh`
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
