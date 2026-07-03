from flask import Flask, render_template, request, redirect, session, send_from_directory, abort, flash
import pymysql
from werkzeug.utils import secure_filename
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = "secret123"


@app.route('/__source__')
def _source():
    # Diagnostic endpoint to identify which app instance is serving requests
    info = {
        'root_path': app.root_path,
        'template_folder': app.template_folder,
        'static_folder': app.static_folder,
        'file': __file__
    }
    return info


UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

PROFILE_PIC_FOLDER = "profile_pics"
os.makedirs(PROFILE_PIC_FOLDER, exist_ok=True)

db = pymysql.connect(
    host="localhost",
    user="root",
    password="password123",
    database="regdb"
)

cursor = db.cursor()


@app.route("/")
def home():
    if "user_id" in session:
        return redirect("/dashboard")

    return render_template("login.html")


@app.route("/register-page")
def register_page():
    return render_template("register.html")


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect("/")

    cursor.execute(
        "SELECT * FROM files WHERE user_id=%s ORDER BY upload_time DESC",
        (session["user_id"],)
    )

    files = cursor.fetchall()
    
    total_uploads = len(files)
    total_downloads = sum(f[5] or 0 for f in files)
    
    storage_bytes = 0
    for f in files:
        filepath = f[3]
        if filepath and os.path.exists(filepath):
            storage_bytes += os.path.getsize(filepath)
            
    # Format storage size
    if storage_bytes < 1024:
        storage_str = f"{storage_bytes} B"
    elif storage_bytes < 1024 * 1024:
        storage_str = f"{storage_bytes / 1024:.1f} KB"
    elif storage_bytes < 1024 * 1024 * 1024:
        storage_str = f"{storage_bytes / (1024 * 1024):.1f} MB"
    else:
        storage_str = f"{storage_bytes / (1024 * 1024 * 1024):.2f} GB"

    summary_stats = {
        'uploads': total_uploads,
        'downloads': total_downloads,
        'storage': storage_str
    }

    cursor.execute("SELECT * FROM users WHERE id=%s", (session["user_id"],))
    user = cursor.fetchone()

    return render_template(
        "dashboard.html",
        files=files,
        user=user,
        username=session["username"],
        summary_stats=summary_stats
    )


@app.route("/register", methods=["POST"])
def register():
    username = request.form["username"]
    email = request.form["email"]
    password = request.form["password"]

    cursor.execute(
        """
        INSERT INTO users(username,email,password)
        VALUES(%s,%s,%s)
        """,
        (username, email, password)
    )

    db.commit()

    return redirect("/")


@app.route("/login", methods=["POST"])
def login():
    identity = request.form["identity"]
    password = request.form["password"]

    cursor.execute(
        """
        SELECT * FROM users
        WHERE (username=%s OR email=%s)
        AND password=%s
        """,
        (identity, identity, password)
    )

    user = cursor.fetchone()

    if user:
        session["user_id"] = user[0]
        session["username"] = user[1]
        
        cursor.execute("UPDATE users SET last_login=%s WHERE id=%s", (datetime.now(), user[0]))
        db.commit()
        
        return redirect("/dashboard")

    return "Invalid Username/Email or Password"


@app.route("/profile")
def profile():
    if "user_id" not in session:
        return redirect("/")

    cursor.execute("SELECT * FROM users WHERE id=%s", (session["user_id"],))
    user = cursor.fetchone()

    cursor.execute(
        "SELECT * FROM files WHERE user_id=%s ORDER BY upload_time DESC",
        (session["user_id"],)
    )
    files = cursor.fetchall()

    return render_template("profile.html", user=user, files=files)


@app.route("/update_password", methods=["POST"])
def update_password():
    if "user_id" not in session:
        return redirect("/")

    current_password = request.form["current_password"]
    new_password = request.form["new_password"]
    confirm_password = request.form["confirm_password"]
    referrer = request.form.get("referrer", "/profile")

    cursor.execute("SELECT password FROM users WHERE id=%s", (session["user_id"],))
    user = cursor.fetchone()

    if user[0] != current_password:
        flash("Current password is incorrect.", "error")
        return redirect(referrer)
    
    if new_password != confirm_password:
        flash("New passwords do not match.", "error")
        return redirect(referrer)

    cursor.execute("UPDATE users SET password=%s WHERE id=%s", (new_password, session["user_id"]))
    db.commit()
    
    flash("Password updated successfully.", "success")
    return redirect(referrer)


