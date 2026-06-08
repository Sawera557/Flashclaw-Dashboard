#!/bin/bash
cd /root/.openclaw/workspace/flashclaw-dashboard
source venv/bin/activate
export MATON_API_KEY="v2.CC4kibArJ4JXpJXxIi999bDMDCVFwXsKXGGS8VUCC_bC1pyh0-69OgU8hUkbzpnf5ik0Aw09krhOuS4Eb1x77pId2uRCgu86_-ewGkNfVO-7qRJqHrvfXH3J"
exec python run.py
