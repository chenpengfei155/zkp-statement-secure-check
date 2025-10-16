#!/usr/bin/env python3
"""CirVerify Web UI - A simple web interface for analyzing Circom circuits."""

from flask import Flask, render_template, request, jsonify
import tempfile
import os
import sys
import json
from io import StringIO
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from cirverify.core import detect, print_reports, report_to_file

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB max file size

# Example circuits
EXAMPLES = {
    "Single Assignment Issue": """template SingleAssignment0() {
    signal input a;
    signal input b;
    signal output out;
    out <-- a + 1;
    out === b + 1;
}
component main = SingleAssignment0();""",

    "Unconstrained Output": """template UnconstrainedOutput() {
    signal input a;
    signal output out;
    out <-- a * 2;
}
component main = UnconstrainedOutput();""",

    "Valid Circuit": """template Multiplier() {
    signal input a;
    signal input b;
    signal output c;
    c <== a * b;
}
component main = Multiplier();""",

    "Unused Signal": """template UnusedSignal() {
    signal input a;
    signal input b;
    signal unused;
    signal output out;
    out <== a + b;
}
component main = UnusedSignal();"""
}


@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html', examples=EXAMPLES)


@app.route('/analyze', methods=['POST'])
def analyze():
    """Analyze the provided Circom code."""
    try:
        data = request.get_json()
        code = data.get('code', '').strip()
        
        if not code:
            return jsonify({'error': 'No code provided'}), 400
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.circom', delete=False) as f:
            f.write(code)
            temp_path = f.name
        
        try:
            # Run analysis
            graphs, reports = detect(temp_path)
            
            if not graphs or not reports:
                return jsonify({'error': 'Analysis failed. Check if the Circom code is valid.'}), 400
            
            # Capture printed output
            old_stdout = sys.stdout
            sys.stdout = StringIO()
            print_reports(graphs, reports)
            output = sys.stdout.getvalue()
            sys.stdout = old_stdout
            
            return jsonify({'result': output if output else 'No issues detected! ✅'})
            
        finally:
            # Clean up temp file
            if os.path.exists(temp_path):
                os.unlink(temp_path)
                
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/upload', methods=['POST'])
def upload():
    """Handle file upload."""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.endswith('.circom'):
            return jsonify({'error': 'File must be a .circom file'}), 400
        
        # Read file content
        code = file.read().decode('utf-8')
        return jsonify({'code': code})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    print("Starting CirVerify Web UI...")
    print("Access the application at: http://127.0.0.1:5000")
    app.run(debug=True, host='127.0.0.1', port=5000)

