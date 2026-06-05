#!/usr/bin/env bash
# Quick launcher for STDF Analyzer
cd "$(dirname "$0")"
exec streamlit run app.py --server.port "${PORT:-8501}" --server.address "${HOST:-localhost}"
