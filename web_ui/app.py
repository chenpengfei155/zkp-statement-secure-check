#!/usr/bin/env python3
"""CirVerify Web UI - A simple web interface for analyzing Circom circuits."""

from flask import Flask, render_template, request, jsonify, Response, stream_with_context
import tempfile
import os
import sys
import json
import re
import shutil
import threading
import queue
import time
from io import StringIO
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from cirverify.core import detect, print_reports, report_to_file

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size for folder uploads

# Global queue for progress updates and thread management
progress_queues = {}
analysis_threads = {}
stop_flags = {}

class ProgressCapture:
    """Capture stdout/stderr and send to queue for SSE."""
    def __init__(self, queue_obj):
        self.queue = queue_obj
        self.old_stdout = sys.stdout
        self.old_stderr = sys.stderr
        self.buffer = StringIO()
        
    def write(self, text):
        self.buffer.write(text)
        # Send to queue for real-time updates
        if text.strip():
            try:
                self.queue.put_nowait(('output', text))
            except queue.Full:
                pass
        return len(text)
    
    def flush(self):
        self.buffer.flush()
    
    def getvalue(self):
        return self.buffer.getvalue()
    
    def __enter__(self):
        sys.stdout = self
        sys.stderr = self
        return self
    
    def __exit__(self, *args):
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr

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
    """Analyze the provided Circom code with progress streaming."""
    import uuid
    session_id = str(uuid.uuid4())
    progress_queue = queue.Queue()
    progress_queues[session_id] = progress_queue
    stop_flags[session_id] = threading.Event()
    
    temp_dir = None
    temp_path = None
    
    # Get data before starting thread
    data = request.get_json()
    code = data.get('code', '').strip()
    
    def run_analysis():
        try:
            if not code:
                progress_queue.put(('error', 'No code provided'))
                return
            
            # Check if stopped
            if stop_flags.get(session_id, threading.Event()).is_set():
                progress_queue.put(('cancelled', 'Analysis cancelled by user'))
                return
            
            # Check if code contains include statements with relative paths
            include_pattern = r'include\s+["\']([^"\']+)["\']'
            includes = re.findall(include_pattern, code)
            has_relative_includes = any(
                not os.path.isabs(inc) and ('../' in inc or './' in inc or not inc.startswith('/'))
                for inc in includes
            )
            
            # If there are relative includes, create temp file in benchmarks directory
            if has_relative_includes:
                project_root = Path(__file__).parent.parent
                benchmarks_dir = project_root / 'benchmarks'
                temp_dir = tempfile.mkdtemp(prefix='cirverify_', dir=str(benchmarks_dir))
                temp_path = os.path.join(temp_dir, 'temp.circom')
                with open(temp_path, 'w', encoding='utf-8') as f:
                    f.write(code)
            else:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.circom', delete=False) as f:
                    f.write(code)
                    temp_path = f.name
            
            # Check if stopped before analysis
            if stop_flags.get(session_id, threading.Event()).is_set():
                progress_queue.put(('cancelled', 'Analysis cancelled by user'))
                return
            
            try:
                # Run analysis with progress capture
                with ProgressCapture(progress_queue) as capture:
                    # Note: detect() function doesn't support cancellation directly
                    # We can only cancel before it starts or after it completes
                    graphs, reports = detect(temp_path)
                    
                    # Check if stopped during analysis
                    if stop_flags.get(session_id, threading.Event()).is_set():
                        progress_queue.put(('cancelled', 'Analysis cancelled by user'))
                        return
                    
                    if not graphs or not reports:
                        progress_queue.put(('error', 'Analysis failed. Check if the Circom code is valid.'))
                        return
                    
                    # Capture printed output
                    print_reports(graphs, reports)
                    output = capture.getvalue()
                    
                    progress_queue.put(('complete', output if output else 'No issues detected! ✅'))
            finally:
                # Clean up temp file/directory
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except:
                        pass
                if temp_dir and os.path.exists(temp_dir):
                    try:
                        shutil.rmtree(temp_dir)
                    except:
                        pass
        except Exception as e:
            if not stop_flags.get(session_id, threading.Event()).is_set():
                progress_queue.put(('error', str(e)))
        finally:
            progress_queue.put(('done', None))
            # Clean up session data
            if session_id in progress_queues:
                del progress_queues[session_id]
            if session_id in stop_flags:
                del stop_flags[session_id]
            if session_id in analysis_threads:
                del analysis_threads[session_id]
    
    # Run analysis in background thread
    thread = threading.Thread(target=run_analysis)
    thread.daemon = True
    thread.start()
    analysis_threads[session_id] = thread
    
    return jsonify({'session_id': session_id})


