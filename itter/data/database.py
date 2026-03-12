# /database.py
import asyncio
from typing import Optional, Dict, Any, List, cast
from supabase import AsyncClient
from postgrest.types import CountMethod

from itter.core.utils import debug_log, hash_ip
from itter.core.config import EET_MAX_LENGTH, DEFAULT_TIMELINE_PAGE_SIZE

supabase_client: Optional[AsyncClient] = None


def init_db(client: AsyncClient) -> None:
    global supabase_client
    supabase_client = client
    debug_log("Database module initialized.")


async def db_get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    try:
        resp = (
            await supabase_client.table("users")
            .select("*")
            .eq("username", username)
            .execute()
        )
        data = cast(List[Dict[str, Any]], resp.data)
        return data[0] if data else None
    except Exception as e:
        debug_log(f"[DB ERROR] get_user_by_username: {e}")
        return None


async def db_get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    try:
        resp = (
            await supabase_client.table("users").select("*").eq("id", user_id).execute()
        )
        data = cast(List[Dict[str, Any]], resp.data)
        return data[0] if data else None
    except Exception as e:
        debug_log(f"[DB ERROR] get_user_by_id: {e}")
        return None


async def db_username_exists_case_insensitive(
    username: str,
) -> Optional[Dict[str, Any]]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    username_lower: str = username.lower()
    try:
        resp = (
            await supabase_client.table("users")
            .select("username")
            .ilike("username", username_lower)
            .execute()
        )
        data = cast(List[Dict[str, Any]], resp.data)
        return data[0] if data else None
    except Exception as e:
        debug_log(f"[DB ERROR] db_username_exists_case_insensitive: {e}")
        return None


