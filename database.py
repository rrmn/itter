# /Users/roman/work/itter/database.py
import asyncio
from typing import Optional, Dict, Any, List
from supabase import Client
from utils import debug_log, hash_ip
import traceback
from config import ITTER_DEBUG_MODE, EET_MAX_LENGTH, DEFAULT_TIMELINE_PAGE_SIZE

# Placeholder for the client - will be initialized in main.py
supabase_client: Optional[Client] = None


def init_db(client: Client):
    """Initializes the database module with the Supabase client."""
    global supabase_client
    supabase_client = client
    debug_log("Database module initialized.")


# --- User Operations ---


async def db_get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: get_user_by_username('{username}')")
    try:
        resp = await asyncio.to_thread(
            supabase_client.table("users").select("*").eq("username", username).execute
        )
        return resp.data[0] if resp.data else None
    except Exception as e:
        debug_log(f"[DB ERROR] get_user_by_username: {e}")
        return None


async def db_get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: get_user_by_id('{user_id}')")
    try:
        resp = await asyncio.to_thread(
            supabase_client.table("users").select("*").eq("id", user_id).execute
        )
        return resp.data[0] if resp.data else None
    except Exception as e:
        debug_log(f"[DB ERROR] get_user_by_id: {e}")
        return None

async def db_username_exists_case_insensitive(username: str) -> Optional[str]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: db_username_exists_case_insensitive('{username}')")

    username_lower = username.lower()
    try:
        resp = await asyncio.to_thread(
            supabase_client.table("users")
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
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: Creating user '{username}'")
    try:
        await asyncio.to_thread(
            supabase_client.table("users")
            .insert({"username": username, "public_key": public_key})
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_create_user: {e}")
        # Re-raise crucial errors like unique constraint violations
        raise e


async def db_update_profile(
    username: str, new_display_name: Optional[str], new_email: Optional[str]
) -> None:
    if not supabase_client:
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

    if not update_data:
        raise ValueError("Nothing to update. Provide a new name and/or email.")

    try:
        await asyncio.to_thread(
            supabase_client.table("users")
            .update(update_data)
            .eq("id", user["id"])
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_update_profile: {e}")
        raise e


async def db_get_profile_stats(username: str) -> Dict[str, Any]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: get_profile_stats('{username}')")
    user = await db_get_user_by_username(username)
    if not user:
        raise ValueError("User not found for profile stats.")

    try:
        tasks = [
            asyncio.to_thread(
                supabase_client.table("posts")
                .select("id", count="exact")
                .eq("user_id", user["id"])
                .execute
            ),
            asyncio.to_thread(
                supabase_client.table("follows")
                .select("follower_id", count="exact")
                .eq("follower_id", user["id"])
                .execute
            ),
            asyncio.to_thread(
                supabase_client.table("follows")
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
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: is_following('{follower_username}', '{following_username}')")
    follower = await db_get_user_by_username(follower_username)
    following = await db_get_user_by_username(following_username)
    if not follower or not following:
        return False
    try:
        resp = await asyncio.to_thread(
            supabase_client.table("follows")
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
    if not supabase_client:
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
            supabase_client.table("follows")
            .insert({"follower_id": user["id"], "following_id": target["id"]})
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_follow_user: {e}")
        raise e


async def db_unfollow_user(
    current_username: str, target_username_to_unfollow: str
) -> None:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    debug_log(
        f"DB: unfollow_user('{current_username}', '{target_username_to_unfollow}')"
    )
    user = await db_get_user_by_username(current_username)
    target = await db_get_user_by_username(target_username_to_unfollow)

    if not user or not target:
        raise ValueError("User not found for unfollow operation.")
    if not await db_is_following(current_username, target_username_to_unfollow):
        raise ValueError(
            f"You are not following @{target_username_to_unfollow} anyway."
        )

    try:
        await asyncio.to_thread(
            supabase_client.table("follows")
            .delete()
            .match({"follower_id": user["id"], "following_id": target["id"]})
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_unfollow_user: {e}")
        raise e


async def db_is_ignoring(ignorer_username: str, ignored_username: str) -> bool:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    debug_log(f"DB: is_ignoring('{ignorer_username}', '{ignored_username}')")
    ignorer = await db_get_user_by_username(ignorer_username)
    ignored = await db_get_user_by_username(ignored_username)
    if not ignorer or not ignored:
        return False
    try:
        resp = await asyncio.to_thread(
            supabase_client.table("ignored_users")
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
    if not supabase_client:
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
            supabase_client.table("ignored_users")
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
    if not supabase_client:
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
            supabase_client.table("ignored_users")
            .delete()
            .match({"ignorer_id": user["id"], "ignored_user_id": target["id"]})
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_unignore_user: {e}")
        raise e


async def db_get_ignored_user_ids(username: str) -> List[str]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    user = await db_get_user_by_username(username)
    if not user:
        return []

    try:
        resp = await asyncio.to_thread(
            supabase_client.table("ignored_users")
            .select("ignored_user_id")
            .eq("ignorer_id", user["id"])
            .execute
        )
        return [item["ignored_user_id"] for item in resp.data] if resp.data else []
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_ignored_user_ids for {username}: {e}")
        return []


async def db_post_eet(
    username: str,
    content: str,
    tags: List[str],
    mentions: List[str],
    client_ip: Optional[str] = None,
) -> None:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    debug_log(
        f"DB: post_eet('{username}') -> tags={tags}, mentions={mentions}, client_ip={client_ip}"
    )
    if len(content) > EET_MAX_LENGTH:
        raise ValueError(f"Eet too long (max {EET_MAX_LENGTH} chars).")

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
            supabase_client.table("posts")
            .insert(post_data)
            .execute
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_post_eet: {e}")
        raise e


async def db_get_filtered_timeline_posts(
    current_username: str,
    target_filter: Dict[str, Any],
    page: int = 1,
    page_size: int = DEFAULT_TIMELINE_PAGE_SIZE,
) -> List[Dict[str, Any]]:
    if not supabase_client:
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

    rpc_name: Optional[str] = None
    rpc_params: Dict[str, Any] = {
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
        rpc_params["p_channel_tag"] = filter_value
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
            supabase_client.rpc(rpc_name, rpc_params).execute
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
        if ITTER_DEBUG_MODE:
            debug_log(traceback.format_exc())
        return []
