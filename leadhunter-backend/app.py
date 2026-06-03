#!/usr/bin/env python
"""Lead Hunter AI - Flask Backend Entry Point."""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app

app = create_app(os.environ.get('FLASK_ENV', 'dev'))

if __name__ == '__main__':
    app.run(
        host=os.environ.get('HOST', '0.0.0.0'),
        port=int(os.environ.get('PORT', 5000)),
        debug=app.config.get('DEBUG', False),
        threaded=True,
    )
