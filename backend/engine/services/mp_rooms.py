"""
Smash Kart Multiplayer – Room Logic
===================================
In-memory room store for the Flask-SocketIO backend.

A *room* is identified by a 6-letter uppercase code (A-Z). The first player
who creates a room is its "host". Any subsequent player can join using the
code. State is kept in-process only (no DB); rooms auto-destroy after 60s of
being empty.

The blueprint (`blueprints/smash_kart.py`) owns REST endpoints; the SocketIO
handlers (registered in `app.py`) own realtime events. Both talk to this
module through a single `RoomStore` instance returned by `get_store()`.

Positional data (`state`, `shot`) is trusted from clients for responsiveness;
authoritative changes (hp/kills on `hit`) are computed here so the server
can't be trivially cheated.
"""
from __future__ import annotations

import logging
import random
import string
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

_logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────
ROOM_CODE_LENGTH = 6
ROOM_CODE_ALPHABET = string.ascii_uppercase  # A-Z
MAX_PLAYERS_PER_ROOM = 8
EMPTY_ROOM_TTL_SECONDS = 60
STARTING_HP = 100
SWEEP_INTERVAL_SECONDS = 10  # how often the reaper thread wakes up


# ── Data classes ───────────────────────────────────────────────────────
@dataclass
class Player:
    id: str                # socket id (sid) from SocketIO
    name: str
    color: str             # kart color (e.g. "#ff0055")
    hp: int = STARTING_HP
    kills: int = 0
    x: float = 0.0
    z: float = 0.0
    angle: float = 0.0
    speed: float = 0.0
    weapon: str = "pistol"

    def to_public(self) -> dict:
        """Payload shape sent to other clients."""
        return {
            "playerId": self.id,
            "name": self.name,
            "color": self.color,
            "hp": self.hp,
            "kills": self.kills,
            "x": self.x,
            "z": self.z,
            "angle": self.angle,
            "speed": self.speed,
            "weapon": self.weapon,
        }


@dataclass
class Room:
    code: str
    host_id: Optional[str] = None
    players: Dict[str, Player] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    empty_since: Optional[float] = None  # set when player count hits 0

    @property
    def player_count(self) -> int:
        return len(self.players)

    @property
    def is_full(self) -> bool:
        return self.player_count >= MAX_PLAYERS_PER_ROOM

    def snapshot(self) -> dict:
        return {
            "code": self.code,
            "host": self.host_id,
            "players": [p.to_public() for p in self.players.values()],
            "createdAt": self.created_at,
        }


