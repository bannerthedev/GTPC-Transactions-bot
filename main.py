from PIL import Image, ImageDraw, ImageFont
import random
import os
import json
import logging
import discord
from discord.ext import commands
from typing import Optional, List, Dict, Set

import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------- CONFIG (fill these) ----------------
GUILD_ID = 1273371437817790514  # your guild id as int

MATCH_TIMES_CHANNEL_ID = 1288006765874643006
ASSIGNMENTS_CHANNEL_ID = 1502419407417249954
TRANSACTIONS_CHANNEL_ID = 1279153976205508718
MATCH_SCORES_CHANNEL_ID = 1396869436342014073
# ----------------------------------------------------

# ---- INVITES STATE ----
pending_invites: Dict[int, List[Dict]] = {}


# Global role IDs (right‑click role > Copy ID with Developer Mode on)
GLOBAL_CAPTAIN_ROLE_ID = 1273381699194978386   # captain role ID
GLOBAL_COCAPTAIN_ROLE_ID = 1396623890155044946  # co‑captain role ID
GLOBAL_PLAYER_ROLE_ID = 1273383405274398862    # player role ID

DEFAULT_REF_PING = ""
DEFAULT_CASTER_PING = ""

FREE_AGENT_ROLE_NAME = "Free Agent"
TEAMS_FILE = "teams.json"

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---- state ----
assignments: Dict[str, Dict] = {}
pending_invites: Dict[int, List[Dict]] = {}

# ---------------- helpers ----------------
def is_staff(member: discord.Member) -> bool:
    return bool(getattr(member, "guild_permissions", None) and member.guild_permissions.manage_guild)

def is_captain(member: discord.Member) -> bool:
    for r in member.roles:
        if r.name.lower().startswith("captain |"):
            return True
    return False

def gtag_to_hex(code: str) -> int:
    code = str(code).strip()
    if len(code) != 3 or not code.isdigit():
        raise ValueError("Gorilla Tag code must be 3 digits")
    r = int(code[0]) * 28
    g = int(code[1]) * 28
    b = int(code[2]) * 28
    return (r << 16) + (g << 8) + b

def get_global_team_roles(guild: discord.Guild):
    global_captain = guild.get_role(GLOBAL_CAPTAIN_ROLE_ID)
    global_cocaptain = guild.get_role(GLOBAL_COCAPTAIN_ROLE_ID)
    global_player = guild.get_role(GLOBAL_PLAYER_ROLE_ID)
    return global_captain, global_cocaptain, global_player

# --- standings helpers ---
POINTS_FOR_WIN = 3
POINTS_FOR_LOSS = 0
POINTS_FOR_TIMECAP = 2

