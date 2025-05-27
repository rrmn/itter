import asyncio
import logging
import traceback
from typing import Any


from itter.context import config, db_client_ctx
from itter.utils import debug_log, hash_ip

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(levelname)s] - %(asctime)s - %(name)s - %(funcName)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# --- User Operations ---
async def db_get_user_by_username(username: str) -> dict[str, Any] | None:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: get_user_by_username('{username}')")
    try:
        resp = await asyncio.to_thread(
            db_client_ctx.get()
            .table("users")
            .select("*")
            .eq("username", username)
            .execute
        )
        return resp.data[0] if resp.data else None
    except Exception as e:
        debug_log(f"[DB ERROR] get_user_by_username: {e}")
        return None


async def db_get_user_by_id(user_id: str) -> dict[str, Any] | None:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: get_user_by_id('{user_id}')")
    try:
        resp = await asyncio.to_thread(
            db_client_ctx.get().table("users").select("*").eq("id", user_id).execute
        )
        return resp.data[0] if resp.data else None
    except Exception as e:
        debug_log(f"[DB ERROR] get_user_by_id: {e}")
        return None


async def db_username_exists_case_insensitive(username: str) -> str | None:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: db_username_exists_case_insensitive('{username}')")

    username_lower = username.lower()
    try:
        resp = await asyncio.to_thread(
            db_client_ctx.get()
            .table("users")
            .select("username")
            .ilike("username", username_lower)
            .execute
        )
        return resp.data[0] if resp.data else None
    except Exception as e:
        debug_log(f"[DB ERROR] db_username_exists_case_insensitive: {e}")
        return None


async def db_create_user(username: str, public_key: str) -> None:
    """Creates a new user entry."""
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    logger.debug("Creating user: %s", username)
    try:
        await asyncio.to_thread(
            db_client_ctx.get()
            .table("users")
            .insert({"username": username, "public_key": public_key})
            .execute,
        )
    except Exception:
        logger.exception("Error creating user:")
        # Re-raise crucial errors like unique constraint violations
        raise


