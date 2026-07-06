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


import boto3

# Database Configuration
DB_HOST = "cloudlocker-db.c49meyskk70n.us-east-1.rds.amazonaws.com"          # Replace with RDS endpoint (e.g. "xxx.rds.amazonaws.com") when deploying
DB_USER = "admin"               # Replace with RDS database username
DB_PASSWORD = "password123"    # Replace with RDS database password
DB_NAME = "regdb"              # Replace with RDS database name
DB_PORT = 3306

# AWS S3 Configuration
AWS_ACCESS_KEY_ID = None          # Replace with AWS Access Key, or set to None if using EC2 IAM Role
AWS_SECRET_ACCESS_KEY = None    # Replace with AWS Secret Key, or set to None if using EC2 IAM Role
AWS_REGION = "us-east-1"
BUCKET_NAME = "cloudlocker-storage-ab"            # Replace with your S3 Bucket Name

db = pymysql.connect(
    host=DB_HOST,
    user=DB_USER,
    password=DB_PASSWORD,
    database=DB_NAME,
    port=DB_PORT
)

cursor = db.cursor()

# Initialize AWS S3 Client
s3_params = {"region_name": AWS_REGION}
if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
    s3_params["aws_access_key_id"] = AWS_ACCESS_KEY_ID
    s3_params["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY

s3_client = boto3.client("s3", **s3_params)


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
    db.ping(reconnect=True)
    cursor.execute(
        "SELECT id, user_id, file_name, s3_key, upload_time, download_count FROM files WHERE user_id=%s ORDER BY upload_time DESC",
        (session["user_id"],)
    )

    files = cursor.fetchall()
    
    total_uploads = len(files)
    total_downloads = sum(f[5] or 0 for f in files)
    
    storage_bytes = 0
    for f in files:
        filepath = f[3]
        if filepath:
            try:
                response = s3_client.head_object(Bucket=BUCKET_NAME, Key=filepath)
                storage_bytes += response.get('ContentLength', 0)
            except Exception as e:
                print(f"Error getting file size for {filepath}: {e}")
            
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
    db.ping(reconnect=True)
    cursor.execute("SELECT id, username, email, password, profile_pic, last_login FROM users WHERE id=%s", (session["user_id"],))
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
    db.ping(reconnect=True)
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
    db.ping(reconnect=True)
    cursor.execute(
        """
        SELECT id, username, email, password, profile_pic, last_login FROM users
        WHERE (username=%s OR email=%s)
        AND password=%s
        """,
        (identity, identity, password)
    )

    user = cursor.fetchone()

    if user:
        session["user_id"] = user[0]
        session["username"] = user[1]
        db.ping(reconnect=True)
        cursor.execute("UPDATE users SET last_login=%s WHERE id=%s", (datetime.now(), user[0]))
        db.commit()
        
        return redirect("/dashboard")

    return "Invalid Username/Email or Password"


@app.route("/profile")
def profile():
    if "user_id" not in session:
        return redirect("/")
    db.ping(reconnect=True)
    cursor.execute("SELECT id, username, email, password, profile_pic, last_login FROM users WHERE id=%s", (session["user_id"],))
    user = cursor.fetchone()
    db.ping(reconnect=True)
    cursor.execute(
        "SELECT id, user_id, file_name, s3_key, upload_time, download_count FROM files WHERE user_id=%s ORDER BY upload_time DESC",
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
    db.ping(reconnect=True)
    cursor.execute("SELECT password FROM users WHERE id=%s", (session["user_id"],))
    user = cursor.fetchone()

    if user[0] != current_password:
        flash("Current password is incorrect.", "error")
        return redirect(referrer)
    
    if new_password != confirm_password:
        flash("New passwords do not match.", "error")
        return redirect(referrer)
    db.ping(reconnect=True)
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
    s3_key = f"profile_pics/{filename}"

    try:
        s3_client.upload_fileobj(file, BUCKET_NAME, s3_key)
        cursor.execute("UPDATE users SET profile_pic=%s WHERE id=%s", (s3_key, session["user_id"]))
        db.commit()
        flash("Profile picture updated.", "success")
    except Exception as e:
        print(f"Error uploading profile picture to S3: {e}")
        flash("Error uploading profile picture to S3.", "error")

    return redirect("/profile")


@app.route("/profile_pics/<path:filename>")
def profile_pic(filename):
    if "user_id" not in session:
        return redirect("/")
    
    # Prefix filename if not already formatted
    s3_key = filename if filename.startswith("profile_pics/") else f"profile_pics/{filename}"
    
    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': BUCKET_NAME, 'Key': s3_key},
            ExpiresIn=3600
        )
        return redirect(url)
    except Exception as e:
        print(f"Error generating presigned URL for profile pic: {e}")
        return abort(404, description="Profile picture not found")


@app.route("/settings")
def settings():
    if "user_id" not in session:
        return redirect("/")
    db.ping(reconnect=True)
    cursor.execute("SELECT id, username, email, password, profile_pic, last_login FROM users WHERE id=%s", (session["user_id"],))
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
    db.ping(reconnect=True)
    cursor.execute("UPDATE users SET email=%s WHERE id=%s", (new_email, session["user_id"]))
    db.commit()

    flash("Email updated successfully.", "success")
    return redirect("/settings")


@app.route("/delete_account", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return redirect("/")

    user_id = session["user_id"]
    db.ping(reconnect=True)
    # Retrieve all files owned by the user
    cursor.execute("SELECT id, user_id, file_name, s3_key, upload_time, download_count FROM files WHERE user_id=%s", (user_id,))
    files = cursor.fetchall()

    # Delete files from S3
    for file in files:
        s3_key = file[3]
        if s3_key:
            try:
                s3_client.delete_object(Bucket=BUCKET_NAME, Key=s3_key)
            except Exception as e:
                print(f"Error removing file {s3_key} from S3: {e}")
    db.ping(reconnect=True)
    # Delete profile picture from S3 if exists
    cursor.execute("SELECT profile_pic FROM users WHERE id=%s", (user_id,))
    user_record = cursor.fetchone()
    if user_record and user_record[0]:
        try:
            s3_client.delete_object(Bucket=BUCKET_NAME, Key=user_record[0])
        except Exception as e:
            print(f"Error removing profile picture {user_record[0]} from S3: {e}")
    db.ping(reconnect=True)
    # Delete database records
    cursor.execute("DELETE FROM files WHERE user_id=%s", (user_id,))
    db.ping(reconnect=True)
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

    if "uploaded_file" not in request.files:
        flash("No file part", "error")
        return redirect("/dashboard")

    file = request.files["uploaded_file"]

    if file.filename == "":
        flash("No selected file", "error")
        return redirect("/dashboard")

    filename = secure_filename(file.filename)
    s3_key = f"uploads/user_{session['user_id']}_{filename}"

    try:
        s3_client.upload_fileobj(file, BUCKET_NAME, s3_key)
        db.ping(reconnect=True)
        cursor.execute(
            """
            INSERT INTO files (user_id, file_name, s3_key)
            VALUES (%s, %s, %s)
            """,
            (session["user_id"], filename, s3_key)
        )

        db.commit()
        flash("File uploaded successfully.", "success")

    except Exception as e:
        print(f"Error uploading file to S3: {e}")
        flash("Error uploading file to S3.", "error")

    return redirect("/dashboard")


@app.route("/download/<int:file_id>")
def download_file(file_id):
    if "user_id" not in session:
        return redirect("/")
    db.ping(reconnect=True)
    cursor.execute(
        "SELECT id, user_id, file_name, s3_key, upload_time, download_count FROM files WHERE id=%s AND user_id=%s",
        (file_id, session["user_id"])
    )
    file_record = cursor.fetchone()

    if not file_record:
        return abort(404, description="File not found or access denied")
    db.ping(reconnect=True)
    cursor.execute("UPDATE files SET download_count = download_count + 1 WHERE id=%s", (file_id,))
    db.commit()

    filename = file_record[2]
    s3_key = file_record[3]

    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': BUCKET_NAME,
                'Key': s3_key,
                'ResponseContentDisposition': f'attachment; filename="{filename}"'
            },
            ExpiresIn=3600
        )
        return redirect(url)
    except Exception as e:
        print(f"Error generating presigned download URL: {e}")
        return abort(500, description="Could not download file from S3")


@app.route("/delete/<int:file_id>", methods=["POST"])
def delete_file(file_id):
    if "user_id" not in session:
        return redirect("/")
    db.ping(reconnect=True)
    cursor.execute(
        "SELECT id, user_id, file_name, s3_key, upload_time, download_count FROM files WHERE id=%s AND user_id=%s",
        (file_id, session["user_id"])
    )
    file_record = cursor.fetchone()

    if not file_record:
        return abort(404, description="File not found or access denied")

    # Delete from S3
    s3_key = file_record[3]
    try:
        s3_client.delete_object(Bucket=BUCKET_NAME, Key=s3_key)
    except Exception as e:
        print(f"Error deleting file {s3_key} from S3: {e}")
    db.ping(reconnect=True)
    # Delete from database
    cursor.execute("DELETE FROM files WHERE id=%s", (file_id,))
    db.commit()

    return redirect("/dashboard")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