def load_teams() -> List[Dict]:
    if not os.path.exists(TEAMS_FILE):
        return []
    try:
        with open(TEAMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logging.exception("Failed to load teams file")
        return []

def save_teams(teams: List[Dict]) -> None:
    try:
        with open(TEAMS_FILE, "w", encoding="utf-8") as f:
            json.dump(teams, f, indent=2)
    except Exception:
        logging.exception("Failed to save teams file")

def find_team_entry(teams: List[Dict], team_name: str) -> Optional[Dict]:
    team_name_lower = team_name.strip().lower()
    for t in teams:
        if t.get("name", "").strip().lower() == team_name_lower:
            return t
    return None

def ensure_team_stats_entry(teams: List[Dict], team_name: str) -> Dict:
    entry = find_team_entry(teams, team_name)
    if not entry:
        entry = {"name": team_name, "wins": 0, "losses": 0, "timecaps": 0, "points": 0}
        teams.append(entry)
    entry.setdefault("wins", 0)
    entry.setdefault("losses", 0)
    entry.setdefault("timecaps", 0)
    entry.setdefault("points", 0)
    return entry

def recompute_points(entry: Dict) -> None:
    entry["points"] = (
        int(entry.get("wins", 0)) * POINTS_FOR_WIN
        + int(entry.get("losses", 0)) * POINTS_FOR_LOSS
        + int(entry.get("timecaps", 0)) * POINTS_FOR_TIMECAP
    )

# ---------------- more helpers ----------------
def get_member_team_name(member: discord.Member) -> Optional[str]:
    for r in member.roles:
        lower = r.name.lower()
        if lower.startswith("captain |") or lower.startswith("co-captain |") or lower.startswith("player |"):
            return r.name.split("|", 1)[1].strip()
    return None

def get_leadership_team_name(member: discord.Member) -> Optional[str]:
    """
    Return the team name for a captain/co-captain/player.

    Works with:
    - Per-team roles: "Captain | Team", "Co-Captain | Team", "Player | Team"
    - Plain team roles from teams.json + global captain/co-captain/player roles
    """
    guild = member.guild
    if guild is None:
        return None

    # 1) Old behaviour: per-team Captain | / Co-Captain | / Player |
    for r in member.roles:
        lower = r.name.lower()
        if lower.startswith("captain |") or lower.startswith("co-captain |") or lower.startswith("player |"):
            return r.name.split("|", 1)[1].strip()

    # 2) Use teams.json + team roles + global roles
    global_captain_role, global_cocap_role, global_player_role = get_global_team_roles(guild)
    global_ids = {
        r.id for r in (global_captain_role, global_cocap_role, global_player_role) if r
    }

    teams = load_teams()
    for t in teams:
        name = t.get("name")
        if not name:
            continue
        team_role = discord.utils.get(guild.roles, name=name)
        if team_role and team_role in member.roles:
            # Require that they also have some global team role to be considered "on" this team
            if any((r.id in global_ids) for r in member.roles):
                return name

    return None

def get_team_roster_members(guild: discord.Guild, team_name: str) -> Dict[str, List[discord.Member]]:
    team_role, captain_role, cocap_role, player_role = get_team_roles(guild, team_name)
    global_captain_role, global_cocap_role, global_player_role = get_global_team_roles(guild)

    captains: List[discord.Member] = []
    cocaps: List[discord.Member] = []
    players: List[discord.Member] = []

    if not team_role:
        return {"captains": captains, "cocaps": cocaps, "players": players}

    for m in guild.members:
        if team_role not in m.roles:
            continue  # must be on this team by team role

        # captain if has per-team Captain | or global captain
        if (captain_role and captain_role in m.roles) or (global_captain_role and global_captain_role in m.roles):
            captains.append(m)
            continue

        # co-captain if has per-team Co-Captain | or global cocap
        if (cocap_role and cocap_role in m.roles) or (global_cocap_role and global_cocap_role in m.roles):
            cocaps.append(m)
            continue

        # player if has per-team Player | or global player
        if (player_role and player_role in m.roles) or (global_player_role and global_player_role in m.roles):
            players.append(m)

    return {"captains": captains, "cocaps": cocaps, "players": players}



def get_team_roles(guild: discord.Guild, team_name: str):
    team_role = discord.utils.get(guild.roles, name=team_name)
    captain_role = discord.utils.get(guild.roles, name=f"Captain | {team_name}")
    cocap_role = discord.utils.get(guild.roles, name=f"Co-Captain | {team_name}")
    player_role = discord.utils.get(guild.roles, name=f"Player | {team_name}")
    return team_role, captain_role, cocap_role, player_role

def get_team_roster_counts(guild: discord.Guild, team_name: str) -> Dict[str, int]:
    captain_role = discord.utils.get(guild.roles, name=f"Captain | {team_name}")
    cocap_role = discord.utils.get(guild.roles, name=f"Co-Captain | {team_name}")
    player_role = discord.utils.get(guild.roles, name=f"Player | {team_name}")
    counts = {
        "captain": len(captain_role.members) if captain_role else 0,
        "co_captain": len(cocap_role.members) if cocap_role else 0,
        "player": len(player_role.members) if player_role else 0,
    }
    return counts

# transactions logging helper
async def log_transaction(guild: discord.Guild, message: str):
    try:
        tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
        if tx_ch and tx_ch.guild and tx_ch.guild.id == guild.id:
            await tx_ch.send(message)
    except Exception:
        logging.exception("Failed to send transaction log")

async def log_invite_accepted(guild: discord.Guild, user: discord.Member, team_name: str):
    await log_transaction(guild, f"{user.mention} Has Joined **{team_name}**")

# ---------------- UI classes ----------------
class TargetModal(discord.ui.Modal, title="Transaction"):
    target = discord.ui.TextInput(label="Target (mention or name)", required=True, max_length=200)
    reason = discord.ui.TextInput(label="Reason (optional)", required=False, max_length=500)

    def __init__(self, action: str, actor: discord.Member):
        super().__init__()
        self.action = action
        self.actor = actor

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        tgt = self.target.value.strip()
        reason = self.reason.value.strip()
        target_member = None
        if guild and tgt.startswith("<@"):
            try:
                uid = int(tgt.strip("<@!>"))
                target_member = guild.get_member(uid)
            except Exception:
                target_member = None
        if guild and target_member is None:
            matches = [m for m in guild.members if m.display_name == tgt or m.name == tgt]
            target_member = matches[0] if matches else None
        display = target_member.mention if target_member else tgt
        entry = f"{display} — {self.action} by {self.actor.mention}"
        if reason:
            entry += f" — {reason}"
        tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
        if tx_ch:
            await tx_ch.send(entry)
        await interaction.response.send_message("Transaction recorded.", ephemeral=True)

# ---- INVITES ----
class InviteUserSelect(discord.ui.UserSelect):
    def __init__(self, inviter: discord.Member):
        self.inviter = inviter
        super().__init__(
            placeholder="Who do you invite to your team?",
            min_values=1,
            max_values=1
        )

    def _get_team_name_from_inviter(self) -> Optional[str]:
        guild = self.inviter.guild
        if guild is None:
            return None

        # 1) Direct per‑team captain/co‑captain roles: "Captain | Team", "Co-Captain | Team"
        for role in self.inviter.roles:
            lower = role.name.lower()
            if lower.startswith("captain |") or lower.startswith("co-captain |"):
                return role.name.split("|", 1)[1].strip()

        # 2) Look in teams.json and see if inviter has that team role by exact name
        teams = load_teams()
        for t in teams:
            name = t.get("name")
            if not name:
                continue
            team_role = discord.utils.get(guild.roles, name=name)
            if team_role and team_role in self.inviter.roles:
                return name

        # 3) Last‑resort guess: any non‑global, non‑@everyone role they have
        global_captain_role, global_cocap_role, global_player_role = get_global_team_roles(guild)
        global_ids = {
            r.id for r in (global_captain_role, global_cocap_role, global_player_role) if r
        }

        candidate_roles = []
        for r in self.inviter.roles:
            if r.is_default():   # @everyone
                continue
            if r.id in global_ids:
                continue
            # skip very generic roles if you have them, you can add more names here if needed
            if r.name.lower() in ("admin", "staff", "moderator", "owner"):
                continue
            candidate_roles.append(r)

        # if exactly one team‑ish role left, assume that's the team name
        if len(candidate_roles) == 1:
            return candidate_roles[0].name

        return None


        teams = load_teams()
        for t in teams:
            name = t.get("name")
            if not name:
                continue
            team_role = discord.utils.get(guild.roles, name=name)
            if team_role and team_role in self.inviter.roles:
                return name

        return None

    async def callback(self, interaction: discord.Interaction):
        target: discord.Member = self.values[0]
        team_name = self._get_team_name_from_inviter()
        if not team_name:
            await interaction.response.send_message(
                "Could not determine your team name.",
                ephemeral=True
            )
            return

        teams = load_teams()
        entry = find_team_entry(teams, team_name)
        if entry and entry.get("roster_locked"):
            await interaction.response.send_message(
                f"Team **{team_name}** is under roster lock.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        if guild:
            counts = get_team_roster_counts(guild, team_name)
            if counts["player"] >= 12:
                await interaction.response.send_message(
                    f"Team **{team_name}** is at maximum capacity (12 players).",
                    ephemeral=True
                )
                return

        # store invite
        user_invites = pending_invites.setdefault(target.id, [])
        user_invites.append({
            "inviter_id": self.inviter.id,
            "team_name": team_name
        })
        invite_index = len(user_invites) - 1

        # try DM the invited user
        try:
            dm_content = (
                f"**You've been invited to {team_name}**\n"
                f"{self.inviter.mention} invited you to join {team_name}."
            )
            view = InviteDecisionView(target.id, invite_index)
            await target.send(content=dm_content, view=view)

            await interaction.response.edit_message(
                content="Invite sent via DM. You can select another player to invite.",
                view=self.view
            )
        except Exception:
            # if DM fails, fall back to /check_invites
            await interaction.response.edit_message(
                content=(
                    "Invite created. Player must run /check_invites.\n"
                    "You can select another player to invite."
                ),
                view=self.view
            )

# ---- INVITES UI ----

class InviteUserSelect(discord.ui.UserSelect):
    def __init__(self, inviter: discord.Member):
        self.inviter = inviter
        super().__init__(
            placeholder="Who do you invite to your team?",
            min_values=1,
            max_values=1
        )

    def _get_team_name_from_inviter(self) -> Optional[str]:
        guild = self.inviter.guild
        if guild is None:
            return None

        # 1) Per-team "Captain | Team" / "Co-Captain | Team"
        for role in self.inviter.roles:
            lower = role.name.lower()
            if lower.startswith("captain |") or lower.startswith("co-captain |"):
                return role.name.split("|", 1)[1].strip()

        # 2) Using teams.json + team roles + global roles
        global_captain_role, global_cocap_role, global_player_role = get_global_team_roles(guild)
        global_ids = {
            r.id for r in (global_captain_role, global_cocap_role, global_player_role) if r
        }

        teams = load_teams()
        for t in teams:
            name = t.get("name")
            if not name:
                continue
            team_role = discord.utils.get(guild.roles, name=name)
            if team_role and team_role in self.inviter.roles:
                # ensure they also have a global team role
                if any((r.id in global_ids) for r in self.inviter.roles):
                    return name

        # 3) Last-resort guess: any non-global, non-@everyone role
        if teams:
            from typing import cast
        global_role_ids = global_ids
        candidate_roles = []
        for r in self.inviter.roles:
            if r.is_default():  # @everyone
                continue
            if r.id in global_role_ids:
                continue
            # skip some generic names
            if r.name.lower() in ("admin", "staff", "moderator", "owner"):
                continue
            candidate_roles.append(r)
        if len(candidate_roles) == 1:
            return candidate_roles[0].name

        return None

    async def callback(self, interaction: discord.Interaction):
        target: discord.Member = self.values[0]
        team_name = self._get_team_name_from_inviter()
        if not team_name:
            await interaction.response.send_message(
                "Could not determine your team.",
                ephemeral=True
            )
            return

        teams = load_teams()
        entry = find_team_entry(teams, team_name)
        if entry and entry.get("roster_locked"):
            await interaction.response.send_message(
                f"Team **{team_name}** is under roster lock.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        if guild:
            counts = get_team_roster_counts(guild, team_name)
            if counts["player"] >= 12:
                await interaction.response.send_message(
                    f"Team **{team_name}** is at maximum capacity (12 players).",
                    ephemeral=True
                )
                return

        # store invite
        user_invites = pending_invites.setdefault(target.id, [])
        user_invites.append({
            "inviter_id": self.inviter.id,
            "team_name": team_name
        })
        invite_index = len(user_invites) - 1

        # DM the invited player
        try:
            dm_content = (
                f"**You've been invited to {team_name}**\n"
                f"{self.inviter.mention} invited you to join {team_name}."
            )
            view = InviteDecisionView(target.id, invite_index)
            await target.send(content=dm_content, view=view)

            await interaction.response.edit_message(
                content="Invite sent via DM. You can select another player to invite.",
                view=self.view
            )
        except Exception:
            # if DM fails, fall back to /check_invites
            await interaction.response.edit_message(
                content=(
                    "Invite created. Player must run /check_invites.\n"
                    "You can select another player to invite."
                ),
                view=self.view
            )


class InviteSelectView(discord.ui.View):
    def __init__(self, inviter: discord.Member):
        super().__init__(timeout=None)
        self.add_item(InviteUserSelect(inviter))


class InviteDecisionView(discord.ui.View):
    def __init__(self, user_id: int, invite_index: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.invite_index = invite_index

    def _get_invite(self):
        user_invites = pending_invites.get(self.user_id, [])
        if 0 <= self.invite_index < len(user_invites):
            return user_invites[self.invite_index]
        return None

    def _get_guild_and_member(self, interaction: discord.Interaction) -> Optional[tuple[discord.Guild, discord.Member]]:
        guild = interaction.guild or bot.get_guild(GUILD_ID)
        if guild is None:
            return None
        member = guild.get_member(self.user_id)
        if member is None:
            return None
        return guild, member

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This invite is not for you.", ephemeral=True)
            return

        invite = self._get_invite()
        if not invite:
            await interaction.response.send_message("Invite no longer available.", ephemeral=True)
            return

        gm = self._get_guild_and_member(interaction)
        if gm is None:
            await interaction.response.send_message("Cannot resolve server/member for this invite.", ephemeral=True)
            return
        guild, member = gm

        team_name = invite.get("team_name", "Team")
        team_role, _, _, player_role = get_team_roles(guild, team_name)
        _, _, global_player_role = get_global_team_roles(guild)

        roles_to_add = [
            r for r in (team_role, player_role, global_player_role)
            if r and r not in member.roles
        ]
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Accepted team invite")

        await log_invite_accepted(guild, member, team_name)

        user_invites = pending_invites.get(self.user_id, [])
        if 0 <= self.invite_index < len(user_invites):
            user_invites.pop(self.invite_index)
        pending_invites[self.user_id] = user_invites

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(content="You accepted this invite.", view=self)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This invite is not for you.", ephemeral=True)
            return

        invite = self._get_invite()
        if not invite:
            await interaction.response.send_message("Invite no longer available.", ephemeral=True)
            return

        user_invites = pending_invites.get(self.user_id, [])
        if 0 <= self.invite_index < len(user_invites):
            user_invites.pop(self.invite_index)
        pending_invites[self.user_id] = user_invites

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(content="You declined this invite.", view=self)


class InviteSelectView(discord.ui.View):
    def __init__(self, inviter: discord.Member):
        super().__init__(timeout=None)
        self.add_item(InviteUserSelect(inviter))

class InviteDecisionView(discord.ui.View):
    def __init__(self, user_id: int, invite_index: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.invite_index = invite_index

    def _get_invite(self):
        user_invites = pending_invites.get(self.user_id, [])
        if 0 <= self.invite_index < len(user_invites):
            return user_invites[self.invite_index]
        return None

    def _get_guild_and_member(self, interaction: discord.Interaction) -> Optional[tuple[discord.Guild, discord.Member]]:
        # In a server interaction.guild is set; in a DM it's None, so fallback to configured GUILD_ID
        guild = interaction.guild or bot.get_guild(GUILD_ID)
        if guild is None:
            return None
        member = guild.get_member(self.user_id)
        if member is None:
            return None
        return guild, member

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This invite is not for you.", ephemeral=True)
            return

        invite = self._get_invite()
        if not invite:
            await interaction.response.send_message("Invite no longer available.", ephemeral=True)
            return

        gm = self._get_guild_and_member(interaction)
        if gm is None:
            await interaction.response.send_message("Cannot resolve server/member for this invite.", ephemeral=True)
            return
        guild, member = gm

        team_name = invite.get("team_name", "Team")
        team_role, _, _, player_role = get_team_roles(guild, team_name)
        _, _, global_player_role = get_global_team_roles(guild)

        # Give team role, Player | Team role, and global player role
        roles_to_add = [
            r for r in (team_role, player_role, global_player_role)
            if r and r not in member.roles
        ]
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Accepted team invite")

        await log_invite_accepted(guild, member, team_name)

        # remove this invite from pending
        user_invites = pending_invites.get(self.user_id, [])
        if 0 <= self.invite_index < len(user_invites):
            user_invites.pop(self.invite_index)
        pending_invites[self.user_id] = user_invites

        # disable buttons
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(content="You accepted this invite.", view=self)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This invite is not for you.", ephemeral=True)
            return

        invite = self._get_invite()
        if not invite:
            await interaction.response.send_message("Invite no longer available.", ephemeral=True)
            return

        user_invites = pending_invites.get(self.user_id, [])
        if 0 <= self.invite_index < len(user_invites):
            user_invites.pop(self.invite_index)
        pending_invites[self.user_id] = user_invites

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(content="You declined this invite.", view=self)

# ---------- Roster UI ----------
class RosterSelect(discord.ui.Select):
    def __init__(self, teams: List[Dict]):
        teams = teams[:25]  # Discord max options
        options: List[discord.SelectOption] = []
        for t in teams:
            name = t.get("name")
            if not name:
                continue
            options.append(
                discord.SelectOption(
                    label=name,
                    value=name,
                    description="View this team roster"
                )
            )

        super().__init__(
            placeholder="Select a team...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        team_name = self.values[0]
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "Must be used in a server.",
                ephemeral=True
            )
            return

        team_role, captain_role, cocap_role, player_role = get_team_roles(guild, team_name)

        captain = captain_role.members[0] if captain_role and captain_role.members else None
        cocaps = list(cocap_role.members) if cocap_role else []
        players = sorted(player_role.members, key=lambda m: m.display_name.lower()) if player_role else []

        lines = [
            f"**Team: {team_name}**",
            f"Captain: {captain.mention if captain else 'None'}",
            f"Co‑Captain(s): {', '.join(m.mention for m in cocaps) if cocaps else 'None'}",
            "Players:",
        ]
        if players:
            for idx, m in enumerate(players, start=1):
                lines.append(f"{idx}. {m.mention}")
        else:
            lines.append("No players found.")

        await interaction.response.send_message(
            "\n".join(lines),
            ephemeral=True
        )

class RosterView(discord.ui.View):
    def __init__(self, teams: List[Dict]):
        super().__init__(timeout=None)
        self.add_item(RosterSelect(teams))

@bot.tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="roster",
    description="View team rosters from the system"
)
async def roster(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message(
            "Must be used in a server.",
            ephemeral=True
        )
        return

    teams = load_teams()

    # Fallback: if teams.json empty, infer from roles
    if not teams:
        found_names = set()
        for role in guild.roles:
            lower = role.name.lower()
            if lower.startswith("captain |") or lower.startswith("co-captain |") or lower.startswith("player |"):
                team_name = role.name.split("|", 1)[1].strip()
                found_names.add(team_name)
        teams = [{"name": n} for n in sorted(found_names, key=str.lower)]
        if not teams:
            await interaction.response.send_message(
                "No teams found from roles or system file.",
                ephemeral=True
            )
            return

    view = RosterView(teams)
    await interaction.response.send_message(
        "Select a team to view its roster:",
        view=view,
        ephemeral=True
    )

# ---- Assignment view (claiming) ----
class AssignmentView(discord.ui.View):
    def __init__(self, match_key: str, match_message: Optional[discord.Message]):
        super().__init__(timeout=None)
        self.match_key = match_key
        self.match_message = match_message

    def _fmt(self, v):
        return v

    async def update_messages(self, interaction: discord.Interaction):
        data = assignments.get(self.match_key)
        if not data:
            return
        ref_text = self._fmt(data.get("ref", "TBD"))
        caster_text = self._fmt(data.get("caster", "TBD"))
        text = (
            f"> **{self.match_key}\n"
            f"> Time: {data.get('time', 'TBD')}\n"
            f"> Referee: {ref_text}\n"
            f"> Caster: {caster_text} **"
        )
        try:
            await interaction.message.edit(content=text, view=self)
        except Exception:
            pass
        if self.match_message:
            try:
                await self.match_message.edit(content=text)
            except Exception:
                pass

    @discord.ui.button(label="Claim Caster", style=discord.ButtonStyle.primary)
    async def caster(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = assignments.get(self.match_key)
        if not data:
            await interaction.response.send_message("Assignment not found.", ephemeral=True)
            return
        if data.get("caster") != "TBD":
            await interaction.response.send_message("Caster already taken.", ephemeral=True)
            return
        data["caster"] = interaction.user.mention
        await self.update_messages(interaction)
        await interaction.response.send_message("You are now the caster.", ephemeral=True)

    @discord.ui.button(label="Claim Referee", style=discord.ButtonStyle.secondary)
    async def ref(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = assignments.get(self.match_key)
        if not data:
            await interaction.response.send_message("Assignment not found.", ephemeral=True)
            return
        if data.get("ref") != "TBD":
            await interaction.response.send_message("Referee already taken.", ephemeral=True)
            return
        data["ref"] = interaction.user.mention
        await self.update_messages(interaction)
        await interaction.response.send_message("You are now the referee.", ephemeral=True)

    @discord.ui.button(label="Unclaim", style=discord.ButtonStyle.danger)
    async def unclaim(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = assignments.get(self.match_key)
        if not data:
            await interaction.response.send_message("Assignment not found.", ephemeral=True)
            return
        u = interaction.user.mention
        changed = False
        if data.get("caster") == u:
            data["caster"] = "TBD"
            changed = True
        elif data.get("ref") == u:
            data["ref"] = "TBD"
            changed = True
        if not changed:
            await interaction.response.send_message("You have nothing to unclaim.", ephemeral=True)
            return
        await self.update_messages(interaction)
        await interaction.response.send_message("You unclaimed your role.", ephemeral=True)

# ---- AcceptView ----
class AcceptView(discord.ui.View):
    def __init__(self, match_key: str, time_str: str, week: str, team1: str, team2: str, channel_id: int):
        super().__init__(timeout=None)
        self.match_key = match_key
        self.time_str = time_str
        self.week = week
        self.team1 = team1
        self.team2 = team2
        self.channel_id = channel_id
        self.accepted_for: Set[str] = set()

    def _fmt_accepts(self):
        a1 = "✅" if self.team1 in self.accepted_for else "❌"
        a2 = "✅" if self.team2 in self.accepted_for else "❌"
        return f"{self.team1}: {a1}\n{self.team2}: {a2}"

    async def _update_message(self, message: discord.Message):
        content = f"WEEK {self.week}\n\nAccept status:\n{self._fmt_accepts()}"
        try:
            await message.edit(content=content, view=self)
        except Exception:
            pass

    def _is_captain(self, member: discord.Member) -> bool:
        for r in getattr(member, "roles", []):
            name = r.name.lower()
            if name.startswith("captain |") or name.startswith("co-captain |"):
                return True
        return False

    async def _handle_accept(self, interaction: discord.Interaction, target_team: str):
        # require using same channel as the one where the accept message was posted
        if interaction.channel is None or interaction.channel.id != self.channel_id:
            await interaction.response.send_message("You must accept in the channel where this match was posted.", ephemeral=True)
            return

        user = interaction.user
        if not self._is_captain(user):
            await interaction.response.send_message("Only a captain or co-captain may accept.", ephemeral=True)
            return
        if target_team in self.accepted_for:
            await interaction.response.send_message(f"{target_team} has already accepted.", ephemeral=True)
            return

        self.accepted_for.add(target_team)
        try:
            await self._update_message(interaction.message)
        except Exception:
            pass
        await interaction.response.send_message(f"You accepted for {target_team}.", ephemeral=True)

        # If both teams accepted, post match + assignment with claim buttons
        if self.team1 in self.accepted_for and self.team2 in self.accepted_for:
            match_channel = bot.get_channel(MATCH_TIMES_CHANNEL_ID)
            assign_channel = bot.get_channel(ASSIGNMENTS_CHANNEL_ID)

            ref_text = DEFAULT_REF_PING if DEFAULT_REF_PING else "TBD"
            caster_text = DEFAULT_CASTER_PING if DEFAULT_CASTER_PING else "TBD"
            base_text = (
                f"> **{self.match_key}\n"
                f"> Time: {self.time_str}\n"
                f"> Referee: {ref_text}\n"
                f"> Caster: {caster_text} **"
            )

            assignments[self.match_key] = {
                "time": self.time_str,
                "ref": ref_text,
                "caster": caster_text,
            }

            match_message = None
            try:
                if match_channel:
                    match_message = await match_channel.send(base_text)
                if assign_channel:
                    view = AssignmentView(self.match_key, match_message)
                    await assign_channel.send(base_text, view=view)
            except Exception:
                logging.exception("Failed to post match/assignment")

            try:
                await interaction.followup.send("Both teams accepted. Match posted.", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Accept for Team 1", style=discord.ButtonStyle.success)
    async def accept_team1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_accept(interaction, self.team1)

    @discord.ui.button(label="Accept for Team 2", style=discord.ButtonStyle.primary)
    async def accept_team2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_accept(interaction, self.team2)
# ---- end AcceptView ----


# ---------- NEW management views for captain panel ----------
class PromoteCoCaptainView(discord.ui.View):
    def __init__(self, actor: discord.Member, team_name: str, candidates: List[discord.Member]):
        super().__init__(timeout=60)
        self.actor = actor
        self.team_name = team_name

        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id))
            for m in candidates
        ]
        self.select = discord.ui.Select(
            placeholder="Select a player to promote to co-captain",
            min_values=1,
            max_values=1,
            options=options
        )
        self.select.callback = self.promote_callback  # type: ignore
        self.add_item(self.select)

    async def promote_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        _, _, cocap_role, _ = get_team_roles(guild, self.team_name)
        if not cocap_role:
            await interaction.response.send_message("Co-Captain role not found for this team.", ephemeral=True)
            return

        member_id = int(interaction.data["values"][0])  # type: ignore
        target = guild.get_member(member_id)
        if not target:
            await interaction.response.send_message("Member not found.", ephemeral=True)
            return

        await target.add_roles(cocap_role, reason="Promoted to co-captain")

        # DM to promoted user
        try:
            dm_msg = f"{target.mention} you have been promoted to co-captain by {self.actor.mention}"
            await target.send(dm_msg)
        except Exception:
            pass

        # Transaction
        tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
        if tx_ch:
            await tx_ch.send(
                f"{target.mention} has been promoted to co-captain of **{self.team_name}**"
            )

        await interaction.response.edit_message(content="Promotion recorded.", view=None)


class DemoteCoCaptainView(discord.ui.View):
    def __init__(self, actor: discord.Member, team_name: str, cocaps: List[discord.Member]):
        super().__init__(timeout=60)
        self.actor = actor
        self.team_name = team_name

        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id))
            for m in cocaps
        ]
        self.select = discord.ui.Select(
            placeholder="Select a co-captain to demote",
            min_values=1,
            max_values=1,
            options=options
        )
        self.select.callback = self.demote_callback  # type: ignore
        self.add_item(self.select)

    async def demote_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        _, _, cocap_role, _ = get_team_roles(guild, self.team_name)
        if not cocap_role:
            await interaction.response.send_message("Co-Captain role not found for this team.", ephemeral=True)
            return

        member_id = int(interaction.data["values"][0])  # type: ignore
        target = guild.get_member(member_id)
        if not target:
            await interaction.response.send_message("Member not found.", ephemeral=True)
            return

        await target.remove_roles(cocap_role, reason="Demoted from co-captain")

        # DM to demoted user
        try:
            dm_msg = f"{target.mention} you have been demoted from co-captain of **{self.team_name}**"
            await target.send(dm_msg)
        except Exception:
            pass

        # Transaction
        tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
        if tx_ch:
            await tx_ch.send(
                f"{target.mention} has been demoted from co-captain of **{self.team_name}**"
            )

        await interaction.response.edit_message(content="Demotion recorded.", view=None)


class KickMemberView(discord.ui.View):
    def __init__(self, actor: discord.Member, team_name: str, members: List[discord.Member]):
        super().__init__(timeout=60)
        self.actor = actor
        self.team_name = team_name

        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id))
            for m in members
        ]
        self.select = discord.ui.Select(
            placeholder="Select a member to kick from the team",
            min_values=1,
            max_values=1,
            options=options
        )
        self.select.callback = self.kick_callback  # type: ignore
        self.add_item(self.select)

    async def kick_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        team_role, captain_role, cocap_role, player_role = get_team_roles(guild, self.team_name)

        member_id = int(interaction.data["values"][0])  # type: ignore
        target = guild.get_member(member_id)
        if not target:
            await interaction.response.send_message("Member not found.", ephemeral=True)
            return

        roles_to_remove = []
        for r in (team_role, player_role, cocap_role, captain_role):
            if r and r in target.roles:
                roles_to_remove.append(r)

        if roles_to_remove:
            await target.remove_roles(*roles_to_remove, reason="Kicked from team")

        # DM to kicked user
        try:
            dm_msg = f"{target.mention} you've been kick from **{self.team_name}** by {self.actor.mention}"
            await target.send(dm_msg)
        except Exception:
            pass

        # Transaction
        tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
        if tx_ch:
            await tx_ch.send(
                f"{target.mention} has been kicked from **{self.team_name}** by {self.actor.mention}"
            )

        await interaction.response.edit_message(content="Kick recorded.", view=None)


