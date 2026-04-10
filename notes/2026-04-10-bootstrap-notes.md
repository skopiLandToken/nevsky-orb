# Nevsky ORB Build Note

## Date
2026-04-10

## Change
Created the first isolated Nevsky dev environment on the current server.

## Why
Iosif wanted Nevsky isolated from the main SKOpi stack so it can be developed safely now and moved to its own server later.

## What was done
- Installed Docker and Docker Compose
- Created /opt/nevsky-dev
- Added FastAPI API container
- Added worker container
- Added Postgres and Redis containers
- Added source docs into docs/

## Notes
The bootstrap mostly succeeded.
Containers came up successfully.
The API health endpoint still needs verification because curl returned an empty reply.

## Next
- Initialize git and connect GitHub
- Commit repo structure
- Fix API health response
- Create schema v1
