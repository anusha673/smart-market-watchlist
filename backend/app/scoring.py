from typing import Optional

PRICE_WEIGHT = 40
VOLUME_WEIGHT = 35
FIFTY_TWO_WEEK_WEIGHT = 25
FIFTY_TWO_WEEK_PROXIMITY_PCT = 1.5  # "near" the 52-week high/low means within this %


def compute_attention_score(
    delta_pct: Optional[float],
    price_threshold_pct: float,
    volume_ratio: Optional[float],
    volume_threshold: float,
    price: Optional[float],
    year_high: Optional[float],
    year_low: Optional[float],
) -> tuple[int, list[str]]:
    """Combines three independent signals into one 0-100 score, instead of a
    single flat price threshold. Each signal fires on its own - a stock can
    score high purely on unusual volume even if its price barely moved,
    which is exactly the 'institutional conviction vs retail noise'
    distinction a price-only threshold can't make. Weights are a considered
    starting point (price weighted highest since it's the most direct
    signal), not something derived from backtested data - worth saying
    plainly if asked, rather than presenting them as more rigorous than
    they are.
    """
    score = 0
    triggers: list[str] = []

    if delta_pct is not None and abs(delta_pct) >= price_threshold_pct:
        score += PRICE_WEIGHT
        triggers.append(f"Moved {delta_pct:+.2f}% since you last checked")

    if volume_ratio is not None and volume_ratio >= volume_threshold:
        score += VOLUME_WEIGHT
        triggers.append(f"Volume {volume_ratio:.1f}x the 10-day average")

    if price and year_high and year_high > 0:
        if abs(price - year_high) / year_high * 100 <= FIFTY_TWO_WEEK_PROXIMITY_PCT:
            score += FIFTY_TWO_WEEK_WEIGHT
            triggers.append("Near its 52-week high")

    if price and year_low and year_low > 0:
        if abs(price - year_low) / year_low * 100 <= FIFTY_TWO_WEEK_PROXIMITY_PCT:
            score += FIFTY_TWO_WEEK_WEIGHT
            triggers.append("Near its 52-week low")

    return min(score, 100), triggers
