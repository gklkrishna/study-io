from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_socketio import join_room,leave_room,send,SocketIO,emit
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from collections import defaultdict
from datetime import datetime
import os
from dotenv import load_dotenv
from datetime import datetime, timezone,timedelta


# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
socketio=SocketIO(app)


app.config['SQLALCHEMY_DATABASE_URI'] = (
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    f"?ssl_ca={os.getenv('DB_SSL_CERT')}"
)

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static/images')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


#database models

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    profile_picture = db.Column(db.String(255), nullable=False, default="default_profile.png")
    description= db.Column(db.Text,nullable=True)
   
    
    # One User can own multiple Study Rooms.
    study_rooms = db.relationship(
        'Studyrooms',
        backref='owner',
        lazy=True,
        cascade="all, delete",
        passive_deletes=True
    )
    # If you want to cascade delete messages when a user is deleted:
    messages = db.relationship(
        'ChatMessage',
        backref='user',
        lazy=True,
        cascade="all, delete",
        passive_deletes=True
    )

class Studyrooms(db.Model):
    room_id = db.Column(db.Integer, primary_key=True)
    room_name = db.Column(db.String(100), nullable=False)
    room_code = db.Column(db.String(20), nullable=False, unique=True)
    
    # Foreign Key Constraint with cascade deletion.
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now(timezone.utc))
    
    # Relationships for room members, timers and chat messages.
    members = db.relationship(
        'Roommembers',
        backref='studyroom',
        lazy=True,
        cascade="all, delete",
        passive_deletes=True
    )
    timers = db.relationship(
        'Timers',
        backref='studyroom',
        lazy=True,
        cascade="all, delete",
        passive_deletes=True
    )
    chat_messages = db.relationship(
        'ChatMessage',
        backref='studyroom',
        lazy=True,
        cascade="all, delete",
        passive_deletes=True
    )

class Roommembers(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('studyrooms.room_id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    joined_at = db.Column(db.DateTime, nullable=False, default=datetime.now(timezone.utc))

class Timers(db.Model):
    timer_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('studyrooms.room_id', ondelete='CASCADE'), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, default=None, nullable=True)
    duration = db.Column(db.Integer, nullable=False)

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('studyrooms.room_id', ondelete='CASCADE'), nullable=False)
    username = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)



    
# Create tables
with app.app_context():
    db.create_all()

active_timers = {}  # { room_id: { user_id: start_time } }
paused_timers = {}  # { room_id: { user_id: paused_elapsed } }


# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm-password']

        if password != confirm_password:
            flash("Passwords do not match!", "error")
            return render_template('signup.html')

        hashed_password = generate_password_hash(password)
        new_user = User(name=name, email=email, password=hashed_password)
        
        try:
            db.session.add(new_user)
            db.session.commit()
        except Exception as e:
            flash(f"Database error: {e}", "error")
            return render_template('signup.html')

        flash("Account created successfully!", "success")
        return redirect(url_for('signin'))

    return render_template('signup.html')

