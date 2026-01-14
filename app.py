"""
Real Estate Panorama Plot Marker - Flask Application
"""
import os
import sqlite3
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size
app.config['DATABASE'] = 'panorama.db'

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Ensure directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('templates', exist_ok=True)
os.makedirs('static', exist_ok=True)


def get_db():
    """Get database connection"""
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Create panoramas table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS panoramas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            is_360 INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Add is_360 column if it doesn't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE panoramas ADD COLUMN is_360 INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Create plots table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS plots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            panorama_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            area TEXT,
            price TEXT,
            status TEXT DEFAULT 'available',
            description TEXT,
            color TEXT DEFAULT 'emerald',
            points TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (panorama_id) REFERENCES panoramas(id) ON DELETE CASCADE
        )
    ''')
    
    conn.commit()
    conn.close()


def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# Initialize database on startup
init_db()


# Routes
@app.route('/')
def index():
    """Landing page with upload and history"""
    return render_template('index.html')


@app.route('/editor/<int:panorama_id>')
def editor(panorama_id):
    """Plot marker editor for a specific panorama"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM panoramas WHERE id = ?', (panorama_id,))
    panorama = cursor.fetchone()
    conn.close()
    
    if not panorama:
        return "Panorama not found", 404
    
    return render_template('editor.html', panorama=dict(panorama))


@app.route('/view/<int:panorama_id>')
def view(panorama_id):
    """View-only mode for clients - no edit controls"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM panoramas WHERE id = ?', (panorama_id,))
    panorama = cursor.fetchone()
    conn.close()
    
    if not panorama:
        return "Panorama not found", 404
    
    return render_template('viewer.html', panorama=dict(panorama))


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded files"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# API Routes
@app.route('/api/panoramas', methods=['GET'])
def get_panoramas():
    """Get all panoramas with plot counts"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT p.*, COUNT(pl.id) as plot_count 
        FROM panoramas p 
        LEFT JOIN plots pl ON p.id = pl.panorama_id 
        GROUP BY p.id 
        ORDER BY p.updated_at DESC
    ''')
    panoramas = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(panoramas)


@app.route('/api/panoramas', methods=['POST'])
def create_panorama():
    """Upload new panorama image"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    name = request.form.get('name', 'Untitled Panorama')
    is_360 = request.form.get('is_360', 'false').lower() == 'true'
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400
    
    # Generate unique filename
    original_filename = secure_filename(file.filename)
    ext = original_filename.rsplit('.', 1)[1].lower()
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secure_filename(name)}.{ext}"
    
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    # Get image dimensions
    try:
        from PIL import Image
        with Image.open(filepath) as img:
            width, height = img.size
    except:
        width, height = 0, 0
    
    # Save to database
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO panoramas (name, filename, original_filename, width, height, is_360)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (name, filename, original_filename, width, height, 1 if is_360 else 0))
    panorama_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return jsonify({
        'id': panorama_id,
        'name': name,
        'filename': filename,
        'width': width,
        'height': height,
        'is_360': is_360
    }), 201


@app.route('/api/panoramas/<int:panorama_id>', methods=['DELETE'])
def delete_panorama(panorama_id):
    """Delete a panorama and its plots"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Get filename to delete
    cursor.execute('SELECT filename FROM panoramas WHERE id = ?', (panorama_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return jsonify({'error': 'Panorama not found'}), 404
    
    # Delete file
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], row['filename'])
    if os.path.exists(filepath):
        os.remove(filepath)
    
    # Delete from database (cascade will delete plots)
    cursor.execute('DELETE FROM panoramas WHERE id = ?', (panorama_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})


@app.route('/api/panoramas/<int:panorama_id>/plots', methods=['GET'])
def get_plots(panorama_id):
    """Get all plots for a panorama"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM plots WHERE panorama_id = ? ORDER BY created_at', (panorama_id,))
    plots = []
    for row in cursor.fetchall():
        plot = dict(row)
        plot['points'] = json.loads(plot['points'])
        plots.append(plot)
    conn.close()
    return jsonify(plots)


@app.route('/api/panoramas/<int:panorama_id>/plots', methods=['POST'])
def create_plot(panorama_id):
    """Create a new plot"""
    data = request.json
    
    if not data.get('name') or not data.get('points'):
        return jsonify({'error': 'Name and points are required'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO plots (panorama_id, name, area, price, status, description, color, points)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        panorama_id,
        data['name'],
        data.get('area', ''),
        data.get('price', ''),
        data.get('status', 'available'),
        data.get('description', ''),
        data.get('color', 'emerald'),
        json.dumps(data['points'])
    ))
    
    plot_id = cursor.lastrowid
    
    # Update panorama's updated_at
    cursor.execute('UPDATE panoramas SET updated_at = CURRENT_TIMESTAMP WHERE id = ?', (panorama_id,))
    
    conn.commit()
    conn.close()
    
    return jsonify({'id': plot_id, **data}), 201


@app.route('/api/plots/<int:plot_id>', methods=['DELETE'])
def delete_plot(plot_id):
    """Delete a plot"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM plots WHERE id = ?', (plot_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/plots/<int:plot_id>', methods=['PUT'])
def update_plot(plot_id):
    """Update a plot"""
    data = request.json
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE plots SET
            name = ?,
            area = ?,
            price = ?,
            status = ?,
            description = ?,
            color = ?,
            points = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (
        data.get('name'),
        data.get('area', ''),
        data.get('price', ''),
        data.get('status', 'available'),
        data.get('description', ''),
        data.get('color', 'emerald'),
        json.dumps(data.get('points', [])),
        plot_id
    ))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(debug=debug, host='0.0.0.0', port=port)

