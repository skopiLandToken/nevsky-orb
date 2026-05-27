# Yindo bridge — deploy artifacts

Versioned copies of the host-side files that live outside the repo. Source of
truth is the in-repo copy here; the active files are deployed to:

| Repo copy | Active path |
|-----------|-------------|
| `yindo-worker.service` | `/etc/systemd/system/yindo-worker.service` |
| `yindo.sudoers` | `/etc/sudoers.d/yindo` |

Update workflow when changing either file:

```bash
# Edit the repo copy first
$EDITOR deploy/yindo/yindo-worker.service

# Deploy
sudo cp deploy/yindo/yindo-worker.service /etc/systemd/system/yindo-worker.service
sudo systemctl daemon-reload
sudo systemctl restart yindo-worker

# For sudoers, validate before activating
sudo cp deploy/yindo/yindo.sudoers /etc/sudoers.d/yindo
sudo chmod 440 /etc/sudoers.d/yindo
sudo visudo -cf /etc/sudoers.d/yindo
```

See `DOCTRINE-YINDO-SECURITY-BOUNDARY-01` in `knowledge_store` for the
security model these files implement.

## Initial install (fresh host)

```bash
# 1. System user
sudo useradd --system --home-dir /var/lib/yindo --create-home \
    --shell /usr/sbin/nologin --user-group yindo

# 2. Python venv for the worker
sudo apt-get install -y python3.12-venv
sudo python3 -m venv /opt/nevsky-dev/.venv-yindo
sudo /opt/nevsky-dev/.venv-yindo/bin/pip install \
    redis httpx 'psycopg[binary]' python-dotenv
sudo chown -R yindo:yindo /opt/nevsky-dev/.venv-yindo
sudo chown yindo:yindo /opt/nevsky-dev/scripts/yindo_worker.py

# 3. Scratch dir (yindo's writable workspace inside the project)
sudo mkdir -p /opt/nevsky-dev/yindo-scratch
sudo chown yindo:yindo /opt/nevsky-dev/yindo-scratch
sudo chmod 755 /opt/nevsky-dev/yindo-scratch

# 4. Claude Code system-wide so yindo can reach it
sudo npm install -g @anthropic-ai/claude-code

# 5. Migration
docker exec -i nevsky-postgres psql -U nevsky -d nevsky_dev \
    < migrations/2026-05-27_yindo_bridge.sql

# 6. Deploy systemd + sudoers (see "Update workflow" above)

# 7. Env vars in .env (paste real values)
YINDO_BOT_TOKEN=<from @BotFather>
YINDO_WEBHOOK_SECRET=<openssl rand -hex 32>

# 8. Enable + start
sudo systemctl enable --now yindo-worker

# 9. Tell Telegram where to send (production)
curl -F "url=https://<public-host>/telegram/yindo/webhook" \
     -F "secret_token=<same as YINDO_WEBHOOK_SECRET>" \
     -F "allowed_updates=[\"message\"]" \
     "https://api.telegram.org/bot${YINDO_BOT_TOKEN}/setWebhook"
```