@app.route('/signin', methods=['GET', 'POST'])
def signin():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['user_name'] = user.name
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid email or password!", "error")

    return render_template('signin.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('signin'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('signin'))
    
    user_id = session['user_id']

    # Fetch study rooms where the user is a member
    user_rooms = db.session.query(Studyrooms).join(Roommembers).filter(Roommembers.user_id == user_id).all()

    return render_template('dashboard.html', user_name=session['user_name'], study_rooms=user_rooms)


@app.route('/community')
def community():
    if 'user_id' not in session:
        return redirect(url_for('signin'))
    return render_template('community.html')

@app.route('/solo-study')
def solo_study():
    if 'user_id' not in session:
        return redirect(url_for('signin'))
    return render_template('solo-study.html')

@app.route('/createstudyroom', methods=['GET', 'POST'])
def createstudyroom():
    if request.method == 'POST':
        room_name = request.form.get('room_name')
        room_code = request.form.get('room_code')

        if not room_name or not room_code:
            flash("Please fill in all fields", "error")
            return render_template('createstudyroom.html')

        # Check if room_code already exists
        existing_room = Studyrooms.query.filter_by(room_code=room_code).first()
        if existing_room:
            flash("Unique Code already exists. Try another one.", "error")
            return render_template('createstudyroom.html')

        # Get logged-in user as owner_id
        owner_id = session.get('user_id')
        if not owner_id:
            flash("You need to be logged in to create a study room.", "error")
            return redirect(url_for('signin'))

        # Create the study room
        new_room = Studyrooms(room_name=room_name, room_code=room_code, owner_id=owner_id)
        db.session.add(new_room)
        db.session.commit()

        # ✅ Automatically add the creator as a member
        new_member = Roommembers(room_id=new_room.room_id, user_id=owner_id)
        db.session.add(new_member)
        db.session.commit()

        flash("Study Room created successfully!", "success")
        return redirect(url_for('dashboard'))  # Redirect to dashboard instead of studyroom page

    return render_template('createstudyroom.html')



@app.route('/studyroom/<room_code>')
def studyroom(room_code):
    # Check if the user is logged in
    if "user_id" not in session:
        return redirect(url_for("homepage"))

    # Fetch room details from the database
    room = Studyrooms.query.filter_by(room_code=room_code).first()
    
    if not room:
        flash("Study Room not found!", "danger")
        return redirect(url_for('dashboard'))  # Redirect if room does not exist

    # Fetch chat history with sender's name and profile picture
    messages = ChatMessage.query.join(User).add_columns(
        ChatMessage.message, ChatMessage.timestamp, User.name, User.profile_picture
    ).filter(ChatMessage.room_id == room.room_id).order_by(ChatMessage.timestamp).all()
    
    # Render studyroom.html with room details and messages
    return render_template('studyroom.html', room=room, messages=messages)



# analysis section

@app.route('/analysis')
def analysis():
    if 'user_id' not in session:
        return redirect(url_for('signin'))
    return render_template('study_analysis.html')

@app.route('/analysis_data')
def analysis_data():
    if 'user_id' not in session:
        return redirect(url_for('signin'))
    
    user_id = session['user_id']
    # Get completed timer sessions (where end_time is set)
    timers = Timers.query.filter_by(user_id=user_id).filter(Timers.end_time.isnot(None)).all()

    # Aggregations
    daily = defaultdict(int)
    weekly = defaultdict(int)
    session_durations = []
    room_comparison = defaultdict(int)

    for timer in timers:
        # Daily: group by date of start_time (YYYY-MM-DD)
        day = timer.start_time.strftime('%Y-%m-%d')
        daily[day] += timer.duration
        
        # Weekly: group by year and week number (e.g. '2025-W07')
        week = timer.start_time.strftime('%Y-W%U')
        weekly[week] += timer.duration
        
        # Record each session duration for distribution analysis
        session_durations.append(timer.duration)
        
        # Room comparison: sum durations per room_id
        room_comparison[timer.room_id] += timer.duration

    # Prepare room data with room names
    room_data = []
    for room_id, total in room_comparison.items():
        room_obj = Studyrooms.query.get(room_id)
        room_name = room_obj.room_name if room_obj else f'Room {room_id}'
        room_data.append({'room': room_name, 'total': total})

    # Return the aggregated data as JSON
    return {
        'daily': sorted(daily.items()),
        'weekly': sorted(weekly.items()),
        'session_durations': session_durations,
        'room_comparison': room_data
    }


studying_members = {}  # Store active studying members per room

@socketio.on('join')
def handle_join(data):
    room = data['room']
    username = data['username']
    join_room(room)
   # send(f"{username} has joined the chat.", to=room)

@socketio.on('message')
def handle_message(data):
    room = data['room']
    username = data['username']
    message = data['message']
    user_id = session.get('user_id')

    # Fetch user's profile picture
    user = User.query.get(user_id)
    profile_picture = user.profile_picture if user else "default_profile.png"

    # Store message in the database
    new_message = ChatMessage(room_id=room, username=username, message=message, user_id=user_id)
    db.session.add(new_message)
    db.session.commit()

    send({'username': username, 'message': message, 'profile_picture': profile_picture}, to=room)








@app.route('/joinstudyroom', methods=['GET', 'POST'])
def joinstudyroom():
    if request.method == 'POST':
        room_code = request.form.get('room_code')

        # Check if study room exists
        study_room = Studyrooms.query.filter_by(room_code=room_code).first()

        if not study_room:
            flash("No study room found with that code!", "error")
            return redirect(url_for('joinstudyroom'))

        user_id = session.get('user_id')
        if not user_id:
            flash("You need to be logged in to join a study room.", "error")
            return redirect(url_for('signin'))

        # Check if user is already a member of the study room
        existing_member = Roommembers.query.filter_by(room_id=study_room.room_id, user_id=user_id).first()
        if existing_member:
            flash("You are already a member of this study room!", "info")
            return redirect(url_for('studyroom', room_code=room_code))

        # Add user to Roommembers table
        new_member = Roommembers(room_id=study_room.room_id, user_id=user_id)
        db.session.add(new_member)
        db.session.commit()

        flash("Successfully joined the study room!", "success")
        return redirect(url_for('studyroom', room_code=room_code))

    return render_template('joinstudyroom.html')

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('signin'))
    
    user = User.query.get(session['user_id'])

    if request.method == 'POST':
        username = request.form.get("username")
        description = request.form.get("description")
        profile_picture = request.files.get("profile_picture")

        if username:
            user.name = username
        if description:
            user.description = description
        
        # Handle profile picture update
        if profile_picture and profile_picture.filename:
            filename = secure_filename(profile_picture.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
            
            profile_picture.save(filepath)
            user.profile_picture = filename

        db.session.commit()
        return redirect(url_for('profile'))

    study_rooms = db.session.query(Studyrooms).join(Roommembers).filter(Roommembers.user_id == user.id).all()
    return render_template('profile.html', user=user, study_rooms=study_rooms)
# Other routes...

if __name__ == '__main__':
    app.run(debug=True)

def get_studying_members(room_id):
    members = []
    # Process active timers: calculate live elapsed time.
    if room_id in active_timers:
        for user_id, start_time in active_timers[room_id].items():
            elapsed = (datetime.utcnow() - start_time).seconds
            user = User.query.get(user_id)
            if user:
                members.append({
                    'user_id': user_id,
                    'username': user.name,
                    'profile_picture': user.profile_picture,
                    'time_left': elapsed
                })
    # Process paused timers: use stored frozen elapsed time.
    if room_id in paused_timers:
        for user_id, paused_value in paused_timers[room_id].items():
            user = User.query.get(user_id)
            if user:
                members.append({
                    'user_id': user_id,
                    'username': user.name,
                    'profile_picture': user.profile_picture,
                    'time_left': paused_value
                })
    return members

@socketio.on('join_room')
def handle_join_room(data):
    room_id = str(data.get('room_id'))
    username = data.get('username', '')
    user_id = session.get('user_id')
    if not user_id:
        return

    join_room(room_id)
    print(f"User {username} joined room {room_id}")

    # Fetch active timer records from the database
    active_timer_records = Timers.query.filter_by(room_id=room_id, end_time=None).all()
    studying_members = []
    for timer in active_timer_records:
        elapsed_time = (datetime.utcnow() - timer.start_time).seconds
        user = User.query.get(timer.user_id)
        if user:
            studying_members.append({
                'user_id': timer.user_id,
                'username': user.name,
                'profile_picture': user.profile_picture,
                'time_left': elapsed_time
            })
    emit('update_studying_members', {'members': studying_members}, room=room_id)

@socketio.on('start_timer')
def handle_start_timer(data):
    room_id = str(data.get('room_id'))
    user_id = session.get('user_id')
    
    # Check if the user is resuming from pause.
    if room_id in paused_timers and user_id in paused_timers[room_id]:
        paused_elapsed = paused_timers[room_id][user_id]
        new_start_time = datetime.utcnow() - timedelta(seconds=paused_elapsed)
        del paused_timers[room_id][user_id]
        if room_id not in active_timers:
            active_timers[room_id] = {}
        active_timers[room_id][user_id] = new_start_time
        user = User.query.get(user_id)
        print(f"User {user_id} resumed with paused elapsed {paused_elapsed}")
        emit('update_studying_members', {'members': get_studying_members(room_id)}, room=room_id, broadcast=True)
        return

    # Check for an existing active timer.
    existing_timer = Timers.query.filter_by(user_id=user_id, room_id=room_id, end_time=None).first()
    if existing_timer:
        print("Active timer already exists for user", user_id)
        return
    # Start a new timer.
    start_time = datetime.utcnow()
    new_timer = Timers(user_id=user_id, room_id=room_id, start_time=start_time, duration=0)
    db.session.add(new_timer)
    db.session.commit()

    if room_id not in active_timers:
        active_timers[room_id] = {}
    active_timers[room_id][user_id] = start_time

    user = User.query.get(user_id)
    emit('update_studying_members', {'members': get_studying_members(room_id)}, room=room_id, broadcast=True)

@socketio.on('pause_timer')
def handle_pause_timer(data):
    room_id = str(data.get('room_id'))
    user_id = session.get('user_id')
    if room_id in active_timers and user_id in active_timers[room_id]:
        start_time = active_timers[room_id][user_id]
        paused_elapsed = (datetime.utcnow() - start_time).seconds
        del active_timers[room_id][user_id]
        if room_id not in paused_timers:
            paused_timers[room_id] = {}
        paused_timers[room_id][user_id] = paused_elapsed
        print(f"User {user_id} paused at {paused_elapsed} seconds")
        socketio.emit('update_studying_members', {'members': get_studying_members(room_id)}, room=room_id)

@socketio.on('stop_timer')
def handle_stop_timer(data):
    room_id = str(data.get('room_id'))
    user_id = session.get('user_id')
    timer = Timers.query.filter_by(user_id=user_id, room_id=room_id, end_time=None).first()
    if timer:
        timer.end_time = datetime.utcnow()
        timer.duration = (timer.end_time - timer.start_time).seconds
        db.session.commit()
    if room_id in active_timers and user_id in active_timers[room_id]:
        del active_timers[room_id][user_id]
        emit('remove_studying_member', {'user_id': user_id}, room=room_id, broadcast=True)

@socketio.on('reset_timer')
def handle_reset_timer(data):
    room_id = str(data.get('room_id'))
    user_id = session.get('user_id')
    # End any active timer in the database
    timer = Timers.query.filter_by(user_id=user_id, room_id=room_id, end_time=None).first()
    if timer:
        timer.end_time = datetime.utcnow()
        timer.duration = (timer.end_time - timer.start_time).seconds
        db.session.commit()
        db.session.expire_all()  # Refresh session so new queries get fresh data
    # Remove user from active_timers (if present)
    if room_id in active_timers and user_id in active_timers[room_id]:
        del active_timers[room_id][user_id]
    # Also remove user from paused_timers (if present)
    if room_id in paused_timers and user_id in paused_timers[room_id]:
        del paused_timers[room_id][user_id]
    # Notify clients to remove the user from the shared timer display
    emit('remove_studying_member', {'user_id': user_id}, room=room_id, broadcast=True)


def update_active_timers():
    with app.app_context():
        while True:
            socketio.sleep(1)
            # Process room IDs from both active and paused dictionaries.
            rooms = set(list(active_timers.keys()) + list(paused_timers.keys()))
            for room_id in rooms:
                members = get_studying_members(room_id)
                socketio.emit('update_studying_members', {'members': members}, room=room_id)

socketio.start_background_task(update_active_timers)


#leaderboard

@app.route('/studyroom/<room_code>/leaderboard')
def studyroom_leaderboard(room_code):
    # Fetch the study room using room_code
    room = Studyrooms.query.filter_by(room_code=room_code).first()
    if not room:
        flash("Study Room not found!", "danger")
        return redirect(url_for('dashboard'))
    # Render the dedicated leaderboard page template
    return render_template('leaderboard.html', room=room)



@app.route('/leaderboard/<room_code>')
def leaderboard(room_code):
    # Get the study room from room_code.
    room = Studyrooms.query.filter_by(room_code=room_code).first()
    if not room:
        return {"error": "Room not found"}, 404

    now = datetime.utcnow()
    # Define start times for the periods:
    start_of_day = datetime(now.year, now.month, now.day)
    start_of_month = datetime(now.year, now.month, 1)
    # Assuming Monday as the start of week.
    start_of_week = now - timedelta(days=now.weekday())

    # Only count timers that have an end_time (completed sessions)
    overall_timers = (
        db.session.query(Timers.user_id, db.func.sum(Timers.duration).label("total"))
        .filter(Timers.room_id == room.room_id, Timers.end_time.isnot(None))
        .group_by(Timers.user_id)
        .all()
    )
    monthly_timers = (
        db.session.query(Timers.user_id, db.func.sum(Timers.duration).label("total"))
        .filter(
            Timers.room_id == room.room_id,
            Timers.end_time.isnot(None),
            Timers.start_time >= start_of_month
        )
        .group_by(Timers.user_id)
        .all()
    )
    weekly_timers = (
        db.session.query(Timers.user_id, db.func.sum(Timers.duration).label("total"))
        .filter(
            Timers.room_id == room.room_id,
            Timers.end_time.isnot(None),
            Timers.start_time >= start_of_week
        )
        .group_by(Timers.user_id)
        .all()
    )
    daily_timers = (
        db.session.query(Timers.user_id, db.func.sum(Timers.duration).label("total"))
        .filter(
            Timers.room_id == room.room_id,
            Timers.end_time.isnot(None),
            Timers.start_time >= start_of_day
        )
        .group_by(Timers.user_id)
        .all()
    )

    def format_leaderboard(data):
        lb = []
        for user_id, total in data:
            user = User.query.get(user_id)
            if user:
                lb.append({
                    "username": user.name,
                    "total": total,
                    "profile_picture": user.profile_picture
                })
        lb.sort(key=lambda x: x["total"], reverse=True)
        return lb

    leaderboard_data = {
        "overall": format_leaderboard(overall_timers),
        "monthly": format_leaderboard(monthly_timers),
        "weekly": format_leaderboard(weekly_timers),
        "daily": format_leaderboard(daily_timers)
    }
    return leaderboard_data


@app.route('/roommembers/<room_code>')
def room_members(room_code):
    room = Studyrooms.query.filter_by(room_code=room_code).first()
    if not room:
        return {"error": "Room not found"}, 404
    # Query to join Roommembers and User to get member details.
    members = db.session.query(User).join(Roommembers, User.id == Roommembers.user_id).filter(Roommembers.room_id == room.room_id).all()
    members_data = [{"name": member.name, "profile_picture": member.profile_picture} for member in members]
    return {"members": members_data}


# ---------------------------
# New Models for the Community Blog
# ---------------------------
class BlogPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    community = db.Column(db.String(100), nullable=False)  # e.g., "science", "coding", etc.
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    author = db.relationship('User', backref='blog_posts')

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('blog_post.id'), nullable=False)
    
    author = db.relationship('User', backref='comments')
    post = db.relationship('BlogPost', backref='comments')

