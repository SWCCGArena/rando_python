from enum import Enum

class GameState(Enum):
    """States the bot can be in"""
    STOPPED = "stopped"
    CONNECTING = "connecting"
    IN_LOBBY = "in_lobby"
    CREATING_TABLE = "creating_table"
    WAITING_FOR_OPPONENT = "waiting_for_opponent"
    JOINING_GAME = "joining_game"
    PLAYING = "playing"
    GAME_ENDED = "game_ended"
    ERROR = "error"
