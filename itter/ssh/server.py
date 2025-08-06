import asyncio
import asyncssh
import re
from typing import Optional, Dict
from .shell import ItterShell

# Import from our modules
import itter.data.database as db
import itter.core.utils as utils

def init_ssh(sessions_dict: Dict[str, "ItterShell"]):
    """Initializes the SSH module with the active sessions reference."""
    global active_sessions_ref
    active_sessions_ref = sessions_dict
    utils.debug_log("SSH Server module initialized.")


class ItterSSHServer(asyncssh.SSHServer):
    def __init__(self):
        self.is_registration_attempt = False
        self.registration_username_candidate: Optional[str] = None
        self.submitted_public_key: Optional[str] = None
        self.current_username: Optional[str] = None
        super().__init__()

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        utils.debug_log(
            f"ItterSSHServer connection_made by {conn.get_extra_info('peername')}"
        )
        self._conn = conn

    def connection_lost(self, exc: Optional[Exception]) -> None:
        utils.debug_log(f"ItterSSHServer connection_lost: {exc}")
        if (
            self.current_username
            and active_sessions_ref is not None
            and self.current_username in active_sessions_ref
        ):
            utils.debug_log(
                f"Removing session for {self.current_username} due to connection loss."
            )
            # Use try-except in case the session was already removed somehow
            try:
                del active_sessions_ref[self.current_username]
            except KeyError:
                utils.debug_log(f"Session for {self.current_username} already removed.")
        self.current_username = None

    async def begin_auth(self, username: str) -> bool:
        # Reset state for this auth attempt
        self.is_registration_attempt = False
        self.registration_username_candidate = None
        utils.debug_log(
            f"begin_auth for '{username}'. Initial state: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'"
        )
        if username.startswith("register:"):
            potential_username = username[9:]
            error_message = None
            if not re.match(r"^[a-zA-Z0-9_]{3,20}$", potential_username):
                utils.debug_log(
                    f"Invalid registration username format: '{potential_username}'"
                )
                error_message = "Username must be 3-20 characters (letters, numbers, underscores only)."
            else:
                conflicting_db_username = await db.db_username_exists_case_insensitive(
                    potential_username
                )
                if conflicting_db_username:
                    utils.debug_log(
                        f"Registration attempt for '{potential_username}' rejected. Case-insensitive conflict with existing username: '{conflicting_db_username}'"
                    )
                    error_message = (
                        f"Sorry, the username '{potential_username}' is already taken."
                    )
            if error_message:
                await self._send_auth_failure_message(error_message)
                self.is_registration_attempt = False
                self.current_username = None
                self.registration_username_candidate = None
                utils.debug_log(
                    f"begin_auth returning False. Reason: {error_message}. Final state: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'"
                )
                return False
            self.is_registration_attempt = True
            self.registration_username_candidate = potential_username
            self.current_username = None
            utils.debug_log(
                f"Registration mode activated for: '{self.registration_username_candidate}'. State: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'. Returning True."
            )
            return True

        # Normal login
        self.is_registration_attempt = False
        self.registration_username_candidate = None
        user_data = await db.db_get_user_by_username(username)
        if not user_data:
            utils.debug_log(f"Login attempt for non-existent user: '{username}'")
            # For login failures, usually no special banner is needed.
            # The client will typically show "Permission denied".
            self.current_username = None
            utils.debug_log(
                f"begin_auth returning False for non-existent login user '{username}'. Final state: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'"
            )
            return False
        self.current_username = username
        utils.debug_log(
            f"User '{self.current_username}' found for login. State: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'. Returning True."
        )
        return True

    async def _send_auth_failure_message(self, message: str):
        if hasattr(self, "_conn") and self._conn:
            try:
                banner_message = message
                if not banner_message.endswith("\r\n"):
                    banner_message += "\r\n"
                utils.debug_log(
                    f"Attempting to send auth banner: {banner_message.strip()}"
                )
                self._conn.send_auth_banner(banner_message)

                await asyncio.sleep(0.1)  # Brief pause for banner

                utils.debug_log(
                    f"Requesting client disconnect after sending auth banner for: {message.strip()}"
                )
                # If begin_auth returns False, asyncssh should handle the auth failure.
                # Calling disconnect() here is an explicit request to close the connection
                # if it hasn't already started closing due to auth failure.
                # The try/except will catch errors if it's already closing.
                self._conn.disconnect(14, "Authentication failed")

            except asyncssh.Error as e:
                await asyncio.sleep(0.1)
                utils.debug_log(
                    f"Requesting client disconnect after sending auth banner for: {message.strip()} (Error: {e})"
                )
                self._conn.disconnect(14, "Authentication failed")
            except asyncssh.Error as e:
                utils.debug_log(
                    f"asyncssh error during _send_auth_failure_message (banner/disconnect): {e}"
                )
            except Exception as e:
                utils.debug_log(
                    f"Generic error during _send_auth_failure_message (banner/disconnect): {e}"
                )
        else:
            utils.debug_log(
                f"No connection object (_conn) available to send auth failure message: {message}"
            )

    def public_key_auth_supported(self) -> bool:
        return True

    async def validate_public_key(
        self, username_from_auth_begin: str, key: asyncssh.SSHKey
    ) -> bool:
        utils.debug_log(
            f"validate_public_key for '{username_from_auth_begin}'. Server state: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'"
        )
        try:
            self.submitted_public_key = key.export_public_key().decode().strip()
        except Exception as e:
            utils.debug_log(f"Error exporting public key: {e}")
            return False
        if self.is_registration_attempt:
            # For registration, registration_username_candidate should be set
            if not self.registration_username_candidate:
                utils.debug_log(
                    f"[CRITICAL] validate_public_key: is_registration_attempt is True, but registration_username_candidate is None for '{username_from_auth_begin}'. This is an inconsistent state."
                )
                return False
            utils.debug_log(
                f"Public key captured for registration of '{self.registration_username_candidate}' (original user in auth: '{username_from_auth_begin}'). Returning True."
            )
            return True

        # Not a registration attempt (self.is_registration_attempt is False)
        if not self.current_username:
            utils.debug_log(
                f"Public key validation attempted for '{username_from_auth_begin}' but self.current_username is None and not a registration attempt. Returning False."
            )
            return False

        # This must be a login attempt, self.current_username should match username_from_auth_begin (or be derived)
        if self.current_username != username_from_auth_begin:
            utils.debug_log(
                f"[WARNING] validate_public_key: username_from_auth_begin ('{username_from_auth_begin}') differs from self.current_username ('{self.current_username}') in login flow."
            )
            # This might indicate an issue if they are expected to be same. For now, proceed with self.current_username.

        user_obj = await db.db_get_user_by_username(self.current_username)
        if not user_obj:
            utils.debug_log(
                f"User '{self.current_username}' (for '{username_from_auth_begin}') not found. Returning False."
            )
            return False

        user_keys = await db.db_get_user_public_keys(user_obj["id"])
        if not user_keys:
            utils.debug_log(
                f"User '{self.current_username}' has no registered public keys. Returning False."
            )
            return False

        for key_record in user_keys:
            stored_key = key_record.get("public_key", "").strip()
            if stored_key and stored_key == self.submitted_public_key:
                utils.debug_log(
                    f"Key validation success for user '{self.current_username}'."
                )
                # Update last_used_at for the key here
                key_name = key_record.get("name")
                if key_name:
                    asyncio.create_task(
                        db.db_update_key_last_used(user_obj["id"], key_name)
                    )
                return True

        utils.debug_log(
            f"Key validation failure for user '{self.current_username}'. Submitted key not in user's list."
        )
        return False

    def session_requested(self) -> Optional["ItterShell"]:
        utils.debug_log(
            f"session_requested called. Server state: is_registration_attempt={self.is_registration_attempt}, current_username='{self.current_username}', registration_candidate='{self.registration_username_candidate}'"
        )
        shell_to_return: Optional[ItterShell] = None
        if self.is_registration_attempt:
            if self.registration_username_candidate and self.submitted_public_key:
                utils.debug_log(
                    f"Creating ItterShell for REGISTRATION of '{self.registration_username_candidate}'"
                )
                shell_to_return = ItterShell(
                    ssh_server_ref=self,
                    initial_username=None,
                    authenticated_key=self.submitted_public_key,
                    is_registration_flow=True,
                    registration_details=(
                        self.registration_username_candidate,
                        self.submitted_public_key,
                    ),
                )
            else:
                utils.debug_log(
                    f"[CRITICAL] session_requested: In registration flow but registration_username_candidate ('{self.registration_username_candidate}') or submitted_public_key is missing. Refusing session."
                )

                self._conn.send_auth_banner(
                    "Something went wrong during registration. Please try again.\r\n"
                )  # Optional
                self._conn.disconnect(14, "Authentication failed")
                return None  # Refuse session

                return None
        elif self.current_username:
            utils.debug_log(
                f"Creating ItterShell for LOGIN of '{self.current_username}'"
            )
            shell_to_return = ItterShell(
                ssh_server_ref=self,
                initial_username=self.current_username,
                authenticated_key=self.submitted_public_key,
                is_registration_flow=False,
                registration_details=None,
            )
        else:
            # This is the problematic state: no registration, no current_username.
            # This means begin_auth likely failed or didn't establish a user,
            # but other auth (e.g. public key without specific user context) passed.
            utils.debug_log(
                "[CRITICAL] session_requested: No valid user context (not registration, no current_username). Refusing session."
            )
            return None
        if shell_to_return and active_sessions_ref is not None:
            shell_to_return.set_active_sessions_ref(active_sessions_ref)
        elif shell_to_return and active_sessions_ref is None:
            utils.debug_log(
                "WARNING: active_sessions_ref is None when creating ItterShell! Shell will be created but may lack full functionality."
            )
        return shell_to_return