async def db_update_profile(
    username: str,
    new_display_name: str | None,
    new_email: str | None,
    reset: bool | None = False,
) -> None:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(
        f"DB: update_profile('{username}') -> name='{new_display_name}', email='{new_email}'"
    )
    user = await db_get_user_by_username(username)
    if not user:
        raise ValueError("User not found for profile update.")

    update_data = {}
    if new_display_name is not None:
        update_data["display_name"] = new_display_name
    if new_email is not None:
        update_data["email"] = new_email
    # Reset display name and email if reset is True
    if reset:
        update_data["display_name"] = None
        update_data["email"] = None

    if not update_data and not reset:
        raise ValueError("Nothing to update. Provide a new name and/or email.")

    try:
        await asyncio.to_thread(
            db_client_ctx.get()
            .table("users")
            .update(update_data)
            .eq("id", user["id"])
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_update_profile: {e}")
        raise e


async def db_get_profile_stats(username: str) -> dict[str, Any]:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: get_profile_stats('{username}')")
    user = await db_get_user_by_username(username)
    if not user:
        raise ValueError("User not found for profile stats.")

    try:
        tasks = [
            asyncio.to_thread(
                db_client_ctx.get()
                .table("posts")
                .select("id", count="exact")
                .eq("user_id", user["id"])
                .execute
            ),
            asyncio.to_thread(
                db_client_ctx.get()
                .table("follows")
                .select("follower_id", count="exact")
                .eq("follower_id", user["id"])
                .execute
            ),
            asyncio.to_thread(
                db_client_ctx.get()
                .table("follows")
                .select("following_id", count="exact")
                .eq("following_id", user["id"])
                .execute
            ),
        ]
        results = await asyncio.gather(*tasks)

        return {
            "username": user["username"],
            "display_name": user.get("display_name"),
            "email": user.get("email"),
            "joined_at": user.get("created_at"),
            "eet_count": results[0].count if results[0] else 0,
            "following_count": results[1].count if results[1] else 0,
            "follower_count": results[2].count if results[2] else 0,
        }
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_profile_stats: {e}")
        raise e


# --- Follow Operations ---


async def db_is_following(follower_username: str, following_username: str) -> bool:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: is_following('{follower_username}', '{following_username}')")
    follower = await db_get_user_by_username(follower_username)
    following = await db_get_user_by_username(following_username)
    if not follower or not following:
        return False
    try:
        resp = await asyncio.to_thread(
            db_client_ctx.get()
            .table("follows")
            .select("follower_id", count="exact")
            .eq("follower_id", follower["id"])
            .eq("following_id", following["id"])
            .execute
        )
        return (
            resp.count > 0
            if hasattr(resp, "count") and resp.count is not None
            else bool(resp.data)
        )
    except Exception as e:
        debug_log(f"[DB ERROR] is_following: {e}")
        return False


async def db_follow_user(current_username: str, target_username_to_follow: str) -> None:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: follow_user('{current_username}', '{target_username_to_follow}')")
    user = await db_get_user_by_username(current_username)
    target = await db_get_user_by_username(target_username_to_follow)

    if not user or not target:
        raise ValueError("User not found for follow operation.")
    if user["id"] == target["id"]:
        raise ValueError("You cannot follow yourself, silly.")
    if await db_is_following(current_username, target_username_to_follow):
        raise ValueError(f"You are already following @{target_username_to_follow}.")

    try:
        await asyncio.to_thread(
            db_client_ctx.get()
            .table("follows")
            .insert({"follower_id": user["id"], "following_id": target["id"]})
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_follow_user: {e}")
        raise e


async def db_unfollow_user(
    current_username: str, target_username_to_unfollow: str
) -> None:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(
        f"DB: unfollow_user('{current_username}', '{target_username_to_unfollow}')"
    )
    user = await db_get_user_by_username(current_username)
    target = await db_get_user_by_username(target_username_to_unfollow)

    if not user or not target:
        raise ValueError("User not found for unfollow operation.")
    if user["id"] == target["id"]:
        raise ValueError("No chance. You cannot get rid of yourself. This is life.")
    if not await db_is_following(current_username, target_username_to_unfollow):
        raise ValueError(
            f"You are not following @{target_username_to_unfollow} anyway."
        )

    try:
        await asyncio.to_thread(
            db_client_ctx.get()
            .table("follows")
            .delete()
            .match({"follower_id": user["id"], "following_id": target["id"]})
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_unfollow_user: {e}")
        raise e


async def db_is_following_channel(username: str, channel_tag: str) -> bool:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    channel_tag_lower = channel_tag.lower()
    debug_log(f"DB: is_following_channel('{username}', '#{channel_tag_lower}')")
    user = await db_get_user_by_username(username)
    if not user:
        return False
    try:
        resp = await asyncio.to_thread(
            db_client_ctx.get()
            .table("user_channel_follows")
            .select("user_id", count="exact")
            .eq("user_id", user["id"])
            .eq("channel_tag", channel_tag_lower)
            .execute
        )
        return (
            resp.count > 0
            if hasattr(resp, "count") and resp.count is not None
            else bool(resp.data)
        )
    except Exception as e:
        debug_log(f"[DB ERROR] is_following_channel: {e}")
        return False


async def db_follow_channel(current_username: str, channel_tag: str) -> None:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    channel_tag_lower = channel_tag.lower()
    debug_log(f"DB: follow_channel('{current_username}', '#{channel_tag_lower}')")
    user = await db_get_user_by_username(current_username)

    if not user:
        raise ValueError(f"User '{current_username}' not found.")
    if not channel_tag_lower:  # Should be caught by command parser, but good to have
        raise ValueError("Channel tag cannot be empty.")
    if await db_is_following_channel(current_username, channel_tag_lower):
        raise ValueError(f"You are already following channel #{channel_tag_lower}.")

    try:
        await asyncio.to_thread(
            db_client_ctx.get()
            .table("user_channel_follows")
            .insert({"user_id": user["id"], "channel_tag": channel_tag_lower})
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_follow_channel: {e}")
        if "violates unique constraint" in str(e) or "user_channel_follows_pkey" in str(
            e
        ):  # adapt to actual constraint name
            raise ValueError(f"You are already following channel #{channel_tag_lower}.")
        raise e


async def db_unfollow_channel(current_username: str, channel_tag: str) -> None:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    channel_tag_lower = channel_tag.lower()
    debug_log(f"DB: unfollow_channel('{current_username}', '#{channel_tag_lower}')")
    user = await db_get_user_by_username(current_username)

    if not user:
        raise ValueError(f"User '{current_username}' not found.")
    if not await db_is_following_channel(current_username, channel_tag_lower):
        raise ValueError(f"You are not following channel #{channel_tag_lower} anyway.")

    try:
        await asyncio.to_thread(
            db_client_ctx.get()
            .table("user_channel_follows")
            .delete()
            .match({"user_id": user["id"], "channel_tag": channel_tag_lower})
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_unfollow_channel: {e}")
        raise e


# --- Ignore Operations ---
async def db_is_ignoring(ignorer_username: str, ignored_username: str) -> bool:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: is_ignoring('{ignorer_username}', '{ignored_username}')")
    ignorer = await db_get_user_by_username(ignorer_username)
    ignored = await db_get_user_by_username(ignored_username)
    if not ignorer or not ignored:
        return False
    try:
        resp = await asyncio.to_thread(
            db_client_ctx.get()
            .table("ignored_users")
            .select("ignorer_id", count="exact")
            .eq("ignorer_id", ignorer["id"])
            .eq("ignored_user_id", ignored["id"])
            .execute
        )
        return (
            resp.count > 0
            if hasattr(resp, "count") and resp.count is not None
            else bool(resp.data)
        )
    except Exception as e:
        debug_log(f"[DB ERROR] is_ignoring: {e}")
        return False


async def db_ignore_user(current_username: str, target_username_to_ignore: str) -> None:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: ignore_user('{current_username}', '{target_username_to_ignore}')")

    user = await db_get_user_by_username(current_username)
    target = await db_get_user_by_username(target_username_to_ignore)

    if not user or not target:
        raise ValueError("User not found for ignore operation.")
    if user["id"] == target["id"]:
        raise ValueError("You cannot ignore yourself.")
    if await db_is_ignoring(current_username, target_username_to_ignore):
        raise ValueError(f"You are already ignoring @{target_username_to_ignore}.")

    try:
        await asyncio.to_thread(
            db_client_ctx.get()
            .table("ignored_users")
            .insert({"ignorer_id": user["id"], "ignored_user_id": target["id"]})
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_ignore_user: {e}")
        if "violates unique constraint" in str(e) or "ignored_users_pkey" in str(e):
            raise ValueError(f"You are already ignoring @{target_username_to_ignore}.")
        elif 'violates check constraint "check_cannot_ignore_self"' in str(e):
            raise ValueError("You cannot ignore yourself.")
        raise e


async def db_unignore_user(
    current_username: str, target_username_to_unignore: str
) -> None:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(
        f"DB: unignore_user('{current_username}', '{target_username_to_unignore}')"
    )
    user = await db_get_user_by_username(current_username)
    target = await db_get_user_by_username(target_username_to_unignore)

    if not user or not target:
        raise ValueError("User not found for unignore operation.")
    if not await db_is_ignoring(current_username, target_username_to_unignore):
        raise ValueError(f"You are not ignoring @{target_username_to_unignore} anyway.")

    try:
        await asyncio.to_thread(
            db_client_ctx.get()
            .table("ignored_users")
            .delete()
            .match({"ignorer_id": user["id"], "ignored_user_id": target["id"]})
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_unignore_user: {e}")
        raise e


async def db_get_ignored_user_ids(username: str) -> list[str]:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    user = await db_get_user_by_username(username)
    if not user:
        return []

    try:
        resp = await asyncio.to_thread(
            db_client_ctx.get()
            .table("ignored_users")
            .select("ignored_user_id")
            .eq("ignorer_id", user["id"])
            .execute
        )
        return [item["ignored_user_id"] for item in resp.data] if resp.data else []
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_ignored_user_ids for {username}: {e}")
        return []


# --- RPC-based list functions ---
async def db_get_user_following(current_username: str) -> list[dict[str, Any]]:
    """Gets a list of users the current_username is following."""
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: db_get_user_following for '{current_username}'")
    user = await db_get_user_by_username(current_username)
    if not user:
        debug_log(
            f"DB: User '{current_username}' not found for db_get_user_following. Returning empty list."
        )
        return []
    user_uuid = user["id"]

    try:
        resp = await asyncio.to_thread(
            db_client_ctx.get()
            .rpc("get_user_following", {"input_user_id": user_uuid})
            .execute
        )
        return resp.data if resp.data else []
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_user_following for {current_username}: {e}")
        if config.itter_debug_mode:
            debug_log(traceback.format_exc())
        return []


async def db_get_user_followers(current_username: str) -> list[dict[str, Any]]:
    """Gets a list of users following the current_username."""
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: db_get_user_followers for '{current_username}'")
    user = await db_get_user_by_username(current_username)
    if not user:
        debug_log(
            f"DB: User '{current_username}' not found for db_get_user_followers. Returning empty list."
        )
        return []
    user_uuid = user["id"]

    try:
        resp = await asyncio.to_thread(
            db_client_ctx.get()
            .rpc("get_user_followers", {"input_user_id": user_uuid})
            .execute
        )
        return resp.data if resp.data else []
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_user_followers for {current_username}: {e}")
        if config.itter_debug_mode:
            debug_log(traceback.format_exc())
        return []


async def db_get_user_ignoring(current_username: str) -> list[dict[str, Any]]:
    """Gets a list of users the current_username is ignoring."""
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: db_get_user_ignoring for '{current_username}'")
    user = await db_get_user_by_username(current_username)
    if not user:
        debug_log(
            f"DB: User '{current_username}' not found for db_get_user_ignoring. Returning empty list."
        )
        return []
    user_uuid = user["id"]

    try:
        resp = await asyncio.to_thread(
            db_client_ctx.get()
            .rpc("get_user_ignoring", {"input_user_id": user_uuid})
            .execute
        )
        return resp.data if resp.data else []
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_user_ignoring for {current_username}: {e}")
        if config.itter_debug_mode:
            debug_log(traceback.format_exc())
        return []


async def db_get_user_following_channels(current_username: str) -> list[dict[str, Any]]:
    """Gets a list of channels the current_username is following."""
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: db_get_user_following_channels for '{current_username}'")
    user = await db_get_user_by_username(current_username)
    if not user:
        debug_log(
            f"DB: User '{current_username}' not found for db_get_user_following_channels. Returning empty list."
        )
        return []
    user_uuid = user["id"]

    try:
        # This RPC needs to be created in Supabase:
        # SQL: SELECT lower(channel_tag) as channel_tag, created_at FROM user_channel_follows WHERE user_id = input_user_id ORDER BY created_at DESC;
        resp = await asyncio.to_thread(
            db_client_ctx.get()
            .rpc("get_user_following_channels", {"input_user_id": user_uuid})
            .execute
        )
        return resp.data if resp.data else []
    except Exception as e:
        debug_log(
            f"[DB ERROR] db_get_user_following_channels for {current_username}: {e}"
        )
        if config.itter_debug_mode:
            debug_log(traceback.format_exc())
        return []


# --- Post Operations ---
async def db_post_eet(
    username: str,
    content: str,
    tags: list[str],
    mentions: list[str],
    client_ip: str | None = None,
) -> None:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(
        f"DB: post_eet('{username}') -> tags={tags}, mentions={mentions}, client_ip={client_ip}"
    )
    if len(content) > config.eet_max_length:
        raise ValueError(f"Eet too long (max {config.eet_max_length} chars).")

    user = await db_get_user_by_username(username)
    if not user:
        raise ValueError("User not found for posting eet.")

    valid_user_mentions = []
    for ref_username in mentions:
        mentioned_user = await db_get_user_by_username(ref_username)
        if mentioned_user:
            valid_user_mentions.append(ref_username)

    # --- Prepare post_data and add hashed_ip ---
    post_data = {
        "user_id": user["id"],
        "content": content,
        "tags": tags,
        "users_mentioned": valid_user_mentions,
    }

    if client_ip:
        hashed_client_ip = hash_ip(client_ip)
        if hashed_client_ip:
            post_data["hashed_ip"] = hashed_client_ip
            debug_log(f"DB: Storing hashed IP for post by {username}")
        else:
            debug_log(
                f"DB: Could not hash IP {client_ip} for post by {username}. IP will not be stored."
            )

    try:
        await asyncio.to_thread(
            db_client_ctx.get().table("posts").insert(post_data).execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_post_eet: {e}")
        raise e


async def db_get_filtered_timeline_posts(
    current_username: str,
    target_filter: dict[str, Any],
    page: int = 1,
    page_size: int = config.default_timeline_page_size,
) -> list[dict[str, Any]]:
    if not db_client_ctx:
        raise RuntimeError("Database not initialized")
    debug_log(
        f"DB: get_filtered_timeline_posts via RPC ('{current_username}', {target_filter}, page={page})"
    )

    user = await db_get_user_by_username(current_username)
    if not user:
        debug_log(
            f"DB: User '{current_username}' not found for timeline. Returning empty list."
        )
        return []
    user_uuid = user["id"]

    rpc_name: str | None = None
    rpc_params: dict[str, Any] = {
        "input_user_id": user_uuid,
        "p_page": page,
        "p_page_size": page_size,
    }

    filter_type = target_filter.get("type")
    filter_value = target_filter.get("value")

    if filter_type == "mine":
        rpc_name = "get_timeline"
    elif filter_type == "all":
        rpc_name = "get_all_posts_timeline"
    elif filter_type == "channel":
        if not filter_value or not isinstance(filter_value, str):
            debug_log(
                f"DB: Channel filter requested but invalid/missing channel value: {filter_value}. Returning empty list."
            )
            return []
        rpc_name = "get_channel_timeline"
        rpc_params["p_channel_tag"] = filter_value.lower()
    elif filter_type == "user":
        if not filter_value or not isinstance(filter_value, str):
            debug_log(
                f"DB: User filter requested but invalid/missing target username: {filter_value}. Returning empty list."
            )
            return []
        rpc_name = "get_user_posts_timeline"
        rpc_params["p_target_username"] = filter_value
    else:
        debug_log(f"DB: Unknown filter type '{filter_type}'. Returning empty list.")
        return []

    if not rpc_name:
        debug_log("DB: Could not determine RPC name. Returning empty list.")
        return []

    try:
        debug_log(f"DB: Calling RPC '{rpc_name}' with params: {rpc_params}")
        resp = await asyncio.to_thread(
            db_client_ctx.get().rpc(rpc_name, rpc_params).execute
        )
        posts_data = resp.data or []
        debug_log(f"DB: RPC {rpc_name} returned {len(posts_data)} posts")

        return [
            {
                "id": p.get("post_id"),
                "user_id": p.get("author_id"),
                "content": p.get("eet_content"),
                "tags": p.get("eet_tags") or [],
                "users_mentioned": p.get("eet_users_mentioned") or [],
                "created_at": p.get("eet_created_at"),
                "username": p.get("author_username"),
                "display_name": p.get("author_display_name"),
            }
            for p in posts_data
        ]
    except Exception as e:
        debug_log(
            f"[DB ERROR] Calling RPC {rpc_name} for {current_username} with {target_filter}: {e}"
        )
        if config.itter_debug_mode:
            debug_log(traceback.format_exc())
        return []
