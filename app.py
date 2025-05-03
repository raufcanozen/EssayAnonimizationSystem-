"""
Author: Rauf Özen,Recep Birdal

Academic Paper Anonymization and Review Systems' Main File.
Main Application File (app.py)
"""
import os
import uuid
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, after_this_request
from werkzeug.utils import secure_filename
from blurring import PDFBlurAnonymizer
import hashlib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import base64

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialized Flask app
app = Flask(__name__)
app.secret_key = 'my_very_secret_key'  

#Configured the upload folder and allowed extensions.
UPLOAD_FOLDER = 'uploads'
ANONYMIZED_FOLDER = 'anonymized'
REVIEWED_FOLDER = 'reviewed'
ALLOWED_EXTENSIONS = {'pdf'}

# Here I have ensured the directories exist.
for folder in [UPLOAD_FOLDER, ANONYMIZED_FOLDER, REVIEWED_FOLDER]:
    os.makedirs(folder, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024#16MB max upload size

#Initialized the PDF anonymizer here
anonymizer = PDFBlurAnonymizer()

papers = {}  # This will hold {paper_id: {filename, email, status, keywords, reviewer, review_text, anonymous_path, original_path}}

# Some helper functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_key():
    """
    Generate a random 32-byte key for AES-256 encryption
    
    Returns:
    bytes: Random 32-byte key
    """
    return os.urandom(32)

# Global encryption key - in production this should be stored securely
ENCRYPTION_KEY = generate_key()

def encrypt_file(file_path):
    """
    Encrypt a file using AES-256
    
    Parameters:
    file_path (str): Path to the file to encrypt
    
    Returns:
    str: Path to the encrypted file
    """
    # Generate a random 16-byte IV
    iv = os.urandom(16)
    
    # Create the cipher
    cipher = Cipher(algorithms.AES(ENCRYPTION_KEY), modes.CFB(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    
    # Read the file
    with open(file_path, 'rb') as f:
        plaintext = f.read()
    
    # Encrypt the file
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    
    # Create the encrypted file path
    encrypted_path = file_path + '.enc'
    
    # Write the IV and encrypted content to the new file
    with open(encrypted_path, 'wb') as f:
        f.write(iv + ciphertext)
    
    return encrypted_path

def decrypt_file(encrypted_path, output_path=None):
    """
    Decrypt a file encrypted with AES-256
    
    Parameters:
    encrypted_path (str): Path to the encrypted file
    output_path (str, optional): Path to save the decrypted file. If None, a temporary path is generated.
    
    Returns:
    str: Path to the decrypted file
    """
    if output_path is None:
        output_path = encrypted_path.replace('.enc', '.dec')
    
    # Read the encrypted file
    with open(encrypted_path, 'rb') as f:
        data = f.read()
    
    # Extract the IV (first 16 bytes)
    iv = data[:16]
    ciphertext = data[16:]
    
    # Create the cipher
    cipher = Cipher(algorithms.AES(ENCRYPTION_KEY), modes.CFB(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    
    # Decrypt the content
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    
    # Write the decrypted content to the output file
    with open(output_path, 'wb') as f:
        f.write(plaintext)
    
    return output_path


def hash_data(data):
    """
    Hash the given data using SHA-256
    Parameters:
    data (str): Data to be hashed
    Returns:
    str: Hexadecimal digest of the hashed data
    """
    if not isinstance(data, bytes):
        data = str(data).encode('utf-8')
    return hashlib.sha256(data).hexdigest()

def generate_paper_id():
    return str(uuid.uuid4())[:8].upper()

def log_event(paper_id, event):
    if paper_id in papers:
        if 'log' not in papers[paper_id]:
            papers[paper_id]['log'] = []
        papers[paper_id]['log'].append({
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'event': event
        })

#Author routes (no login required)
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['GET', 'POST'])
def upload_paper():
    if request.method == 'POST':
        #Checked if email is provided.
        email = request.form.get('email', '').strip()
        if not email or '@' not in email:
            flash('Please provide a valid email address.')
            return redirect(request.url)
            
        #Checked if the post requested has the file part.
        if 'file' not in request.files:
            flash('No valid file part')
            return redirect(request.url)
            
        file = request.files['file']
        
        # If user does not select a file, browser submits an empty file
        if file.filename == '':
            flash('File not selected')
            return redirect(request.url)
            
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            paper_id = generate_paper_id()
            original_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{paper_id}_{filename}")
            file.save(original_path)
            encrypted_path = encrypt_file(original_path)
            
            #Stored the paper info here
            papers[paper_id] = {
                'filename': filename,
                'email': hash_data(email),
                'status': 'Uploaded',
                'original_path': original_path,
                'encrypted_path': encrypted_path,  # Şifrelenmiş dosya yolunu kaydet
                'upload_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'keywords': [],
                'reviewer': None,
                'review_text': None,
                'messages': []
            }
            log_event(paper_id, f"Paper uploaded by {email}")
            flash(f'Paper uploaded successfully! Your tracking ID is {paper_id}')
            return render_template('upload_success.html', paper_id=paper_id)
            
    return render_template('upload.html')

@app.route('/status', methods=['GET', 'POST'])
def check_status():
    if request.method == 'POST':
        paper_id = request.form.get('paper_id', '').strip().upper()
        email = request.form.get('email', '').strip()
        if paper_id in papers and papers[paper_id]['email'] == hash_data(email):
            paper = papers[paper_id]
            return render_template('status_result.html', paper=paper, paper_id=paper_id)
        else:
            flash('Invalid paper ID or email. Please try again.')
    return render_template('check_status.html')

@app.route('/message/<paper_id>', methods=['GET', 'POST'])
def send_message(paper_id):
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if paper_id in papers and message:
            papers[paper_id]['messages'].append({
                'from': 'author',
                'text': message,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            flash('Message sent to editor successfully!')
            return redirect(url_for('check_status'))
    return render_template('send_message.html', paper_id=paper_id)

#Editor Routes(NO LOGIN REQUIRED as specified in the project)
@app.route('/editor')
def editor_dashboard():
    return render_template('editor_dashboard.html', papers=papers)

@app.route('/editor/paper/<paper_id>', methods=['GET', 'POST'])
def editor_view_paper(paper_id):
    if paper_id not in papers:
        flash('Paper not found')
        return redirect(url_for('editor_dashboard'))
    if request.method == 'POST':
        #Processed the keywords here.
        keywords = request.form.get('keywords', '').strip()
        papers[paper_id]['keywords'] = [k.strip() for k in keywords.split(',') if k.strip()]
        #Processed the reviewer assignments here.
        reviewer = request.form.get('reviewer_email', '').strip()
        if reviewer:
            papers[paper_id]['reviewer'] = hash_data(reviewer)
            papers[paper_id]['status'] = 'Assigned to Reviewer'
            log_event(paper_id, f"Assigned to reviewer: {reviewer}")
        #Processed the messagign to the author here.
        message = request.form.get('message', '').strip()
        if message:
            papers[paper_id]['messages'].append({
                'from': 'editor',
                'text': message,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
        #Anonymized the papers if requested.
        # Anonymize blok içinde
    if 'anonymize' in request.form:
        try:
            original_path = papers[paper_id]['original_path']
            anonymous_filename = f"anon_{paper_id}_{os.path.basename(original_path)}"
            anonymous_path = os.path.join(ANONYMIZED_FOLDER, anonymous_filename)
            
            # Önce orijinal dosyayı deşifre etmemiz gerekebilir
            if 'encrypted_path' in papers[paper_id]:
                temp_original = os.path.join(UPLOAD_FOLDER, f"temp_original_{paper_id}")
                decrypt_file(papers[paper_id]['encrypted_path'], temp_original)
                success = anonymizer.anonymize_pdf(temp_original, anonymous_path)
                os.remove(temp_original)  # Geçici dosyayı temizle
            else:
                success = anonymizer.anonymize_pdf(original_path, anonymous_path)
                
            if success:
                # Anonim dosyayı şifrele
                encrypted_anon_path = encrypt_file(anonymous_path)
                # Orijinal anonim dosyayı sil
                os.remove(anonymous_path)
                
                papers[paper_id]['anonymous_path'] = encrypted_anon_path
                papers[paper_id]['status'] = 'Anonymized'
                log_event(paper_id, "Paper anonymized")
                
                # Orijinal dosyayı SİLMİYORUZ - bu satırları kaldırıyoruz
                # if os.path.exists(original_path):
                #     os.remove(original_path)
                
                flash('Paper anonymized successfully')
            else:
                flash('Error anonymizing paper')
        except Exception as e:
            flash(f'Error: {str(e)}')
        
        flash('Paper updated successfully')
        return redirect(url_for('editor_dashboard'))
    return render_template('editor_view_paper.html', paper=papers[paper_id], paper_id=paper_id)


@app.route('/editor/download/<paper_id>/<type>')
def editor_download(paper_id, type):
    if paper_id not in papers:
        flash('Paper not found')
        return redirect(url_for('editor_dashboard'))
    
    # Eşsiz bir geçici dosya adı oluştur
    import time
    timestamp = int(time.time())
    
    try:
        if type == 'original':
            # Şifrelenmiş dosyayı geçici olarak deşifre et
            if 'encrypted_path' not in papers[paper_id]:
                flash('Original file not available')
                return redirect(url_for('editor_view_paper', paper_id=paper_id))
                
            encrypted_path = papers[paper_id]['encrypted_path']
            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{timestamp}_{paper_id}.pdf")
            path = decrypt_file(encrypted_path, temp_path)
            
        elif type == 'anonymous':
            if 'anonymous_path' not in papers[paper_id]:
                flash('Anonymous version not available')
                return redirect(url_for('editor_view_paper', paper_id=paper_id))
            
            # Şifrelenmiş anonim dosyayı deşifre et
            encrypted_path = papers[paper_id]['anonymous_path']
            temp_path = os.path.join(ANONYMIZED_FOLDER, f"temp_{timestamp}_{paper_id}.pdf")
            path = decrypt_file(encrypted_path, temp_path)
            
        elif type == 'reviewed':
            if 'reviewed_path' not in papers[paper_id]:
                flash('Reviewed version not available')
                return redirect(url_for('editor_view_paper', paper_id=paper_id))
            
            # Şifrelenmiş değerlendirme dosyasını deşifre et
            encrypted_path = papers[paper_id]['reviewed_path']
            temp_path = os.path.join(REVIEWED_FOLDER, f"temp_{timestamp}_{paper_id}.pdf")
            path = decrypt_file(encrypted_path, temp_path)
            
        else:
            flash('Invalid download type')
            return redirect(url_for('editor_view_paper', paper_id=paper_id))
        
        # Dosyayı indirdikten sonra temizleme işlemi için callback kullan
        @after_this_request
        def cleanup(response):
            try:
                if temp_path and os.path.exists(temp_path):
                    import time
                    # Bazen Windows'ta dosyaları hemen silmek zor olabilir
                    # Bu nedenle kısa bir bekleme süresi ekliyoruz
                    time.sleep(0.1)
                    os.remove(temp_path)
                    logger.info(f"Temporary file {temp_path} removed successfully")
            except Exception as e:
                logger.error(f"Error removing temporary file: {str(e)}")
            return response
        
        # Dosyayı indir
        return send_file(path, as_attachment=True)
        
    except Exception as e:
        logger.error(f"Error in editor_download: {str(e)}")
        if 'temp_path' in locals() and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        flash(f'Error downloading file: {str(e)}')
        return redirect(url_for('editor_view_paper', paper_id=paper_id))
    
# Routes for Reviewer (NO LOGIN REQUIRED as specified in the project)
@app.route('/reviewer')
def reviewer_dashboard():
    reviewer_email = request.args.get('email', '')
    reviewer_hash = hash_data(reviewer_email)
    assigned_papers = {pid: paper for pid, paper in papers.items() if paper.get('reviewer') == reviewer_hash}   
    return render_template('reviewer_dashboard.html', papers=assigned_papers, reviewer_email=reviewer_email)


@app.route('/reviewer/paper/<paper_id>', methods=['GET', 'POST'])
def reviewer_view_paper(paper_id):
    reviewer_email = request.args.get('email', '')    
    if paper_id not in papers or papers[paper_id].get('reviewer') != hash_data(reviewer_email):
        flash('Paper not found or not assigned to you')
        return redirect(url_for('reviewer_dashboard', email=reviewer_email))
    if request.method == 'POST':
        review_text = request.form.get('review_text', '').strip()
        if review_text:
            papers[paper_id]['review_text'] = review_text
            papers[paper_id]['review_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            papers[paper_id]['status'] = 'Reviewed'
            log_event(paper_id, "Review submitted")
            
            # In a real system, generate a PDF with the review appended
            flash('Review submitted successfully')
            return redirect(url_for('reviewer_dashboard', email=reviewer_email))
    return render_template('reviewer_view_paper.html', paper=papers[paper_id], paper_id=paper_id, reviewer_email=reviewer_email)


@app.route('/reviewer/download/<paper_id>')
def reviewer_download(paper_id):
    reviewer_email = request.args.get('email', '')    
    if paper_id not in papers or papers[paper_id].get('reviewer') != hash_data(reviewer_email):
        flash('Paper not found or not assigned to you')
        return redirect(url_for('reviewer_dashboard', email=reviewer_email))
    
    if 'anonymous_path' not in papers[paper_id]:
        flash('Anonymous version not available')
        return redirect(url_for('reviewer_view_paper', paper_id=paper_id, email=reviewer_email))
    
    # Eşsiz bir geçici dosya adı oluştur
    import time
    timestamp = int(time.time())
    temp_path = os.path.join(ANONYMIZED_FOLDER, f"temp_{paper_id}_{timestamp}.pdf")
    
    try:
        # Şifrelenmiş anonim dosyayı deşifre et
        encrypted_path = papers[paper_id]['anonymous_path']
        path = decrypt_file(encrypted_path, temp_path)
        
        # Dosyayı göndermeden önce bir callback oluştur
        @after_this_request
        def cleanup(response):
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                    logger.info(f"Temporary file {temp_path} removed successfully")
            except Exception as e:
                logger.error(f"Error removing temporary file: {str(e)}")
            return response
        
        return send_file(path, as_attachment=True)
        
    except Exception as e:
        # Hata durumunda temizleme yapmaya çalış
        logger.error(f"Error in reviewer_download: {str(e)}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        flash(f'Error downloading file: {str(e)}')
        return redirect(url_for('reviewer_view_paper', paper_id=paper_id, email=reviewer_email))

@app.template_filter('nl2br')
def nl2br_filter(text):
    if not text:
        return ""
    return text.replace('\n', '<br>')


if __name__ == '__main__':
    app.run(debug=True, port = 8080)