class TransferCaptainView(discord.ui.View):
    def __init__(self, actor: discord.Member, team_name: str, candidates: List[discord.Member]):
        super().__init__(timeout=60)
        self.actor = actor
        self.team_name = team_name

        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id))
            for m in candidates
        ]
        select = discord.ui.Select(
            placeholder="Select the new captain",
            min_values=1,
            max_values=1,
            options=options
        )
        select.callback = self.transfer_callback  # type: ignore
        self.add_item(select)

    async def transfer_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor.id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        team_role, captain_role, cocap_role, player_role = get_team_roles(guild, self.team_name)
        if not captain_role:
            await interaction.response.send_message("Captain role not found for this team.", ephemeral=True)
            return

        member_id = int(interaction.data["values"][0])  # type: ignore
        new_cap = guild.get_member(member_id)
        if not new_cap:
            await interaction.response.send_message("Member not found.", ephemeral=True)
            return

        old_cap = self.actor

        roles_to_add_old = []
        roles_to_remove_old = []
        if captain_role in old_cap.roles:
            roles_to_remove_old.append(captain_role)
        if cocap_role and cocap_role not in old_cap.roles:
            roles_to_add_old.append(cocap_role)
        if roles_to_remove_old:
            await old_cap.remove_roles(*roles_to_remove_old, reason="Transferred captaincy")
        if roles_to_add_old:
            await old_cap.add_roles(*roles_to_add_old, reason="Transferred captaincy")

        roles_to_add_new = [captain_role]
        if team_role and team_role not in new_cap.roles:
            roles_to_add_new.append(team_role)
        if cocap_role and cocap_role in new_cap.roles:
            await new_cap.remove_roles(cocap_role, reason="Promoted to captain")
        await new_cap.add_roles(*roles_to_add_new, reason="Promoted to captain")

        tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
        if tx_ch:
            await tx_ch.send(
                f"{new_cap.mention} is now the captain of {self.team_name} by {old_cap.mention}"
            )

        await interaction.response.edit_message(content="Captaincy transfer recorded.", view=None)


