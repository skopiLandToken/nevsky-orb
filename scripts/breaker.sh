#!/bin/bash
# Breaker — quick CLI to talk to the test child.
# Usage: ./scripts/breaker.sh "your question here"
# Or:    ./scripts/breaker.sh   (then type interactively)

if [ -z "$1" ]; then
    read -p "Ask Breaker: " QUESTION
else
    QUESTION="$*"
fi

cd /opt/nevsky-dev && docker compose exec -T api python3 -c "
import asyncio, sys
from children.child_engine import ask_child
result = asyncio.run(ask_child('breaker', '''$QUESTION'''))
print(result)
"
