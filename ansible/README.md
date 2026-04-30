# Metaseed Hub Ansible Deployment

## How it works

```
┌─────────┐  push   ┌────────┐  CI passes  ┌─────────┐
│   Dev   │────────>│  main  │────────────>│ Release │
└─────────┘         └────────┘             └─────────┘
                                                │
                                           every 5 min
                                                v
                                           ┌─────────┐
                                           │ Server  │
                                           │ (pulls) │
                                           └─────────┘
```

1. Push to `main` → CI runs tests
2. Create release (tag `v1.0.0`) when ready
3. Server detects new release within 5 minutes
4. Auto-deploys: checkout tag → install deps → migrate → reload

## Workflow

```bash
# Development
git push origin main          # CI tests run

# Deploy to production
git tag v1.0.0
git push origin v1.0.0        # Server picks it up automatically
```

## Initial Setup

```bash
cd ansible
ansible-playbook deploy.yml
```

## Manual Deploy

Force immediate deploy on server:

```bash
ssh metaseed.ewi
sudo -u app /app/deploy.sh
```

## Server Management

```bash
# View current version
cat /app/.deployed_version

# View logs
journalctl -u metaseed-hub -f      # App logs
journalctl -u metaseed-deploy -f   # Deploy logs

# Check timer
systemctl list-timers metaseed-deploy.timer
```