class ChangeColorModal(discord.ui.Modal, title="Change Team Color Code"):
    color_code = discord.ui.TextInput(
        label="New Gorilla Tag color code (3 digits)",
        max_length=3,
        required=True
    )

    def __init__(self, team_name: str, actor: discord.Member):
        super().__init__()
        self.team_name = team_name
        self.actor = actor

    async def on_submit(self, interaction: discord.Interaction):
        code = self.color_code.value.strip()
        if len(code) != 3 or not code.isdigit():
            await interaction.response.send_message("Color code must be 3 digits.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        teams = load_teams()
        entry = find_team_entry(teams, self.team_name)
        if not entry:
            await interaction.response.send_message("Team not found in system.", ephemeral=True)
            return

        old_code = entry.get("color_code", "N/A")
        try:
            hex_color = gtag_to_hex(code)
        except Exception:
            await interaction.response.send_message("Invalid color code.", ephemeral=True)
            return

        entry["color"] = hex_color
        entry["color_code"] = code
        save_teams(teams)

        color_obj = discord.Color(hex_color)
        team_role, captain_role, cocap_role, player_role = get_team_roles(guild, self.team_name)
        for role in (team_role, captain_role, cocap_role, player_role):
            if role:
                try:
                    await role.edit(colour=color_obj, reason="Team color changed")
                except Exception:
                    pass

        tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
        if tx_ch:
            await tx_ch.send(
                f"{self.actor.mention} has changed teams color code from {old_code} to {code} for {self.team_name}"
            )

        await interaction.response.send_message("Team color updated.", ephemeral=True)


class TransactionActionView(discord.ui.View):
    def __init__(self, actor: discord.Member):
        super().__init__(timeout=120)
        self.actor = actor

    @discord.ui.select(
        placeholder="Choose action",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="invite", description="Invite a player"),
            discord.SelectOption(label="kick", description="Kick a player from your team"),
            discord.SelectOption(label="+co-captain", description="Promote a player to co-captain"),
            discord.SelectOption(label="-co-captain", description="Demote a co-captain"),
            discord.SelectOption(label="transfer_captain", description="Transfer captain role"),
            discord.SelectOption(label="change_color", description="Change team color code"),
            discord.SelectOption(label="disband", description="Disband your team"),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        action = select.values[0]
        member = interaction.user
        guild = interaction.guild

        if not guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        team_name = get_leadership_team_name(member)
        if not team_name:
            await interaction.response.send_message("Could not determine your team.", ephemeral=True)
            return

        role_names = [r.name.lower() for r in getattr(member, "roles", [])]
        perms = getattr(member, "guild_permissions", None)
        is_admin = perms and (perms.administrator or perms.manage_guild)
        is_captain_role = any(rn.startswith("captain |") for rn in role_names)
        is_cocaptain_role = any(rn.startswith("co-captain |") for rn in role_names)
        is_captain = is_captain_role
        is_cocaptain = is_cocaptain_role or is_captain_role

        team_role, captain_role, cocap_role, player_role = get_team_roles(guild, team_name)

        if action == "kick" and not (is_cocaptain or is_admin):
            await interaction.response.send_message("Only co-captains and above can use kick.", ephemeral=True)
            return
        if action in ("+co-captain", "-co-captain", "transfer_captain", "change_color", "disband") and not (is_captain or is_admin):
            await interaction.response.send_message("Only captains and above can use this action.", ephemeral=True)
            return

        if action == "invite":
            view = InviteSelectView(member)
            await interaction.response.send_message("Who do you invite to your team?", view=view, ephemeral=True)
            return

        if action == "+co-captain":
            if not team_role:
                await interaction.response.send_message("No team role found for this team.", ephemeral=True)
                return

            current_cocaps = cocap_role.members if cocap_role else []
            max_cocaps = 2
            if len(current_cocaps) >= max_cocaps:
                await interaction.response.send_message(
                    f"This team already has the maximum of {max_cocaps} co-captains.",
                    ephemeral=True
                )
                return

            base_members = set(team_role.members)
            captain_members = set(captain_role.members) if captain_role else set()
            cocap_members = set(current_cocaps)
            excluded = captain_members | cocap_members

            candidates = [m for m in base_members if m not in excluded]
            if not candidates:
                await interaction.response.send_message("No eligible players to promote.", ephemeral=True)
                return

            view = PromoteCoCaptainView(member, team_name, candidates)
            await interaction.response.send_message("Select a player to promote:", view=view, ephemeral=True)
            return

        if action == "-co-captain":
            if not cocap_role:
                await interaction.response.send_message("No co-captain role for this team.", ephemeral=True)
                return

            cocaps = list(cocap_role.members)
            if not cocaps:
                await interaction.response.send_message("There are no co-captains to demote.", ephemeral=True)
                return

            view = DemoteCoCaptainView(member, team_name, cocaps)
            await interaction.response.send_message("Select a co-captain to demote:", view=view, ephemeral=True)
            return

        if action == "kick":
            if not team_role:
                await interaction.response.send_message("No team role found for this team.", ephemeral=True)
                return

            roster_members = set(team_role.members)
            members = [m for m in roster_members if m != member]
            if not members:
                await interaction.response.send_message("No one to kick on your team.", ephemeral=True)
                return

            view = KickMemberView(member, team_name, members)
            await interaction.response.send_message("Select a member to kick:", view=view, ephemeral=True)
            return

        if action == "transfer_captain":
            candidates_set = set()
            for r in (player_role, cocap_role):
                if r:
                    candidates_set.update(r.members)
            candidates = [m for m in candidates_set if m != member]
            if not candidates:
                await interaction.response.send_message("No eligible members to transfer captain to.", ephemeral=True)
                return

            view = TransferCaptainView(member, team_name, candidates)
            await interaction.response.send_message("Select the new captain:", view=view, ephemeral=True)
            return

        if action == "change_color":
            modal = ChangeColorModal(team_name, member)
            await interaction.response.send_modal(modal)
            return

        if action == "disband":
            team_role, captain_role, cocap_role, player_role = get_team_roles(guild, team_name)
            roles = [r for r in (team_role, captain_role, cocap_role, player_role) if r]

            for role in roles:
                for m in list(role.members):
                    try:
                        await m.remove_roles(role, reason=f"Team {team_name} disbanded by captain")
                    except Exception:
                        pass
                try:
                    await role.delete(reason=f"Team {team_name} disbanded by captain")
                except Exception:
                    pass

            teams = load_teams()
            teams = [t for t in teams if t.get("name", "").lower() != team_name.lower()]
            save_teams(teams)

            tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
            if tx_ch:
                await tx_ch.send(f"# {team_name} has been disbanded #")

            await interaction.response.send_message(f"Team **{team_name}** has been disbanded.", ephemeral=True)
            return


# ---- CaptainPanelView ----
class CaptainPanelView(discord.ui.View):
    def __init__(self, team_name: str):
        super().__init__(timeout=None)
        self.team_name = team_name

    @discord.ui.button(label="Open Captain Actions", style=discord.ButtonStyle.primary)
    async def open_actions(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        role_names = [r.name.lower() for r in getattr(member, "roles", [])]
        is_captain_role = any("captain |" in rn for rn in role_names)
        perms = getattr(member, "guild_permissions", None)
        has_priv = perms and (perms.administrator or perms.manage_guild)

        if not (is_captain_role or has_priv):
            await interaction.response.send_message(
                "Only captains, co-captains, or admins can use this panel.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Select a captain action:",
            view=TransactionActionView(member),
            ephemeral=True
        )


# ---- CoCaptainPanelView ----
class CoCaptainPanelView(discord.ui.View):
    def __init__(self, team_name: str):
        super().__init__(timeout=None)
        self.team_name = team_name

    @discord.ui.button(label="Invite Player", style=discord.ButtonStyle.primary)
    async def invite_player(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        role_names = [r.name.lower() for r in getattr(member, "roles", [])]
        is_cocaptain = any("co-captain |" in rn for rn in role_names) or any("captain |" in rn for rn in role_names)
        if not is_cocaptain:
            await interaction.response.send_message("Only co-captains or captains can use this.", ephemeral=True)
            return
        view = InviteSelectView(member)
        await interaction.response.send_message("Who do you invite to your team?", view=view, ephemeral=True)

    @discord.ui.button(label="Kick Player", style=discord.ButtonStyle.danger)
    async def kick_player(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True)
            return

        role_names = [r.name.lower() for r in getattr(member, "roles", [])]
        is_cocaptain = any("co-captain |" in rn for rn in role_names) or any("captain |" in rn for rn in role_names)
        if not is_cocaptain:
            await interaction.response.send_message("Only co-captains or captains can use this.", ephemeral=True)
            return

        team_name = get_leadership_team_name(member)
        if not team_name:
            await interaction.response.send_message("Could not determine your team.", ephemeral=True)
            return

        roster = get_team_roster_members(guild, team_name)
        members = [
            m for m in (roster["players"] + roster["cocaps"] + roster["captains"])
            if m != member
        ]

        if not members:
            await interaction.response.send_message("No one to kick on your team.", ephemeral=True)
            return

        view = KickMemberView(member, team_name, members)
        await interaction.response.send_message("Select a member to kick:", view=view, ephemeral=True)


# ---------- create_team command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="create_team", description="Create a new team (staff only)")
@discord.app_commands.describe(team_name="Name of the team", captain="Captain user", color_code="Color code (3 digits)")
async def create_team(interaction: discord.Interaction, team_name: str, captain: discord.Member, color_code: str):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Only staff can use this.", ephemeral=True)
        return

    team_name = team_name.strip()
    if not team_name:
        await interaction.response.send_message("Team name cannot be empty.", ephemeral=True)
        return

    if len(color_code) != 3 or not color_code.isdigit():
        await interaction.response.send_message("Color code must be 3 digits.", ephemeral=True)
        return
    try:
        hex_color = gtag_to_hex(color_code)
    except Exception:
        await interaction.response.send_message("Invalid color code.", ephemeral=True)
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    teams = load_teams()
    if find_team_entry(teams, team_name):
        await interaction.response.send_message(f"Team '{team_name}' already exists.", ephemeral=True)
        return

    color_obj = discord.Color(hex_color)

    try:
        team_role = discord.utils.get(guild.roles, name=team_name) or await guild.create_role(
            name=team_name,
            color=color_obj,
            reason="AGT team created"
        )
    except Exception:
        logging.exception("Failed creating team role")
        await interaction.response.send_message("Failed to create team role.", ephemeral=True)
        return

    global_captain_role, _, _ = get_global_team_roles(guild)

    roles_to_add = [team_role]
    if global_captain_role and global_captain_role not in captain.roles:
        roles_to_add.append(global_captain_role)

    try:
        if roles_to_add:
            await captain.add_roles(*roles_to_add, reason="Assigned as captain of new team")
    except Exception:
        logging.exception("Failed assigning roles to captain")

    teams.append({
        "name": team_name,
        "color": hex_color,
        "captain": captain.id,
        "roster_locked": False,
        "color_code": color_code
    })
    save_teams(teams)

    await log_transaction(
        guild,
        f"**New Team Created!**\n• Team Name: {team_role.mention}\n• Team Captain: {captain.mention}"
    )
    await interaction.response.send_message(
        f"Team {team_name} created with captain {captain.mention}.",
        ephemeral=True
    )
# ---------- end create_team ----------

# --# ---------- code command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="code", description="Generate a random code for two teams (staff only)")
async def code(interaction: discord.Interaction, team1: discord.Role, team2: discord.Role):
    guild = interaction.guild
    await log_transaction(guild, f"{interaction.user.mention} used /code")

    if not is_staff(interaction.user):
        await interaction.response.send_message("Only staff can use this.", ephemeral=True)
        return

    code_value = f"GTPC{random.randint(1000, 9999)}"

    channel = interaction.channel
    if not channel:
        await interaction.response.send_message("Cannot determine channel.", ephemeral=True)
        return

    message = f"{team1.mention} and {team2.mention} code is: ||{code_value}||"
    await channel.send(message)
    await interaction.response.send_message(
        f"Code generated and posted: {code_value}",
        ephemeral=True
    )
# --# ---------- end code ----------

# ---------- submit_score command ----------
@bot.tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="submit_score",
    description="Submit a scrim/match score and update seeding"
)
@discord.app_commands.describe(
    team1="Team 1 role",
    team2="Team 2 role",
    winner_team="Winning team role",
    score="Final score (e.g. 5-0)",
    timecap="Player who timecapped (leave empty if none)",
    next_stage="What they move into (e.g. 'bracket round')"
)
async def submit_score(
    interaction: discord.Interaction,
    team1: discord.Role,
    team2: discord.Role,
    winner_team: discord.Role,
    score: str,
    timecap: Optional[discord.Member],
    next_stage: str
):
    # Staff-only check
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "Only staff can submit scores with this command.",
            ephemeral=True
        )
        return

    scores_channel = bot.get_channel(MATCH_SCORES_CHANNEL_ID)
    if scores_channel is None:
        await interaction.response.send_message(
            "Match scores channel not configured.",
            ephemeral=True
        )
        return

    # Determine loser
    if winner_team.id == team1.id:
        loser_team = team2
    elif winner_team.id == team2.id:
        loser_team = team1
    else:
        await interaction.response.send_message(
            "Winner must be either Team 1 or Team 2.",
            ephemeral=True
        )
        return

    # Build timecap text
    timecap_text = timecap.mention if timecap else "none"

    msg = (
        f"# {team1.mention} vs {team2.mention}\n"
        f"winner: {winner_team.mention}\n"
        f"score: {score}\n"
        f"timecaper: {timecap_text}\n"
        f"{winner_team.mention} moves to the next {next_stage}"
    )

    await scores_channel.send(msg)

    # ---- update seeding stats ----
    teams = load_teams()

    winner_entry = ensure_team_stats_entry(teams, winner_team.name.strip())
    loser_entry = ensure_team_stats_entry(teams, loser_team.name.strip())

    winner_entry["wins"] = int(winner_entry.get("wins", 0)) + 1
    loser_entry["losses"] = int(loser_entry.get("losses", 0)) + 1

    # timecap = extra 3 pts for winner
    if timecap is not None:
        winner_entry["timecaps"] = int(winner_entry.get("timecaps", 0)) + 1

    recompute_points(winner_entry)
    recompute_points(loser_entry)

    save_teams(teams)

    # optional transaction log
    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(
            f"Score submitted: {winner_team.name} beat {loser_team.name} ({score}), "
            f"timecap: {timecap_text}. "
            f"Seeding -> {winner_team.name}: {winner_entry['points']}P, "
            f"{loser_team.name}: {loser_entry['points']}P"
        )

    await interaction.response.send_message("Score submitted and seeding updated.", ephemeral=True)

# ---------- end submit_score ----------

# ---------- submit_time command ----------
@bot.tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="submit_time",
    description="Propose a match time. Captains must accept; casters/refs can claim after acceptance (staff only)"
)
@discord.app_commands.describe(
    week="Example: WEEK1",
    time="Example: Today at 8PM EST",
    team1="Team 1 role",
    team2="Team 2 role"
)
async def submit_time(interaction: discord.Interaction, week: str, time: str, team1: discord.Role, team2: discord.Role):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Only staff can use this.", ephemeral=True)
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    match_key = f"{week} — {team1.name} vs {team2.name}"
    announce_text = (
        f"WEEK: {week}\n"
        f"Time: {time}\n"
        f"Match: {team1.mention} vs {team2.mention}\n\n"
        f"Both captains must accept to post the match and create assignment claims."
    )

    target_channel = interaction.channel
    if target_channel and isinstance(target_channel, discord.abc.GuildChannel):
        ch_name = getattr(target_channel, "name", "") or ""
        if not ch_name.startswith("scrim-"):
            target_channel = bot.get_channel(MATCH_TIMES_CHANNEL_ID) or interaction.channel
    else:
        target_channel = bot.get_channel(MATCH_TIMES_CHANNEL_ID)

    if target_channel is None:
        await interaction.response.send_message(
            "Match times channel not configured and no channel context.",
            ephemeral=True
        )
        return

    view = AcceptView(match_key, time, week, team1.name, team2.name, target_channel.id)

    try:
        await target_channel.send(
            content=f"{team1.mention} {team2.mention}\n{announce_text}",
            view=view
        )
    except Exception:
        logging.exception("Failed to post accept message")
        await interaction.response.send_message("Failed to post accept message.", ephemeral=True)
        return

    await interaction.response.send_message(
        f"Proposed match posted for {match_key} in {target_channel.mention}. "
        f"Waiting for captains to accept.",
        ephemeral=True
    )
