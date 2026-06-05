import json
import os
import urllib.request

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.services.supabase import select_one, eq

gmail_bp = Blueprint('gmail', __name__)

MATON_API_KEY = os.environ.get('MATON_API_KEY', '')
MATON_BASE = 'https://api.maton.ai/google-mail/gmail/v1/users/me'


def _maton_request(path, method='GET', body=None):
    """Make a request to the Maton Gmail API."""
    if not MATON_API_KEY:
        return None, 'MATON_API_KEY not configured'
    url = f'{MATON_BASE}/{path}'
    req = urllib.request.Request(url)
    req.add_header('Authorization', f'Bearer {MATON_API_KEY}')
    req.add_header('Content-Type', 'application/json')
    if body:
        req.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp), None
    except Exception as e:
        return None, str(e)


@gmail_bp.route('/api/gmail/emails', methods=['GET'])
@jwt_required()
def list_emails():
    """List recent emails with full content."""
    user_id = get_jwt_identity()
    user = select_one('users', filters=[eq('id', int(user_id))])
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    limit = request.args.get('limit', 5, type=int)
    query = request.args.get('q', '')

    # Get message list
    params = f'messages?maxResults={limit}'
    if query:
        params += f'&q={urllib.parse.quote(query)}'

    data, err = _maton_request(params)
    if err:
        return jsonify({'error': err}), 502

    messages = data.get('messages', [])
    emails = []

    for msg in messages:
        msg_id = msg['id']
        detail, err = _maton_request(f'messages/{msg_id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date')
        if err:
            continue

        payload = detail.get('payload', {})
        headers = {h['name']: h['value'] for h in payload.get('headers', [])}
        snippet = detail.get('snippet', '')

        emails.append({
            'id': msg_id,
            'threadId': msg.get('threadId', ''),
            'from': headers.get('From', 'Unknown'),
            'subject': headers.get('Subject', '(no subject)'),
            'date': headers.get('Date', ''),
            'snippet': snippet,
        })

    return jsonify({'emails': emails, 'total': len(emails)})


@gmail_bp.route('/api/gmail/emails/<message_id>', methods=['GET'])
@jwt_required()
def get_email(message_id):
    """Get full content of a specific email."""
    user_id = get_jwt_identity()
    user = select_one('users', filters=[eq('id', int(user_id))])
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    detail, err = _maton_request(f'messages/{message_id}?format=full')
    if err:
        return jsonify({'error': err}), 502

    payload = detail.get('payload', {})
    headers = {h['name']: h['value'] for h in payload.get('headers', [])}

    # Decode body
    body = ''
    parts = payload.get('parts', [])
    if not parts:
        # Single part
        body_data = payload.get('body', {}).get('data', '')
        if body_data:
            import base64
            body = base64.urlsafe_b64decode(body_data + '==').decode('utf-8', errors='replace')
    else:
        for part in parts:
            if part.get('mimeType') == 'text/plain':
                body_data = part.get('body', {}).get('data', '')
                if body_data:
                    import base64
                    body = base64.urlsafe_b64decode(body_data + '==').decode('utf-8', errors='replace')
                break

    return jsonify({
        'id': message_id,
        'threadId': detail.get('threadId', ''),
        'from': headers.get('From', 'Unknown'),
        'subject': headers.get('Subject', '(no subject)'),
        'date': headers.get('Date', ''),
        'snippet': detail.get('snippet', ''),
        'body': body[:5000],
    })


@gmail_bp.route('/api/gmail/stats', methods=['GET'])
@jwt_required()
def gmail_stats():
    """Get Gmail account stats."""
    user_id = get_jwt_identity()
    user = select_one('users', filters=[eq('id', int(user_id))])
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    # Get profile
    profile, err = _maton_request('profile')
    if err:
        return jsonify({'error': err}), 502

    return jsonify({
        'email': profile.get('emailAddress', ''),
        'total_messages': profile.get('messagesTotal', 0),
        'threads_total': profile.get('threadsTotal', 0),
        'history_id': profile.get('historyId', ''),
    })
