from datetime import datetime, timezone
from app import db
from werkzeug.security import generate_password_hash, check_password_hash


class Workspace(db.Model):
    __tablename__ = 'workspaces'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    users = db.relationship('User', backref='workspace', lazy=True)
    leads = db.relationship('Lead', backref='workspace', lazy=True)
    linkedin_activities = db.relationship('LinkedInActivity', backref='workspace', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='sdr')  # sdr, manager, admin
    avatar = db.Column(db.String(10), default='')  # initials
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    leads = db.relationship('Lead', backref='owner', lazy=True)
    generated_emails = db.relationship('GeneratedEmail', backref='creator', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'name': self.name,
            'email': self.email,
            'role': self.role,
            'avatar': self.avatar,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
