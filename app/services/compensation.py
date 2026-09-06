"""RFCTLARR award arithmetic: market value, statutory solatium, delay interest.

One function rather than inlined at each call site because the seed
generator and the compensation router must agree on the same formula — a
seeded award and an officer-entered one that disagreed on how solatium
compounds would be an inconsistency nobody would think to check for.
"""


def solatium_amount(market_value_amount: int, solatium_rate_pct: int) -> int:
    """Sec. 30(1): solatium is a fixed percentage of market value (100% under
    the current Act), not a flat sum, so it moves with market value."""
    return round(market_value_amount * solatium_rate_pct / 100)


def compute_award(market_value_amount: int, solatium_rate_pct: int, interest_amount: int) -> int:
    """Sec. 26-30: award = market value + solatium + Sec. 34 delay interest.

    Interest is not a percentage of market value — it depends on how late
    the award is — so it is supplied directly rather than derived here.
    """
    return market_value_amount + solatium_amount(market_value_amount, solatium_rate_pct) + interest_amount
