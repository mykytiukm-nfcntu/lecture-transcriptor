"""Human-readable language names keyed by faster-whisper's ISO 639-1 code."""
from __future__ import annotations

_NAMES: dict[str, str] = {
    "en": "English",
    "uk": "Ukrainian",
    "ru": "Russian",
    "pl": "Polish",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "tr": "Turkish",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "cs": "Czech",
    "sk": "Slovak",
    "ro": "Romanian",
    "hu": "Hungarian",
    "el": "Greek",
    "ar": "Arabic",
    "he": "Hebrew",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "vi": "Vietnamese",
    "hi": "Hindi",
    "bg": "Bulgarian",
    "sr": "Serbian",
    "hr": "Croatian",
    "sl": "Slovenian",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "et": "Estonian",
    "ka": "Georgian",
    "az": "Azerbaijani",
    "be": "Belarusian",
    "ca": "Catalan",
}


def language_name(code: str) -> str:
    """Return the English name for the given ISO 639-1 code, or the code itself if unknown."""
    return _NAMES.get(code.lower(), code)