# ---------- end submit_time ----------

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # detect newly added roles
    before_roles = set(before.roles)
    after_roles = set(after.roles)
    new_roles = after_roles - before_roles

    if not new_roles or after.guild is None:
        return

    guild = after.guild
    global_captain_role, global_cocap_role, global_player_role = get_global_team_roles(guild)

    for role in new_roles:
        lower = role.name.lower()

        # When a player gets a "Player | Team" role, also give them the team role + global player
        if lower.startswith("player |"):
            team_name = role.name.split("|", 1)[1].strip()
            team_role, _, _, player_role = get_team_roles(guild, team_name)

            roles_to_add = []
            if team_role and team_role not in after.roles:
                roles_to_add.append(team_role)
            if global_player_role and global_player_role not in after.roles:
                roles_to_add.append(global_player_role)

            if roles_to_add:
                try:
                    await after.add_roles(*roles_to_add, reason="Auto-link Player | Team to team/global roles")
                except Exception:
                    pass

        # (optional but nice) When someone gets Captain | / Co-Captain |, ensure they also have team + global roles
        elif lower.startswith("captain |") or lower.startswith("co-captain |"):
            team_name = role.name.split("|", 1)[1].strip()
            team_role, _, _, _ = get_team_roles(guild, team_name)

            roles_to_add = []
            if team_role and team_role not in after.roles:
                roles_to_add.append(team_role)
            if lower.startswith("captain |") and global_captain_role and global_captain_role not in after.roles:
                roles_to_add.append(global_captain_role)
            if lower.startswith("co-captain |") and global_cocap_role and global_cocap_role not in after.roles:
                roles_to_add.append(global_cocap_role)
            if global_player_role and global_player_role not in after.roles:
                roles_to_add.append(global_player_role)

            if roles_to_add:
                try:
                    await after.add_roles(*roles_to_add, reason="Auto-link captain/co-captain to team/global roles")
                except Exception:
                    pass


