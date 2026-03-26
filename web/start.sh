#!/bin/bash
# Serve the Vite production build as a static SPA
export TZ=UTC
exec npx -y serve dist -l 3000 -s
