"""
Audio alerts using pygame.mixer.
Lazy-initialised so the bot starts even when no audio device is available.
Falls back to a log warning when pygame or the WAV files are missing.
"""
import logging
import os

logger = logging.getLogger(__name__)

_ROOT            = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUY_SOUND_PATH  = os.path.join(_ROOT, "dashboard", "assets", "alert_long.wav")
_SELL_SOUND_PATH = os.path.join(_ROOT, "dashboard", "assets", "alert_short.wav")

_mixer_ready = False
_buy_sound   = None
_sell_sound  = None


def _init() -> bool:
    """Attempt to initialise pygame.mixer once. Returns True on success."""
    global _mixer_ready, _buy_sound, _sell_sound
    if _mixer_ready:
        return True
    try:
        # pygame-ce (community edition) is preferred on Python 3.12+;
        # fall back to standard pygame if ce is not installed
        try:
            import pygame_ce as pygame
        except ImportError:
            import pygame
        pygame.mixer.init(frequency=44100, size=-16, channels=1, buffer=512)
        if os.path.exists(_BUY_SOUND_PATH):
            _buy_sound = pygame.mixer.Sound(_BUY_SOUND_PATH)
        else:
            logger.warning("alert_long.wav not found — run generate_sounds.py to create it")
        if os.path.exists(_SELL_SOUND_PATH):
            _sell_sound = pygame.mixer.Sound(_SELL_SOUND_PATH)
        else:
            logger.warning("alert_short.wav not found — run generate_sounds.py to create it")
        _mixer_ready = True
        return True
    except Exception as exc:
        logger.warning("Audio unavailable (%s) — alerts will be visual only", exc)
        return False


def play_alert(direction: str) -> None:
    """Play the BUY (ascending) or SELL (descending) chime."""
    if not _init():
        return
    sound = _buy_sound if direction == "long" else _sell_sound
    if sound is not None:
        sound.play()
    else:
        logger.debug("Audio file missing for direction=%s", direction)


def play_reminder(direction: str) -> None:
    """Repeat chime when countdown reaches 5 seconds."""
    play_alert(direction)


def is_ready() -> bool:
    """Return True when mixer is initialised and sound files are loaded."""
    _init()
    return _mixer_ready and _buy_sound is not None and _sell_sound is not None
