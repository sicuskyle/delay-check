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

- **`default_analysis_time_sec`**: Maximum analysis time for the first segment (seconds)
- **`segment_analysis_time_sec`**: Duration of each additional segment (seconds)
- **`confidence_threshold`**: Minimum correlation threshold for validity (0-100)
- **`drift_tolerance_ms`**: Drift tolerances in milliseconds
- **`sample_rate`**: Sampling frequency for processing (Hz)
- **`n_mfcc`**: Number of MFCC coefficients for audio fingerprinting

## How it works

### Phase 1: Getting the initial delay
1. Extracts the first 300 seconds (configurable) from both files
2. Computes audio fingerprints using MFCC
3. Applies cross-correlation to find the optimal offset
4. Reports the estimated delay and correlation score

### Phase 2: Segment validation
1. Analyzes 4 additional segments (25%, 50%, 75%, end)
2. Calculates the delay for each segment
3. Compares consistency over time

### Phase 3: Confidence level
1. Compares delays across all segments
2. Applies penalties for time drift
3. Generates a confidence score (0-100%)
4. Detects potential issues (VFR, different versions, etc.)

## Interpreting results

### Correlation score
- **Above `confidence_threshold`** (20 by default, configurable in `config.json`): Good correlation for that window
- **Below `confidence_threshold`**: The tool falls back to cross-window consensus -- if a majority of windows still agree tightly on the same delay, that consensus is used instead. Real dubbed content (different dialogue, shared music/effects) often scores well below 100% per window even for a correct match, since only part of each window's audio actually correlates.

### Confidence level
- **>= 95%**: Constant and reliable delay
- **< 95%**: Variable delay, possible sync issue

### Time drift
- **<= 25ms**: Excellent consistency
- **26-50ms**: Good consistency
- **51-75ms**: Acceptable consistency
- **76-100ms**: Poor consistency
- **> 100ms**: Out of sync (possible VFR or editing)

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
│   ├── test_config.py
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

