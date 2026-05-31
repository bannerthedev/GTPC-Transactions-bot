from __future__ import annotations

raise RuntimeError("BOOT TEST - Railway is running THIS main.py")

import asyncio
import json
import logging
import os
import random
from pathlib import Path

from dotenv import load_dotenv

import discord
from discord import app_commands
from discord.ext import commands

logging.basicConfig(level=logging.INFO, force=True)
log = logging.getLogger("discord")

load_dotenv()

log.warning("BOOT OK - main.py version 1")
log.warning(f"BOOTED FILE: {__file__}")
log.warning("SAFE_LOAD_JSON VERSION: 2026-05-29-1")


# ---------------- CONFIG (fill these) ----------------
GUILD_ID = 1273371437817790514  # your guild/server ID

# channels
TRANSACTIONS_ID = 1279153976205508718
MATCH_TIMES_CHANNEL_ID = 1503206384156803192
ASSIGNMENTS_CHANNEL_ID = 1503223033706582076
SCRIM_CATEGORY_ID = 1503216258508787732  # put your Scheduling category ID here, or 0 to create/use none
MATCH_SCORES_CHANNEL_ID = 1396869436342014073  # put your match-score channel ID here

# staff/team roles
CAPTAIN_ROLE_ID = 1273381699194978386
CO_CAPTAIN_ROLE_ID = 1396623890155044946
EXECUTIVE_ROLE_ID = 1508257747941199892
TEAM_PLAYER_ROLE_ID = 1508257725438627890

# assignment roles
HEAD_REF_ROLE_ID = 1487426934907605063
REF_ROLE_ID = 1273382013730160730
HEAD_CASTER_ROLE_ID = 1484661740196335699
CASTER_ROLE_ID = 1396624017431203891

FAQ_CHANNEL_ID = 1471959246273904875  # replace with the channel ID where FAQ should be posted
STREAM_WATCHER_ROLE_ID = 1508258495588470834  # 🎥 Stream Watcher
UNBORN_CAPTAIN_ROLE_ID = 1397318047164928000  # 🚀 Unborn Captain
EVENT_PING_ROLE_ID = 1508258542950285492      # 🎉 Event Ping

# ----------------------------------------------------

# ---------------- FILES ----------------
DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

TEAMS_FILE = DATA_DIR / "teams.json"
PLAYER_HISTORY_FILE = DATA_DIR / "player_history.json"
INVITES_FILE = DATA_DIR / "invites.json"
ROSTER_LOCK_FILE = DATA_DIR / "roster_lock.json"

# ---------------- HELPERS ----------------
def is_staff(user: discord.Member) -> bool:
    perms = user.guild_permissions
    return perms.administrator or perms.manage_guild


def gtag_to_hex(code: str) -> int:
    code = str(code).strip()
    if len(code) != 3 or not code.isdigit():
        raise ValueError("Gorilla Tag code must be 3 digits")
    r = int(code[0]) * 28
    g = int(code[1]) * 28
    b = int(code[2]) * 28
    return (r << 16) + (g << 8) + b


def _safe_load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"[ERROR] {path.name} invalid: {e}. Resetting.")
        path.write_text(json.dumps(default, indent=4), encoding="utf-8")
        return default


def load_teams() -> list:
    return _safe_load_json(TEAMS_FILE, [])


def save_teams(data: list) -> None:
    TEAMS_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def load_player_history() -> dict:
    return _safe_load_json(PLAYER_HISTORY_FILE, {})


def save_player_history(data: dict) -> None:
    PLAYER_HISTORY_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def load_invites() -> dict:
    return _safe_load_json(INVITES_FILE, {})


def save_invites(data: dict) -> None:
    INVITES_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def load_roster_locks() -> dict:
    data = _safe_load_json(ROSTER_LOCK_FILE, {"ALL": False})
    if "ALL" not in data:
        data["ALL"] = False
    return data


def save_roster_locks(data: dict) -> None:
    if "ALL" not in data:
        data["ALL"] = False
    ROSTER_LOCK_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def is_roster_locked(guild: discord.Guild, team: discord.Role) -> bool:
    locks = load_roster_locks()
    return bool(locks.get("ALL", False) or locks.get(str(team.id), False))


def role_in(member: discord.Member, role: discord.Role | None) -> bool:
    return role is not None and role in member.roles


def get_team_roles_from_file(guild: discord.Guild) -> list[discord.Role]:
    roles: list[discord.Role] = []
    for t in load_teams():
        rid = t.get("role_id")
        if not rid:
            continue
        r = guild.get_role(int(rid))
        if r:
            roles.append(r)
    return roles


def find_single_team_for_member(guild: discord.Guild, member: discord.Member) -> discord.Role | None:
    team_roles = get_team_roles_from_file(guild)
    owned = [r for r in team_roles if r in member.roles]
    return owned[0] if len(owned) == 1 else None


def add_pending_invite(team_role_id: int, user_id: int) -> None:
    invites = load_invites()
    key = str(team_role_id)
    invites.setdefault(key, [])
    uid = str(user_id)
    if uid not in invites[key]:
        invites[key].append(uid)
    save_invites(invites)


def remove_pending_invite(team_role_id: int, user_id: int) -> None:
    invites = load_invites()
    key = str(team_role_id)
    uid = str(user_id)
    if key in invites and uid in invites[key]:
        invites[key].remove(uid)
        if not invites[key]:
            invites.pop(key, None)
    save_invites(invites)


# ---------------- BOT SETUP ----------------
intents = discord.Intents.default()
intents.members = True

GUILD_OBJ = discord.Object(id=GUILD_ID)


class MyBot(commands.Bot):
    async def setup_hook(self) -> None:
        await self.add_cog(TeamManager(self))
        synced = await self.tree.sync(guild=GUILD_OBJ)
        print("SYNCED:", [c.name for c in synced])


bot = MyBot(command_prefix="!", intents=intents)


# ---------------- UI: INVITES ----------------
class InviteDMView(discord.ui.View):
    def __init__(self, guild_id: int, team_role_id: int, inviter_id: int, invited_id: int, timeout: float = 86400):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.team_role_id = team_role_id
        self.inviter_id = inviter_id
        self.invited_id = invited_id

    async def _finish(self, interaction: discord.Interaction):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.invited_id:
            await interaction.response.send_message("This invite is not for you.", ephemeral=True)
            return

        guild = interaction.client.get_guild(self.guild_id)
        if guild is None:
            await interaction.response.send_message("Server not found.", ephemeral=True)
            return

        member = guild.get_member(self.invited_id)
        team_role = guild.get_role(self.team_role_id)
        team_player_role = guild.get_role(TEAM_PLAYER_ROLE_ID)

        if member is None or team_role is None or team_player_role is None:
            await interaction.response.send_message("Invite is no longer valid.", ephemeral=True)
            return

        # --- NEW: ensure member is not already on a team ---
        existing_team = find_single_team_for_member(guild, member)
        if existing_team is not None:
            await interaction.response.send_message(
                f"{member.mention} already has a team",
                ephemeral=True,
            )
            return
        # ---------------------------------------------------

        try:
            await member.add_roles(team_role, team_player_role, reason="Accepted team invite")
        except Exception as e:
            await interaction.response.send_message(f"Failed to add roles: {e}", ephemeral=True)
            return

        remove_pending_invite(team_role.id, member.id)

        await interaction.response.send_message("You have accepted this invite.", ephemeral=True)
        await self._finish(interaction)

        tx_ch = guild.get_channel(TRANSACTIONS_ID)
        if tx_ch:
            await tx_ch.send(f"{member.mention} Has Joined **{team_role.name}**")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.invited_id:
            await interaction.response.send_message("This invite is not for you.", ephemeral=True)
            return

        guild = interaction.client.get_guild(self.guild_id)
        if guild:
            remove_pending_invite(self.team_role_id, self.invited_id)

        await interaction.response.send_message("You have denied this invite.", ephemeral=True)
        await self._finish(interaction)


