import json

from flask import Blueprint, request, jsonify, Response, stream_with_context
from flask_jwt_extended import jwt_required, get_jwt_identity

from app import db
from app.models.user import User
from app.models.lead import Lead, GeneratedEmail
from app.services.scoring import get_groq_client, score_lead_via_groq
from app.services.hubspot_service import build_context_summary, search_owner_by_name, get_deals_for_owner, HubSpotError

ai_bp = Blueprint('ai', __name__)


@ai_bp.route('/api/ai/chat', methods=['POST'])
@jwt_required()
def ai_chat():
    """Streaming AI chat endpoint using Groq SSE."""
    current_user_id = get_jwt_identity()
    user = User.query.get(int(current_user_id))
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json() or {}
    messages = data.get('messages', [])

    if not messages:
        return jsonify({'error': 'Messages array is required'}), 400

    # System prompt for Lead Hunter
    system_content = (
        'You are Lead Hunter AI, an expert sales assistant. You help SDRs and sales teams '
        'find leads, craft outreach emails, analyze ICP fit, and provide sales strategy advice. '
        'Be concise, actionable, and data-driven. When asked to write emails, personalize them. '
        'When analyzing leads, focus on buying signals. You can help with cold email copy, '
        'LinkedIn messages, follow-up sequences, ICP definitions, and outreach strategy.'
    )

    # Detect HubSpot questions and inject live context
    user_msg = messages[-1]['content'] if messages else ''
    user_msg_lower = user_msg.lower()
    hs_keywords = ['deal', 'owner', 'pipeline', 'hubspot', 'jordan', 'anna', 'working on',
                   'latest deal', 'open deal', 'stage', 'qualified', 'meeting scheduled']
    if any(kw in user_msg_lower for kw in hs_keywords):
        try:
            hs_context = build_context_summary()
            system_content += f'\n\n--- HUBSPOT CRM DATA (LIVE) ---\n{hs_context}\n--- END HUBSPOT DATA ---\n\n'
            system_content += (
                'You have live HubSpot CRM data above. The user can ask you about any owner\'s deals. '
                'Owners are sales reps. If asked for details on a specific owner, describe their '
                'deals, stages, and amounts. If the user names someone who is a HubSpot contact '
                '(not an owner), say so and offer to search their owner. Be concise and data-driven.'
            )
        except HubSpotError:
            pass

    system_prompt = {'role': 'system', 'content': system_content}

    # Check for HubSpot-specific owner queries — fetch deals for a specific person
    import re
    import os
    import urllib.request, urllib.parse
    
    user_msg = messages[-1]['content'] if messages else ''
    user_msg_lower = user_msg.lower()

    # Detect "what deals is [name] working on" pattern
    owner_match = re.search(r'(?:what|which)\s+deals\s+(?:is|does)\s+(.+?)\s+(?:working on|have|own)', user_msg_lower)
    if owner_match:
        name_query = owner_match.group(1).strip()
        try:
            matched_owners = search_owner_by_name(name_query)
            if matched_owners:
                owner = matched_owners[0]
                deals = [None]  # lazy: use search endpoint
                from app.services.hubspot_service import get_deals_for_owner
                deals = get_deals_for_owner(owner['id'], limit=10)
                if deals:
                    deal_lines = [f'Deals for {owner["name"]} ({owner["email"]}):']
                    for d in deals:
                        amt = f"${d['amount']}" if d['amount'] != '0' else '$0'
                        deal_lines.append(f'  · {d["name"]} — {amt} — {d["stage"]} (last: {d["modified"]})')
                    system_content += '\n\n--- HUBSPOT RESULT ---\n' + '\n'.join(deal_lines) + '\n--- END HUBSPOT RESULT ---'
        except Exception:
            pass
    
    # Detect Gmail-related requests
    if any(p in user_msg_lower for p in ['my last', 'my emails', 'my gmail', 'my emails from gmail', 'gmail emails', 'recent emails', 'show my inbox', 'last 5 emails']):
        limit_match = re.search(r'last (\d+)', user_msg_lower)
        limit = int(limit_match.group(1)) if limit_match else 5
        maton_key = os.environ.get('MATON_API_KEY', '')
        if maton_key:
            try:
                import base64 as b64
                # Get message list
                req_url = f'https://api.maton.ai/google-mail/gmail/v1/users/me/messages?maxResults={limit}'
                req = urllib.request.Request(req_url)
                req.add_header('Authorization', f'Bearer {maton_key}')
                with urllib.request.urlopen(req) as resp:
                    msg_list = json.load(resp).get('messages', [])
                
                emails = []
                for msg in msg_list:
                    detail_req = urllib.request.Request(f'https://api.maton.ai/google-mail/gmail/v1/users/me/messages/{msg["id"]}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date')
                    detail_req.add_header('Authorization', f'Bearer {maton_key}')
                    with urllib.request.urlopen(detail_req) as resp:
                        detail = json.load(resp)
                    payload = detail.get('payload', {})
                    headers = {h['name']: h['value'] for h in payload.get('headers', [])}
                    emails.append({
                        'from': headers.get('From', '?'),
                        'subject': headers.get('Subject', '?'),
                        'date': headers.get('Date', '?'),
                        'snippet': detail.get('snippet', ''),
                    })
                
                # Inject emails as context
                email_context = '\n\n--- YOUR RECENT EMAILS FROM GMAIL ---\n'
                for i, e in enumerate(emails, 1):
                    email_context += f'{i}. From: {e["from"]}\n   Subject: {e["subject"]}\n   Date: {e["date"]}\n   Preview: {e["snippet"]}\n\n'
                system_prompt['content'] += email_context
            except Exception as e:
                system_prompt['content'] += f'\n\nNote: Attempted to fetch Gmail but got error: {str(e)}'

    full_messages = [system_prompt] + messages

    client = get_groq_client()

    def generate():
        if not client:
            yield 'data: {"error": "GROQ_API_KEY not configured"}\n\n'
            yield 'data: [DONE]\n\n'
            return

        try:
            completion = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=full_messages,
                temperature=0.7,
                max_tokens=2048,
                stream=True,
            )

            for chunk in completion:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    yield f'data: {json.dumps({"text": content})}\n\n'

            yield 'data: [DONE]\n\n'
        except Exception as e:
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
            yield 'data: [DONE]\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )


