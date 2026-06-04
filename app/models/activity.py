"""Dashboard-required models: lead_activities, email_activities, meetings, hubspot_deals."""
from datetime import datetime, timezone
from app import db


class LeadActivity(db.Model):
    """Log of actions taken on leads (calls, notes, status changes, etc.)."""
    __tablename__ = 'lead_activities'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'), nullable=True)

    activity_type = db.Column(db.String(50), nullable=False)  # call, note, status_change, enrichment, scored, email_opened, email_clicked
    description = db.Column(db.Text, default='')
    metadata_json = db.Column(db.Text, default='{}')  # JSON extras
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'user_id': self.user_id,
            'lead_id': self.lead_id,
            'activity_type': self.activity_type,
            'description': self.description,
            'metadata_json': self.metadata_json,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class EmailActivity(db.Model):
    """Tracks email sends, opens, clicks, replies — workspace and user scoped."""
    __tablename__ = 'email_activities'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'), nullable=True)

    email_type = db.Column(db.String(20), default='cold')  # cold, followup, linkedin, sequence
    subject = db.Column(db.String(500), default='')
    recipient = db.Column(db.String(255), default='')
    status = db.Column(db.String(30), default='sent')  # sent, opened, clicked, replied, bounced
    sent_at = db.Column(db.DateTime, nullable=True)
    opened_at = db.Column(db.DateTime, nullable=True)
    clicked_at = db.Column(db.DateTime, nullable=True)
    replied_at = db.Column(db.DateTime, nullable=True)
    reply_sentiment = db.Column(db.String(20), nullable=True)  # positive, neutral, negative

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'user_id': self.user_id,
            'lead_id': self.lead_id,
            'email_type': self.email_type,
            'subject': self.subject,
            'recipient': self.recipient,
            'status': self.status,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'opened_at': self.opened_at.isoformat() if self.opened_at else None,
            'clicked_at': self.clicked_at.isoformat() if self.clicked_at else None,
            'replied_at': self.replied_at.isoformat() if self.replied_at else None,
            'reply_sentiment': self.reply_sentiment,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Meeting(db.Model):
    """Scheduled meetings/events linked to leads."""
    __tablename__ = 'meetings'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'), nullable=True)

    title = db.Column(db.String(300), default='')
    meeting_type = db.Column(db.String(50), default='discovery')  # discovery, demo, followup, close
    scheduled_at = db.Column(db.DateTime, nullable=True)
    duration_minutes = db.Column(db.Integer, default=30)
    status = db.Column(db.String(20), default='scheduled')  # scheduled, completed, cancelled, no_show
    notes = db.Column(db.Text, default='')
    calendar_event_id = db.Column(db.String(255), default='')

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'user_id': self.user_id,
            'lead_id': self.lead_id,
            'title': self.title,
            'meeting_type': self.meeting_type,
            'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else None,
            'duration_minutes': self.duration_minutes,
            'status': self.status,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class HubspotDeal(db.Model):
    """Cached HubSpot deal data for pipeline snapshots."""
    __tablename__ = 'hubspot_deals'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    hubspot_deal_id = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(300), default='')
    amount = db.Column(db.Float, default=0.0)
    stage = db.Column(db.String(100), default='')
    stage_id = db.Column(db.String(50), default='')
    close_date = db.Column(db.DateTime, nullable=True)
    owner_name = db.Column(db.String(200), default='')
    owner_id = db.Column(db.String(50), default='')
    created_date = db.Column(db.DateTime, nullable=True)

    synced_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'hubspot_deal_id': self.hubspot_deal_id,
            'name': self.name,
            'amount': self.amount,
            'stage': self.stage,
            'stage_id': self.stage_id,
            'close_date': self.close_date.isoformat() if self.close_date else None,
            'owner_name': self.owner_name,
            'synced_at': self.synced_at.isoformat() if self.synced_at else None,
        }
