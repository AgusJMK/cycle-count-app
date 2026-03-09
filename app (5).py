from flask import Flask, render_template, request, redirect, url_for, session, flash
import pandas as pd
from datetime import datetime
import os
import sqlite3
from pathlib import Path

app = Flask(__name__)
app.secret_key = 'cycle-count-secret-key-2026'

# ============================================
# DATABASE SETUP FOR RAILWAY
# ============================================
# PENTING: Ini folder khusus buat nyimpen database biar ga ilang di Railway
DATA_DIR = Path("/app/data")  # Buat production di Railway
DATA_DIR.mkdir(exist_ok=True)  # Auto bikin folder kalo belum ada

# Kalo lagi develop di lokal, pake folder lokal aja
if not os.path.exists("/app/data"):
    DATA_DIR = Path(".")  # Pake folder sekarang kalo di lokal

DB_PATH = DATA_DIR / "cycle_count.db"

def get_db():
    """Buat koneksi ke database SQLite"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row  # Biar hasil query bisa diakses kayak dictionary
    return conn

# ============================================
# DATABASE INIT
# ============================================
def init_db():
    """Bikin tabel-tabel kalo belum ada"""
    conn = get_db()
    c = conn.cursor()
    
    # Tabel users
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT DEFAULT 'admin',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tabel cycle sessions
    c.execute('''
        CREATE TABLE IF NOT EXISTS cycle_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            cycle_name TEXT,
            start_time DATETIME,
            end_time DATETIME,
            status TEXT DEFAULT 'active',
            total_stock INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Tabel master stock
    c.execute('''
        CREATE TABLE IF NOT EXISTS master_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER,
            lot_number TEXT NOT NULL,
            product_name TEXT,
            location TEXT,
            quantity INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cycle_id) REFERENCES cycle_sessions(id)
        )
    ''')
    
    # Tabel scan results
    c.execute('''
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            lot_number TEXT NOT NULL,
            location_scan TEXT,
            scan_time DATETIME,
            status TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES cycle_sessions(id)
        )
    ''')
    
    # Insert user default kalo belum ada
    c.execute("SELECT * FROM users WHERE username = 'admin'")
    if not c.fetchone():
        c.execute("INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)",
                  ('admin', 'admin', 'Administrator', 'admin'))
    
    # Insert user koordinator kalo belum ada
    c.execute("SELECT * FROM users WHERE username = 'koordinator'")
    if not c.fetchone():
        c.execute("INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)",
                  ('koordinator', 'koord123', 'Koordinator Gudang', 'koordinator'))
    
    conn.commit()
    conn.close()
    
    print(f"Database initialized at: {DB_PATH}")

# Panggil init_db pas aplikasi start
init_db()

# ============================================
# HELPER FUNCTIONS
# ============================================
def get_now():
    return datetime.now()

def normalize_location(loc):
    """Ambil bagian terakhir setelah PRGS"""
    if not loc:
        return ''
    loc = str(loc)
    if 'PRGS ' in loc:
        parts = loc.split('PRGS ')
        return parts[-1].strip()
    return loc.strip()

# ============================================
# LOGIN ROUTES
# ============================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', 
                          (username, password)).fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['full_name'] = user['full_name']
            session['role'] = user['role']
            session['logged_in'] = True
            flash('Login berhasil!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Username atau password salah!', 'danger')
    
    return render_template('login.html', now=get_now())

@app.route('/logout')
def logout():
    session.clear()
    flash('Anda telah logout', 'info')
    return redirect(url_for('login'))

# ============================================
# MIDDLEWARE
# ============================================
@app.before_request
def require_login():
    public_routes = ['login', 'static']
    if request.endpoint in public_routes:
        return
    if not session.get('logged_in'):
        return redirect(url_for('login'))

# ============================================
# MAIN ROUTES
# ============================================
@app.route('/')
def index():
    conn = get_db()
    
    active_cycle = None
    if 'active_cycle' in session:
        active_cycle = conn.execute('''
            SELECT * FROM cycle_sessions WHERE id = ? AND status = 'active'
        ''', (session['active_cycle']['id'],)).fetchone()
    
    extra_items = []
    mismatch_items = []
    missing_items = []
    extra_count = 0
    mismatch_count = 0
    missing_count = 0
    
    if active_cycle:
        # Extra items
        extra_items = conn.execute('''
            SELECT * FROM scan_results 
            WHERE session_id = ? AND status = 'not_found'
            ORDER BY scan_time DESC
            LIMIT 50
        ''', (active_cycle['id'],)).fetchall()
        extra_count = conn.execute('''
            SELECT COUNT(*) as count FROM scan_results 
            WHERE session_id = ? AND status = 'not_found'
        ''', (active_cycle['id'],)).fetchone()['count']
        
        # Mismatch items
        mismatch_items = conn.execute('''
            SELECT sr.*, ms.product_name, ms.location as system_location 
            FROM scan_results sr
            JOIN master_stock ms ON sr.lot_number = ms.lot_number AND ms.cycle_id = sr.session_id
            WHERE sr.session_id = ? AND sr.status = 'found_mismatch'
            ORDER BY sr.scan_time DESC
            LIMIT 50
        ''', (active_cycle['id'],)).fetchall()
        mismatch_count = conn.execute('''
            SELECT COUNT(*) as count FROM scan_results 
            WHERE session_id = ? AND status = 'found_mismatch'
        ''', (active_cycle['id'],)).fetchone()['count']
        
        # Missing items
        missing_items = conn.execute('''
            SELECT ms.* FROM master_stock ms
            LEFT JOIN scan_results sr ON ms.lot_number = sr.lot_number AND sr.session_id = ms.cycle_id
            WHERE ms.cycle_id = ? AND sr.id IS NULL
            ORDER BY ms.lot_number
            LIMIT 50
        ''', (active_cycle['id'],)).fetchall()
        missing_count = conn.execute('''
            SELECT COUNT(*) as count FROM master_stock ms
            LEFT JOIN scan_results sr ON ms.lot_number = sr.lot_number AND sr.session_id = ms.cycle_id
            WHERE ms.cycle_id = ? AND sr.id IS NULL
        ''', (active_cycle['id'],)).fetchone()['count']
    
    conn.close()
    
    return render_template('index.html', 
                         now=get_now(), 
                         user=session.get('full_name'),
                         role=session.get('role'),
                         active_cycle=active_cycle,
                         extra_items=extra_items,
                         mismatch_items=mismatch_items,
                         missing_items=missing_items,
                         extra_count=extra_count,
                         mismatch_count=mismatch_count,
                         missing_count=missing_count)

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    # Cek role: hanya koordinator yang bisa upload
    if session.get('role') != 'koordinator':
        flash('Hanya Koordinator yang bisa upload stock!', 'danger')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        cycle_name = request.form.get('cycle_name')
        file = request.files.get('file')
        
        if not file:
            return render_template('upload.html', now=get_now(), error='File tidak boleh kosong')
        
        if not file.filename.endswith(('.xlsx', '.xls')):
            return render_template('upload.html', now=get_now(), error='File harus Excel')
        
        try:
            df = pd.read_excel(file)
            
            required_cols = ['Lot/Serial Number', 'Product/Name', 'Location']
            missing = [col for col in required_cols if col not in df.columns]
            if missing:
                return render_template('upload.html', now=get_now(), 
                                     error=f'Kolom missing: {missing}')
            
            conn = get_db()
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO cycle_sessions (user_id, cycle_name, status, total_stock)
                VALUES (?, ?, ?, ?)
            ''', (session['user_id'], cycle_name, 'active', len(df)))
            cycle_id = cur.lastrowid
            
            for _, row in df.iterrows():
                lot = str(row.get('Lot/Serial Number', '')).strip()
                if lot and lot.lower() != 'nan':
                    cur.execute('''
                        INSERT INTO master_stock (cycle_id, lot_number, product_name, location, quantity)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (
                        cycle_id,
                        lot,
                        str(row.get('Product/Name', '')),
                        str(row.get('Location', '')),
                        int(row.get('Available Quantity', 0)) if pd.notna(row.get('Available Quantity', 0)) else 0
                    ))
            
            conn.commit()
            conn.close()
            
            session['active_cycle'] = {
                'id': cycle_id,
                'name': cycle_name,
                'total_stock': len(df)
            }
            session['last_upload'] = {
                'cycle_name': cycle_name,
                'filename': file.filename,
                'total_rows': len(df),
                'timestamp': get_now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            return redirect(url_for('upload_success', rows=len(df), name=cycle_name))
            
        except Exception as e:
            return render_template('upload.html', now=get_now(), error=f'Error: {str(e)}')
    
    return render_template('upload.html', now=get_now())

@app.route('/upload/success')
def upload_success():
    rows = request.args.get('rows', 0)
    name = request.args.get('name', 'Cycle Count')
    return render_template('upload_success.html', now=get_now(), rows=rows, name=name)

@app.route('/scan')
def scan():
    # Cek role: hanya admin yang bisa scan
    if session.get('role') != 'admin':
        flash('Hanya Admin yang bisa melakukan scan!', 'danger')
        return redirect(url_for('index'))
    
    active_cycle = session.get('active_cycle')
    if not active_cycle:
        flash('Silakan upload stock terlebih dahulu!', 'warning')
        return redirect(url_for('upload'))
    
    return render_template('scan.html', 
                         now=get_now(), 
                         user=session.get('full_name'),
                         cycle=active_cycle)

@app.route('/report')
def report():
    conn = get_db()
    
    cycles = conn.execute('''
        SELECT cs.*, u.full_name 
        FROM cycle_sessions cs
        JOIN users u ON cs.user_id = u.id
        ORDER BY cs.created_at DESC
    ''').fetchall()
    
    active_cycle = session.get('active_cycle')
    total = scanned = found = not_found = mismatch_count = 0
    extra_items = []
    mismatch_items = []
    missing_items = []
    
    if active_cycle:
        total = conn.execute('SELECT COUNT(*) as count FROM master_stock WHERE cycle_id = ?', 
                            (active_cycle['id'],)).fetchone()['count']
        scanned = conn.execute('SELECT COUNT(*) as count FROM scan_results WHERE session_id = ?', 
                              (active_cycle['id'],)).fetchone()['count']
        found = conn.execute('''
            SELECT COUNT(*) as count FROM scan_results 
            WHERE session_id = ? AND status = 'found_match'
        ''', (active_cycle['id'],)).fetchone()['count']
        not_found = conn.execute('''
            SELECT COUNT(*) as count FROM scan_results 
            WHERE session_id = ? AND status = 'not_found'
        ''', (active_cycle['id'],)).fetchone()['count']
        mismatch_count = conn.execute('''
            SELECT COUNT(*) as count FROM scan_results 
            WHERE session_id = ? AND status = 'found_mismatch'
        ''', (active_cycle['id'],)).fetchone()['count']
        
        extra_items = conn.execute('''
            SELECT * FROM scan_results 
            WHERE session_id = ? AND status = 'not_found'
            ORDER BY scan_time DESC
        ''', (active_cycle['id'],)).fetchall()
        
        mismatch_items = conn.execute('''
            SELECT sr.*, ms.product_name, ms.location as system_location 
            FROM scan_results sr
            JOIN master_stock ms ON sr.lot_number = ms.lot_number AND ms.cycle_id = sr.session_id
            WHERE sr.session_id = ? AND sr.status = 'found_mismatch'
            ORDER BY sr.scan_time DESC
        ''', (active_cycle['id'],)).fetchall()
        
        missing_items = conn.execute('''
            SELECT ms.* FROM master_stock ms
            LEFT JOIN scan_results sr ON ms.lot_number = sr.lot_number AND sr.session_id = ms.cycle_id
            WHERE ms.cycle_id = ? AND sr.id IS NULL
            ORDER BY ms.lot_number
            LIMIT 100
        ''', (active_cycle['id'],)).fetchall()
    
    conn.close()
    
    return render_template('report.html',
                         now=get_now(),
                         cycles=cycles,
                         active_cycle=active_cycle,
                         total=total,
                         scanned=scanned,
                         found=found,
                         not_found=not_found,
                         mismatch_count=mismatch_count,
                         extra_items=extra_items,
                         mismatch_items=mismatch_items,
                         missing_items=missing_items,
                         normalize=normalize_location)

# ============================================
# API ROUTES
# ============================================
@app.route('/api/start-scan', methods=['POST'])
def api_start_scan():
    if 'active_cycle' not in session:
        return {'status': 'error', 'message': 'No active cycle'}, 400
    
    conn = get_db()
    conn.execute('''
        UPDATE cycle_sessions 
        SET start_time = ? 
        WHERE id = ? AND status = 'active'
    ''', (get_now().strftime('%Y-%m-%d %H:%M:%S'), session['active_cycle']['id']))
    conn.commit()
    conn.close()
    
    return {'status': 'success', 'time': get_now().strftime('%H:%M:%S')}

@app.route('/api/end-scan', methods=['POST'])
def api_end_scan():
    if 'active_cycle' not in session:
        return {'status': 'error', 'message': 'No active cycle'}, 400
    
    conn = get_db()
    conn.execute('''
        UPDATE cycle_sessions 
        SET end_time = ?, status = 'completed'
        WHERE id = ? AND status = 'active'
    ''', (get_now().strftime('%Y-%m-%d %H:%M:%S'), session['active_cycle']['id']))
    conn.commit()
    conn.close()
    
    session.pop('active_cycle', None)
    
    return {'status': 'success', 'time': get_now().strftime('%H:%M:%S')}

@app.route('/api/scan', methods=['POST'])
def api_scan():
    data = request.get_json()
    barcode = data.get('barcode')
    location_scan = data.get('location')
    
    if not barcode:
        return {'status': 'error', 'message': 'Barcode wajib diisi'}, 400
    
    if not location_scan:
        return {'status': 'error', 'message': 'Lokasi wajib diisi'}, 400
    
    if 'active_cycle' not in session:
        return {'status': 'error', 'message': 'No active cycle'}, 400
    
    cycle_id = session['active_cycle']['id']
    
    conn = get_db()
    
    stock = conn.execute('''
        SELECT * FROM master_stock 
        WHERE cycle_id = ? AND lot_number = ?
    ''', (cycle_id, barcode)).fetchone()
    
    if not stock:
        status = 'not_found'
        response = {
            'status': 'not_found',
            'barcode': barcode,
            'message': 'Barcode tidak ditemukan di database'
        }
    else:
        system_location = normalize_location(stock['location'])
        scan_location = normalize_location(location_scan)
        
        if system_location == scan_location:
            status = 'found_match'
            response = {
                'status': 'found_match',
                'barcode': barcode,
                'product': stock['product_name'],
                'location': system_location,
                'qty': stock['quantity'],
                'message': 'Lokasi sesuai'
            }
        else:
            status = 'found_mismatch'
            response = {
                'status': 'found_mismatch',
                'barcode': barcode,
                'product': stock['product_name'],
                'location_scan': scan_location,
                'location_system': system_location,
                'qty': stock['quantity'],
                'message': f'Lokasi tidak sesuai! Seharusnya: {system_location}'
            }
    
    conn.execute('''
        INSERT INTO scan_results (session_id, lot_number, location_scan, scan_time, status)
        VALUES (?, ?, ?, ?, ?)
    ''', (cycle_id, barcode, location_scan, get_now().strftime('%Y-%m-%d %H:%M:%S'), status))
    
    conn.commit()
    conn.close()
    
    return response

@app.route('/api/recent-scans', methods=['GET'])
def api_recent_scans():
    if 'active_cycle' not in session:
        return {'scans': []}
    
    cycle_id = session['active_cycle']['id']
    
    conn = get_db()
    scans = conn.execute('''
        SELECT * FROM scan_results 
        WHERE session_id = ? 
        ORDER BY scan_time DESC 
        LIMIT 10
    ''', (cycle_id,)).fetchall()
    conn.close()
    
    result = []
    for scan in scans:
        status_text = {
            'found_match': 'SESUAI',
            'found_mismatch': 'LOKASI BERBEDA',
            'not_found': 'TIDAK DITEMUKAN'
        }.get(scan['status'], scan['status'])
        
        result.append({
            'time': scan['scan_time'][11:19] if scan['scan_time'] else '-',
            'barcode': scan['lot_number'],
            'status': scan['status'],
            'status_text': status_text
        })
    
    return {'scans': result}

@app.route('/api/scan-progress', methods=['GET'])
def api_scan_progress():
    if 'active_cycle' not in session:
        return {'total': 0, 'scanned': 0, 'found': 0, 'not_found': 0, 'mismatch': 0}
    
    cycle_id = session['active_cycle']['id']
    
    conn = get_db()
    
    total = conn.execute('SELECT COUNT(*) as count FROM master_stock WHERE cycle_id = ?', 
                        (cycle_id,)).fetchone()['count']
    scanned = conn.execute('SELECT COUNT(*) as count FROM scan_results WHERE session_id = ?', 
                          (cycle_id,)).fetchone()['count']
    found = conn.execute('''
        SELECT COUNT(*) as count FROM scan_results 
        WHERE session_id = ? AND status = 'found_match'
    ''', (cycle_id,)).fetchone()['count']
    not_found = conn.execute('''
        SELECT COUNT(*) as count FROM scan_results 
        WHERE session_id = ? AND status = 'not_found'
    ''', (cycle_id,)).fetchone()['count']
    mismatch = conn.execute('''
        SELECT COUNT(*) as count FROM scan_results 
        WHERE session_id = ? AND status = 'found_mismatch'
    ''', (cycle_id,)).fetchone()['count']
    
    conn.close()
    
    return {
        'total': total,
        'scanned': scanned,
        'found': found,
        'not_found': not_found,
        'mismatch': mismatch
    }

@app.route('/api/reset-cycle', methods=['POST'])
def api_reset_cycle():
    if 'active_cycle' not in session:
        return {'status': 'error', 'message': 'No active cycle'}, 400
    
    cycle_id = session['active_cycle']['id']
    
    conn = get_db()
    conn.execute('DELETE FROM scan_results WHERE session_id = ?', (cycle_id,))
    conn.execute('''
        UPDATE cycle_sessions 
        SET start_time = NULL, end_time = NULL 
        WHERE id = ?
    ''', (cycle_id,))
    conn.commit()
    conn.close()
    
    return {'status': 'success', 'message': 'Cycle has been reset'}

@app.route('/api/reset-database', methods=['POST'])
def api_reset_database():
    try:
        conn = get_db()
        conn.execute('DELETE FROM scan_results')
        conn.execute('DELETE FROM master_stock')
        conn.execute('DELETE FROM cycle_sessions')
        conn.commit()
        conn.close()
        
        if 'active_cycle' in session:
            session.pop('active_cycle')
        
        return {'status': 'success', 'message': 'Database berhasil direset'}
    
    except Exception as e:
        return {'status': 'error', 'message': str(e)}, 500

# ============================================
# ERROR HANDLERS
# ============================================
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html', now=get_now()), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html', now=get_now()), 500

# ============================================
# PRODUCTION CONFIGURATION
# ============================================
if __name__ != '__main__':
    # Kalo di Railway (production), pake port dari environment
    port = int(os.environ.get('PORT', 5000))
else:
    # Kalo local development
    if __name__ == '__main__':
        app.run(debug=True, host='0.0.0.0', port=5000)