@app.route('/stop/<session_id>', methods=['POST'])
def stop_analysis(session_id):
    """Stop a running analysis."""
    if session_id in stop_flags:
        stop_flags[session_id].set()
        if session_id in progress_queues:
            progress_queues[session_id].put(('cancelled', 'Analysis cancelled by user'))
        return jsonify({'success': True, 'message': 'Analysis stop requested'})
    return jsonify({'success': False, 'message': 'Session not found'}), 404


@app.route('/progress/<session_id>')
def progress_stream(session_id):
    """SSE endpoint for progress updates."""
    def generate():
        if session_id not in progress_queues:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Session not found'})}\n\n"
            return
        
        q = progress_queues[session_id]
        while True:
            try:
                msg_type, content = q.get(timeout=30)
                
                if msg_type == 'done':
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                elif msg_type == 'error':
                    yield f"data: {json.dumps({'type': 'error', 'message': content})}\n\n"
                elif msg_type == 'cancelled':
                    yield f"data: {json.dumps({'type': 'cancelled', 'message': content})}\n\n"
                elif msg_type == 'complete':
                    yield f"data: {json.dumps({'type': 'complete', 'result': content})}\n\n"
                elif msg_type == 'output':
                    # Parse progress bar information
                    progress_data = parse_progress(content)
                    yield f"data: {json.dumps({'type': 'progress', 'data': progress_data, 'raw': content})}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                break
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


def parse_progress(text):
    """Parse tqdm progress bar output."""
    import re
    # Match tqdm format: 100%|██████████| 33480/33480 [00:02<00:00, 14965.93it/s]
    pattern = r'(\d+)%\|.*?\| (\d+)/(\d+) \[([^\]]+)\]'
    match = re.search(pattern, text)
    if match:
        percent = int(match.group(1))
        current = int(match.group(2))
        total = int(match.group(3))
        time_info = match.group(4)
        return {
            'percent': percent,
            'current': current,
            'total': total,
            'time': time_info
        }
    
    # Match info messages like "[Info] Building condition constraint edges..."
    info_match = re.search(r'\[Info\]\s+(.+)', text)
    if info_match:
        return {
            'message': info_match.group(1).strip(),
            'type': 'info'
        }
    
    return {'raw': text.strip()}


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


def analyze_single_file(file_path, project_root):
    """Analyze a single Circom file and return the result."""
    try:
        graphs, reports = detect(file_path)
        
        if not graphs or not reports:
            return {'success': False, 'error': 'Analysis failed. Check if the Circom code is valid.'}
        
        # Capture printed output
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        print_reports(graphs, reports)
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        
        return {
            'success': True,
            'result': output if output else 'No issues detected! ✅'
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


@app.route('/analyze_folder', methods=['POST'])
def analyze_folder():
    """Analyze all .circom files in an uploaded folder."""
    temp_dir = None
    try:
        # Get project root directory
        project_root = Path(__file__).parent.parent
        benchmarks_dir = project_root / 'benchmarks'
        
        # Create a temporary directory in benchmarks to preserve relative paths
        temp_dir = tempfile.mkdtemp(prefix='cirverify_folder_', dir=str(benchmarks_dir))
        
        # Collect files and their paths
        file_data = []
        for key in request.files:
            if key.startswith('file_'):
                index = key.replace('file_', '')
                file = request.files[key]
                if file.filename and file.filename.endswith('.circom'):
                    # Get relative path from form data if available
                    path_key = f'path_{index}'
                    relative_path = request.form.get(path_key, file.filename)
                    file_data.append((file, relative_path))
        
        if not file_data:
            return jsonify({'error': 'No .circom files found in the folder'}), 400
        
        # Save all uploaded files preserving directory structure
        uploaded_files = []
        for file, relative_path in file_data:
            # Normalize path separators
            relative_path = relative_path.replace('\\', '/')
            # Remove leading folder name if present (webkitRelativePath includes folder name)
            parts = relative_path.split('/')
            if len(parts) > 1:
                # Keep only the relative path within the folder
                relative_path = '/'.join(parts[1:]) if len(parts) > 1 else parts[0]
            
            file_path = os.path.join(temp_dir, relative_path)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            file.save(file_path)
            uploaded_files.append((file_path, relative_path))
        
        # Analyze each file
        results = []
        total_files = len(uploaded_files)
        
        for idx, (file_path, relative_path) in enumerate(uploaded_files, 1):
            result = analyze_single_file(file_path, project_root)
            result['file'] = relative_path
            result['index'] = idx
            result['total'] = total_files
            results.append(result)
        
        # Count successes and failures
        success_count = sum(1 for r in results if r['success'])
        fail_count = total_files - success_count
        
        return jsonify({
            'success': True,
            'total_files': total_files,
            'success_count': success_count,
            'fail_count': fail_count,
            'results': results
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        # Clean up temp directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except:
                pass


if __name__ == '__main__':
    print("Starting CirVerify Web UI...")
    print("Access the application at: http://127.0.0.1:5000")
    app.run(debug=True, host='127.0.0.1', port=5000)