async def db_create_user(username: str, public_key: str) -> None:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    try:
        user_resp = (
            await supabase_client.table("users")
            .insert({"username": username})
            .execute()
        )
        data = cast(List[Dict[str, Any]], user_resp.data)
        if not data:
            raise Exception("User creation failed, no data returned.")
        new_user_id: str = str(data[0]["id"])
        await (
            supabase_client.table("user_public_keys")
            .insert(
                {
                    "user_id": new_user_id,
                    "public_key": public_key,
                    "name": "initial-key",
                }
            )
            .execute()
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_create_user: {e}")
        raise e


async def db_update_profile(
    username: str,
    new_display_name: Optional[str],
    new_email: Optional[str],
    reset: bool = False,
) -> None:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    user = await db_get_user_by_username(username)
    if not user:
        raise ValueError("User not found for profile update.")

    update_data: Dict[str, Any] = {}
    if new_display_name is not None:
        update_data["display_name"] = new_display_name
    if new_email is not None:
        update_data["email"] = new_email
    if reset:
        update_data["display_name"] = None
        update_data["email"] = None

    if not update_data and not reset:
        raise ValueError("Nothing to update. Provide a new name and/or email.")

    try:
        await (
            supabase_client.table("users")
            .update(update_data)
            .eq("id", user["id"])
            .execute()
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_update_profile: {e}")
        raise e


async def db_get_profile_stats(username: str) -> Dict[str, Any]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    user = await db_get_user_by_username(username)
    if not user:
        raise ValueError("User not found for profile stats.")

    try:
        results = await asyncio.gather(
            supabase_client.table("posts")
            .select("id", count=CountMethod.exact)
            .eq("user_id", user["id"])
            .execute(),
            supabase_client.table("follows")
            .select("follower_id", count=CountMethod.exact)
            .eq("follower_id", user["id"])
            .execute(),
            supabase_client.table("follows")
            .select("following_id", count=CountMethod.exact)
            .eq("following_id", user["id"])
            .execute(),
        )
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


async def db_get_user_public_keys(user_id: str) -> List[Dict[str, Any]]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    try:
        resp = (
            await supabase_client.table("user_public_keys")
            .select("name, public_key, created_at")
            .eq("user_id", user_id)
            .execute()
        )
        return cast(List[Dict[str, Any]], resp.data) if resp.data else []
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_user_public_keys: {e}")
        return []


async def db_add_user_public_key(
    user_id: str, key_name: str, public_key_str: str
) -> None:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    try:
        await (
            supabase_client.table("user_public_keys")
            .insert(
                {"user_id": user_id, "name": key_name, "public_key": public_key_str}
            )
            .execute()
        )
    except Exception as e:
        if "duplicate key value violates unique constraint" in str(e):
            raise ValueError(
                "Hey there, no duplicates! This public key is already registered to your account."
            )
        raise e


async def db_remove_user_public_key(user_id: str, key_name: str) -> None:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    try:
        await (
            supabase_client.table("user_public_keys")
            .delete()
            .match({"user_id": user_id, "name": key_name})
            .execute()
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_remove_user_public_key: {e}")
        raise e


async def db_get_key_count_for_user(user_id: str) -> int:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    try:
        resp = (
            await supabase_client.table("user_public_keys")
            .select("id", count=CountMethod.exact)
            .eq("user_id", user_id)
            .execute()
        )
        return resp.count if resp.count is not None else 0
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_key_count_for_user: {e}")
        return 0


async def db_update_key_last_used(user_id: str, key_name: str) -> None:
    if not supabase_client:
        return
    try:
        await (
            supabase_client.table("user_public_keys")
            .update({"last_used_at": "now()"})
            .match({"user_id": user_id, "name": key_name})
            .execute()
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_update_key_last_used: {e}")


async def db_is_following(follower_username: str, following_username: str) -> bool:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    follower = await db_get_user_by_username(follower_username)
    following = await db_get_user_by_username(following_username)
    if not follower or not following:
        return False
    try:
        resp = (
            await supabase_client.table("follows")
            .select("follower_id", count=CountMethod.exact)
            .eq("follower_id", follower["id"])
            .eq("following_id", following["id"])
            .execute()
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
    user = await db_get_user_by_username(current_username)
    target = await db_get_user_by_username(target_username_to_follow)
    if not user or not target:
        raise ValueError("User not found for follow operation.")
    if user["id"] == target["id"]:
        raise ValueError("You cannot follow yourself, silly.")
    if await db_is_following(current_username, target_username_to_follow):
        raise ValueError(f"You are already following @{target_username_to_follow}.")
    try:
        await (
            supabase_client.table("follows")
            .insert({"follower_id": user["id"], "following_id": target["id"]})
            .execute()
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_follow_user: {e}")
        raise e


async def db_unfollow_user(
    current_username: str, target_username_to_unfollow: str
) -> None:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
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
        await (
            supabase_client.table("follows")
            .delete()
            .match({"follower_id": user["id"], "following_id": target["id"]})
            .execute()
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_unfollow_user: {e}")
        raise e


async def db_is_following_channel(username: str, channel_tag: str) -> bool:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    user = await db_get_user_by_username(username)
    if not user:
        return False
    try:
        resp = (
            await supabase_client.table("user_channel_follows")
            .select("user_id", count=CountMethod.exact)
            .eq("user_id", user["id"])
            .eq("channel_tag", channel_tag.lower())
            .execute()
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
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    channel_tag_lower: str = channel_tag.lower()
    user = await db_get_user_by_username(current_username)
    if not user:
        raise ValueError(f"User '{current_username}' not found.")
    if await db_is_following_channel(current_username, channel_tag_lower):
        raise ValueError(f"You are already following channel #{channel_tag_lower}.")
    try:
        await (
            supabase_client.table("user_channel_follows")
            .insert({"user_id": user["id"], "channel_tag": channel_tag_lower})
            .execute()
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_follow_channel: {e}")
        raise e


async def db_unfollow_channel(current_username: str, channel_tag: str) -> None:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    channel_tag_lower: str = channel_tag.lower()
    user = await db_get_user_by_username(current_username)
    if not user:
        raise ValueError(f"User '{current_username}' not found.")
    if not await db_is_following_channel(current_username, channel_tag_lower):
        raise ValueError(f"You are not following channel #{channel_tag_lower} anyway.")
    try:
        await (
            supabase_client.table("user_channel_follows")
            .delete()
            .match({"user_id": user["id"], "channel_tag": channel_tag_lower})
            .execute()
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_unfollow_channel: {e}")
        raise e


async def db_is_ignoring(ignorer_username: str, ignored_username: str) -> bool:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    ignorer = await db_get_user_by_username(ignorer_username)
    ignored = await db_get_user_by_username(ignored_username)
    if not ignorer or not ignored:
        return False
    try:
        resp = (
            await supabase_client.table("ignored_users")
            .select("ignorer_id", count=CountMethod.exact)
            .eq("ignorer_id", ignorer["id"])
            .eq("ignored_user_id", ignored["id"])
            .execute()
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
    user = await db_get_user_by_username(current_username)
    target = await db_get_user_by_username(target_username_to_ignore)
    if not user or not target:
        raise ValueError("User not found for ignore operation.")
    if user["id"] == target["id"]:
        raise ValueError("You cannot ignore yourself.")
    if await db_is_ignoring(current_username, target_username_to_ignore):
        raise ValueError(f"You are already ignoring @{target_username_to_ignore}.")
    try:
        await (
            supabase_client.table("ignored_users")
            .insert({"ignorer_id": user["id"], "ignored_user_id": target["id"]})
            .execute()
        )
    except Exception as e:
        debug_log(f"[DB ERROR] db_ignore_user: {e}")
        raise e


async def db_unignore_user(
    current_username: str, target_username_to_unignore: str
) -> None:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    user = await db_get_user_by_username(current_username)
    target = await db_get_user_by_username(target_username_to_unignore)
    if not user or not target:
        raise ValueError("User not found for unignore operation.")
    if not await db_is_ignoring(current_username, target_username_to_unignore):
        raise ValueError(f"You are not ignoring @{target_username_to_unignore} anyway.")
    try:
        await (
            supabase_client.table("ignored_users")
            .delete()
            .match({"ignorer_id": user["id"], "ignored_user_id": target["id"]})
            .execute()
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
        resp = (
            await supabase_client.table("ignored_users")
            .select("ignored_user_id")
            .eq("ignorer_id", user["id"])
            .execute()
        )
        data = cast(List[Dict[str, Any]], resp.data)
        return [str(item["ignored_user_id"]) for item in data] if data else []
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_ignored_user_ids for {username}: {e}")
        return []


async def db_get_user_following(current_username: str) -> List[Dict[str, Any]]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    user = await db_get_user_by_username(current_username)
    if not user:
        return []
    try:
        resp = await supabase_client.rpc(
            "get_user_following", {"input_user_id": user["id"]}
        ).execute()
        return cast(List[Dict[str, Any]], resp.data) if resp.data else []
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_user_following: {e}")
        return []


async def db_get_user_followers(current_username: str) -> List[Dict[str, Any]]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    user = await db_get_user_by_username(current_username)
    if not user:
        return []
    try:
        resp = await supabase_client.rpc(
            "get_user_followers", {"input_user_id": user["id"]}
        ).execute()
        return cast(List[Dict[str, Any]], resp.data) if resp.data else []
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_user_followers: {e}")
        return []


async def db_get_user_ignoring(current_username: str) -> List[Dict[str, Any]]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    user = await db_get_user_by_username(current_username)
    if not user:
        return []
    try:
        resp = await supabase_client.rpc(
            "get_user_ignoring", {"input_user_id": user["id"]}
        ).execute()
        return cast(List[Dict[str, Any]], resp.data) if resp.data else []
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_user_ignoring: {e}")
        return []


async def db_get_user_following_channels(current_username: str) -> List[Dict[str, Any]]:
    if not supabase_client:
        raise RuntimeError("Database not initialized")
    user = await db_get_user_by_username(current_username)
    if not user:
        return []
    try:
        resp = await supabase_client.rpc(
            "get_user_following_channels", {"input_user_id": user["id"]}
        ).execute()
        return cast(List[Dict[str, Any]], resp.data) if resp.data else []
    except Exception as e:
        debug_log(f"[DB ERROR] db_get_user_following_channels: {e}")
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
    if len(content) > EET_MAX_LENGTH:
        raise ValueError(f"Eet too long (max {EET_MAX_LENGTH} chars).")

    user = await db_get_user_by_username(username)
    if not user:
        raise ValueError("User not found for posting eet.")

    valid_user_mentions: List[str] = []
    if mentions:
        try:
            resp = (
                await supabase_client.table("users")
                .select("username")
                .in_("username", mentions)
                .execute()
            )
            data = cast(List[Dict[str, Any]], resp.data)
            if data:
                valid_user_mentions = [str(u["username"]) for u in data]
        except Exception as e:
            debug_log(f"[DB ERROR] Failed resolving mentions: {e}")

    post_data: Dict[str, Any] = {
        "user_id": user["id"],
        "content": content,
        "tags": tags,
        "users_mentioned": valid_user_mentions,
    }

    if client_ip:
        hashed_client_ip = hash_ip(client_ip)
        if hashed_client_ip:
            post_data["hashed_ip"] = hashed_client_ip

    try:
        await supabase_client.table("posts").insert(post_data).execute()
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
    user = await db_get_user_by_username(current_username)
    if not user:
        return []

    rpc_name: Optional[str] = None
    rpc_params: Dict[str, Any] = {
        "input_user_id": user["id"],
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
            return []
        rpc_name = "get_channel_timeline"
        rpc_params["p_channel_tag"] = filter_value.lower()
    elif filter_type == "user":
        if not filter_value or not isinstance(filter_value, str):
            return []
        rpc_name = "get_user_posts_timeline"
        rpc_params["p_target_username"] = filter_value
    else:
        return []

    try:
        resp = await supabase_client.rpc(rpc_name, rpc_params).execute()
        posts_data = cast(List[Dict[str, Any]], resp.data) or []
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
        debug_log(f"[DB ERROR] Calling RPC {rpc_name}: {e}")
        return []
