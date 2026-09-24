# Delay Check

Tool to calculate the time offset (delay) between two audio files, ideal for synchronization.

## Features

    - **Precise delay detection**: Uses audio fingerprints (MFCC) and multi-channel cross-correlation with overlap normalization and sub-frame interpolation
- **Multi-window validation**: Analyzes delay consistency over time across evenly spaced windows
- **Trend tracking**: Predicts the expected delay from confirmed windows and retries divergent windows with a pre-shifted read
- **Timebase drift detection**: Detects linear delay drift (speed mismatch) and suggests an `atempo` correction instead of a fixed offset
- **Multi-format support**: Works with audio and video files
- **Track selection**: Interactive handling of multiple audio tracks
- **Flexible configuration**: All parameters adjustable via JSON
- **Logging**: Detailed logging of all operations
- **Robust error handling**: Specific exceptions and clear messages

## Installation

### Requirements

- Python 3.10 or higher
- **FFmpeg**: Required for audio extraction and conversion


### From the repository

```bash
git clone <repository-url>
cd delay-check

# Editable installation (recommended for development)
pip install -e .

# Or direct installation
pip install .
```

### Verify installation

```bash
delay-check --help
# or
python -m delay_check --help
```

### System dependencies

**Windows:**
- Download FFmpeg from [ffmpeg.org](https://ffmpeg.org/download.html)
- Add it to your system PATH

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

## Usage

### Basic syntax

```bash
delay-check <reference_file> <dubbed_file>
```

Or as a Python module:

```bash
python -m delay_check <reference_file> <dubbed_file>
```

### Examples

```bash
# Compare two audio files
delay-check original.wav dubbed.wav

# Compare two videos
delay-check original.mkv dubbed.mkv

# The program will ask to select the audio track if there are multiple
```

### Supported formats

**Video containers:** `.mkv`, `.mp4`, `.mka`, `.m4a`

**Audio files:** `.wav`, `.eac3`, `.e-ac3`, `.ac3`, `.ec3`, `.aac`, `.dts`, `.dtshd`, `.flac`, `.thd`, `.mp3`

## Configuration

The `config.json` file inside the package allows you to customize all program parameters.

### Main parameters

- **`application.segment_analysis_time_sec`**: Duration of each analysis window in seconds (60 by default)
- **`application.confidence_threshold`**: Minimum correlation score for a window to count as individually correlated (20 by default)
- **`application.drift_tolerance_ms`**: Delay-drift thresholds used by the confidence calculation
- **`audio_processing.sample_rate`**: Sampling frequency for processing (44100 Hz by default)
- **`audio_processing.mfcc.n_mfcc`**: Number of MFCC coefficients for audio fingerprinting (13 by default)
- **`confidence_scoring.base_confidence`**: Initial confidence score before comparing windows (20 by default)
- **`confidence_scoring.segments_to_analyze`**: Number of windows sampled across the file (8 by default)
- **`confidence_scoring.penalty_factors`**: Score multipliers for each drift-tolerance range
- **`correlation.min_overlap_frames`**: Minimum overlapping frames required for a lag to be considered (10 by default)
- **`correlation.min_overlap_fraction`**: Minimum overlap as a fraction of the shorter window (0.15 by default)
- **`correlation.row_std_floor_ratio`**: Relative floor used to discard silent/degenerate MFCC rows (0.01 by default)

## How it works

### Phase 1: Window analysis
1. Determines the shorter duration of the two extracted audio samples.
2. Places 8 evenly spaced windows from 0 to the end of that shared duration by default (first window always starts at 0, last window ends at the file end).
3. Loads 60 seconds per window by default (configurable via `segment_analysis_time_sec`).
4. Computes MFCC fingerprints and applies multi-channel cross-correlation to each window (per-coefficient z-score, overlap normalization, parabolic sub-frame peak refinement).
5. Tracks the delay trend from windows that cleared `confidence_threshold`. If a naive (unshifted) result diverges from the predicted trend by more than 20% of the window's length (`TRACKING_DEVIATION_FRACTION`, typical of the correlation overlap-capacity limit), the window is retried with a pre-shifted read and only the residual is resolved.
6. Reports the delay and correlation score for every window.

### Phase 2: Delay consistency and confidence
1. Selects windows whose correlation score meets `confidence_threshold`.
2. Requires at least **two** of those correlated windows to agree with each other within `drift_tolerance.excellent` (25 ms by default); a single high-score window alone is never trusted, since with a permissive per-window threshold one window can score high by coincidence on unrelated audio.
3. Uses the median delay of that agreeing cluster as the estimated delay.
4. If there is no corroborated high-score agreement, falls back to a strict-majority consensus cluster over **all** window delays (regardless of individual scores) -- many independent windows tightly agreeing is itself strong evidence, and is also the common path for real dubbed content where only shared music/effects correlate per window.
5. Compares all window delays against the estimated delay.
6. Starts at `base_confidence` and distributes the remaining score across the analyzed windows, applying drift penalties.

## Interpreting results

### Correlation score
- **Above `confidence_threshold`** (20 by default, configurable in `config.json`): Good correlation for that window -- but a single such window is not enough on its own. At least two correlated windows must agree within the excellent drift tolerance before their median is accepted.
- **Below `confidence_threshold`**, or without corroborated agreement: The tool falls back to cross-window consensus -- if a strict majority of windows still agree tightly on the same delay, that consensus is used instead. Real dubbed content (different dialogue, shared music/effects) often scores well below 100% per window even for a correct match, since only part of each window's audio actually correlates.
- **No agreement found**: the tool reports that no reliable constant delay could be estimated. Possible causes: unrelated content, or different cuts/versions (Director's Cut, Extended, Theatrical, Unrated, etc.) with added/removed/reordered scenes or different music.

### Confidence level
- The default base score is 20 points.
- The remaining 80 points are distributed across 8 windows, giving each window a maximum of 10 points.
- Each window receives a multiplier based on its delay drift: `1.0`, `0.95`, `0.85`, `0.70`, or `0.0`.
- A perfect result scores 100%: `20 + (8 * 10)`.
- **>= 95%**: Constant and reliable delay
- **< 95%**: Variable delay, possible sync issue

### Time drift
- **<= 25ms**: Excellent consistency
- **26-50ms**: Good consistency
- **51-75ms**: Acceptable consistency
- **76-100ms**: Poor consistency
- **> 100ms**: Out of sync (possible VFR or editing)

### Timebase drift
When the delay between windows changes **linearly** over time (a strong straight-line fit, R >= 0.9, slope >= 0.5 ms/s), the tool reports a timebase drift instead of a constant offset:

```
### Timebase drift ###
 ### Dubbed is 0.153% (1530 ppm) FASTER than reference
 ### Delay slope: +1.526 ms per second
 ### Recommendation: apply tempo correction, not a fixed offset
 ### Suggested: ffmpeg -i dubbed -af "atempo=0.99848" output
```

- **Sign convention**: positive growing delay = dubbed content appears earlier over time = dubbed is **faster**; negative trend = dubbed is **slower**.
- **Units**: `%` (percent of speed difference) and `ppm` (parts per million; 1% = 10,000 ppm).
- **Fix**: a single delay cannot correct this -- the dubbed track needs a tempo/resample adjustment (the suggested `atempo` value brings the dubbed rate back in line with the reference).
- The block is printed when a linear drift is detected: either after the confidence level when confidence is below 95% (replacing the generic "visually review" recommendation), or when no constant delay could be estimated at all (in place of the generic no-confidence warning).

## Troubleshooting

### Error: "ffmpeg not in path"
Install FFmpeg and add it to your system PATH.

### Low correlation score (below `confidence_threshold`)
**Possible causes:**
- The audio files are fundamentally different
- One of the audio files has additional effects or music
- Significant differences in equalization or volume
- The files are not related

### No reliable constant delay found
**Possible causes:**
- The audio files are not related content
- Different cuts/versions (Director's Cut, Extended, Theatrical, Unrated, etc.) with added/removed/reordered scenes or different music
- Only one window cleared the threshold and no corroborating window agreed with it (a single high-score window is discarded as a possible coincidence)

### Low confidence level (< 95%)
**Possible causes:**
- **Timebase drift**: One track runs slightly faster/slower (see Timebase drift above)
- **No corroborated agreement**: Fewer than two correlated windows agreed within the excellent tolerance, so the delay came from the consensus fallback
- **VFR (Variable Frame Rate)**: The video has a variable frame rate
- **Different versions**: The files come from different sources
- **Edits**: One of the files has been edited or cut
- **Sampling issues**: Different original sample rates

## Development

### Project structure

```
delay-check/
├── src/
│   └── delay_check/
│       ├── __init__.py      # Version and metadata
│       ├── __main__.py      # Entry point as a module
│       ├── cli.py           # CLI, arguments and main flow
│       ├── config.py        # Configuration management
│       ├── commons.py       # General utilities
│       ├── utils.py         # Audio-specific utilities
│       ├── fgp.py           # Fingerprinting and correlation
│       ├── fileinfo.py      # Multimedia file information
│       └── config.json      # Default configuration
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── helpers.py
│   ├── test_cli.py
│   ├── test_config.py
│   ├── test_fgp.py
│   ├── test_fileinfo.py
│   └── test_utils.py
├── pyproject.toml
├── LICENSE
└── README.md
```

### Running tests

```bash
pip install pytest
pytest tests/
```

### Contributing

Contributions are welcome.

1. Fork the repository
2. Create a branch for your feature (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Authors

- **Sicuskyle**

## Acknowledgments

- [librosa](https://librosa.org/) library for audio analysis
- [FFmpeg](https://ffmpeg.org/) for media processing

