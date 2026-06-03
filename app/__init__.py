from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from app.config import config_by_name

db = SQLAlchemy()
jwt = JWTManager()


def create_app(config_name='dev'):
    """Application factory."""
    app = Flask(__name__)

    # Load config
    config = config_by_name.get(config_name, config_by_name['dev'])
    app.config.from_object(config)

    # Initialize extensions
    db.init_app(app)
    jwt.init_app(app)

    # Enable CORS for all /api/* routes
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Register blueprints (imported here to avoid circular imports)
    from app.routes.auth import auth_bp
    from app.routes.leads import leads_bp
    from app.routes.ai_agent import ai_bp
    from app.routes.linkedin import linkedin_bp
    from app.routes.analytics import analytics_bp
    from app.routes.admin import admin_bp
    from app.routes.gmail import gmail_bp
    from app.routes.hubspot import hubspot_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(linkedin_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(gmail_bp)
    app.register_blueprint(hubspot_bp)

    # Create tables and seed demo data on first run
    with app.app_context():
        db.create_all()
        _seed_demo_data()

    @app.route('/')
    def index():
        return {
            'app': 'Lead Hunter AI',
            'version': '1.0.0',
            'status': 'running',
        }

    @app.route('/app')
    @app.route('/dashboard')
    def serve_frontend():
        import os
        from flask import send_from_directory
        frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')
        return send_from_directory(frontend_dir, 'index.html')

    @app.route('/api/health')
    def health():
        return {'status': 'healthy'}

    return app


def _seed_demo_data():
    """Seed demo leads and LinkedIn activities on first run if DB is empty."""
    from app.models.user import User, Workspace
    from app.models.lead import Lead, LinkedInActivity

    # Don't seed if data already exists
    if Lead.query.first():
        return

    # Create demo workspace
    workspace = Workspace.query.first()
    if not workspace:
        workspace = Workspace(name='Demo Agency')
        db.session.add(workspace)
        db.session.flush()

    # Create demo user
    demo_user = User.query.filter_by(email='demo@leadhunter.ai').first()
    if not demo_user:
        demo_user = User(
            workspace_id=workspace.id,
            name='Demo User',
            email='demo@leadhunter.ai',
            role='admin',
            avatar='DU',
        )
        demo_user.set_password('demo123')
        db.session.add(demo_user)
        db.session.flush()

    # Seed sample leads
    sample_leads = [
        {
            'first_name': 'Sarah', 'last_name': 'Chen',
            'email': 'sarah.chen@techstartup.io',
            'company': 'TechStartup.io', 'job_title': 'VP of Sales',
            'industry': 'SaaS', 'location': 'San Francisco, CA',
            'company_size': '51-200', 'source': 'linkedin',
            'lead_score': 85, 'status': 'contacted',
        },
        {
            'first_name': 'Marcus', 'last_name': 'Johnson',
            'email': 'marcus@fintechpro.com',
            'company': 'FinTech Pro', 'job_title': 'CEO',
            'industry': 'Fintech', 'location': 'New York, NY',
            'company_size': '11-50', 'source': 'apollo',
            'lead_score': 92, 'status': 'interested',
        },
        {
            'first_name': 'Emily', 'last_name': 'Rodriguez',
            'email': 'emily.r@healthtech.ai',
            'company': 'HealthTech AI', 'job_title': 'Director of Marketing',
            'industry': 'Healthcare', 'location': 'Austin, TX',
            'company_size': '201-500', 'source': 'hunter',
            'lead_score': 70, 'status': 'new',
        },
        {
            'first_name': 'James', 'last_name': 'Wilson',
            'email': 'jwilson@ecommercescale.com',
            'company': 'Ecommerce Scale', 'job_title': 'Head of Growth',
            'industry': 'E-commerce', 'location': 'Remote, US',
            'company_size': '51-200', 'source': 'maps',
            'lead_score': 78, 'status': 'replied',
        },
        {
            'first_name': 'Aisha', 'last_name': 'Patel',
            'email': 'aisha@cloudsecure.dev',
            'company': 'CloudSecure', 'job_title': 'CTO',
            'industry': 'Cybersecurity', 'location': 'Seattle, WA',
            'company_size': '51-200', 'source': 'firecrawl',
            'lead_score': 95, 'status': 'meeting_booked',
        },
    ]

    for lead_data in sample_leads:
        lead = Lead(
            workspace_id=workspace.id,
            user_id=demo_user.id,
            **lead_data,
        )
        db.session.add(lead)

    # Seed sample LinkedIn activities
    sample_activities = [
        {
            'lead_name': 'Sarah Chen', 'company': 'TechStartup.io',
            'linkedin_url': 'https://linkedin.com/in/sarahchen',
            'activity_type': 'connection_sent', 'activity_date': '2026-06-01',
            'notes': 'Sent connection request with personalized note',
            'source': 'manual',
        },
        {
            'lead_name': 'Sarah Chen', 'company': 'TechStartup.io',
            'linkedin_url': 'https://linkedin.com/in/sarahchen',
            'activity_type': 'connection_accepted', 'activity_date': '2026-06-01',
            'notes': 'Accepted connection request',
            'source': 'manual',
        },
        {
            'lead_name': 'Sarah Chen', 'company': 'TechStartup.io',
            'linkedin_url': 'https://linkedin.com/in/sarahchen',
            'activity_type': 'dm_sent', 'activity_date': '2026-06-01',
            'notes': 'Sent initial outreach DM about sales automation',
            'source': 'manual',
        },
        {
            'lead_name': 'Marcus Johnson', 'company': 'FinTech Pro',
            'linkedin_url': 'https://linkedin.com/in/marcusj',
            'activity_type': 'connection_sent', 'activity_date': '2026-05-28',
            'notes': 'Referred by mutual connection',
            'source': 'manual',
        },
        {
            'lead_name': 'Marcus Johnson', 'company': 'FinTech Pro',
            'linkedin_url': 'https://linkedin.com/in/marcusj',
            'activity_type': 'interested', 'activity_date': '2026-05-29',
            'notes': 'Expressed interest in demo, scheduling call',
            'source': 'manual',
        },
        {
            'lead_name': 'Aisha Patel', 'company': 'CloudSecure',
            'linkedin_url': 'https://linkedin.com/in/aishapatel',
            'activity_type': 'meeting_booked', 'activity_date': '2026-06-02',
            'notes': 'Demo call scheduled for Friday',
            'source': 'gmail',
        },
    ]

    for activity_data in sample_activities:
        activity = LinkedInActivity(
            workspace_id=workspace.id,
            user_id=demo_user.id,
            **activity_data,
        )
        db.session.add(activity)

    db.session.commit()
