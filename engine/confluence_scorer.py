def score_signal(ema_score: float, structure_score: float,
                 pa_score: float, ml_win_prob: float = None) -> int:
    """
    Combine sub-scores into a final 0-100 confluence score.

    Max contributions:
      EMA score:       0–25  (6 conditions, proportional)
      Structure score: 0–45  (structure +20, SR zone +15, BoS +10)
      PA score:        0–30  (clamped — contradicting patterns can't kill valid signals)

    Total max: 100

    >= MIN_CONFLUENCE_SCORE : fire alert + queue trade (default 60, slider can only raise)
    35–59                   : WATCHING — shown on dashboard, no trade
    < 35                    : discard
    """
    # Clamp PA: contradicting candle patterns shouldn't cancel strong EMA+structure setups
    pa_clamped = max(0.0, float(pa_score))
    raw = float(ema_score) + float(structure_score) + pa_clamped

    if ml_win_prob is not None:
        if ml_win_prob > 0.65:
            raw = min(100.0, raw * 1.10)
        elif ml_win_prob < 0.35:   # loosened from 0.40 — less aggressive suppression
            raw = raw * 0.70       # soft penalty instead of hard zero

    return round(raw)
