# CirVerify Web UI

A simple web interface for analyzing Circom circuits.

## Features

- 📝 **Type Code**: Write or paste Circom code directly in the editor
- 📚 **Examples**: Select from pre-loaded example circuits with common issues
- 📁 **Upload**: Upload local `.circom` files for analysis
- ⚡ **Quick Analysis**: Instant security vulnerability detection

## Setup

1. Install dependencies:
   ```bash
   cd ..
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. Run the server:
   ```bash
   ./run_server.sh
   ```
   
   Or manually:
   ```bash
   cd ..
   source venv/bin/activate
   python3 web_ui/app.py
   ```

3. Open browser at: http://127.0.0.1:5000

## Usage

1. **Select an example** from the dropdown to see sample circuits
2. **Type your code** directly in the text area
3. **Upload a file** using the upload button
4. Click **Analyze** to check for vulnerabilities
5. View results showing detected issues

## Security Notes

- Max file size: 1MB
- Only `.circom` files accepted
- Temporary files are cleaned up after analysis