@app.route("/update_profile_pic", methods=["POST"])
def update_profile_pic():
    if "user_id" not in session:
        return redirect("/")

    if "profile_pic" not in request.files:
        flash("No file part", "error")
        return redirect("/profile")
        
    file = request.files["profile_pic"]
    if file.filename == "":
        flash("No selected file", "error")
        return redirect("/profile")

    filename = secure_filename(f"user_{session['user_id']}_{file.filename}")
    filepath = os.path.join(PROFILE_PIC_FOLDER, filename)
    file.save(filepath)

    cursor.execute("UPDATE users SET profile_pic=%s WHERE id=%s", (filename, session["user_id"]))
    db.commit()

    flash("Profile picture updated.", "success")
    return redirect("/profile")


@app.route("/profile_pics/<filename>")
def profile_pic(filename):
    if "user_id" not in session:
        return redirect("/")
    directory = os.path.abspath(PROFILE_PIC_FOLDER)
    return send_from_directory(directory, filename)


@app.route("/settings")
def settings():
    if "user_id" not in session:
        return redirect("/")

    cursor.execute("SELECT * FROM users WHERE id=%s", (session["user_id"],))
    user = cursor.fetchone()

    return render_template("settings.html", user=user)


@app.route("/update_email", methods=["POST"])
def update_email():
    if "user_id" not in session:
        return redirect("/")

    new_email = request.form["email"]
    if not new_email:
        flash("Email cannot be empty.", "error")
        return redirect("/settings")

    cursor.execute("UPDATE users SET email=%s WHERE id=%s", (new_email, session["user_id"]))
    db.commit()

    flash("Email updated successfully.", "success")
    return redirect("/settings")


@app.route("/delete_account", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return redirect("/")

    user_id = session["user_id"]

    # Retrieve all files owned by the user
    cursor.execute("SELECT * FROM files WHERE user_id=%s", (user_id,))
    files = cursor.fetchall()

    # Delete files from filesystem
    for file in files:
        filepath = file[3]
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                print(f"Error removing file {filepath} from filesystem: {e}")

    # Delete database records
    cursor.execute("DELETE FROM files WHERE user_id=%s", (user_id,))
    cursor.execute("DELETE FROM users WHERE id=%s", (user_id,))
    db.commit()

    # Clear session and redirect to home
    session.clear()
    flash("Your account and all associated files have been permanently deleted.", "success")
    return redirect("/")


@app.route("/upload", methods=["POST"])
def upload():
    if "user_id" not in session:
        return redirect("/")

    file = request.files["uploaded_file"]
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    cursor.execute(
        """
        INSERT INTO files(user_id,file_name,file_path)
        VALUES(%s,%s,%s)
        """,
        (session["user_id"], filename, filepath)
    )
    db.commit()

    return redirect("/dashboard")


@app.route("/download/<int:file_id>")
def download_file(file_id):
    if "user_id" not in session:
        return redirect("/")

    cursor.execute(
        "SELECT * FROM files WHERE id=%s AND user_id=%s",
        (file_id, session["user_id"])
    )
    file_record = cursor.fetchone()

    if not file_record:
        return abort(404, description="File not found or access denied")

    cursor.execute("UPDATE files SET download_count = download_count + 1 WHERE id=%s", (file_id,))
    db.commit()

    filename = file_record[2]
    directory = os.path.abspath(UPLOAD_FOLDER)
    return send_from_directory(directory, filename, as_attachment=True)


@app.route("/delete/<int:file_id>", methods=["POST"])
def delete_file(file_id):
    if "user_id" not in session:
        return redirect("/")

    cursor.execute(
        "SELECT * FROM files WHERE id=%s AND user_id=%s",
        (file_id, session["user_id"])
    )
    file_record = cursor.fetchone()

    if not file_record:
        return abort(404, description="File not found or access denied")

    # Delete from filesystem if it exists
    filepath = file_record[3]
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            print(f"Error removing file from filesystem: {e}")

    # Delete from database
    cursor.execute("DELETE FROM files WHERE id=%s", (file_id,))
    db.commit()

    return redirect("/dashboard")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