@ai_bp.route('/api/ai/generate-email', methods=['POST'])
@jwt_required()
def generate_email():
    """Generate a personalized email for a lead."""
    current_user_id = get_jwt_identity()
    user = User.query.get(int(current_user_id))
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json() or {}
    lead_data = data.get('lead', {})
    email_type = data.get('type', 'cold')
    lead_id = data.get('lead_id')

    if not lead_data:
        return jsonify({'error': 'Lead data is required'}), 400

    prompt = _build_email_prompt(lead_data, email_type)

    client = get_groq_client()
    if not client:
        # Fallback template-based generation
        return _generate_template_email(lead_data, email_type, user.id, lead_id)

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.7,
            max_tokens=1024,
        )
        result = json.loads(completion.choices[0].message.content)
    except Exception as e:
        return _generate_template_email(lead_data, email_type, user.id, lead_id)

    subject = result.get('subject', '')[:500]
    body = result.get('body', '')

    # Save to database if lead_id provided
    if lead_id:
        gen_email = GeneratedEmail(
            lead_id=lead_id,
            user_id=user.id,
            email_type=email_type,
            subject=subject,
            body=body,
            model="llama-3.1-8b-instant",
        )
        db.session.add(gen_email)
        db.session.commit()

    return jsonify({
        'subject': subject,
        'body': body,
    })


def _build_email_prompt(lead_data, email_type):
    """Build the prompt for email generation."""
    name = f"{lead_data.get('first_name', '')} {lead_data.get('last_name', '')}".strip()
    company = lead_data.get('company', '')
    title = lead_data.get('job_title', '')
    industry = lead_data.get('industry', '')
    pain_points = lead_data.get('pain_points', '')

    type_guidance = {
        'cold': 'A first-touch cold outreach email. Keep it short (3-5 sentences), personalized, with a clear value proposition. Don\'t ask for a meeting in the first email.',
        'followup': 'A follow-up email to someone you already reached out to. Reference the previous email, add new value, and include a soft CTA.',
        'linkedin': 'A LinkedIn connection request message (300 chars max) or InMail. Brief, professional, and personalized.',
        'sequence': 'Generate an email sequence: subjects and bodies for 3 emails (initial, follow-up, break-up). Return as JSON with keys sequence.[0-2].subject and sequence.[0-2].body.',
    }

    guidance = type_guidance.get(email_type, type_guidance['cold'])

    return f"""Write a personalized {email_type} email for this lead:

Name: {name}
Company: {company}
Title: {title}
Industry: {industry}
Pain points: {pain_points}

Guidance: {guidance}

Return ONLY valid JSON: {{"subject": "...", "body": "..."}}"""


