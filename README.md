# Delay Check

Tool to calculate the time offset (delay) between two audio files, ideal for synchronization.

## Features

- **Precise delay detection**: Uses audio fingerprints (MFCC) and cross-correlation
- **Multi-segment validation**: Analyzes delay consistency over time
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

## How it works

### Phase 1: Window analysis
1. Determines the shorter duration of the two extracted audio samples.
2. Places 8 evenly spaced windows across that shared duration by default.
3. Loads 60 seconds per window by default (configurable via `segment_analysis_time_sec`).
4. Computes MFCC fingerprints and applies cross-correlation to each window.
5. Reports the delay and correlation score for every window.

### Phase 2: Delay consistency and confidence
1. Selects windows whose correlation score meets `confidence_threshold`.
2. Uses the median delay of those correlated windows as the estimated delay.
3. If no individual window meets the threshold, uses a strict-majority consensus cluster as a fallback.
4. Compares all window delays against the estimated delay.
5. Starts at `base_confidence` and distributes the remaining score across the analyzed windows, applying drift penalties.

## Interpreting results

### Correlation score
- **Above `confidence_threshold`** (20 by default, configurable in `config.json`): Good correlation for that window
- **Below `confidence_threshold`**: The tool falls back to cross-window consensus -- if a majority of windows still agree tightly on the same delay, that consensus is used instead. Real dubbed content (different dialogue, shared music/effects) often scores well below 100% per window even for a correct match, since only part of each window's audio actually correlates.

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
- The block is printed after the confidence level when confidence is below 95% and a linear drift is detected (it replaces the generic "visually review" recommendation).

## Troubleshooting

### Error: "ffmpeg not in path"
Install FFmpeg and add it to your system PATH.

### Low correlation score (below `confidence_threshold`)
**Possible causes:**
- The audio files are fundamentally different
- One of the audio files has additional effects or music
- Significant differences in equalization or volume
- The files are not related

### Low confidence level (< 95%)
**Possible causes:**
- **Timebase drift**: One track runs slightly faster/slower (see Timebase drift above)
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