class InviteUserSelectView(discord.ui.View):
    def __init__(self, requester_id: int, team_role_id: int, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.requester_id = requester_id
        self.team_role_id = team_role_id

        self.user_select = discord.ui.UserSelect(
            placeholder="Select a player to invite...",
            min_values=1,
            max_values=1,
        )
        self.user_select.callback = self.on_select
        self.add_item(self.user_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("You can’t use this menu.", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Guild not found.", ephemeral=True)
            return

        team_role = guild.get_role(self.team_role_id)
        if team_role is None:
            await interaction.response.send_message("Team role not found.", ephemeral=True)
            return

        target: discord.Member = self.user_select.values[0]

        if team_role in target.roles:
            await interaction.response.send_message(f"{target.mention} is already on {team_role.mention}.", ephemeral=True)
            return

        add_pending_invite(team_role.id, target.id)

        dm_view = InviteDMView(
            guild_id=guild.id,
            team_role_id=team_role.id,
            inviter_id=interaction.user.id,
            invited_id=target.id,
        )

        dm_text = (
            f"# You Have been invited to {team_role.name}\n"
            f"{interaction.user.mention} has invited you to {team_role.name}"
        )

        try:
            await target.send(dm_text, view=dm_view)
        except discord.Forbidden:
            remove_pending_invite(team_role.id, target.id)
            await interaction.response.send_message(
                f"Could not DM {target.mention}. They may have DMs closed.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=f"Invite sent to {target.mention}. Ask them to check their DMs.",
            view=None,
        )

# ---------------- UI: ROSTER ----------------
class TeamRosterView(discord.ui.View):
    def __init__(self, options: list[discord.SelectOption], requester_id: int, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.requester_id = requester_id

        self.select = discord.ui.Select(
            placeholder="Choose a team...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("You are not allowed to use this menu.", ephemeral=True)
            return False
        return True

    async def select_callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Guild not found.", ephemeral=True)
            return

        team_role = guild.get_role(int(self.select.values[0]))
        if team_role is None:
            await interaction.response.send_message("Team role not found.", ephemeral=True)
            return

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        cocap_role = guild.get_role(CO_CAPTAIN_ROLE_ID)
        executive_role = guild.get_role(EXECUTIVE_ROLE_ID)
        team_player_role = guild.get_role(TEAM_PLAYER_ROLE_ID)

        members = list(team_role.members)

        executives = [m for m in members if role_in(m, executive_role)]
        captains = [m for m in members if role_in(m, captain_role)]
        cocaps = [m for m in members if role_in(m, cocap_role)]

        players = []
        for m in members:
            if role_in(m, executive_role) or role_in(m, captain_role) or role_in(m, cocap_role):
                continue
            if role_in(m, team_player_role):
                players.append(m)

        cocaps_display = cocaps[:2]
        players_display = players[:12]
        player_count = min(len(players), 12)

        invites = load_invites()
        pending_ids = invites.get(str(team_role.id), [])
        pending_mentions = []
        for uid in pending_ids:
            mem = guild.get_member(int(uid))
            if mem:
                pending_mentions.append(mem.mention)

        def fmt_block(title: str, lst: list[discord.Member]):
            out = [f"{title}:\n"]
            if not lst:
                out.append("> • None\n\n")
            else:
                for m in lst:
                    out.append(f"> • {m.mention}\n")
                out.append("\n")
            return out

        lines: list[str] = [f"# ROSTER OF {team_role.name}\n\n"]
        lines += fmt_block("Team Executive", executives)
        lines += fmt_block("Captain", captains)
        lines += fmt_block("Co-Captain", cocaps_display)

        lines.append("Players:\n")
        if players_display:
            for m in players_display:
                lines.append(f"> • {m.mention}\n")
        else:
            lines.append("> • None\n")

        lines.append(f"\n{player_count}/12\n\n")
        lines.append("Pending Invites:\n")
        lines.append(", ".join(pending_mentions) if pending_mentions else "None")

        await interaction.response.edit_message(content="".join(lines), view=None)

class FAQRoleView(discord.ui.View):
    def __init__(self, timeout: float = 0):
        # timeout=0 → persistent view (doesn't auto-timeout)
        super().__init__(timeout=timeout)

    async def _toggle_role(
        self,
        interaction: discord.Interaction,
        role_id: int,
        role_name: str,
    ):
        guild = interaction.guild
        member = guild.get_member(interaction.user.id) if guild else None
        if guild is None or member is None:
            await interaction.response.send_message("Guild error.", ephemeral=True)
            return

        role = guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message(
                f"{role_name} role is not configured correctly.",
                ephemeral=True,
            )
            return

        # toggle: if user has role, remove; else add
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="FAQ auto-role toggle")
            except Exception as e:
                await interaction.response.send_message(
                    f"Failed to remove {role_name} role: {e}",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"Removed **{role_name}**.",
                ephemeral=True,
            )
        else:
            try:
                await member.add_roles(role, reason="FAQ auto-role toggle")
            except Exception as e:
                await interaction.response.send_message(
                    f"Failed to add {role_name} role: {e}",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"Added **{role_name}**.",
                ephemeral=True,
            )

    @discord.ui.button(label="🎥 Stream Watcher", style=discord.ButtonStyle.blurple)
    async def stream_watcher(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._toggle_role(
            interaction,
            STREAM_WATCHER_ROLE_ID,
            "Stream Watcher",
        )

    @discord.ui.button(label="🚀 Unborn Captain", style=discord.ButtonStyle.blurple)
    async def unborn_captain(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._toggle_role(
            interaction,
            UNBORN_CAPTAIN_ROLE_ID,
            "Unborn Captain",
        )

    @discord.ui.button(label="🎉 Event Ping", style=discord.ButtonStyle.blurple)
    async def event_ping(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._toggle_role(
            interaction,
            EVENT_PING_ROLE_ID,
            "Event Ping",
        )

 
# ---------------- MATCH: ACCEPT -> ASSIGNMENTS ----------------
class MatchAcceptView(discord.ui.View):
    def __init__(self, week: str, time_str: str, header: str, team1_id: int, team2_id: int, timeout: float = 900):
        super().__init__(timeout=timeout)
        self.week = week
        self.time_str = time_str
        self.header = header
        self.team1_id = team1_id
        self.team2_id = team2_id

        self.team1_accepted = False
        self.team2_accepted = False

        self.accept_message_id: int | None = None
        self.accept_channel_id: int | None = None

        b1 = discord.ui.Button(label="Accept for Team 1", style=discord.ButtonStyle.green, custom_id="accept_team1")
        b2 = discord.ui.Button(label="Accept for Team 2", style=discord.ButtonStyle.green, custom_id="accept_team2")
        b1.callback = self.accept_team1
        b2.callback = self.accept_team2
        self.add_item(b1)
        self.add_item(b2)

    def _is_team_staff(self, member: discord.Member, team_role_id: int) -> bool:
        guild = member.guild
        team_role = guild.get_role(team_role_id)
        if team_role is None or team_role not in member.roles:
            return False
        return (
            role_in(member, guild.get_role(CAPTAIN_ROLE_ID))
            or role_in(member, guild.get_role(CO_CAPTAIN_ROLE_ID))
            or role_in(member, guild.get_role(EXECUTIVE_ROLE_ID))
        )

    async def _handle_accept(self, interaction: discord.Interaction, team_index: int):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Guild not found.", ephemeral=True)
            return

        member = guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message("Could not find you in this guild.", ephemeral=True)
            return

        if team_index == 1:
            team_id, other_id = self.team1_id, self.team2_id
        else:
            team_id, other_id = self.team2_id, self.team1_id

        if not self._is_team_staff(member, team_id):
            if self._is_team_staff(member, other_id):
                await interaction.response.send_message(
                    "You are not the captain, co-captain, or executive of this team.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "You must be a captain, co-captain, or executive of the team to accept.",
                    ephemeral=True,
                )
            return

        if team_index == 1:
            if self.team1_accepted:
                await interaction.response.send_message("Team 1 has already accepted.", ephemeral=True)
                return
            self.team1_accepted = True
            await interaction.response.send_message("Team 1 has accepted.", ephemeral=True)
        else:
            if self.team2_accepted:
                await interaction.response.send_message("Team 2 has already accepted.", ephemeral=True)
                return
            self.team2_accepted = True
            await interaction.response.send_message("Team 2 has accepted.", ephemeral=True)

        if self.team1_accepted and self.team2_accepted:
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            await self._finalize_and_post(guild)

    async def accept_team1(self, interaction: discord.Interaction):
        await self._handle_accept(interaction, 1)

    async def accept_team2(self, interaction: discord.Interaction):
        await self._handle_accept(interaction, 2)

    async def _finalize_and_post(self, guild: discord.Guild):
        # freeze accept message in-place
        if self.accept_channel_id and self.accept_message_id:
            ch = guild.get_channel(self.accept_channel_id)
            if ch:
                try:
                    msg = await ch.fetch_message(self.accept_message_id)
                    await msg.edit(view=self)
                except Exception:
                    pass

        # create match-times post
        mt_ch = guild.get_channel(MATCH_TIMES_CHANNEL_ID)
        mt_msg_id = None
        if mt_ch:
            mt_content = (
                f"{self.header}\n" if self.header else ""
            ) + (
                f"> **{self.week}\n"
                f"> Time: {self.time_str}\n"
                f"> Referee: Unassigned\n"
                f"> Caster: Unassigned **"
            )
            mt = await mt_ch.send(mt_content)
            mt_msg_id = mt.id

        # create assignments post with buttons + pings
        as_ch = guild.get_channel(ASSIGNMENTS_CHANNEL_ID)
        if as_ch:
            head_ref = guild.get_role(HEAD_REF_ROLE_ID)
            ref_role = guild.get_role(REF_ROLE_ID)
            head_caster = guild.get_role(HEAD_CASTER_ROLE_ID)
            caster_role = guild.get_role(CASTER_ROLE_ID)
            ping_line = " ".join([r.mention for r in (head_ref, ref_role, head_caster, caster_role) if r])

            ass_block = (
                f"{self.header}\n" if self.header else ""
            ) + (
                f"> **{self.week}\n"
                f"> Time: {self.time_str}\n"
                f"> Referee: Unassigned\n"
                f"> Caster: Unassigned **"
            )
            content = (ping_line + "\n" if ping_line else "") + ass_block

            view = AssignmentView(self.week, self.time_str, mt_msg_id, ping_line)
            await as_ch.send(content, view=view)


# ---------------- TEAM MANAGER COG ----------------
class TeamManager(commands.Cog):
    def __init__(self, bot_: commands.Bot):
        self.bot = bot_

    async def _roster_lock_block(self, interaction: discord.Interaction, team: discord.Role) -> bool:
        """Return True if a command should be blocked due to roster lock."""
        if is_roster_locked(interaction.guild, team):
            if interaction.response.is_done():
                await interaction.followup.send("Roster lock has been enabled.", ephemeral=True)
            else:
                await interaction.response.send_message("Roster lock has been enabled.", ephemeral=True)
            return True
        return False

    # -------- /create-team (admin, hex color) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="create-team",
        description="Create a team role and assign the captain (admin only)."
    )
    @app_commands.describe(
        team_name="Team name",
        captain="Captain member",
        color_code="Hex color code, e.g. FF00FF or #FF00FF",
    )
    async def create_team(
        self,
        interaction: discord.Interaction,
        team_name: str,
        captain: discord.Member,
        color_code: str,
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You must be an administrator to use this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        # parse hex color
        try:
            code = color_code.strip().lstrip("#")
            if len(code) != 6 or any(c not in "0123456789abcdefABCDEF" for c in code):
                raise ValueError("Hex color must be 6 hex digits, e.g. FF00FF")
            color = discord.Color(int(code, 16))
        except Exception as e:
            await interaction.followup.send(f"Invalid hex color code: {e}", ephemeral=True)
            return

        try:
            team_role = await guild.create_role(
                name=team_name,
                colour=color,
                reason=f"Team created by {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Missing permission to create roles.", ephemeral=True
            )
            return

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)

        try:
            roles_to_add = [team_role]
            if captain_role:
                roles_to_add.append(captain_role)
            await captain.add_roles(
                *roles_to_add, reason=f"Assigned as captain for {team_name}"
            )
        except Exception as e:
            print(f"[ERROR] Role assignment error: {e}")

        teams = load_teams()
        teams.append({"role_id": team_role.id, "name": team_name})
        save_teams(teams)

        tx = (
            "**New Team Created!**\n"
            f"• Team Name: {team_role.mention}\n"
            f"• Team Captain: {captain.mention}"
        )
        ch = guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send(tx)

        await interaction.followup.send("Team created.", ephemeral=True)

            # -------- /change-color-code (admin) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="change-color-code",
        description="Change a team's role color using a hex color code (admin only)."
    )
    @app_commands.describe(
        team="Team role whose color you want to change",
        color_code="New hex color code, e.g. 00FF00 or #00FF00"
    )
    async def change_color_code(
        self,
        interaction: discord.Interaction,
        team: discord.Role,
        color_code: str,
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You must be an administrator to use this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        # parse new hex color
        try:
            new_code = color_code.strip().lstrip("#")
            if len(new_code) != 6 or any(c not in "0123456789abcdefABCDEF" for c in new_code):
                raise ValueError("Hex color must be 6 hex digits, e.g. FF00FF")
            new_color = discord.Color(int(new_code, 16))
        except Exception as e:
            await interaction.followup.send(f"Invalid hex color code: {e}", ephemeral=True)
            return

        # old color
        old_color = team.colour
        old_code = f"{old_color.value:06X}"

        # change the role color
        try:
            await team.edit(colour=new_color, reason=f"Color changed by {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to edit that role's color.", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"Failed to change color: {e}", ephemeral=True)
            return

        # transactions
        guild = interaction.guild
        tx_ch = guild.get_channel(TRANSACTIONS_ID)
        if tx_ch:
            await tx_ch.send(
                f"# {team.mention} HAS CHANGED THERE COLOR CODE\n"
                f"Color Code Changed {old_code} to {new_code.upper()}"
            )

        await interaction.followup.send(
            f"Color for {team.mention} changed from {old_code} to {new_code.upper()}.",
            ephemeral=True,
        )

    # -------- /change-captain (captains only) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="change-captain",
        description="Change your team's captain to another member (captains only)."
    )
    @app_commands.describe(
        member="The new captain (must already be on your team)"
    )
    async def change_captain(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        me = guild.get_member(interaction.user.id)

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        if not role_in(me, captain_role):
            await interaction.followup.send(
                "Only the current team captain can use this command.",
                ephemeral=True,
            )
            return

        team = find_single_team_for_member(guild, me)
        if team is None:
            await interaction.followup.send(
                "You must be on exactly 1 team to use this.",
                ephemeral=True,
            )
            return

        # optional: respect roster lock
        if await self._roster_lock_block(interaction, team):
            return

        # new captain must be on the same team
        if team not in member.roles:
            await interaction.followup.send(
                f"{member.mention} must already be on your roster ({team.mention}).",
                ephemeral=True,
            )
            return

        # find old captain(s) on this team
        old_captains = [m for m in team.members if role_in(m, captain_role)]
        # remove role from all old captains, then assign to new one
        try:
            for oc in old_captains:
                if oc.id != member.id and captain_role in oc.roles:
                    await oc.remove_roles(
                        captain_role,
                        reason=f"Captain changed by {interaction.user}",
                    )
            if captain_role not in member.roles:
                await member.add_roles(
                    captain_role,
                    reason=f"Promoted to captain by {interaction.user}",
                )
        except Exception as e:
            await interaction.followup.send(
                f"Failed to change captain: {e}",
                ephemeral=True,
            )
            return

        # transaction log
        old_cap_mentions = (
            ", ".join(oc.mention for oc in old_captains) if old_captains else "None"
        )
        tx_ch = guild.get_channel(TRANSACTIONS_ID)
        if tx_ch:
            await tx_ch.send(
                f"# {team.mention} HAS CHANGED THERE CAPTAIN\n"
                f"old captain: {old_cap_mentions} new captain: {member.mention}"
            )

        await interaction.followup.send(
            f"Captain changed to {member.mention}.",
            ephemeral=True,
        )


    # -------- /invite (captains + co-captains) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="invite",
        description="Invite a player to your team (captains and co-captains only)."
    )
    async def invite_player(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        me = guild.get_member(interaction.user.id)
        if me is None:
            await interaction.response.send_message("Could not find you in this server.", ephemeral=True)
            return

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        cocap_role = guild.get_role(CO_CAPTAIN_ROLE_ID)

        if not role_in(me, captain_role) and not role_in(me, cocap_role):
            await interaction.response.send_message(
                "Only captains and co-captains can use this command.",
                ephemeral=True,
            )
            return

        team = find_single_team_for_member(guild, me)
        if team is None:
            await interaction.response.send_message(
                "You must be on exactly 1 team to use this.",
                ephemeral=True,
            )
            return

        if await self._roster_lock_block(interaction, team):
            return

        await interaction.response.send_message(
            "Select a player to invite:",
            view=InviteUserSelectView(interaction.user.id, team.id),
            ephemeral=True,
        )

    # -------- /leave (team members) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="leave",
        description="Leave your current team and remove all associated roles."
    )
    async def leave(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = guild.get_member(interaction.user.id)

        team = find_single_team_for_member(guild, member)
        if team is None:
            await interaction.followup.send("You are not on exactly one team.", ephemeral=True)
            return

        if is_roster_locked(guild, team):
            await interaction.followup.send("Roster lock has been enabled.", ephemeral=True)
            return

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        cocap_role = guild.get_role(CO_CAPTAIN_ROLE_ID)
        exec_role = guild.get_role(EXECUTIVE_ROLE_ID)
        team_player_role = guild.get_role(TEAM_PLAYER_ROLE_ID)

        roles_to_remove = [team]
        for r in (captain_role, cocap_role, exec_role, team_player_role):
            if r and r in member.roles:
                roles_to_remove.append(r)

        try:
            await member.remove_roles(*roles_to_remove, reason="Left team via /leave")
        except Exception as e:
            await interaction.followup.send(f"Failed to remove team roles: {e}", ephemeral=True)
            return

        # transactions
        ch = guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send(f"{member.mention} has left **{team.name}**")

        await interaction.followup.send("You have left your team and your team roles were removed.", ephemeral=True)


    # -------- /list-teams (everyone, ephemeral) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="list-teams", description="List all teams (everyone).")
    async def list_teams(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        data = load_teams()
        if not data:
            await interaction.followup.send("No teams found.", ephemeral=True)
            return

        lines = ["Below is a list of teams:\n"]
        for entry in data:
            rid = entry.get("role_id")
            name = entry.get("name", "Unknown Team")
            role = guild.get_role(int(rid)) if rid else None
            if role:
                lines.append(f"> {role.mention} ({name})")
            else:
                lines.append(f"> {name} (role not found)")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # -------- /roster (everyone, ephemeral, dropdown) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="roster", description="View a team's roster (everyone).")
    async def roster(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        teams_data = load_teams()
        if not teams_data:
            await interaction.followup.send("No teams found in teams.json.", ephemeral=True)
            return

        options: list[discord.SelectOption] = []
        for t in teams_data:
            rid = t.get("role_id")
            name = t.get("name", "Unknown Team")
            if not rid:
                continue
            role = guild.get_role(int(rid))
            if role:
                options.append(
                    discord.SelectOption(
                        label=name,
                        value=str(role.id),
                        description=f"View roster for {name}",
                    )
                )

        if not options:
            await interaction.followup.send(
                "No valid team roles found in this server.", ephemeral=True
            )
            return

        await interaction.followup.send(
            "Select a team to view its roster:",
            view=TeamRosterView(options, interaction.user.id),
            ephemeral=True,
        )

    # -------- /player-info (everyone, can view others) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="player-info",
        description="View a player's league information (current and past teams).",
    )
    @app_commands.describe(
        member="The player to look up (leave empty to view yourself)"
    )
    async def player_info(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        if member is None:
            member = guild.get_member(interaction.user.id)

        current_team_role = find_single_team_for_member(guild, member)
        current_team_mention = current_team_role.mention if current_team_role else "None"

        history = load_player_history()
        entry = history.get(str(member.id), {})
        past = entry.get("past_teams", [])

        if current_team_role is None and not past:
            await interaction.followup.send(
                f"{member.mention} does not have any league information!",
                ephemeral=True,
            )
            return

        lines = [
            f"# League Information for {member.mention}:\n",
            f"Current Team: {current_team_mention}",
            "Past Teams:",
        ]
        if past:
            for name in past:
                lines.append(f"> {name}")
        else:
            lines.append("> None")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # -------- /add-executive --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="add-executive",
        description="Promote a roster member to Team Executive (captains only)."
    )
    @app_commands.describe(executive="Member to promote (must already be on your team)")
    async def add_executive(self, interaction: discord.Interaction, executive: discord.Member):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        me = guild.get_member(interaction.user.id)

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        exec_role = guild.get_role(EXECUTIVE_ROLE_ID)

        if not role_in(me, captain_role):
            await interaction.followup.send("Only team captains can use this command.", ephemeral=True)
            return
        if exec_role is None:
            await interaction.followup.send("Executive role not configured.", ephemeral=True)
            return

        team = find_single_team_for_member(guild, me)
        if team is None:
            await interaction.followup.send("You must be on exactly 1 team to use this.", ephemeral=True)
            return

        if await self._roster_lock_block(interaction, team):
            return

        if team not in executive.roles:
            await interaction.followup.send(f"{executive.mention} must already be on your roster ({team.mention}).", ephemeral=True)
            return

        if exec_role in executive.roles:
            await interaction.followup.send(f"{executive.mention} is already an executive.", ephemeral=True)
            return

        await executive.add_roles(exec_role, reason=f"Promoted to executive by {interaction.user}")

        ch = guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send(f"{executive.mention} Has been Promoted To Team Executive By {interaction.user.mention}")

        await interaction.followup.send("Executive added.", ephemeral=True)

    # -------- /remove-executive --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="remove-executive",
        description="Demote your team's executive to Team Player (captains only)."
    )
    async def remove_executive(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        me = guild.get_member(interaction.user.id)

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        exec_role = guild.get_role(EXECUTIVE_ROLE_ID)
        team_player_role = guild.get_role(TEAM_PLAYER_ROLE_ID)

        if not role_in(me, captain_role):
            await interaction.followup.send("Only team captains can use this command.", ephemeral=True)
            return
        if exec_role is None or team_player_role is None:
            await interaction.followup.send("Executive/Team Player role not configured.", ephemeral=True)
            return

        team = find_single_team_for_member(guild, me)
        if team is None:
            await interaction.followup.send("You must be on exactly 1 team to use this.", ephemeral=True)
            return

        if await self._roster_lock_block(interaction, team):
            return

        executive_member = next((m for m in team.members if exec_role in m.roles), None)
        if executive_member is None:
            await interaction.followup.send("No executive found on your team.", ephemeral=True)
            return

        await executive_member.remove_roles(exec_role, reason=f"Demoted by {interaction.user}")
        if team_player_role not in executive_member.roles:
            await executive_member.add_roles(team_player_role, reason=f"Demoted by {interaction.user}")

        ch = guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send(f"{executive_member.mention} Has been Demoted from Team Executive By {interaction.user.mention}")

        await interaction.followup.send("Executive demoted.", ephemeral=True)

    # -------- /add-co-captain --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="add-co-captain",
        description="Promote a roster member to Co-Captain (captains only, max 2)."
    )
    @app_commands.describe(member="Member to promote (must already be on your team)")
    async def add_co_captain(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        me = guild.get_member(interaction.user.id)

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        cocap_role = guild.get_role(CO_CAPTAIN_ROLE_ID)

        if not role_in(me, captain_role):
            await interaction.followup.send("Only team captains can use this command.", ephemeral=True)
            return
        if cocap_role is None:
            await interaction.followup.send("Co-Captain role not configured.", ephemeral=True)
            return

        team = find_single_team_for_member(guild, me)
        if team is None:
            await interaction.followup.send("You must be on exactly 1 team to use this.", ephemeral=True)
            return

        if await self._roster_lock_block(interaction, team):
            return

        if team not in member.roles:
            await interaction.followup.send(f"{member.mention} must already be on your roster ({team.mention}).", ephemeral=True)
            return

        current_cocaps = [m for m in team.members if cocap_role in m.roles]
        if len(current_cocaps) >= 2:
            await interaction.followup.send("Your team already has 2 Co-Captains.", ephemeral=True)
            return

        if cocap_role in member.roles:
            await interaction.followup.send("That member is already a Co-Captain.", ephemeral=True)
            return

        await member.add_roles(cocap_role, reason=f"Promoted by {interaction.user}")

        ch = guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send(f"{member.mention} Has Been Promoted to co-captain by {interaction.user.mention}")

        await interaction.followup.send("Co-Captain added.", ephemeral=True)

    # -------- /remove-co-captain --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="remove-co-captain",
        description="Demote a co-captain to Team Player (captains only)."
    )
    @app_commands.describe(member="Co-captain to demote")
    async def remove_co_captain(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        me = guild.get_member(interaction.user.id)

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        cocap_role = guild.get_role(CO_CAPTAIN_ROLE_ID)
        team_player_role = guild.get_role(TEAM_PLAYER_ROLE_ID)

        if not role_in(me, captain_role):
            await interaction.followup.send("Only team captains can use this command.", ephemeral=True)
            return
        if cocap_role is None or team_player_role is None:
            await interaction.followup.send("Co-Captain/Team Player role not configured.", ephemeral=True)
            return

        team = find_single_team_for_member(guild, me)
        if team is None:
            await interaction.followup.send("You must be on exactly 1 team to use this.", ephemeral=True)
            return

        if await self._roster_lock_block(interaction, team):
            return

        if team not in member.roles or cocap_role not in member.roles:
            await interaction.followup.send("That member is not a Co-Captain on your team.", ephemeral=True)
            return

        await member.remove_roles(cocap_role, reason=f"Demoted by {interaction.user}")
        if team_player_role not in member.roles:
            await member.add_roles(team_player_role, reason=f"Demoted by {interaction.user}")

        ch = guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send(f"{member.mention} Has Been Demoted From Co-captain by {interaction.user.mention}")

        await interaction.followup.send("Co-Captain demoted.", ephemeral=True)

    # -------- /disband (admin) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="disband",
        description="Disband a team (admin only)."
    )
    @app_commands.describe(team="Team role to disband")
    async def disband(self, interaction: discord.Interaction, team: discord.Role):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        cocap_role = guild.get_role(CO_CAPTAIN_ROLE_ID)
        exec_role = guild.get_role(EXECUTIVE_ROLE_ID)
        team_player_role = guild.get_role(TEAM_PLAYER_ROLE_ID)

        members = list(team.members)

        for m in members:
            roles_to_remove = [team]
            for r in (captain_role, cocap_role, exec_role, team_player_role):
                if r and r in m.roles:
                    roles_to_remove.append(r)
            try:
                await m.remove_roles(*roles_to_remove, reason=f"Team {team.name} disbanded")
            except Exception as e:
                print(f"[ERROR] remove roles from {m}: {e}")

        hist = load_player_history()
        for m in members:
            uid = str(m.id)
            entry = hist.get(uid, {})
            past = entry.get("past_teams", [])
            if team.name not in past:
                past.append(team.name)
            entry["past_teams"] = past
            hist[uid] = entry
        save_player_history(hist)

        try:
            await team.delete(reason="Team disbanded")
        except Exception as e:
            print(f"[ERROR] delete team role: {e}")

        teams = [t for t in load_teams() if str(t.get("role_id")) != str(team.id)]
        save_teams(teams)

        ch = guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send(f"# **{team.name}** has been disbanded #")

        await interaction.followup.send("Team disbanded.", ephemeral=True)

    # -------- /disband-all (admin) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="disband-all",
        description="Disband ALL teams (admin only)."
    )
    async def disband_all(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        teams_data = load_teams()
        team_roles: list[discord.Role] = []
        for t in teams_data:
            rid = t.get("role_id")
            if rid:
                role = guild.get_role(int(rid))
                if role:
                    team_roles.append(role)

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        cocap_role = guild.get_role(CO_CAPTAIN_ROLE_ID)
        exec_role = guild.get_role(EXECUTIVE_ROLE_ID)
        team_player_role = guild.get_role(TEAM_PLAYER_ROLE_ID)

        affected_members: set[discord.Member] = set()
        for tr in team_roles:
            for m in tr.members:
                affected_members.add(m)

        # remove roles
        for m in affected_members:
            roles_to_remove: list[discord.Role] = []
            for tr in team_roles:
                if tr in m.roles:
                    roles_to_remove.append(tr)
            for r in (captain_role, cocap_role, exec_role, team_player_role):
                if r and r in m.roles:
                    roles_to_remove.append(r)
            if roles_to_remove:
                try:
                    await m.remove_roles(*roles_to_remove, reason="All teams disbanded")
                except Exception as e:
                    print(f"[ERROR] remove roles: {e}")

        # history
        try:
            hist = load_player_history()
            names = [r.name for r in team_roles]
            for m in affected_members:
                uid = str(m.id)
                entry = hist.get(uid, {})
                past = entry.get("past_teams", [])
                for n in names:
                    if n not in past:
                        past.append(n)
                entry["past_teams"] = past
                hist[uid] = entry
            save_player_history(hist)
        except Exception as e:
            print(f"[ERROR] disband-all history: {e}")

        # delete team roles
        for tr in team_roles:
            try:
                await tr.delete(reason="All teams disbanded")
            except Exception as e:
                print(f"[ERROR] delete role {tr}: {e}")

        save_teams([])

        ch = guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send("# ALL TEAMS HAS BEEN DISBANDED")

        await interaction.followup.send("All teams disbanded.", ephemeral=True)

    # -------- /roster-lock (admin) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="roster-lock",
        description="Enable roster lock for a team (admin only)."
    )
    @app_commands.describe(team="Team role")
    async def roster_lock(self, interaction: discord.Interaction, team: discord.Role):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only admins can use this.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        locks = load_roster_locks()
        locks[str(team.id)] = True
        save_roster_locks(locks)

        ch = interaction.guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send(f"# ROSTER LOCK HAS BEEN ENABLED FOR {team.mention}")

        await interaction.followup.send("Roster lock enabled.", ephemeral=True)

    # -------- /roster-lock-all (admin) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="roster-lock-all",
        description="Enable roster lock for ALL teams (admin only)."
    )
    async def roster_lock_all(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only admins can use this.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        locks = load_roster_locks()
        locks["ALL"] = True
        save_roster_locks(locks)

        ch = interaction.guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send("# ROSTER LOCK HAS BEEN ENABLED FOR ALL TEAM!")

        await interaction.followup.send("Roster lock enabled for all teams.", ephemeral=True)

    # -------- /unlock-roster (admin) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="unlock-roster",
        description="Disable roster lock for a specific team (admin only)."
    )
    @app_commands.describe(team="Team role")
    async def unlock_roster(self, interaction: discord.Interaction, team: discord.Role):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only admins can use this.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        locks = load_roster_locks()
        locks[str(team.id)] = False
        save_roster_locks(locks)

        ch = interaction.guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send("# ROSTER LOCK HAS BEEN DISABLE")

        await interaction.followup.send("Roster unlocked for that team.", ephemeral=True)

    # -------- /unlock-roster-all (admin) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="unlock-roster-all",
        description="Disable roster lock for ALL teams (admin only)."
    )
    async def unlock_roster_all(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only admins can use this.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        locks = load_roster_locks()
        locks["ALL"] = False
        for key in list(locks.keys()):
            if key != "ALL":
                locks[key] = False
        save_roster_locks(locks)

        ch = interaction.guild.get_channel(TRANSACTIONS_ID)
        if ch:
            await ch.send("# ROSTER LOCK HAS BEEN DISABLE")

        await interaction.followup.send("Roster unlocked for all teams.", ephemeral=True)

    # -------- /kick-player (captains + co-captains) --------
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(
        name="kick-player",
        description="Kick a player from your team (removes team + team player roles)."
    )
    @app_commands.describe(
        member="The player you want to kick from your team"
    )
    async def kick_player(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        me = guild.get_member(interaction.user.id)

        captain_role = guild.get_role(CAPTAIN_ROLE_ID)
        cocap_role = guild.get_role(CO_CAPTAIN_ROLE_ID)
        team_player_role = guild.get_role(TEAM_PLAYER_ROLE_ID)

        # perms: captains and co-captains
        if not role_in(me, captain_role) and not role_in(me, cocap_role):
            await interaction.followup.send(
                "Only captains and co-captains can use this command.",
                ephemeral=True,
            )
            return

        # find captain's team
        team = find_single_team_for_member(guild, me)
        if team is None:
            await interaction.followup.send(
                "You must be on exactly 1 team to use this.",
                ephemeral=True,
            )
            return

        # roster lock check
        if await self._roster_lock_block(interaction, team):
            return

        # target must be on same team
        if team not in member.roles:
            await interaction.followup.send(
                f"{member.mention} is not on your team ({team.mention}).",
                ephemeral=True,
            )
            return

        # only remove team role + team player role
        roles_to_remove = [team]
        if team_player_role and team_player_role in member.roles:
            roles_to_remove.append(team_player_role)

        try:
            await member.remove_roles(
                *roles_to_remove,
                reason=f"Kicked from team {team.name} by {interaction.user}",
            )
        except Exception as e:
            await interaction.followup.send(
                f"Failed to remove roles from {member.mention}: {e}",
                ephemeral=True,
            )
            return

        # transaction log
        tx_ch = guild.get_channel(TRANSACTIONS_ID)
        if tx_ch:
            await tx_ch.send(
                f"{member.mention} has been kicked from **{team.name}** by {interaction.user.mention}"
            )

        await interaction.followup.send(
            f"{member.mention} has been kicked from {team.mention}.",
            ephemeral=True,
        )


# ---------------- /info (public) ----------------
@bot.tree.command(
    guild=GUILD_OBJ,
    name="info",
    description="Shows information about this bot and its commands."
)
async def info(interaction: discord.Interaction):
    embed = discord.Embed(
        title="GTPL Transactions Bot – Command Guide",
        description="Public + team commands.",
        colour=discord.Colour.blurple(),
    )

    embed.add_field(
        name="/info (everyone)",
        value="Shows information about this bot and its commands.",
        inline=False,
    )
    embed.add_field(
        name="/player-info (everyone)",
        value="View a player's league information.",
        inline=False,
    )
    embed.add_field(
        name="/list-teams (everyone)",
        value="View a list of all teams.",
        inline=False,
    )
    embed.add_field(
        name="/roster (everyone)",
        value="View team rosters that are stored in the system.",
        inline=False,
    )
    embed.add_field(
        name="/standing (everyone)",
        value="View leagues standings for all teams.",
        inline=False,
    )
    embed.add_field(
        name="/leave (team members)",
        value="Leave your current team and remove all associated roles.",
        inline=False,
    )
    embed.add_field(
        name="/invite-player (captains and co-captains)",
        value="Invite a player to your team.",
        inline=False,
    )
    embed.add_field(
        name="/add-co-captain (captains)",
        value="Add a co-captain to your team (only 2 per team).",
        inline=False,
    )
    embed.add_field(
        name="/remove-co-captain (captains)",
        value="Remove a co-captain from your team.",
        inline=False,
    )
    embed.add_field(
        name="/change-captain (captains)",
        value="Change the team's captain (e.g. banner1234 to mmm.compsova).",
        inline=False,
    )
    embed.add_field(
        name="/add-executive (captains)",
        value="Add an executive to your team.",
        inline=False,
    )
    embed.add_field(
        name="/remove-executive (captains)",
        value="Remove an executive from your team.",
        inline=False,
    )
    embed.add_field(
        name="/kick-player (captains and co-captains)",
        value="Kick a player from your team.",
        inline=False,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------- /admin-info (admins only) ----------------
@bot.tree.command(
    guild=GUILD_OBJ,
    name="admin-info",
    description="Admin-only command guide."
)
async def admin_info(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only admins can use this.", ephemeral=True)
        return

    embed = discord.Embed(
        title="GTPL Transactions Bot – Admin Command Guide",
        description="Admin-only commands.",
        colour=discord.Colour.red(),
    )

    embed.add_field(
        name="/disband",
        value="Disband a specific team and remove its roles.",
        inline=False,
    )
    embed.add_field(
        name="/disband-all",
        value="Disband all teams in the system and clean up their roles.",
        inline=False,
    )
    embed.add_field(
        name="/create-team",
        value="Create a new team, set its captain, and apply the color code.",
        inline=False,
    )
    embed.add_field(
        name="/submit-time",
        value="Submit a match time for two teams; posts formatted info and creates assignments.",
        inline=False,
    )
    embed.add_field(
        name="/change-color",
        value="Change a team's color (e.g. FFFFFF to FF00FF).",
        inline=False,
    )
    embed.add_field(
        name="/change-team-name",
        value="Change the name of a team (e.g. TEST1 to TEST2).",
        inline=False,
    )
    embed.add_field(
        name="/roster-lock",
        value="Enable roster lock on a specific team (no more roster moves).",
        inline=False,
    )
    embed.add_field(
        name="/roster-lock-all",
        value="Enable roster lock on all teams in the system.",
        inline=False,
    )
    embed.add_field(
        name="/unlock-roster",
        value="Disable roster lock on a specific team.",
        inline=False,
    )
    embed.add_field(
        name="/unlock-roster-all",
        value="Disable roster lock on all teams in the system.",
        inline=False,
    )
    embed.add_field(
        name="/code",
        value="Generate a random code for two teams.",
        inline=False,
    )
    embed.add_field(
        name="/add-scrim",
        value="Create a scrim text channel for two team roles with proper permissions.",
        inline=False,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    guild=GUILD_OBJ,
    name="faq",
    description="Post the GTPL FAQ and auto-role buttons (admins only)."
)
async def faq(interaction: discord.Interaction):
    # admin-only guard
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Only admins can use this.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "Must be used in a server.",
            ephemeral=True,
        )
        return

    faq_channel = guild.get_channel(FAQ_CHANNEL_ID)
    if faq_channel is None:
        await interaction.response.send_message(
            "FAQ channel is not configured correctly (FAQ_CHANNEL_ID).",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    text = (
        "# Gorilla Tag Premire League Asked Questions\n\n"
        "## • How can I make a team/How do I get it official?\n\n"
        "> **Making a team is quite easy,**\n"
        "> - Simply make a discord for your team, and use recruitment-center,\n"
        "> - Getting your team official is another challenge however,\n"
        "> **The first step to getting your team official is getting unborn captain role!,**\n"
        "> - We will use <#1479692824894247012> to update you on our team situation! We pick the best teams we can from out forms, so make sure you are active and competitive!\n"
        "> - Teams normally get selected at the start of a new season or replacing an older team during seeding season,\n"
        "> **If you are interested, use our auto roles to join!**\n\n"
        "## • Moderation Support\n\n"
        "> - If you have any reports of players, please open a ticket so the moderation team can tend to it,\n"
        "> - Tickets are not a place for discussion or questions, if you have something to ask, please head over to questions!,\n\n"
        "## • Application Forms\n\n"
        "> - GTPL has a various list of positions and applications to better help the league!,\n"
        "> - These applications are looked at when needed, you will be messaged if it is accepted\n"
        "> <#1500305586061840495>\n\n"
        "# ー Role Assign\n"
        "> 🎥 **Stream Watcher** ー Get Notified when a Live Match is occurring!\n"
        "> 🚀 **Unborn Captain** ー Allows you to apply your team to participate in the league!\n"
        "> 🎉 **Event Ping** ー Participate in events! (Will receive pings)\n"
    )

    view = FAQRoleView()

    await faq_channel.send(text, view=view)

    await interaction.followup.send(
        f"FAQ message posted in {faq_channel.mention}.",
        ephemeral=True,
    )



@bot.tree.command(
    guild=GUILD_OBJ,
    name="standing",
    description="View leagues standings for all teams."
)
async def standing(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    # only caller sees it
    await interaction.response.defer(ephemeral=True)

    teams_data = load_teams()
    if not teams_data:
        await interaction.followup.send("There are no teams in the system.", ephemeral=True)
        return

    # init stats only for teams whose role still exists
    stats: dict[str, dict[str, int]] = {}
    for entry in teams_data:
        rid = entry.get("role_id")
        name = entry.get("name", "Unknown Team")
        role = guild.get_role(int(rid)) if rid else None
        if role is None:
            continue
        stats[name] = {"W": 0, "L": 0, "TC": 0, "PT": 0}

    if not stats:
        await interaction.followup.send("There are no teams in the system.", ephemeral=True)
        return

    score_ch = guild.get_channel(MATCH_SCORES_CHANNEL_ID)
    if score_ch is None:
        await interaction.followup.send("Match scores channel is not configured correctly.", ephemeral=True)
        return

    # parse match scores
    async for msg in score_ch.history(limit=500):
        content = msg.content
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if not lines:
            continue

        try:
            winner_name = None
            loser_name = None
            timecap_val = "no"

            for ln in lines:
                if ln.startswith(">"):
                    ln_clean = ln.lstrip(">").strip()
                else:
                    ln_clean = ln
                lower = ln_clean.lower()
                if lower.startswith("winner:"):
                    winner_name = ln_clean.split(":", 1)[1].strip()
                elif lower.startswith("loser:"):
                    loser_name = ln_clean.split(":", 1)[1].strip()
                elif lower.startswith("timecap:"):
                    timecap_val = ln_clean.split(":", 1)[1].strip()

            if not winner_name or not loser_name:
                continue
            if winner_name not in stats or loser_name not in stats:
                continue

            stats[winner_name]["W"] += 1
            stats[loser_name]["L"] += 1
            if timecap_val.lower() != "no":
                stats[winner_name]["TC"] += 1

        except Exception:
            continue

    # points: 3 per win, 1 per loss, 3 per timecap
    for name, s in stats.items():
        s["PT"] = 3 * s["W"] + 1 * s["L"] + 3 * s["TC"]

    # sort by points desc then name
    ordered = sorted(
        stats.items(),
        key=lambda kv: (-kv[1]["PT"], kv[0].lower()),
    )

    if not ordered:
        await interaction.followup.send("There are no teams in the system.", ephemeral=True)
        return

    lines_out = ["Monke Monke Monke League SEEDING"]
    rank = 1
    for name, s in ordered:
        lines_out.append(
            f"> {rank}. {name} {s['W']} W - {s['L']} L - {s['PT']} PT"
        )
        rank += 1

    await interaction.followup.send("\n".join(lines_out), ephemeral=True)




# ---------------- MATCH TOOLS ----------------
@bot.tree.command(
    guild=GUILD_OBJ,
    name="code",
    description="Generate a random code for two teams (staff only)."
)
@app_commands.describe(team1="Team 1 role", team2="Team 2 role")
async def code(interaction: discord.Interaction, team1: discord.Role, team2: discord.Role):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Only staff can use this.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    code_value = f"GTPL{random.randint(1000, 9999)}"

    if interaction.channel is None:
        await interaction.followup.send("Cannot determine channel.", ephemeral=True)
        return

    await interaction.channel.send(f"{team1.mention} and {team2.mention} code is: ||{code_value}||")
    await interaction.followup.send(f"Code generated and posted: {code_value}", ephemeral=True)


@bot.tree.command(
    guild=GUILD_OBJ,
    name="submit-time",
    description="Propose a match time (staff only)."
)
@app_commands.describe(
    week="Example: WEEK1",
    time="Example: today at 4:45PM EST",
    team1="Team 1 role",
    team2="Team 2 role",
    finals="Set True if this match is Finals",
    semi_finals="Set True if this match is Semi Finals",
)
async def submit_time(
    interaction: discord.Interaction,
    week: str,
    time: str,
    team1: discord.Role,
    team2: discord.Role,
    finals: bool = False,
    semi_finals: bool = False,
):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Only staff can use this.", ephemeral=True)
        return

    guild = interaction.guild
    channel = interaction.channel
    if guild is None or channel is None:
        await interaction.response.send_message("Must be used in a server text channel.", ephemeral=True)
        return

    header = ""
    if finals:
        header = "# FINALS!!"
    elif semi_finals:
        header = "# SEMI FINALS!"

    title_line = f"{header}\n" if header else ""

    content = (
        f"{team1.mention} {team2.mention}\n"
        f"Team staff must accept this match.\n\n"
        f"{title_line}"
        f"> **{week}\n"
        f"> Time: {time}\n"
        f"> Referee: Unassigned\n"
        f"> Caster: Unassigned **"
    )

    view = MatchAcceptView(week=week, time_str=time, header=header, team1_id=team1.id, team2_id=team2.id)
    msg = await channel.send(content, view=view)
    view.accept_message_id = msg.id
    view.accept_channel_id = channel.id

    await interaction.response.send_message("Match posted. Waiting for both teams to accept.", ephemeral=True)

# ---------- /addscrim command ----------
@bot.tree.command(
    guild=GUILD_OBJ,
    name="addscrim",
    description="Create a scrim channel for two teams (staff only)."
)
@app_commands.describe(
    team1="First team role",
    team2="Second team role"
)
async def addscrim(
    interaction: discord.Interaction,
    team1: discord.Role,
    team2: discord.Role,
):
    member = interaction.user
    perms = getattr(member, "guild_permissions", None)
    if not (perms and (perms.administrator or perms.manage_guild)):
        await interaction.response.send_message(
            "Only administrators or managers can use this command.",
            ephemeral=True
        )
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    ch_name = f"scrim-{team1.name}-vs-{team2.name}".lower().replace(" ", "-")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        team1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        team2: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }

    category = None
    if SCRIM_CATEGORY_ID:
        category = guild.get_channel(SCRIM_CATEGORY_ID)
        if category is None or not isinstance(category, discord.CategoryChannel):
            category = None  # fallback if ID is wrong

    channel = await guild.create_text_channel(
        name=ch_name,
        overwrites=overwrites,
        category=category,
        reason=f"Scrim created by {member}",
    )

    msg = (
        f"{team1.mention} vs {team2.mention}\n\n"
        "# Welcome to GTPL Bracket.\n"
        "> 📅 You guys will have 3 day to schedule \n"
        "> ⚔️ And 4 days to play\n"
        "> Ping a staff member when you're ready to schedule or have any questions!"
    )

    await channel.send(msg)

    if category:
        extra = f"in category {category.name}"
    else:
        extra = " (no scrim category configured; created at top level)"

    await interaction.followup.send(
        f"Created {channel.mention}{extra}.",
        ephemeral=True,
    )
# ---------- end /addscrim ----------


@bot.tree.command(
    guild=GUILD_OBJ,
    name="submit-score",
    description="Submit a match score for two teams (staff only)."
)
@app_commands.describe(
    teams_team1="First team role (Team 1)",
    teams_team2="Second team role (Team 2)",
    timecap="Timecap result (e.g. yes/no)",
    winner="Winning team role",
    loser="Losing team role",
    finals="Set True if this match is Finals",
    semi_finals="Set True if this match is Semi Finals",
    score="Final score (e.g. 5-0)",
)
async def submit_score(
    interaction: discord.Interaction,
    teams_team1: discord.Role,
    teams_team2: discord.Role,
    timecap: str,
    winner: discord.Role,
    loser: discord.Role,
    finals: bool = False,
    semi_finals: bool = False,
    score: str = "",
):
    # perms: staff only (admin or manage_guild)
    member = interaction.user
    perms = getattr(member, "guild_permissions", None)
    if not (perms and (perms.administrator or perms.manage_guild)):
        await interaction.response.send_message(
            "Only staff can use this.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "Must be used in a server.",
            ephemeral=True,
        )
        return

    score_channel = guild.get_channel(MATCH_SCORES_CHANNEL_ID)
    if score_channel is None:
        await interaction.response.send_message(
            "Match scores channel is not configured correctly.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    # Header line: @TEAM1 vs @TEAM2
    header_line = f"# {teams_team1.mention} vs {teams_team2.mention}\n"

    # Finals / Semi Finals line
    stage_line = ""
    if finals:
        stage_line = "# FINALS\n"
    elif semi_finals:
        stage_line = "# SEMI FINALS\n"

    # Winner/loser names (no mention in the > lines)
    winner_name = winner.name
    loser_name = loser.name

    score_text = score if score.strip() else "N/A"

    msg = (
        header_line +
        stage_line +
        f"> Winner: {winner_name}\n"
        f"score: {score_text}\n"
        f"timecap: {timecap}\n"
        f"Loser: {loser_name}"
    )

    await score_channel.send(msg)
    await interaction.followup.send(
        f"Match score submitted in {score_channel.mention}.",
        ephemeral=True,
    )







# ---------------- READY / RUN ----------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Guild commands:", [c.name for c in bot.tree.get_commands(guild=GUILD_OBJ)])


async def main():
    await bot.start(os.getenv("TOKEN"))


if __name__ == "__main__":
    asyncio.run(main())
