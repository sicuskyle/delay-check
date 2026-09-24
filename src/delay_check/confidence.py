import statistics

from delay_check.config import get_config
from delay_check.commons import print_Title


config = get_config()


def _largest_agreement_cluster(delays: list[int], tolerance: int) -> list[int]:
    ordered = sorted(delays)
    clusters = []
    current_cluster = [ordered[0]]
    for delay in ordered[1:]:
        if delay - current_cluster[-1] <= tolerance:
            current_cluster.append(delay)
        else:
            clusters.append(current_cluster)
            current_cluster = [delay]
    clusters.append(current_cluster)
    return max(clusters, key=len)


def aggregate_delay(segment_results: list[dict]) -> tuple:
    if not segment_results:
        return None, []

    confidence_threshold = config.confidence_threshold
    correlated_delays = [
        s["Delay"] for s in segment_results if s["Score"] >= confidence_threshold
    ]

    # A single window clearing the score threshold is not enough on its own:
    # with several independent windows checked against a permissive
    # per-window threshold (real dubbed content often only scores well on
    # shared music/effects, not dialogue, so the threshold has to stay low),
    # unrelated audio has a real chance of exactly one window scoring high
    # by coincidence. Require at least two correlated windows to agree with
    # each other (same tolerance as the consensus fallback below) before
    # trusting them outright.
    if len(correlated_delays) > 1:
        cluster = _largest_agreement_cluster(correlated_delays, config.drift_tolerance['excellent'])
        if len(cluster) > 1:
            return int(round(statistics.median(cluster))), cluster

    # No corroborated high-score agreement. This is also the common path for
    # real dubbed content, where only part of a window's audio (shared
    # music/effects, not the re-recorded dialogue) actually correlates,
    # capping the per-window score even for a correct match. Fall back to
    # cross-window consensus: many independent windows tightly agreeing on
    # the same delay is itself strong evidence, regardless of their
    # individual scores -- the chance of that happening for unrelated
    # audio is far lower than any single window's score being spuriously
    # high.
    all_delays = [s["Delay"] for s in segment_results]
    cluster = _largest_agreement_cluster(all_delays, config.drift_tolerance['excellent'])

    if len(cluster) > len(segment_results) / 2:
        return int(round(statistics.median(cluster))), cluster

    return None, []


def calculate_confidence(delays: list[int], anchor: int | None = None) -> tuple[float, list[dict]]:
    print_Title("Phase 2: Confidence level (constant delay)", 55)
    print("Strategy: Cross-Correlation Consistency Check")
    if anchor is None:
        print("* Anchor Point: Establishes base delay from first Segment")
    else:
        print("* Anchor Point: Median delay across correlated windows")
    print(f"* Multi-Point Analysis: Samples {config.segments_to_analyze} windows across the file.")
    print("* Drift Calculation: Measures variance against the anchor.")
    print("* Confidence Scoring: Detects VFR or edits via sync penalties.\n")

    if not delays:
        return 0.0, []

    drift_tolerance = config.drift_tolerance
    base_confidence = config.base_confidence
    penalty_factors = config.penalty_factors

    if anchor is None:
        # delays[0] is the anchor itself and isn't re-scored against itself;
        # scored_delays[0] (delays[1]) is window #2, so segment_index = i+1.
        anchor = delays[0]
        scored_delays = delays[1:]
        max_per_segment = config.max_confidence_per_segment
        index_offset = 1
    else:
        # Every real window (including delays[0], window #1) is scored
        # against the external anchor, so segment_index = i.
        scored_delays = delays
        max_per_segment = (100.0 - base_confidence) / len(delays)
        index_offset = 0

    confidence_score = base_confidence
    out_of_sync_segments = []

    for i, current_delay in enumerate(scored_delays, start=1):
        diff = abs(anchor - current_delay)

        if diff <= drift_tolerance['excellent']:
            penalty_factor = penalty_factors['excellent']
        elif diff <= drift_tolerance['good']:
            penalty_factor = penalty_factors['good']
        elif diff <= drift_tolerance['acceptable']:
            penalty_factor = penalty_factors['acceptable']
        elif diff <= drift_tolerance['poor']:
            penalty_factor = penalty_factors['poor']
        else:
            penalty_factor = penalty_factors['failed']
            out_of_sync_segments.append({
                "segment_index": i + index_offset,
                "delay_found": current_delay,
                "drift_amount": diff
            })

        confidence_score += max_per_segment * penalty_factor

    return round(confidence_score, 2), out_of_sync_segments
