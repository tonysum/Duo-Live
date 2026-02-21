#!/bin/bash
# Copy static assets into standalone directory (required for standalone mode)
cp -r .next/static .next/standalone/.next/static
cp -r public .next/standalone/public 2>/dev/null || true

exec node .next/standalone/server.js
