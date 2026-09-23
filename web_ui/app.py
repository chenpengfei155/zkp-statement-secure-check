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
from werkzeug.exceptions import RequestEntityTooLarge

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from cirverify.core import detect, print_reports
from cirverify.r1cs import R1CSError
if __package__:
    from .r1cs_store import R1CSStore
    from .picus import PicusEngine, empty_report
else:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from r1cs_store import R1CSStore
    from web_ui.picus import PicusEngine, empty_report

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size for folder uploads

# Global queue for progress updates and thread management
progress_queues = {}
analysis_threads = {}
stop_flags = {}
analysis_finished = {}
analysis_lock = threading.Lock()
r1cs_analysis_lock = threading.Lock()
r1cs_store = R1CSStore()
picus_engine = PicusEngine()
r1cs_sessions = {}
maintenance_lock = threading.Lock()
maintenance_started = False


def expire_resources():
    r1cs_store.expire()
    now = time.monotonic()
    for session_id, finished in list(analysis_finished.items()):
        if now - finished >= 30 * 60:
            progress_queues.pop(session_id, None)
            analysis_finished.pop(session_id, None)


@app.before_request
def start_maintenance():
    """Start lazily so importing the app does not create a background worker."""
    global maintenance_started
    with maintenance_lock:
        if not maintenance_started:
            def maintain():
                while True:
                    time.sleep(60)
                    expire_resources()
            threading.Thread(target=maintain, daemon=True).start()
            maintenance_started = True


@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(error):
    return jsonify({'error': 'Upload exceeds the 100 MiB request limit (including form data).'}), 413

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
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('code'), str) or not data['code'].strip():
        return jsonify({'error': 'Please provide Circom source code.'}), 400
    code = data['code'].strip()
    session_id = str(uuid.uuid4())
    progress_queue = queue.Queue()
    progress_queues[session_id] = progress_queue
    stop_flags[session_id] = threading.Event()
    
    def run_analysis():
        temp_dir = None
        temp_path = None
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
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.circom', delete=False) as f:
                    f.write(code)
                    temp_path = f.name
            
            # Check if stopped before analysis
            if stop_flags.get(session_id, threading.Event()).is_set():
                progress_queue.put(('cancelled', 'Analysis cancelled by user'))
                return
            
            # stdout capture is process-wide, so source analyses must not overlap.
            with analysis_lock, ProgressCapture(progress_queue) as capture:
                if stop_flags[session_id].is_set():
                    progress_queue.put(('cancelled', 'Analysis cancelled by user'))
                    return
                graphs, reports = detect(temp_path)
                if stop_flags[session_id].is_set():
                    progress_queue.put(('cancelled', 'Analysis cancelled by user'))
                    return
                if not graphs or not reports:
                    progress_queue.put(('error', 'Analysis failed. Check if the Circom code is valid.'))
                    return
                print_reports(graphs, reports)
                progress_queue.put(('complete', capture.getvalue()))
        except Exception as e:
            if not stop_flags.get(session_id, threading.Event()).is_set():
                progress_queue.put(('error', str(e)))
        finally:
            # Also clean up if cancelled between file creation and detection.
            try:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except OSError:
                app.logger.exception('Could not clean up a source analysis temporary file')
            # Keep results available for clients that connect after a fast analysis.
            if session_id in progress_queues:
                analysis_finished[session_id] = time.monotonic()
            progress_queue.put(('done', None))
            if session_id in stop_flags:
                del stop_flags[session_id]
            if session_id in analysis_threads:
                del analysis_threads[session_id]
    
    # Run analysis in background thread
    thread = threading.Thread(target=run_analysis)
    thread.daemon = True
    analysis_threads[session_id] = thread
    thread.start()
    
    return jsonify({'session_id': session_id})


@app.route('/stop/<session_id>', methods=['POST'])
def stop_analysis(session_id):
    """Stop a running analysis."""
    flag = stop_flags.get(session_id)
    if flag:
        flag.set()
        messages = progress_queues.get(session_id)
        if messages is not None and session_id not in r1cs_sessions:
            messages.put(('cancelled', 'Analysis cancelled by user'))
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
        terminal_consumed = False
        try:
            while True:
                try:
                    msg_type, content = q.get(timeout=30)
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    continue
                if msg_type == 'done':
                    terminal_consumed = True
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                elif msg_type == 'error':
                    terminal_consumed = True
                    yield f"data: {json.dumps({'type': 'error', 'message': content})}\n\n"
                elif msg_type == 'cancelled':
                    terminal_consumed = True
                    yield f"data: {json.dumps({'type': 'cancelled', 'message': content})}\n\n"
                elif msg_type == 'complete':
                    terminal_consumed = True
                    yield f"data: {json.dumps({'type': 'complete', 'result': content})}\n\n"
                elif msg_type == 'output':
                    # Parse progress bar information
                    progress_data = parse_progress(content)
                    yield f"data: {json.dumps({'type': 'progress', 'data': progress_data, 'raw': content})}\n\n"
                elif msg_type == 'progress':
                    yield f"data: {json.dumps({'type': 'progress', 'data': content})}\n\n"
        finally:
            if not terminal_consumed and session_id in r1cs_sessions:
                flag = stop_flags.get(session_id)
                if flag:
                    flag.set()
            if terminal_consumed:
                progress_queues.pop(session_id, None)
                analysis_finished.pop(session_id, None)
    
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
        
        suffix = Path(file.filename).suffix.lower()
        if suffix == '.r1cs':
            return jsonify(r1cs_store.add(file))
        if suffix != '.circom':
            return jsonify({'error': 'Choose a .circom or .r1cs file. Symbol (.sym) files are not supported.'}), 400
        
        # Read file content
        code = file.read().decode('utf-8-sig')
        if '\x00' in code:
            return jsonify({'error': 'Circom source must be UTF-8 text, not a binary file.'}), 400
        return jsonify({'kind': 'circom', 'code': code})
        
    except RequestEntityTooLarge:
        raise
    except UnicodeDecodeError:
        return jsonify({'error': 'Circom source must be a UTF-8 text file.'}), 400
    except R1CSError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        app.logger.exception('Upload failed')
        return jsonify({'error': 'Could not read the uploaded file.'}), 500


