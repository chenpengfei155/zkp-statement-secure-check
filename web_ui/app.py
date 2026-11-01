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
from pathlib import Path
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import RequestEntityTooLarge

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from cirverify.source_report import (
    build_source_report, incomplete_source_report, summarize_source_analysis,
)
from cirverify.r1cs import R1CSError
if __package__:
    from .r1cs_store import R1CSStore
    from .picus import PicusEngine, empty_report
    from .source_analysis import analyze_source
else:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from r1cs_store import R1CSStore
    from web_ui.picus import PicusEngine, empty_report
    from web_ui.source_analysis import analyze_source

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size for folder uploads
app.config['SOURCE_TIMEOUT_SECONDS'] = os.environ.get('CIRVERIFY_SOURCE_TIMEOUT_SECONDS', '150')

# Global queue for progress updates and thread management
progress_queues = {}
analysis_threads = {}
stop_flags = {}
analysis_finished = {}
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

R1CS_EXAMPLES = {
    'Unused Input (R1CS)': 'demo.r1cs',
    '2-Bit XOR (R1CS)': 'demo1.r1cs',
}


@app.route('/design-options')
def design_options():
    return render_template('design_options.html')

@app.route('/')
def index():
    """Render the main page."""
    examples = {**EXAMPLES, **{
        label: {'kind': 'r1cs', 'filename': filename}
        for label, filename in R1CS_EXAMPLES.items()
    }}
    return render_template('index.html', examples=examples)


@app.route('/examples/r1cs/<filename>', methods=['POST'])
def open_r1cs_example(filename):
    """Give each opened example the same isolated lifecycle as an upload."""
    if filename not in R1CS_EXAMPLES.values():
        return jsonify({'error': 'Unknown R1CS example.'}), 404
    try:
        path = Path(__file__).resolve().parent.parent / filename
        with path.open('rb') as stream:
            return jsonify(r1cs_store.add(FileStorage(stream=stream, filename=filename)))
    except (OSError, R1CSError):
        app.logger.exception('Could not open R1CS example %s', filename)
        return jsonify({'error': f'Could not open the bundled example {filename}.'}), 500


def source_paths(paths):
    """Validate upload names and remove a shared browser folder prefix."""
    normalized = []
    seen = set()
    for path in paths:
        if not isinstance(path, str):
            raise ValueError('Source paths must be strings.')
        path = path.replace('\\', '/')
        parts = path.split('/')
        if (any(part in ('', '.', '..') for part in parts) or ':' in path or '\x00' in path
                or not path.lower().endswith('.circom')):
            raise ValueError(f'Invalid source file path: {path}')
        if path.casefold() in seen:
            raise ValueError(f'Duplicate source file path: {path}')
        seen.add(path.casefold())
        normalized.append(parts)
    shared_root = (normalized and all(len(parts) > 1 for parts in normalized)
                   and len({parts[0] for parts in normalized}) == 1)
    return ['/'.join(parts[1:] if shared_root else parts) for parts in normalized]