# Create new tables if they don't exist
with app.app_context():
    db.create_all()

# ---------------------------
# Routes for the Community Blog
# ---------------------------
@app.route('/community_blog/<community>', methods=['GET', 'POST'])
def community_blog(community):
    if 'user_id' not in session:
        return redirect(url_for('signin'))
    
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        if not title or not content:
            flash("Title and content are required to create a post.", "error")
        else:
            new_post = BlogPost(community=community, title=title, content=content, author_id=session['user_id'])
            db.session.add(new_post)
            db.session.commit()
            flash("Post created successfully!", "success")
            return redirect(url_for('community_blog', community=community))
    
    posts = BlogPost.query.filter_by(community=community).order_by(BlogPost.timestamp.desc()).all()
    return render_template('community_blog.html', community=community, posts=posts)

@app.route('/community/blog/<community>/post/<int:post_id>', methods=['GET', 'POST'])
def view_post(community, post_id):
    if 'user_id' not in session:
        return redirect(url_for('signin'))
    
    post = BlogPost.query.get_or_404(post_id)
    
    if request.method == 'POST':
        comment_content = request.form.get('comment')
        if comment_content:
            new_comment = Comment(content=comment_content, author_id=session['user_id'], post_id=post.id)
            db.session.add(new_comment)
            db.session.commit()
            flash("Comment added successfully!", "success")
            return redirect(url_for('view_post', community=community, post_id=post_id))
    
    comments = Comment.query.filter_by(post_id=post.id).order_by(Comment.timestamp.asc()).all()
    return render_template('view_post.html', community=community, post=post, comments=comments)

@app.route('/studygoals')
def studygoals():
    return render_template('studygoals.html')

@socketio.on('leave_room')
def handle_leave_room(data):
    room_id = data['room_id']
    username = data['username']

    # Get the user by username
    user = User.query.filter_by(name=username).first()
    if user:
        # Find the record of the user in Roommembers
        room_member = Roommembers.query.filter_by(room_id=room_id, user_id=user.id).first()
        if room_member:
            db.session.delete(room_member)  # Delete user from Roommembers table
            db.session.commit()
            print(f"User {username} has left the room {room_id}")

    # Ensure the user leaves the room
    leave_room(room_id)

    # Update the room members list for everyone else
    emit('update_studying_members', {'members': get_studying_members(room_id)}, room=room_id, broadcast=True)


if __name__ == '__main__':
    socketio.run(app, debug=True)