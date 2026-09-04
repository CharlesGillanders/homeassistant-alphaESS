"""Currency handling for the monetary sensors.

AlphaESS answers `moneyType` with either an ISO 4217 code or the currency
symbol, and has changed which one it sends. Home Assistant's monetary device
class only accepts a code, so a symbol has to be resolved to one.

Some symbols name more than one currency, and there the only honest tiebreak
available is the currency Home Assistant is already configured for: a "$" from
an inverter belonging to someone running AUD is far more likely to be AUD than
USD.
"""
from __future__ import annotations

# Symbol -> the ISO codes that share it, most common first. A single-entry list
# is unambiguous and is used whatever Home Assistant is configured for.
SYMBOL_TO_ISO: dict[str, list[str]] = {
    "$": ["USD", "AUD", "CAD", "NZD", "SGD", "HKD", "MXN"],
    "€": ["EUR"],
    "£": ["GBP"],
    "¥": ["JPY", "CNY"],
    "元": ["CNY"],
    "₩": ["KRW"],
    "₹": ["INR"],
    "₽": ["RUB"],
    "₺": ["TRY"],
    "R$": ["BRL"],
    "₫": ["VND"],
    "₴": ["UAH"],
    "₱": ["PHP"],
    "₦": ["NGN"],
    "Fr": ["CHF"],
    "kr": ["SEK", "NOK", "DKK", "ISK"],
    "zł": ["PLN"],
    "Kč": ["CZK"],
    "A$": ["AUD"],
    "C$": ["CAD"],
    "NZ$": ["NZD"],
    "R": ["ZAR"],
}


def normalize_currency_unit(value: str | None, fallback: str | None) -> str | None:
    """Return an ISO 4217 code for a `moneyType`, or the fallback.

    A three-letter code is taken as-is. A symbol naming one currency resolves to
    it. A symbol naming several resolves to the configured currency when that is
    one of them, and otherwise to the most common. Anything unrecognised falls
    back rather than guessing.
    """
    if value is None:
        return fallback

    normalized = value.strip()
    if not normalized:
        return fallback

    # Already a 3-letter ISO code
    if len(normalized) == 3 and normalized.isalpha():
        return normalized.upper()

    candidates = SYMBOL_TO_ISO.get(normalized)
    if not candidates:
        # Unknown symbol - the configured currency is a better answer than a guess.
        return fallback

    if len(candidates) > 1 and fallback:
        configured = fallback.strip().upper()
        if configured in candidates:
            return configured

    return candidates[0]