@app.route('/r1cs/<file_id>/constraints')
def r1cs_constraints(file_id):
    try:
        offset = int(request.args.get('offset', '0'))
        limit = int(request.args.get('limit', '50'))
        if offset < 0 or not 1 <= limit <= 50:
            raise ValueError()
    except ValueError:
        return jsonify({'error': 'Offset must be nonnegative and limit must be between 1 and 50.'}), 400
    try:
        return jsonify(r1cs_store.page(file_id, offset, limit))
    except KeyError:
        return jsonify({'error': 'This R1CS file has expired or was closed. Please upload it again.'}), 404


@app.route('/r1cs/<file_id>', methods=['DELETE'])
def delete_r1cs(file_id):
    with r1cs_store.lock:
        for session_id, owned_id in list(r1cs_sessions.items()):
            flag = stop_flags.get(session_id)
            if owned_id == file_id and flag:
                flag.set()
        r1cs_store.delete(file_id)
    return jsonify({'success': True})


@app.route('/r1cs/engine')
def r1cs_engine_status():
    return jsonify(picus_engine.status())


@app.route('/r1cs/<file_id>/analyze', methods=['POST'])
def analyze_r1cs_upload(file_id):
    """Run Picus on a leased binary; source analysis remains a separate path."""
    import uuid
    # One R1CS worker at a time bounds memory/CPU across browser tabs and clients.
    if not r1cs_analysis_lock.acquire(blocking=False):
        return jsonify({'error': 'An R1CS analysis is already running. Please wait or stop it.'}), 409
    store = r1cs_store
    try:
        reader = store.acquire(file_id)
    except KeyError:
        r1cs_analysis_lock.release()
        return jsonify({'error': 'This R1CS file has expired or was closed. Please upload it again.'}), 404
    except Exception:
        r1cs_analysis_lock.release()
        raise
    metadata = reader.metadata
    if 1 + metadata['public_outputs'] + metadata['public_inputs'] + metadata['private_inputs'] > metadata['wires']:
        store.release(file_id)
        r1cs_analysis_lock.release()
        return jsonify({'error': 'Picus cannot map this file’s inputs to wires. Recompile with --O0 and upload again.'}), 422
    if metadata['public_outputs']:
        try:
            engine_status = picus_engine.status()
        except Exception:
            store.release(file_id)
            r1cs_analysis_lock.release()
            raise
        if not engine_status['ready']:
            store.release(file_id)
            r1cs_analysis_lock.release()
            return jsonify({'error': engine_status['reason']}), 503
    session_id = str(uuid.uuid4())
    messages = queue.Queue()
    cancelled = threading.Event()
    with store.lock:
        # A tab may close while the environment check is still running.
        if file_id not in store.entries:
            store.release(file_id)
            r1cs_analysis_lock.release()
            return jsonify({'error': 'This R1CS file was closed. Please upload it again.'}), 404
        progress_queues[session_id] = messages
        stop_flags[session_id] = cancelled
        r1cs_sessions[session_id] = file_id

    def run():
        report = None
        try:
            if not metadata['public_outputs']:
                report = empty_report('not_applicable', 'no_outputs')
            else:
                report = picus_engine.analyze(reader, cancelled=cancelled.is_set,
                                              progress=lambda data: messages.put(('progress', data)))
        except Exception as error:
            app.logger.exception('R1CS analysis failed')
            report = empty_report('error', 'runtime_error')
            report['logs'] = [str(error)]
        finally:
            try:
                store.release(file_id)
            finally:
                r1cs_analysis_lock.release()
                if cancelled.is_set():
                    report = report or empty_report('cancelled', 'cancelled')
                    report['verdict'], report['reason'] = 'cancelled', 'cancelled'
                messages.put(('complete', report))
                if session_id in progress_queues:
                    analysis_finished[session_id] = time.monotonic()
                messages.put(('done', None))
                stop_flags.pop(session_id, None)
                analysis_threads.pop(session_id, None)
                r1cs_sessions.pop(session_id, None)

    thread = threading.Thread(target=run, daemon=True)
    analysis_threads[session_id] = thread
    try:
        thread.start()
    except Exception:
        store.release(file_id)
        r1cs_analysis_lock.release()
        progress_queues.pop(session_id, None)
        stop_flags.pop(session_id, None)
        analysis_threads.pop(session_id, None)
        r1cs_sessions.pop(session_id, None)
        raise
    return jsonify({'session_id': session_id})


def analyze_single_file(file_path, project_root):
    """Analyze a single Circom file and return the result."""
    try:
        with analysis_lock:
            graphs, reports = detect(file_path)
            if not graphs or not reports:
                return {'success': False, 'error': 'Analysis failed. Check if the Circom code is valid.'}
            with ProgressCapture(queue.Queue()) as capture:
                print_reports(graphs, reports)
                output = capture.getvalue()
        
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

