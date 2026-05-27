#!/bin/bash
# READ-ONLY HTTPS GET wrapper for public GIS/permit endpoints.
URL="$1"
shift
curl -s -L --max-time 30 -A "SKOpi-TERRA/1.0" "$URL" "$@"
