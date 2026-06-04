def score_signal(ema_score: float, structure_score: float,
                 pa_score: float, ml_win_prob: float = None) -> int:
    """
    Combine sub-scores into a final 0-100 confluence score.

    >= 70 : fire alert + queue trade
    50-69 : log only, no trade
    < 50  : discard
    """
    raw = ema_score + structure_score + pa_score   # max 100

    if ml_win_prob is not None:
        if ml_win_prob > 0.65:
            raw = min(100, raw * 1.10)
        elif ml_win_prob < 0.40:
            return 0   # suppress signal

    return round(raw)