@app.route('/analyze', methods=['POST'])
def analyze():
    """Analyze the provided Circom code with progress streaming."""
    import uuid
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('code'), str) or not data['code'].strip():
        return jsonify({'error': 'Please provide Circom source code.'}), 400
    code = data['code']
    filename = data.get('filename') if isinstance(data.get('filename'), str) else None
    project_files = data.get('files')
    project_paths = None
    if project_files is not None:
        try:
            if (not isinstance(project_files, list) or not project_files
                    or any(not isinstance(item, dict) or not isinstance(item.get('code'), str)
                           for item in project_files)):
                raise ValueError('Provide project files with a path and source code.')
            project_paths = source_paths([item.get('path') for item in project_files])
            if filename not in [item['path'] for item in project_files]:
                raise ValueError('The selected entry must be included in the project files.')
        except ValueError as error:
            return jsonify({'error': str(error)}), 400
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
                report = incomplete_source_report('cancelled', filename=filename)
                progress_queue.put(('complete', {'summary': summarize_source_analysis(report), 'report': report}))
                return
            
            # Check if code contains include statements with relative paths
            include_pattern = r'include\s+["\']([^"\']+)["\']'
            includes = re.findall(include_pattern, code)
            has_relative_includes = any(
                not os.path.isabs(inc) and ('../' in inc or './' in inc or not inc.startswith('/'))
                for inc in includes
            )
            
            # If there are relative includes, create temp file in benchmarks directory
            if project_files is not None:
                benchmarks_dir = Path(__file__).resolve().parent.parent / 'benchmarks'
                temp_dir = tempfile.mkdtemp(prefix='cirverify_', dir=str(benchmarks_dir))
                for item, relative_path in zip(project_files, project_paths):
                    path = Path(temp_dir) / relative_path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(code if item['path'] == filename else item['code'], encoding='utf-8')
                    if item['path'] == filename:
                        temp_path = str(path)
            elif has_relative_includes:
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
                report = incomplete_source_report('cancelled', filename=filename)
                progress_queue.put(('complete', {'summary': summarize_source_analysis(report), 'report': report}))
                return
            
            source_report = analyze_source(
                temp_path, filename=filename,
                timeout_seconds=app.config['SOURCE_TIMEOUT_SECONDS'],
                cancelled=stop_flags[session_id].is_set,
            )
            progress_queue.put(('complete', {
                'summary': summarize_source_analysis(source_report),
                'report': source_report,
            }))
        except Exception as error:
            app.logger.exception('Could not run source analysis')
            source_report = incomplete_source_report(
                'analysis_error', filename=filename, diagnostics=[str(error)])
            progress_queue.put(('complete', {
                'summary': summarize_source_analysis(source_report),
                'report': source_report,
            }))
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
                    if isinstance(content, dict) and 'report' in content:
                        payload = {
                            'type': 'complete',
                            'result': content.get('summary', ''),
                            'report': content['report'],
                        }
                    else:
                        payload = {'type': 'complete', 'result': content}
                    yield f"data: {json.dumps(payload)}\n\n"
                elif msg_type == 'output':
                    # Parse progress bar information
                    progress_data = parse_progress(content)
                    yield f"data: {json.dumps({'type': 'progress', 'data': progress_data, 'raw': content})}\n\n"
                elif msg_type == 'progress':
                    yield f"data: {json.dumps({'type': 'progress', 'data': content})}\n\n"
        finally:
            if not terminal_consumed:
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


def analyze_single_file(file_path, project_root, filename=None):
    """Return a report for this file, including incomplete analysis outcomes."""
    filename = filename or os.path.basename(file_path)
    try:
        report = analyze_source(file_path, filename=filename,
                                timeout_seconds=app.config['SOURCE_TIMEOUT_SECONDS'])
    except Exception as error:
        app.logger.exception('Could not analyze source file %s', filename)
        report = incomplete_source_report(
            'analysis_error', filename=filename, diagnostics=[str(error)])
    completed = report.get('status') == 'completed'
    return {
        'success': completed,
        'status': report.get('status', 'incomplete'),
        'warning_count': report['warning_count'],
        'result': summarize_source_analysis(report),
        'report': report,
        **({} if completed else {'error': report['message']}),
    }


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
        
        try:
            normalized_paths = source_paths([path for _, path in file_data])
        except ValueError as error:
            return jsonify({'error': str(error)}), 400

        # Save all uploaded files preserving dependency paths.
        uploaded_files = []
        for (file, _), relative_path in zip(file_data, normalized_paths):
            file_path = os.path.join(temp_dir, relative_path)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            file.save(file_path)
            uploaded_files.append((file_path, relative_path))
        
        # Analyze each file
        results = []
        total_files = len(uploaded_files)
        
        for idx, (file_path, relative_path) in enumerate(uploaded_files, 1):
            result = analyze_single_file(file_path, project_root, filename=relative_path)
            result['file'] = relative_path
            result['index'] = idx
            result['total'] = total_files
            results.append(result)
        
        # Count successes and failures
        success_count = sum(1 for r in results if r['success'])
        fail_count = total_files - success_count
        skipped_count = sum(r['status'] == 'not_applicable' for r in results)
        
        return jsonify({
            'success': True,
            'total_files': total_files,
            'success_count': success_count,
            'fail_count': fail_count,
            'incomplete_count': fail_count - skipped_count,
            'skipped_count': skipped_count,
            'results': results
        })
        
    except RequestEntityTooLarge:
        raise
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