def _generate_template_email(lead_data, email_type, user_id, lead_id):
    """Fallback template-based email generation."""
    name = f"{lead_data.get('first_name', '')} {lead_data.get('last_name', '')}".strip()
    first_name = lead_data.get('first_name', 'there')
    company = lead_data.get('company', 'your company')
    title = lead_data.get('job_title', '')

    templates = {
        'cold': {
            'subject': f'Quick question about {company}',
            'body': (
                f"Hi {first_name},\n\n"
                f"I noticed you're the {title} at {company}. "
                f"I've been working with similar companies to improve their sales pipeline "
                f"and thought you might find this relevant.\n\n"
                f"Would you be open to a 10-minute chat to see if there's a fit?\n\n"
                f"Best,\n[Your Name]"
            ),
        },
        'followup': {
            'subject': f'Re: Quick question about {company}',
            'body': (
                f"Hi {first_name},\n\n"
                f"I wanted to follow up on my previous email. "
                f"I know things get busy — just wanted to check if improving "
                f"your outreach pipeline is a priority right now?\n\n"
                f"No worries either way.\n\n"
                f"Best,\n[Your Name]"
            ),
        },
        'linkedin': {
            'subject': 'LinkedIn connection',
            'body': (
                f"Hi {first_name}, I've been following {company}'s work in the space. "
                f"Would love to connect and share ideas."
            ),
        },
        'sequence': {
            'subject': f'Sales outreach for {company}',
            'body': (
                f"Email 1 (Initial):\n"
                f"Subject: Quick question about {company}\n\n"
                f"Hi {first_name}, I noticed you're the {title} at {company}...\n\n"
                f"Email 2 (Follow-up):\n"
                f"Subject: Re: Quick question about {company}\n\n"
                f"Hi {first_name}, following up on my previous email...\n\n"
                f"Email 3 (Break-up):\n"
                f"Subject: Should I close your file?\n\n"
                f"Hi {first_name}, I haven't heard back so I'll assume timing isn't right..."
            ),
        },
    }

    template = templates.get(email_type, templates['cold'])

    if lead_id:
        gen_email = GeneratedEmail(
            lead_id=lead_id,
            user_id=user_id,
            email_type=email_type,
            subject=template['subject'][:500],
            body=template['body'],
            model='template-fallback',
        )
        db.session.add(gen_email)
        db.session.commit()

    return jsonify(template)


@ai_bp.route('/api/ai/score-lead', methods=['POST'])
@jwt_required()
def ai_score_lead():
    """Score a lead using AI based on ICP criteria."""
    current_user_id = get_jwt_identity()
    user = User.query.get(int(current_user_id))
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json() or {}
    lead_data = data.get('lead', {})
    icp = data.get('icp', {})

    if not lead_data:
        return jsonify({'error': 'Lead data is required'}), 400

    result = score_lead_via_groq(lead_data, icp)

    # Update lead in DB if lead_id provided
    lead_id = data.get('lead_id')
    if lead_id:
        lead = Lead.query.filter_by(id=lead_id, workspace_id=user.workspace_id).first()
        if lead:
            lead.lead_score = result['score']
            lead.icp_match = result['icp_match']
            lead.score_reason = result['reason']
            db.session.commit()

    return jsonify(result)


@ai_bp.route('/api/ai/parse-linkedin', methods=['POST'])
@jwt_required()
def parse_linkedin_notes():
    """Parse raw LinkedIn activity notes into structured activities using AI."""
    current_user_id = get_jwt_identity()
    user = User.query.get(int(current_user_id))
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json() or {}
    raw_text = data.get('text', '')

    if not raw_text:
        return jsonify({'error': 'Text to parse is required'}), 400

    client = get_groq_client()
    if not client:
        return jsonify({
            'activities': [
                {'lead_name': 'Unknown', 'activity_type': 'dm_sent',
                 'activity_date': '', 'notes': raw_text[:200], 'source': 'ai_dump'}
            ]
        })

    prompt = f"""Parse these raw LinkedIn activity notes into structured activities.

RAW NOTES:
{raw_text}

Activity types can be: connection_sent, connection_accepted, dm_sent, reply_received,
interested, not_interested, meeting_booked, followup_sent, profile_viewed.

Extract lead name, company, activity type, date, and clean notes.

Return ONLY valid JSON: {{"activities": [{{"lead_name": "...", "company": "...", "linkedin_url": "", "activity_type": "...", "activity_date": "...", "notes": "..."}}]}}"""

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.2,
            max_tokens=2000,
        )
        result = json.loads(completion.choices[0].message.content)
        activities = result.get('activities', [])

        # Mark source
        for activity in activities:
            activity['source'] = 'ai_dump'

        return jsonify({'activities': activities})
    except Exception as e:
        return jsonify({
            'activities': [
                {'lead_name': 'Unknown', 'activity_type': 'dm_sent',
                 'activity_date': '', 'notes': raw_text[:200], 'source': 'ai_dump'}
            ]
        })