# ---------- seeding & record_result ----------
@bot.tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="seeding",
    description="Show current seeding (staff only)"
)
async def seeding(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Only staff can use this.", ephemeral=True)
        return

    teams = load_teams()
    # ensure fields exist and recompute points consistently
    for t in teams:
        t.setdefault("wins", 0)
        t.setdefault("losses", 0)
        t.setdefault("timecaps", 0)
        recompute_points(t)

    # Sort by: points desc, timecaps desc, wins desc, name asc
    sorted_teams = sorted(
        teams,
        key=lambda x: (
            -int(x.get("points", 0)),
            -int(x.get("timecaps", 0)),
            -int(x.get("wins", 0)),
            x.get("name", "").lower()
        )
    )

    lines = []
    header = f"{interaction.guild.name} SEEDING"
    lines.append(header)
    for idx, t in enumerate(sorted_teams, start=1):
        name = t.get("name", "Unknown")
        w = t.get("wins", 0)
        l = t.get("losses", 0)
        tc = t.get("timecaps", 0)
        p = t.get("points", 0)
        lines.append(f"{idx}. {name} - {w}W {l}L {tc}T | {p}P")
    lines.append("")
    lines.append("Key:")
    lines.append("Win = 3PTS")
    lines.append("Loss = 0PTS")
    lines.append("Timecap = 2PTS")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="record_result",
    description="Mark the last posted match as a timecap and update seeding (staff only)"
)
async def record_result(
    interaction: discord.Interaction,
):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Only staff can use this.", ephemeral=True)
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    scores_channel = bot.get_channel(MATCH_SCORES_CHANNEL_ID)
    if scores_channel is None:
        await interaction.response.send_message(
            "Match scores channel not configured.",
            ephemeral=True
        )
        return

    # Get the most recent score message
    last_msg = None
    async for m in scores_channel.history(limit=1):
        last_msg = m
        break

    if last_msg is None:
        await interaction.response.send_message(
            "No match scores found to record.",
            ephemeral=True
        )
        return

    # Expecting the format created by /submit_score:
    # # Team1 vs Team2
    # winner: @WinnerTeamRole
    lines = last_msg.content.splitlines()
    if len(lines) < 2 or not lines[0].startswith("# "):
        await interaction.response.send_message(
            "Could not parse the last match message. Make sure it was created by /submit_score.",
            ephemeral=True
        )
        return

    # Get team roles and winner role from mentions
    mentioned_roles = [r for r in last_msg.role_mentions]
    if len(mentioned_roles) < 3:
        await interaction.response.send_message(
            "Could not find enough team roles in the last match message.",
            ephemeral=True
        )
        return

    team1_role = mentioned_roles[0]
    team2_role = mentioned_roles[1]
    winner_role = mentioned_roles[2]

    if winner_role.id == team1_role.id:
        loser_role = team2_role
    elif winner_role.id == team2_role.id:
        loser_role = team1_role
    else:
        await interaction.response.send_message(
            "Could not determine winner/loser from the last match message.",
            ephemeral=True
        )
        return

    # ---- update seeding stats ----
    teams = load_teams()

    winner_entry = ensure_team_stats_entry(teams, winner_role.name.strip())
    loser_entry = ensure_team_stats_entry(teams, loser_role.name.strip())

    winner_entry["wins"] = int(winner_entry.get("wins", 0)) + 1
    loser_entry["losses"] = int(loser_entry.get("losses", 0)) + 1

    # Always treat as timecap for the winner
    winner_entry["timecaps"] = int(winner_entry.get("timecaps", 0)) + 1

    recompute_points(winner_entry)
    recompute_points(loser_entry)

    save_teams(teams)

    await interaction.response.send_message(
        f"Timecap result recorded for last match. {winner_entry['name']} now has {winner_entry['points']} points.",
        ephemeral=True
    )
# ---------- end seeding & record_result ----------



# ---------- leave command ----------
@bot.tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="leave",
    description="Leave your current team"
)
async def leave(interaction: discord.Interaction):
    member = interaction.user
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message(
            "Must be used in a server.",
            ephemeral=True
        )
        return

    team_name = get_member_team_name(member)
    if not team_name:
        await interaction.response.send_message(
            "You are not on a team.",
            ephemeral=True
        )
        return

    team_role, captain_role, cocap_role, player_role = get_team_roles(guild, team_name)
    roles_to_remove = [
        r for r in (team_role, captain_role, cocap_role, player_role)
        if r and r in member.roles
    ]

    if roles_to_remove:
        try:
            await member.remove_roles(
                *roles_to_remove,
                reason=f"Left team {team_name}"
            )
        except Exception:
            pass

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(
            f"{member.mention} Has Left {team_name}"
        )

    await interaction.response.send_message(
        f"You have left **{team_name}**.",
        ephemeral=True
    )
