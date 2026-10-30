# Layer Forge

Layer Forge is a Python-based SVG processing tool that generates multiple layout variations and exports them as SVG and PNG files in different dimensions.

## Features

- Processes SVG input files in batches.
- Generates multiple output dimensions.
- Supports proportional and full-bleed layouts.
- Exports designs in SVG and PNG formats.
- Organizes generated files into separate output folders.
- Processes multiple SVG files in one run.

## Supported Output Dimensions

- 1080 × 1080
- 1080 × 1920
- 1200 × 628
- 1200 × 1200
- 1200 × 1500

## Project Structure

```text
Layer-Forge/
├── inputs/                 # Input SVG files
├── outputs/                # Generated output files
├── batch_svg.py            # Batch SVG processing
├── editor.html             # HTML editor
├── layout_quality.py       # Layout quality utilities
├── main.py                 # Main Python module
├── resize_svg.py           # SVG resizing utilities
├── run.py                  # Application entry point
├── svg_layout.py           # SVG layout processing
├── svg_native.py           # Native SVG processing
├── svg_web.py              # Web-related SVG functionality
├── requirements.txt        # Python dependencies
└── requirements-svg.txt    # SVG processing dependencies
```

## Requirements

- Python 3.11 or newer
- Internet connection for the initial dependency installation

## Installation and Usage

1. Clone the repository:

   ```bash
   git clone https://github.com/ananyagurram8/Layer-Forge.git
   ```

2. Navigate to the project directory:

   ```bash
   cd Layer-Forge
   ```

3. Run the application:

   ```bash
   python run.py
   ```

On the first run, the startup script creates a virtual environment and installs the SVG processing dependencies.

## Output

Generated files are saved in timestamped folders inside `outputs/`. The tool produces SVG and PNG files for the supported dimensions.

## Repository

GitHub: https://github.com/ananyagurram8/Layer-Forge