# ── Store ──────────────────────────────────────────────────────────────
class RoomStore:
    """Thread-safe room registry. Used by both REST + SocketIO handlers."""

    def __init__(self) -> None:
        self._rooms: Dict[str, Room] = {}
        self._lock = threading.RLock()
        # sid → code lookup, so we can find the room on disconnect without
        # scanning the whole dict.
        self._sid_to_room: Dict[str, str] = {}
        self._reaper_started = False

    # ---- code generation ---------------------------------------------
    def _new_code(self) -> str:
        with self._lock:
            for _ in range(50):
                code = "".join(
                    random.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH)
                )
                if code not in self._rooms:
                    return code
            # Astronomically unlikely – 26^6 ≈ 308M codes.
            raise RuntimeError("Could not generate unique room code after 50 tries")

    # ---- lifecycle ---------------------------------------------------
    def create_room(self, host_hint: Optional[str] = None) -> Room:
        with self._lock:
            code = self._new_code()
            room = Room(code=code, host_id=host_hint, empty_since=time.time())
            self._rooms[code] = room
            _logger.info("[mp] room %s created", code)
            return room

    def get_room(self, code: str) -> Optional[Room]:
        with self._lock:
            return self._rooms.get(code.upper())

    def destroy_room(self, code: str) -> None:
        with self._lock:
            room = self._rooms.pop(code.upper(), None)
            if room:
                for sid in list(room.players.keys()):
                    self._sid_to_room.pop(sid, None)
                _logger.info("[mp] room %s destroyed", code)

    # ---- player ops --------------------------------------------------
    def add_player(
        self,
        code: str,
        sid: str,
        name: str,
        color: str,
    ) -> Optional[Player]:
        """Add a player. Returns None if room missing or full."""
        with self._lock:
            room = self._rooms.get(code.upper())
            if room is None or room.is_full:
                return None
            if sid in room.players:
                return room.players[sid]  # idempotent re-join
            player = Player(id=sid, name=name or "Player", color=color or "#ffffff")
            room.players[sid] = player
            self._sid_to_room[sid] = room.code
            if room.host_id is None:
                room.host_id = sid
            room.empty_since = None
            _logger.info("[mp] %s joined %s (%d/%d)", sid[:6], room.code,
                         room.player_count, MAX_PLAYERS_PER_ROOM)
            return player

    def remove_player(self, sid: str) -> Optional[tuple[Room, Player]]:
        """Remove a player by sid. Returns (room, player) if found, else None."""
        with self._lock:
            code = self._sid_to_room.pop(sid, None)
            if not code:
                return None
            room = self._rooms.get(code)
            if room is None:
                return None
            player = room.players.pop(sid, None)
            if player is None:
                return None
            # Reassign host if needed
            if room.host_id == sid:
                room.host_id = next(iter(room.players), None)
            if room.player_count == 0:
                room.empty_since = time.time()
            _logger.info("[mp] %s left %s (%d left)", sid[:6], code, room.player_count)
            return room, player

    def update_state(
        self,
        sid: str,
        x: float,
        z: float,
        angle: float,
        speed: float,
        weapon: Optional[str] = None,
    ) -> Optional[Room]:
        with self._lock:
            code = self._sid_to_room.get(sid)
            if not code:
                return None
            room = self._rooms.get(code)
            if room is None or sid not in room.players:
                return None
            p = room.players[sid]
            p.x, p.z, p.angle, p.speed = x, z, angle, speed
            if weapon:
                p.weapon = weapon
            return room

    def apply_hit(
        self,
        attacker_sid: str,
        victim_sid: str,
        damage: int,
    ) -> Optional[dict]:
        """Server-authoritative hp update. Returns a dict describing the outcome
        or None if either player isn't in the same room."""
        with self._lock:
            code = self._sid_to_room.get(attacker_sid)
            if not code or self._sid_to_room.get(victim_sid) != code:
                return None
            room = self._rooms.get(code)
            if room is None:
                return None
            victim = room.players.get(victim_sid)
            attacker = room.players.get(attacker_sid)
            if victim is None or attacker is None:
                return None
            damage = max(0, int(damage))
            victim.hp = max(0, victim.hp - damage)
            killed = victim.hp == 0
            if killed:
                attacker.kills += 1
                # Respawn after a brief delay – but keep it simple: just reset hp
                # and let the client decide where to respawn. Server just flips
                # the hp back so the next hit doesn't immediately re-kill.
                victim.hp = STARTING_HP
            return {
                "room": room.code,
                "attackerId": attacker_sid,
                "victimId": victim_sid,
                "damage": damage,
                "victimHp": victim.hp,
                "killed": killed,
                "attackerKills": attacker.kills,
            }

    # ---- reaper ------------------------------------------------------
    def start_reaper(
        self,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Start background thread that destroys rooms empty longer than TTL."""
        with self._lock:
            if self._reaper_started:
                return
            self._reaper_started = True

        def _loop():
            while True:
                try:
                    now = time.time()
                    with self._lock:
                        expired = [
                            r.code for r in self._rooms.values()
                            if r.empty_since is not None
                            and (now - r.empty_since) > EMPTY_ROOM_TTL_SECONDS
                        ]
                    for code in expired:
                        self.destroy_room(code)
                except Exception:
                    _logger.exception("[mp] reaper iteration failed")
                sleep(SWEEP_INTERVAL_SECONDS)

        t = threading.Thread(target=_loop, name="mp-room-reaper", daemon=True)
        t.start()


# ── Module-level singleton ─────────────────────────────────────────────
_store: Optional[RoomStore] = None
_store_lock = threading.Lock()


def get_store() -> RoomStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = RoomStore()
            _store.start_reaper()
        return _store
