import csv
import io
import json
import logging
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, Response
from flask_jwt_extended import jwt_required, get_jwt_identity
from app import db
from app.models.lead import Lead
from app.models.user import User
from app.services.dedup import is_duplicate_lead
from app.services.scoring import score_lead_via_groq, enrich_lead_via_groq

leads_bp = Blueprint('leads', __name__)
logger = logging.getLogger(__name__)


def _get_current_user(user_id_str):
    """Get User model from JWT identity string."""
    try:
        return User.query.get(int(user_id_str))
    except (ValueError, TypeError):
        return None


@leads_bp.route('/api/leads', methods=['GET'])
@jwt_required()
def list_leads():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    search = request.args.get('search', '', type=str)
    source = request.args.get('source', '', type=str)
    status = request.args.get('status', '', type=str)
    min_score = request.args.get('min_score', 0, type=int)

    per_page = min(per_page, 100)

    query = Lead.query.filter_by(workspace_id=user.workspace_id)

    if search:
        search_term = f'%{search}%'
        query = query.filter(
            db.or_(
                Lead.first_name.ilike(search_term),
                Lead.last_name.ilike(search_term),
                Lead.email.ilike(search_term),
                Lead.company.ilike(search_term),
                Lead.job_title.ilike(search_term),
            )
        )

    if source:
        query = query.filter(Lead.source == source)

    if status:
        query = query.filter(Lead.status == status)

    if min_score > 0:
        query = query.filter(Lead.lead_score >= min_score)

    query = query.order_by(Lead.updated_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        'leads': [lead.to_dict() for lead in pagination.items],
        'total': pagination.total,
        'page': page,
        'pages': pagination.pages,
        'per_page': per_page,
    })


@leads_bp.route('/api/leads', methods=['POST'])
@jwt_required()
def create_lead():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Check for duplicate by email
    if data.get('email'):
        existing = Lead.query.filter_by(email=data['email'].strip().lower()).first()
        if existing:
            return jsonify({'error': 'Lead with this email already exists', 'lead': existing.to_dict()}), 409

    lead = Lead(
        workspace_id=user.workspace_id,
        user_id=user.id,
        first_name=data.get('first_name', ''),
        last_name=data.get('last_name', ''),
        email=data.get('email', '').strip().lower() if data.get('email') else '',
        company=data.get('company', ''),
        job_title=data.get('job_title', ''),
        phone=data.get('phone', ''),
        linkedin_url=data.get('linkedin_url', ''),
        website=data.get('website', ''),
        industry=data.get('industry', ''),
        location=data.get('location', ''),
        company_size=data.get('company_size', ''),
        source=data.get('source', 'manual'),
        lead_score=data.get('lead_score', 0),
        status=data.get('status', 'new'),
    )

    db.session.add(lead)
    db.session.commit()

    return jsonify({'lead': lead.to_dict()}), 201


@leads_bp.route('/api/leads/bulk', methods=['POST'])
@jwt_required()
def bulk_create_leads():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()
    if not data or not isinstance(data, list):
        return jsonify({'error': 'Expected array of lead objects'}), 400

    saved = 0
    errors = 0
    existing_leads = Lead.query.filter_by(workspace_id=user.workspace_id).all()
    existing_dict = [l.to_dict() for l in existing_leads]

    for lead_data in data:
        try:
            # Skip duplicates
            is_dup, matched_on = is_duplicate_lead(lead_data, existing_dict)
            if is_dup:
                errors += 1
                continue

            lead = Lead(
                workspace_id=user.workspace_id,
                user_id=user.id,
                first_name=lead_data.get('first_name', ''),
                last_name=lead_data.get('last_name', ''),
                email=lead_data.get('email', '').strip().lower() if lead_data.get('email') else '',
                company=lead_data.get('company', ''),
                job_title=lead_data.get('job_title', ''),
                phone=lead_data.get('phone', ''),
                linkedin_url=lead_data.get('linkedin_url', ''),
                website=lead_data.get('website', ''),
                industry=lead_data.get('industry', ''),
                location=lead_data.get('location', ''),
                company_size=lead_data.get('company_size', ''),
                source=lead_data.get('source', 'manual'),
                lead_score=lead_data.get('lead_score', 0),
                status=lead_data.get('status', 'new'),
            )
            db.session.add(lead)
            saved += 1
        except Exception as e:
            errors += 1
            logger.error(f'Bulk lead error: {str(e)}')

    db.session.commit()

    return jsonify({'saved': saved, 'errors': errors}), 201


