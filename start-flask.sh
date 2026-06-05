#!/bin/bash
cd /root/.openclaw/workspace/flashclaw-dashboard
source venv/bin/activate
export MATON_API_KEY="v2.DJcJG65XIg_K1zeQJ9oI-KVWj0ZZ0zPlL1mEZ7HBWempNQAzRNOOmHbe1_qCkHT1Sfd8Z7tLiauT-dyVL6iv41Gtf28K3N9ituDfTHXFBgUNGWjFp1wFhO-9"
exec python run.py
