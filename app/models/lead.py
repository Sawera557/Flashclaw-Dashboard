from datetime import datetime, timezone
from app import db


class Lead(db.Model):
    __tablename__ = 'leads'
    __table_args__ = (
        db.UniqueConstraint('workspace_id', 'email', name='uq_leads_workspace_email'),
    )

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    first_name = db.Column(db.String(100), default='')
    last_name = db.Column(db.String(100), default='')
    email = db.Column(db.String(255), index=True, nullable=True)
    company = db.Column(db.String(255), default='')
    job_title = db.Column(db.String(255), default='')
    phone = db.Column(db.String(50), default='')
    linkedin_url = db.Column(db.String(500), default='')
    website = db.Column(db.String(500), default='')
    industry = db.Column(db.String(200), default='')
    location = db.Column(db.String(200), default='')
    company_size = db.Column(db.String(50), default='')

    source = db.Column(db.String(50), default='manual')  # apollo, maps, hunter, linkedin, firecrawl, csv, manual
    lead_score = db.Column(db.Integer, default=0)  # 0-100
    status = db.Column(db.String(30), default='new')  # new, contacted, replied, interested, not_interested, meeting_booked
    icp_match = db.Column(db.Float, default=0.0)
    score_reason = db.Column(db.Text, default='')

    enriched_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    generated_emails = db.relationship('GeneratedEmail', backref='lead', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'user_id': self.user_id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'email': self.email,
            'company': self.company,
            'job_title': self.job_title,
            'phone': self.phone,
            'linkedin_url': self.linkedin_url,
            'website': self.website,
            'industry': self.industry,
            'location': self.location,
            'company_size': self.company_size,
            'source': self.source,
            'lead_score': self.lead_score,
            'status': self.status,
            'icp_match': self.icp_match,
            'score_reason': self.score_reason,
            'enriched_at': self.enriched_at.isoformat() if self.enriched_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class LinkedInActivity(db.Model):
    __tablename__ = 'linkedin_activities'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    lead_name = db.Column(db.String(200), default='')
    company = db.Column(db.String(255), default='')
    linkedin_url = db.Column(db.String(500), default='')
    activity_type = db.Column(db.String(50), nullable=False)  # connection_sent, connection_accepted, dm_sent, reply_received, interested, not_interested, meeting_booked, followup_sent, profile_viewed
    activity_date = db.Column(db.String(50), default='')  # stored as string for flexibility
    notes = db.Column(db.Text, default='')
    source = db.Column(db.String(50), default='manual')  # manual, ai_dump, gmail, csv

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'user_id': self.user_id,
            'lead_name': self.lead_name,
            'company': self.company,
            'linkedin_url': self.linkedin_url,
            'activity_type': self.activity_type,
            'activity_date': self.activity_date,
            'notes': self.notes,
            'source': self.source,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class GeneratedEmail(db.Model):
    __tablename__ = 'generated_emails'

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    email_type = db.Column(db.String(20), default='cold')  # cold, followup, linkedin, sequence
    subject = db.Column(db.String(500), default='')
    body = db.Column(db.Text, default='')
    model = db.Column(db.String(100), default='')
    sent = db.Column(db.Boolean, default=False)
    sent_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'lead_id': self.lead_id,
            'user_id': self.user_id,
            'email_type': self.email_type,
            'subject': self.subject,
            'body': self.body,
            'model': self.model,
            'sent': self.sent,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