@leads_bp.route('/api/leads/<int:lead_id>', methods=['PUT'])
@jwt_required()
def update_lead(lead_id):
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    lead = Lead.query.filter_by(id=lead_id, workspace_id=user.workspace_id).first()
    if not lead:
        return jsonify({'error': 'Lead not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    allowed_fields = [
        'first_name', 'last_name', 'email', 'company', 'job_title', 'phone',
        'linkedin_url', 'website', 'industry', 'location', 'company_size',
        'source', 'lead_score', 'status', 'icp_match', 'score_reason',
    ]

    for field in allowed_fields:
        if field in data:
            setattr(lead, field, data[field])

    lead.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({'lead': lead.to_dict()})


@leads_bp.route('/api/leads/<int:lead_id>', methods=['DELETE'])
@jwt_required()
def delete_lead(lead_id):
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    lead = Lead.query.filter_by(id=lead_id, workspace_id=user.workspace_id).first()
    if not lead:
        return jsonify({'error': 'Lead not found'}), 404

    db.session.delete(lead)
    db.session.commit()

    return jsonify({'success': True})


@leads_bp.route('/api/leads/hunt', methods=['POST'])
@jwt_required()
def hunt_leads():
    """Lead hunting endpoint — simulated async for now.

    Accepts sources and ICP profile, returns queued job id.
    """
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json() or {}

    # Simulate a hunt job (in production, this would be async with Celery/Redis)
    # For now, generate some sample leads based on ICP
    icp = data.get('icp', {})
    limit = min(data.get('limit', 50), 200)
    sources = data.get('sources', [])
    min_score = data.get('min_score', 60)

    # Generate mock leads based on ICP
    sample_industries = icp.get('industries', ['Technology', 'SaaS'])
    sample_titles = icp.get('job_titles', ['VP of Sales', 'CEO', 'Director of Sales'])

    saved_leads = []
    for i in range(min(limit, 10)):  # Limit mock generation
        lead = Lead(
            workspace_id=user.workspace_id,
            user_id=user.id,
            first_name=f'Sample{i+1}',
            last_name='Lead',
            email=f'sample{i+1}@example.com',
            company=f'Company {i+1}',
            job_title=sample_titles[i % len(sample_titles)],
            industry=sample_industries[i % len(sample_industries)],
            location='Remote, US',
            source=','.join(sources) if sources else 'simulated',
            lead_score=70 + (i * 3),
            status='new',
        )
        db.session.add(lead)
        saved_leads.append(lead.to_dict())

    db.session.commit()

    return jsonify({
        'job_id': 'simulated-job-id',
        'status': 'completed',
        'leads_found': len(saved_leads),
    })


@leads_bp.route('/api/leads/enrich', methods=['POST'])
@jwt_required()
def enrich_lead():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json() or {}
    lead_id = data.get('lead_id')

    if not lead_id:
        return jsonify({'error': 'lead_id is required'}), 400

    lead = Lead.query.filter_by(id=lead_id, workspace_id=user.workspace_id).first()
    if not lead:
        return jsonify({'error': 'Lead not found'}), 404

    # Enrich using Groq (or fallback)
    enriched_data = enrich_lead_via_groq(lead.to_dict())

    # Update lead with any new data
    for field in ['industry', 'company_size', 'location', 'company', 'job_title']:
        if enriched_data.get(field) and not getattr(lead, field):
            setattr(lead, field, enriched_data[field])

    lead.enriched_at = datetime.now(timezone.utc)
    lead.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({'lead': lead.to_dict()})


@leads_bp.route('/api/leads/score', methods=['POST'])
@jwt_required()
def score_lead():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json() or {}
    lead_id = data.get('lead_id')
    icp = data.get('icp', {})

    if not lead_id:
        return jsonify({'error': 'lead_id is required'}), 400

    lead = Lead.query.filter_by(id=lead_id, workspace_id=user.workspace_id).first()
    if not lead:
        return jsonify({'error': 'Lead not found'}), 404

    result = score_lead_via_groq(lead.to_dict(), icp)

    lead.lead_score = result['score']
    lead.icp_match = result['icp_match']
    lead.score_reason = result['reason']
    lead.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({
        'score': result['score'],
        'reason': result['reason'],
        'icp_match': result['icp_match'],
        'lead': lead.to_dict(),
    })


@leads_bp.route('/api/leads/export', methods=['GET'])
@jwt_required()
def export_leads():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    fmt = request.args.get('format', 'csv')

    leads = Lead.query.filter_by(workspace_id=user.workspace_id).order_by(Lead.created_at.desc()).all()

    if fmt == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'ID', 'First Name', 'Last Name', 'Email', 'Company', 'Job Title',
            'Phone', 'LinkedIn URL', 'Website', 'Industry', 'Location',
            'Company Size', 'Source', 'Score', 'Status', 'ICP Match',
            'Created At', 'Updated At'
        ])
        for lead in leads:
            writer.writerow([
                lead.id, lead.first_name, lead.last_name, lead.email,
                lead.company, lead.job_title, lead.phone, lead.linkedin_url,
                lead.website, lead.industry, lead.location, lead.company_size,
                lead.source, lead.lead_score, lead.status, lead.icp_match,
                lead.created_at.isoformat() if lead.created_at else '',
                lead.updated_at.isoformat() if lead.updated_at else '',
            ])

        csv_content = output.getvalue()
        return Response(
            csv_content,
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=leads_export.csv'},
        )

    return jsonify({'leads': [lead.to_dict() for lead in leads]})