# ---------- end leave ----------

# ---------- add_team command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="add_team", description="Add a player to a team (assign roles)")
async def add_team(interaction: discord.Interaction, member: discord.Member, team_name: str):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Must be used in guild.", ephemeral=True)
        return

    team_role, _, _, player_role = get_team_roles(guild, team_name)
    if not team_role and not player_role:
        await interaction.response.send_message("Team roles not found.", ephemeral=True)
        return

    counts = get_team_roster_counts(guild, team_name)
    if counts["player"] >= 12:
        await interaction.response.send_message(f"Team **{team_name}** is at maximum capacity (12 players).", ephemeral=True)
        return

    roles_to_add = [r for r in (team_role, player_role) if r]
    await member.add_roles(*roles_to_add, reason="Added to team")

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(f"{member.mention} Has Joined **{team_name}**")

    await interaction.response.send_message("Player added to team.", ephemeral=True)
# ---------- end add_team ----------

# ---------- check_invites command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="check_invites", description="Check pending team invites")
async def check_invites(interaction: discord.Interaction):
    user_invites = pending_invites.get(interaction.user.id, [])
    if not user_invites:
        await interaction.response.send_message("You have no pending invites.", ephemeral=True)
        return

    invite = user_invites[0]
    inviter = interaction.guild.get_member(invite["inviter_id"]) if interaction.guild else None
    inviter_name = inviter.display_name if inviter else "Unknown"
    content = f"Invite to join {invite.get('team_name','Team')} from {inviter_name}"
    await interaction.response.send_message(content, view=InviteDecisionView(interaction.user.id, 0), ephemeral=True)

# ---------- end check_invites ----------

# ---------- disban command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="disban", description="Disband a team (captain can disband their own; staff can disband any)")
async def disban(interaction: discord.Interaction, team_name: str = None):
    member = interaction.user
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    staff = is_staff(member)
    if team_name is None:
        team_name = get_member_team_name(member)
        if not team_name:
            await interaction.response.send_message(
                "You must specify a team name, or be a Captain/Co-Captain/Player of a team.",
                ephemeral=True
            )
            return
    else:
        if not staff:
            own_team = get_member_team_name(member)
            if not own_team or own_team.lower() != team_name.strip().lower():
                await interaction.response.send_message(
                    "Only staff can disband other teams. Captains may only disband their own team.",
                    ephemeral=True
                )
                return

    team_name = team_name.strip()
    team_role, _, _, _ = get_team_roles(guild, team_name)

    global_captain_role, global_cocap_role, global_player_role = get_global_team_roles(guild)

    if team_role:
        for m in list(team_role.members):
            roles_to_remove = [team_role]
            if global_captain_role in m.roles:
                roles_to_remove.append(global_captain_role)
            if global_cocap_role in m.roles:
                roles_to_remove.append(global_cocap_role)
            if global_player_role in m.roles:
                roles_to_remove.append(global_player_role)
            try:
                await m.remove_roles(*roles_to_remove, reason=f"Team {team_name} disbanded")
            except Exception:
                pass

        try:
            await team_role.delete(reason=f"Team {team_name} disbanded")
        except Exception:
            pass

    teams = load_teams()
    teams = [t for t in teams if t.get("name", "").lower() != team_name.lower()]
    save_teams(teams)

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(f"# **{team_name}** has been disbanded #")

    await interaction.response.send_message(
        f"Team **{team_name}** has been disbanded.",
        ephemeral=True
    )
# ---------- end disban ----------


# ---------- disban_all command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="disban_all", description="Disband all teams in the system (staff only)")
async def disban_all(interaction: discord.Interaction):
    member = interaction.user
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    if not is_staff(member):
        await interaction.response.send_message("Only staff can disband all teams.", ephemeral=True)
        return

    global_captain_role, global_cocap_role, global_player_role = get_global_team_roles(guild)

    teams = load_teams()
    for t in teams:
        team_name = t.get("name")
        if not team_name:
            continue

        team_role, _, _, _ = get_team_roles(guild, team_name)
        if not team_role:
            continue

        for m in list(team_role.members):
            roles_to_remove = [team_role]
            if global_captain_role in m.roles:
                roles_to_remove.append(global_captain_role)
            if global_cocap_role in m.roles:
                roles_to_remove.append(global_cocap_role)
            if global_player_role in m.roles:
                roles_to_remove.append(global_player_role)
            try:
                await m.remove_roles(*roles_to_remove, reason="All teams disbanded")
            except Exception:
                pass

        try:
            await team_role.delete(reason="All teams disbanded")
        except Exception:
            pass

    save_teams([])

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(f"All teams have been disbanded by {member.mention}")

    await interaction.response.send_message(
        "All teams in the system have been disbanded.",
        ephemeral=True
    )
# ---------- end disban_all ----------

# ---------- roster_lock / unlock commands ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="roster_lock", description="Enable roster lock on a team (staff only)")
async def roster_lock(interaction: discord.Interaction, team_name: str):
    member = interaction.user
    if not is_staff(member):
        await interaction.response.send_message("Only staff can roster lock teams.", ephemeral=True)
        return

    teams = load_teams()
    entry = find_team_entry(teams, team_name)
    if not entry:
        await interaction.response.send_message("Team not found in system.", ephemeral=True)
        return

    entry["roster_locked"] = True
    save_teams(teams)

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(
            f"Roster lock has been enabled on **{entry['name']}** by {member.mention}"
        )

    await interaction.response.send_message(
        f"Roster lock enabled on **{entry['name']}**.",
        ephemeral=True
    )

@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="roster_lock_all", description="Enable roster lock on all teams (staff only)")
async def roster_lock_all(interaction: discord.Interaction):
    member = interaction.user
    if not is_staff(member):
        await interaction.response.send_message("Only staff can roster lock all teams.", ephemeral=True)
        return

    teams = load_teams()
    if not teams:
        await interaction.response.send_message("No teams found in the system.", ephemeral=True)
        return

    for t in teams:
        t["roster_locked"] = True
    save_teams(teams)

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(
            f"Roster lock has been enabled on **all teams** by {member.mention}"
        )

    await interaction.response.send_message(
        "Roster lock enabled on all teams.",
        ephemeral=True
    )

@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="roster_unlock", description="Disable roster lock on a team (staff only)")
async def roster_unlock(interaction: discord.Interaction, team_name: str):
    member = interaction.user
    if not is_staff(member):
        await interaction.response.send_message("Only staff can unlock team rosters.", ephemeral=True)
        return

    teams = load_teams()
    entry = find_team_entry(teams, team_name)
    if not entry:
        await interaction.response.send_message("Team not found in system.", ephemeral=True)
        return

    entry["roster_locked"] = False
    save_teams(teams)

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(
            f"Roster lock has been **disabled** on **{entry['name']}** by {member.mention}"
        )

    await interaction.response.send_message(
        f"Roster lock disabled on **{entry['name']}**. Captains and co-captains can invite again.",
        ephemeral=True
    )

@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="roster_unlock_all", description="Disable roster lock on all teams (staff only)")
async def roster_unlock_all(interaction: discord.Interaction):
    member = interaction.user
    if not is_staff(member):
        await interaction.response.send_message("Only staff can unlock all team rosters.", ephemeral=True)
        return

    teams = load_teams()
    if not teams:
        await interaction.response.send_message("No teams found in the system.", ephemeral=True)
        return

    for t in teams:
        t["roster_locked"] = False
    save_teams(teams)

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(
            f"Roster lock has been **disabled** on **all teams** by {member.mention}"
        )

    await interaction.response.send_message(
        "Roster lock disabled on all teams. Captains and co-captains can invite again.",
        ephemeral=True
    )
# ---------- end roster_lock / unlock ----------

# ---------- captain_panel command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="captain_panel", description="Show the captain panel (captains only)")
async def captain_panel(interaction: discord.Interaction):
    member = interaction.user
    guild = interaction.guild

    if not guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    # treat global captain role OR per‑team 'Captain | Team' role as captain
    global_captain_role, _, _ = get_global_team_roles(guild)
    has_global_captain = global_captain_role in member.roles if global_captain_role else False
    has_per_team_captain = any(r.name.lower().startswith("captain |") for r in member.roles)
    if not (has_global_captain or has_per_team_captain):
        await interaction.response.send_message(
            "You must be a Captain of a team to use this.",
            ephemeral=True
        )
        return

    # figure out team name by matching team roles from teams.json
    team_name = None
    teams = load_teams()
    for t in teams:
        name = t.get("name")
        if not name:
            continue
        role = discord.utils.get(guild.roles, name=name)
        if role and role in member.roles:
            team_name = name
            break

    # fallback: use 'Captain | TeamName' pattern if present
    if not team_name:
        for r in member.roles:
            lower = r.name.lower()
            if lower.startswith("captain |"):
                team_name = r.name.split("|", 1)[1].strip()
                break

    if not team_name:
        await interaction.response.send_message(
            "Could not determine which team you captain.",
            ephemeral=True
        )
        return

    team_role, captain_role, cocap_role, player_role = get_team_roles(guild, team_name)

    captain = member
    cocaps = cocap_role.members if cocap_role else []
    players = list(player_role.members) if player_role else []

    co_caps_text = ", ".join(m.mention for m in cocaps) if cocaps else "None"
    players_text = ", ".join(m.mention for m in players) if players else "No players found."

    desc = (
        "Review your roster, leadership, and team identity below.\n\n"
        "Use the buttons to manage invites, kicks, leadership, color, and disbanding."
    )

    colour = None
    if captain_role:
        colour = captain_role.colour
    elif team_role:
        colour = team_role.colour
    else:
        colour = discord.Colour.blurple()

    embed = discord.Embed(
        title=f"GTPC Captain Panel – {team_name}",
        description=desc,
        colour=colour
    )
    embed.add_field(name="👑 Captain", value=captain.mention if captain else "None", inline=False)
    embed.add_field(name="🤝 Co-Captains", value=co_caps_text, inline=False)
    embed.add_field(name="🧑‍🤝‍🧑 Team Members", value=players_text, inline=False)
    embed.set_footer(text="GTPC Transactions Bot")

    view = CaptainPanelView(team_name)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
# ---------- end captain_panel ----------

# ---------- co-captain_panel command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="co-captain_panel", description="Show the co-captain panel (co-captains only)")
async def co_captain_panel(interaction: discord.Interaction):
    member = interaction.user
    guild = interaction.guild

    if not guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    global_captain_role, global_cocap_role, _ = get_global_team_roles(guild)
    has_global_cocap = global_cocap_role in member.roles if global_cocap_role else False
    has_global_captain = global_captain_role in member.roles if global_captain_role else False
    has_per_team_cocap = any(r.name.lower().startswith("co-captain |") for r in member.roles)

    if not (has_global_cocap or has_global_captain or has_per_team_cocap):
        await interaction.response.send_message(
            "You must be a Co-Captain of a team to use this.",
            ephemeral=True
        )
        return

    team_name = None
    teams = load_teams()
    for t in teams:
        name = t.get("name")
        if not name:
            continue
        role = discord.utils.get(guild.roles, name=name)
        if role and role in member.roles:
            team_name = name
            break

    if not team_name:
        for r in member.roles:
            lower = r.name.lower()
            if lower.startswith("co-captain |"):
                team_name = r.name.split("|", 1)[1].strip()
                break

    if not team_name:
        await interaction.response.send_message(
            "Could not determine which team you are co-captain of.",
            ephemeral=True
        )
        return

    team_role, captain_role, cocap_role, player_role = get_team_roles(guild, team_name)

    team_members = set(team_role.members) if team_role else set()
    captains = set(captain_role.members) if captain_role else set()
    cocaps = set(cocap_role.members) if cocap_role else set()

    captain = next(iter(captains), None)
    cocap_list = list(cocaps)
    players = [m for m in team_members if m not in captains and m not in cocaps]

    co_caps_text = ", ".join(m.mention for m in cocap_list) if cocap_list else "None"
    players_text = ", ".join(m.mention for m in players) if players else "No players found."

    desc = (
        "Review your roster, leadership, and team identity below.\n\n"
        "Use the buttons to invite or kick players."
    )

    colour = None
    if cocap_role:
        colour = cocap_role.colour
    elif team_role:
        colour = team_role.colour
    else:
        colour = discord.Colour.blurple()

    embed = discord.Embed(
        title=f"GTPC Co-Captain Panel – {team_name}",
        description=desc,
        colour=colour
    )
    embed.add_field(name="👑 Captain", value=captain.mention if captain else "None", inline=False)
    embed.add_field(name="🤝 Co-Captains", value=co_caps_text, inline=False)
    embed.add_field(name="🧑‍🤝‍🧑 Team Members", value=players_text, inline=False)
    embed.set_footer(text="GTPC Transactions Bot")

    view = CoCaptainPanelView(team_name)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
# ---------- end co-captain_panel ----------


# ---------- addscrim command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="addscrim", description="Create a scrim channel for two teams (staff only)")
async def addscrim(interaction: discord.Interaction, team1: discord.Role, team2: discord.Role):
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
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    ch_name = f"scrim-{team1.name}-vs-{team2.name}".lower().replace(" ", "-")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        team1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        team2: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }

    channel = await guild.create_text_channel(
        name=ch_name,
        overwrites=overwrites,
        reason=f"Scrim created by {member}"
    )

    await channel.send(
        f"⚔️ **Scrim Created**\n\n"
        f"{team1.mention} vs {team2.mention}\n\n"
        f"# Welcome to GTPC seeding.\n"
        f"🗓️ You guys will have 5 days to schedule\n"
        f"⚔️ And 6 days to play\n"
        f"GOOD LUCK TEAMS (you’ll need it😈)"
        f"ping a staff member when you’re ready to schedule or if you have any questions!"
    )

    await interaction.response.send_message(
        f"Created {channel.mention}",
        ephemeral=True
    )
# ---------- end addscrim ----------

# ---------- info command ----------
@bot.tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="info",
    description="Show information about the GTPC Transactions Bot commands"
)
async def info(interaction: discord.Interaction):
    embed = discord.Embed(
        title="GTPC Transactions Bot – Command Guide",
        description=(
            "What every command does and who is allowed to use it.\n\n"
            "**Bot Name:** GTPC Transactions Bot\n"
            "**Created by:** banner"
        ),
        colour=discord.Colour.blurple()
    )

    # Everyone
    embed.add_field(
        name="/info",
        value="Who can use it: **Everyone**\nShows information about this bot and its commands.",
        inline=False
    )
    embed.add_field(
        name="/check_invites",
        value="Who can use it: **Everyone**\nCheck your pending team invites and accept or decline.",
        inline=False
    )
    embed.add_field(
        name="/roster",
        value="Who can use it: **Everyone**\nView team rosters that are stored in the system.",
        inline=False
    )
    embed.add_field(
        name="/submit_score",
        value=(
            "Who can use it: **Staff**\n"
            "Submit a scrim/match result in the format:\n"
            "`# Team A vs Team B #` with winner, score, and who moves to the next round."
        ),
        inline=False
    )
    embed.add_field(
        name="/leave",
        value=(
            "Who can use it: **Players**\n"
            "Leave your current team and remove all associated roles."
        ),
        inline=False
    )

    # Captains / Co-Captains
    embed.add_field(
        name="/captain_panel",
        value=(
            "Who can use it: **Captains**\n"
            "Open the captain panel to manage:\n"
            "• Invites\n"
            "• Kicks\n"
            "• Co‑captain promotions/demotions\n"
            "• Captain transfers\n"
            "• Team color changes\n"
            "• Disbanding your team"
        ),
        inline=False
    )
    embed.add_field(
        name="/co-captain_panel",
        value=(
            "Who can use it: **Co-Captains**\n"
            "Open a lighter panel to invite or kick players from your team."
        ),
        inline=False
    )

    # Staff / Admin tools
    embed.add_field(
        name="/create_team",
        value=(
            "Who can use it: **Staff**\n"
            "Create a new team, set its captain, and apply the color code."
        ),
        inline=False
    )
    embed.add_field(
        name="/submit_time",
        value=(
            "Who can use it: **Staff / League Management**\n"
            "Submit a match time for two teams; posts formatted info and creates assignments."
        ),
        inline=False
    )
    embed.add_field(
        name="/add_team",
        value=(
            "Who can use it: **Staff**\n"
            "Add a player to a team (gives them the team and player roles)."
        ),
        inline=False
    )
    embed.add_field(
        name="/disban",
        value=(
            "Who can use it: **Captains (their own team)** / **Staff (any team)**\n"
            "Disband a specific team and remove its roles."
        ),
        inline=False
    )
    embed.add_field(
        name="/disban_all",
        value=(
            "Who can use it: **Staff**\n"
            "Disband all teams in the system and clean up their roles."
        ),
        inline=False
    )
    embed.add_field(
        name="/roster_lock",
        value=(
            "Who can use it: **Staff**\n"
            "Enable roster lock on a specific team (no more roster moves)."
        ),
        inline=False
    )
    embed.add_field(
        name="/roster_lock_all",
        value=(
            "Who can use it: **Staff**\n"
            "Enable roster lock on all teams in the system."
        ),
        inline=False
    )
    embed.add_field(
        name="/addscrim",
        value=(
            "Who can use it: **Staff / Admins**\n"
            "Create a scrim text channel for two team roles with proper permissions."
        ),
        inline=False
    )
    embed.add_field(
        name="/code",
        value=(
            "Who can use it: **Staff**\n"
            "Generate a random code for two teams."
        ),
        inline=False
    )
    embed.add_field(
        name="/unlock roster",
        value=(
            "Who can use it: **Staff**\n"
            "Disable roster lock on a specific team (no more roster moves)."
        ),
        inline=False
    )
    embed.add_field(
        name="/unlock roster all",
        value=(
            "Who can use it: **Staff**\n"
            "Disable roster lock on all teams in the system."
        ),
        inline=False
    )
    embed.add_field(
        name="/seeding",
        value=(
            "Who can use it: **Staff**\n"
            "Show current seeding based on recorded match results."
        ),
        inline=False
    )
    embed.set_footer(text="GTPC Transactions Bot • created by banner")

    await interaction.response.send_message(embed=embed, ephemeral=True)
# ---------- end info ----------

# ---------------- Startup ----------------
@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild
    if not guild:
        return

    team_name = get_leadership_team_name(member)
    if not team_name:
        return

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(
            f"{member.mention} left the server and was automatically removed from {team_name}"
        )

# ---------------- on_ready and run ----------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"Bot is in guilds: {[g.id for g in bot.guilds]}")
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        print(f"Bot is not in guild {GUILD_ID}. Please invite the bot to the server with the correct permissions.")
        return
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"Synced {len(synced)} command(s) to guild {GUILD_ID}")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    await print_guild_commands()

async def print_guild_commands():
    try:
        guild_obj = discord.Object(id=GUILD_ID)
        cmds = await bot.tree.fetch_commands(guild=guild_obj)
        logging.info(f"Guild-registered commands: {[c.name for c in cmds]}")
    except Exception:
        logging.exception("Failed to fetch guild commands")

if __name__ == "__main__":
    bot.run(os.getenv("TOKEN"))
